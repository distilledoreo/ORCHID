import {
  createAlphaChannel,
  createDocument,
  createGroupLayer,
  createPixelLayer,
  createRasterMaskFromCoverageStore,
  createSelectionMask,
  createShapeLayer,
  createSmartObjectLayer,
  createTextLayer,
  createVectorMask,
} from '@freetoshop/core-document';
import { TiledCoverageStore, TiledPixelStore } from '@freetoshop/pixel-storage';
import {
  CompositeTileCache,
  compositeDocumentRegion,
  compositeDocumentPixels,
  rasterizeLayerNearest,
} from '@freetoshop/renderer';
import { describe, expect, it } from 'vitest';
import {
  compositeWorkerResultBuffer,
  computeCompositeWorkerPixels,
} from './compositeWorkerTask.js';
import {
  deserializeWorkerTask,
  serializedCoverageSnapshot,
  serializedPixelSnapshot,
  serializeWorkerTask,
  workerTaskTransferables,
  type SerializedImageWorkerTask,
} from './protocol.js';

function advancedFixture() {
  return createDocument({
    id: 'worker-composite-fixture',
    name: 'Worker composite fixture',
    width: 16,
    height: 12,
    layers: [
      createPixelLayer({
        id: 'base',
        name: 'Base',
        width: 16,
        height: 12,
        pixels: new Uint8ClampedArray(16 * 12 * 4).fill(48),
        blendMode: 'normal',
      }),
      createPixelLayer({
        id: 'overlay',
        name: 'Overlay',
        width: 16,
        height: 12,
        pixels: new Uint8ClampedArray(16 * 12 * 4).fill(180),
        blendMode: 'multiply',
        opacity: 0.75,
      }),
    ],
  });
}

function sparseLargeFixture(masked = false) {
  const width = 4096;
  const height = 4096;
  const tileSize = 64;
  const tilePixels = new Uint8ClampedArray(tileSize * tileSize * 4).fill(96);
  const positions = [0, 512, 1024, 2048, 3072, 4032];
  const store = positions.reduce(
    (current, position) =>
      current.write({
        x: position,
        y: position,
        width: tileSize,
        height: tileSize,
        pixels: tilePixels,
      }).store,
    TiledPixelStore.empty(width, height, tileSize),
  );
  const coverageStore = TiledCoverageStore.empty(width, height, tileSize).write({
    x: 0,
    y: 0,
    width: tileSize,
    height: tileSize,
    coverage: new Uint8ClampedArray(tileSize * tileSize).fill(255),
  }).store;
  const farCoverageStore = positions.reduce(
    (current, position) =>
      current.write({
        x: position,
        y: position,
        width: tileSize,
        height: tileSize,
        coverage: new Uint8ClampedArray(tileSize * tileSize).fill(position === 0 ? 128 : 255),
      }).store,
    TiledCoverageStore.empty(width, height, tileSize),
  );
  const rasterMask = masked
    ? createRasterMaskFromCoverageStore(width, height, farCoverageStore, { density: 0.75 })
    : null;
  return createDocument({
    id: 'large-worker-composite-fixture',
    name: 'Large worker composite fixture',
    width,
    height,
    selection: createSelectionMask({ width, height, coverageStore }),
    channels: [
      createAlphaChannel({ id: 'saved-alpha', name: 'Saved alpha', width, height, coverageStore }),
    ],
    layers: [
      createPixelLayer({ id: 'base', name: 'Base', width, height, pixelStore: store, rasterMask }),
      createPixelLayer({
        id: 'overlay',
        name: 'Overlay',
        width,
        height,
        pixelStore: store,
        blendMode: 'multiply',
        opacity: 0.75,
        rasterMask,
      }),
    ],
  });
}

function sparseLargeMaskedGroupFixture() {
  const document = sparseLargeFixture();
  const tileSize = 64;
  const positions = [0, 512, 1024, 2048, 3072, 4032];
  const coverageStore = positions.reduce(
    (current, position) =>
      current.write({
        x: position,
        y: position,
        width: tileSize,
        height: tileSize,
        coverage: new Uint8ClampedArray(tileSize * tileSize).fill(position === 0 ? 128 : 255),
      }).store,
    TiledCoverageStore.empty(document.canvas.width, document.canvas.height, tileSize),
  );
  return createDocument({
    id: 'large-worker-masked-group-fixture',
    name: 'Large worker masked group fixture',
    width: document.canvas.width,
    height: document.canvas.height,
    layers: [
      createGroupLayer({
        id: 'masked-group',
        name: 'Masked group',
        compositing: 'isolated',
        rasterMask: createRasterMaskFromCoverageStore(
          document.canvas.width,
          document.canvas.height,
          coverageStore,
          { density: 0.75 },
        ),
        children: document.layers,
      }),
    ],
  });
}

function sparseLargeVectorMaskedGroupFixture() {
  const document = sparseLargeFixture();
  return createDocument({
    id: 'large-worker-vector-group-fixture',
    name: 'Large worker vector group fixture',
    width: document.canvas.width,
    height: document.canvas.height,
    layers: [
      createGroupLayer({
        id: 'vector-group',
        name: 'Vector group',
        compositing: 'pass-through',
        vectorMask: createVectorMask(
          document.canvas.width,
          document.canvas.height,
          [
            { type: 'move', x: 0, y: 0 },
            { type: 'line', x: 96, y: 0 },
            { type: 'line', x: 96, y: 96 },
            { type: 'line', x: 0, y: 96 },
            { type: 'close' },
          ],
          { density: 0.75 },
        ),
        children: document.layers,
      }),
    ],
  });
}

describe('composite worker task (shipped path)', () => {
  it('matches compositeDocumentPixels after serialize/deserialize transfer', () => {
    const document = advancedFixture();
    const expected = compositeDocumentPixels(document, { rasterize: rasterizeLayerNearest });
    const task = serializeWorkerTask({
      kind: 'composite',
      document,
      generation: 11,
    });
    const transferables = workerTaskTransferables(task);
    const cloned = structuredClone(task, { transfer: transferables }) as SerializedImageWorkerTask;
    const hydrated = deserializeWorkerTask(cloned);
    if (hydrated.kind !== 'composite') throw new Error('Expected composite task');
    const pixels = computeCompositeWorkerPixels(hydrated.document, { tileSize: 8 });
    expect(pixels).toEqual(expected);
    const result = compositeWorkerResultBuffer(hydrated.document, hydrated.generation);
    expect(result.kind).toBe('composite');
    expect(result.generation).toBe(11);
    expect(result.width).toBe(16);
    expect(result.height).toBe(12);
    expect(new Uint8ClampedArray(result.buffer)).toEqual(expected);
  });

  it('echoes generation for stale-result rejection at the client boundary', () => {
    const document = advancedFixture();
    const first = compositeWorkerResultBuffer(document, 1);
    const second = compositeWorkerResultBuffer(document, 2);
    expect(first.generation).toBe(1);
    expect(second.generation).toBe(2);
    expect(new Uint8ClampedArray(first.buffer)).toEqual(new Uint8ClampedArray(second.buffer));
  });

  it('returns a smaller mip buffer with matching protocol metadata', () => {
    const document = advancedFixture();
    const serialized = serializeWorkerTask({
      kind: 'composite',
      document,
      generation: 12,
      mipLevel: 2,
    });
    const hydrated = deserializeWorkerTask(serialized);
    if (hydrated.kind !== 'composite') throw new Error('Expected composite task');
    expect(hydrated.mipLevel).toBe(2);
    const result = compositeWorkerResultBuffer(hydrated.document, hydrated.generation, {
      mipLevel: hydrated.mipLevel,
    });
    expect(result.mipLevel).toBe(2);
    expect(result.width).toBe(4);
    expect(result.height).toBe(3);
    expect(result.buffer.byteLength).toBe(4 * 3 * 4);
  });

  it('progressive viewport worker fill matches a cropped full composite in-viewport', () => {
    const document = advancedFixture();
    const viewport = { x: 0, y: 0, width: 8, height: 6 };
    const progressive = computeCompositeWorkerPixels(document, {
      tileSize: 8,
      viewportRegion: viewport,
      progressiveViewportOnly: true,
    });
    const full = compositeDocumentPixels(document, { rasterize: rasterizeLayerNearest });
    // Viewport corner matches full composite.
    for (let y = 0; y < viewport.height; y += 1) {
      for (let x = 0; x < viewport.width; x += 1) {
        const offset = (y * document.canvas.width + x) * 4;
        expect(progressive.slice(offset, offset + 4)).toEqual(full.slice(offset, offset + 4));
      }
    }
    // Off-viewport remains unfilled (transparent) for progressive first paint.
    const off = (8 * document.canvas.width + 8) * 4;
    expect(progressive[off + 3]).toBe(0);
    const result = compositeWorkerResultBuffer(document, 3, {
      viewportRegion: viewport,
      progressiveViewportOnly: true,
    });
    expect(result.progressiveViewportOnly).toBe(true);
    expect(result.viewportRegion).toEqual(viewport);
  });

  it('imageWorker-equivalent path: deserialize progressive task → result buffer ≡ viewport crop', () => {
    // Mirrors apps/web/src/workers/imageWorker.ts composite branch exactly.
    const document = advancedFixture();
    const viewport = { x: 0, y: 0, width: 8, height: 6 };
    const task = serializeWorkerTask({
      kind: 'composite',
      document,
      generation: 42,
      viewportRegion: viewport,
      progressiveViewportOnly: true,
    });
    const cloned = structuredClone(task, {
      transfer: workerTaskTransferables(task),
    }) as SerializedImageWorkerTask;
    const hydrated = deserializeWorkerTask(cloned);
    if (hydrated.kind !== 'composite') throw new Error('Expected composite task');
    const result = compositeWorkerResultBuffer(hydrated.document, hydrated.generation, {
      viewportRegion: hydrated.viewportRegion,
      progressiveViewportOnly: hydrated.progressiveViewportOnly,
    });
    expect(result.generation).toBe(42);
    expect(result.progressiveViewportOnly).toBe(true);
    const pixels = new Uint8ClampedArray(result.buffer);
    const full = compositeDocumentPixels(document, { rasterize: rasterizeLayerNearest });
    for (let y = 0; y < viewport.height; y += 1) {
      for (let x = 0; x < viewport.width; x += 1) {
        const offset = (y * document.canvas.width + x) * 4;
        expect(pixels.slice(offset, offset + 4)).toEqual(full.slice(offset, offset + 4));
      }
    }
  });

  it('transfers only viewport tiles for an eligible 4096² progressive composite', () => {
    const document = sparseLargeFixture();
    const viewport = { x: 0, y: 0, width: 64, height: 64 };
    const task = serializeWorkerTask({
      kind: 'composite',
      document,
      generation: 43,
      viewportRegion: viewport,
      progressiveViewportOnly: true,
    });
    if (task.kind !== 'composite') throw new Error('Expected composite task');
    expect(task.document.selection).toBeNull();
    expect(task.document.channels).toEqual([]);
    expect(task.document.layers.map((layer) => layer.kind)).toEqual(['pixel', 'pixel']);
    for (const layer of task.document.layers) {
      if (layer.kind !== 'pixel') throw new Error('Expected pixel layer');
      expect(serializedPixelSnapshot(layer.pixelStore).tiles).toHaveLength(1);
    }
    const transferables = workerTaskTransferables(task);
    expect(
      transferables.reduce<number>(
        (bytes, transferable) =>
          bytes + (transferable instanceof ArrayBuffer ? transferable.byteLength : 0),
        0,
      ),
    ).toBe(2 * 64 * 64 * 4);

    const cloned = structuredClone(task, { transfer: transferables }) as SerializedImageWorkerTask;
    const hydrated = deserializeWorkerTask(cloned);
    if (hydrated.kind !== 'composite') throw new Error('Expected composite task');
    const result = compositeWorkerResultBuffer(hydrated.document, hydrated.generation, {
      viewportRegion: hydrated.viewportRegion,
      progressiveViewportOnly: hydrated.progressiveViewportOnly,
    });
    const pixels = new Uint8ClampedArray(result.buffer);
    expect(result.pixelRegion).toEqual(viewport);
    expect(result.width).toBe(64);
    expect(result.height).toBe(64);
    expect(result.canvasWidth).toBe(4096);
    expect(result.canvasHeight).toBe(4096);
    expect(result.buffer.byteLength).toBe(64 * 64 * 4);
    const full = compositeDocumentPixels(document, { rasterize: rasterizeLayerNearest });
    for (let y = 0; y < viewport.height; y += 1) {
      for (let x = 0; x < viewport.width; x += 1) {
        const compactOffset = (y * result.width + x) * 4;
        const fullOffset = (y * document.canvas.width + x) * 4;
        expect(pixels.slice(compactOffset, compactOffset + 4)).toEqual(
          full.slice(fullOffset, fullOffset + 4),
        );
      }
    }
    const cache = new CompositeTileCache({ tileSize: 64, maxBytes: 64 * 64 * 4 });
    if (!result.pixelRegion) throw new Error('Expected compact pixel region');
    cache.seedRegion(document, pixels, result.pixelRegion, result.mipLevel ?? 0);
    expect(
      cache.probeWarmth(document, {
        viewportRegion: viewport,
        progressiveViewportOnly: true,
      }).fullyWarm,
    ).toBe(true);

    const mipResult = compositeWorkerResultBuffer(document, 44, {
      viewportRegion: { x: 0, y: 0, width: 256, height: 256 },
      progressiveViewportOnly: true,
      mipLevel: 2,
    });
    expect(mipResult.pixelRegion).toEqual({ x: 0, y: 0, width: 64, height: 64 });
    expect(mipResult.canvasWidth).toBe(1024);
    expect(mipResult.canvasHeight).toBe(1024);
    expect(mipResult.buffer.byteLength).toBe(64 * 64 * 4);
    const mipPixels = new Uint8ClampedArray(mipResult.buffer);
    for (let y = 0; y < 64; y += 1) {
      for (let x = 0; x < 64; x += 1) {
        const compactOffset = (y * 64 + x) * 4;
        const fullOffset = (y * 4 * document.canvas.width + x * 4) * 4;
        expect(mipPixels.slice(compactOffset, compactOffset + 4)).toEqual(
          full.slice(fullOffset, fullOffset + 4),
        );
      }
    }
  });

  it('transfers only viewport pixel and mask tiles for a masked 4096² progressive composite', () => {
    const document = sparseLargeFixture(true);
    const viewport = { x: 0, y: 0, width: 64, height: 64 };
    const expected = compositeDocumentRegion(document, viewport, {
      rasterize: rasterizeLayerNearest,
    });
    const task = serializeWorkerTask({
      kind: 'composite',
      document,
      generation: 45,
      viewportRegion: viewport,
      progressiveViewportOnly: true,
    });
    if (task.kind !== 'composite') throw new Error('Expected composite task');
    for (const layer of task.document.layers) {
      if (layer.kind !== 'pixel' || layer.rasterMask === null) {
        throw new Error('Expected raster-masked pixel layer');
      }
      expect(serializedPixelSnapshot(layer.pixelStore).tiles).toHaveLength(1);
      expect(serializedCoverageSnapshot(layer.rasterMask.coverageStore).tiles).toHaveLength(1);
    }
    const transferables = workerTaskTransferables(task);
    expect(transferables).toHaveLength(4);
    expect(
      transferables.reduce<number>(
        (bytes, transferable) =>
          bytes + (transferable instanceof ArrayBuffer ? transferable.byteLength : 0),
        0,
      ),
    ).toBe(2 * (64 * 64 * 4 + 64 * 64));

    const cloned = structuredClone(task, { transfer: transferables }) as SerializedImageWorkerTask;
    const hydrated = deserializeWorkerTask(cloned);
    if (hydrated.kind !== 'composite') throw new Error('Expected composite task');
    const result = compositeWorkerResultBuffer(hydrated.document, hydrated.generation, {
      viewportRegion: hydrated.viewportRegion,
      progressiveViewportOnly: hydrated.progressiveViewportOnly,
    });
    expect(result.pixelRegion).toEqual(viewport);
    expect(result.buffer.byteLength).toBe(64 * 64 * 4);
    expect(new Uint8ClampedArray(result.buffer)).toEqual(expected);
  });

  it('transfers only viewport child and group-mask tiles for a masked 4096² group', () => {
    const document = sparseLargeMaskedGroupFixture();
    const viewport = { x: 0, y: 0, width: 64, height: 64 };
    const expected = compositeDocumentRegion(document, viewport, {
      rasterize: rasterizeLayerNearest,
    });
    const task = serializeWorkerTask({
      kind: 'composite',
      document,
      generation: 46,
      viewportRegion: viewport,
      progressiveViewportOnly: true,
    });
    if (task.kind !== 'composite') throw new Error('Expected composite task');
    const [group] = task.document.layers;
    if (group?.kind !== 'group' || group.rasterMask === null) {
      throw new Error('Expected raster-masked group');
    }
    expect(serializedCoverageSnapshot(group.rasterMask.coverageStore).tiles).toHaveLength(1);
    expect(group.children).toHaveLength(2);
    for (const child of group.children) {
      if (child.kind !== 'pixel') throw new Error('Expected pixel child');
      expect(serializedPixelSnapshot(child.pixelStore).tiles).toHaveLength(1);
    }
    const transferables = workerTaskTransferables(task);
    expect(transferables).toHaveLength(3);
    expect(
      transferables.reduce<number>(
        (bytes, transferable) =>
          bytes + (transferable instanceof ArrayBuffer ? transferable.byteLength : 0),
        0,
      ),
    ).toBe(2 * 64 * 64 * 4 + 64 * 64);

    const cloned = structuredClone(task, { transfer: transferables }) as SerializedImageWorkerTask;
    const hydrated = deserializeWorkerTask(cloned);
    if (hydrated.kind !== 'composite') throw new Error('Expected composite task');
    const result = compositeWorkerResultBuffer(hydrated.document, hydrated.generation, {
      viewportRegion: hydrated.viewportRegion,
      progressiveViewportOnly: hydrated.progressiveViewportOnly,
    });
    expect(result.pixelRegion).toEqual(viewport);
    expect(result.buffer.byteLength).toBe(64 * 64 * 4);
    expect(new Uint8ClampedArray(result.buffer)).toEqual(expected);
  });

  it('transfers only viewport child tiles for a vector-masked 4096² group', () => {
    const document = sparseLargeVectorMaskedGroupFixture();
    const viewport = { x: 0, y: 0, width: 64, height: 64 };
    const expected = compositeDocumentRegion(document, viewport, {
      rasterize: rasterizeLayerNearest,
    });
    const task = serializeWorkerTask({
      kind: 'composite',
      document,
      generation: 47,
      viewportRegion: viewport,
      progressiveViewportOnly: true,
    });
    if (task.kind !== 'composite') throw new Error('Expected composite task');
    const [group] = task.document.layers;
    if (group?.kind !== 'group' || group.vectorMask === null) {
      throw new Error('Expected vector-masked group');
    }
    expect(group.rasterMask).toBeNull();
    expect(group.children).toHaveLength(2);
    for (const child of group.children) {
      if (child.kind !== 'pixel') throw new Error('Expected pixel child');
      expect(serializedPixelSnapshot(child.pixelStore).tiles).toHaveLength(1);
    }
    const transferables = workerTaskTransferables(task);
    expect(transferables).toHaveLength(2);
    expect(
      transferables.reduce<number>(
        (bytes, transferable) =>
          bytes + (transferable instanceof ArrayBuffer ? transferable.byteLength : 0),
        0,
      ),
    ).toBe(2 * 64 * 64 * 4);

    const cloned = structuredClone(task, { transfer: transferables }) as SerializedImageWorkerTask;
    const hydrated = deserializeWorkerTask(cloned);
    if (hydrated.kind !== 'composite') throw new Error('Expected composite task');
    const result = compositeWorkerResultBuffer(hydrated.document, hydrated.generation, {
      viewportRegion: hydrated.viewportRegion,
      progressiveViewportOnly: hydrated.progressiveViewportOnly,
    });
    expect(result.pixelRegion).toEqual(viewport);
    expect(result.buffer.byteLength).toBe(64 * 64 * 4);
    expect(new Uint8ClampedArray(result.buffer)).toEqual(expected);
  });

  it('transfers and composites only inverse-mapped tiles for affine 4096² pixels', () => {
    const source = sparseLargeFixture();
    const transform = { a: 1.2, b: 0.15, c: -0.08, d: 0.9, e: 8.5, f: 6.25 };
    const document = createDocument({
      id: 'large-worker-affine-fixture',
      name: 'Large worker affine fixture',
      width: source.canvas.width,
      height: source.canvas.height,
      layers: source.layers.map((layer) => ({
        ...layer,
        transform,
        effects: [
          {
            type: 'drop-shadow' as const,
            enabled: true,
            opacity: 0.75,
            offsetX: 6,
            offsetY: 4,
            blur: 4,
            color: { red: 0, green: 0, blue: 0, alpha: 255 },
          },
          {
            type: 'stroke' as const,
            enabled: true,
            opacity: 0.6,
            size: 3,
            color: { red: 255, green: 255, blue: 255, alpha: 255 },
          },
        ],
      })),
    });
    const viewport = { x: 0, y: 0, width: 64, height: 64 };
    const expected = compositeDocumentRegion(document, viewport, {
      rasterize: rasterizeLayerNearest,
    });
    const task = serializeWorkerTask({
      kind: 'composite',
      document,
      generation: 48,
      viewportRegion: viewport,
      progressiveViewportOnly: true,
    });
    if (task.kind !== 'composite') throw new Error('Expected composite task');
    for (const layer of task.document.layers) {
      if (layer.kind !== 'pixel') throw new Error('Expected pixel layer');
      expect(serializedPixelSnapshot(layer.pixelStore).tiles).toHaveLength(1);
    }
    const transferables = workerTaskTransferables(task);
    expect(transferables).toHaveLength(2);
    expect(
      transferables.reduce<number>(
        (bytes, transferable) =>
          bytes + (transferable instanceof ArrayBuffer ? transferable.byteLength : 0),
        0,
      ),
    ).toBe(2 * 64 * 64 * 4);

    const cloned = structuredClone(task, { transfer: transferables }) as SerializedImageWorkerTask;
    const hydrated = deserializeWorkerTask(cloned);
    if (hydrated.kind !== 'composite') throw new Error('Expected composite task');
    const result = compositeWorkerResultBuffer(hydrated.document, hydrated.generation, {
      viewportRegion: hydrated.viewportRegion,
      progressiveViewportOnly: hydrated.progressiveViewportOnly,
    });
    expect(result.buffer.byteLength).toBe(64 * 64 * 4);
    expect(new Uint8ClampedArray(result.buffer)).toEqual(expected);
  });

  it('transfers editable 4096² shapes as metadata and returns an exact compact frame', () => {
    const size = 4096;
    const document = createDocument({
      id: 'large-worker-shape-fixture',
      name: 'Large worker shape fixture',
      width: size,
      height: size,
      layers: [
        createShapeLayer({
          id: 'shape',
          name: 'Shape',
          geometry: { type: 'ellipse', width: size, height: size, cornerRadius: 0 },
          style: {
            fill: { red: 70, green: 140, blue: 220, alpha: 255 },
            stroke: { red: 255, green: 255, blue: 255, alpha: 255 },
            strokeWidth: 6,
          },
          transform: { a: 1.05, b: 0.08, c: -0.06, d: 0.95, e: 4.5, f: 3.25 },
          effects: [
            {
              type: 'drop-shadow',
              enabled: true,
              opacity: 0.7,
              offsetX: 6,
              offsetY: 4,
              blur: 4,
              color: { red: 0, green: 0, blue: 0, alpha: 255 },
            },
          ],
        }),
      ],
    });
    const viewport = { x: 0, y: 0, width: 64, height: 64 };
    const expected = compositeDocumentRegion(document, viewport, {
      rasterize: rasterizeLayerNearest,
    });
    const task = serializeWorkerTask({
      kind: 'composite',
      document,
      generation: 49,
      viewportRegion: viewport,
      progressiveViewportOnly: true,
    });
    if (task.kind !== 'composite') throw new Error('Expected composite task');
    expect(task.document.layers[0]?.kind).toBe('shape');
    expect(workerTaskTransferables(task)).toHaveLength(0);

    const hydrated = deserializeWorkerTask(structuredClone(task));
    if (hydrated.kind !== 'composite') throw new Error('Expected composite task');
    const result = compositeWorkerResultBuffer(hydrated.document, hydrated.generation, {
      viewportRegion: hydrated.viewportRegion,
      progressiveViewportOnly: hydrated.progressiveViewportOnly,
    });
    expect(result.pixelRegion).toEqual(viewport);
    expect(result.buffer.byteLength).toBe(64 * 64 * 4);
    expect(new Uint8ClampedArray(result.buffer)).toEqual(expected);
  }, 15_000);

  it('transfers editable 4096² text as metadata and returns an exact compact frame', () => {
    const size = 4096;
    const document = createDocument({
      id: 'large-worker-text-fixture',
      name: 'Large worker text fixture',
      width: size,
      height: size,
      layers: [
        createTextLayer({
          id: 'text',
          name: 'Text',
          text: 'Progressive text',
          width: size,
          height: size,
          style: {
            fontFamily: 'sans-serif',
            fontSize: 52,
            color: { red: 80, green: 160, blue: 230, alpha: 255 },
          },
          transform: { a: 1.05, b: 0.08, c: -0.06, d: 0.95, e: 4.5, f: 3.25 },
          effects: [
            {
              type: 'stroke',
              enabled: true,
              opacity: 0.8,
              size: 3,
              color: { red: 255, green: 255, blue: 255, alpha: 255 },
            },
          ],
        }),
      ],
    });
    const viewport = { x: 0, y: 0, width: 64, height: 64 };
    const expected = compositeDocumentRegion(document, viewport, {
      rasterize: rasterizeLayerNearest,
    });
    const task = serializeWorkerTask({
      kind: 'composite',
      document,
      generation: 50,
      viewportRegion: viewport,
      progressiveViewportOnly: true,
    });
    if (task.kind !== 'composite') throw new Error('Expected composite task');
    expect(task.document.layers[0]?.kind).toBe('text');
    expect(workerTaskTransferables(task)).toHaveLength(0);

    const hydrated = deserializeWorkerTask(structuredClone(task));
    if (hydrated.kind !== 'composite') throw new Error('Expected composite task');
    const result = compositeWorkerResultBuffer(hydrated.document, hydrated.generation, {
      viewportRegion: hydrated.viewportRegion,
      progressiveViewportOnly: hydrated.progressiveViewportOnly,
    });
    expect(result.pixelRegion).toEqual(viewport);
    expect(result.buffer.byteLength).toBe(64 * 64 * 4);
    expect(new Uint8ClampedArray(result.buffer)).toEqual(expected);
  });

  it('transfers only recursive source tiles for a filtered 4096² smart object', () => {
    const size = 4096;
    const tileSize = 64;
    const tile = new Uint8ClampedArray(tileSize * tileSize * 4).fill(130);
    const positions = [0, 512, 1024, 2048, 3072, 4032];
    const store = positions.reduce(
      (current, position) =>
        current.write({
          x: position,
          y: position,
          width: tileSize,
          height: tileSize,
          pixels: tile,
        }).store,
      TiledPixelStore.empty(size, size, tileSize),
    );
    const document = createDocument({
      id: 'large-worker-smart-fixture',
      name: 'Large worker smart fixture',
      width: size,
      height: size,
      layers: [
        createSmartObjectLayer({
          id: 'smart',
          name: 'Smart',
          source: {
            id: 'smart-source',
            name: 'Smart source',
            canvas: { width: size, height: size },
            layers: [
              createPixelLayer({
                id: 'embedded',
                name: 'Embedded',
                width: size,
                height: size,
                pixelStore: store,
              }),
            ],
          },
          smartFilters: [
            { id: 'blur', type: 'gaussian-blur', enabled: true, opacity: 1, radius: 4 },
            { id: 'invert', type: 'invert', enabled: true, opacity: 0.5 },
          ],
          transform: { a: 1.05, b: 0.08, c: -0.06, d: 0.95, e: 4.5, f: 3.25 },
        }),
      ],
    });
    const viewport = { x: 0, y: 0, width: 64, height: 64 };
    const expected = compositeDocumentRegion(document, viewport, {
      rasterize: rasterizeLayerNearest,
    });
    const task = serializeWorkerTask({
      kind: 'composite',
      document,
      generation: 51,
      viewportRegion: viewport,
      progressiveViewportOnly: true,
    });
    if (task.kind !== 'composite') throw new Error('Expected composite task');
    const [serializedSmart] = task.document.layers;
    if (serializedSmart?.kind !== 'smart-object') throw new Error('Expected smart object');
    const [serializedEmbedded] = serializedSmart.source.layers;
    if (serializedEmbedded?.kind !== 'pixel') throw new Error('Expected embedded pixel layer');
    expect(serializedPixelSnapshot(serializedEmbedded.pixelStore).tiles).toHaveLength(1);
    const transferables = workerTaskTransferables(task);
    expect(transferables).toHaveLength(1);
    expect(transferables[0]).toBeInstanceOf(ArrayBuffer);
    expect((transferables[0] as ArrayBuffer).byteLength).toBe(64 * 64 * 4);

    const cloned = structuredClone(task, { transfer: transferables }) as SerializedImageWorkerTask;
    const hydrated = deserializeWorkerTask(cloned);
    if (hydrated.kind !== 'composite') throw new Error('Expected composite task');
    const result = compositeWorkerResultBuffer(hydrated.document, hydrated.generation, {
      viewportRegion: hydrated.viewportRegion,
      progressiveViewportOnly: hydrated.progressiveViewportOnly,
    });
    expect(result.pixelRegion).toEqual(viewport);
    expect(result.buffer.byteLength).toBe(64 * 64 * 4);
    expect(new Uint8ClampedArray(result.buffer)).toEqual(expected);
  });
});
