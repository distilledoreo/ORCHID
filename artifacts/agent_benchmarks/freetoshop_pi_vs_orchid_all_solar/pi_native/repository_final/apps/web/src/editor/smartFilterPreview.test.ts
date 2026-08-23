import {
  createDocument,
  createPixelLayer,
  createSmartObjectLayer,
} from '@freetoshop/core-document';
import { describe, expect, it } from 'vitest';
import { previewDocumentWithSmartFilterDraft } from './smartFilterPreview.js';
import type { FilterDraft } from './filterOperations.js';

const blurDraft = (radius: number): FilterDraft => ({
  kind: 'gaussian-blur',
  contrast: 0,
  inputBlack: 0,
  gamma: 1,
  inputWhite: 255,
  outputBlack: 0,
  outputWhite: 255,
  hue: 0,
  saturation: 0,
  lightness: 0,
  threshold: 128,
  posterizeLevels: 4,
  blurRadius: radius,
});

describe('previewDocumentWithSmartFilterDraft', () => {
  it('appends a temporary gaussian-blur smart filter for live preview', () => {
    const source = createDocument({
      id: 'src',
      name: 'Source',
      width: 4,
      height: 4,
      layers: [
        createPixelLayer({
          id: 'px',
          name: 'Px',
          width: 4,
          height: 4,
          pixels: new Uint8ClampedArray(4 * 4 * 4).fill(200),
        }),
      ],
    });
    const document = createDocument({
      id: 'doc',
      name: 'Doc',
      width: 4,
      height: 4,
      layers: [
        createSmartObjectLayer({
          id: 'so',
          name: 'SO',
          source,
          smartFilters: [],
        }),
      ],
    });
    const preview = previewDocumentWithSmartFilterDraft(document, 'so', blurDraft(12));
    const layer = preview.layers[0];
    expect(layer?.kind).toBe('smart-object');
    if (layer?.kind !== 'smart-object') throw new Error('expected smart object');
    expect(layer.smartFilters).toHaveLength(1);
    expect(layer.smartFilters[0]).toMatchObject({
      id: 'smart-filter-preview',
      type: 'gaussian-blur',
      radius: 12,
    });
    // Original document is unchanged (preview is a new tree).
    const original = document.layers[0];
    expect(original?.kind === 'smart-object' ? original.smartFilters : null).toEqual([]);
  });

  it('replaces prior preview filter when radius changes', () => {
    const source = createDocument({
      id: 'src',
      name: 'Source',
      width: 2,
      height: 2,
      layers: [
        createPixelLayer({
          id: 'px',
          name: 'Px',
          width: 2,
          height: 2,
          pixels: new Uint8ClampedArray(2 * 2 * 4).fill(100),
        }),
      ],
    });
    const document = createDocument({
      id: 'doc',
      name: 'Doc',
      width: 2,
      height: 2,
      layers: [
        createSmartObjectLayer({
          id: 'so',
          name: 'SO',
          source,
          smartFilters: [
            { id: 'existing', type: 'gaussian-blur', enabled: true, opacity: 1, radius: 2 },
          ],
        }),
      ],
    });
    const first = previewDocumentWithSmartFilterDraft(document, 'so', blurDraft(5));
    const second = previewDocumentWithSmartFilterDraft(first, 'so', blurDraft(9));
    const layer = second.layers[0];
    if (layer?.kind !== 'smart-object') throw new Error('expected smart object');
    expect(layer.smartFilters).toHaveLength(2);
    expect(layer.smartFilters.map((filter) => filter.id)).toEqual([
      'existing',
      'smart-filter-preview',
    ]);
    expect(layer.smartFilters[1]).toMatchObject({ radius: 9 });
  });
});
