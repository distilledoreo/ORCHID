import { describe, expect, it, vi } from 'vitest';
import {
  createSelectionMask,
  createPixelLayer,
  createRectangularSelection,
  identityLayerTransform,
  type PixelLayer,
  type PixelPatch,
  type SelectionMask,
} from '@freetoshop/core-document';
import { TiledCoverageStore, TiledPixelStore } from '@freetoshop/pixel-storage';
import {
  contrast,
  gaussianBlur,
  gaussianBlurPatches,
  grayscale,
  hueSaturation,
  invert,
  levels,
  posterize,
  pointwiseRgbFilterPatches,
  threshold,
} from './index.js';

function layer(width: number, height: number, pixels: readonly number[]): PixelLayer {
  return createPixelLayer({ id: 'pixels', name: 'Pixels', width, height, pixels });
}

function applyPatch(source: PixelLayer, patch: PixelPatch): Uint8ClampedArray {
  const output = source.pixelStore.toContiguous();
  for (let row = 0; row < patch.height; row += 1) {
    const sourceOffset = row * patch.width * 4;
    const destinationOffset = ((patch.y + row) * source.width + patch.x) * 4;
    output.set(
      new Uint8ClampedArray(patch.pixels).subarray(sourceOffset, sourceOffset + patch.width * 4),
      destinationOffset,
    );
  }
  return output;
}

function applyPatches(source: PixelLayer, patches: readonly PixelPatch[]): Uint8ClampedArray {
  let store = source.pixelStore;
  for (const patch of patches) {
    store = store.write({ ...patch, pixels: new Uint8ClampedArray(patch.pixels) }).store;
  }
  return store.toContiguous();
}

function fullSelection(width: number, height: number, values: readonly number[]): SelectionMask {
  return createSelectionMask({ width, height, coverage: new Uint8ClampedArray(values) });
}

describe('contrast and levels', () => {
  it('applies contrast without changing alpha and returns only changed bounds', () => {
    const source = layer(3, 1, [0, 64, 128, 7, 128, 128, 128, 99, 255, 192, 128, 201]);
    const patch = contrast(source, 100);

    expect(patch).toMatchObject({ x: 0, y: 0, width: 3, height: 1 });
    expect([...applyPatch(source, patch)]).toEqual([
      0, 0, 128, 7, 128, 128, 128, 99, 255, 255, 128, 201,
    ]);
    expect([...source.pixelStore.toContiguous()]).toEqual([
      0, 64, 128, 7, 128, 128, 128, 99, 255, 192, 128, 201,
    ]);
    expect(contrast(source, 0)).toEqual({
      x: 0,
      y: 0,
      width: 0,
      height: 0,
      pixels: new Uint8ClampedArray(),
    });
  });

  it('maps levels through input, gamma, and output ranges', () => {
    const source = layer(3, 1, [0, 64, 255, 1, 64, 128, 192, 2, 255, 192, 0, 3]);
    const patch = levels(source, {
      inputBlack: 64,
      gamma: 2,
      inputWhite: 192,
      outputBlack: 10,
      outputWhite: 210,
    });

    expect([...applyPatch(source, patch)]).toEqual([
      10, 10, 210, 1, 10, 151, 210, 2, 210, 210, 10, 3,
    ]);
  });

  it('rejects invalid contrast and levels inputs instead of silently clamping them', () => {
    const source = layer(1, 1, [10, 20, 30, 40]);
    expect(() => contrast(source, Number.NaN)).toThrow('Contrast amount must be finite');
    expect(() => contrast(source, 101)).toThrow('between -100 and 100');
    expect(() =>
      levels(source, {
        inputBlack: 128,
        gamma: 1,
        inputWhite: 128,
        outputBlack: 0,
        outputWhite: 255,
      }),
    ).toThrow('Input black must be less than input white');
    expect(() =>
      levels(source, {
        inputBlack: 0,
        gamma: 0,
        inputWhite: 255,
        outputBlack: 0,
        outputWhite: 255,
      }),
    ).toThrow('Gamma must be greater than 0');
    expect(() =>
      levels(source, {
        inputBlack: 0,
        gamma: 1,
        inputWhite: 255,
        outputBlack: 200,
        outputWhite: 100,
      }),
    ).toThrow('Output black must be less than output white');
  });
});

describe('color filters', () => {
  it('rotates hue and applies relative saturation and lightness', () => {
    const red = layer(1, 1, [255, 0, 0, 77]);
    expect([
      ...applyPatch(red, hueSaturation(red, { hue: 120, saturation: 0, lightness: 0 })),
    ]).toEqual([0, 255, 0, 77]);
    expect([
      ...applyPatch(red, hueSaturation(red, { hue: 0, saturation: -100, lightness: 0 })),
    ]).toEqual([128, 128, 128, 77]);
    expect([
      ...applyPatch(red, hueSaturation(red, { hue: 0, saturation: 0, lightness: 100 })),
    ]).toEqual([255, 255, 255, 77]);
  });

  it('keeps tiled pointwise filters byte-identical to legacy patches', () => {
    const source = layer(
      5,
      3,
      Array.from({ length: 5 * 3 * 4 }, (_, index) =>
        index % 4 === 3 ? (index * 13) % 256 : (index * 29) % 256,
      ),
    );
    const cases = [
      [{ kind: 'contrast', amount: 31 } as const, contrast(source, 31)],
      [
        {
          kind: 'levels',
          options: {
            inputBlack: 12,
            gamma: 0.8,
            inputWhite: 230,
            outputBlack: 4,
            outputWhite: 245,
          },
        } as const,
        levels(source, {
          inputBlack: 12,
          gamma: 0.8,
          inputWhite: 230,
          outputBlack: 4,
          outputWhite: 245,
        }),
      ],
      [
        { kind: 'hue-saturation', options: { hue: 37, saturation: -20, lightness: 12 } } as const,
        hueSaturation(source, { hue: 37, saturation: -20, lightness: 12 }),
      ],
      [{ kind: 'invert' } as const, invert(source)],
      [{ kind: 'grayscale' } as const, grayscale(source)],
      [{ kind: 'threshold', level: 117 } as const, threshold(source, 117)],
      [{ kind: 'posterize', levels: 7 } as const, posterize(source, 7)],
    ] as const;
    for (const [filter, legacyPatch] of cases) {
      expect(applyPatches(source, pointwiseRgbFilterPatches(source, filter))).toEqual(
        applyPatch(source, legacyPatch),
      );
    }
  });

  it('filters a dense 4096² layer through bounded tile reads', () => {
    const size = 4096;
    const tileSize = 256;
    let store = TiledPixelStore.empty(size, size, tileSize);
    const pixels = new Uint8ClampedArray(tileSize * tileSize * 4);
    for (let offset = 0; offset < pixels.length; offset += 4) {
      pixels.set([20, 100, 220, 177], offset);
    }
    for (let y = 0; y < size; y += tileSize) {
      for (let x = 0; x < size; x += tileSize) {
        store = store.write({ x, y, width: tileSize, height: tileSize, pixels }).store;
      }
    }
    const source = createPixelLayer({
      id: 'dense-pointwise',
      name: 'Dense pointwise',
      width: size,
      height: size,
      pixelStore: store,
    });
    const read = vi.spyOn(store, 'read');
    const materialize = vi.spyOn(store, 'toContiguous');

    const patches = pointwiseRgbFilterPatches(source, { kind: 'invert' });

    expect(materialize).not.toHaveBeenCalled();
    expect(read).toHaveBeenCalledTimes((size / tileSize) ** 2);
    expect(patches).toHaveLength((size / tileSize) ** 2);
    expect(patches.every((patch) => patch.width <= tileSize && patch.height <= tileSize)).toBe(
      true,
    );
    expect(new Uint8ClampedArray(patches.at(-1)?.pixels ?? []).slice(-4)).toEqual(
      new Uint8ClampedArray([235, 155, 35, 177]),
    );
  }, 30_000);

  it('keeps complement-selection reads tile-bounded on an 8192² sparse layer', () => {
    const size = 8192;
    const tileSize = 256;
    const tile = new Uint8ClampedArray(tileSize * tileSize * 4);
    for (let offset = 0; offset < tile.length; offset += 4) {
      tile.set([20, 100, 220, 255], offset);
    }
    const pixelStore = TiledPixelStore.empty(size, size, tileSize).write({
      x: 4096,
      y: 2048,
      width: tileSize,
      height: tileSize,
      pixels: tile,
    }).store;
    const coverageStore = TiledCoverageStore.uniform(size, size, 255, tileSize);
    const selection = createSelectionMask({ width: size, height: size, coverageStore });
    const source = createPixelLayer({
      id: 'sparse-complement-filter',
      name: 'Sparse complement filter',
      width: size,
      height: size,
      pixelStore,
    });
    const coverageRead = vi.spyOn(coverageStore, 'read');
    const materialize = vi.spyOn(coverageStore, 'toContiguous');

    const pointwise = pointwiseRgbFilterPatches(source, { kind: 'invert' }, selection);
    const blurred = gaussianBlurPatches(source, 1, selection);

    expect(pointwise).toHaveLength(1);
    expect(blurred.length).toBeGreaterThan(0);
    expect(materialize).not.toHaveBeenCalled();
    expect(coverageRead).toHaveBeenCalled();
    expect(
      coverageRead.mock.calls.every(
        ([region]) => region.width <= tileSize + 2 && region.height <= tileSize + 2,
      ),
    ).toBe(true);
    expect(pointwise.every((patch) => patch.width <= tileSize && patch.height <= tileSize)).toBe(
      true,
    );
  });

  it('inverts, grayscales, thresholds, and posterizes deterministically', () => {
    const source = layer(2, 1, [20, 100, 220, 13, 64, 128, 250, 241]);
    expect([...applyPatch(source, invert(source))]).toEqual([235, 155, 35, 13, 191, 127, 5, 241]);
    expect([...applyPatch(source, grayscale(source))]).toEqual([
      92, 92, 92, 13, 123, 123, 123, 241,
    ]);
    expect([...applyPatch(source, threshold(source, 100))]).toEqual([
      0, 0, 0, 13, 255, 255, 255, 241,
    ]);
    expect([...applyPatch(source, posterize(source, 4))]).toEqual([
      0, 85, 255, 13, 85, 170, 255, 241,
    ]);
    expect(posterize(source, 256).width).toBe(0);
  });

  it('rejects invalid hue, threshold, and posterize values', () => {
    const source = layer(1, 1, [1, 2, 3, 4]);
    expect(() => hueSaturation(source, { hue: 181, saturation: 0, lightness: 0 })).toThrow(
      'Hue must be between -180 and 180',
    );
    expect(() => hueSaturation(source, { hue: 0, saturation: -101, lightness: 0 })).toThrow(
      'Saturation must be between -100 and 100',
    );
    expect(() => threshold(source, 12.5)).toThrow('Threshold level must be an integer');
    expect(() => posterize(source, 1)).toThrow('Posterize levels must be an integer');
  });
});

describe('selection and transform behavior', () => {
  it('uses document-space selection coverage through a supplied world transform', () => {
    const source = layer(3, 1, [10, 20, 30, 40, 10, 20, 30, 50, 10, 20, 30, 60]);
    const selection = fullSelection(5, 1, [0, 0, 128, 0, 0]);
    const patch = invert(source, selection, {
      ...identityLayerTransform(),
      e: 1,
    });

    expect(patch).toMatchObject({ x: 1, y: 0, width: 1, height: 1 });
    expect([...new Uint8ClampedArray(patch.pixels)]).toEqual([128, 128, 128, 50]);
    expect([...applyPatch(source, patch)]).toEqual([
      10, 20, 30, 40, 128, 128, 128, 50, 10, 20, 30, 60,
    ]);
  });

  it('treats null selection as full coverage and preserves alpha for every color operation', () => {
    const source = layer(1, 1, [40, 120, 200, 31]);
    const patches = [
      contrast(source, 25, null),
      levels(source, {
        inputBlack: 10,
        gamma: 0.8,
        inputWhite: 230,
        outputBlack: 5,
        outputWhite: 240,
      }),
      hueSaturation(source, { hue: -30, saturation: 20, lightness: -10 }),
      invert(source),
      grayscale(source),
      threshold(source, 100),
      posterize(source, 5),
    ];
    for (const patch of patches) expect(new Uint8ClampedArray(patch.pixels)[3]).toBe(31);
  });

  it('returns an empty patch for an empty selection', () => {
    const source = layer(1, 1, [40, 120, 200, 31]);
    const selection = fullSelection(1, 1, [0]);
    expect(invert(source, selection).width).toBe(0);
  });
});

describe('Gaussian blur', () => {
  it('matches the reference kernel across tile seams and canvas edges', () => {
    const width = 270;
    const height = 19;
    const pixels = Uint8ClampedArray.from({ length: width * height * 4 }, (_, index) =>
      index % 4 === 3 ? (index * 17) % 256 : (index * 31) % 256,
    );
    const source = createPixelLayer({
      id: 'gaussian-seams',
      name: 'Gaussian seams',
      width,
      height,
      pixels,
    });
    const legacy = gaussianBlur(source, 4);
    const tiled = gaussianBlurPatches(source, 4);

    expect(applyPatches(source, tiled)).toEqual(applyPatch(source, legacy));
    expect(tiled.some((patch) => patch.x < 256)).toBe(true);
    expect(tiled.some((patch) => patch.x >= 256)).toBe(true);
  });

  it('matches transformed selection blending across Gaussian tiles', () => {
    const width = 300;
    const height = 24;
    const source = createPixelLayer({
      id: 'gaussian-selection',
      name: 'Gaussian selection',
      width,
      height,
      pixels: Uint8ClampedArray.from({ length: width * height * 4 }, (_, index) =>
        index % 4 === 3 ? 255 : (index * 23) % 256,
      ),
    });
    const transform = { a: 1, b: 0, c: 0, d: 1, e: 7, f: 3 } as const;
    const selection = createRectangularSelection(
      { width: 320, height: 40 },
      { x: 245, y: 5, width: 28, height: 14 },
    );
    const legacy = gaussianBlur(source, 3, selection, transform);
    const tiled = gaussianBlurPatches(source, 3, selection, transform);

    expect(applyPatches(source, tiled)).toEqual(applyPatch(source, legacy));
  });

  it('blurs a dense 4096² layer through radius-bounded source windows', () => {
    const size = 4096;
    const tileSize = 256;
    let store = TiledPixelStore.empty(size, size, tileSize);
    const pixels = new Uint8ClampedArray(tileSize * tileSize * 4);
    for (let offset = 0; offset < pixels.length; offset += 4) {
      const pixel = offset / 4;
      pixels.set([pixel % 251, (pixel * 3) % 251, (pixel * 7) % 251, 255], offset);
    }
    for (let y = 0; y < size; y += tileSize) {
      for (let x = 0; x < size; x += tileSize) {
        store = store.write({ x, y, width: tileSize, height: tileSize, pixels }).store;
      }
    }
    const source = createPixelLayer({
      id: 'dense-gaussian',
      name: 'Dense Gaussian',
      width: size,
      height: size,
      pixelStore: store,
    });
    const read = vi.spyOn(store, 'read');
    const materialize = vi.spyOn(store, 'toContiguous');

    const patches = gaussianBlurPatches(source, 1);

    expect(materialize).not.toHaveBeenCalled();
    expect(read).toHaveBeenCalledTimes((size / tileSize) ** 2);
    expect(read.mock.calls.every(([region]) => region.width <= 258 && region.height <= 258)).toBe(
      true,
    );
    expect(patches).toHaveLength((size / tileSize) ** 2);
    expect(patches.every((patch) => patch.width <= tileSize && patch.height <= tileSize)).toBe(
      true,
    );
  }, 30_000);

  it('convolves in premultiplied alpha without leaking hidden transparent RGB', () => {
    const source = layer(3, 1, [0, 0, 255, 0, 255, 0, 0, 255, 0, 0, 255, 0]);
    const patch = gaussianBlur(source, 1);
    const output = applyPatch(source, patch);

    expect(patch).toMatchObject({ x: 0, y: 0, width: 3, height: 1 });
    expect(output[0]).toBe(255);
    expect(output[1]).toBe(0);
    expect(output[2]).toBe(0);
    expect(output[3]).toBeGreaterThan(0);
    expect(output[4]).toBe(255);
    expect(output[5]).toBe(0);
    expect(output[6]).toBe(0);
    expect(output[7]).toBeLessThan(255);
    expect([...output.slice(8, 12)]).toEqual([...output.slice(0, 4)]);
    expect([...source.pixelStore.toContiguous()]).toEqual([
      0, 0, 255, 0, 255, 0, 0, 255, 0, 0, 255, 0,
    ]);
  });

  it('blends the blur through selection coverage in premultiplied space', () => {
    const source = layer(3, 1, [0, 0, 0, 0, 255, 255, 255, 255, 0, 0, 0, 0]);
    const selection = fullSelection(3, 1, [0, 255, 0]);
    const patch = gaussianBlur(source, 1, selection);

    expect(patch).toMatchObject({ x: 1, y: 0, width: 1, height: 1 });
    expect(new Uint8ClampedArray(patch.pixels)[3]).toBeLessThan(255);
    const output = applyPatch(source, patch);
    expect([...output.slice(0, 4)]).toEqual([0, 0, 0, 0]);
    expect([...output.slice(8, 12)]).toEqual([0, 0, 0, 0]);
  });

  it('returns no change for radius zero and a uniform opaque layer', () => {
    const source = layer(
      2,
      2,
      [50, 60, 70, 255, 50, 60, 70, 255, 50, 60, 70, 255, 50, 60, 70, 255],
    );
    expect(gaussianBlur(source, 0).width).toBe(0);
    expect(gaussianBlur(source, 3).width).toBe(0);
  });

  it('rejects invalid radii', () => {
    const source = layer(1, 1, [0, 0, 0, 0]);
    expect(() => gaussianBlur(source, -1)).toThrow('Gaussian blur radius must be an integer');
    expect(() => gaussianBlur(source, 1.5)).toThrow('Gaussian blur radius must be an integer');
    expect(() => gaussianBlur(source, 101)).toThrow('Gaussian blur radius must be an integer');
  });

  it('blurs an unselected sparse layer without densifying empty canvas regions', () => {
    const width = 1024;
    const height = 1024;
    const empty = createPixelLayer({ id: 'empty', name: 'Empty', width, height });
    // Fully transparent sparse layer: no-op without full-buffer work.
    expect(gaussianBlur(empty, 3).width).toBe(0);
    expect(invert(empty).width).toBe(0);

    const paintedStore = empty.pixelStore.write({
      x: 100,
      y: 120,
      width: 8,
      height: 8,
      pixels: new Uint8ClampedArray(8 * 8 * 4).fill(180),
    }).store;
    const sparse = createPixelLayer({
      id: 'sparse',
      name: 'Sparse',
      width,
      height,
      pixelStore: paintedStore,
    });
    const blurred = gaussianBlur(sparse, 2);
    expect(blurred.width).toBeGreaterThan(0);
    // Sparse store may expose whole tiles (e.g. 256²) but must stay << full canvas.
    expect(blurred.width * blurred.height).toBeLessThan((width * height) / 10);
    expect(blurred.x + blurred.width).toBeLessThanOrEqual(100 + 256 + 4);
    expect(blurred.y + blurred.height).toBeLessThanOrEqual(120 + 256 + 4);
    const inverted = invert(sparse);
    expect(inverted.width * inverted.height).toBeLessThan((width * height) / 10);
    expect(inverted.width * inverted.height).toBeLessThanOrEqual(256 * 256);
    expect(inverted.x).toBeLessThanOrEqual(100);
    expect(inverted.y).toBeLessThanOrEqual(120);
  });

  it('blurs a sparse selection on a large layer without densifying the full canvas', () => {
    // 1024² layer with a tiny selection: work set must stay local to selection±radius.
    const width = 1024;
    const height = 1024;
    const source = createPixelLayer({
      id: 'huge',
      name: 'Huge',
      width,
      height,
    });
    // Paint a small solid patch so blur has content.
    const patchStore = source.pixelStore.write({
      x: 500,
      y: 500,
      width: 16,
      height: 16,
      pixels: new Uint8ClampedArray(16 * 16 * 4).fill(200),
    }).store;
    const painted = createPixelLayer({
      id: 'huge',
      name: 'Huge',
      width,
      height,
      pixelStore: patchStore,
    });
    const selection = createRectangularSelection(
      { width, height },
      { x: 500, y: 500, width: 16, height: 16 },
    );
    const beforeSel = selection.coverageStore.allocatedByteLength;
    expect(beforeSel).toBeLessThan(width * height);

    const result = gaussianBlur(painted, 2, selection);
    expect(result.width).toBeGreaterThan(0);
    expect(result.height).toBeGreaterThan(0);
    // Patch must stay near the selection (not a full-canvas result).
    expect(result.x + result.width).toBeLessThanOrEqual(500 + 16 + 4);
    expect(result.y + result.height).toBeLessThanOrEqual(500 + 16 + 4);
    expect(result.width * result.height).toBeLessThan((width * height) / 100);
    // Selection store remains sparse (immutable).
    expect(selection.coverageStore.allocatedByteLength).toBe(beforeSel);

    // Point filter (invert) similarly stays local.
    const inverted = invert(painted, selection);
    expect(inverted.width * inverted.height).toBeLessThanOrEqual(16 * 16 + 4);
    expect(inverted.x).toBeGreaterThanOrEqual(500 - 1);
    expect(inverted.y).toBeGreaterThanOrEqual(500 - 1);
  });
});

describe('input validation', () => {
  it('rejects malformed layers, selections, and transforms', () => {
    const source = layer(1, 1, [1, 2, 3, 4]);
    const malformed = { ...source, pixelStore: TiledPixelStore.empty(2, 1) };
    expect(() => invert(malformed)).toThrow('pixel buffer length');
    expect(() =>
      invert(source, {
        width: 2,
        height: 1,
        coverageStore: createSelectionMask({ width: 1, height: 1, coverage: [255] }).coverageStore,
      }),
    ).toThrow('Selection coverage length');
    expect(() => invert(source, null, { a: 1, b: 0, c: 0, d: 0, e: 0, f: 0 })).toThrow(
      'invertible',
    );
  });
});
