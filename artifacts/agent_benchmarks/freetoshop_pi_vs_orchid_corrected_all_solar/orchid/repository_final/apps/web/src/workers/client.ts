import {
  adoptWorkerResultResources,
  acceptWorkerResultStreamEvent,
  createWorkerResultStreamContext,
  createWorkerTaskSerializationContext,
  commitWorkerTaskResources,
  deserializeStreamedWorkerResult,
  retainedWorkerTaskResourceIds,
  serializeWorkerTask,
  workerTaskTransferables,
  workerResultStreamAllocatedByteLength,
  workerTaskRetainedResourceStats,
  type ImageWorkerEvent,
  type ImageWorkerResult,
  type ImageWorkerTask,
  type WorkerTaskSerializationContext,
} from './protocol.js';
import type { PixelPatch } from '@freetoshop/core-document';

export interface WorkerTaskOptions {
  readonly signal?: AbortSignal;
  readonly onProgress?: (progress: number) => void;
  readonly createWorker?: () => Worker;
}

export function runImageWorkerTask(
  task: ImageWorkerTask,
  options: WorkerTaskOptions = {},
): Promise<ImageWorkerResult> {
  if (options.signal?.aborted) return Promise.reject(abortError());
  const worker =
    options.createWorker?.() ??
    new Worker(new URL('./imageWorker.ts', import.meta.url), {
      type: 'module',
      name: `freetoshop-${task.kind}`,
    });
  const id = crypto.randomUUID();

  return new Promise((resolve, reject) => {
    let settled = false;
    let lastProgress = 0;
    const streamedPatches: PixelPatch[] = [];
    const resultStream = createWorkerResultStreamContext();
    const finish = (callback: () => void) => {
      if (settled) return;
      settled = true;
      options.signal?.removeEventListener('abort', onAbort);
      worker.terminate();
      callback();
    };
    const onAbort = () => finish(() => reject(abortError()));
    options.signal?.addEventListener('abort', onAbort, { once: true });
    worker.onerror = (event) => {
      finish(() => reject(new Error(event.message || 'Image worker failed.')));
    };
    worker.onmessage = (event: MessageEvent<ImageWorkerEvent>) => {
      const data = event.data;
      if (data.id !== id) return;
      if (data.type === 'progress') {
        try {
          lastProgress = validatedProgress(data.progress, lastProgress);
          options.onProgress?.(lastProgress);
        } catch (error) {
          finish(() => reject(workerEventError(error)));
        }
      } else if (data.type === 'filter-patch') {
        try {
          streamedPatches.push(validatedFilterPatch(task, data.patch));
        } catch (error) {
          finish(() => reject(workerEventError(error)));
        }
      } else if (data.type === 'result-resource' || data.type === 'result-resource-tile') {
        try {
          acceptWorkerResultStreamEvent(resultStream, data);
        } catch (error) {
          finish(() => reject(workerEventError(error)));
        }
      } else if (data.type === 'error') {
        finish(() => reject(new Error(data.message)));
      } else {
        const result = deserializeStreamedWorkerResult(data.result, resultStream);
        finish(() =>
          resolve(
            result.kind === 'filter'
              ? { ...result, patches: [...streamedPatches, ...result.patches] }
              : result,
          ),
        );
      }
    };
    const serializedTask = serializeWorkerTask(task);
    worker.postMessage({ id, task: serializedTask }, workerTaskTransferables(serializedTask));
  });
}

/**
 * Sequential worker session that retains immutable pixel/coverage resources
 * between successful tasks. Abort still terminates the worker and clears every
 * resource reference so cancellation cannot leave stale work or detached state.
 */
export class PersistentImageWorkerClient {
  readonly #createWorker?: () => Worker;
  #worker: Worker | null = null;
  #resources: WorkerTaskSerializationContext = createWorkerTaskSerializationContext();
  #busy = false;
  #lastTransferByteLength = 0;
  #lastStreamedPatchCount = 0;
  #lastStreamedResultTileCount = 0;
  #lastHydratedResultResourceCount = 0;
  #lastStreamedResultAllocatedByteLength = 0;
  #peakStreamedResultAllocatedByteLength = 0;

  constructor(options: Pick<WorkerTaskOptions, 'createWorker'> = {}) {
    this.#createWorker = options.createWorker;
  }

  get lastTransferByteLength(): number {
    return this.#lastTransferByteLength;
  }

  get lastStreamedPatchCount(): number {
    return this.#lastStreamedPatchCount;
  }

  get lastStreamedResultTileCount(): number {
    return this.#lastStreamedResultTileCount;
  }

  get lastHydratedResultResourceCount(): number {
    return this.#lastHydratedResultResourceCount;
  }

  get lastStreamedResultAllocatedByteLength(): number {
    return this.#lastStreamedResultAllocatedByteLength;
  }

  get peakStreamedResultAllocatedByteLength(): number {
    return this.#peakStreamedResultAllocatedByteLength;
  }

  get retainedResourceCount(): number {
    return workerTaskRetainedResourceStats(this.#resources).resourceCount;
  }

  get retainedAllocatedByteLength(): number {
    return workerTaskRetainedResourceStats(this.#resources).allocatedByteLength;
  }

  run(task: ImageWorkerTask, options: Omit<WorkerTaskOptions, 'createWorker'> = {}) {
    if (options.signal?.aborted) return Promise.reject(abortError());
    if (this.#busy) return Promise.reject(new Error('Persistent image worker is already busy.'));
    const worker = this.#worker ?? this.#startWorker(task.kind);
    const id = crypto.randomUUID();
    this.#busy = true;
    this.#lastStreamedPatchCount = 0;
    this.#lastStreamedResultTileCount = 0;
    this.#lastHydratedResultResourceCount = 0;
    this.#lastStreamedResultAllocatedByteLength = 0;
    this.#peakStreamedResultAllocatedByteLength = 0;

    return new Promise<ImageWorkerResult>((resolve, reject) => {
      let settled = false;
      let lastProgress = 0;
      const streamedPatches: PixelPatch[] = [];
      const resultStream = createWorkerResultStreamContext();
      const finish = (callback: () => void, terminate = false) => {
        if (settled) return;
        settled = true;
        this.#busy = false;
        options.signal?.removeEventListener('abort', onAbort);
        worker.onmessage = null;
        worker.onerror = null;
        if (terminate) this.#terminateWorker();
        callback();
      };
      const onAbort = () => finish(() => reject(abortError()), true);
      options.signal?.addEventListener('abort', onAbort, { once: true });
      worker.onerror = (event) => {
        finish(() => reject(new Error(event.message || 'Image worker failed.')), true);
      };
      worker.onmessage = (event: MessageEvent<ImageWorkerEvent>) => {
        const data = event.data;
        if (data.id !== id) return;
        if (data.type === 'progress') {
          try {
            lastProgress = validatedProgress(data.progress, lastProgress);
            options.onProgress?.(lastProgress);
          } catch (error) {
            finish(() => reject(workerEventError(error)), true);
          }
        } else if (data.type === 'filter-patch') {
          try {
            streamedPatches.push(validatedFilterPatch(task, data.patch));
            this.#lastStreamedPatchCount += 1;
          } catch (error) {
            finish(() => reject(workerEventError(error)), true);
          }
        } else if (data.type === 'result-resource' || data.type === 'result-resource-tile') {
          try {
            acceptWorkerResultStreamEvent(resultStream, data);
            if (data.type === 'result-resource-tile') this.#lastStreamedResultTileCount += 1;
            this.#lastHydratedResultResourceCount =
              resultStream.hydratedResources.pixelResources.size +
              resultStream.hydratedResources.coverageResources.size;
            this.#lastStreamedResultAllocatedByteLength =
              workerResultStreamAllocatedByteLength(resultStream);
            this.#peakStreamedResultAllocatedByteLength = Math.max(
              this.#peakStreamedResultAllocatedByteLength,
              this.#lastStreamedResultAllocatedByteLength,
            );
          } catch (error) {
            finish(() => reject(workerEventError(error)), true);
          }
        } else if (data.type === 'error') {
          finish(() => reject(new Error(data.message)));
        } else {
          try {
            const hydrated = deserializeStreamedWorkerResult(data.result, resultStream);
            const result =
              hydrated.kind === 'filter'
                ? { ...hydrated, patches: [...streamedPatches, ...hydrated.patches] }
                : hydrated;
            adoptWorkerResultResources(data.result, result, this.#resources);
            finish(() => resolve(result));
          } catch (error) {
            finish(
              () =>
                reject(
                  error instanceof Error
                    ? error
                    : new Error('Worker result resource adoption failed.'),
                ),
              true,
            );
          }
        }
      };
      try {
        const serializedTask = serializeWorkerTask(task, this.#resources);
        const transferables = workerTaskTransferables(serializedTask);
        this.#lastTransferByteLength = transferables.reduce<number>(
          (bytes, transferable) =>
            bytes + (transferable instanceof ArrayBuffer ? transferable.byteLength : 0),
          0,
        );
        worker.postMessage(
          {
            id,
            task: serializedTask,
            retainResourceIds: retainedWorkerTaskResourceIds(this.#resources),
          },
          transferables,
        );
        commitWorkerTaskResources(serializedTask, this.#resources);
      } catch (error) {
        finish(
          () => reject(error instanceof Error ? error : new Error('Image worker dispatch failed.')),
          true,
        );
      }
    });
  }

  dispose(): void {
    if (!this.#busy) this.#terminateWorker();
  }

  #startWorker(kind: ImageWorkerTask['kind']): Worker {
    const worker =
      this.#createWorker?.() ??
      new Worker(new URL('./imageWorker.ts', import.meta.url), {
        type: 'module',
        name: `freetoshop-persistent-${kind}`,
      });
    this.#worker = worker;
    return worker;
  }

  #terminateWorker(): void {
    this.#worker?.terminate();
    this.#worker = null;
    this.#resources = createWorkerTaskSerializationContext();
    this.#lastTransferByteLength = 0;
    this.#lastStreamedPatchCount = 0;
    this.#lastStreamedResultTileCount = 0;
    this.#lastHydratedResultResourceCount = 0;
    this.#lastStreamedResultAllocatedByteLength = 0;
    this.#peakStreamedResultAllocatedByteLength = 0;
  }
}

function abortError(): DOMException {
  return new DOMException('The operation was cancelled.', 'AbortError');
}

function validatedProgress(progress: number, previous: number): number {
  if (!Number.isFinite(progress)) throw new RangeError('Worker progress must be finite.');
  return Math.max(previous, Math.max(0, Math.min(1, progress)));
}

function validatedFilterPatch(task: ImageWorkerTask, patch: PixelPatch): PixelPatch {
  if (task.kind !== 'filter') {
    throw new RangeError(`Unexpected filter patch for ${task.kind} worker task.`);
  }
  const values = [patch.x, patch.y, patch.width, patch.height];
  if (!values.every(Number.isSafeInteger) || patch.width <= 0 || patch.height <= 0) {
    throw new RangeError('Worker filter patch geometry is invalid.');
  }
  if (
    patch.x < 0 ||
    patch.y < 0 ||
    patch.x + patch.width > task.layer.width ||
    patch.y + patch.height > task.layer.height
  ) {
    throw new RangeError('Worker filter patch exceeds the layer bounds.');
  }
  const expectedLength = patch.width * patch.height * 4;
  if (!(patch.pixels instanceof Uint8ClampedArray) || patch.pixels.length !== expectedLength) {
    throw new RangeError('Worker filter patch buffer length is invalid.');
  }
  return patch;
}

function workerEventError(error: unknown): Error {
  return error instanceof Error ? error : new Error('Worker emitted an invalid event.');
}

export function isAbortError(error: unknown): boolean {
  return (
    typeof error === 'object' && error !== null && 'name' in error && error.name === 'AbortError'
  );
}
