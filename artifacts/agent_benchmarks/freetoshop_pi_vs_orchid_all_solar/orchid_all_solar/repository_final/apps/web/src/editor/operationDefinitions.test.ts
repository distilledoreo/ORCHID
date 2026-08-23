import { describe, expect, it } from 'vitest';
import {
  ADJUSTMENT_LAYER_DEFINITIONS,
  IMAGE_ADJUSTMENT_DEFINITIONS,
  createAdjustmentPreset,
  createSmartFilterFromDraft,
  isImmediateImageAdjustment,
  supportsSmartFilter,
} from './operationDefinitions.js';

describe('operation definitions', () => {
  it('covers all supported direct operations and layers', () => {
    expect(IMAGE_ADJUSTMENT_DEFINITIONS).toHaveLength(7);
    expect(ADJUSTMENT_LAYER_DEFINITIONS).toHaveLength(6);
    expect(createAdjustmentPreset('levels')).toEqual({
      type: 'levels',
      inputBlack: 0,
      inputWhite: 255,
      gamma: 1,
      outputBlack: 0,
      outputWhite: 255,
    });
    expect(createAdjustmentPreset('posterize')).toEqual({ type: 'posterize', levels: 4 });
    expect(createAdjustmentPreset('threshold')).toEqual({ type: 'threshold', level: 128 });
  });
  it('classifies immediate operations and smart filters', () => {
    expect(isImmediateImageAdjustment('invert')).toBe(true);
    expect(isImmediateImageAdjustment('levels')).toBe(false);
    expect(supportsSmartFilter('gaussian-blur')).toBe(true);
    expect(
      createSmartFilterFromDraft(
        'gaussian-blur',
        {
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
          blurRadius: 6,
        },
        'filter-1',
      ),
    ).toMatchObject({ id: 'filter-1', type: 'gaussian-blur', radius: 6 });
  });
});
