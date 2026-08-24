import { describe, expect, it, vi } from 'vitest';
import { TiledCoverageStore, TiledPixelStore } from '@freetoshop/pixel-storage';
import { sampleMaskThumbnail, samplePixelThumbnail } from './layerThumbnailSampling.js';

describe('layer thumbnail sampling', () => {
  it('samples immutable pixel tiles without materializing a full surface', () => {
    const store = TiledPixelStore.fromContiguous(
      2,
      1,
      new Uint8ClampedArray([255, 0, 0, 255, 0, 0, 255, 128]),
      1,
    );
    expect([...samplePixelThumbnail(store, 2)]).toEqual([
      255, 0, 0, 255, 0, 0, 255, 128, 255, 0, 0, 255, 0, 0, 255, 128,
    ]);
  });

  it('samples sparse raster-mask coverage as opaque grayscale', () => {
    const store = TiledCoverageStore.fromContiguous(2, 1, new Uint8ClampedArray([0, 192]), 1);
    expect([...sampleMaskThumbnail(store, 2)]).toEqual([
      0, 0, 0, 255, 192, 192, 192, 255, 0, 0, 0, 255, 192, 192, 192, 255,
    ]);
  });

  it('samples complement masks without expanding implicit coverage chunks', () => {
    const store = TiledCoverageStore.uniform(4096, 4096, 255, 256).write({
      x: 2048,
      y: 2048,
      width: 1,
      height: 1,
      coverage: new Uint8ClampedArray([0]),
    }).store;
    const chunks = vi.spyOn(store, 'coverageChunks');
    const read = vi.spyOn(store, 'read');
    const thumbnail = sampleMaskThumbnail(store, 32);
    expect(chunks).not.toHaveBeenCalled();
    expect(read).toHaveBeenCalledTimes(32 * 32);
    expect(thumbnail.every((value) => value === 255)).toBe(true);
  });
});
