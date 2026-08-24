import { describe, expect, it, vi } from 'vitest';
import { createDocument, createPixelLayer } from '@freetoshop/core-document';
import { PersistentImageWorkerClient, runImageWorkerTask } from './client.js';
import type { ImageWorkerEvent, ImageWorkerRequest } from './protocol.js';

class FakeWorker {
  onmessage: ((event: MessageEvent<ImageWorkerEvent>) => void) | null = null;
  onerror: ((event: ErrorEvent) => void) | null = null;
  readonly terminate = vi.fn();
  readonly postMessage = vi.fn((request: { id: string }) => {
    queueMicrotask(() => {
      this.onmessage?.(
        new MessageEvent('message', {
          data: {
            id: request.id,
            type: 'filter-patch',
            patch: {
              x: 0,
              y: 0,
              width: 1,
              height: 1,
              pixels: new Uint8ClampedArray([254, 253, 252, 4]),
            },
          } satisfies ImageWorkerEvent,
        }),
      );
      this.onmessage?.(
        new MessageEvent('message', {
          data: { id: request.id, type: 'progress', progress: 1.5 } satisfies ImageWorkerEvent,
        }),
      );
      this.onmessage?.(
        new MessageEvent('message', {
          data: {
            id: request.id,
            type: 'result',
            result: {
              kind: 'filter',
              patches: [],
            },
          } satisfies ImageWorkerEvent,
        }),
      );
    });
  });
}

const task = {
  kind: 'filter' as const,
  layer: createPixelLayer({
    id: 'pixel',
    name: 'Pixel',
    width: 1,
    height: 1,
    pixels: new Uint8ClampedArray([1, 2, 3, 4]),
  }),
  selection: null,
  worldTransform: { a: 1, b: 0, c: 0, d: 1, e: 0, f: 0 },
  draft: {
    kind: 'invert' as const,
    contrast: 0,
    inputBlack: 0,
    gamma: 1,
    inputWhite: 255,
    outputBlack: 0,
    outputWhite: 255,
    hue: 0,
    saturation: 0,
    lightness: 0,
    threshold: 128,
    posterizeLevels: 4,
    blurRadius: 0,
  },
};

const resizeTask = {
  kind: 'resize' as const,
  document: createDocument({
    id: 'resize',
    name: 'Resize',
    width: 1,
    height: 1,
    layers: [task.layer],
  }),
  width: 1,
  height: 1,
  method: 'nearest' as const,
};

describe('runImageWorkerTask', () => {
  it('reports progress, resolves a result, and terminates its worker', async () => {
    const worker = new FakeWorker();
    const onProgress = vi.fn();
    await expect(
      runImageWorkerTask(task, {
        createWorker: () => worker as unknown as Worker,
        onProgress,
      }),
    ).resolves.toMatchObject({
      kind: 'filter',
      patches: [{ x: 0, y: 0, width: 1, height: 1 }],
    });
    expect(onProgress).toHaveBeenCalledWith(1);
    expect(worker.terminate).toHaveBeenCalledOnce();
  });

  it('terminates and rejects with AbortError when cancelled', async () => {
    const worker = new FakeWorker();
    worker.postMessage.mockImplementation(() => undefined);
    const controller = new AbortController();
    const pending = runImageWorkerTask(task, {
      createWorker: () => worker as unknown as Worker,
      signal: controller.signal,
    });
    controller.abort();
    await expect(pending).rejects.toMatchObject({ name: 'AbortError' });
    expect(worker.terminate).toHaveBeenCalledOnce();
  });

  it('discards streamed patches and terminates when cancelled before the terminal result', async () => {
    const worker = new FakeWorker();
    worker.postMessage.mockImplementation((request: { id: string }) => {
      queueMicrotask(() => {
        worker.onmessage?.(
          new MessageEvent('message', {
            data: {
              id: request.id,
              type: 'filter-patch',
              patch: {
                x: 0,
                y: 0,
                width: 1,
                height: 1,
                pixels: new Uint8ClampedArray([254, 253, 252, 4]),
              },
            } satisfies ImageWorkerEvent,
          }),
        );
      });
    });
    const controller = new AbortController();
    const pending = runImageWorkerTask(task, {
      createWorker: () => worker as unknown as Worker,
      signal: controller.signal,
    });
    await Promise.resolve();
    controller.abort();
    await expect(pending).rejects.toMatchObject({ name: 'AbortError' });
    expect(worker.terminate).toHaveBeenCalledOnce();
  });

  it('normalizes regressing worker progress into a finite monotonic sequence', async () => {
    const worker = new FakeWorker();
    worker.postMessage.mockImplementation((request: { id: string }) => {
      queueMicrotask(() => {
        for (const progress of [0.7, 0.3, 1.5]) {
          worker.onmessage?.(
            new MessageEvent('message', {
              data: { id: request.id, type: 'progress', progress } satisfies ImageWorkerEvent,
            }),
          );
        }
        worker.onmessage?.(
          new MessageEvent('message', {
            data: {
              id: request.id,
              type: 'result',
              result: { kind: 'filter', patches: [] },
            } satisfies ImageWorkerEvent,
          }),
        );
      });
    });
    const onProgress = vi.fn();
    await runImageWorkerTask(task, { createWorker: () => worker as unknown as Worker, onProgress });
    expect(onProgress.mock.calls.map(([progress]) => progress)).toEqual([0.7, 0.7, 1]);
  });

  it('does not create a worker for an already-aborted task', async () => {
    const controller = new AbortController();
    const createWorker = vi.fn();
    controller.abort();
    await expect(
      runImageWorkerTask(task, { signal: controller.signal, createWorker }),
    ).rejects.toMatchObject({ name: 'AbortError' });
    expect(createWorker).not.toHaveBeenCalled();
  });
});

describe('PersistentImageWorkerClient', () => {
  it('discards privately assembled resize tiles and resets the session on cancellation', async () => {
    const worker = new FakeWorker();
    worker.postMessage.mockImplementation((request: { id: string }) => {
      queueMicrotask(() => {
        worker.onmessage?.(
          new MessageEvent('message', {
            data: {
              id: request.id,
              type: 'result-resource',
              resourceKind: 'pixel',
              resourceId: 'result-pixel',
              width: 1,
              height: 1,
              tileSize: 256,
              totalTiles: 1,
            } satisfies ImageWorkerEvent,
          }),
        );
        worker.onmessage?.(
          new MessageEvent('message', {
            data: {
              id: request.id,
              type: 'result-resource-tile',
              resourceKind: 'pixel',
              resourceId: 'result-pixel',
              tileIndex: 0,
              column: 0,
              row: 0,
              pixels: new Uint8ClampedArray([1, 2, 3, 4]),
            } satisfies ImageWorkerEvent,
          }),
        );
      });
    });
    const controller = new AbortController();
    const client = new PersistentImageWorkerClient({
      createWorker: () => worker as unknown as Worker,
    });
    const pending = client.run(resizeTask, { signal: controller.signal });
    await Promise.resolve();
    controller.abort();
    await expect(pending).rejects.toMatchObject({ name: 'AbortError' });
    expect(worker.terminate).toHaveBeenCalledOnce();
    expect(client.lastStreamedResultTileCount).toBe(0);
  });

  it('terminates the session and rejects a malformed streamed patch', async () => {
    const worker = new FakeWorker();
    worker.postMessage.mockImplementation((request: { id: string }) => {
      queueMicrotask(() => {
        worker.onmessage?.(
          new MessageEvent('message', {
            data: {
              id: request.id,
              type: 'filter-patch',
              patch: {
                x: 0,
                y: 0,
                width: 2,
                height: 1,
                pixels: new Uint8ClampedArray(8),
              },
            } satisfies ImageWorkerEvent,
          }),
        );
      });
    });
    const client = new PersistentImageWorkerClient({
      createWorker: () => worker as unknown as Worker,
    });
    await expect(client.run(task)).rejects.toThrow('exceeds the layer bounds');
    expect(worker.terminate).toHaveBeenCalledOnce();
    expect(client.lastStreamedPatchCount).toBe(0);
  });

  it('uploads an immutable tiled resource once and references it on later tasks', async () => {
    const worker = new FakeWorker();
    const createWorker = vi.fn(() => worker as unknown as Worker);
    const client = new PersistentImageWorkerClient({ createWorker });

    await expect(client.run(task)).resolves.toMatchObject({ kind: 'filter' });
    expect(client.lastTransferByteLength).toBe(4);
    const firstRequest = worker.postMessage.mock.calls[0]?.[0] as unknown as ImageWorkerRequest;
    expect(firstRequest.task.kind).toBe('filter');
    if (firstRequest.task.kind !== 'filter') throw new Error('Expected filter task');
    expect(firstRequest.task.layer.pixelStore.resource).toBe('snapshot');

    await expect(client.run(task)).resolves.toMatchObject({ kind: 'filter' });
    expect(client.lastTransferByteLength).toBe(0);
    const secondRequest = worker.postMessage.mock.calls[1]?.[0] as unknown as ImageWorkerRequest;
    if (secondRequest.task.kind !== 'filter') throw new Error('Expected filter task');
    expect(secondRequest.task.layer.pixelStore).toMatchObject({
      resource: 'reference',
      id: firstRequest.task.layer.pixelStore.id,
    });
    expect(createWorker).toHaveBeenCalledOnce();
    expect(worker.terminate).not.toHaveBeenCalled();

    client.dispose();
    expect(worker.terminate).toHaveBeenCalledOnce();
  });

  it('terminates on abort and re-uploads resources into a fresh namespace', async () => {
    const first = new FakeWorker();
    const second = new FakeWorker();
    const createWorker = vi
      .fn<() => Worker>()
      .mockReturnValueOnce(first as unknown as Worker)
      .mockReturnValueOnce(second as unknown as Worker);
    const client = new PersistentImageWorkerClient({ createWorker });
    await client.run(task);
    first.postMessage.mockImplementation(() => undefined);
    const controller = new AbortController();
    const pending = client.run(task, { signal: controller.signal });
    controller.abort();
    await expect(pending).rejects.toMatchObject({ name: 'AbortError' });
    expect(first.terminate).toHaveBeenCalledOnce();

    await client.run(task);
    expect(client.lastTransferByteLength).toBe(4);
    const request = second.postMessage.mock.calls[0]?.[0] as unknown as ImageWorkerRequest;
    if (request.task.kind !== 'filter') throw new Error('Expected filter task');
    expect(request.task.layer.pixelStore.resource).toBe('snapshot');
    expect(createWorker).toHaveBeenCalledTimes(2);
    client.dispose();
  });
});
