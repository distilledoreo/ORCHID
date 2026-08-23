import { describe, expect, it } from 'vitest';
import {
  ContiguousCoverageSource,
  ContiguousPixelSource,
  DirtyRegionAccumulator,
  TiledCoverageStore,
  TiledPixelStore,
  intersectRegions,
  unionRegions,
} from './index.js';

describe('TiledPixelStore', () => {
  it('round-trips edge tiles without padding their owned buffers', () => {
    const pixels = Uint8ClampedArray.from({ length: 5 * 3 * 4 }, (_, index) => index + 1);
    const store = TiledPixelStore.fromContiguous(5, 3, pixels, 2);
    expect(store).toMatchObject({ columnCount: 3, rowCount: 2, tileCount: 6 });
    expect(store.getTile(2, 1)).toMatchObject({ x: 4, y: 2, width: 1, height: 1 });
    expect(store.getTile(2, 1)?.pixels).toHaveLength(4);
    expect(store.toContiguous()).toEqual(pixels);
  });

  it('allocates sparse tiles and copies only tiles changed by a patch', () => {
    const original = TiledPixelStore.empty(8, 8, 4);
    const first = original.write({
      x: 3,
      y: 3,
      width: 2,
      height: 2,
      pixels: new Uint8ClampedArray(16).fill(255),
    });
    expect(original.tileCount).toBe(0);
    expect(first.store.tileCount).toBe(4);
    expect(first.changedTileKeys).toEqual([0, 1, 2, 3]);
    expect(first.dirtyRegion).toEqual({ x: 3, y: 3, width: 2, height: 2 });

    const unchangedTile = first.store.getTile(1, 1)?.pixels;
    const second = first.store.write({
      x: 0,
      y: 0,
      width: 1,
      height: 1,
      pixels: new Uint8ClampedArray([10, 20, 30, 255]),
    });
    expect(second.changedTileKeys).toEqual([0]);
    expect(second.store.getTile(1, 1)?.pixels).toBe(unchangedTile);
    expect(first.store.toContiguous().slice(0, 4)).toEqual(new Uint8ClampedArray(4));
  });

  it('clips off-canvas writes, reports exact dirt, and removes transparent tiles', () => {
    const store = TiledPixelStore.empty(4, 4, 2);
    const painted = store.write({
      x: -1,
      y: -1,
      width: 2,
      height: 2,
      pixels: new Uint8ClampedArray(16).fill(200),
    });
    expect(painted.dirtyRegion).toEqual({ x: 0, y: 0, width: 1, height: 1 });
    expect(painted.store.tileCount).toBe(1);
    const cleared = painted.store.write({
      x: 0,
      y: 0,
      width: 1,
      height: 1,
      pixels: new Uint8ClampedArray(4),
    });
    expect(cleared.store.tileCount).toBe(0);
    expect(cleared.store.allocatedByteLength).toBe(0);
    expect(
      cleared.store.write({
        x: 0,
        y: 0,
        width: 1,
        height: 1,
        pixels: new Uint8ClampedArray(4),
      }).store,
    ).toBe(cleared.store);
  });

  it('validates buffers and computes region intersection and union', () => {
    expect(() => TiledPixelStore.empty(1, 1, 2048)).toThrow('Tile size cannot exceed');
    expect(() => TiledPixelStore.fromContiguous(2, 2, new Uint8ClampedArray(4))).toThrow(
      'Pixel buffer length',
    );
    expect(
      intersectRegions({ x: 0, y: 0, width: 5, height: 5 }, { x: 3, y: 2, width: 4, height: 2 }),
    ).toEqual({ x: 3, y: 2, width: 2, height: 2 });
    expect(
      unionRegions({ x: 1, y: 2, width: 2, height: 3 }, { x: 0, y: 4, width: 8, height: 2 }),
    ).toEqual({ x: 0, y: 2, width: 8, height: 4 });
  });

  it('reads exact regions and exposes only intersecting allocated chunks', () => {
    const source = TiledPixelStore.empty(8, 8, 4).write({
      x: 3,
      y: 3,
      width: 2,
      height: 2,
      pixels: new Uint8ClampedArray(16).fill(200),
    }).store;
    expect([...source.pixelChunks({ x: 0, y: 0, width: 4, height: 4 })]).toHaveLength(1);
    const read = source.read({ x: 3, y: 3, width: 2, height: 2 });
    expect(read).toEqual(new Uint8ClampedArray(16).fill(200));
    expect(source.read({ x: -1, y: -1, width: 2, height: 2 }).slice(0, 12)).toEqual(
      new Uint8ClampedArray(12),
    );
  });

  it('round-trips deterministic owned snapshots across worker and persistence boundaries', () => {
    const store = TiledPixelStore.empty(7, 5, 4).write({
      x: 3,
      y: 3,
      width: 3,
      height: 2,
      pixels: new Uint8ClampedArray(3 * 2 * 4).fill(90),
    }).store;
    const snapshot = store.snapshot();
    expect(snapshot.tiles.map(({ column, row }) => [column, row])).toEqual([
      [0, 0],
      [1, 0],
      [0, 1],
      [1, 1],
    ]);
    const restored = TiledPixelStore.fromSnapshot(snapshot);
    expect(restored.toContiguous()).toEqual(store.toContiguous());
    snapshot.tiles[0]?.pixels.fill(0);
    expect(restored.tileCount).toBe(4);
    expect(restored.snapshot().tiles[0]?.pixels.some((value) => value !== 0)).toBe(true);
  });

  it('snapshots only allocated tiles intersecting a requested worker region', () => {
    let store = TiledPixelStore.empty(256, 256, 64);
    store = store.write({
      x: 1,
      y: 1,
      width: 1,
      height: 1,
      pixels: new Uint8ClampedArray([1, 2, 3, 255]),
    }).store;
    store = store.write({
      x: 200,
      y: 200,
      width: 1,
      height: 1,
      pixels: new Uint8ClampedArray([4, 5, 6, 255]),
    }).store;
    const snapshot = store.snapshot({ x: 192, y: 192, width: 32, height: 32 });
    expect(snapshot.tiles).toHaveLength(1);
    expect(snapshot.tiles[0]).toMatchObject({ column: 3, row: 3 });
    expect(snapshot.tiles[0]?.pixels.byteLength).toBe(64 * 64 * 4);
  });

  it('snapshots only allocated coverage tiles intersecting a requested worker region', () => {
    const coverage = new Uint8ClampedArray(64 * 64).fill(255);
    let store = TiledCoverageStore.empty(256, 256, 64);
    store = store.write({ x: 0, y: 0, width: 64, height: 64, coverage }).store;
    store = store.write({ x: 192, y: 192, width: 64, height: 64, coverage }).store;

    const snapshot = store.snapshot({ x: 0, y: 0, width: 32, height: 32 });

    expect(snapshot.tiles).toHaveLength(1);
    expect(snapshot.tiles[0]).toMatchObject({ column: 0, row: 0 });
    expect(snapshot.tiles[0]?.coverage.byteLength).toBe(64 * 64);
  });

  it('rejects malformed, duplicate, and non-canonical snapshot tiles', () => {
    const base = {
      schemaVersion: 1 as const,
      width: 4,
      height: 4,
      tileSize: 2,
    };
    const tile = { column: 0, row: 0, pixels: new Uint8ClampedArray(16).fill(1) };
    expect(() => TiledPixelStore.fromSnapshot({ ...base, tiles: [tile, tile] })).toThrow(
      'duplicated',
    );
    expect(() =>
      TiledPixelStore.fromSnapshot({
        ...base,
        tiles: [{ ...tile, column: 2 }],
      }),
    ).toThrow('outside');
    expect(() =>
      TiledPixelStore.fromSnapshot({
        ...base,
        tiles: [{ ...tile, pixels: new Uint8ClampedArray(4) }],
      }),
    ).toThrow('length');
    expect(() =>
      TiledPixelStore.fromSnapshot({
        ...base,
        tiles: [{ ...tile, pixels: new Uint8ClampedArray(16) }],
      }),
    ).toThrow('fully transparent');
  });
});

describe('ContiguousPixelSource', () => {
  it('owns its input and returns exact clipped copies', () => {
    const pixels = new Uint8ClampedArray(4 * 2 * 4);
    pixels.set([10, 20, 30, 255], 4);
    const source = new ContiguousPixelSource(4, 2, pixels);
    pixels.fill(0);
    expect(source.read({ x: 1, y: 0, width: 1, height: 1 })).toEqual(
      new Uint8ClampedArray([10, 20, 30, 255]),
    );
    const contiguous = source.toContiguous();
    contiguous.fill(0);
    expect(source.toContiguous().slice(4, 8)).toEqual(new Uint8ClampedArray([10, 20, 30, 255]));
  });
});

describe('TiledCoverageStore', () => {
  it('round-trips sparse one-channel edge tiles without RGBA amplification', () => {
    const coverage = Uint8ClampedArray.from({ length: 15 }, (_, index) => index + 1);
    const store = TiledCoverageStore.fromContiguous(5, 3, coverage, 2);
    expect(store).toMatchObject({ columnCount: 3, rowCount: 2, tileCount: 6 });
    expect(store.getTile(2, 1)).toMatchObject({ x: 4, y: 2, width: 1, height: 1 });
    expect(store.getTile(2, 1)?.coverage).toHaveLength(1);
    expect(store.allocatedByteLength).toBe(15);
    expect(store.toContiguous()).toEqual(coverage);
  });

  it('copy-on-write patches exact coverage dirt and removes zero tiles', () => {
    const empty = TiledCoverageStore.empty(8, 8, 4);
    const painted = empty.write({
      x: 3,
      y: 3,
      width: 2,
      height: 2,
      coverage: new Uint8ClampedArray(4).fill(200),
    });
    expect(empty.tileCount).toBe(0);
    expect(painted.changedTileKeys).toEqual([0, 1, 2, 3]);
    expect(painted.dirtyRegion).toEqual({ x: 3, y: 3, width: 2, height: 2 });
    const unchanged = painted.store.getTile(1, 1)?.coverage;
    const patched = painted.store.write({
      x: 0,
      y: 0,
      width: 1,
      height: 1,
      coverage: new Uint8ClampedArray([50]),
    });
    expect(patched.store.getTile(1, 1)?.coverage).toBe(unchanged);
    const cleared = patched.store.write({
      x: 0,
      y: 0,
      width: 4,
      height: 4,
      coverage: new Uint8ClampedArray(16),
    });
    expect(cleared.store.getTile(0, 0)).toBeNull();
    expect(patched.store.getTile(0, 0)).not.toBeNull();
  });

  it('owns deterministic snapshots and rejects malformed coverage tiles', () => {
    const store = TiledCoverageStore.fromContiguous(
      3,
      2,
      new Uint8ClampedArray([0, 10, 0, 20, 0, 30]),
      2,
    );
    const snapshot = store.snapshot();
    const restored = TiledCoverageStore.fromSnapshot(snapshot);
    expect(restored.toContiguous()).toEqual(store.toContiguous());
    snapshot.tiles[0]?.coverage.fill(0);
    expect(restored.toContiguous()).toEqual(new Uint8ClampedArray([0, 10, 0, 20, 0, 30]));
    expect(() =>
      TiledCoverageStore.fromSnapshot({
        ...snapshot,
        tiles: [{ column: 0, row: 0, coverage: new Uint8ClampedArray(3) }],
      }),
    ).toThrow('length');
    expect(() =>
      TiledCoverageStore.fromSnapshot({
        ...snapshot,
        tiles: [{ column: 0, row: 0, coverage: new Uint8ClampedArray(4) }],
      }),
    ).toThrow('default coverage');
  });

  it('loads legacy zero-default snapshots', () => {
    const restored = TiledCoverageStore.fromSnapshot({
      schemaVersion: 1,
      width: 2,
      height: 1,
      tileSize: 2,
      tiles: [{ column: 0, row: 0, coverage: new Uint8ClampedArray([0, 190]) }],
    });
    expect(restored.defaultCoverage).toBe(0);
    expect(restored.toContiguous()).toEqual(new Uint8ClampedArray([0, 190]));
  });

  it('inverts coverage as sparse deviations with exact edge dimensions', () => {
    const source = TiledCoverageStore.empty(5, 3, 2).write({
      x: 4,
      y: 2,
      width: 1,
      height: 1,
      coverage: new Uint8ClampedArray([10]),
    }).store;

    const inverted = source.invert();

    const expected = new Uint8ClampedArray(15).fill(255);
    expected[14] = 245;
    expect(inverted.toContiguous()).toEqual(expected);
    expect(inverted).toMatchObject({
      defaultCoverage: 255,
      tileCount: 1,
      allocatedByteLength: 1,
    });
    expect(inverted.snapshot()).toMatchObject({ schemaVersion: 2, defaultCoverage: 255 });
    expect(inverted.invert().toContiguous()).toEqual(source.toContiguous());
    expect(inverted.invert()).toMatchObject({ defaultCoverage: 0, tileCount: 1 });
  });

  it('removes writes that return a complement tile to its implicit default', () => {
    const full = TiledCoverageStore.uniform(8, 8, 255, 4);
    const cut = full.write({
      x: 0,
      y: 0,
      width: 1,
      height: 1,
      coverage: new Uint8ClampedArray([0]),
    }).store;
    expect(cut).toMatchObject({ defaultCoverage: 255, tileCount: 1 });
    const restored = cut.write({
      x: 0,
      y: 0,
      width: 1,
      height: 1,
      coverage: new Uint8ClampedArray([255]),
    }).store;
    expect(restored).toMatchObject({ defaultCoverage: 255, tileCount: 0 });
  });
});

describe('CoverageSource adapters', () => {
  it('reads owned regions consistently from contiguous and tiled coverage', () => {
    const coverage = new Uint8ClampedArray([0, 1, 2, 3, 4, 5]);
    const contiguous = new ContiguousCoverageSource(3, 2, coverage);
    const tiled = TiledCoverageStore.fromContiguous(3, 2, coverage, 2);
    const region = { x: 1, y: 0, width: 2, height: 2 };
    expect(contiguous.read(region)).toEqual(new Uint8ClampedArray([1, 2, 4, 5]));
    expect(tiled.read(region)).toEqual(contiguous.read(region));
    expect(contiguous.toContiguous()).not.toBe(coverage);
    expect([...contiguous.coverageChunks(region)]).toEqual([
      { ...region, coverage: new Uint8ClampedArray([1, 2, 4, 5]) },
    ]);
    expect([...tiled.coverageChunks(region)]).toEqual([
      { x: 0, y: 0, width: 2, height: 2, coverage: new Uint8ClampedArray([0, 1, 3, 4]) },
      { x: 2, y: 0, width: 1, height: 2, coverage: new Uint8ClampedArray([2, 5]) },
    ]);
  });
});

describe('DirtyRegionAccumulator', () => {
  it('clips, merges touching invalidations, and consumes them atomically', () => {
    const dirty = new DirtyRegionAccumulator(100, 80);
    dirty.add({ x: -10, y: 5, width: 20, height: 10 });
    dirty.add({ x: 10, y: 5, width: 5, height: 10 });
    dirty.add({ x: 70, y: 70, width: 50, height: 50 });
    expect(dirty.peek()).toEqual([
      { x: 0, y: 5, width: 15, height: 10 },
      { x: 70, y: 70, width: 30, height: 10 },
    ]);
    expect(dirty.consume()).toHaveLength(2);
    expect(dirty.size).toBe(0);
  });

  it('collapses fragmentation and supports full invalidation', () => {
    const dirty = new DirtyRegionAccumulator(20, 10, 2);
    dirty.add({ x: 0, y: 0, width: 1, height: 1 });
    dirty.add({ x: 5, y: 0, width: 1, height: 1 });
    dirty.add({ x: 10, y: 0, width: 1, height: 1 });
    expect(dirty.peek()).toEqual([{ x: 0, y: 0, width: 11, height: 1 }]);
    dirty.invalidateAll();
    expect(dirty.consume()).toEqual([{ x: 0, y: 0, width: 20, height: 10 }]);
  });
});
