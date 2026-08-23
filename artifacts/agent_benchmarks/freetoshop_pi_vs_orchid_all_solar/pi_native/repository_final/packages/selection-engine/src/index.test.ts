import { describe, expect, it } from 'vitest';
import {
  borderSelection,
  contractSelection,
  ellipticalSelection,
  expandSelection,
  featherSelection,
  magicWandSelection,
  magicWandSelectionFromSource,
  polygonalSelection,
  smoothSelection,
} from './index.js';

import { createSelectionMask, type SelectionMask } from '@freetoshop/core-document';
import { TiledCoverageStore } from '@freetoshop/pixel-storage';

const mask = (width: number, height: number, coverage: readonly number[]) =>
  createSelectionMask({ width, height, coverage: new Uint8ClampedArray(coverage) });

const rgba = (...pixels: ReadonlyArray<readonly [number, number, number, number]>) =>
  new Uint8ClampedArray(pixels.flat());

describe('vector selections', () => {
  it('creates deterministic antialiased ellipse coverage at pixel-center sub-samples', () => {
    const result = ellipticalSelection(3, 3, { x: 1, y: 1, width: 1, height: 1 });
    expect(result.coverageStore.toContiguous()).toEqual(
      new Uint8ClampedArray([0, 0, 0, 0, 191, 0, 0, 0, 0]),
    );
    expect(result).toMatchObject({ width: 3, height: 3 });
  });

  it('clips ellipses to the canvas and remains symmetric', () => {
    const result = ellipticalSelection(2, 2, { x: -1, y: -1, width: 4, height: 4 });
    expect(result.coverageStore.toContiguous()[0]).toBe(result.coverageStore.toContiguous()[1]);
    expect(result.coverageStore.toContiguous()[1]).toBe(result.coverageStore.toContiguous()[2]);
    expect(result.coverageStore.toContiguous()[2]).toBe(result.coverageStore.toContiguous()[3]);
    expect(result.coverageStore.toContiguous()[0]).toBeGreaterThan(0);
  });

  it('creates an antialiased even-odd polygon without owning or mutating point data', () => {
    const points = [
      { x: 0, y: 0 },
      { x: 2, y: 0 },
      { x: 0, y: 2 },
    ];
    const before = structuredClone(points);
    const result = polygonalSelection(2, 2, points);
    expect(result.coverageStore.toContiguous()).toEqual(new Uint8ClampedArray([255, 159, 159, 0]));
    expect(points).toEqual(before);
  });

  it('supports concave polygons with deterministic even-odd fill', () => {
    const result = polygonalSelection(3, 3, [
      { x: 0, y: 0 },
      { x: 3, y: 0 },
      { x: 3, y: 1 },
      { x: 1, y: 1 },
      { x: 1, y: 3 },
      { x: 0, y: 3 },
    ]);
    expect(result.coverageStore.toContiguous()).toEqual(
      new Uint8ClampedArray([255, 255, 255, 255, 0, 0, 255, 0, 0]),
    );
  });

  it('validates vector canvas, geometry, points, and allocation ceilings', () => {
    expect(() => ellipticalSelection(0, 1, { x: 0, y: 0, width: 1, height: 1 })).toThrow(
      'Selection width must be a positive integer',
    );
    expect(() => ellipticalSelection(32_769, 1, { x: 0, y: 0, width: 1, height: 1 })).toThrow(
      'Selection dimensions cannot exceed 32768 pixels',
    );
    expect(() => ellipticalSelection(20_000, 10_000, { x: 0, y: 0, width: 1, height: 1 })).toThrow(
      'Selection area cannot exceed 100000000 pixels',
    );
    expect(() => ellipticalSelection(1, 1, { x: 0, y: 0, width: 0, height: 1 })).toThrow(
      'Ellipse dimensions must be positive',
    );
    expect(() => ellipticalSelection(1, 1, { x: Number.NaN, y: 0, width: 1, height: 1 })).toThrow(
      'Ellipse x must be finite',
    );
    expect(() => polygonalSelection(1, 1, [{ x: 0, y: 0 }])).toThrow(
      'Polygon must contain at least three points',
    );
    expect(() =>
      polygonalSelection(1, 1, [
        { x: 0, y: 0 },
        { x: 1, y: Number.POSITIVE_INFINITY },
        { x: 0, y: 1 },
      ]),
    ).toThrow('Polygon point y must be finite');
  });
});

describe('magic wand selection', () => {
  const pixels = rgba(
    [100, 0, 0, 255],
    [110, 0, 0, 255],
    [120, 0, 0, 255],
    [0, 0, 0, 255],
    [0, 0, 0, 255],
    [100, 0, 0, 255],
  );

  it('uses RMS byte distance and four-connected flood fill in contiguous mode', () => {
    const before = new Uint8ClampedArray(pixels);
    const result = magicWandSelection({
      pixels,
      width: 3,
      height: 2,
      seedX: 0,
      seedY: 0,
      tolerance: 6,
      contiguous: true,
      sampleAlpha: false,
    });
    expect(result.coverageStore.toContiguous()).toEqual(
      new Uint8ClampedArray([255, 255, 0, 0, 0, 0]),
    );
    expect(pixels).toEqual(before);
  });

  it('selects every matching region when contiguous mode is disabled', () => {
    const result = magicWandSelection({
      pixels,
      width: 3,
      height: 2,
      seedX: 0,
      seedY: 0,
      tolerance: 6,
      contiguous: false,
      sampleAlpha: false,
    });
    expect(result.coverageStore.toContiguous()).toEqual(
      new Uint8ClampedArray([255, 255, 0, 0, 0, 255]),
    );
  });

  it('optionally includes alpha in color distance', () => {
    const alphaPixels = rgba([50, 60, 70, 255], [50, 60, 70, 0]);
    const base = {
      pixels: alphaPixels,
      width: 2,
      height: 1,
      seedX: 0,
      seedY: 0,
      tolerance: 100,
      contiguous: false,
    } as const;
    expect(
      magicWandSelection({ ...base, sampleAlpha: false }).coverageStore.toContiguous(),
    ).toEqual(new Uint8ClampedArray([255, 255]));
    expect(magicWandSelection({ ...base, sampleAlpha: true }).coverageStore.toContiguous()).toEqual(
      new Uint8ClampedArray([255, 0]),
    );
  });

  it('does not connect diagonally adjacent matching pixels', () => {
    const result = magicWandSelection({
      pixels: rgba([1, 2, 3, 255], [9, 9, 9, 255], [9, 9, 9, 255], [1, 2, 3, 255]),
      width: 2,
      height: 2,
      seedX: 0,
      seedY: 0,
      tolerance: 0,
      contiguous: true,
      sampleAlpha: true,
    });
    expect(result.coverageStore.toContiguous()).toEqual(new Uint8ClampedArray([255, 0, 0, 0]));
  });

  it('validates dimensions, pixel buffers, seed coordinates, and tolerance', () => {
    const base = {
      pixels: new Uint8ClampedArray(4),
      width: 1,
      height: 1,
      seedX: 0,
      seedY: 0,
      tolerance: 0,
      contiguous: true,
      sampleAlpha: false,
    } as const;
    expect(() => magicWandSelection({ ...base, pixels: new Uint8ClampedArray(3) })).toThrow(
      'Magic wand pixel buffer must match its dimensions',
    );
    expect(() => magicWandSelection({ ...base, seedX: 0.5 })).toThrow(
      'Magic wand seed coordinates must be integers',
    );
    expect(() => magicWandSelection({ ...base, seedX: 1 })).toThrow(
      'Magic wand seed is outside the image',
    );
    expect(() => magicWandSelection({ ...base, tolerance: 256 })).toThrow(
      'Magic wand tolerance must be between 0 and 255',
    );
  });

  it('keeps contiguous wand results sparse on large mostly-nonmatching images', () => {
    const width = 1024;
    const height = 1024;
    const pixels = new Uint8ClampedArray(width * height * 4);
    // Distinct seed island surrounded by non-matching color.
    for (let offset = 0; offset < pixels.length; offset += 4) {
      pixels[offset] = 200;
      pixels[offset + 1] = 10;
      pixels[offset + 2] = 10;
      pixels[offset + 3] = 255;
    }
    const seedX = 50;
    const seedY = 60;
    for (let y = seedY; y < seedY + 8; y += 1) {
      for (let x = seedX; x < seedX + 8; x += 1) {
        const offset = (y * width + x) * 4;
        pixels[offset] = 1;
        pixels[offset + 1] = 2;
        pixels[offset + 2] = 3;
        pixels[offset + 3] = 255;
      }
    }
    const result = magicWandSelection({
      pixels,
      width,
      height,
      seedX,
      seedY,
      tolerance: 0,
      contiguous: true,
      sampleAlpha: true,
    });
    // One default coverage tile (e.g. 256²) is fine; full 1024² buffer is not.
    expect(result.coverageStore.allocatedByteLength).toBeLessThan((width * height) / 8);
    expect(result.coverageStore.allocatedByteLength).toBeLessThanOrEqual(256 * 256);
    expect(result.coverageStore.toContiguous()[seedY * width + seedX]).toBe(255);
    expect(result.coverageStore.toContiguous()[0]).toBe(0);
    // 8×8 island fully selected.
    expect(result.coverageStore.toContiguous()[(seedY + 7) * width + (seedX + 7)]).toBe(255);
    expect(result.coverageStore.toContiguous()[(seedY + 8) * width + seedX] ?? 0).toBe(0);
  });

  it('globally scans an 8192² region source with one bounded tile buffer at a time', () => {
    const width = 8192;
    const height = 8192;
    let readCalls = 0;
    let maximumReadBytes = 0;
    const result = magicWandSelectionFromSource({
      source: {
        width,
        height,
        read(region) {
          readCalls += 1;
          const pixels = new Uint8ClampedArray(region.width * region.height * 4);
          maximumReadBytes = Math.max(maximumReadBytes, pixels.byteLength);
          for (let localY = 0; localY < region.height; localY += 1) {
            for (let localX = 0; localX < region.width; localX += 1) {
              const offset = (localY * region.width + localX) * 4;
              const documentX = region.x + localX;
              const documentY = region.y + localY;
              const matches =
                (documentX === 1 && documentY === 1) ||
                (documentX === width - 1 && documentY === height - 1);
              pixels[offset] = matches ? 1 : 200;
              pixels[offset + 1] = matches ? 2 : 10;
              pixels[offset + 2] = matches ? 3 : 10;
              pixels[offset + 3] = 255;
            }
          }
          return pixels;
        },
      },
      seedX: 1,
      seedY: 1,
      tolerance: 0,
      contiguous: false,
      sampleAlpha: true,
    });
    expect(readCalls).toBe(1 + (width / 256) * (height / 256));
    expect(maximumReadBytes).toBe(256 * 256 * 4);
    expect(result.coverageStore.tileCount).toBe(2);
    expect(result.coverageStore.allocatedByteLength).toBe(2 * 256 * 256);
    expect(result.coverageStore.getTile(0, 0)?.coverage[1 * 256 + 1]).toBe(255);
    expect(result.coverageStore.getTile(31, 31)?.coverage.at(-1)).toBe(255);
  });
});

describe('selection modification', () => {
  it('feathers coverage with deterministic zero-padded box filtering', () => {
    const source = mask(3, 3, [0, 0, 0, 0, 255, 0, 0, 0, 0]);
    const before = new Uint8ClampedArray(source.coverageStore.toContiguous());
    const result = featherSelection(source, 1);
    expect(result.coverageStore.toContiguous()).toEqual(new Uint8ClampedArray(9).fill(28));
    expect(source.coverageStore.toContiguous()).toEqual(before);
  });

  it('feathers sparse selections without densifying large transparent canvases', () => {
    const width = 2048;
    const height = 2048;
    const store = TiledCoverageStore.empty(width, height).write({
      x: 100,
      y: 100,
      width: 32,
      height: 32,
      coverage: new Uint8ClampedArray(32 * 32).fill(255),
    }).store;
    const source: SelectionMask = { width, height, coverageStore: store };
    const beforeBytes = source.coverageStore.allocatedByteLength;
    const result = featherSelection(source, 4);
    const afterBytes = result.coverageStore.allocatedByteLength;
    expect(afterBytes).toBeGreaterThan(0);
    expect(afterBytes).toBeLessThan((width * height) / 20);
    // Still densifies the feather halo working set, not the full canvas.
    expect(afterBytes).toBeLessThanOrEqual(beforeBytes * 16);
    expect(result.coverageStore.toContiguous()[100 * width + 100]).toBeGreaterThan(0);
    expect(result.coverageStore.toContiguous()[0]).toBe(0);
  });

  it('expands using maximum coverage and contracts using minimum coverage', () => {
    const point = mask(3, 3, [0, 0, 0, 0, 127, 0, 0, 0, 0]);
    expect(expandSelection(point, 1).coverageStore.toContiguous()).toEqual(
      new Uint8ClampedArray(9).fill(127),
    );
    const full = mask(3, 3, new Array<number>(9).fill(200));
    expect(contractSelection(full, 1).coverageStore.toContiguous()).toEqual(
      new Uint8ClampedArray([0, 0, 0, 0, 200, 0, 0, 0, 0]),
    );
  });

  it('smooths isolated pixels with a deterministic median neighborhood', () => {
    const isolated = mask(3, 3, [0, 0, 0, 0, 255, 0, 0, 0, 0]);
    expect(smoothSelection(isolated, 1).coverageStore.toContiguous()).toEqual(
      new Uint8ClampedArray(9),
    );
    const hole = mask(3, 3, [255, 255, 255, 255, 0, 255, 255, 255, 255]);
    expect(smoothSelection(hole, 1).coverageStore.toContiguous()[4]).toBe(255);
  });

  it('creates a centered border from expanded minus contracted coverage', () => {
    const source = mask(
      5,
      5,
      [0, 0, 0, 0, 0, 0, 255, 255, 255, 0, 0, 255, 255, 255, 0, 0, 255, 255, 255, 0, 0, 0, 0, 0, 0],
    );
    const expected = new Uint8ClampedArray(25).fill(255);
    expected[12] = 0;
    expect(borderSelection(source, 1).coverageStore.toContiguous()).toEqual(expected);
  });

  it('produces identical neighborhood results for dense and complement representations', () => {
    const width = 7;
    const height = 5;
    const coverage = new Uint8ClampedArray(width * height).fill(211);
    coverage[0] = 15;
    coverage[2 * width + 3] = 0;
    coverage[height * width - 1] = 87;
    const dense: SelectionMask = {
      width,
      height,
      coverageStore: TiledCoverageStore.fromContiguous(width, height, coverage, 4),
    };
    let complementStore = TiledCoverageStore.uniform(width, height, 211, 4);
    complementStore = complementStore.write({ x: 0, y: 0, width, height, coverage }).store;
    const complement: SelectionMask = { width, height, coverageStore: complementStore };

    const operations = [
      (source: SelectionMask) => featherSelection(source, 1),
      (source: SelectionMask) => expandSelection(source, 1),
      (source: SelectionMask) => contractSelection(source, 1),
      (source: SelectionMask) => smoothSelection(source, 1),
      (source: SelectionMask) => borderSelection(source, 1),
    ];
    for (const operation of operations) {
      expect(operation(complement).coverageStore.toContiguous()).toEqual(
        operation(dense).coverageStore.toContiguous(),
      );
    }
  });

  it('keeps 4096-square complement morphology tile-bounded', () => {
    const width = 4096;
    const height = 4096;
    const hole = new Uint8ClampedArray(8 * 8);
    const store = TiledCoverageStore.uniform(width, height, 255).write({
      x: 2044,
      y: 2044,
      width: 8,
      height: 8,
      coverage: hole,
    }).store;
    const source: SelectionMask = { width, height, coverageStore: store };
    const fullBytes = width * height;
    const maximumTileNeighborhoodBytes = 64 * 256 * 256;

    for (const result of [
      featherSelection(source, 1),
      expandSelection(source, 1),
      contractSelection(source, 1),
      smoothSelection(source, 1),
      borderSelection(source, 1),
    ]) {
      expect(result.coverageStore.allocatedByteLength).toBeLessThanOrEqual(
        maximumTileNeighborhoodBytes,
      );
      expect(result.coverageStore.allocatedByteLength).toBeLessThan(fullBytes / 3);
    }
    expect(expandSelection(source, 1).coverageStore.defaultCoverage).toBe(255);
    expect(expandSelection(source, 1).coverageStore.tileCount).toBeLessThanOrEqual(4);
    expect(
      contractSelection(source, 1).coverageStore.read({ x: 0, y: 0, width: 1, height: 1 })[0],
    ).toBe(0);
    expect(
      contractSelection(source, 1).coverageStore.read({
        x: width / 2,
        y: height / 2,
        width: 1,
        height: 1,
      })[0],
    ).toBe(0);
  });

  it('returns owned copies for zero-radius operations', () => {
    const source = mask(2, 1, [12, 240]);
    for (const result of [
      featherSelection(source, 0),
      expandSelection(source, 0),
      contractSelection(source, 0),
      smoothSelection(source, 0),
    ]) {
      expect(result.coverageStore.toContiguous()).toEqual(source.coverageStore.toContiguous());
      expect(result.coverageStore.toContiguous()).not.toBe(source.coverageStore.toContiguous());
    }
  });

  it('validates mask coverage and operation radii', () => {
    const source = mask(1, 1, [255]);
    expect(() =>
      featherSelection(
        {
          width: 2,
          height: 2,
          coverageStore: createSelectionMask({ width: 1, height: 1, coverage: [255] })
            .coverageStore,
        },
        1,
      ),
    ).toThrow('Selection coverage must match its dimensions');
    expect(() => expandSelection(source, -1)).toThrow(
      'Selection radius must be a non-negative integer',
    );
    expect(() => contractSelection(source, 0.5)).toThrow(
      'Selection radius must be a non-negative integer',
    );
    expect(() => smoothSelection(source, 32_769)).toThrow(
      'Smooth radius cannot exceed 32768 pixels',
    );
    expect(() => borderSelection(source, 0)).toThrow('Border width must be a positive integer');
  });
});
