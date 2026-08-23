import {
  createSelectionMask,
  createAlphaChannel,
  createAdjustmentLayer,
  createDocument,
  createGroupLayer,
  createPixelLayer,
  createRasterMask,
  createRasterMaskFromCoverageStore,
  createRectangularSelection,
  createVectorMask,
  findLayer,
  findLayerWorldTransform,
  transformPoint,
  type SelectionMask,
} from '@freetoshop/core-document';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { TiledCoverageStore, TiledPixelStore } from '@freetoshop/pixel-storage';
import {
  adjustBrightness,
  adjustBrightnessPixelStore,
  paintMaskStroke,
  paintStroke,
  resampleCoverage,
  resampleCoverageStore,
  resamplePixelStore,
  resamplePixels,
  resizeImage,
  type BrushOptions,
  type MaskBrushOptions,
} from './index.js';

const identity = { a: 1, b: 0, c: 0, d: 1, e: 0, f: 0 } as const;

afterEach(() => vi.restoreAllMocks());

const redBrush: BrushOptions = {
  mode: 'source-over',
  size: 1,
  hardness: 1,
  opacity: 1,
  color: [255, 0, 0, 255],
};

const revealMaskBrush: MaskBrushOptions = {
  mode: 'reveal',
  size: 1,
  hardness: 1,
  opacity: 1,
};

describe('paintMaskStroke', () => {
  it('interpolates sparse points into a continuous mask stroke', () => {
    const mask = createRasterMask(5, 1, new Uint8ClampedArray(5));
    const patch = paintMaskStroke(
      mask,
      identity,
      [
        { x: 0, y: 0 },
        { x: 4, y: 0 },
      ],
      revealMaskBrush,
    );

    expect(patch).toMatchObject({ x: 0, y: 0, width: 5, height: 1 });
    expect(Array.from(patch.coverage)).toEqual([255, 255, 255, 255, 255]);
    expect(Array.from(mask.coverageStore.toContiguous())).toEqual([0, 0, 0, 0, 0]);
  });

  it('supports both reveal and conceal painting', () => {
    const mask = createRasterMask(2, 1, new Uint8ClampedArray([0, 255]));
    const revealed = paintMaskStroke(mask, identity, [{ x: 0, y: 0 }], revealMaskBrush);
    const concealed = paintMaskStroke(mask, identity, [{ x: 1, y: 0 }], {
      ...revealMaskBrush,
      mode: 'conceal',
    });

    expect(Array.from(revealed.coverage)).toEqual([255]);
    expect(Array.from(concealed.coverage)).toEqual([0]);
    expect(Array.from(mask.coverageStore.toContiguous())).toEqual([0, 255]);
  });

  it('samples selection coverage in document coordinates for translated masks', () => {
    const mask = createRasterMask(2, 1, new Uint8ClampedArray(2));
    const selectionCoverage = new Uint8ClampedArray(15);
    selectionCoverage[1 * 5 + 3] = 255;
    const selection: SelectionMask = createSelectionMask({
      width: 5,
      height: 3,
      coverage: selectionCoverage,
    });
    const patch = paintMaskStroke(
      mask,
      { ...identity, e: 2, f: 1 },
      [
        { x: 2, y: 1 },
        { x: 3, y: 1 },
      ],
      revealMaskBrush,
      selection,
    );

    expect(patch).toMatchObject({ x: 1, y: 0, width: 1, height: 1 });
    expect(Array.from(patch.coverage)).toEqual([255]);
  });

  it('inverse-maps rotated mask strokes and samples selections in document space', () => {
    const mask = createRasterMask(3, 1, new Uint8ClampedArray(3));
    const rotation = { a: 0, b: 1, c: -1, d: 0, e: 3, f: 1 };
    const selectionCoverage = new Uint8ClampedArray(25);
    selectionCoverage[2 * 5 + 3] = 255;
    const selection: SelectionMask = createSelectionMask({
      width: 5,
      height: 5,
      coverage: selectionCoverage,
    });

    const patch = paintMaskStroke(
      mask,
      rotation,
      [
        { x: 3, y: 1 },
        { x: 3, y: 3 },
      ],
      revealMaskBrush,
      selection,
    );

    expect(patch).toMatchObject({ x: 1, y: 0, width: 1, height: 1 });
    expect(Array.from(patch.coverage)).toEqual([255]);
  });

  it('combines pressure and opacity and treats a zero-size brush as a no-op', () => {
    const mask = createRasterMask(1, 1, new Uint8ClampedArray([0]));
    const patch = paintMaskStroke(mask, identity, [{ x: 0, y: 0, pressure: 0.5 }], {
      ...revealMaskBrush,
      opacity: 0.5,
    });
    const noOp = paintMaskStroke(mask, identity, [{ x: 0, y: 0 }], {
      ...revealMaskBrush,
      size: 0,
    });

    expect(Array.from(patch.coverage)).toEqual([64]);
    expect(noOp).toEqual({
      x: 0,
      y: 0,
      width: 0,
      height: 0,
      coverage: new Uint8ClampedArray(),
    });
  });

  it('rejects invalid mask geometry and brush values', () => {
    const mask = createRasterMask(1, 1);
    expect(() =>
      paintMaskStroke(mask, { ...identity, a: 0 }, [{ x: 0, y: 0 }], revealMaskBrush),
    ).toThrow('Layer transform must be invertible');
    expect(() =>
      paintMaskStroke(mask, identity, [{ x: 0, y: 0 }], {
        ...revealMaskBrush,
        opacity: 2,
      }),
    ).toThrow('Mask brush opacity must be between 0 and 1');
    expect(() =>
      paintMaskStroke(
        { ...mask, coverageStore: TiledCoverageStore.empty(2, 1) },
        identity,
        [{ x: 0, y: 0 }],
        revealMaskBrush,
      ),
    ).toThrow('Mask coverage length does not match its dimensions');
  });
});

describe('paintStroke', () => {
  it('interpolates a continuous stroke between sparse points', () => {
    const layer = createPixelLayer({ id: 'a', name: 'A', width: 5, height: 1 });
    const patch = paintStroke(
      layer,
      [
        { x: 0, y: 0 },
        { x: 4, y: 0 },
      ],
      redBrush,
    );
    expect(patch).toMatchObject({ x: 0, y: 0, width: 5, height: 1 });
    expect(Array.from(patch.pixels)).toEqual([
      255, 0, 0, 255, 255, 0, 0, 255, 255, 0, 0, 255, 255, 0, 0, 255, 255, 0, 0, 255,
    ]);
  });

  it('paints a small stroke on a large sparse layer without densifying the whole layer buffer', () => {
    const layer = createPixelLayer({ id: 'huge', name: 'Huge', width: 2048, height: 2048 });
    const beforeBytes = layer.pixelStore.allocatedByteLength;
    const patch = paintStroke(layer, [{ x: 100, y: 120 }], { ...redBrush, size: 8, hardness: 1 });
    // Patch is limited to the dab footprint, not the full 2048² layer.
    expect(patch.width).toBeGreaterThan(0);
    expect(patch.height).toBeGreaterThan(0);
    expect(patch.width * patch.height).toBeLessThan(32 * 32);
    expect(patch.pixels.length).toBe(patch.width * patch.height * 4);
    expect(patch.pixels.length).toBeLessThan(2048 * 2048 * 4);
    // Source layer remains sparse (paint returns a patch; does not mutate layer).
    expect(layer.pixelStore.allocatedByteLength).toBe(beforeBytes);
    // At least one fully opaque red sample exists in the dab.
    let foundOpaqueRed = false;
    for (let offset = 0; offset < patch.pixels.length; offset += 4) {
      if (
        patch.pixels[offset] === 255 &&
        patch.pixels[offset + 1] === 0 &&
        patch.pixels[offset + 2] === 0 &&
        patch.pixels[offset + 3] === 255
      ) {
        foundOpaqueRed = true;
        break;
      }
    }
    expect(foundOpaqueRed).toBe(true);
  });

  it('uses straight-alpha source-over compositing', () => {
    const layer = createPixelLayer({
      id: 'a',
      name: 'A',
      width: 1,
      height: 1,
      pixels: [0, 0, 255, 255],
    });
    const patch = paintStroke(layer, [{ x: 0, y: 0 }], { ...redBrush, opacity: 0.5 });
    expect(Array.from(patch.pixels)).toEqual([128, 0, 128, 255]);
  });

  it('erases alpha without changing color channels', () => {
    const layer = createPixelLayer({
      id: 'a',
      name: 'A',
      width: 1,
      height: 1,
      pixels: [10, 20, 30, 200],
    });
    const patch = paintStroke(layer, [{ x: 0, y: 0 }], {
      ...redBrush,
      mode: 'erase',
      opacity: 0.25,
    });
    expect(Array.from(patch.pixels)).toEqual([10, 20, 30, 150]);
  });

  it('samples selection coverage in document coordinates for translated layers', () => {
    const layer = createPixelLayer({
      id: 'a',
      name: 'A',
      width: 2,
      height: 1,
      transform: { ...identity, e: 2, f: 1 },
    });
    const selectionCoverage = new Uint8ClampedArray(15);
    selectionCoverage[1 * 5 + 3] = 255;
    const selection: SelectionMask = createSelectionMask({
      width: 5,
      height: 3,
      coverage: selectionCoverage,
    });
    const patch = paintStroke(
      layer,
      [
        { x: 2, y: 1 },
        { x: 3, y: 1 },
      ],
      redBrush,
      selection,
    );
    expect(patch).toMatchObject({ x: 1, y: 0, width: 1, height: 1 });
    expect(Array.from(patch.pixels)).toEqual([255, 0, 0, 255]);
  });

  it('keeps brush diameter in document space on a scaled layer', () => {
    const layer = createPixelLayer({
      id: 'a',
      name: 'A',
      width: 5,
      height: 1,
      transform: { a: 2, b: 0, c: 0, d: 2, e: 0, f: 0 },
    });

    const patch = paintStroke(layer, [{ x: 4, y: 0 }], { ...redBrush, size: 4 });

    expect(patch).toMatchObject({ x: 1, y: 0, width: 3, height: 1 });
    expect(Array.from(patch.pixels)).toEqual([255, 0, 0, 255, 255, 0, 0, 255, 255, 0, 0, 255]);
  });

  it('inverse-maps a rotated stroke and constrains it through document-space selection', () => {
    const layer = createPixelLayer({
      id: 'a',
      name: 'A',
      width: 3,
      height: 1,
      transform: { a: 0, b: 1, c: -1, d: 0, e: 3, f: 1 },
    });
    const selectionCoverage = new Uint8ClampedArray(25);
    selectionCoverage[2 * 5 + 3] = 255;
    const selection: SelectionMask = createSelectionMask({
      width: 5,
      height: 5,
      coverage: selectionCoverage,
    });

    const patch = paintStroke(
      layer,
      [
        { x: 3, y: 1 },
        { x: 3, y: 3 },
      ],
      redBrush,
      selection,
    );

    expect(patch).toMatchObject({ x: 1, y: 0, width: 1, height: 1 });
    expect(Array.from(patch.pixels)).toEqual([255, 0, 0, 255]);
  });

  it('rejects singular and non-finite world transforms', () => {
    const layer = createPixelLayer({ id: 'a', name: 'A', width: 1, height: 1 });
    expect(() =>
      paintStroke(layer, [{ x: 0, y: 0 }], redBrush, null, {
        a: 1,
        b: 0,
        c: 2,
        d: 0,
        e: 0,
        f: 0,
      }),
    ).toThrow('Layer transform must be invertible');
    expect(() =>
      paintStroke(layer, [{ x: 0, y: 0 }], redBrush, null, {
        ...identity,
        e: Number.NaN,
      }),
    ).toThrow('Layer transform e must be finite');
  });

  it('keeps multi-point brush frames interactive on a 4096² sparse layer', () => {
    // M2 brush-frame gate: ordinary pointer samples must stay tile-local and
    // well under a multi-second full-layer path on large sparse documents.
    const layer = createPixelLayer({ id: 'huge', name: 'Huge', width: 4096, height: 4096 });
    const brush: BrushOptions = { ...redBrush, size: 24, hardness: 0.85, opacity: 0.9 };
    // Simulate one frame of pointer samples along a short diagonal.
    const points = Array.from({ length: 12 }, (_, index) => ({
      x: 200 + index * 3,
      y: 180 + index * 2,
    }));
    const samples: number[] = [];
    for (let i = 0; i < 5; i += 1) {
      const started = performance.now();
      const patch = paintStroke(layer, points, brush);
      samples.push(performance.now() - started);
      expect(patch.width * patch.height).toBeLessThan(128 * 128);
      expect(patch.pixels.length).toBe(patch.width * patch.height * 4);
    }
    samples.sort((a, b) => a - b);
    const median = samples[Math.floor(samples.length / 2)] ?? Number.POSITIVE_INFINITY;
    // Interactive frame budget with CI headroom (not multi-second full-frame work).
    expect(median).toBeLessThan(50);
    expect(Math.max(...samples)).toBeLessThan(150);
  });
});

describe('adjustBrightness', () => {
  it('changes only selected pixels while accounting for layer translation', () => {
    const layer = createPixelLayer({
      id: 'a',
      name: 'A',
      width: 2,
      height: 1,
      pixels: [10, 20, 30, 255, 40, 50, 60, 255],
      transform: { ...identity, e: 1 },
    });
    const selection: SelectionMask = createSelectionMask({
      width: 3,
      height: 1,
      coverage: new Uint8ClampedArray([0, 0, 255]),
    });
    const patch = adjustBrightness(layer, 25, selection);
    expect(patch).toMatchObject({ x: 1, y: 0, width: 1, height: 1 });
    expect(Array.from(patch.pixels)).toEqual([65, 75, 85, 255]);
  });

  it('samples brightness selection coverage through a rotated world transform', () => {
    const layer = createPixelLayer({
      id: 'a',
      name: 'A',
      width: 2,
      height: 1,
      pixels: [10, 20, 30, 255, 40, 50, 60, 255],
      transform: { a: 0, b: 1, c: -1, d: 0, e: 3, f: 1 },
    });
    const selectionCoverage = new Uint8ClampedArray(25);
    selectionCoverage[2 * 5 + 3] = 255;
    const selection: SelectionMask = createSelectionMask({
      width: 5,
      height: 5,
      coverage: selectionCoverage,
    });

    const patch = adjustBrightness(layer, 25, selection);

    expect(patch).toMatchObject({ x: 1, y: 0, width: 1, height: 1 });
    expect(Array.from(patch.pixels)).toEqual([65, 75, 85, 255]);
  });

  it('adjusts only the sparse selection footprint on a large layer', () => {
    const layer = createPixelLayer({
      id: 'huge',
      name: 'Huge',
      width: 1024,
      height: 1024,
      pixels: new Uint8ClampedArray(1024 * 1024 * 4).fill(40),
    });
    // Sparse selection: one 16×16 patch far from origin.
    const store = TiledCoverageStore.empty(1024, 1024).write({
      x: 200,
      y: 300,
      width: 16,
      height: 16,
      coverage: new Uint8ClampedArray(16 * 16).fill(255),
    }).store;
    const selection: SelectionMask = {
      width: 1024,
      height: 1024,
      coverageStore: store,
    };
    const patch = adjustBrightness(layer, 10, selection);
    // Working patch stays near the selection footprint, not the full 1024² layer.
    expect(patch.width * patch.height).toBeLessThan(64 * 64);
    expect(patch.pixels.length).toBeLessThan(1024 * 1024 * 4);
    expect(patch.x).toBeGreaterThanOrEqual(190);
    expect(patch.y).toBeGreaterThanOrEqual(290);
    // Brightened sample inside the selection.
    expect(patch.pixels[0]).toBe(50);
  });

  it('matches the compact patch path for full-layer and selected brightness', () => {
    const source = createPixelLayer({
      id: 'equivalence',
      name: 'Equivalence',
      width: 5,
      height: 3,
      pixels: Uint8ClampedArray.from({ length: 5 * 3 * 4 }, (_, index) =>
        index % 4 === 3 ? (index * 13) % 256 : (index * 29) % 256,
      ),
    });
    const selection = createRectangularSelection(
      { width: 5, height: 3 },
      {
        x: 1,
        y: 1,
        width: 3,
        height: 2,
      },
    );
    for (const selected of [null, selection]) {
      const patch = adjustBrightness(source, 37, selected);
      const expected = source.pixelStore.write({
        ...patch,
        pixels: new Uint8ClampedArray(patch.pixels),
      }).store;
      expect(adjustBrightnessPixelStore(source, 37, selected).toContiguous()).toEqual(
        expected.toContiguous(),
      );
    }
  });

  it('brightens a dense 4096² layer with tile-sized reads and no materialization', () => {
    const size = 4096;
    const tileSize = 256;
    let store = TiledPixelStore.empty(size, size, tileSize);
    const tilePixels = new Uint8ClampedArray(tileSize * tileSize * 4);
    for (let offset = 0; offset < tilePixels.length; offset += 4) {
      tilePixels.set([40, 80, 120, 200], offset);
    }
    for (let y = 0; y < size; y += tileSize) {
      for (let x = 0; x < size; x += tileSize) {
        store = store.write({
          x,
          y,
          width: tileSize,
          height: tileSize,
          pixels: tilePixels,
        }).store;
      }
    }
    const source = createPixelLayer({
      id: 'dense-brightness',
      name: 'Dense brightness',
      width: size,
      height: size,
      pixelStore: store,
    });
    const read = vi.spyOn(store, 'read');
    const materialize = vi.spyOn(store, 'toContiguous');

    const result = adjustBrightnessPixelStore(source, 15, null);

    expect(materialize).not.toHaveBeenCalled();
    expect(read).toHaveBeenCalledTimes((size / tileSize) ** 2);
    for (const [region] of read.mock.calls) {
      expect(region.width).toBeLessThanOrEqual(tileSize);
      expect(region.height).toBeLessThanOrEqual(tileSize);
    }
    expect(result.read({ x: size - 1, y: size - 1, width: 1, height: 1 })).toEqual(
      new Uint8ClampedArray([55, 95, 135, 200]),
    );
    expect(store.read({ x: 0, y: 0, width: 1, height: 1 })).toEqual(
      new Uint8ClampedArray([40, 80, 120, 200]),
    );
  }, 30_000);
});

describe('image resizing', () => {
  it('resamples sparse coverage stores without densifying empty selections', () => {
    const empty = TiledCoverageStore.empty(64, 64);
    const resizedEmpty = resampleCoverageStore(empty, 32, 32, 'nearest');
    expect(resizedEmpty.tileCount).toBe(0);
    expect(resizedEmpty.width).toBe(32);
    expect(resizedEmpty.height).toBe(32);

    const sparse = TiledCoverageStore.empty(64, 64).write({
      x: 8,
      y: 8,
      width: 4,
      height: 4,
      coverage: new Uint8ClampedArray(16).fill(255),
    }).store;
    const denseReference = resampleCoverage(sparse.toContiguous(), 64, 64, 32, 32, 'nearest');
    const sparseResult = resampleCoverageStore(sparse, 32, 32, 'nearest');
    expect(sparseResult.toContiguous()).toEqual(denseReference);
    // Sparse result may allocate one tile (≤ dest canvas) but not more than dense full buffer.
    expect(sparseResult.allocatedByteLength).toBeLessThanOrEqual(32 * 32);
    expect(sparseResult.tileCount).toBeGreaterThan(0);
    expect(sparseResult.tileCount).toBeLessThanOrEqual(4);
  });

  it('resamples sparse complement coverage without allocating its full destination', () => {
    const complement = TiledCoverageStore.uniform(4096, 4096, 255, 256).write({
      x: 1024,
      y: 2048,
      width: 16,
      height: 8,
      coverage: new Uint8ClampedArray(16 * 8),
    }).store;
    const materialize = vi.spyOn(complement, 'toContiguous');
    const resized = resampleCoverageStore(complement, 2048, 2048, 'nearest');
    expect(materialize).not.toHaveBeenCalled();
    expect(resized).toMatchObject({
      defaultCoverage: 255,
      tileCount: 1,
      allocatedByteLength: 256 * 256,
    });
    expect(resized.read({ x: 512, y: 1024, width: 1, height: 1 })[0]).toBe(0);
    expect(resized.read({ x: 0, y: 0, width: 1, height: 1 })[0]).toBe(255);
  });

  it('resamples sparse pixel stores without densifying empty layers', () => {
    const empty = TiledPixelStore.empty(128, 128);
    const resizedEmpty = resamplePixelStore(empty, 64, 64, 'nearest');
    expect(resizedEmpty.tileCount).toBe(0);

    const pixels = new Uint8ClampedArray(8 * 8 * 4);
    for (let offset = 0; offset < pixels.length; offset += 4) {
      pixels[offset] = 200;
      pixels[offset + 1] = 10;
      pixels[offset + 2] = 20;
      pixels[offset + 3] = 255;
    }
    const sparse = TiledPixelStore.empty(128, 128).write({
      x: 16,
      y: 24,
      width: 8,
      height: 8,
      pixels,
    }).store;
    const denseReference = resamplePixels(sparse.toContiguous(), 128, 128, 64, 64, 'nearest');
    const sparseResult = resamplePixelStore(sparse, 64, 64, 'nearest');
    expect(sparseResult.toContiguous()).toEqual(denseReference);
    // Sparse path may use one dest tile (≤ full canvas bytes) but stays bounded.
    expect(sparseResult.allocatedByteLength).toBeLessThanOrEqual(64 * 64 * 4);
    expect(sparseResult.tileCount).toBeGreaterThan(0);
    expect(sparseResult.tileCount).toBeLessThanOrEqual(4);
  });

  it('resizes distant 8192² pixel and coverage tiles without a full-span read', () => {
    const size = 8192;
    const tileSize = 64;
    const far = size - tileSize;
    const coverageTile = new Uint8ClampedArray(tileSize * tileSize).fill(255);
    const pixelTile = new Uint8ClampedArray(tileSize * tileSize * 4).fill(120);
    let coverageStore = TiledCoverageStore.empty(size, size, tileSize);
    let pixelStore = TiledPixelStore.empty(size, size, tileSize);
    for (const position of [0, far]) {
      coverageStore = coverageStore.write({
        x: position,
        y: position,
        width: tileSize,
        height: tileSize,
        coverage: coverageTile,
      }).store;
      pixelStore = pixelStore.write({
        x: position,
        y: position,
        width: tileSize,
        height: tileSize,
        pixels: pixelTile,
      }).store;
    }
    const pixelRead = vi.spyOn(TiledPixelStore.prototype, 'read');
    const coverageRead = vi.spyOn(TiledCoverageStore.prototype, 'read');
    const pixelDense = vi.spyOn(TiledPixelStore.prototype, 'toContiguous');
    const coverageDense = vi.spyOn(TiledCoverageStore.prototype, 'toContiguous');
    const resized = resizeImage({ width: size / 2, height: size / 2, method: 'nearest' }).execute(
      createDocument({
        id: 'sparse-resize-document',
        name: 'Sparse resize document',
        width: size,
        height: size,
        selection: createSelectionMask({ width: size, height: size, coverageStore }),
        channels: [
          createAlphaChannel({
            id: 'alpha',
            name: 'Alpha',
            width: size,
            height: size,
            coverageStore,
          }),
        ],
        layers: [
          createPixelLayer({
            id: 'pixels',
            name: 'Pixels',
            width: size,
            height: size,
            pixelStore,
            rasterMask: createRasterMaskFromCoverageStore(size, size, coverageStore),
          }),
        ],
      }),
    );
    expect(pixelRead).not.toHaveBeenCalled();
    expect(coverageRead).not.toHaveBeenCalled();
    expect(pixelDense).not.toHaveBeenCalled();
    expect(coverageDense).not.toHaveBeenCalled();
    const layer = resized.layers[0];
    if (layer?.kind !== 'pixel') throw new Error('Expected resized pixel layer');
    expect(layer.pixelStore.tileCount).toBe(2);
    expect(layer.pixelStore.allocatedByteLength).toBe(2 * tileSize * tileSize * 4);
    expect(layer.rasterMask?.coverageStore.tileCount).toBe(2);
    expect(resized.selection?.coverageStore.tileCount).toBe(2);
    expect(resized.channels[0]?.coverageStore.tileCount).toBe(2);
    expect(layer.pixelStore.getTile(0, 0)?.pixels[3]).toBe(120);
    expect(layer.pixelStore.getTile(63, 63)?.pixels[(32 * tileSize + 32) * 4 + 3]).toBe(120);
  });

  it('keeps sparse tiled bilinear output pixel-equivalent to the dense reference', () => {
    const coverage = TiledCoverageStore.empty(32, 32, 8)
      .write({
        x: 0,
        y: 0,
        width: 2,
        height: 2,
        coverage: new Uint8ClampedArray([64, 128, 192, 255]),
      })
      .store.write({
        x: 30,
        y: 30,
        width: 2,
        height: 2,
        coverage: new Uint8ClampedArray([255, 192, 128, 64]),
      }).store;
    const pixels = new Uint8ClampedArray(2 * 2 * 4).fill(160);
    const pixelStore = TiledPixelStore.empty(32, 32, 8)
      .write({ x: 0, y: 0, width: 2, height: 2, pixels })
      .store.write({ x: 30, y: 30, width: 2, height: 2, pixels }).store;
    expect(resampleCoverageStore(coverage, 19, 17, 'bilinear').toContiguous()).toEqual(
      resampleCoverage(coverage.toContiguous(), 32, 32, 19, 17, 'bilinear'),
    );
    expect(resamplePixelStore(pixelStore, 19, 17, 'bilinear').toContiguous()).toEqual(
      resamplePixels(pixelStore.toContiguous(), 32, 32, 19, 17, 'bilinear'),
    );
  });

  it('resamples coverage with nearest and bilinear pixel-center sampling', () => {
    expect(Array.from(resampleCoverage([0, 255], 2, 1, 4, 1, 'nearest'))).toEqual([0, 0, 255, 255]);
    expect(Array.from(resampleCoverage([0, 255], 2, 1, 3, 1, 'bilinear'))).toEqual([0, 128, 255]);
  });

  it('interpolates RGBA in premultiplied alpha to avoid transparent color fringes', () => {
    const resized = resamplePixels([255, 0, 0, 255, 0, 0, 255, 0], 2, 1, 3, 1, 'bilinear');
    expect(Array.from(resized)).toEqual([255, 0, 0, 255, 255, 0, 0, 128, 0, 0, 0, 0]);
  });

  it('replicates exact nearest-neighbor quadrants when enlarging', () => {
    const source = [255, 0, 0, 255, 0, 255, 0, 255, 0, 0, 255, 255, 255, 255, 0, 255];
    const resized = resamplePixels(source, 2, 2, 4, 4, 'nearest');
    const pixel = (x: number, y: number) =>
      Array.from(resized.slice((y * 4 + x) * 4, (y * 4 + x + 1) * 4));
    expect(pixel(0, 0)).toEqual([255, 0, 0, 255]);
    expect(pixel(3, 0)).toEqual([0, 255, 0, 255]);
    expect(pixel(0, 3)).toEqual([0, 0, 255, 255]);
    expect(pixel(3, 3)).toEqual([255, 255, 0, 255]);
  });

  it('resizes nested layers, masks, selections, and channels without changing prior state', () => {
    const pixels = new Uint8ClampedArray([
      255, 0, 0, 255, 0, 255, 0, 255, 0, 0, 255, 255, 255, 255, 0, 255,
    ]);
    const document = createDocument({
      id: 'doc',
      name: 'Resize',
      width: 4,
      height: 4,
      selection: createSelectionMask({
        width: 4,
        height: 4,
        coverage: new Uint8ClampedArray([0, 0, 0, 0, 0, 255, 255, 0, 0, 255, 255, 0, 0, 0, 0, 0]),
      }),
      channels: [
        createAlphaChannel({
          id: 'alpha',
          name: 'Alpha',
          width: 4,
          height: 4,
          coverage: new Uint8ClampedArray(16).fill(255),
        }),
      ],
      layers: [
        createGroupLayer({
          id: 'group',
          name: 'Group',
          transform: { ...identity, e: 1, f: 1 },
          rasterMask: createRasterMask(4, 4, new Uint8ClampedArray(16).fill(192)),
          vectorMask: createVectorMask(4, 4, [
            { type: 'move', x: 0, y: 0 },
            { type: 'line', x: 2, y: 0 },
            { type: 'line', x: 2, y: 4 },
            { type: 'close' },
          ]),
          children: [
            createPixelLayer({
              id: 'pixel',
              name: 'Pixel',
              width: 2,
              height: 2,
              pixels,
              transform: { a: 0, b: 1, c: -1, d: 0, e: 2, f: 0 },
              rasterMask: createRasterMask(2, 2, [0, 255, 255, 0]),
              vectorMask: createVectorMask(2, 2, [
                { type: 'move', x: 0.5, y: 0.5 },
                { type: 'line', x: 1.5, y: 0.5 },
                { type: 'line', x: 1, y: 1.5 },
                { type: 'close' },
              ]),
            }),
          ],
        }),
        createAdjustmentLayer({
          id: 'adjustment',
          name: 'Adjustment',
          vectorMask: createVectorMask(4, 4, [
            { type: 'move', x: 1, y: 1 },
            { type: 'line', x: 3, y: 1 },
            { type: 'line', x: 2, y: 3 },
            { type: 'close' },
          ]),
        }),
      ],
    });
    const originalWorld = findLayerWorldTransform(document, 'pixel');
    const resized = resizeImage({ width: 8, height: 6, method: 'nearest' }).execute(document);
    const resizedLayer = findLayer(resized, 'pixel');
    const resizedWorld = findLayerWorldTransform(resized, 'pixel');

    expect(resized.canvas).toEqual({ width: 8, height: 6 });
    expect(resizedLayer).toMatchObject({ width: 4, height: 3 });
    expect(findLayer(resized, 'group')?.transform).toEqual({
      a: 2,
      b: 0,
      c: 0,
      d: 1.5,
      e: 2,
      f: 1.5,
    });
    expect(findLayer(resized, 'group')).toMatchObject({
      kind: 'group',
      vectorMask: {
        width: 8,
        height: 6,
        commands: [
          { type: 'move', x: 0, y: 0 },
          { type: 'line', x: 4, y: 0 },
          { type: 'line', x: 4, y: 6 },
          { type: 'close' },
        ],
      },
      rasterMask: { width: 8, height: 6 },
    });
    const resizedGroup = findLayer(resized, 'group');
    expect(resizedGroup?.rasterMask?.coverageStore.toContiguous()).toEqual(
      new Uint8ClampedArray(48).fill(192),
    );
    expect(resizedLayer?.transform).toEqual({
      a: 0,
      b: 0.5,
      c: -2 / 3,
      d: 0,
      e: 2,
      f: 0,
    });
    expect(resizedLayer?.kind === 'pixel' ? resizedLayer.rasterMask : null).toMatchObject({
      width: 4,
      height: 3,
    });
    expect(resizedLayer?.kind === 'pixel' ? resizedLayer.vectorMask : null).toMatchObject({
      width: 4,
      height: 3,
      commands: [
        { type: 'move', x: 1, y: 0.75 },
        { type: 'line', x: 3, y: 0.75 },
        { type: 'line', x: 2, y: 2.25 },
        { type: 'close' },
      ],
    });
    expect(findLayer(resized, 'adjustment')).toMatchObject({
      kind: 'adjustment',
      vectorMask: {
        width: 8,
        height: 6,
        commands: [
          { type: 'move', x: 2, y: 1.5 },
          { type: 'line', x: 6, y: 1.5 },
          { type: 'line', x: 4, y: 4.5 },
          { type: 'close' },
        ],
      },
    });
    expect(resized.selection).toMatchObject({ width: 8, height: 6 });
    expect(resized.channels[0]).toMatchObject({ width: 8, height: 6 });
    expect(resized.channels[0]?.coverageStore.toContiguous().length).toBe(8 * 6);
    expect(resizeImage({ width: 8, height: 6, method: 'nearest' }).label).toBe('Resize image');
    if (!originalWorld || !resizedWorld) throw new Error('Expected layer transforms');
    const originalPoint = transformPoint(originalWorld, 1, 1);
    const resizedPoint = transformPoint(resizedWorld, 2, 1.5);
    expect(resizedPoint.x).toBeCloseTo(originalPoint.x * 2);
    expect(resizedPoint.y).toBeCloseTo(originalPoint.y * 1.5);
    const originalPixelLayer = findLayer(document, 'pixel');
    expect(originalPixelLayer).toMatchObject({ width: 2, height: 2 });
    expect(
      originalPixelLayer?.kind === 'pixel'
        ? originalPixelLayer.pixelStore.toContiguous()
        : undefined,
    ).toEqual(pixels);
    expect(document.canvas).toEqual({ width: 4, height: 4 });
    expect(resizeImage({ width: 4, height: 4, method: 'nearest' }).execute(document)).toBe(
      document,
    );
  });

  it('validates methods, buffers, dimensions, and allocation limits', () => {
    expect(() => resamplePixels([0], 1, 1, 1, 1, 'nearest')).toThrow(
      'Pixel buffer length does not match',
    );
    expect(() => resampleCoverage([], 1, 1, 1, 1, 'nearest')).toThrow(
      'Coverage buffer length does not match',
    );
    expect(() => resizeImage({ width: 0, height: 1, method: 'nearest' })).toThrow(
      'Image width must be a positive integer',
    );
    expect(() => resizeImage({ width: 32_769, height: 1, method: 'nearest' })).toThrow(
      'Image dimensions cannot exceed 32768 pixels',
    );
    expect(() => resizeImage({ width: 20_000, height: 10_000, method: 'bilinear' })).toThrow(
      'Image area cannot exceed 100000000 pixels',
    );
    expect(() => resizeImage({ width: 1, height: 1, method: 'cubic' as 'nearest' })).toThrow(
      'Resampling method must be nearest or bilinear',
    );
    const layered = createDocument({
      id: 'large',
      name: 'Large',
      width: 1,
      height: 1,
      layers: ['One', 'Two', 'Three'].map((name) =>
        createPixelLayer({ id: name, name, width: 1, height: 1 }),
      ),
    });
    expect(() =>
      resizeImage({ width: 10_000, height: 10_000, method: 'nearest' }).execute(layered),
    ).toThrow('Resize working set cannot exceed 1000000000 bytes');
  });
});
