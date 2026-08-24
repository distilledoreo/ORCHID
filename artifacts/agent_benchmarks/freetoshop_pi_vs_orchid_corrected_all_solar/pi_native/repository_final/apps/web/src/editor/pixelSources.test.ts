import { createPixelLayer } from '@freetoshop/core-document';
import { describe, expect, it } from 'vitest';
import { createSparsePixelSourceResolver } from './pixelSources.js';

describe('createSparsePixelSourceResolver', () => {
  it('returns the canonical sparse tile resource without conversion', () => {
    const pixels = new Uint8ClampedArray(8 * 8 * 4);
    pixels.set([10, 20, 30, 255], (6 * 8 + 6) * 4);
    const layer = createPixelLayer({ id: 'sparse', name: 'Sparse', width: 8, height: 8, pixels });
    const resolve = createSparsePixelSourceResolver();
    const first = resolve(layer);
    expect(first).toBe(layer.pixelStore);
    expect(first.allocatedByteLength).toBe(8 * 8 * 4);
    expect(resolve(layer)).toBe(first);
    expect(first.read({ x: 6, y: 6, width: 1, height: 1 })).toEqual(
      new Uint8ClampedArray([10, 20, 30, 255]),
    );
  });

  it('returns the same canonical resource for dense layers', () => {
    const dense = createPixelLayer({
      id: 'dense',
      name: 'Dense',
      width: 8,
      height: 8,
      pixels: new Uint8ClampedArray(8 * 8 * 4).fill(255),
    });
    const resolve = createSparsePixelSourceResolver();
    expect(resolve(dense)).toBe(dense.pixelStore);
    expect(resolve(dense).toContiguous()).toEqual(new Uint8ClampedArray(8 * 8 * 4).fill(255));
  });
});
