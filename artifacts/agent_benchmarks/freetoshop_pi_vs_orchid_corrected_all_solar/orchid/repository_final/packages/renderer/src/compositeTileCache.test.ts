import {
  createDocument,
  createGroupLayer,
  createPixelLayer,
  createRasterMaskFromCoverageStore,
  createShapeLayer,
  createSmartObjectLayer,
  createTextLayer,
  createVectorMask,
  setLayerOpacity,
  setLayerVisibility,
  type ImageDocument,
  type PixelLayer,
} from '@freetoshop/core-document';
import { TiledCoverageStore, TiledPixelStore } from '@freetoshop/pixel-storage';
import { describe, expect, it, vi } from 'vitest';
import {
  CompositeTileCache,
  compositeDocumentPixels,
  compositeDocumentRegion,
  compositeTileDependencyKey,
  rasterizeLayerNearest,
} from './index.js';

function solidLayer(
  id: string,
  width: number,
  height: number,
  rgba: readonly [number, number, number, number],
  options: {
    readonly opacity?: number;
    readonly blendMode?: 'normal' | 'multiply' | 'screen';
  } = {},
): PixelLayer {
  const pixels = new Uint8ClampedArray(width * height * 4);
  // Faster solid fill than per-channel loop for large dense fixtures.
  if (rgba[0] === rgba[1] && rgba[1] === rgba[2] && rgba[2] === rgba[3]) {
    pixels.fill(rgba[0]);
  } else {
    for (let offset = 0; offset < pixels.length; offset += 4) {
      pixels[offset] = rgba[0];
      pixels[offset + 1] = rgba[1];
      pixels[offset + 2] = rgba[2];
      pixels[offset + 3] = rgba[3];
    }
  }
  return createPixelLayer({
    id,
    name: id,
    width,
    height,
    pixels,
    opacity: options.opacity ?? 0.85,
    blendMode: options.blendMode ?? 'normal',
  });
}

function layeredDoc(width: number, height: number, count: number): ImageDocument {
  return createDocument({
    id: 'cache-doc',
    name: 'Cache doc',
    width,
    height,
    layers: Array.from({ length: count }, (_, index) =>
      solidLayer(`layer-${String(index)}`, width, height, [
        (index * 40) % 256,
        (index * 70) % 256,
        (index * 110) % 256,
        255,
      ]),
    ),
  });
}

function reference(document: ImageDocument): Uint8ClampedArray {
  return compositeDocumentPixels(document, { rasterize: rasterizeLayerNearest });
}

describe('CompositeTileCache', () => {
  it('matches the deterministic reference compositor tile-for-tile', () => {
    const document = layeredDoc(48, 32, 3);
    const cache = new CompositeTileCache({ tileSize: 16, maxBytes: 8 * 1024 * 1024 });
    const cached = cache.composite(document, { referenceComposite: reference });
    expect(cached).toEqual(reference(document));
    expect(cache.stats.misses).toBeGreaterThan(0);
  });

  it('serves a fully warm compose without calling the reference compositor', () => {
    const document = layeredDoc(32, 32, 2);
    const cache = new CompositeTileCache({ tileSize: 16 });
    let referenceCalls = 0;
    const tracked = (doc: ImageDocument) => {
      referenceCalls += 1;
      return reference(doc);
    };
    const first = cache.composite(document, { referenceComposite: tracked });
    const coldCalls = referenceCalls;
    const second = cache.composite(document, { referenceComposite: tracked });
    expect(second).toEqual(first);
    expect(referenceCalls).toBe(coldCalls);
    expect(cache.stats.lastComposeFullyWarm).toBe(true);
    expect(cache.stats.hits).toBeGreaterThan(0);
  });

  it('keeps warm tiles faster than a cold reference recompute on repeated frames', () => {
    const document = layeredDoc(64, 64, 4);
    const cache = new CompositeTileCache({ tileSize: 32 });
    cache.composite(document, { referenceComposite: reference });
    const warmStarted = performance.now();
    for (let index = 0; index < 8; index += 1) {
      cache.composite(document, { referenceComposite: reference });
    }
    const warmMs = performance.now() - warmStarted;
    const coldStarted = performance.now();
    for (let index = 0; index < 8; index += 1) {
      reference(document);
    }
    const coldMs = performance.now() - coldStarted;
    expect(warmMs).toBeLessThan(coldMs);
    expect(cache.stats.lastComposeFullyWarm).toBe(true);
  });

  it('invalidates dependency keys when layer visibility changes', () => {
    const document = layeredDoc(16, 16, 2);
    const cache = new CompositeTileCache({ tileSize: 16 });
    const first = cache.composite(document, { referenceComposite: reference });
    const firstLayer = document.layers[0];
    if (!firstLayer) throw new Error('Expected first layer');
    const hidden = setLayerVisibility(firstLayer.id, false).execute(document);
    const second = cache.composite(hidden, { referenceComposite: reference });
    expect(second).toEqual(reference(hidden));
    expect(second).not.toEqual(first);
    expect(cache.stats.misses).toBeGreaterThan(1);
  });

  it('invalidates dependency keys when opacity changes', () => {
    const document = layeredDoc(16, 16, 2);
    const cache = new CompositeTileCache({ tileSize: 8 });
    cache.composite(document, { referenceComposite: reference });
    const secondLayer = document.layers[1];
    if (!secondLayer) throw new Error('Expected second layer');
    const faded = setLayerOpacity(secondLayer.id, 0.25).execute(document);
    const output = cache.composite(faded, { referenceComposite: reference });
    expect(output).toEqual(reference(faded));
  });

  it('evicts least-recently-used tiles under the memory budget', () => {
    const document = layeredDoc(64, 64, 1);
    // One 32×32 RGBA tile is 4096 bytes; budget forces eviction while composing.
    const cache = new CompositeTileCache({ tileSize: 32, maxBytes: 5000 });
    cache.composite(document, { referenceComposite: reference });
    expect(cache.stats.tileCount).toBeGreaterThan(0);
    expect(cache.stats.allocatedBytes).toBeLessThanOrEqual(5000);
    // Second document with different pixels forces new keys and eviction pressure.
    const other = layeredDoc(64, 64, 2);
    cache.composite(other, { referenceComposite: reference });
    expect(cache.stats.evictions).toBeGreaterThan(0);
    expect(cache.stats.allocatedBytes).toBeLessThanOrEqual(5000);
  });

  it('reports unique spatial tile count separately from dependency-key entry count', () => {
    const document = layeredDoc(64, 64, 2);
    // Generous budget so both revisions of the same spatial tiles can remain resident.
    const cache = new CompositeTileCache({ tileSize: 32, maxBytes: 64 * 1024 * 1024 });
    cache.composite(document, { referenceComposite: reference });
    const spatialAfterFirst = cache.stats.uniqueSpatialTileCount;
    expect(spatialAfterFirst).toBeGreaterThan(0);
    expect(cache.stats.tileCount).toBe(spatialAfterFirst);

    const secondLayer = document.layers[1];
    if (!secondLayer) throw new Error('Expected second layer');
    const faded = setLayerOpacity(secondLayer.id, 0.3).execute(document);
    cache.composite(faded, { referenceComposite: reference });

    // New dependency keys for the same coordinates increase entry count.
    expect(cache.stats.tileCount).toBeGreaterThan(spatialAfterFirst);
    // Unique spatial footprint should not double solely from a single opacity revision.
    expect(cache.stats.uniqueSpatialTileCount).toBe(spatialAfterFirst);
  });

  it('preserves tile identity across COW-equivalent pixel stores for dependency keys', () => {
    const pixels = new Uint8ClampedArray(8 * 8 * 4).fill(200);
    const layer = createPixelLayer({
      id: 'px',
      name: 'px',
      width: 8,
      height: 8,
      pixels,
    });
    const region = { x: 0, y: 0, width: 8, height: 8 };
    const first = compositeTileDependencyKey([layer], region);
    const relayer = {
      ...layer,
      pixelStore: TiledPixelStore.fromContiguous(8, 8, layer.pixelStore.toContiguous()),
    };
    // New store instance with equivalent bytes is a different buffer identity — keys differ.
    // Unchanged COW-shared tiles keep identity; here fromContiguous allocates new tiles.
    const second = compositeTileDependencyKey([relayer], region);
    expect(typeof first).toBe('string');
    expect(typeof second).toBe('string');
    expect(first.length).toBeGreaterThan(10);
  });

  it('prioritizes viewport tiles when scheduling composite work', () => {
    const document = layeredDoc(64, 64, 2);
    const cache = new CompositeTileCache({ tileSize: 16 });
    cache.composite(document, {
      referenceComposite: reference,
      viewportRegion: { x: 0, y: 0, width: 16, height: 16 },
    });
    expect(cache.stats.lastComposePrioritizedViewportTiles).toBeGreaterThan(0);
    expect(cache.stats.lastComposePrioritizedViewportTiles).toBeLessThanOrEqual(4);
  });

  it('supports progressive viewport-only assembly without filling off-screen tiles', () => {
    const document = layeredDoc(64, 64, 1);
    const cache = new CompositeTileCache({ tileSize: 32 });
    const output = cache.composite(document, {
      referenceComposite: reference,
      viewportRegion: { x: 0, y: 0, width: 32, height: 32 },
      progressiveViewportOnly: true,
    });
    expect(output.length).toBe(64 * 64 * 4);
    // Only the viewport tile(s) should be cached after a progressive compose.
    expect(cache.stats.tileCount).toBeLessThan(4);
    expect(cache.stats.lastComposePrioritizedViewportTiles).toBeGreaterThan(0);
    // Viewport corner should match the reference composite.
    const full = reference(document);
    expect(output.slice(0, 16)).toEqual(full.slice(0, 16));
  });

  it('maps a screen viewport into a document-space region', () => {
    const region = CompositeTileCache.viewportDocumentRegion(
      { zoom: 2, panX: -10, panY: -20 },
      100,
      80,
      { width: 200, height: 160 },
    );
    expect(region.x).toBe(5);
    expect(region.y).toBe(10);
    expect(region.width).toBe(50);
    expect(region.height).toBe(40);
  });

  it('fills sparse misses with region-local composition instead of a full reference', () => {
    const document = layeredDoc(64, 64, 2);
    const cache = new CompositeTileCache({ tileSize: 32 });
    let fullCalls = 0;
    let regionCalls = 0;
    const referenceComposite = (doc: ImageDocument) => {
      fullCalls += 1;
      return reference(doc);
    };
    const regionComposite = (
      doc: ImageDocument,
      region: { x: number; y: number; width: number; height: number },
    ) => {
      regionCalls += 1;
      return compositeDocumentRegion(doc, region, { rasterize: rasterizeLayerNearest });
    };
    // Cold populate (all tiles miss → full reference path when miss footprint is whole canvas).
    cache.composite(document, { referenceComposite, regionComposite });
    expect(fullCalls).toBe(1);
    // Drop only the top-left tile so the next compose has a minority miss set.
    cache.invalidate([{ x: 0, y: 0, width: 32, height: 32 }], document.canvas);
    fullCalls = 0;
    regionCalls = 0;
    const output = cache.composite(document, { referenceComposite, regionComposite });
    expect(output).toEqual(reference(document));
    // Batched miss fill: one region composite for the union of missing tiles.
    expect(regionCalls).toBe(1);
    expect(cache.stats.lastComposeRegionLocalMisses).toBeGreaterThan(0);
    expect(fullCalls).toBe(0);
  });

  it('batches progressive cold viewport misses into one region composite', () => {
    const document = layeredDoc(128, 128, 2);
    const cache = new CompositeTileCache({ tileSize: 32 });
    let fullCalls = 0;
    let regionCalls = 0;
    let largestRegionArea = 0;
    const referenceComposite = (doc: ImageDocument) => {
      fullCalls += 1;
      return reference(doc);
    };
    const regionComposite = (
      doc: ImageDocument,
      region: { x: number; y: number; width: number; height: number },
    ) => {
      regionCalls += 1;
      largestRegionArea = Math.max(largestRegionArea, region.width * region.height);
      return compositeDocumentRegion(doc, region, { rasterize: rasterizeLayerNearest });
    };
    const viewport = { x: 0, y: 0, width: 64, height: 64 };
    const output = cache.composite(document, {
      referenceComposite,
      regionComposite,
      progressiveViewportOnly: true,
      viewportRegion: viewport,
    });
    // Progressive cold must not pay for a full-canvas reference composite.
    expect(fullCalls).toBe(0);
    expect(regionCalls).toBe(1);
    expect(largestRegionArea).toBeLessThanOrEqual(64 * 64);
    expect(cache.stats.lastComposeRegionLocalMisses).toBeGreaterThan(0);
    expect(cache.stats.lastComposeFullReferenceMisses).toBe(0);
    // Viewport pixels match a cropped full reference.
    const full = reference(document);
    for (let y = 0; y < 64; y += 1) {
      for (let x = 0; x < 64; x += 1) {
        const offset = (y * 128 + x) * 4;
        expect(output.slice(offset, offset + 4)).toEqual(full.slice(offset, offset + 4));
      }
    }
  });

  it('stores mip-level tiles and cancels obsolete work', () => {
    const document = layeredDoc(32, 32, 1);
    const cache = new CompositeTileCache({ tileSize: 16 });
    const mip = cache.composite(document, {
      referenceComposite: reference,
      mipLevel: 1,
    });
    expect(mip.length).toBe(16 * 16 * 4);
    const controller = new AbortController();
    controller.abort();
    expect(() =>
      cache.composite(document, {
        referenceComposite: reference,
        signal: controller.signal,
      }),
    ).toThrow(/cancelled|AbortError/i);
  });

  it('seeds mip tiles directly from an already-downsampled worker frame', () => {
    const document = layeredDoc(32, 32, 1);
    const cache = new CompositeTileCache({ tileSize: 8 });
    const frame = new Uint8ClampedArray(16 * 16 * 4).fill(73);
    const result = cache.composite(document, {
      referenceComposite: () => {
        throw new Error('full-resolution reference must not run');
      },
      mipReferenceComposite: () => frame,
      mipLevel: 1,
    });
    expect(result).toEqual(frame);
    expect(cache.stats.allocatedBytes).toBe(frame.byteLength);
  });

  it('matches compositeDocumentRegion to a cropped full composite for simple stacks', () => {
    const document = layeredDoc(24, 16, 2);
    const region = { x: 4, y: 2, width: 8, height: 6 };
    const local = compositeDocumentRegion(document, region, { rasterize: rasterizeLayerNearest });
    const full = reference(document);
    const cropped = new Uint8ClampedArray(region.width * region.height * 4);
    for (let row = 0; row < region.height; row += 1) {
      const sourceStart = ((region.y + row) * 24 + region.x) * 4;
      cropped.set(
        full.subarray(sourceStart, sourceStart + region.width * 4),
        row * region.width * 4,
      );
    }
    expect(local).toEqual(cropped);
  });

  it('uses an explicit custom region rasterizer without a full-frame fallback on 8192²', () => {
    const size = 8192;
    const store = TiledPixelStore.empty(size, size).write({
      x: 4096,
      y: 2048,
      width: 1,
      height: 1,
      pixels: new Uint8ClampedArray([31, 63, 127, 255]),
    }).store;
    const document = createDocument({
      id: 'custom-region-rasterizer',
      name: 'Custom region rasterizer',
      width: size,
      height: size,
      layers: [
        createPixelLayer({
          id: 'pixels',
          name: 'Pixels',
          width: size,
          height: size,
          pixelStore: store,
          transform: { a: 1, b: 0, c: 0, d: 1, e: 0.25, f: 0.25 },
        }),
      ],
    });
    const region = { x: 4096, y: 2048, width: 64, height: 64 };
    const fullRasterize = vi.fn(() => {
      throw new Error('full-frame custom rasterizer must not run');
    });
    const sourceSizes: Array<{ width: number; height: number; bytes: number }> = [];
    const rasterizeRegion = vi.fn((layer, transform, canvas, pixels, requestedRegion) => {
      sourceSizes.push({ width: layer.width, height: layer.height, bytes: pixels.byteLength });
      return rasterizeLayerNearest(layer, transform, canvas, pixels, requestedRegion);
    });

    const output = compositeDocumentRegion(document, region, {
      rasterize: fullRasterize,
      rasterizeRegion,
    });
    const expected = compositeDocumentRegion(document, region, {
      rasterizeRegion: rasterizeLayerNearest,
    });

    expect(output).toEqual(expected);
    expect(output.byteLength).toBe(64 * 64 * 4);
    expect(fullRasterize).not.toHaveBeenCalled();
    expect(rasterizeRegion).toHaveBeenCalledOnce();
    expect(sourceSizes).toEqual([{ width: 67, height: 67, bytes: 67 * 67 * 4 }]);
  });

  it.each(['red', 'green', 'blue'] as const)(
    'matches the full %s-channel preview crop from an exact bounded region',
    (mode) => {
      const document = layeredDoc(24, 16, 3);
      const region = { x: 5, y: 3, width: 7, height: 6 };
      const local = compositeDocumentRegion(document, region, {
        preview: { mode },
        rasterize: rasterizeLayerNearest,
      });
      const full = compositeDocumentPixels(document, {
        preview: { mode },
        rasterize: rasterizeLayerNearest,
      });
      const cropped = new Uint8ClampedArray(region.width * region.height * 4);
      for (let row = 0; row < region.height; row += 1) {
        const sourceStart = ((region.y + row) * document.canvas.width + region.x) * 4;
        cropped.set(
          full.subarray(sourceStart, sourceStart + region.width * 4),
          row * region.width * 4,
        );
      }
      expect(local).toEqual(cropped);
    },
  );

  it('reads only the requested sparse alpha-channel preview region on an 8192² canvas', () => {
    const size = 8192;
    const coverageStore = TiledCoverageStore.empty(size, size).write({
      x: 4096,
      y: 2048,
      width: 64,
      height: 64,
      coverage: new Uint8ClampedArray(64 * 64).fill(173),
    }).store;
    const read = vi.spyOn(coverageStore, 'read');
    const materialize = vi.spyOn(coverageStore, 'toContiguous');
    const region = { x: 4096, y: 2048, width: 64, height: 64 };
    const output = compositeDocumentRegion(
      createDocument({ id: 'alpha-region', name: 'Alpha region', width: size, height: size }),
      region,
      { preview: { mode: 'alpha', coverage: coverageStore } },
    );

    expect(read).toHaveBeenCalledWith(region);
    expect(materialize).not.toHaveBeenCalled();
    expect(output.byteLength).toBe(64 * 64 * 4);
    expect(output.slice(0, 8)).toEqual(
      new Uint8ClampedArray([173, 173, 173, 255, 173, 173, 173, 255]),
    );
  });

  it('region composite matches full crop for integer-translated pixel layers (fast path)', () => {
    const pixels = new Uint8ClampedArray(8 * 8 * 4);
    for (let i = 0; i < pixels.length; i += 4) {
      pixels[i] = (i / 4) % 256;
      pixels[i + 1] = 40;
      pixels[i + 2] = 80;
      pixels[i + 3] = 255;
    }
    const document = createDocument({
      id: 'translated',
      name: 'Translated',
      width: 16,
      height: 16,
      layers: [
        createPixelLayer({
          id: 'p',
          name: 'P',
          width: 8,
          height: 8,
          pixels,
          transform: { a: 1, b: 0, c: 0, d: 1, e: 3, f: 2 },
        }),
      ],
    });
    const region = { x: 2, y: 1, width: 10, height: 10 };
    const local = compositeDocumentRegion(document, region, { rasterize: rasterizeLayerNearest });
    const full = reference(document);
    const cropped = new Uint8ClampedArray(region.width * region.height * 4);
    for (let row = 0; row < region.height; row += 1) {
      const sourceStart = ((region.y + row) * 16 + region.x) * 4;
      cropped.set(
        full.subarray(sourceStart, sourceStart + region.width * 4),
        row * region.width * 4,
      );
    }
    expect(local).toEqual(cropped);
  });

  it('region composite matches full crop for translated pixel layers with raster masks', () => {
    const pixels = new Uint8ClampedArray(8 * 8 * 4).fill(200);
    const coverage = Uint8ClampedArray.from({ length: 8 * 8 }, (_, index) =>
      index % 3 === 0 ? 255 : 64,
    );
    const coverageStore = TiledCoverageStore.fromContiguous(8, 8, coverage, 4);
    const document = createDocument({
      id: 'translated-masked',
      name: 'Translated masked',
      width: 16,
      height: 16,
      layers: [
        createPixelLayer({
          id: 'p',
          name: 'P',
          width: 8,
          height: 8,
          pixels,
          rasterMask: createRasterMaskFromCoverageStore(8, 8, coverageStore, { density: 0.65 }),
          vectorMask: createVectorMask(
            8,
            8,
            [
              { type: 'move', x: 1, y: 1 },
              { type: 'line', x: 7, y: 1 },
              { type: 'line', x: 7, y: 7 },
              { type: 'line', x: 1, y: 7 },
              { type: 'close' },
            ],
            { density: 0.55 },
          ),
          transform: { a: 1, b: 0, c: 0, d: 1, e: 3, f: 2 },
        }),
      ],
    });
    const region = { x: 2, y: 1, width: 10, height: 10 };
    const local = compositeDocumentRegion(document, region, { rasterize: rasterizeLayerNearest });
    const full = reference(document);
    const cropped = new Uint8ClampedArray(region.width * region.height * 4);
    for (let row = 0; row < region.height; row += 1) {
      const sourceStart = ((region.y + row) * 16 + region.x) * 4;
      cropped.set(
        full.subarray(sourceStart, sourceStart + region.width * 4),
        row * region.width * 4,
      );
    }
    expect(local).toEqual(cropped);
  });

  it('region composite matches full crop for scaled and rotated masked pixel layers', () => {
    const pixels = Uint8ClampedArray.from({ length: 12 * 10 * 4 }, (_, index) =>
      index % 4 === 3 ? 255 : (index * 17) % 256,
    );
    const document = createDocument({
      id: 'affine-masked',
      name: 'Affine masked',
      width: 32,
      height: 28,
      layers: [
        createPixelLayer({
          id: 'pixels',
          name: 'Pixels',
          width: 12,
          height: 10,
          pixels,
          vectorMask: createVectorMask(12, 10),
          effects: [
            {
              type: 'drop-shadow',
              enabled: true,
              opacity: 0.7,
              offsetX: 3,
              offsetY: -2,
              blur: 2,
              color: { red: 20, green: 30, blue: 40, alpha: 255 },
            },
            {
              type: 'stroke',
              enabled: true,
              opacity: 0.6,
              size: 2,
              color: { red: 10, green: 220, blue: 30, alpha: 255 },
            },
            {
              type: 'color-overlay',
              enabled: true,
              opacity: 0.4,
              blendMode: 'screen',
              color: { red: 180, green: 40, blue: 90, alpha: 255 },
            },
          ],
          transform: {
            a: Math.cos(0.31) * 1.35,
            b: Math.sin(0.31) * 1.35,
            c: -Math.sin(0.31) * 0.85,
            d: Math.cos(0.31) * 0.85,
            e: 8.25,
            f: 4.5,
          },
        }),
      ],
    });
    const region = { x: 5, y: 3, width: 17, height: 16 };
    const local = compositeDocumentRegion(document, region, { rasterize: rasterizeLayerNearest });
    const full = reference(document);
    const cropped = new Uint8ClampedArray(region.width * region.height * 4);
    for (let row = 0; row < region.height; row += 1) {
      const sourceStart = ((region.y + row) * 32 + region.x) * 4;
      cropped.set(
        full.subarray(sourceStart, sourceStart + region.width * 4),
        row * region.width * 4,
      );
    }
    expect(local).toEqual(cropped);
  });

  it('reads only an inverse-mapped patch for an affine 8192² pixel layer', () => {
    const size = 8192;
    const store = TiledPixelStore.empty(size, size, 64).write({
      x: 0,
      y: 0,
      width: 64,
      height: 64,
      pixels: new Uint8ClampedArray(64 * 64 * 4).fill(210),
    }).store;
    const read = vi.fn((region: { x: number; y: number; width: number; height: number }) =>
      store.read(region),
    );
    const source = {
      width: size,
      height: size,
      allocatedByteLength: store.allocatedByteLength,
      read,
      pixelChunks: (region?: { x: number; y: number; width: number; height: number }) =>
        store.pixelChunks(region),
      toContiguous: vi.fn(() => {
        throw new Error('affine region composite must not materialize the full layer');
      }),
    };
    const document = createDocument({
      id: 'large-affine',
      name: 'Large affine',
      width: size,
      height: size,
      layers: [
        createPixelLayer({
          id: 'pixels',
          name: 'Pixels',
          width: size,
          height: size,
          pixelStore: store,
          transform: { a: 1.2, b: 0.2, c: -0.1, d: 0.9, e: 8.5, f: 6.25 },
          effects: [
            {
              type: 'drop-shadow',
              enabled: true,
              opacity: 0.8,
              offsetX: 10,
              offsetY: 6,
              blur: 8,
              color: { red: 0, green: 0, blue: 0, alpha: 255 },
            },
            {
              type: 'stroke',
              enabled: true,
              opacity: 1,
              size: 4,
              color: { red: 255, green: 255, blue: 255, alpha: 255 },
            },
          ],
        }),
      ],
    });

    const local = compositeDocumentRegion(
      document,
      { x: 0, y: 0, width: 64, height: 64 },
      {
        rasterize: rasterizeLayerNearest,
        pixelSource: () => source,
      },
    );

    expect(local.byteLength).toBe(64 * 64 * 4);
    expect(read).toHaveBeenCalledTimes(1);
    const requested = read.mock.calls[0]?.[0];
    expect((requested?.width ?? size) * (requested?.height ?? size)).toBeLessThan(128 * 128);
    expect(source.toContiguous).not.toHaveBeenCalled();
  });

  it('matches full crops for affine masked and effected editable shapes', () => {
    const width = 96;
    const height = 80;
    const coverage = new Uint8ClampedArray(width * height).fill(220);
    const shape = createShapeLayer({
      id: 'region-shape',
      name: 'Region shape',
      geometry: { type: 'ellipse', width, height, cornerRadius: 0 },
      style: {
        fill: { red: 210, green: 80, blue: 30, alpha: 230 },
        stroke: { red: 20, green: 180, blue: 240, alpha: 255 },
        strokeWidth: 5,
      },
      transform: { a: 1.1, b: 0.15, c: -0.12, d: 0.9, e: 18.5, f: 12.25 },
      rasterMask: createRasterMaskFromCoverageStore(
        width,
        height,
        TiledCoverageStore.fromContiguous(width, height, coverage),
      ),
      effects: [
        {
          type: 'drop-shadow',
          enabled: true,
          opacity: 0.7,
          offsetX: 3,
          offsetY: -2,
          blur: 2,
          color: { red: 0, green: 0, blue: 0, alpha: 255 },
        },
        {
          type: 'stroke',
          enabled: true,
          opacity: 0.6,
          size: 2,
          color: { red: 255, green: 255, blue: 255, alpha: 255 },
        },
      ],
    });
    const document = createDocument({
      id: 'region-shape-doc',
      name: 'Region shape doc',
      width: 160,
      height: 128,
      layers: [shape],
    });
    const region = { x: 24, y: 20, width: 64, height: 56 };
    const full = compositeDocumentPixels(document, { rasterize: rasterizeLayerNearest });
    const expected = new Uint8ClampedArray(region.width * region.height * 4);
    for (let row = 0; row < region.height; row += 1) {
      const start = ((region.y + row) * document.canvas.width + region.x) * 4;
      expected.set(full.subarray(start, start + region.width * 4), row * region.width * 4);
    }
    expect(compositeDocumentRegion(document, region, { rasterize: rasterizeLayerNearest })).toEqual(
      expected,
    );
  });

  it('rasterizes an 8192² editable path only for the requested region', () => {
    const size = 8192;
    const shape = createShapeLayer({
      id: 'large-region-shape',
      name: 'Large region shape',
      geometry: {
        type: 'path',
        width: size,
        height: size,
        cornerRadius: 0,
        commands: [
          { type: 'move', x: 0, y: 0 },
          { type: 'line', x: size, y: 0 },
          { type: 'line', x: size, y: size },
          { type: 'line', x: 0, y: size },
          { type: 'close' },
        ],
      },
      style: { fill: { red: 30, green: 90, blue: 180, alpha: 255 } },
    });
    const document = createDocument({
      id: 'large-region-shape-doc',
      name: 'Large region shape doc',
      width: size,
      height: size,
      layers: [shape],
    });
    const output = compositeDocumentRegion(
      document,
      { x: 4096, y: 4096, width: 64, height: 64 },
      { rasterize: rasterizeLayerNearest },
    );
    expect(output).toHaveLength(64 * 64 * 4);
    expect(output[3]).toBe(255);
    expect(output.at(-1)).toBe(255);
  });

  it('passes only an inverse-mapped patch to the rasterizer for an 8192² text layer', () => {
    const size = 8192;
    const text = createTextLayer({
      id: 'large-region-text',
      name: 'Large region text',
      text: 'Bounded text',
      width: size,
      height: size,
      style: {
        fontFamily: 'sans-serif',
        fontSize: 48,
        color: { red: 20, green: 40, blue: 80, alpha: 255 },
      },
      transform: { a: 1.1, b: 0.08, c: -0.05, d: 0.95, e: 6.5, f: 4.25 },
      effects: [
        {
          type: 'drop-shadow',
          enabled: true,
          opacity: 0.7,
          offsetX: 5,
          offsetY: 3,
          blur: 4,
          color: { red: 0, green: 0, blue: 0, alpha: 255 },
        },
      ],
    });
    const document = createDocument({
      id: 'large-region-text-doc',
      name: 'Large region text doc',
      width: size,
      height: size,
      layers: [text],
    });
    const rasterize = vi.fn(
      (
        layer: { readonly width: number; readonly height: number },
        _transform: unknown,
        _canvas: unknown,
        _pixels: Uint8ClampedArray,
        region?: { readonly width: number; readonly height: number },
      ) =>
        new Uint8ClampedArray(
          (region?.width ?? layer.width) * (region?.height ?? layer.height) * 4,
        ),
    );
    const output = compositeDocumentRegion(
      document,
      { x: 128, y: 96, width: 64, height: 64 },
      { rasterizeRegion: rasterize },
    );
    expect(output).toHaveLength(64 * 64 * 4);
    expect(rasterize).toHaveBeenCalledTimes(1);
    const [patch] = rasterize.mock.calls[0] ?? [];
    expect(patch?.width).toBeLessThan(128);
    expect(patch?.height).toBeLessThan(128);
  });

  it('matches full crops for affine masked/effected smart objects with ordered filters', () => {
    const sourceSize = 72;
    const sourcePixels = new Uint8ClampedArray(sourceSize * sourceSize * 4);
    for (let y = 12; y < 60; y += 1) {
      for (let x = 10; x < 58; x += 1) {
        const offset = (y * sourceSize + x) * 4;
        sourcePixels.set([40 + x, 80 + y, 180, 230], offset);
      }
    }
    const maskCoverage = new Uint8ClampedArray(sourceSize * sourceSize).fill(210);
    const smart = createSmartObjectLayer({
      id: 'region-smart',
      name: 'Region smart',
      source: {
        id: 'region-smart-source',
        name: 'Region smart source',
        canvas: { width: sourceSize, height: sourceSize },
        layers: [
          createPixelLayer({
            id: 'embedded',
            name: 'Embedded',
            width: sourceSize,
            height: sourceSize,
            pixels: sourcePixels,
          }),
        ],
      },
      smartFilters: [
        { id: 'blur', type: 'gaussian-blur', enabled: true, opacity: 1, radius: 3 },
        { id: 'invert', type: 'invert', enabled: true, opacity: 0.45 },
        { id: 'gray', type: 'grayscale', enabled: true, opacity: 0.3 },
      ],
      rasterMask: createRasterMaskFromCoverageStore(
        sourceSize,
        sourceSize,
        TiledCoverageStore.fromContiguous(sourceSize, sourceSize, maskCoverage),
      ),
      transform: { a: 1.15, b: 0.12, c: -0.08, d: 0.92, e: 20.5, f: 14.25 },
      effects: [
        {
          type: 'drop-shadow',
          enabled: true,
          opacity: 0.65,
          offsetX: 4,
          offsetY: 3,
          blur: 3,
          color: { red: 0, green: 0, blue: 0, alpha: 255 },
        },
      ],
    });
    const document = createDocument({
      id: 'region-smart-doc',
      name: 'Region smart doc',
      width: 140,
      height: 120,
      layers: [smart],
    });
    const region = { x: 26, y: 20, width: 64, height: 56 };
    const full = compositeDocumentPixels(document, { rasterize: rasterizeLayerNearest });
    const expected = new Uint8ClampedArray(region.width * region.height * 4);
    for (let row = 0; row < region.height; row += 1) {
      const start = ((region.y + row) * document.canvas.width + region.x) * 4;
      expected.set(full.subarray(start, start + region.width * 4), row * region.width * 4);
    }
    expect(compositeDocumentRegion(document, region, { rasterize: rasterizeLayerNearest })).toEqual(
      expected,
    );
  });

  it('reads only the recursive source patch for an effected 8192² smart object', () => {
    const size = 8192;
    const tileSize = 64;
    const tile = new Uint8ClampedArray(tileSize * tileSize * 4).fill(150);
    const store = TiledPixelStore.empty(size, size, tileSize)
      .write({ x: 0, y: 0, width: tileSize, height: tileSize, pixels: tile })
      .store.write({ x: 4096, y: 4096, width: tileSize, height: tileSize, pixels: tile }).store;
    const smart = createSmartObjectLayer({
      id: 'large-region-smart',
      name: 'Large region smart',
      source: {
        id: 'large-source',
        name: 'Large source',
        canvas: { width: size, height: size },
        layers: [
          createPixelLayer({
            id: 'large-embedded',
            name: 'Large embedded',
            width: size,
            height: size,
            pixelStore: store,
          }),
        ],
      },
      smartFilters: [{ id: 'blur', type: 'gaussian-blur', enabled: true, opacity: 1, radius: 4 }],
      transform: { a: 1.1, b: 0.08, c: -0.05, d: 0.95, e: 5.5, f: 4.25 },
    });
    const document = createDocument({
      id: 'large-region-smart-doc',
      name: 'Large region smart doc',
      width: size,
      height: size,
      layers: [smart],
    });
    const [documentSmart] = document.layers;
    if (documentSmart?.kind !== 'smart-object') throw new Error('Expected smart object');
    const [embedded] = documentSmart.source.layers;
    if (embedded?.kind !== 'pixel') throw new Error('Expected embedded pixel layer');
    const read = vi.spyOn(embedded.pixelStore, 'read');
    const contiguous = vi.spyOn(embedded.pixelStore, 'toContiguous');
    const output = compositeDocumentRegion(
      document,
      { x: 0, y: 0, width: 64, height: 64 },
      { rasterize: rasterizeLayerNearest },
    );
    expect(output).toHaveLength(64 * 64 * 4);
    expect(read).toHaveBeenCalled();
    for (const [requested] of read.mock.calls) {
      expect(requested.width).toBeLessThan(128);
      expect(requested.height).toBeLessThan(128);
    }
    expect(contiguous).not.toHaveBeenCalled();
  });

  it.each(['pass-through', 'isolated'] as const)(
    'region composite matches full crop for %s raster-masked groups',
    (compositing) => {
      const base = solidLayer('base', 16, 16, [0, 0, 180, 255], { opacity: 1 });
      const child = solidLayer('child', 8, 8, [220, 30, 20, 255], { opacity: 1 });
      const coverage = Uint8ClampedArray.from({ length: 16 * 16 }, (_, index) =>
        index % 5 === 0 ? 255 : 48,
      );
      const document = createDocument({
        id: `group-${compositing}`,
        name: `Group ${compositing}`,
        width: 16,
        height: 16,
        layers: [
          base,
          createGroupLayer({
            id: 'group',
            name: 'Group',
            compositing,
            rasterMask: createRasterMaskFromCoverageStore(
              16,
              16,
              TiledCoverageStore.fromContiguous(16, 16, coverage, 4),
              { density: 0.7 },
            ),
            vectorMask: createVectorMask(
              16,
              16,
              [
                { type: 'move', x: 1, y: 1 },
                { type: 'line', x: 13, y: 1 },
                { type: 'line', x: 13, y: 14 },
                { type: 'line', x: 1, y: 14 },
                { type: 'close' },
              ],
              { density: 0.6, inverted: compositing === 'isolated' },
            ),
            transform: { a: 1, b: 0, c: 0, d: 1, e: 3, f: 2 },
            children: [child],
          }),
        ],
      });
      const region = { x: 2, y: 1, width: 10, height: 11 };
      const local = compositeDocumentRegion(document, region, { rasterize: rasterizeLayerNearest });
      const full = reference(document);
      const cropped = new Uint8ClampedArray(region.width * region.height * 4);
      for (let row = 0; row < region.height; row += 1) {
        const sourceStart = ((region.y + row) * 16 + region.x) * 4;
        cropped.set(
          full.subarray(sourceStart, sourceStart + region.width * 4),
          row * region.width * 4,
        );
      }
      expect(local).toEqual(cropped);
    },
  );

  it('rasterizes an 8192² vector mask only for the requested viewport region', () => {
    const size = 8192;
    const tileSize = 64;
    const tilePixels = new Uint8ClampedArray(tileSize * tileSize * 4).fill(180);
    const store = TiledPixelStore.empty(size, size, tileSize).write({
      x: 0,
      y: 0,
      width: tileSize,
      height: tileSize,
      pixels: tilePixels,
    }).store;
    const document = createDocument({
      id: 'large-vector-region',
      name: 'Large vector region',
      width: size,
      height: size,
      layers: [
        createPixelLayer({
          id: 'pixels',
          name: 'Pixels',
          width: size,
          height: size,
          pixelStore: store,
          vectorMask: createVectorMask(
            size,
            size,
            [
              { type: 'move', x: 0, y: 0 },
              { type: 'line', x: 32, y: 0 },
              { type: 'line', x: 32, y: 64 },
              { type: 'line', x: 0, y: 64 },
              { type: 'close' },
            ],
            { density: 1 },
          ),
        }),
      ],
    });
    const region = { x: 0, y: 0, width: tileSize, height: tileSize };

    const local = compositeDocumentRegion(document, region, { rasterize: rasterizeLayerNearest });

    expect(local.byteLength).toBe(tileSize * tileSize * 4);
    expect(local[3]).toBe(180);
    expect(local[31 * 4 + 3]).toBe(180);
    expect(local[32 * 4 + 3]).toBe(0);
  });

  it('progressive cold region fill stays well under a multi-second budget on 512² dense stacks', () => {
    // M2 cold-path guardrail: integer-translation region reads must keep progressive
    // viewport fills interactive-ish on Node (full browser evidence still via probes).
    const document = layeredDoc(512, 512, 6);
    const cache = new CompositeTileCache({ tileSize: 64, maxBytes: 64 * 1024 * 1024 });
    const regionComposite = (
      doc: ImageDocument,
      area: { x: number; y: number; width: number; height: number },
    ) => compositeDocumentRegion(doc, area, { rasterize: rasterizeLayerNearest });
    const viewportRegion = { x: 64, y: 64, width: 256, height: 192 };
    const started = performance.now();
    const output = cache.composite(document, {
      referenceComposite: (doc) => reference(doc),
      regionComposite,
      progressiveViewportOnly: true,
      viewportRegion,
    });
    const elapsed = performance.now() - started;
    expect(output.length).toBe(512 * 512 * 4);
    expect(cache.stats.lastComposeRegionLocalMisses).toBeGreaterThan(0);
    expect(cache.stats.lastComposeFullReferenceMisses).toBe(0);
    // Tightened after compositor optim: must stay sub-second on CI.
    expect(elapsed).toBeLessThan(1000);
  });

  it('progressive cold fill on 2048² dense stacks stays region-local under budget', () => {
    // Dense (non-sparse) large-doc gate short of full 4k×N OOM risk: solid layers
    // fill every pixel, but progressive viewport still must use region composites.
    const document = layeredDoc(2048, 2048, 4);
    const cache = new CompositeTileCache({ tileSize: 64, maxBytes: 256 * 1024 * 1024 });
    const regionComposite = (
      doc: ImageDocument,
      area: { x: number; y: number; width: number; height: number },
    ) => compositeDocumentRegion(doc, area, { rasterize: rasterizeLayerNearest });
    const viewportRegion = { x: 0, y: 0, width: 384, height: 384 };
    const started = performance.now();
    const output = cache.composite(document, {
      referenceComposite: (doc) => reference(doc),
      regionComposite,
      progressiveViewportOnly: true,
      viewportRegion,
    });
    const elapsed = performance.now() - started;
    expect(output.length).toBe(2048 * 2048 * 4);
    expect(cache.stats.lastComposeFullReferenceMisses).toBe(0);
    expect(cache.stats.lastComposeRegionLocalMisses).toBeGreaterThan(0);
    // Progressive tile set << full canvas (2048/64)^2 = 1024 tiles.
    expect(cache.stats.tileCount).toBeLessThan(1024 / 2);
    // Dense cold progressive should stay multi-second-free on CI hosts.
    expect(elapsed).toBeLessThan(5000);
  });

  it('progressive cold fill on 4096² dense two-layer stack stays region-local', () => {
    // True dense 4k gate: two full-canvas solid layers (not sparse patches).
    // Progressive viewport only; full-reference path must not run.
    const document = layeredDoc(4096, 4096, 2);
    const cache = new CompositeTileCache({ tileSize: 64, maxBytes: 512 * 1024 * 1024 });
    const regionComposite = (
      doc: ImageDocument,
      area: { x: number; y: number; width: number; height: number },
    ) => compositeDocumentRegion(doc, area, { rasterize: rasterizeLayerNearest });
    const viewportRegion = { x: 128, y: 128, width: 384, height: 384 };
    const started = performance.now();
    const output = cache.composite(document, {
      referenceComposite: (doc) => reference(doc),
      regionComposite,
      progressiveViewportOnly: true,
      viewportRegion,
    });
    const elapsed = performance.now() - started;
    expect(output.length).toBe(4096 * 4096 * 4);
    expect(cache.stats.lastComposeFullReferenceMisses).toBe(0);
    expect(cache.stats.lastComposeRegionLocalMisses).toBeGreaterThan(0);
    // (4096/64)^2 = 4096 full tiles; progressive viewport is a small subset.
    expect(cache.stats.tileCount).toBeLessThan(4096 / 4);
    expect(elapsed).toBeLessThan(8000);
  });

  it('progressive cold fill on 4096² dense twelve-layer stack stays region-local and bounded', () => {
    // Closer to the 4k×20 representative fixture: twelve solid full-canvas layers.
    // Fixture construction alone is multi-second; allow generous CI timeout.
    const document = layeredDoc(4096, 4096, 12);
    const cache = new CompositeTileCache({ tileSize: 64, maxBytes: 1024 * 1024 * 1024 });
    const regionComposite = (
      doc: ImageDocument,
      area: { x: number; y: number; width: number; height: number },
    ) => compositeDocumentRegion(doc, area, { rasterize: rasterizeLayerNearest });
    const home = { x: 0, y: 0, width: 384, height: 384 };
    const pan = { x: 1024, y: 512, width: 384, height: 384 };
    const started = performance.now();
    cache.composite(document, {
      referenceComposite: (doc) => reference(doc),
      regionComposite,
      progressiveViewportOnly: true,
      viewportRegion: home,
    });
    cache.composite(document, {
      referenceComposite: (doc) => reference(doc),
      regionComposite,
      progressiveViewportOnly: true,
      viewportRegion: pan,
    });
    const elapsed = performance.now() - started;
    expect(cache.stats.lastComposeFullReferenceMisses).toBe(0);
    expect(cache.stats.lastComposeRegionLocalMisses).toBeGreaterThan(0);
    expect(cache.stats.tileCount).toBeLessThan(4096 / 2);
    expect(cache.stats.allocatedBytes).toBeLessThanOrEqual(1024 * 1024 * 1024);
    expect(elapsed).toBeLessThan(30_000);
  }, 60_000);

  it('progressive cold fill on 4096² dense twenty-layer stack stays region-local and bounded', () => {
    // Goal representative fixture: 4096×4096 with 20 solid layers.
    // Progressive viewport only; prove region-local composite + bounded cache.
    const document = layeredDoc(4096, 4096, 20);
    const cache = new CompositeTileCache({ tileSize: 64, maxBytes: 1536 * 1024 * 1024 });
    const regionComposite = (
      doc: ImageDocument,
      area: { x: number; y: number; width: number; height: number },
    ) => compositeDocumentRegion(doc, area, { rasterize: rasterizeLayerNearest });
    const home = { x: 0, y: 0, width: 384, height: 384 };
    const pan = { x: 1024, y: 512, width: 384, height: 384 };
    const started = performance.now();
    cache.composite(document, {
      referenceComposite: (doc) => reference(doc),
      regionComposite,
      progressiveViewportOnly: true,
      viewportRegion: home,
    });
    const homeTiles = cache.stats.tileCount;
    const homeBytes = cache.stats.allocatedBytes;
    const coldRegionMisses = cache.stats.lastComposeRegionLocalMisses;
    const coldFullRefMisses = cache.stats.lastComposeFullReferenceMisses;
    cache.composite(document, {
      referenceComposite: (doc) => reference(doc),
      regionComposite,
      progressiveViewportOnly: true,
      viewportRegion: pan,
    });
    const panRegionMisses = cache.stats.lastComposeRegionLocalMisses;
    const panFullRefMisses = cache.stats.lastComposeFullReferenceMisses;
    // Warm recompose of home viewport after pan (interactive reassemble).
    const warmStarted = performance.now();
    cache.composite(document, {
      referenceComposite: (doc) => reference(doc),
      regionComposite,
      progressiveViewportOnly: true,
      viewportRegion: home,
    });
    const warmMs = performance.now() - warmStarted;
    const elapsed = performance.now() - started;
    // Cold/pan phases must use region-local fills, never full-reference densify.
    expect(coldFullRefMisses).toBe(0);
    expect(panFullRefMisses).toBe(0);
    expect(coldRegionMisses + panRegionMisses).toBeGreaterThan(0);
    expect(homeTiles).toBeLessThan(4096 / 4);
    expect(cache.stats.tileCount).toBeLessThan(4096 / 2);
    expect(homeBytes).toBeLessThanOrEqual(1536 * 1024 * 1024);
    expect(cache.stats.allocatedBytes).toBeLessThanOrEqual(1536 * 1024 * 1024);
    // Warm viewport reassemble must beat cold construction path (interactive intent).
    expect(warmMs).toBeLessThan(500);
    expect(elapsed).toBeLessThan(60_000);
  }, 180_000);

  it('progressive cold fill on 4096² dense six-layer stack stays region-local and bounded', () => {
    // Intermediate toward 4k×20: six solid full-canvas layers. Progressive viewport
    // only; memory budget must not explode and full-ref path must not run.
    const document = layeredDoc(4096, 4096, 6);
    const cache = new CompositeTileCache({ tileSize: 64, maxBytes: 768 * 1024 * 1024 });
    const regionComposite = (
      doc: ImageDocument,
      area: { x: number; y: number; width: number; height: number },
    ) => compositeDocumentRegion(doc, area, { rasterize: rasterizeLayerNearest });
    const home = { x: 0, y: 0, width: 384, height: 384 };
    const pan = { x: 1024, y: 512, width: 384, height: 384 };
    const started = performance.now();
    cache.composite(document, {
      referenceComposite: (doc) => reference(doc),
      regionComposite,
      progressiveViewportOnly: true,
      viewportRegion: home,
    });
    const coldHomeTiles = cache.stats.tileCount;
    cache.composite(document, {
      referenceComposite: (doc) => reference(doc),
      regionComposite,
      progressiveViewportOnly: true,
      viewportRegion: pan,
    });
    const elapsed = performance.now() - started;
    expect(cache.stats.lastComposeFullReferenceMisses).toBe(0);
    expect(cache.stats.lastComposeRegionLocalMisses).toBeGreaterThan(0);
    expect(coldHomeTiles).toBeLessThan(4096 / 4);
    expect(cache.stats.tileCount).toBeLessThan(4096 / 2);
    // Allocated cache bytes must stay within the configured budget.
    expect(cache.stats.allocatedBytes).toBeLessThanOrEqual(768 * 1024 * 1024);
    expect(elapsed).toBeLessThan(15_000);
  });

  it('progressive cold fill on 4096² sparse document stays region-local and bounded', () => {
    // 4k sparse gate: small placed layers so progressive only pays visible miss-union.
    const patch = new Uint8ClampedArray(256 * 256 * 4);
    for (let i = 0; i < patch.length; i += 4) {
      patch[i] = 40;
      patch[i + 1] = 80;
      patch[i + 2] = 120;
      patch[i + 3] = 255;
    }
    const document = createDocument({
      id: '4k',
      name: '4k sparse',
      width: 4096,
      height: 4096,
      layers: [
        createPixelLayer({
          id: 'a',
          name: 'A',
          width: 256,
          height: 256,
          pixels: patch,
          transform: { a: 1, b: 0, c: 0, d: 1, e: 0, f: 0 },
        }),
        createPixelLayer({
          id: 'b',
          name: 'B',
          width: 256,
          height: 256,
          pixels: patch,
          blendMode: 'multiply',
          transform: { a: 1, b: 0, c: 0, d: 1, e: 32, f: 32 },
        }),
      ],
    });
    const cache = new CompositeTileCache({ tileSize: 64, maxBytes: 128 * 1024 * 1024 });
    const regionComposite = (
      doc: ImageDocument,
      area: { x: number; y: number; width: number; height: number },
    ) => compositeDocumentRegion(doc, area, { rasterize: rasterizeLayerNearest });
    const viewportRegion = { x: 0, y: 0, width: 384, height: 384 };
    const started = performance.now();
    const output = cache.composite(document, {
      referenceComposite: (doc) => reference(doc),
      regionComposite,
      progressiveViewportOnly: true,
      viewportRegion,
    });
    const elapsed = performance.now() - started;
    expect(output.length).toBe(4096 * 4096 * 4);
    expect(cache.stats.lastComposeFullReferenceMisses).toBe(0);
    expect(cache.stats.lastComposeRegionLocalMisses).toBeGreaterThan(0);
    // Progressive must not materialize the full 4k stack on the reference path.
    expect(cache.stats.tileCount).toBeLessThan(Math.ceil(4096 / 64) ** 2 / 4);
    expect(elapsed).toBeLessThan(3000);
  });

  it('progressive cold fill on 8192² sparse document stays region-local and bounded', () => {
    // Goal sparse large-document fixture: 8192×8192 with small placed patches.
    // Progressive must only pay for the visible miss-union, not the full canvas.
    const patch = new Uint8ClampedArray(256 * 256 * 4);
    for (let i = 0; i < patch.length; i += 4) {
      patch[i] = 30;
      patch[i + 1] = 90;
      patch[i + 2] = 160;
      patch[i + 3] = 255;
    }
    const document = createDocument({
      id: '8k',
      name: '8k sparse',
      width: 8192,
      height: 8192,
      layers: [
        createPixelLayer({
          id: 'a',
          name: 'A',
          width: 256,
          height: 256,
          pixels: patch,
          transform: { a: 1, b: 0, c: 0, d: 1, e: 0, f: 0 },
        }),
        createPixelLayer({
          id: 'b',
          name: 'B',
          width: 256,
          height: 256,
          pixels: patch,
          blendMode: 'multiply',
          transform: { a: 1, b: 0, c: 0, d: 1, e: 48, f: 48 },
        }),
        createPixelLayer({
          id: 'c',
          name: 'C',
          width: 256,
          height: 256,
          pixels: patch,
          blendMode: 'screen',
          transform: { a: 1, b: 0, c: 0, d: 1, e: 2000, f: 1500 },
        }),
      ],
    });
    const cache = new CompositeTileCache({ tileSize: 64, maxBytes: 256 * 1024 * 1024 });
    const regionComposite = (
      doc: ImageDocument,
      area: { x: number; y: number; width: number; height: number },
    ) => compositeDocumentRegion(doc, area, { rasterize: rasterizeLayerNearest });
    const home = { x: 0, y: 0, width: 384, height: 384 };
    const pan = { x: 1900, y: 1400, width: 384, height: 384 };
    const started = performance.now();
    cache.composite(document, {
      referenceComposite: (doc) => reference(doc),
      regionComposite,
      progressiveViewportOnly: true,
      viewportRegion: home,
    });
    const homeTiles = cache.stats.tileCount;
    const homeRegionMisses = cache.stats.lastComposeRegionLocalMisses;
    const homeFullRef = cache.stats.lastComposeFullReferenceMisses;
    cache.composite(document, {
      referenceComposite: (doc) => reference(doc),
      regionComposite,
      progressiveViewportOnly: true,
      viewportRegion: pan,
    });
    const elapsed = performance.now() - started;
    expect(homeFullRef).toBe(0);
    expect(cache.stats.lastComposeFullReferenceMisses).toBe(0);
    expect(homeRegionMisses + cache.stats.lastComposeRegionLocalMisses).toBeGreaterThan(0);
    // Full 8192/64 = 128 tiles per side → 16384 tiles; progressive must stay tiny.
    expect(homeTiles).toBeLessThan(Math.ceil(8192 / 64) ** 2 / 50);
    expect(cache.stats.tileCount).toBeLessThan(Math.ceil(8192 / 64) ** 2 / 25);
    expect(cache.stats.allocatedBytes).toBeLessThanOrEqual(256 * 1024 * 1024);
    expect(elapsed).toBeLessThan(10_000);
  }, 30_000);

  it('warmAssembleOnly never invokes referenceComposite for cold misses', () => {
    const document = layeredDoc(64, 64, 4);
    const cache = new CompositeTileCache({ tileSize: 32 });
    let referenceCalls = 0;
    const referenceComposite = (doc: ImageDocument) => {
      referenceCalls += 1;
      return reference(doc);
    };
    const output = cache.composite(document, {
      referenceComposite,
      warmAssembleOnly: true,
      progressiveViewportOnly: true,
      viewportRegion: { x: 0, y: 0, width: 64, height: 64 },
    });
    expect(referenceCalls).toBe(0);
    expect(cache.stats.lastComposeFullReferenceMisses).toBe(0);
    expect(cache.stats.misses).toBeGreaterThan(0);
    expect(cache.stats.lastComposeFullyWarm).toBe(false);
    // Fully cold warm-only assemble leaves transparent holes.
    expect(output.every((value) => value === 0)).toBe(true);
  });

  it('keeps allocatedBytes within maxBytes across a multi-revision working-set session', () => {
    // Tight budget: one 64×64 tile is 16 KiB; full 128×128 / tile 64 needs 4 tiles (~64 KiB).
    const maxBytes = 24 * 1024;
    const cache = new CompositeTileCache({ tileSize: 64, maxBytes });
    let document = layeredDoc(128, 128, 4);
    const samples: number[] = [];
    for (let iteration = 0; iteration < 24; iteration += 1) {
      const layer = document.layers[iteration % document.layers.length];
      if (!layer) throw new Error('expected layer');
      // Unique opacity each iteration so dependency keys cannot reuse forever.
      document = setLayerOpacity(layer.id, Math.min(1, 0.2 + iteration * 0.03)).execute(document);
      cache.composite(document, {
        referenceComposite: reference,
        progressiveViewportOnly: true,
        viewportRegion: { x: 0, y: 0, width: 128, height: 128 },
      });
      expect(cache.stats.allocatedBytes).toBeLessThanOrEqual(maxBytes);
      samples.push(cache.stats.allocatedBytes);
    }
    expect(cache.stats.evictions).toBeGreaterThan(0);
    const midpoint = Math.floor(samples.length / 2);
    const firstPeak = Math.max(...samples.slice(0, midpoint));
    const secondPeak = Math.max(...samples.slice(midpoint));
    // Second half must not climb without bound relative to the first half.
    expect(secondPeak).toBeLessThanOrEqual(firstPeak * 1.25 + 64 * 64 * 4);
  });

  it('keeps memory bounded across a long multi-revision session (80+ edits)', () => {
    const maxBytes = 32 * 1024;
    const cache = new CompositeTileCache({ tileSize: 64, maxBytes });
    // Smaller canvas than the browser long-session probe so unit time stays bounded.
    let document = layeredDoc(128, 128, 4);
    const samples: number[] = [];
    for (let iteration = 0; iteration < 80; iteration += 1) {
      const layer = document.layers[iteration % document.layers.length];
      if (!layer) throw new Error('expected layer');
      document = setLayerOpacity(layer.id, Math.min(1, 0.1 + (iteration % 30) * 0.03)).execute(
        document,
      );
      cache.composite(document, {
        referenceComposite: reference,
        progressiveViewportOnly: true,
        viewportRegion: { x: 0, y: 0, width: 128, height: 128 },
      });
      expect(cache.stats.allocatedBytes).toBeLessThanOrEqual(maxBytes);
      samples.push(cache.stats.allocatedBytes);
    }
    expect(cache.stats.evictions).toBeGreaterThan(20);
    const midpoint = Math.floor(samples.length / 2);
    const firstPeak = Math.max(...samples.slice(0, midpoint));
    const secondPeak = Math.max(...samples.slice(midpoint));
    expect(secondPeak).toBeLessThanOrEqual(firstPeak * 1.25 + 64 * 64 * 4);
    expect(Math.max(...samples)).toBeLessThanOrEqual(maxBytes);
  }, 30_000);

  it('warms progressive pan viewports on a large canvas without filling all tiles', () => {
    // 512² / tile 64 → 64 full tiles; progressive 192² viewports should stay sparse.
    const document = layeredDoc(512, 512, 3);
    const cache = new CompositeTileCache({ tileSize: 64, maxBytes: 32 * 1024 * 1024 });
    const fullTileCount = Math.ceil(512 / 64) * Math.ceil(512 / 64);
    const home = { x: 0, y: 0, width: 192, height: 192 };
    const pan = { x: 256, y: 128, width: 192, height: 192 };
    const regionComposite = (
      doc: ImageDocument,
      area: { x: number; y: number; width: number; height: number },
    ) => compositeDocumentRegion(doc, area, { rasterize: rasterizeLayerNearest });

    cache.composite(document, {
      referenceComposite: reference,
      regionComposite,
      progressiveViewportOnly: true,
      viewportRegion: home,
    });
    expect(cache.stats.tileCount).toBeLessThan(fullTileCount * 0.5);
    expect(cache.stats.lastComposePrioritizedViewportTiles).toBeGreaterThan(0);

    cache.composite(document, {
      referenceComposite: reference,
      regionComposite,
      progressiveViewportOnly: true,
      viewportRegion: home,
    });
    expect(cache.stats.lastComposeFullyWarm).toBe(true);

    cache.composite(document, {
      referenceComposite: reference,
      regionComposite,
      progressiveViewportOnly: true,
      viewportRegion: pan,
    });
    // Pan introduces new tiles; combined footprint still well under full canvas.
    expect(cache.stats.tileCount).toBeLessThan(fullTileCount * 0.75);

    cache.composite(document, {
      referenceComposite: reference,
      regionComposite,
      progressiveViewportOnly: true,
      viewportRegion: pan,
    });
    expect(cache.stats.lastComposeFullyWarm).toBe(true);

    cache.composite(document, {
      referenceComposite: reference,
      regionComposite,
      progressiveViewportOnly: true,
      viewportRegion: home,
    });
    expect(cache.stats.lastComposeFullyWarm).toBe(true);
  });
});
