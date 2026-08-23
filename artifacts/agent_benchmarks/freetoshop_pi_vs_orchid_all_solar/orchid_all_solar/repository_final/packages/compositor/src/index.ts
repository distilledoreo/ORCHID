export const BLEND_MODES = [
  'normal',
  'multiply',
  'screen',
  'overlay',
  'darken',
  'darker-color',
  'lighten',
  'lighter-color',
  'color-dodge',
  'linear-dodge',
  'color-burn',
  'linear-burn',
  'hard-light',
  'soft-light',
  'vivid-light',
  'linear-light',
  'pin-light',
  'hard-mix',
  'difference',
  'exclusion',
  'subtract',
  'divide',
  'hue',
  'saturation',
  'color',
  'luminosity',
] as const;

export type BlendMode = (typeof BLEND_MODES)[number];

/** Straight-alpha, 8-bit RGBA. Out-of-range inputs are clamped at the API boundary. */
export type RgbaPixel = readonly [red: number, green: number, blue: number, alpha: number];

/** Linear numeric representation with premultiplied color and every component in [0, 1]. */
export type PremultipliedPixel = readonly [red: number, green: number, blue: number, alpha: number];
type Rgb = readonly [red: number, green: number, blue: number];

export interface CompositeOptions {
  readonly blendMode?: BlendMode;
  /** Scales the complete source result, including future effects. */
  readonly opacity?: number;
  /** Scales source content coverage independently from layer opacity. */
  readonly fillOpacity?: number;
}

const TRANSPARENT: PremultipliedPixel = [0, 0, 0, 0];

function clamp(value: number, minimum = 0, maximum = 1): number {
  if (Number.isNaN(value)) return minimum;
  return Math.min(maximum, Math.max(minimum, value));
}

function unitOpacity(value: number | undefined, field: string): number {
  const result = value ?? 1;
  if (!Number.isFinite(result) || result < 0 || result > 1) {
    throw new RangeError(`${field} must be a finite number between 0 and 1.`);
  }
  return result;
}

function byte(value: number): number {
  return Math.round(clamp(value, 0, 255));
}

function normalizedByte(value: number): number {
  return byte(value) / 255;
}

function normalizePremultiplied(pixel: PremultipliedPixel): PremultipliedPixel {
  const alpha = clamp(pixel[3]);
  if (alpha === 0) return TRANSPARENT;
  return [clamp(pixel[0], 0, alpha), clamp(pixel[1], 0, alpha), clamp(pixel[2], 0, alpha), alpha];
}

export function premultiplyPixel(pixel: RgbaPixel): PremultipliedPixel {
  const alpha = normalizedByte(pixel[3]);
  if (alpha === 0) return TRANSPARENT;
  return [
    normalizedByte(pixel[0]) * alpha,
    normalizedByte(pixel[1]) * alpha,
    normalizedByte(pixel[2]) * alpha,
    alpha,
  ];
}

export function unpremultiplyPixel(pixel: PremultipliedPixel): RgbaPixel {
  const normalized = normalizePremultiplied(pixel);
  const alpha = normalized[3];
  if (alpha === 0) return [0, 0, 0, 0];
  return [
    byte((normalized[0] / alpha) * 255),
    byte((normalized[1] / alpha) * 255),
    byte((normalized[2] / alpha) * 255),
    byte(alpha * 255),
  ];
}

/** Applies one separable blend-mode channel to normalized straight colors. */
export function blendChannel(mode: BlendMode, backdrop: number, source: number): number {
  const cb = clamp(backdrop);
  const cs = clamp(source);
  switch (mode) {
    case 'normal':
      return cs;
    case 'multiply':
      return cb * cs;
    case 'screen':
      return cb + cs - cb * cs;
    case 'overlay':
      return cb <= 0.5 ? 2 * cb * cs : 1 - 2 * (1 - cb) * (1 - cs);
    case 'darken':
      return Math.min(cb, cs);
    case 'darker-color':
    case 'lighter-color':
    case 'hue':
    case 'saturation':
    case 'color':
    case 'luminosity':
      throw new RangeError(`${mode} requires complete RGB colors.`);
    case 'lighten':
      return Math.max(cb, cs);
    case 'color-dodge':
      return cs >= 1 ? 1 : Math.min(1, cb / (1 - cs));
    case 'color-burn':
      return cs <= 0 ? 0 : 1 - Math.min(1, (1 - cb) / cs);
    case 'linear-dodge':
      return Math.min(1, cb + cs);
    case 'linear-burn':
      return Math.max(0, cb + cs - 1);
    case 'hard-light':
      return cs <= 0.5 ? 2 * cb * cs : 1 - 2 * (1 - cb) * (1 - cs);
    case 'soft-light': {
      if (cs <= 0.5) return cb - (1 - 2 * cs) * cb * (1 - cb);
      const curve = cb <= 0.25 ? ((16 * cb - 12) * cb + 4) * cb : Math.sqrt(cb);
      return cb + (2 * cs - 1) * (curve - cb);
    }
    case 'vivid-light':
      return cs <= 0.5
        ? blendChannel('color-burn', cb, 2 * cs)
        : blendChannel('color-dodge', cb, 2 * (cs - 0.5));
    case 'linear-light':
      return clamp(cb + 2 * cs - 1);
    case 'pin-light':
      return cs <= 0.5 ? Math.min(cb, 2 * cs) : Math.max(cb, 2 * (cs - 0.5));
    case 'hard-mix':
      return blendChannel('vivid-light', cb, cs) < 0.5 ? 0 : 1;
    case 'difference':
      return Math.abs(cb - cs);
    case 'exclusion':
      return cb + cs - 2 * cb * cs;
    case 'subtract':
      return Math.max(0, cb - cs);
    case 'divide':
      return cs <= 0 ? 1 : Math.min(1, cb / cs);
  }
}

function luminosity(color: Rgb): number {
  return 0.3 * color[0] + 0.59 * color[1] + 0.11 * color[2];
}

function saturation(color: Rgb): number {
  return Math.max(...color) - Math.min(...color);
}

function colorValue(color: Rgb): number {
  return color[0] + color[1] + color[2];
}

function clipColor(color: Rgb): Rgb {
  const lum = luminosity(color);
  const minimum = Math.min(...color);
  const maximum = Math.max(...color);
  let result: Rgb = color;
  if (minimum < 0) {
    result = result.map((channel) => lum + ((channel - lum) * lum) / (lum - minimum)) as [
      number,
      number,
      number,
    ];
  }
  if (maximum > 1) {
    result = result.map((channel) => lum + ((channel - lum) * (1 - lum)) / (maximum - lum)) as [
      number,
      number,
      number,
    ];
  }
  return result.map((channel) => clamp(channel)) as [number, number, number];
}

function setLuminosity(color: Rgb, target: number): Rgb {
  const difference = target - luminosity(color);
  return clipColor(color.map((channel) => channel + difference) as [number, number, number]);
}

function setSaturation(color: Rgb, target: number): Rgb {
  const indices = [0, 1, 2].sort((left, right) => color[left] - color[right]);
  const [minimumIndex, middleIndex, maximumIndex] = indices as [number, number, number];
  const minimum = color[minimumIndex];
  const maximum = color[maximumIndex];
  const result = [0, 0, 0];
  if (maximum > minimum) {
    result[middleIndex] = ((color[middleIndex] - minimum) * target) / (maximum - minimum);
    result[maximumIndex] = target;
  }
  return result as [number, number, number];
}

/** Applies separable and nonseparable blend modes to normalized straight RGB colors. */
export function blendRgb(mode: BlendMode, backdrop: Rgb, source: Rgb): Rgb {
  const cb = backdrop.map((channel) => clamp(channel)) as [number, number, number];
  const cs = source.map((channel) => clamp(channel)) as [number, number, number];
  switch (mode) {
    case 'darker-color':
      return colorValue(cs) < colorValue(cb) ? cs : cb;
    case 'lighter-color':
      return colorValue(cs) > colorValue(cb) ? cs : cb;
    case 'hue':
      return setLuminosity(setSaturation(cs, saturation(cb)), luminosity(cb));
    case 'saturation':
      return setLuminosity(setSaturation(cb, saturation(cs)), luminosity(cb));
    case 'color':
      return setLuminosity(cs, luminosity(cb));
    case 'luminosity':
      return setLuminosity(cb, luminosity(cs));
    default:
      return [
        blendChannel(mode, cb[0], cs[0]),
        blendChannel(mode, cb[1], cs[1]),
        blendChannel(mode, cb[2], cs[2]),
      ];
  }
}

/**
 * Source-over compositing in premultiplied-alpha space. Blend functions operate on the
 * unpremultiplied colors required by the W3C compositing model; the returned color remains
 * premultiplied and is safe to feed into another compositing operation without quantization.
 */
export function compositePremultipliedPixel(
  backdropPixel: PremultipliedPixel,
  sourcePixel: PremultipliedPixel,
  options: CompositeOptions = {},
): PremultipliedPixel {
  const backdrop = normalizePremultiplied(backdropPixel);
  const source = normalizePremultiplied(sourcePixel);
  const opacity = unitOpacity(options.opacity, 'Opacity');
  const fillOpacity = unitOpacity(options.fillOpacity, 'Fill opacity');
  const sourceAlpha = source[3] * opacity * fillOpacity;
  const backdropAlpha = backdrop[3];

  if (sourceAlpha === 0) return backdrop;

  const outputAlpha = sourceAlpha + backdropAlpha * (1 - sourceAlpha);
  const mode = options.blendMode ?? 'normal';
  const result = [0, 0, 0, outputAlpha] as [number, number, number, number];
  const sourceColor = source
    .slice(0, 3)
    .map((channel) => (source[3] === 0 ? 0 : channel / source[3])) as [number, number, number];
  const backdropColor = backdrop
    .slice(0, 3)
    .map((channel) => (backdropAlpha === 0 ? 0 : channel / backdropAlpha)) as [
    number,
    number,
    number,
  ];
  const blendedColor = blendRgb(mode, backdropColor, sourceColor);

  for (let channel = 0; channel < 3; channel += 1) {
    const sourceContribution =
      sourceAlpha *
      ((1 - backdropAlpha) * sourceColor[channel] + backdropAlpha * blendedColor[channel]);
    const backdropContribution = backdrop[channel] * (1 - sourceAlpha);
    result[channel] = clamp(sourceContribution + backdropContribution, 0, outputAlpha);
  }

  return result;
}

/** Exact single-pixel wrapper for straight 8-bit RGBA inputs and output. */
export function compositePixel(
  backdrop: RgbaPixel,
  source: RgbaPixel,
  options: CompositeOptions = {},
): RgbaPixel {
  return unpremultiplyPixel(
    compositePremultipliedPixel(premultiplyPixel(backdrop), premultiplyPixel(source), options),
  );
}

function assertSurface(buffer: ArrayLike<number>, field: string): void {
  if (buffer.length % 4 !== 0) {
    throw new RangeError(`${field} length must be divisible by 4.`);
  }
}

/** Returns a newly allocated straight-alpha RGBA surface. Inputs are never mutated. */
export function compositeSurface(
  backdrop: ArrayLike<number>,
  source: ArrayLike<number>,
  options: CompositeOptions = {},
): Uint8ClampedArray {
  const result = new Uint8ClampedArray(backdrop);
  compositeSurfaceInPlace(result, source, options);
  return result;
}

/** Composites a same-sized source surface into a mutable straight-alpha RGBA destination. */
export function compositeSurfaceInPlace(
  destination: Uint8ClampedArray,
  source: ArrayLike<number>,
  options: CompositeOptions = {},
): void {
  assertSurface(destination, 'Destination surface');
  assertSurface(source, 'Source surface');
  if (destination.length !== source.length) {
    throw new RangeError('Source and destination surfaces must have the same length.');
  }
  // Hoist option normalization once; avoid per-pixel temporary arrays.
  // Pixel math is equivalent to compositePixel(premultiply → blend → unpremultiply).
  const opacity = unitOpacity(options.opacity, 'Opacity');
  const fillOpacity = unitOpacity(options.fillOpacity, 'Fill opacity');
  const mode = options.blendMode ?? 'normal';
  const scale = opacity * fillOpacity;
  if (scale === 0) return;

  // Fast path: normal blend uses standard premultiplied "over" without blendRgb.
  if (mode === 'normal') {
    for (let offset = 0; offset < destination.length; offset += 4) {
      const sAbyte = source[offset + 3] ?? 0;
      if (sAbyte === 0) continue;
      const sA = (sAbyte / 255) * scale;
      if (sA === 0) continue;
      const sR = ((source[offset] ?? 0) / 255) * sA;
      const sG = ((source[offset + 1] ?? 0) / 255) * sA;
      const sB = ((source[offset + 2] ?? 0) / 255) * sA;
      // Fully opaque source replaces backdrop (after opacity/fill scale).
      if (sA >= 1) {
        const invS = 1 / sA;
        destination[offset] = byte(sR * invS * 255);
        destination[offset + 1] = byte(sG * invS * 255);
        destination[offset + 2] = byte(sB * invS * 255);
        destination[offset + 3] = byte(sA * 255);
        continue;
      }
      const dA = (destination[offset + 3] ?? 0) / 255;
      const dR = ((destination[offset] ?? 0) / 255) * dA;
      const dG = ((destination[offset + 1] ?? 0) / 255) * dA;
      const dB = ((destination[offset + 2] ?? 0) / 255) * dA;
      const inv = 1 - sA;
      const outA = sA + dA * inv;
      if (outA === 0) {
        destination[offset] = 0;
        destination[offset + 1] = 0;
        destination[offset + 2] = 0;
        destination[offset + 3] = 0;
        continue;
      }
      const outR = sR + dR * inv;
      const outG = sG + dG * inv;
      const outB = sB + dB * inv;
      const invA = 1 / outA;
      destination[offset] = byte(outR * invA * 255);
      destination[offset + 1] = byte(outG * invA * 255);
      destination[offset + 2] = byte(outB * invA * 255);
      destination[offset + 3] = byte(outA * 255);
    }
    return;
  }

  for (let offset = 0; offset < destination.length; offset += 4) {
    const sAbyte = source[offset + 3] ?? 0;
    if (sAbyte === 0) continue;

    const sRbyte = source[offset] ?? 0;
    const sGbyte = source[offset + 1] ?? 0;
    const sBbyte = source[offset + 2] ?? 0;
    const dRbyte = destination[offset] ?? 0;
    const dGbyte = destination[offset + 1] ?? 0;
    const dBbyte = destination[offset + 2] ?? 0;
    const dAbyte = destination[offset + 3] ?? 0;

    // Premultiply
    const sA = (sAbyte / 255) * scale;
    if (sA === 0) continue;
    const sR = (sRbyte / 255) * sA;
    const sG = (sGbyte / 255) * sA;
    const sB = (sBbyte / 255) * sA;
    const dA = dAbyte / 255;
    const dR = (dRbyte / 255) * dA;
    const dG = (dGbyte / 255) * dA;
    const dB = (dBbyte / 255) * dA;

    const outputAlpha = sA + dA * (1 - sA);
    const sourceColorR = sR / sA;
    const sourceColorG = sG / sA;
    const sourceColorB = sB / sA;
    const backdropColorR = dA === 0 ? 0 : dR / dA;
    const backdropColorG = dA === 0 ? 0 : dG / dA;
    const backdropColorB = dA === 0 ? 0 : dB / dA;

    // Inline common separable modes without allocating blendRgb tuples.
    let bR: number;
    let bG: number;
    let bB: number;
    if (mode === 'multiply') {
      bR = backdropColorR * sourceColorR;
      bG = backdropColorG * sourceColorG;
      bB = backdropColorB * sourceColorB;
    } else if (mode === 'screen') {
      bR = backdropColorR + sourceColorR - backdropColorR * sourceColorR;
      bG = backdropColorG + sourceColorG - backdropColorG * sourceColorG;
      bB = backdropColorB + sourceColorB - backdropColorB * sourceColorB;
    } else {
      const blended = blendRgb(
        mode,
        [backdropColorR, backdropColorG, backdropColorB],
        [sourceColorR, sourceColorG, sourceColorB],
      );
      bR = blended[0];
      bG = blended[1];
      bB = blended[2];
    }

    const outR = clamp(sA * ((1 - dA) * sourceColorR + dA * bR) + dR * (1 - sA), 0, outputAlpha);
    const outG = clamp(sA * ((1 - dA) * sourceColorG + dA * bG) + dG * (1 - sA), 0, outputAlpha);
    const outB = clamp(sA * ((1 - dA) * sourceColorB + dA * bB) + dB * (1 - sA), 0, outputAlpha);

    if (outputAlpha === 0) {
      destination[offset] = 0;
      destination[offset + 1] = 0;
      destination[offset + 2] = 0;
      destination[offset + 3] = 0;
    } else {
      const invA = 1 / outputAlpha;
      destination[offset] = byte(outR * invA * 255);
      destination[offset + 1] = byte(outG * invA * 255);
      destination[offset + 2] = byte(outB * invA * 255);
      destination[offset + 3] = byte(outputAlpha * 255);
    }
  }
}
