import { describe, expect, it } from 'vitest';
import {
  BLEND_MODES,
  blendChannel,
  blendRgb,
  compositePixel,
  compositePremultipliedPixel,
  compositeSurface,
  compositeSurfaceInPlace,
  premultiplyPixel,
  unpremultiplyPixel,
  type BlendMode,
  type RgbaPixel,
} from './index.js';

describe('professional blend modes', () => {
  const backdrop: RgbaPixel = [40, 80, 120, 255];
  const source: RgbaPixel = [200, 100, 50, 255];
  const goldens: Record<BlendMode, RgbaPixel> = {
    normal: [200, 100, 50, 255],
    multiply: [31, 31, 24, 255],
    screen: [209, 149, 146, 255],
    overlay: [63, 63, 47, 255],
    darken: [40, 80, 50, 255],
    'darker-color': [40, 80, 120, 255],
    lighten: [200, 100, 120, 255],
    'lighter-color': [200, 100, 50, 255],
    'color-dodge': [185, 132, 149, 255],
    'linear-dodge': [240, 180, 170, 255],
    'color-burn': [0, 0, 0, 255],
    'linear-burn': [0, 0, 0, 255],
    'hard-light': [162, 63, 47, 255],
    'soft-light': [74, 68, 81, 255],
    'vivid-light': [93, 32, 0, 255],
    'linear-light': [185, 25, 0, 255],
    'pin-light': [145, 80, 100, 255],
    'hard-mix': [0, 0, 0, 255],
    difference: [160, 20, 70, 255],
    exclusion: [177, 117, 123, 255],
    subtract: [0, 0, 70, 255],
    divide: [51, 204, 255, 255],
    hue: [113, 59, 33, 255],
    saturation: [12, 87, 162, 255],
    color: [146, 49, 0, 255],
    luminosity: [92, 132, 172, 255],
  };

  it.each(BLEND_MODES)('matches the opaque %s golden', (blendMode) => {
    expect(compositePixel(backdrop, source, { blendMode })).toEqual(goldens[blendMode]);
  });

  it('requires complete RGB inputs for nonseparable channel blending', () => {
    expect(() => blendChannel('hue', 0.25, 0.75)).toThrow('requires complete RGB colors');
    expect(blendRgb('color', [0.1, 0.2, 0.3], [0.9, 0.2, 0.1])).toEqual([
      0.4842809364548494, 0.060535117056856186, 0,
    ]);
  });

  it('handles divide-by-zero and hard-mix thresholds deterministically', () => {
    expect(blendChannel('divide', 0.25, 0)).toBe(1);
    expect(blendChannel('hard-mix', 0.75, 0.75)).toBe(1);
    expect(blendChannel('hard-mix', 0.25, 0.25)).toBe(0);
  });
});

describe('premultiplied alpha', () => {
  it('composites partial alpha without intermediate byte quantization', () => {
    const output = compositePremultipliedPixel([0, 0, 0.5, 0.5], [0.5, 0, 0, 0.5]);
    expect(output[0]).toBeCloseTo(0.5, 12);
    expect(output[1]).toBe(0);
    expect(output[2]).toBeCloseTo(0.25, 12);
    expect(output[3]).toBeCloseTo(0.75, 12);
    expect(unpremultiplyPixel(output)).toEqual([170, 0, 85, 191]);
  });

  it('keeps opacity and fill opacity as independent source-coverage factors', () => {
    const output = compositePremultipliedPixel([0, 0, 0.5, 0.5], [0.5, 0, 0, 0.5], {
      opacity: 0.5,
      fillOpacity: 0.5,
    });
    expect(output[0]).toBeCloseTo(0.125, 12);
    expect(output[2]).toBeCloseTo(0.4375, 12);
    expect(output[3]).toBeCloseTo(0.5625, 12);
  });

  it('matches a partial-alpha multiply golden with opacity and fill opacity', () => {
    expect(
      compositePixel([20, 80, 200, 128], [220, 40, 100, 128], {
        blendMode: 'multiply',
        opacity: 0.5,
        fillOpacity: 0.5,
      }),
    ).toEqual([42, 68, 175, 144]);
  });

  it('does not leak color from a fully transparent source', () => {
    expect(compositePixel([12, 34, 56, 78], [255, 99, 1, 0], { blendMode: 'screen' })).toEqual([
      12, 34, 56, 78,
    ]);
    expect(compositePremultipliedPixel([0, 0, 0, 0], [1, 1, 1, 0])).toEqual([0, 0, 0, 0]);
  });

  it('preserves straight source color over transparency while scaling alpha', () => {
    expect(compositePixel([0, 0, 0, 0], [300, -20, 128, 200], { opacity: 0.5 })).toEqual([
      255, 0, 128, 100,
    ]);
  });
});

describe('input constraints', () => {
  it('clamps straight and premultiplied channels to their valid ranges', () => {
    expect(premultiplyPixel([999, -1, 127.5, 999])).toEqual([1, 0, 128 / 255, 1]);
    expect(unpremultiplyPixel([2, -1, 0.25, 0.5])).toEqual([255, 0, 128, 128]);
  });

  it('rejects invalid opacity values', () => {
    expect(() => compositePixel([0, 0, 0, 0], [0, 0, 0, 0], { opacity: 1.01 })).toThrow(
      'Opacity must be a finite number between 0 and 1.',
    );
    expect(() => compositePixel([0, 0, 0, 0], [0, 0, 0, 0], { fillOpacity: Number.NaN })).toThrow(
      'Fill opacity must be a finite number between 0 and 1.',
    );
  });
});

describe('surface helpers', () => {
  it('returns a new composite without mutating either input', () => {
    const backdrop = new Uint8ClampedArray([0, 0, 255, 255, 10, 20, 30, 255]);
    const source = new Uint8ClampedArray([255, 0, 0, 128, 110, 120, 130, 255]);
    const result = compositeSurface(backdrop, source);
    expect(result).toEqual(new Uint8ClampedArray([128, 0, 127, 255, 110, 120, 130, 255]));
    expect(backdrop).toEqual(new Uint8ClampedArray([0, 0, 255, 255, 10, 20, 30, 255]));
    expect(source).toEqual(new Uint8ClampedArray([255, 0, 0, 128, 110, 120, 130, 255]));
  });

  it('supports deterministic in-place surface compositing', () => {
    const destination = new Uint8ClampedArray([40, 80, 120, 255]);
    compositeSurfaceInPlace(destination, new Uint8ClampedArray([200, 100, 50, 255]), {
      blendMode: 'multiply',
    });
    expect(destination).toEqual(new Uint8ClampedArray([31, 31, 24, 255]));
  });

  it('rejects malformed and differently sized surfaces', () => {
    expect(() => compositeSurface(new Uint8ClampedArray(3), new Uint8ClampedArray(3))).toThrow(
      'Destination surface length must be divisible by 4.',
    );
    expect(() => compositeSurface(new Uint8ClampedArray(4), new Uint8ClampedArray(8))).toThrow(
      'Source and destination surfaces must have the same length.',
    );
  });
});
