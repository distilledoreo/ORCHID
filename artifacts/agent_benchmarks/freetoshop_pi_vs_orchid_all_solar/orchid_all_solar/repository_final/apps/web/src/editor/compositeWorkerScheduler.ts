import type { ImageDocument } from '@freetoshop/core-document';
import { isAbortError, PersistentImageWorkerClient } from '../workers/client.js';

export interface CompositeWorkerFrame {
  readonly generation: number;
  readonly documentId: string;
  readonly width: number;
  readonly height: number;
  /** Full mip-canvas dimensions; width/height are compact buffer dimensions. */
  readonly canvasWidth?: number;
  readonly canvasHeight?: number;
  /** Mip-space location of compact progressive pixels. */
  readonly pixelRegion?: {
    readonly x: number;
    readonly y: number;
    readonly width: number;
    readonly height: number;
  };
  readonly pixels: Uint8ClampedArray;
  /** Present when the worker filled only a progressive viewport into the full buffer. */
  readonly viewportRegion?: {
    readonly x: number;
    readonly y: number;
    readonly width: number;
    readonly height: number;
  };
  readonly progressiveViewportOnly?: boolean;
  readonly mipLevel: number;
}

export interface CompositeWorkerScheduleOptions {
  readonly viewportRegion?: {
    readonly x: number;
    readonly y: number;
    readonly width: number;
    readonly height: number;
  };
  readonly progressiveViewportOnly?: boolean;
  readonly mipLevel?: number;
  /**
   * When false, a progressive completion will not chain a full-canvas refine.
   * Default true so large cold paints get viewport-first then full background fill.
   */
  readonly chainFullRefine?: boolean;
  /**
   * Internal: full-canvas refine after a progressive frame. Keeps the progressive
   * frame displayable via frameFor while this work is inflight.
   */
  readonly internalRefine?: boolean;
}

export interface CompositeWorkerSchedulerOptions {
  readonly createWorker?: () => Worker;
  readonly onFrame?: (frame: CompositeWorkerFrame) => void;
  readonly onError?: (error: Error, generation: number) => void;
  readonly onProgress?: (progress: number, generation: number) => void;
}

/**
 * Schedules off-main-thread composites with generation-based stale-result
 * rejection and cooperative cancellation. Progressive viewport fills may chain
 * a full-canvas background refine while keeping the progressive frame displayable.
 */
export class CompositeWorkerScheduler {
  #generation = 0;
  #abort: AbortController | null = null;
  #latest: CompositeWorkerFrame | null = null;
  #inflight = false;
  /** True only while a progressive→full refine chain is in flight. */
  #preserveLatestWhileInflight = false;
  readonly #onFrame?: (frame: CompositeWorkerFrame) => void;
  readonly #onError?: (error: Error, generation: number) => void;
  readonly #onProgress?: (progress: number, generation: number) => void;
  readonly #client: PersistentImageWorkerClient;

  constructor(options: CompositeWorkerSchedulerOptions = {}) {
    this.#onFrame = options.onFrame;
    this.#onError = options.onError;
    this.#onProgress = options.onProgress;
    this.#client = new PersistentImageWorkerClient({ createWorker: options.createWorker });
  }

  get generation(): number {
    return this.#generation;
  }

  get inflight(): boolean {
    return this.#inflight;
  }

  get latestFrame(): CompositeWorkerFrame | null {
    return this.#latest;
  }

  /** Bytes uploaded for the latest task; zero when every tiled resource was referenced. */
  get lastInputTransferByteLength(): number {
    return this.#client.lastTransferByteLength;
  }

  /** True while a background full-canvas refine is running after a progressive fill. */
  get refining(): boolean {
    return this.#preserveLatestWhileInflight && this.#inflight;
  }

  /**
   * Returns a usable full or compact frame for `document`.
   * - Exact generation match: always usable.
   * - During an internal progressive→full refine chain, the progressive frame
   *   remains displayable until the full frame lands.
   * - External schedules and cancel do not preserve older frames (avoids stale content).
   */
  frameFor(
    document: ImageDocument,
    generation = this.#generation,
    mipLevel = 0,
  ): CompositeWorkerFrame | null {
    if (!this.#latest) return null;
    if (this.#latest.documentId !== document.id) return null;
    if (this.#latest.mipLevel !== mipLevel) return null;
    const scale = 2 ** mipLevel;
    if (
      (this.#latest.canvasWidth ?? this.#latest.width) !==
        Math.max(1, Math.ceil(document.canvas.width / scale)) ||
      (this.#latest.canvasHeight ?? this.#latest.height) !==
        Math.max(1, Math.ceil(document.canvas.height / scale))
    ) {
      return null;
    }
    if (this.#latest.generation === generation) return this.#latest;
    if (
      this.#preserveLatestWhileInflight &&
      this.#inflight &&
      this.#latest.generation < generation
    ) {
      return this.#latest;
    }
    return null;
  }

  /**
   * Cancels any in-flight composite and starts a new generation for `document`.
   * Prefer progressive viewport options for large cold paints so the worker
   * fills only on-screen tiles first, then chains a full-canvas refine.
   */
  schedule(document: ImageDocument, options: CompositeWorkerScheduleOptions = {}): number {
    this.#generation += 1;
    const generation = this.#generation;
    this.#abort?.abort();
    const controller = new AbortController();
    this.#abort = controller;
    this.#inflight = true;
    const progressiveViewportOnly = options.progressiveViewportOnly === true;
    const viewportRegion = options.viewportRegion;
    const chainFullRefine = options.chainFullRefine !== false;
    const internalRefine = options.internalRefine === true;
    const mipLevel = options.mipLevel ?? 0;
    // Only internal refine preserves the progressive frame; external schedules must
    // not display a previous document revision's pixels.
    this.#preserveLatestWhileInflight = internalRefine;

    void this.#client
      .run(
        {
          kind: 'composite',
          document,
          generation,
          viewportRegion,
          progressiveViewportOnly: progressiveViewportOnly ? true : undefined,
          mipLevel,
        },
        {
          signal: controller.signal,
          onProgress: (progress) => this.#onProgress?.(progress, generation),
        },
      )
      .then((result) => {
        if (generation !== this.#generation) return;
        if (result.kind !== 'composite') {
          throw new Error('The composite worker returned an invalid result.');
        }
        if (result.generation !== generation) return;
        const pixels = new Uint8ClampedArray(result.buffer);
        if (pixels.length !== result.width * result.height * 4) {
          throw new Error('Composite worker buffer length does not match its dimensions.');
        }
        const frame: CompositeWorkerFrame = {
          generation,
          documentId: document.id,
          width: result.width,
          height: result.height,
          canvasWidth: result.canvasWidth,
          canvasHeight: result.canvasHeight,
          pixelRegion: result.pixelRegion,
          pixels,
          viewportRegion: result.viewportRegion ?? viewportRegion,
          progressiveViewportOnly:
            result.progressiveViewportOnly === true || progressiveViewportOnly ? true : undefined,
          mipLevel: result.mipLevel ?? mipLevel,
        };
        this.#latest = frame;
        // Viewport-first cold path: chain full-canvas refine BEFORE notifying the
        // UI so the first paint after progressive completion observes
        // inflight/refining=true and must not cancel the refine (seed plan).
        if (progressiveViewportOnly && chainFullRefine) {
          this.schedule(document, {
            chainFullRefine: false,
            internalRefine: true,
            mipLevel,
          });
        } else {
          this.#inflight = false;
          this.#preserveLatestWhileInflight = false;
        }
        this.#onFrame?.(frame);
      })
      .catch((error: unknown) => {
        if (generation !== this.#generation) return;
        this.#inflight = false;
        this.#preserveLatestWhileInflight = false;
        if (isAbortError(error)) return;
        const normalized =
          error instanceof Error ? error : new Error('Composite worker scheduling failed.');
        this.#onError?.(normalized, generation);
      });

    return generation;
  }

  /** Cancels in-flight work and advances generation so late results are ignored. */
  cancel(): void {
    this.#generation += 1;
    this.#abort?.abort();
    this.#client.dispose();
    this.#abort = null;
    this.#inflight = false;
    this.#preserveLatestWhileInflight = false;
  }

  clear(): void {
    this.cancel();
    this.#latest = null;
  }
}
