import type { ImageDocument } from '@freetoshop/core-document';
import type { CompositeWorkerFrame } from './compositeWorkerScheduler.js';

export interface CompositeRenderPlanInput {
  readonly document: ImageDocument;
  readonly fullyWarm: boolean;
  readonly workerFrame: CompositeWorkerFrame | null;
  readonly scheduledDocument: ImageDocument | null;
  readonly seededGeneration: number;
  readonly workerInflight: boolean;
}

export interface CompositeRenderPlan {
  /** Schedule a new worker composite for this document. */
  readonly scheduleWorker: boolean;
  /** Seed the main-thread tile cache from this frame once. */
  readonly seedFrame: CompositeWorkerFrame | null;
  /** Full-canvas pixels that skip main-thread recomposition. */
  readonly precomposedPixels: Uint8ClampedArray | undefined;
  /**
   * When true, composite tile cache must assemble warm hits only and must not
   * call referenceComposite/regionComposite for misses (worker is the cold path).
   */
  readonly warmAssembleOnly: boolean;
  readonly cancelInflight: boolean;
  /**
   * When true, clear the App's scheduledDocument ref so a later cold region
   * (e.g. pan after cancel) can schedule again.
   */
  readonly clearScheduledDocument: boolean;
}

/**
 * Progressive frames seed only their viewport tiles even though their transport
 * buffer is canvas-sized. Full frames seed only when the cache can retain the
 * complete frame; otherwise App displays the worker pixels directly.
 */
export function shouldSeedCompositeWorkerFrame(
  frame: CompositeWorkerFrame,
  cacheMaxBytes: number,
): boolean {
  return frame.progressiveViewportOnly === true || frame.pixels.byteLength <= cacheMaxBytes;
}

/** Full background refine is useful only when its RGBA frame fits the live cache budget. */
export function shouldChainFullCompositeRefine(
  canvas: { readonly width: number; readonly height: number },
  cacheMaxBytes: number,
  mipLevel = 0,
): boolean {
  const scale = 2 ** mipLevel;
  const frameBytes = Math.ceil(canvas.width / scale) * Math.ceil(canvas.height / scale) * 4;
  return Number.isSafeInteger(frameBytes) && frameBytes > 0 && frameBytes <= cacheMaxBytes;
}

/** Chooses a power-of-two composite mip with at least one source sample per screen pixel. */
export function compositeMipLevelForZoom(zoom: number, maximum = 6): number {
  if (!Number.isFinite(zoom) || zoom <= 0 || !Number.isSafeInteger(maximum) || maximum < 0)
    return 0;
  return Math.min(maximum, Math.max(0, Math.floor(Math.log2(1 / zoom))));
}

/**
 * Whether the live App should abort the in-flight composite worker.
 *
 * Progressive viewport completions chain a full-canvas background refine
 * (`CompositeWorkerScheduler` sets inflight + preserveLatest). Seeding that
 * progressive frame, or painting a fully-warm progressive viewport, must
 * **not** cancel the refine — otherwise off-viewport tiles never fill and pan
 * stays on permanent transparent holes.
 *
 * Returns false when:
 * - nothing is inflight, or
 * - the display frame is progressive-only and a background refine is expected
 *
 * Returns true only for genuine obsolete work (e.g. non-progressive inflight
 * after the viewport is fully warm from a full-frame result).
 */
export function shouldCancelWorkerInflight(options: {
  readonly workerInflight: boolean;
  readonly workerFrame: CompositeWorkerFrame | null | undefined;
}): boolean {
  if (!options.workerInflight) return false;
  const progressiveOnly = options.workerFrame?.progressiveViewportOnly === true;
  // Keep background refine while a progressive frame is the display seed/frame.
  if (progressiveOnly) return false;
  return true;
}

/**
 * Decides how the live advanced compositor should paint while a worker may own
 * cold full-frame work. Pure function for regression coverage.
 */
export function planAdvancedCompositeRender(input: CompositeRenderPlanInput): CompositeRenderPlan {
  const { document, fullyWarm, workerFrame, scheduledDocument, seededGeneration, workerInflight } =
    input;

  if (fullyWarm) {
    // Viewport may be fully warm from a progressive worker frame while a
    // full-canvas background refine is still inflight — do not cancel it.
    const cancelInflight = shouldCancelWorkerInflight({ workerInflight, workerFrame });
    return {
      scheduleWorker: false,
      seedFrame: null,
      precomposedPixels: undefined,
      warmAssembleOnly: false,
      cancelInflight,
      clearScheduledDocument: cancelInflight,
    };
  }

  // New worker frame to seed into the tile cache once.
  if (workerFrame && seededGeneration !== workerFrame.generation) {
    // Progressive completion chains a full-canvas refine (inflight). Seeding the
    // progressive frame must NEVER cancel that refine — hard-coded keep path in
    // addition to shouldCancelWorkerInflight (skeptic: bare workerInflight cancel).
    const progressiveSeed = workerFrame.progressiveViewportOnly === true;
    return {
      scheduleWorker: false,
      seedFrame: workerFrame,
      precomposedPixels: undefined,
      warmAssembleOnly: false,
      cancelInflight: progressiveSeed
        ? false
        : shouldCancelWorkerInflight({ workerInflight, workerFrame }),
      clearScheduledDocument: false,
    };
  }

  // Frame already seeded for this generation.
  if (workerFrame) {
    // Full (non-progressive) frames can paint via precomposed full buffer.
    if (workerFrame.progressiveViewportOnly !== true) {
      return {
        scheduleWorker: false,
        seedFrame: null,
        precomposedPixels: workerFrame.pixels,
        warmAssembleOnly: false,
        cancelInflight: false,
        clearScheduledDocument: false,
      };
    }
    // Progressive frame already seeded but viewport not fully warm (pan/zoom to
    // cold tiles). Do not paint the progressive buffer as precomposed (holes).
    // Schedule another progressive fill when nothing is inflight. Use only
    // workerInflight for already-scheduled — a completed prior schedule must
    // not permanently block reschedule via scheduledDocument identity.
    return {
      scheduleWorker: !workerInflight,
      seedFrame: null,
      precomposedPixels: undefined,
      warmAssembleOnly: true,
      cancelInflight: false,
      clearScheduledDocument: false,
    };
  }

  // Cold, no usable frame: never miss-fill on the UI thread.
  // Schedule when nothing is inflight. Clear a sticky scheduledDocument left
  // after cancel so pan/zoom cold regions can schedule again.
  const stickyWithoutInflight = !workerInflight && scheduledDocument === document;
  return {
    scheduleWorker: !workerInflight,
    seedFrame: null,
    precomposedPixels: undefined,
    warmAssembleOnly: true,
    cancelInflight: false,
    clearScheduledDocument: stickyWithoutInflight,
  };
}
