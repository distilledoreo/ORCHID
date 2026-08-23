import { createDocument, createPixelLayer, createSelectionMask } from '@freetoshop/core-document';
import { TiledCoverageStore, TiledPixelStore } from '@freetoshop/pixel-storage';
import { describe, expect, it, vi } from 'vitest';
import { selectionExportRaster } from './selectionExport.js';

describe('selectionExportRaster', () => {
  it('exports only sparse selected bounds on an 8192² document and applies soft alpha', () => {
    const size = 8192;
    const region = { x: 4096, y: 2048, width: 64, height: 64 };
    const layerStore = TiledPixelStore.empty(size, size).write({
      ...region,
      pixels: new Uint8ClampedArray(region.width * region.height * 4).fill(200),
    }).store;
    const coverageStore = TiledCoverageStore.empty(size, size).write({
      ...region,
      coverage: new Uint8ClampedArray(region.width * region.height).fill(128),
    }).store;
    const layerRead = vi.spyOn(layerStore, 'read');
    const layerMaterialize = vi.spyOn(layerStore, 'toContiguous');
    const coverageRead = vi.spyOn(coverageStore, 'read');
    const coverageMaterialize = vi.spyOn(coverageStore, 'toContiguous');
    const document = createDocument({
      id: 'selection-export',
      name: 'Selection export',
      width: size,
      height: size,
      layers: [
        createPixelLayer({
          id: 'pixels',
          name: 'Pixels',
          width: size,
          height: size,
          pixelStore: layerStore,
        }),
      ],
      selection: createSelectionMask({ width: size, height: size, coverageStore }),
    });

    const raster = selectionExportRaster(document);

    expect(raster).toMatchObject(region);
    expect(raster?.pixels.byteLength).toBe(64 * 64 * 4);
    expect(raster?.pixels.slice(0, 4)).toEqual(new Uint8ClampedArray([200, 200, 200, 100]));
    expect(layerMaterialize).not.toHaveBeenCalled();
    expect(coverageMaterialize).not.toHaveBeenCalled();
    expect(layerRead.mock.calls.every(([requested]) => requested.width <= 64)).toBe(true);
    expect(coverageRead).toHaveBeenCalledWith(region);
  });

  it('returns null without an active nonempty selection', () => {
    expect(
      selectionExportRaster(
        createDocument({ id: 'empty-selection-export', name: 'Empty', width: 4, height: 4 }),
      ),
    ).toBeNull();
  });
});
