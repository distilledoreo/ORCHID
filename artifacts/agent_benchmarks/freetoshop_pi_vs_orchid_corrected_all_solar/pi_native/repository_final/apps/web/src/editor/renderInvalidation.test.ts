import { describe, expect, it } from 'vitest';
import { documentRegionForLocalPatch } from './renderInvalidation.js';

describe('documentRegionForLocalPatch', () => {
  it('transforms the four pixel-cell corners and clips the resulting document AABB', () => {
    expect(
      documentRegionForLocalPatch(
        { x: 1, y: 2, width: 3, height: 4 },
        { a: 0, b: 1, c: -1, d: 0, e: 10, f: -2 },
        { width: 8, height: 8 },
      ),
    ).toEqual({ x: 4, y: 0, width: 4, height: 2 });
  });

  it('returns null for empty or fully off-canvas patches', () => {
    const canvas = { width: 16, height: 16 };
    const identity = { a: 1, b: 0, c: 0, d: 1, e: 0, f: 0 };
    expect(
      documentRegionForLocalPatch({ x: 0, y: 0, width: 0, height: 1 }, identity, canvas),
    ).toBeNull();
    expect(
      documentRegionForLocalPatch(
        { x: 0, y: 0, width: 2, height: 2 },
        { ...identity, e: 20 },
        canvas,
      ),
    ).toBeNull();
  });
});
