import { describe, expect, it } from 'vitest';
import {
  createGroupLayer,
  createPixelLayer,
  createRasterMask,
  createTextLayer,
} from '@freetoshop/core-document';
import { layerMatchesFilter, visibleLayerIdsForFilter } from './layerFiltering.js';

type PixelOverrides = Omit<
  Parameters<typeof createPixelLayer>[0],
  'id' | 'name' | 'width' | 'height'
>;
const pixel = (id: string, name = id, options: PixelOverrides = {}) =>
  createPixelLayer({ id, name, width: 2, height: 2, ...options });

describe('layer filtering', () => {
  const child = pixel('child', 'Portrait', { rasterMask: createRasterMask(2, 2) });
  const group = createGroupLayer({ id: 'group', name: 'Artwork', children: [child] });
  const text = createTextLayer({ id: 'text', name: 'Title', width: 20, height: 20 });

  it('matches names case-insensitively and filters exact kinds', () => {
    expect(layerMatchesFilter(text, 'title', 'all')).toBe(true);
    expect(layerMatchesFilter(text, 'TITLE', 'text')).toBe(true);
    expect(layerMatchesFilter(text, 'title', 'pixel')).toBe(false);
  });

  it('filters masks and enabled effects', () => {
    expect(layerMatchesFilter(child, '', 'masked')).toBe(true);
    expect(layerMatchesFilter(pixel('plain'), '', 'masked')).toBe(false);
    expect(
      layerMatchesFilter(
        pixel('effect', 'Effect', {
          effects: [
            {
              type: 'color-overlay',
              enabled: true,
              opacity: 1,
              blendMode: 'normal',
              color: { red: 0, green: 0, blue: 0, alpha: 255 },
            },
          ],
        }),
        '',
        'effects',
      ),
    ).toBe(true);
  });

  it('keeps ancestors for matching descendants and descendants for matching groups', () => {
    expect([...visibleLayerIdsForFilter([group, text], 'portrait', 'all')]).toEqual([
      'child',
      'group',
    ]);
    expect(visibleLayerIdsForFilter([group, text], 'art', 'all')).toEqual(
      new Set(['group', 'child']),
    );
  });

  it('includes every id for the empty all filter', () => {
    expect(visibleLayerIdsForFilter([group, text], '', 'all')).toEqual(
      new Set(['child', 'group', 'text']),
    );
  });
});
