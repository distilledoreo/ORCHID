import { createDocument, createPixelLayer } from '@freetoshop/core-document';
import { compositeDocumentPixels, rasterizeLayerNearest } from '@freetoshop/renderer';
import { describe, expect, it, vi } from 'vitest';
import { compositeWorkerResultBuffer } from '../workers/compositeWorkerTask.js';
import type { ImageWorkerEvent, ImageWorkerRequest } from '../workers/protocol.js';
import {
  createWorkerTaskDeserializationContext,
  deserializeWorkerTask,
  pruneWorkerTaskDeserializationContext,
} from '../workers/protocol.js';
import { planAdvancedCompositeRender } from './compositeRenderPlan.js';
import { CompositeWorkerScheduler } from './compositeWorkerScheduler.js';

function advancedDocument(id = 'document') {
  return createDocument({
    id,
    name: 'Document',
    width: 2,
    height: 2,
    layers: [
      createPixelLayer({
        id: 'a',
        name: 'A',
        width: 2,
        height: 2,
        pixels: new Uint8ClampedArray(16).fill(40),
      }),
      createPixelLayer({
        id: 'b',
        name: 'B',
        width: 2,
        height: 2,
        pixels: new Uint8ClampedArray(16).fill(200),
        blendMode: 'multiply',
      }),
    ],
  });
}

class CompositeFakeWorker {
  onmessage: ((event: MessageEvent<ImageWorkerEvent>) => void) | null = null;
  onerror: ((event: ErrorEvent) => void) | null = null;
  readonly terminate = vi.fn();
  readonly resourceContext = createWorkerTaskDeserializationContext();
  readonly postMessage = vi.fn((request: ImageWorkerRequest) => {
    queueMicrotask(() => {
      this.onmessage?.(
        new MessageEvent('message', {
          data: { id: request.id, type: 'progress', progress: 0.5 } satisfies ImageWorkerEvent,
        }),
      );
      const width = 2;
      const height = 2;
      const buffer = new ArrayBuffer(width * height * 4);
      new Uint8ClampedArray(buffer).fill(90);
      this.onmessage?.(
        new MessageEvent('message', {
          data: {
            id: request.id,
            type: 'result',
            result: {
              kind: 'composite',
              buffer,
              width,
              height,
              generation: request.task.kind === 'composite' ? request.task.generation : 0,
            },
          } satisfies ImageWorkerEvent,
        }),
      );
    });
  });
}

describe('CompositeWorkerScheduler', () => {
  it('resolves a composite frame and rejects stale generations', async () => {
    const onFrame = vi.fn();
    const onProgress = vi.fn();
    const scheduler = new CompositeWorkerScheduler({
      createWorker: () => new CompositeFakeWorker() as unknown as Worker,
      onFrame,
      onProgress,
    });
    const document = advancedDocument();
    const generation = scheduler.schedule(document);
    expect(scheduler.inflight).toBe(true);
    await vi.waitFor(() => expect(onFrame).toHaveBeenCalledOnce());
    expect(onProgress).toHaveBeenCalledWith(0.5, generation);
    const frame = scheduler.frameFor(document);
    expect(frame?.generation).toBe(generation);
    expect(frame?.pixels.length).toBe(16);
    expect(frame?.pixels[0]).toBe(90);
    expect(scheduler.inflight).toBe(false);

    scheduler.schedule(document);
    expect(scheduler.frameFor(document)).toBeNull();
  });

  it('cancels in-flight work and ignores aborted completions', async () => {
    const worker = new CompositeFakeWorker();
    worker.postMessage.mockImplementation(() => undefined);
    const onFrame = vi.fn();
    const onError = vi.fn();
    const scheduler = new CompositeWorkerScheduler({
      createWorker: () => worker as unknown as Worker,
      onFrame,
      onError,
    });
    scheduler.schedule(advancedDocument());
    expect(scheduler.inflight).toBe(true);
    scheduler.cancel();
    expect(scheduler.inflight).toBe(false);
    expect(onFrame).not.toHaveBeenCalled();
    expect(onError).not.toHaveBeenCalled();
  });

  it('rejects frames that do not match document identity or canvas size', async () => {
    const scheduler = new CompositeWorkerScheduler({
      createWorker: () => new CompositeFakeWorker() as unknown as Worker,
    });
    const document = advancedDocument('doc-a');
    scheduler.schedule(document);
    await vi.waitFor(() => expect(scheduler.latestFrame).not.toBeNull());
    expect(scheduler.frameFor(advancedDocument('doc-b'))).toBeNull();
    const resized = createDocument({
      id: 'doc-a',
      name: 'Resized',
      width: 4,
      height: 4,
      layers: document.layers,
    });
    expect(scheduler.frameFor(resized)).toBeNull();
  });

  it('runs the real composite task path and matches reference pixels', async () => {
    class RealCompositeWorker {
      onmessage: ((event: MessageEvent<ImageWorkerEvent>) => void) | null = null;
      onerror: ((event: ErrorEvent) => void) | null = null;
      readonly terminate = vi.fn();
      readonly resourceContext = createWorkerTaskDeserializationContext();
      readonly postMessage = vi.fn((request: ImageWorkerRequest) => {
        queueMicrotask(() => {
          const task = deserializeWorkerTask(request.task, this.resourceContext);
          if (request.retainResourceIds !== undefined) {
            pruneWorkerTaskDeserializationContext(this.resourceContext, request.retainResourceIds);
          }
          if (task.kind !== 'composite') {
            this.onmessage?.(
              new MessageEvent('message', {
                data: {
                  id: request.id,
                  type: 'error',
                  message: 'Expected composite task',
                } satisfies ImageWorkerEvent,
              }),
            );
            return;
          }
          // Same call imageWorker.ts makes for kind === 'composite'.
          const result = compositeWorkerResultBuffer(task.document, task.generation, {
            viewportRegion: task.viewportRegion,
            progressiveViewportOnly: task.progressiveViewportOnly,
            mipLevel: task.mipLevel,
          });
          this.onmessage?.(
            new MessageEvent('message', {
              data: {
                id: request.id,
                type: 'result',
                result,
              } satisfies ImageWorkerEvent,
            }),
          );
        });
      });
    }

    const document = advancedDocument();
    const expected = compositeDocumentPixels(document, { rasterize: rasterizeLayerNearest });
    const scheduler = new CompositeWorkerScheduler({
      createWorker: () => new RealCompositeWorker() as unknown as Worker,
    });
    scheduler.schedule(document);
    await vi.waitFor(() => expect(scheduler.latestFrame).not.toBeNull());
    const frame = scheduler.frameFor(document);
    expect(frame?.pixels).toEqual(expected);
  });

  it('keeps mip frames isolated and preserves their smaller dimensions', async () => {
    class MipWorker extends CompositeFakeWorker {
      override readonly postMessage = vi.fn((request: ImageWorkerRequest) => {
        queueMicrotask(() => {
          const task = deserializeWorkerTask(request.task, this.resourceContext);
          if (task.kind !== 'composite') return;
          const result = compositeWorkerResultBuffer(task.document, task.generation, {
            mipLevel: task.mipLevel,
          });
          this.onmessage?.(
            new MessageEvent('message', {
              data: { id: request.id, type: 'result', result } satisfies ImageWorkerEvent,
            }),
          );
        });
      });
    }
    const document = advancedDocument();
    const scheduler = new CompositeWorkerScheduler({
      createWorker: () => new MipWorker() as unknown as Worker,
    });
    scheduler.schedule(document, { mipLevel: 1 });
    await vi.waitFor(() => expect(scheduler.latestFrame).not.toBeNull());
    expect(scheduler.frameFor(document, scheduler.generation, 0)).toBeNull();
    const mipFrame = scheduler.frameFor(document, scheduler.generation, 1);
    expect(mipFrame?.mipLevel).toBe(1);
    expect(mipFrame?.width).toBe(1);
    expect(mipFrame?.height).toBe(1);
    expect(mipFrame?.pixels.byteLength).toBe(4);
  });

  it('real progressive path + cancel leaves no stale frame after generation advance', async () => {
    class RealProgressiveWorker {
      onmessage: ((event: MessageEvent<ImageWorkerEvent>) => void) | null = null;
      onerror: ((event: ErrorEvent) => void) | null = null;
      readonly terminate = vi.fn();
      readonly resourceContext = createWorkerTaskDeserializationContext();
      readonly postMessage = vi.fn((request: ImageWorkerRequest) => {
        // Delayed so cancel can win the race.
        setTimeout(() => {
          const task = deserializeWorkerTask(request.task, this.resourceContext);
          if (task.kind !== 'composite') return;
          const result = compositeWorkerResultBuffer(task.document, task.generation, {
            viewportRegion: task.viewportRegion,
            progressiveViewportOnly: task.progressiveViewportOnly,
          });
          this.onmessage?.(
            new MessageEvent('message', {
              data: { id: request.id, type: 'result', result } satisfies ImageWorkerEvent,
            }),
          );
        }, 30);
      });
    }

    const document = advancedDocument();
    const onFrame = vi.fn();
    const scheduler = new CompositeWorkerScheduler({
      createWorker: () => new RealProgressiveWorker() as unknown as Worker,
      onFrame,
    });
    const generation = scheduler.schedule(document, {
      viewportRegion: { x: 0, y: 0, width: 2, height: 2 },
      progressiveViewportOnly: true,
      chainFullRefine: false,
    });
    expect(generation).toBeGreaterThan(0);
    expect(scheduler.inflight).toBe(true);
    scheduler.cancel();
    expect(scheduler.inflight).toBe(false);
    await new Promise((resolve) => setTimeout(resolve, 60));
    expect(onFrame).not.toHaveBeenCalled();
    expect(scheduler.frameFor(document)).toBeNull();
  });

  it('chains full-canvas refine after progressive and keeps progressive frame usable', async () => {
    class RealChainedWorker {
      onmessage: ((event: MessageEvent<ImageWorkerEvent>) => void) | null = null;
      onerror: ((event: ErrorEvent) => void) | null = null;
      readonly terminate = vi.fn();
      readonly resourceContext = createWorkerTaskDeserializationContext();
      readonly postMessage = vi.fn((request: ImageWorkerRequest) => {
        queueMicrotask(() => {
          const task = deserializeWorkerTask(request.task, this.resourceContext);
          if (request.retainResourceIds !== undefined) {
            pruneWorkerTaskDeserializationContext(this.resourceContext, request.retainResourceIds);
          }
          if (task.kind !== 'composite') return;
          const result = compositeWorkerResultBuffer(task.document, task.generation, {
            viewportRegion: task.viewportRegion,
            progressiveViewportOnly: task.progressiveViewportOnly,
          });
          this.onmessage?.(
            new MessageEvent('message', {
              data: { id: request.id, type: 'result', result } satisfies ImageWorkerEvent,
            }),
          );
        });
      });
    }

    const document = advancedDocument();
    const expected = compositeDocumentPixels(document, { rasterize: rasterizeLayerNearest });
    const onFrame = vi.fn();
    const scheduler = new CompositeWorkerScheduler({
      createWorker: () => new RealChainedWorker() as unknown as Worker,
      onFrame,
    });
    scheduler.schedule(document, {
      viewportRegion: { x: 0, y: 0, width: 2, height: 2 },
      progressiveViewportOnly: true,
    });
    // First frame: progressive viewport.
    await vi.waitFor(() => expect(onFrame).toHaveBeenCalled());
    const progressive = onFrame.mock.calls[0]?.[0] as {
      progressiveViewportOnly?: boolean;
      pixels: Uint8ClampedArray;
    };
    expect(progressive.progressiveViewportOnly).toBe(true);
    // Progressive remains frameFor-usable while full refine is scheduled/inflight or after.
    expect(scheduler.frameFor(document)?.pixels).toBeDefined();
    // Second frame: full-canvas refine (not progressive).
    await vi.waitFor(() => expect(onFrame).toHaveBeenCalledTimes(2));
    const full = onFrame.mock.calls[1]?.[0] as {
      progressiveViewportOnly?: boolean;
      pixels: Uint8ClampedArray;
    };
    expect(full.progressiveViewportOnly).toBeUndefined();
    expect(full.pixels).toEqual(expected);
    expect(scheduler.frameFor(document)?.pixels).toEqual(expected);
    expect(scheduler.inflight).toBe(false);
  });

  it('skeptic path: progressive onFrame + chained refine → plan seed does not cancel refine', async () => {
    // End-to-end: scheduler progressive completion chains full refine (inflight=true,
    // preserveLatestWhileInflight). First App-style paint must seed progressive
    // with cancelInflight=false so refine is not aborted (compositeRenderPlan seed).
    class ControlledWorker {
      onmessage: ((event: MessageEvent<ImageWorkerEvent>) => void) | null = null;
      onerror: ((event: ErrorEvent) => void) | null = null;
      readonly terminate = vi.fn();
      readonly resourceContext = createWorkerTaskDeserializationContext();
      readonly pending: Array<() => void> = [];
      readonly postMessage = vi.fn((request: ImageWorkerRequest) => {
        this.pending.push(() => {
          const task = deserializeWorkerTask(request.task, this.resourceContext);
          if (task.kind !== 'composite') return;
          const result = compositeWorkerResultBuffer(task.document, task.generation, {
            viewportRegion: task.viewportRegion,
            progressiveViewportOnly: task.progressiveViewportOnly,
          });
          this.onmessage?.(
            new MessageEvent('message', {
              data: { id: request.id, type: 'result', result } satisfies ImageWorkerEvent,
            }),
          );
        });
      });
      flushOne(): void {
        const next = this.pending.shift();
        next?.();
      }
    }

    const document = advancedDocument();
    const worker = new ControlledWorker();
    const onFrame = vi.fn();
    const scheduler = new CompositeWorkerScheduler({
      createWorker: () => worker as unknown as Worker,
      onFrame,
    });

    // Cold schedule: progressive viewport only (will chain full refine on complete).
    scheduler.schedule(document, {
      viewportRegion: { x: 0, y: 0, width: 2, height: 2 },
      progressiveViewportOnly: true,
    });
    expect(scheduler.inflight).toBe(true);
    expect(worker.pending).toHaveLength(1);

    // Complete progressive — refine is chained BEFORE onFrame so UI first paint
    // already observes inflight/refining.
    worker.flushOne();
    await vi.waitFor(() => expect(onFrame).toHaveBeenCalledTimes(1));
    const progressiveFrame = onFrame.mock.calls[0]?.[0] as {
      generation: number;
      progressiveViewportOnly?: boolean;
      pixels: Uint8ClampedArray;
    };
    expect(progressiveFrame.progressiveViewportOnly).toBe(true);

    // At onFrame time, refine must already be scheduled (not a later microtask).
    expect(scheduler.inflight).toBe(true);
    expect(scheduler.refining).toBe(true);
    await vi.waitFor(() => expect(worker.pending.length).toBeGreaterThanOrEqual(1));
    const frameWhileRefine = scheduler.frameFor(document);
    expect(frameWhileRefine).not.toBeNull();
    expect(frameWhileRefine?.progressiveViewportOnly).toBe(true);
    expect(frameWhileRefine?.generation).toBe(progressiveFrame.generation);

    // App first paint after progressive onFrame: seed without cancelInflight.
    const seedPlan = planAdvancedCompositeRender({
      document,
      fullyWarm: false,
      workerFrame: frameWhileRefine,
      scheduledDocument: document,
      seededGeneration: -1,
      workerInflight: scheduler.inflight,
    });
    expect(seedPlan.seedFrame).toBe(frameWhileRefine);
    expect(seedPlan.cancelInflight).toBe(false);
    expect(seedPlan.scheduleWorker).toBe(false);
    expect(seedPlan.precomposedPixels).toBeUndefined();

    // Refine still inflight after seed decision (App would not call cancel()).
    expect(scheduler.inflight).toBe(true);
    expect(scheduler.refining).toBe(true);

    // Finish refine — full frame lands.
    worker.flushOne();
    await vi.waitFor(() => expect(onFrame).toHaveBeenCalledTimes(2));
    expect(scheduler.inflight).toBe(false);
    const fullFrame = scheduler.frameFor(document);
    expect(fullFrame?.progressiveViewportOnly).not.toBe(true);
  });

  it('reuses tiled inputs across metadata-only revisions with zero transferred bytes', async () => {
    class PersistentCompositeWorker {
      onmessage: ((event: MessageEvent<ImageWorkerEvent>) => void) | null = null;
      onerror: ((event: ErrorEvent) => void) | null = null;
      readonly terminate = vi.fn();
      readonly resourceContext = createWorkerTaskDeserializationContext();
      readonly postMessage = vi.fn((request: ImageWorkerRequest) => {
        queueMicrotask(() => {
          const task = deserializeWorkerTask(request.task, this.resourceContext);
          if (request.retainResourceIds !== undefined) {
            pruneWorkerTaskDeserializationContext(this.resourceContext, request.retainResourceIds);
          }
          if (task.kind !== 'composite') return;
          const result = compositeWorkerResultBuffer(task.document, task.generation);
          this.onmessage?.(
            new MessageEvent('message', {
              data: { id: request.id, type: 'result', result } satisfies ImageWorkerEvent,
            }),
          );
        });
      });
    }

    const worker = new PersistentCompositeWorker();
    const frames: Uint8ClampedArray[] = [];
    const scheduler = new CompositeWorkerScheduler({
      createWorker: () => worker as unknown as Worker,
      onFrame: (frame) => frames.push(frame.pixels),
    });
    const document = advancedDocument();
    scheduler.schedule(document, { chainFullRefine: false });
    await vi.waitFor(() => expect(frames).toHaveLength(1));
    expect(scheduler.lastInputTransferByteLength).toBe(32);

    const opacityRevision = {
      ...document,
      layers: document.layers.map((layer, index) =>
        index === 1 ? { ...layer, opacity: 0.5 } : layer,
      ),
    };
    scheduler.schedule(opacityRevision, { chainFullRefine: false });
    await vi.waitFor(() => expect(frames).toHaveLength(2));
    expect(scheduler.lastInputTransferByteLength).toBe(0);
    expect(frames[1]).toEqual(
      compositeDocumentPixels(opacityRevision, { rasterize: rasterizeLayerNearest }),
    );
    expect(worker.postMessage).toHaveBeenCalledTimes(2);
    expect(worker.terminate).not.toHaveBeenCalled();

    const editedRevision = {
      ...opacityRevision,
      layers: opacityRevision.layers.map((layer, index) =>
        index === 0 && layer.kind === 'pixel'
          ? {
              ...layer,
              pixelStore: layer.pixelStore.write({
                x: 0,
                y: 0,
                width: 1,
                height: 1,
                pixels: new Uint8ClampedArray([255, 0, 0, 255]),
              }).store,
            }
          : layer,
      ),
    };
    scheduler.schedule(editedRevision, { chainFullRefine: false });
    await vi.waitFor(() => expect(frames).toHaveLength(3));
    expect(scheduler.lastInputTransferByteLength).toBe(16);
    expect(frames[2]).toEqual(
      compositeDocumentPixels(editedRevision, { rasterize: rasterizeLayerNearest }),
    );
    expect(worker.resourceContext.pixelResources.size).toBe(2);
    scheduler.clear();
    expect(worker.terminate).toHaveBeenCalledOnce();
  });
});
