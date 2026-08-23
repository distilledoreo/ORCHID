import { createPixelLayer } from '@freetoshop/core-document';
import type {
  Adjustment,
  AdjustmentLayer,
  ImageDocument,
  Layer,
  LayerEffect,
  LayerTransform,
  PixelLayer,
  PathCommand,
  RgbaColor,
  RasterMask,
  SelectionMask,
  ShapeLayer,
  SmartObjectLayer,
  SmartFilter,
  TextLayer,
  VectorMask,
} from '@freetoshop/core-document';
import { compositeSurfaceInPlace } from '@freetoshop/compositor';
import { gaussianBlur } from '@freetoshop/filter-engine';
import {
  ContiguousCoverageSource,
  intersectRegions,
  type CoveragePatch,
  type CoverageSource,
  type PixelRegion,
  type PixelSource,
} from '@freetoshop/pixel-storage';
import { appendShapedTextPath, textEngine } from '@freetoshop/text-engine';
import { CompositeTileCache } from './compositeTileCache.js';

export interface ViewportTransform {
  zoom: number;
  panX: number;
  panY: number;
}

export interface Point {
  x: number;
  y: number;
}

export interface SelectionOutlineSegment {
  readonly x1: number;
  readonly y1: number;
  readonly x2: number;
  readonly y2: number;
}

export interface PixelSurface {
  readonly source: CanvasImageSource;
  putPixels(pixels: Uint8ClampedArray, width: number, height: number): void;
}

export type PixelSurfaceFactory = (width: number, height: number) => PixelSurface;

export type ChannelPreview =
  | { mode: 'composite' }
  | { mode: 'red' | 'green' | 'blue' }
  | { mode: 'alpha'; coverage: Uint8ClampedArray | CoverageSource };

export interface RenderOptions {
  viewport?: ViewportTransform;
  clear?: boolean;
  checkerboard?: CheckerboardOptions | false;
  createSurface?: PixelSurfaceFactory;
  preview?: ChannelPreview;
  rasterize?: LayerRasterizer;
  rasterizeRegion?: RegionLayerRasterizer;
  pixelSource?: PixelSourceResolver;
  /** Document-space regions to clear and redraw. Omit for a full frame. */
  dirtyRegions?: readonly PixelRegion[];
  /**
   * Optional dependency-keyed composite tile cache for the deterministic advanced
   * compositor path. Callers should retain one instance across frames for warm hits.
   */
  compositeTileCache?: CompositeTileCache;
  /**
   * When true with a composite tile cache, only viewport-visible (and dirty) tiles
   * are required for display. Off-screen tiles may stay unpopulated until a later
   * full compose. Default false preserves export-equivalent full surfaces.
   */
  progressiveComposite?: boolean;
  /**
   * Optional precomposed full-canvas RGBA from a worker or prior frame. When set
   * for the advanced composite path, skips main-thread recomposition.
   */
  precomposedPixels?: Uint8ClampedArray;
  /** Mip level of advanced-composite cache and precomposed pixels. Default 0. */
  compositeMipLevel?: number;
  /**
   * When true with a composite tile cache, miss tiles are not filled on the main
   * thread (warm hits only). Used while a worker cold composite is in flight.
   */
  warmAssembleOnly?: boolean;
  /**
   * Document guide/grid overlay. Default draws when the document has guides or a
   * grid. Pass false to suppress (export-style composites).
   */
  guidesOverlay?: GuidesOverlayOptions | false;
}

export interface SmartGuideOverlayLine {
  readonly axis: 'x' | 'y';
  readonly position: number;
}

export interface GuidesOverlayOptions {
  /** Draw ruler guides (default true when guides exist). */
  readonly showGuides?: boolean;
  /** Draw document grid (default true when grid exists). */
  readonly showGrid?: boolean;
  readonly guideColor?: string;
  readonly gridColor?: string;
  /** Temporary smart-guide strokes (active snap edges), document space. */
  readonly smartGuideLines?: readonly SmartGuideOverlayLine[];
  readonly smartGuideColor?: string;
}

export type PixelSourceResolver = (layer: PixelLayer) => PixelSource | undefined;

export type LayerRasterizer = (
  layer: { readonly width: number; readonly height: number },
  worldTransform: LayerTransform,
  canvas: { readonly width: number; readonly height: number },
  pixels: Uint8ClampedArray,
  region?: PixelRegion,
) => Uint8ClampedArray;

/** A rasterizer that must return exactly the requested document-space region. */
export type RegionLayerRasterizer = (
  layer: { readonly width: number; readonly height: number },
  worldTransform: LayerTransform,
  canvas: { readonly width: number; readonly height: number },
  pixels: Uint8ClampedArray,
  region: PixelRegion,
) => Uint8ClampedArray;

export interface CompositeDocumentOptions {
  readonly rasterize?: LayerRasterizer;
  /** Explicit bounded counterpart for custom rasterizers. Omit to retain exact full-frame fallback. */
  readonly rasterizeRegion?: RegionLayerRasterizer;
  readonly preview?: ChannelPreview;
  readonly pixelSource?: PixelSourceResolver;
}

export interface CheckerboardOptions {
  size?: number;
  light?: string;
  dark?: string;
}

type AffineMatrix = LayerTransform;

const IDENTITY_MATRIX: Readonly<AffineMatrix> = {
  a: 1,
  b: 0,
  c: 0,
  d: 1,
  e: 0,
  f: 0,
};

export const DEFAULT_VIEWPORT: Readonly<ViewportTransform> = {
  zoom: 1,
  panX: 0,
  panY: 0,
};

export function clampZoom(zoom: number, minimum = 0.05, maximum = 64): number {
  if (!Number.isFinite(zoom)) return 1;
  return Math.min(maximum, Math.max(minimum, zoom));
}

export function worldToScreen(point: Point, viewport: ViewportTransform): Point {
  const zoom = clampZoom(viewport.zoom);
  return { x: point.x * zoom + viewport.panX, y: point.y * zoom + viewport.panY };
}

export function screenToWorld(point: Point, viewport: ViewportTransform): Point {
  const zoom = clampZoom(viewport.zoom);
  return { x: (point.x - viewport.panX) / zoom, y: (point.y - viewport.panY) / zoom };
}

export function zoomAtPoint(
  viewport: ViewportTransform,
  screenPoint: Point,
  nextZoom: number,
): ViewportTransform {
  const world = screenToWorld(screenPoint, viewport);
  const zoom = clampZoom(nextZoom);
  return {
    zoom,
    panX: screenPoint.x - world.x * zoom,
    panY: screenPoint.y - world.y * zoom,
  };
}

export function drawCheckerboard(
  context: CanvasRenderingContext2D,
  width: number,
  height: number,
  options: CheckerboardOptions = {},
): void {
  const size = Math.max(1, Math.floor(options.size ?? 12));
  context.save();
  context.fillStyle = options.light ?? '#ffffff';
  context.fillRect(0, 0, width, height);
  context.fillStyle = options.dark ?? '#d8d8d8';
  for (let y = 0; y < height; y += size) {
    for (let x = (Math.floor(y / size) % 2) * size; x < width; x += size * 2) {
      context.fillRect(x, y, size, size);
    }
  }
  context.restore();
}

export function createBrowserPixelSurface(width: number, height: number): PixelSurface {
  const canvas =
    typeof OffscreenCanvas !== 'undefined'
      ? new OffscreenCanvas(width, height)
      : Object.assign(document.createElement('canvas'), { width, height });
  const context = canvas.getContext('2d');
  if (!context) throw new Error('Canvas 2D is not available.');
  return {
    source: canvas,
    putPixels(pixels, pixelWidth, pixelHeight) {
      assertPixelBuffer(pixels, pixelWidth, pixelHeight);
      context.putImageData(new ImageData(pixels, pixelWidth, pixelHeight), 0, 0);
    },
  };
}

/** Produces a transparent black-and-white marching-ants boundary at 50% coverage. */
export function selectionOutlinePixels(selection: SelectionMask, phase = 0): Uint8ClampedArray {
  // Explicit dense materialization for the full-canvas RGBA outline compatibility API.
  const coverage = selection.coverageStore.toContiguous();
  assertCoverageBuffer(coverage, selection.width, selection.height);
  if (!Number.isSafeInteger(phase))
    throw new RangeError('Selection outline phase must be an integer.');
  const output = new Uint8ClampedArray(selection.width * selection.height * 4);
  const selected = (x: number, y: number) =>
    x >= 0 &&
    y >= 0 &&
    x < selection.width &&
    y < selection.height &&
    (coverage[y * selection.width + x] ?? 0) >= 128;
  for (let y = 0; y < selection.height; y += 1) {
    for (let x = 0; x < selection.width; x += 1) {
      if (!selected(x, y)) continue;
      if (selected(x - 1, y) && selected(x + 1, y) && selected(x, y - 1) && selected(x, y + 1)) {
        continue;
      }
      const value = (((x + y + phase) % 8) + 8) % 8 < 4 ? 255 : 16;
      const offset = (y * selection.width + x) * 4;
      output[offset] = value;
      output[offset + 1] = value;
      output[offset + 2] = value;
      output[offset + 3] = 255;
    }
  }
  return output;
}

/** Extracts exact pixel-edge segments for resolution-independent selection rendering. */
export function* selectionOutlineSegments(
  selection: SelectionMask,
): Generator<SelectionOutlineSegment> {
  if (
    selection.coverageStore.width !== selection.width ||
    selection.coverageStore.height !== selection.height
  ) {
    throw new RangeError('Selection coverage store must match its dimensions.');
  }
  const store = selection.coverageStore;
  const hasImplicitSelectedBackground = store.defaultCoverage >= 128;
  const outlineChunks: CoveragePatch[] = [];
  if (!hasImplicitSelectedBackground) {
    outlineChunks.push(...store.coverageChunks());
  } else {
    const keys = new Set<number>();
    for (const tile of store.tiles()) {
      for (const [column, row] of [
        [tile.column, tile.row],
        [tile.column - 1, tile.row],
        [tile.column + 1, tile.row],
        [tile.column, tile.row - 1],
        [tile.column, tile.row + 1],
      ]) {
        if (column < 0 || row < 0 || column >= store.columnCount || row >= store.rowCount) continue;
        keys.add(column + row * store.columnCount);
      }
    }
    for (const key of [...keys].sort((left, right) => left - right)) {
      const column = key % store.columnCount;
      const row = Math.floor(key / store.columnCount);
      const x = column * store.tileSize;
      const y = row * store.tileSize;
      const region = {
        x,
        y,
        width: Math.min(store.tileSize, store.width - x),
        height: Math.min(store.tileSize, store.height - y),
      };
      outlineChunks.push({ ...region, coverage: store.read(region) });
    }

    const top = store.read({ x: 0, y: 0, width: selection.width, height: 1 });
    const bottom = store.read({
      x: 0,
      y: selection.height - 1,
      width: selection.width,
      height: 1,
    });
    for (let x = 0; x < selection.width; x += 1) {
      if ((top[x] ?? 0) >= 128) yield { x1: x, y1: 0, x2: x + 1, y2: 0 };
      if ((bottom[x] ?? 0) >= 128) {
        yield { x1: x + 1, y1: selection.height, x2: x, y2: selection.height };
      }
    }
    const left = store.read({ x: 0, y: 0, width: 1, height: selection.height });
    const right = store.read({
      x: selection.width - 1,
      y: 0,
      width: 1,
      height: selection.height,
    });
    for (let y = 0; y < selection.height; y += 1) {
      if ((left[y] ?? 0) >= 128) yield { x1: 0, y1: y + 1, x2: 0, y2: y };
      if ((right[y] ?? 0) >= 128) {
        yield { x1: selection.width, y1: y, x2: selection.width, y2: y + 1 };
      }
    }
  }
  for (const chunk of outlineChunks) {
    const halo = {
      x: chunk.x - 1,
      y: chunk.y - 1,
      width: chunk.width + 2,
      height: chunk.height + 2,
    };
    const neighborhood = selection.coverageStore.read(halo);
    const selected = (x: number, y: number) =>
      (neighborhood[(y - halo.y) * halo.width + x - halo.x] ?? 0) >= 128;
    for (let localY = 0; localY < chunk.height; localY += 1) {
      for (let localX = 0; localX < chunk.width; localX += 1) {
        if ((chunk.coverage[localY * chunk.width + localX] ?? 0) < 128) continue;
        const x = chunk.x + localX;
        const y = chunk.y + localY;
        if ((!hasImplicitSelectedBackground || y > 0) && !selected(x, y - 1)) {
          yield { x1: x, y1: y, x2: x + 1, y2: y };
        }
        if ((!hasImplicitSelectedBackground || x + 1 < selection.width) && !selected(x + 1, y)) {
          yield { x1: x + 1, y1: y, x2: x + 1, y2: y + 1 };
        }
        if ((!hasImplicitSelectedBackground || y + 1 < selection.height) && !selected(x, y + 1)) {
          yield { x1: x + 1, y1: y + 1, x2: x, y2: y + 1 };
        }
        if ((!hasImplicitSelectedBackground || x > 0) && !selected(x - 1, y)) {
          yield { x1: x, y1: y + 1, x2: x, y2: y };
        }
      }
    }
  }
}

function browserLayerRasterizer(
  layer: { readonly width: number; readonly height: number },
  transform: LayerTransform,
  canvasSize: { readonly width: number; readonly height: number },
  pixels: Uint8ClampedArray,
  region?: PixelRegion,
): Uint8ClampedArray {
  const source = createBrowserPixelSurface(layer.width, layer.height);
  source.putPixels(pixels, layer.width, layer.height);
  const outputWidth = region?.width ?? canvasSize.width;
  const outputHeight = region?.height ?? canvasSize.height;
  const canvas =
    typeof OffscreenCanvas !== 'undefined'
      ? new OffscreenCanvas(outputWidth, outputHeight)
      : Object.assign(globalThis.document.createElement('canvas'), {
          width: outputWidth,
          height: outputHeight,
        });
  const context = canvas.getContext('2d', { willReadFrequently: true }) as
    | CanvasRenderingContext2D
    | OffscreenCanvasRenderingContext2D
    | null;
  if (!context) throw new Error('Canvas 2D is not available.');
  context.setTransform(
    transform.a,
    transform.b,
    transform.c,
    transform.d,
    transform.e - (region?.x ?? 0),
    transform.f - (region?.y ?? 0),
  );
  context.drawImage(source.source, 0, 0);
  context.setTransform(1, 0, 0, 1, 0, 0);
  return context.getImageData(0, 0, outputWidth, outputHeight).data;
}

/** Deterministic nearest-neighbor rasterizer used by tests and non-browser CPU fallbacks. */
export function rasterizeLayerNearest(
  layer: { readonly width: number; readonly height: number },
  transform: LayerTransform,
  canvas: { readonly width: number; readonly height: number },
  pixels: Uint8ClampedArray,
  region?: PixelRegion,
): Uint8ClampedArray {
  const determinant = transform.a * transform.d - transform.b * transform.c;
  if (Math.abs(determinant) <= 1e-12) throw new RangeError('Layer transform must be invertible.');
  const originX = region?.x ?? 0;
  const originY = region?.y ?? 0;
  const outWidth = region?.width ?? canvas.width;
  const outHeight = region?.height ?? canvas.height;
  const output = new Uint8ClampedArray(outWidth * outHeight * 4);
  for (let y = 0; y < outHeight; y += 1) {
    for (let x = 0; x < outWidth; x += 1) {
      const documentX = originX + x;
      const documentY = originY + y;
      const translatedX = documentX + 0.5 - transform.e;
      const translatedY = documentY + 0.5 - transform.f;
      const localX = (transform.d * translatedX - transform.c * translatedY) / determinant;
      const localY = (-transform.b * translatedX + transform.a * translatedY) / determinant;
      const sourceX = Math.floor(localX);
      const sourceY = Math.floor(localY);
      if (sourceX < 0 || sourceY < 0 || sourceX >= layer.width || sourceY >= layer.height) continue;
      const sourceOffset = (sourceY * layer.width + sourceX) * 4;
      const targetOffset = (y * outWidth + x) * 4;
      output[targetOffset] = pixels[sourceOffset] ?? 0;
      output[targetOffset + 1] = pixels[sourceOffset + 1] ?? 0;
      output[targetOffset + 2] = pixels[sourceOffset + 2] ?? 0;
      output[targetOffset + 3] = pixels[sourceOffset + 3] ?? 0;
    }
  }
  return output;
}

function clippedSource(source: Uint8ClampedArray, baseAlpha: Uint8ClampedArray): Uint8ClampedArray {
  const result = new Uint8ClampedArray(source);
  for (let offset = 0, pixel = 0; offset < result.length; offset += 4, pixel += 1) {
    result[offset + 3] = Math.round(((result[offset + 3] ?? 0) * (baseAlpha[pixel] ?? 0)) / 255);
  }
  return result;
}

function alphaChannel(pixels: Uint8ClampedArray): Uint8ClampedArray {
  const alpha = new Uint8ClampedArray(pixels.length / 4);
  for (let offset = 3, pixel = 0; offset < pixels.length; offset += 4, pixel += 1) {
    alpha[pixel] = pixels[offset] ?? 0;
  }
  return alpha;
}

function rgbToHsl(red: number, green: number, blue: number): [number, number, number] {
  const maximum = Math.max(red, green, blue);
  const minimum = Math.min(red, green, blue);
  const lightness = (maximum + minimum) / 2;
  if (maximum === minimum) return [0, 0, lightness];
  const delta = maximum - minimum;
  const saturation =
    lightness > 0.5 ? delta / (2 - maximum - minimum) : delta / (maximum + minimum);
  const hue =
    maximum === red
      ? ((green - blue) / delta + (green < blue ? 6 : 0)) / 6
      : maximum === green
        ? ((blue - red) / delta + 2) / 6
        : ((red - green) / delta + 4) / 6;
  return [hue, saturation, lightness];
}

function hueToRgb(p: number, q: number, hue: number): number {
  const wrapped = ((hue % 1) + 1) % 1;
  if (wrapped < 1 / 6) return p + (q - p) * 6 * wrapped;
  if (wrapped < 1 / 2) return q;
  if (wrapped < 2 / 3) return p + (q - p) * (2 / 3 - wrapped) * 6;
  return p;
}

function hslToRgb(hue: number, saturation: number, lightness: number): [number, number, number] {
  if (saturation === 0) return [lightness, lightness, lightness];
  const q =
    lightness < 0.5
      ? lightness * (1 + saturation)
      : lightness + saturation - lightness * saturation;
  const p = 2 * lightness - q;
  return [hueToRgb(p, q, hue + 1 / 3), hueToRgb(p, q, hue), hueToRgb(p, q, hue - 1 / 3)];
}

function adjustedRgb(
  adjustment: Adjustment,
  red: number,
  green: number,
  blue: number,
): [number, number, number] {
  switch (adjustment.type) {
    case 'invert':
      return [255 - red, 255 - green, 255 - blue];
    case 'brightness-contrast': {
      const factor = (259 * (adjustment.contrast + 255)) / (255 * (259 - adjustment.contrast));
      return [red, green, blue].map((value) =>
        Math.min(255, Math.max(0, factor * (value - 128) + 128 + adjustment.brightness)),
      ) as [number, number, number];
    }
    case 'levels': {
      const range = adjustment.inputWhite - adjustment.inputBlack;
      return [red, green, blue].map((value) => {
        const normalized = Math.min(1, Math.max(0, (value - adjustment.inputBlack) / range));
        return (
          adjustment.outputBlack +
          (adjustment.outputWhite - adjustment.outputBlack) * normalized ** (1 / adjustment.gamma)
        );
      }) as [number, number, number];
    }
    case 'hue-saturation': {
      const [hue, saturation, lightness] = rgbToHsl(red / 255, green / 255, blue / 255);
      const rgb = hslToRgb(
        hue + adjustment.hue / 360,
        Math.min(1, Math.max(0, saturation + adjustment.saturation / 100)),
        Math.min(1, Math.max(0, lightness + adjustment.lightness / 100)),
      );
      return rgb.map((value) => value * 255) as [number, number, number];
    }
    case 'posterize': {
      const levels = adjustment.levels;
      const step = 255 / (levels - 1);
      const quantize = (value: number) => Math.round(Math.round(value / step) * step);
      return [quantize(red), quantize(green), quantize(blue)];
    }
    case 'threshold': {
      const luminance = 0.2126 * red + 0.7152 * green + 0.0722 * blue;
      const tone = luminance >= adjustment.level ? 255 : 0;
      return [tone, tone, tone];
    }
  }
}

function applyAdjustment(
  destination: Uint8ClampedArray,
  layer: AdjustmentLayer,
  clipBase: Uint8ClampedArray | null,
): void {
  const mask = layer.rasterMask;
  const rasterCoverage = mask?.coverageStore.toContiguous() ?? null;
  const vectorCoverage = vectorMaskCoverage(layer.vectorMask);
  for (let offset = 0, pixel = 0; offset < destination.length; offset += 4, pixel += 1) {
    const alpha = destination[offset + 3] ?? 0;
    if (alpha === 0) continue;
    const maskCoverage =
      mask === null || !mask.enabled
        ? 1
        : 1 - mask.density + ((rasterCoverage?.[pixel] ?? 0) / 255) * mask.density;
    const vectorMaskCoverage =
      vectorCoverage === null || layer.vectorMask === null
        ? 1
        : 1 -
          layer.vectorMask.density +
          ((vectorCoverage[pixel] ?? 0) / 255) * layer.vectorMask.density;
    const clipping = layer.clipping ? (clipBase?.[pixel] ?? 0) / 255 : 1;
    const strength =
      layer.opacity * layer.fillOpacity * maskCoverage * vectorMaskCoverage * clipping;
    if (strength <= 0) continue;
    const original: [number, number, number] = [
      destination[offset] ?? 0,
      destination[offset + 1] ?? 0,
      destination[offset + 2] ?? 0,
    ];
    const adjusted = adjustedRgb(layer.adjustment, ...original);
    for (let channel = 0; channel < 3; channel += 1) {
      destination[offset + channel] = Math.round(
        (original[channel] ?? 0) * (1 - strength) + (adjusted[channel] ?? 0) * strength,
      );
    }
  }
}

function cssColor(color: RgbaColor): string {
  return `rgba(${String(color.red)}, ${String(color.green)}, ${String(color.blue)}, ${String(color.alpha / 255)})`;
}

function applyLocalMasks(
  pixels: Uint8ClampedArray,
  rasterMask: PixelLayer['rasterMask'],
  vectorMask: VectorMask | null,
): Uint8ClampedArray {
  const vectorCoverage = vectorMaskCoverage(vectorMask);
  if (
    (rasterMask === null || !rasterMask.enabled || rasterMask.density === 0) &&
    vectorCoverage === null
  ) {
    return pixels;
  }
  const result = new Uint8ClampedArray(pixels);
  const rasterCoverage = rasterMask?.coverageStore.toContiguous() ?? null;
  for (let index = 0; index < pixels.length / 4; index += 1) {
    const rasterFactor =
      rasterMask === null || !rasterMask.enabled
        ? 1
        : 1 - rasterMask.density + ((rasterCoverage?.[index] ?? 0) / 255) * rasterMask.density;
    const vectorFactor =
      vectorCoverage === null || vectorMask === null
        ? 1
        : 1 - vectorMask.density + ((vectorCoverage[index] ?? 0) / 255) * vectorMask.density;
    const alpha = index * 4 + 3;
    result[alpha] = Math.round((result[alpha] ?? 0) * rasterFactor * vectorFactor);
  }
  return result;
}

function applyRasterMaskRegion(
  pixels: Uint8ClampedArray,
  rasterMask: RasterMask | null,
  region: PixelRegion,
): Uint8ClampedArray {
  if (rasterMask === null || !rasterMask.enabled || rasterMask.density === 0) return pixels;
  const coverage = rasterMask.coverageStore.read(region);
  assertCoverageBuffer(coverage, region.width, region.height);
  const result = new Uint8ClampedArray(pixels);
  for (let index = 0; index < coverage.length; index += 1) {
    const factor = 1 - rasterMask.density + ((coverage[index] ?? 0) / 255) * rasterMask.density;
    const alpha = index * 4 + 3;
    result[alpha] = Math.round((result[alpha] ?? 0) * factor);
  }
  return result;
}

function applyMasksRegion(
  pixels: Uint8ClampedArray,
  rasterMask: RasterMask | null,
  vectorMask: VectorMask | null,
  region: PixelRegion,
): Uint8ClampedArray {
  const rasterCoverage =
    rasterMask !== null && rasterMask.enabled && rasterMask.density > 0
      ? rasterMask.coverageStore.read(region)
      : null;
  const vectorCoverage = vectorMaskCoverageRegion(vectorMask, region);
  if (rasterCoverage === null && vectorCoverage === null) return pixels;
  if (rasterCoverage !== null) assertCoverageBuffer(rasterCoverage, region.width, region.height);
  const result = new Uint8ClampedArray(pixels);
  for (let index = 0; index < pixels.length / 4; index += 1) {
    const rasterFactor =
      rasterCoverage === null || rasterMask === null
        ? 1
        : 1 - rasterMask.density + ((rasterCoverage[index] ?? 0) / 255) * rasterMask.density;
    const vectorFactor =
      vectorCoverage === null || vectorMask === null
        ? 1
        : 1 - vectorMask.density + ((vectorCoverage[index] ?? 0) / 255) * vectorMask.density;
    const alpha = index * 4 + 3;
    result[alpha] = Math.round((result[alpha] ?? 0) * rasterFactor * vectorFactor);
  }
  return result;
}

function cpuShapePixels(layer: ShapeLayer): Uint8ClampedArray {
  return cpuShapePixelsRegion(layer, {
    x: 0,
    y: 0,
    width: layer.geometry.width,
    height: layer.geometry.height,
  });
}

function cpuShapePixelsRegion(layer: ShapeLayer, region: PixelRegion): Uint8ClampedArray {
  const { width, height, type } = layer.geometry;
  if (type === 'path') return cpuPathPixelsRegion(layer, region);
  const output = new Uint8ClampedArray(region.width * region.height * 4);
  const strokeWidth = layer.style.strokeWidth;
  for (let localY = 0; localY < region.height; localY += 1) {
    const y = region.y + localY;
    for (let localX = 0; localX < region.width; localX += 1) {
      const x = region.x + localX;
      const normalizedX = (x + 0.5 - width / 2) / (width / 2);
      const normalizedY = (y + 0.5 - height / 2) / (height / 2);
      const inside =
        type === 'ellipse' ? normalizedX * normalizedX + normalizedY * normalizedY <= 1 : true;
      if (!inside) continue;
      const inset = Math.max(0, strokeWidth);
      const inner =
        type === 'ellipse'
          ? inset * 2 < width &&
            inset * 2 < height &&
            ((x + 0.5 - width / 2) / (width / 2 - inset)) ** 2 +
              ((y + 0.5 - height / 2) / (height / 2 - inset)) ** 2 <=
              1
          : x + 0.5 >= inset &&
            y + 0.5 >= inset &&
            x + 0.5 <= width - inset &&
            y + 0.5 <= height - inset;
      const color = !inner && layer.style.stroke ? layer.style.stroke : layer.style.fill;
      if (color === null) continue;
      const offset = (localY * region.width + localX) * 4;
      output[offset] = color.red;
      output[offset + 1] = color.green;
      output[offset + 2] = color.blue;
      output[offset + 3] = color.alpha;
    }
  }
  return applyMasksRegion(output, layer.rasterMask, layer.vectorMask, region);
}

interface FlattenedSubpath {
  readonly points: readonly Point[];
  readonly closed: boolean;
}

function flattenedPath(commands: readonly PathCommand[]): readonly FlattenedSubpath[] {
  const result: FlattenedSubpath[] = [];
  let points: Point[] = [];
  let current: Point | null = null;
  const finish = (closed: boolean) => {
    if (points.length > 0) result.push({ points, closed });
    points = [];
    current = null;
  };
  for (const command of commands) {
    if (command.type === 'move') {
      finish(false);
      current = { x: command.x, y: command.y };
      points.push(current);
    } else if (command.type === 'close') {
      finish(true);
    } else if (command.type === 'line') {
      current = { x: command.x, y: command.y };
      points.push(current);
    } else if (current) {
      const start = current;
      for (let step = 1; step <= 16; step += 1) {
        const t = step / 16;
        const inverse = 1 - t;
        points.push({
          x:
            inverse ** 3 * start.x +
            3 * inverse ** 2 * t * command.control1X +
            3 * inverse * t ** 2 * command.control2X +
            t ** 3 * command.x,
          y:
            inverse ** 3 * start.y +
            3 * inverse ** 2 * t * command.control1Y +
            3 * inverse * t ** 2 * command.control2Y +
            t ** 3 * command.y,
        });
      }
      current = { x: command.x, y: command.y };
    }
  }
  finish(false);
  return result;
}

function pointInPath(point: Point, subpaths: readonly FlattenedSubpath[]): boolean {
  let inside = false;
  for (const subpath of subpaths) {
    if (subpath.points.length < 3) continue;
    for (let current = 0, previous = subpath.points.length - 1; current < subpath.points.length; ) {
      const from = subpath.points[previous];
      const to = subpath.points[current];
      if (
        from.y > point.y !== to.y > point.y &&
        point.x < ((to.x - from.x) * (point.y - from.y)) / (to.y - from.y) + from.x
      ) {
        inside = !inside;
      }
      previous = current;
      current += 1;
    }
  }
  return inside;
}

function vectorMaskCoverage(mask: VectorMask | null): Uint8ClampedArray | null {
  if (mask === null || !mask.enabled || mask.density === 0) return null;
  const subpaths = flattenedPath(mask.commands);
  const segmentCount = subpaths.reduce((sum, subpath) => sum + subpath.points.length, 0);
  if (mask.width * mask.height * Math.max(1, segmentCount) > 100_000_000) {
    throw new RangeError('Vector mask rasterization complexity exceeds the CPU safety limit.');
  }
  const coverage = new Uint8ClampedArray(mask.width * mask.height);
  for (let y = 0; y < mask.height; y += 1) {
    for (let x = 0; x < mask.width; x += 1) {
      const inside = pointInPath({ x: x + 0.5, y: y + 0.5 }, subpaths);
      coverage[y * mask.width + x] = inside !== mask.inverted ? 255 : 0;
    }
  }
  return coverage;
}

function vectorMaskCoverageRegion(
  mask: VectorMask | null,
  region: PixelRegion,
): Uint8ClampedArray | null {
  if (mask === null || !mask.enabled || mask.density === 0) return null;
  const subpaths = flattenedPath(mask.commands);
  const segmentCount = subpaths.reduce((sum, subpath) => sum + subpath.points.length, 0);
  if (region.width * region.height * Math.max(1, segmentCount) > 100_000_000) {
    throw new RangeError(
      'Vector mask region rasterization complexity exceeds the CPU safety limit.',
    );
  }
  const coverage = new Uint8ClampedArray(region.width * region.height);
  for (let y = 0; y < region.height; y += 1) {
    for (let x = 0; x < region.width; x += 1) {
      const inside = pointInPath({ x: region.x + x + 0.5, y: region.y + y + 0.5 }, subpaths);
      coverage[y * region.width + x] = inside !== mask.inverted ? 255 : 0;
    }
  }
  return coverage;
}

function distanceToSegment(point: Point, from: Point, to: Point): number {
  const dx = to.x - from.x;
  const dy = to.y - from.y;
  const lengthSquared = dx * dx + dy * dy;
  if (lengthSquared === 0) return Math.hypot(point.x - from.x, point.y - from.y);
  const projection = Math.max(
    0,
    Math.min(1, ((point.x - from.x) * dx + (point.y - from.y) * dy) / lengthSquared),
  );
  return Math.hypot(point.x - (from.x + projection * dx), point.y - (from.y + projection * dy));
}

function pointOnPathStroke(
  point: Point,
  subpaths: readonly FlattenedSubpath[],
  radius: number,
): boolean {
  for (const subpath of subpaths) {
    for (let index = 1; index < subpath.points.length; index += 1) {
      const from = subpath.points[index - 1];
      const to = subpath.points[index];
      if (distanceToSegment(point, from, to) <= radius) return true;
    }
    const first = subpath.points[0] ?? { x: 0, y: 0 };
    const last = subpath.points.at(-1) ?? first;
    if (subpath.closed && distanceToSegment(point, last, first) <= radius) {
      return true;
    }
  }
  return false;
}

function cpuPathPixelsRegion(layer: ShapeLayer, region: PixelRegion): Uint8ClampedArray {
  const { commands } = layer.geometry;
  const subpaths = flattenedPath(commands);
  const segmentCount = subpaths.reduce((sum, subpath) => sum + subpath.points.length, 0);
  if (region.width * region.height * Math.max(1, segmentCount) > 100_000_000) {
    throw new RangeError('Path rasterization complexity exceeds the CPU safety limit.');
  }
  const output = new Uint8ClampedArray(region.width * region.height * 4);
  for (let localY = 0; localY < region.height; localY += 1) {
    const y = region.y + localY;
    for (let localX = 0; localX < region.width; localX += 1) {
      const x = region.x + localX;
      const point = { x: x + 0.5, y: y + 0.5 };
      const onStroke =
        layer.style.stroke !== null &&
        layer.style.strokeWidth > 0 &&
        pointOnPathStroke(point, subpaths, layer.style.strokeWidth / 2);
      const color = onStroke
        ? layer.style.stroke
        : layer.style.fill !== null && pointInPath(point, subpaths)
          ? layer.style.fill
          : null;
      if (color === null) continue;
      const offset = (localY * region.width + localX) * 4;
      output[offset] = color.red;
      output[offset + 1] = color.green;
      output[offset + 2] = color.blue;
      output[offset + 3] = color.alpha;
    }
  }
  return applyMasksRegion(output, layer.rasterMask, layer.vectorMask, region);
}

function applySmartFilters(
  pixels: Uint8ClampedArray,
  width: number,
  height: number,
  filters: readonly SmartFilter[],
): Uint8ClampedArray {
  let current = new Uint8ClampedArray(pixels);
  for (const filter of filters) {
    if (!filter.enabled || filter.opacity <= 0) continue;
    const filtered = new Uint8ClampedArray(current);
    if (filter.type === 'gaussian-blur' && filter.radius > 0) {
      const patch = gaussianBlur(
        createPixelLayer({
          id: 'smart-filter',
          name: 'Smart filter',
          width,
          height,
          pixels: current,
        }),
        filter.radius,
      );
      const patchPixels = new Uint8ClampedArray(patch.pixels);
      for (let row = 0; row < patch.height; row += 1) {
        const target = ((patch.y + row) * width + patch.x) * 4;
        const source = row * patch.width * 4;
        filtered.set(patchPixels.subarray(source, source + patch.width * 4), target);
      }
    } else if (filter.type !== 'gaussian-blur') {
      for (let offset = 0; offset < filtered.length; offset += 4) {
        const red = current[offset] ?? 0;
        const green = current[offset + 1] ?? 0;
        const blue = current[offset + 2] ?? 0;
        if (filter.type === 'invert') {
          filtered[offset] = 255 - red;
          filtered[offset + 1] = 255 - green;
          filtered[offset + 2] = 255 - blue;
        } else if (filter.type === 'grayscale') {
          const value = Math.round(red * 0.2126 + green * 0.7152 + blue * 0.0722);
          filtered[offset] = value;
          filtered[offset + 1] = value;
          filtered[offset + 2] = value;
        } else {
          const adjusted = adjustedRgb(filter, red, green, blue);
          filtered[offset] = adjusted[0];
          filtered[offset + 1] = adjusted[1];
          filtered[offset + 2] = adjusted[2];
        }
      }
    }
    if (filter.opacity >= 1) {
      current = filtered;
      continue;
    }
    for (let offset = 0; offset < current.length; offset += 1) {
      current[offset] = Math.round(
        (current[offset] ?? 0) * (1 - filter.opacity) + (filtered[offset] ?? 0) * filter.opacity,
      );
    }
  }
  return current;
}

function drawShapeLayerPath(
  context: CanvasRenderingContext2D | OffscreenCanvasRenderingContext2D,
  layer: ShapeLayer,
): void {
  context.beginPath();
  if (layer.geometry.type === 'ellipse') {
    context.ellipse(
      layer.geometry.width / 2,
      layer.geometry.height / 2,
      layer.geometry.width / 2,
      layer.geometry.height / 2,
      0,
      0,
      Math.PI * 2,
    );
  } else if (layer.geometry.type === 'rectangle') {
    context.roundRect(
      0,
      0,
      layer.geometry.width,
      layer.geometry.height,
      layer.geometry.cornerRadius,
    );
  } else {
    for (const command of layer.geometry.commands) {
      if (command.type === 'move') context.moveTo(command.x, command.y);
      else if (command.type === 'line') context.lineTo(command.x, command.y);
      else if (command.type === 'cubic')
        context.bezierCurveTo(
          command.control1X,
          command.control1Y,
          command.control2X,
          command.control2Y,
          command.x,
          command.y,
        );
      else context.closePath();
    }
  }
  if (layer.style.fill !== null) {
    context.fillStyle = cssColor(layer.style.fill);
    context.fill(layer.geometry.type === 'path' ? 'evenodd' : 'nonzero');
  }
  if (layer.style.stroke !== null && layer.style.strokeWidth > 0) {
    context.strokeStyle = cssColor(layer.style.stroke);
    context.lineWidth = layer.style.strokeWidth;
    context.stroke();
  }
}

function drawTextLayer(
  context: CanvasRenderingContext2D | OffscreenCanvasRenderingContext2D,
  layer: TextLayer,
): void {
  const { width, height } = layer;
  context.fillStyle = cssColor(layer.style.color);
  if (textEngine.hasFont(layer.style.fontFamily)) {
    const layout = textEngine.layoutText(layer.text, {
      family: layer.style.fontFamily,
      fontSize: layer.style.fontSize,
      lineHeight: layer.style.lineHeight,
      width,
      height,
      direction: 'auto',
      features: ['kern=1', 'liga=1', 'clig=1', 'calt=1'],
    });
    context.beginPath();
    appendShapedTextPath(context, layout, { align: layer.style.align });
    context.fill();
    return;
  }
  context.font = `${layer.style.italic ? 'italic ' : ''}${String(layer.style.fontWeight)} ${String(layer.style.fontSize)}px ${layer.style.fontFamily}`;
  context.textBaseline = 'top';
  context.textAlign = layer.style.align;
  const x = layer.style.align === 'left' ? 0 : layer.style.align === 'center' ? width / 2 : width;
  const lineHeight = layer.style.fontSize * layer.style.lineHeight;
  let y = 0;
  for (const paragraph of layer.text.split('\n')) {
    const words = paragraph.split(/\s+/).filter(Boolean);
    let line = '';
    for (const word of words.length > 0 ? words : ['']) {
      const candidate = line ? `${line} ${word}` : word;
      if (line && context.measureText(candidate).width > width) {
        context.fillText(line, x, y, width);
        y += lineHeight;
        line = word;
      } else {
        line = candidate;
      }
    }
    if (y >= height) break;
    context.fillText(line, x, y, width);
    y += lineHeight;
  }
}

function structuredLayerPixels(layer: ShapeLayer | TextLayer | SmartObjectLayer): {
  readonly width: number;
  readonly height: number;
  readonly pixels: Uint8ClampedArray;
} {
  const width =
    layer.kind === 'shape'
      ? layer.geometry.width
      : layer.kind === 'smart-object'
        ? layer.source.canvas.width
        : layer.width;
  const height =
    layer.kind === 'shape'
      ? layer.geometry.height
      : layer.kind === 'smart-object'
        ? layer.source.canvas.height
        : layer.height;
  if (layer.kind === 'smart-object') {
    const pixels = applySmartFilters(
      compositeDocumentPixels({
        id: layer.source.id,
        name: layer.source.name,
        canvas: { ...layer.source.canvas },
        layers: layer.source.layers,
        selection: null,
        channels: [],
        guides: [],
        grid: null,
        paths: [],
      }),
      width,
      height,
      layer.smartFilters,
    );
    return {
      width,
      height,
      pixels: applyLocalMasks(pixels, layer.rasterMask, layer.vectorMask),
    };
  }
  if (typeof OffscreenCanvas === 'undefined' && typeof globalThis.document === 'undefined') {
    return {
      width,
      height,
      pixels:
        layer.kind === 'shape' ? cpuShapePixels(layer) : new Uint8ClampedArray(width * height * 4),
    };
  }
  const canvas =
    typeof OffscreenCanvas !== 'undefined'
      ? new OffscreenCanvas(width, height)
      : Object.assign(globalThis.document.createElement('canvas'), { width, height });
  const context = canvas.getContext('2d', { willReadFrequently: true }) as
    | CanvasRenderingContext2D
    | OffscreenCanvasRenderingContext2D
    | null;
  if (!context) throw new Error('Canvas 2D is not available.');
  if (layer.kind === 'shape') {
    drawShapeLayerPath(context, layer);
  } else {
    drawTextLayer(context, layer);
  }
  const pixels = context.getImageData(0, 0, width, height).data;
  return {
    width,
    height,
    pixels: applyLocalMasks(pixels, layer.rasterMask, layer.vectorMask),
  };
}

function shapeLayerPixelsRegion(layer: ShapeLayer, region: PixelRegion): Uint8ClampedArray {
  if (typeof OffscreenCanvas === 'undefined' && typeof globalThis.document === 'undefined') {
    return cpuShapePixelsRegion(layer, region);
  }
  const canvas =
    typeof OffscreenCanvas !== 'undefined'
      ? new OffscreenCanvas(region.width, region.height)
      : Object.assign(globalThis.document.createElement('canvas'), {
          width: region.width,
          height: region.height,
        });
  const context = canvas.getContext('2d', { willReadFrequently: true }) as
    | CanvasRenderingContext2D
    | OffscreenCanvasRenderingContext2D
    | null;
  if (!context) throw new Error('Canvas 2D is not available.');
  context.translate(-region.x, -region.y);
  drawShapeLayerPath(context, layer);
  context.setTransform(1, 0, 0, 1, 0, 0);
  return applyMasksRegion(
    context.getImageData(0, 0, region.width, region.height).data,
    layer.rasterMask,
    layer.vectorMask,
    region,
  );
}

function textLayerPixelsRegion(layer: TextLayer, region: PixelRegion): Uint8ClampedArray {
  if (typeof OffscreenCanvas === 'undefined' && typeof globalThis.document === 'undefined') {
    return applyMasksRegion(
      new Uint8ClampedArray(region.width * region.height * 4),
      layer.rasterMask,
      layer.vectorMask,
      region,
    );
  }
  const canvas =
    typeof OffscreenCanvas !== 'undefined'
      ? new OffscreenCanvas(region.width, region.height)
      : Object.assign(globalThis.document.createElement('canvas'), {
          width: region.width,
          height: region.height,
        });
  const context = canvas.getContext('2d', { willReadFrequently: true }) as
    | CanvasRenderingContext2D
    | OffscreenCanvasRenderingContext2D
    | null;
  if (!context) throw new Error('Canvas 2D is not available.');
  context.translate(-region.x, -region.y);
  drawTextLayer(context, layer);
  context.setTransform(1, 0, 0, 1, 0, 0);
  return applyMasksRegion(
    context.getImageData(0, 0, region.width, region.height).data,
    layer.rasterMask,
    layer.vectorMask,
    region,
  );
}

function smartFilterHalo(filters: readonly SmartFilter[]): number {
  return filters.reduce(
    (halo, filter) =>
      filter.enabled && filter.opacity > 0 && filter.type === 'gaussian-blur'
        ? halo + Math.max(0, Math.round(filter.radius))
        : halo,
    0,
  );
}

function expandRegionWithin(
  region: PixelRegion,
  halo: number,
  bounds: { readonly width: number; readonly height: number },
): PixelRegion {
  if (halo <= 0) return region;
  return (
    intersectRegions(
      {
        x: region.x - halo,
        y: region.y - halo,
        width: region.width + halo * 2,
        height: region.height + halo * 2,
      },
      { x: 0, y: 0, width: bounds.width, height: bounds.height },
    ) ?? region
  );
}

function smartObjectPixelsRegion(layer: SmartObjectLayer, region: PixelRegion): Uint8ClampedArray {
  const sourcePixels = compositeDocumentRegion(
    {
      id: layer.source.id,
      name: layer.source.name,
      canvas: { ...layer.source.canvas },
      layers: layer.source.layers,
      selection: null,
      channels: [],
      guides: [],
      grid: null,
      paths: [],
    },
    region,
  );
  return applyMasksRegion(
    applySmartFilters(sourcePixels, region.width, region.height, layer.smartFilters),
    layer.rasterMask,
    layer.vectorMask,
    region,
  );
}

function coloredAlphaSurface(
  alpha: Uint8ClampedArray,
  color: RgbaColor,
  opacity: number,
): Uint8ClampedArray {
  const output = new Uint8ClampedArray(alpha.length * 4);
  for (let index = 0; index < alpha.length; index += 1) {
    const offset = index * 4;
    output[offset] = color.red;
    output[offset + 1] = color.green;
    output[offset + 2] = color.blue;
    output[offset + 3] = Math.round((alpha[index] ?? 0) * (color.alpha / 255) * opacity);
  }
  return output;
}

function blendRenderedResultThroughMasks(
  destination: Uint8ClampedArray,
  rendered: Uint8ClampedArray,
  rasterMask: RasterMask | null,
  vectorMask: VectorMask | null,
): void {
  const vectorCoverage = vectorMaskCoverage(vectorMask);
  const rasterEnabled = rasterMask?.enabled === true;
  const rasterCoverage = rasterEnabled ? rasterMask.coverageStore.toContiguous() : null;
  if (!rasterEnabled && vectorCoverage === null) {
    destination.set(rendered);
    return;
  }
  for (let pixel = 0, offset = 0; offset < destination.length; pixel += 1, offset += 4) {
    const rasterFactor = rasterEnabled
      ? 1 - rasterMask.density + ((rasterCoverage?.[pixel] ?? 0) / 255) * rasterMask.density
      : 1;
    const vectorFactor =
      vectorCoverage !== null && vectorMask
        ? 1 - vectorMask.density + ((vectorCoverage[pixel] ?? 0) / 255) * vectorMask.density
        : 1;
    const factor = rasterFactor * vectorFactor;
    if (factor <= 0) continue;
    if (factor >= 1) {
      destination[offset] = rendered[offset] ?? 0;
      destination[offset + 1] = rendered[offset + 1] ?? 0;
      destination[offset + 2] = rendered[offset + 2] ?? 0;
      destination[offset + 3] = rendered[offset + 3] ?? 0;
      continue;
    }
    const destinationAlpha = (destination[offset + 3] ?? 0) / 255;
    const renderedAlpha = (rendered[offset + 3] ?? 0) / 255;
    const alpha = destinationAlpha + (renderedAlpha - destinationAlpha) * factor;
    for (let channel = 0; channel < 3; channel += 1) {
      const destinationPremultiplied =
        ((destination[offset + channel] ?? 0) / 255) * destinationAlpha;
      const renderedPremultiplied = ((rendered[offset + channel] ?? 0) / 255) * renderedAlpha;
      const premultiplied =
        destinationPremultiplied + (renderedPremultiplied - destinationPremultiplied) * factor;
      destination[offset + channel] = alpha <= 0 ? 0 : Math.round((premultiplied / alpha) * 255);
    }
    destination[offset + 3] = Math.round(alpha * 255);
  }
}

function blendRenderedResultThroughMasksRegion(
  destination: Uint8ClampedArray,
  rendered: Uint8ClampedArray,
  rasterMask: RasterMask | null,
  vectorMask: VectorMask | null,
  region: PixelRegion,
): void {
  const rasterCoverage =
    rasterMask !== null && rasterMask.enabled && rasterMask.density > 0
      ? rasterMask.coverageStore.read(region)
      : null;
  const vectorCoverage = vectorMaskCoverageRegion(vectorMask, region);
  if (rasterCoverage === null && vectorCoverage === null) {
    destination.set(rendered);
    return;
  }
  if (rasterCoverage !== null) assertCoverageBuffer(rasterCoverage, region.width, region.height);
  for (let pixel = 0, offset = 0; offset < destination.length; pixel += 1, offset += 4) {
    const rasterFactor =
      rasterCoverage === null || rasterMask === null
        ? 1
        : 1 - rasterMask.density + ((rasterCoverage[pixel] ?? 0) / 255) * rasterMask.density;
    const vectorFactor =
      vectorCoverage === null || vectorMask === null
        ? 1
        : 1 - vectorMask.density + ((vectorCoverage[pixel] ?? 0) / 255) * vectorMask.density;
    const factor = rasterFactor * vectorFactor;
    if (factor <= 0) continue;
    if (factor >= 1) {
      destination[offset] = rendered[offset] ?? 0;
      destination[offset + 1] = rendered[offset + 1] ?? 0;
      destination[offset + 2] = rendered[offset + 2] ?? 0;
      destination[offset + 3] = rendered[offset + 3] ?? 0;
      continue;
    }
    const destinationAlpha = (destination[offset + 3] ?? 0) / 255;
    const renderedAlpha = (rendered[offset + 3] ?? 0) / 255;
    const alpha = destinationAlpha + (renderedAlpha - destinationAlpha) * factor;
    for (let channel = 0; channel < 3; channel += 1) {
      const destinationPremultiplied =
        ((destination[offset + channel] ?? 0) / 255) * destinationAlpha;
      const renderedPremultiplied = ((rendered[offset + channel] ?? 0) / 255) * renderedAlpha;
      const premultiplied =
        destinationPremultiplied + (renderedPremultiplied - destinationPremultiplied) * factor;
      destination[offset + channel] = alpha <= 0 ? 0 : Math.round((premultiplied / alpha) * 255);
    }
    destination[offset + 3] = Math.round(alpha * 255);
  }
}

function boxBlurAlpha(
  source: Uint8ClampedArray,
  width: number,
  height: number,
  radius: number,
): Uint8ClampedArray {
  const size = Math.max(0, Math.round(radius));
  if (size === 0) return new Uint8ClampedArray(source);
  const horizontal = new Float32Array(source.length);
  for (let y = 0; y < height; y += 1) {
    let sum = 0;
    for (let x = -size; x <= size; x += 1)
      sum += source[y * width + Math.max(0, Math.min(width - 1, x))] ?? 0;
    for (let x = 0; x < width; x += 1) {
      horizontal[y * width + x] = sum / (size * 2 + 1);
      sum += source[y * width + Math.min(width - 1, x + size + 1)] ?? 0;
      sum -= source[y * width + Math.max(0, x - size)] ?? 0;
    }
  }
  const output = new Uint8ClampedArray(source.length);
  for (let x = 0; x < width; x += 1) {
    let sum = 0;
    for (let y = -size; y <= size; y += 1)
      sum += horizontal[Math.max(0, Math.min(height - 1, y)) * width + x] ?? 0;
    for (let y = 0; y < height; y += 1) {
      output[y * width + x] = Math.round(sum / (size * 2 + 1));
      sum += horizontal[Math.min(height - 1, y + size + 1) * width + x] ?? 0;
      sum -= horizontal[Math.max(0, y - size) * width + x] ?? 0;
    }
  }
  return output;
}

function dilateAlpha(
  source: Uint8ClampedArray,
  width: number,
  height: number,
  radius: number,
): Uint8ClampedArray {
  const size = Math.max(0, Math.round(radius));
  if (size === 0) return new Uint8ClampedArray(source);
  const horizontal = new Uint8ClampedArray(source.length);
  for (let y = 0; y < height; y += 1) {
    const deque = new Int32Array(width);
    let head = 0;
    let tail = 0;
    let next = 0;
    for (let x = 0; x < width; x += 1) {
      while (next <= Math.min(width - 1, x + size)) {
        while (
          tail > head &&
          (source[y * width + (deque[tail - 1] ?? 0)] ?? 0) <= (source[y * width + next] ?? 0)
        )
          tail -= 1;
        deque[tail++] = next++;
      }
      while (tail > head && (deque[head] ?? 0) < x - size) head += 1;
      horizontal[y * width + x] = source[y * width + (deque[head] ?? 0)] ?? 0;
    }
  }
  const output = new Uint8ClampedArray(source.length);
  for (let x = 0; x < width; x += 1) {
    const deque = new Int32Array(height);
    let head = 0;
    let tail = 0;
    let next = 0;
    for (let y = 0; y < height; y += 1) {
      while (next <= Math.min(height - 1, y + size)) {
        while (
          tail > head &&
          (horizontal[(deque[tail - 1] ?? 0) * width + x] ?? 0) <=
            (horizontal[next * width + x] ?? 0)
        )
          tail -= 1;
        deque[tail++] = next++;
      }
      while (tail > head && (deque[head] ?? 0) < y - size) head += 1;
      output[y * width + x] = horizontal[(deque[head] ?? 0) * width + x] ?? 0;
    }
  }
  return output;
}

function applyLayerEffects(
  raster: Uint8ClampedArray,
  effects: readonly LayerEffect[],
  width: number,
  height: number,
): Uint8ClampedArray {
  if (!effects.some((effect) => effect.enabled && effect.opacity > 0)) return raster;
  const sourceAlpha = alphaChannel(raster);
  const result = new Uint8ClampedArray(raster.length);
  for (const effect of effects) {
    if (!effect.enabled || effect.opacity <= 0 || effect.type !== 'drop-shadow') continue;
    const blurred = boxBlurAlpha(sourceAlpha, width, height, effect.blur);
    const shifted = new Uint8ClampedArray(blurred.length);
    const offsetX = Math.round(effect.offsetX);
    const offsetY = Math.round(effect.offsetY);
    for (let y = 0; y < height; y += 1) {
      for (let x = 0; x < width; x += 1) {
        const sourceX = x - offsetX;
        const sourceY = y - offsetY;
        if (sourceX >= 0 && sourceX < width && sourceY >= 0 && sourceY < height)
          shifted[y * width + x] = blurred[sourceY * width + sourceX] ?? 0;
      }
    }
    compositeSurfaceInPlace(result, coloredAlphaSurface(shifted, effect.color, effect.opacity), {
      blendMode: 'normal',
      opacity: 1,
      fillOpacity: 1,
    });
  }
  for (const effect of effects) {
    if (!effect.enabled || effect.opacity <= 0 || effect.type !== 'stroke') continue;
    const size = Math.max(0, Math.round(effect.size));
    const dilated = dilateAlpha(sourceAlpha, width, height, size);
    for (let index = 0; index < dilated.length; index += 1)
      dilated[index] = Math.max(0, (dilated[index] ?? 0) - (sourceAlpha[index] ?? 0));
    compositeSurfaceInPlace(result, coloredAlphaSurface(dilated, effect.color, effect.opacity), {
      blendMode: 'normal',
      opacity: 1,
      fillOpacity: 1,
    });
  }
  const content = new Uint8ClampedArray(raster);
  for (const effect of effects) {
    if (!effect.enabled || effect.opacity <= 0 || effect.type !== 'color-overlay') continue;
    const overlay = coloredAlphaSurface(sourceAlpha, effect.color, effect.opacity);
    compositeSurfaceInPlace(content, overlay, {
      blendMode: effect.blendMode,
      opacity: 1,
      fillOpacity: 1,
    });
    for (let index = 0; index < sourceAlpha.length; index += 1)
      content[index * 4 + 3] = sourceAlpha[index] ?? 0;
  }
  compositeSurfaceInPlace(result, content, { blendMode: 'normal', opacity: 1, fillOpacity: 1 });
  return result;
}

function compositeLayerStack(
  destination: Uint8ClampedArray,
  layers: readonly Layer[],
  canvas: { readonly width: number; readonly height: number },
  rasterize: LayerRasterizer,
  pixelSource: PixelSourceResolver | undefined,
  parentTransform: LayerTransform = IDENTITY_MATRIX,
  parentOpacity = 1,
): void {
  let clipBase: Uint8ClampedArray | null = null;
  for (const layer of layers) {
    if (!layer.visible || layer.opacity <= 0 || layer.fillOpacity <= 0) continue;
    const transform = multiplyAffine(parentTransform, layer.transform);
    if (layer.kind === 'adjustment') {
      applyAdjustment(destination, { ...layer, opacity: layer.opacity * parentOpacity }, clipBase);
      if (!layer.clipping) clipBase = alphaChannel(destination);
      continue;
    }
    if (layer.kind === 'group') {
      if (layer.compositing === 'pass-through') {
        const groupDestination =
          layer.rasterMask === null && layer.vectorMask === null
            ? destination
            : new Uint8ClampedArray(destination);
        compositeLayerStack(
          groupDestination,
          layer.children,
          canvas,
          rasterize,
          pixelSource,
          transform,
          parentOpacity * layer.opacity,
        );
        if (layer.rasterMask !== null || layer.vectorMask !== null) {
          blendRenderedResultThroughMasks(
            destination,
            groupDestination,
            layer.rasterMask,
            layer.vectorMask,
          );
        }
        if (!layer.clipping) clipBase = alphaChannel(destination);
      } else {
        const groupSurface = new Uint8ClampedArray(destination.length);
        compositeLayerStack(
          groupSurface,
          layer.children,
          canvas,
          rasterize,
          pixelSource,
          transform,
          1,
        );
        const maskedGroupSurface = applyLocalMasks(
          groupSurface,
          layer.rasterMask,
          layer.vectorMask,
        );
        const source =
          layer.clipping && clipBase
            ? clippedSource(maskedGroupSurface, clipBase)
            : maskedGroupSurface;
        compositeSurfaceInPlace(destination, source, {
          blendMode: layer.blendMode,
          opacity: parentOpacity * layer.opacity,
          fillOpacity: layer.fillOpacity,
        });
        if (!layer.clipping) clipBase = alphaChannel(source);
      }
      continue;
    }
    const local =
      layer.kind === 'pixel'
        ? { width: layer.width, height: layer.height, pixels: maskedPixels(layer, pixelSource) }
        : structuredLayerPixels(layer);
    const raster = applyLayerEffects(
      rasterize(local, transform, canvas, local.pixels),
      layer.effects,
      canvas.width,
      canvas.height,
    );
    const source = layer.clipping && clipBase ? clippedSource(raster, clipBase) : raster;
    compositeSurfaceInPlace(destination, source, {
      blendMode: layer.blendMode,
      opacity: parentOpacity * layer.opacity,
      fillOpacity: layer.fillOpacity,
    });
    if (!layer.clipping) clipBase = alphaChannel(source);
  }
}

/** Produces an export-ready straight-alpha document composite with no checkerboard. */
export function compositeDocumentPixels(
  document: ImageDocument,
  options: CompositeDocumentOptions = {},
): Uint8ClampedArray {
  const output = new Uint8ClampedArray(document.canvas.width * document.canvas.height * 4);
  const rasterize =
    options.rasterize ??
    (typeof OffscreenCanvas !== 'undefined' || typeof globalThis.document !== 'undefined'
      ? browserLayerRasterizer
      : rasterizeLayerNearest);
  compositeLayerStack(output, document.layers, document.canvas, rasterize, options.pixelSource);
  const preview = options.preview;
  if (preview === undefined || preview.mode === 'composite') return output;
  if (preview.mode === 'alpha') {
    return alphaPreviewPixels(
      alphaPreviewSource(
        preview.coverage,
        document.canvas.width,
        document.canvas.height,
      ).toContiguous(),
    );
  }
  return rgbChannelPreviewPixels(output, preview.mode);
}

/**
 * Composites only a document-space region into a region-sized buffer.
 * Pixel-equivalent to cropping `compositeDocumentPixels` for the same region when
 * using `rasterizeLayerNearest`. Used by the composite tile cache for local misses.
 */
export function compositeDocumentRegion(
  document: ImageDocument,
  region: PixelRegion,
  options: CompositeDocumentOptions = {},
): Uint8ClampedArray {
  const clipped = intersectRegions(region, {
    x: 0,
    y: 0,
    width: document.canvas.width,
    height: document.canvas.height,
  });
  if (!clipped || clipped.width <= 0 || clipped.height <= 0) {
    return new Uint8ClampedArray(0);
  }
  const rasterize =
    options.rasterize ??
    (typeof OffscreenCanvas !== 'undefined' || typeof globalThis.document !== 'undefined'
      ? browserLayerRasterizer
      : rasterizeLayerNearest);
  const preview = options.preview;
  if (preview?.mode === 'alpha') {
    const coverage = alphaPreviewSource(
      preview.coverage,
      document.canvas.width,
      document.canvas.height,
    ).read(clipped);
    return alphaPreviewPixels(coverage);
  }
  // Built-ins support regions directly. Custom rasterizers must opt in with an
  // explicit bounded callback; function arity does not prove region semantics.
  const regionRasterize =
    options.rasterizeRegion ??
    (options.rasterize === undefined || options.rasterize === rasterizeLayerNearest
      ? rasterize
      : undefined);
  if (!regionRasterize || !layersSupportRegionComposite(document.layers)) {
    const full = compositeDocumentPixels(document, options);
    return cropRgbaBuffer(full, document.canvas.width, document.canvas.height, clipped);
  }
  const output = new Uint8ClampedArray(clipped.width * clipped.height * 4);
  compositeLayerStackRegion(
    output,
    clipped,
    document.layers,
    document.canvas,
    regionRasterize,
    options.pixelSource,
  );
  return preview && preview.mode !== 'composite'
    ? rgbChannelPreviewPixels(output, preview.mode)
    : output;
}

/** Whether browser/nearest compositing can render exact regions without a full-document fallback. */
export function supportsDocumentRegionComposite(document: ImageDocument): boolean {
  return layersSupportRegionComposite(document.layers);
}

function layersSupportRegionComposite(layers: readonly Layer[]): boolean {
  for (const layer of layers) {
    if (layer.kind === 'group') {
      if (!layersSupportRegionComposite(layer.children)) return false;
      continue;
    }
    if (layer.kind === 'smart-object') {
      if (!layersSupportRegionComposite(layer.source.layers)) return false;
      continue;
    }
    if (layer.kind !== 'pixel' && layer.kind !== 'shape' && layer.kind !== 'text') return false;
  }
  return true;
}

function cropRgbaBuffer(
  source: Uint8ClampedArray,
  sourceWidth: number,
  sourceHeight: number,
  region: PixelRegion,
): Uint8ClampedArray {
  const output = new Uint8ClampedArray(region.width * region.height * 4);
  for (let row = 0; row < region.height; row += 1) {
    const sourceY = region.y + row;
    if (sourceY < 0 || sourceY >= sourceHeight) continue;
    if (region.x >= sourceWidth) continue;
    const copyX = Math.max(0, region.x);
    const copyWidth = Math.min(region.width, sourceWidth - copyX);
    if (copyWidth <= 0) continue;
    const sourceStart = (sourceY * sourceWidth + copyX) * 4;
    const destStart = (row * region.width + (copyX - region.x)) * 4;
    output.set(source.subarray(sourceStart, sourceStart + copyWidth * 4), destStart);
  }
  return output;
}

function compositeLayerStackRegion(
  destination: Uint8ClampedArray,
  region: PixelRegion,
  layers: readonly Layer[],
  canvas: { readonly width: number; readonly height: number },
  rasterize: RegionLayerRasterizer,
  pixelSource: PixelSourceResolver | undefined,
  parentTransform: LayerTransform = IDENTITY_MATRIX,
  parentOpacity = 1,
): void {
  let clipBase: Uint8ClampedArray | null = null;
  for (const layer of layers) {
    if (!layer.visible || layer.opacity <= 0 || layer.fillOpacity <= 0) continue;
    const transform = multiplyAffine(parentTransform, layer.transform);
    if (layer.kind === 'adjustment') {
      applyAdjustment(destination, { ...layer, opacity: layer.opacity * parentOpacity }, clipBase);
      if (!layer.clipping) clipBase = alphaChannel(destination);
      continue;
    }
    if (layer.kind === 'group') {
      if (layer.compositing === 'pass-through') {
        const groupDestination =
          layer.rasterMask === null && layer.vectorMask === null
            ? destination
            : new Uint8ClampedArray(destination);
        compositeLayerStackRegion(
          groupDestination,
          region,
          layer.children,
          canvas,
          rasterize,
          pixelSource,
          transform,
          parentOpacity * layer.opacity,
        );
        if (layer.rasterMask !== null || layer.vectorMask !== null) {
          blendRenderedResultThroughMasksRegion(
            destination,
            groupDestination,
            layer.rasterMask,
            layer.vectorMask,
            region,
          );
        }
        if (!layer.clipping) clipBase = alphaChannel(destination);
      } else {
        const groupSurface = new Uint8ClampedArray(destination.length);
        compositeLayerStackRegion(
          groupSurface,
          region,
          layer.children,
          canvas,
          rasterize,
          pixelSource,
          transform,
          1,
        );
        const maskedGroupSurface = applyMasksRegion(
          groupSurface,
          layer.rasterMask,
          layer.vectorMask,
          region,
        );
        const source =
          layer.clipping && clipBase
            ? clippedSource(maskedGroupSurface, clipBase)
            : maskedGroupSurface;
        compositeSurfaceInPlace(destination, source, {
          blendMode: layer.blendMode,
          opacity: parentOpacity * layer.opacity,
          fillOpacity: layer.fillOpacity,
        });
        if (!layer.clipping) clipBase = alphaChannel(source);
      }
      continue;
    }
    // Pixel region path: direct read for integer translations, or inverse-mapped
    // source patch + adjusted transform for scale/rotate/shear. Neither path
    // materializes the full logical layer.
    if (layer.kind === 'pixel') {
      const effectRegion = pixelEffectRegion(region, layer.effects, canvas);
      const baseRaster =
        isIntegerTranslationTransform(transform) && effectRegion === region
          ? applyMasksRegion(
              readPixelLayerRegion(layer, pixelSource, region, transform),
              layer.rasterMask,
              layer.vectorMask,
              pixelLayerLocalRegion(region, transform),
            )
          : rasterizePixelLayerRegion(
              layer,
              pixelSource,
              effectRegion,
              transform,
              canvas,
              rasterize,
            );
      const effected = applyLayerEffects(
        baseRaster,
        layer.effects,
        effectRegion.width,
        effectRegion.height,
      );
      const raster =
        effectRegion === region
          ? effected
          : cropRgbaBuffer(effected, effectRegion.width, effectRegion.height, {
              x: region.x - effectRegion.x,
              y: region.y - effectRegion.y,
              width: region.width,
              height: region.height,
            });
      const source = layer.clipping && clipBase ? clippedSource(raster, clipBase) : raster;
      compositeSurfaceInPlace(destination, source, {
        blendMode: layer.blendMode,
        opacity: parentOpacity * layer.opacity,
        fillOpacity: layer.fillOpacity,
      });
      if (!layer.clipping) clipBase = alphaChannel(source);
      continue;
    }

    {
      const effectRegion = pixelEffectRegion(region, layer.effects, canvas);
      const baseRaster = rasterizeStructuredLayerRegion(
        layer,
        effectRegion,
        transform,
        canvas,
        rasterize,
      );
      const effected = applyLayerEffects(
        baseRaster,
        layer.effects,
        effectRegion.width,
        effectRegion.height,
      );
      const raster =
        effectRegion === region
          ? effected
          : cropRgbaBuffer(effected, effectRegion.width, effectRegion.height, {
              x: region.x - effectRegion.x,
              y: region.y - effectRegion.y,
              width: region.width,
              height: region.height,
            });
      const source = layer.clipping && clipBase ? clippedSource(raster, clipBase) : raster;
      compositeSurfaceInPlace(destination, source, {
        blendMode: layer.blendMode,
        opacity: parentOpacity * layer.opacity,
        fillOpacity: layer.fillOpacity,
      });
      if (!layer.clipping) clipBase = alphaChannel(source);
    }
  }
}

function rasterizeStructuredLayerRegion(
  layer: ShapeLayer | TextLayer | SmartObjectLayer,
  documentRegion: PixelRegion,
  transform: LayerTransform,
  canvas: { readonly width: number; readonly height: number },
  rasterize: RegionLayerRasterizer,
): Uint8ClampedArray {
  const width =
    layer.kind === 'shape'
      ? layer.geometry.width
      : layer.kind === 'smart-object'
        ? layer.source.canvas.width
        : layer.width;
  const height =
    layer.kind === 'shape'
      ? layer.geometry.height
      : layer.kind === 'smart-object'
        ? layer.source.canvas.height
        : layer.height;
  const mappedRegion = pixelLayerSourceRegion(documentRegion, transform, width, height);
  if (mappedRegion === null) {
    return new Uint8ClampedArray(documentRegion.width * documentRegion.height * 4);
  }
  const sourceRegion =
    layer.kind === 'smart-object'
      ? expandRegionWithin(mappedRegion, smartFilterHalo(layer.smartFilters), { width, height })
      : mappedRegion;
  const pixels =
    layer.kind === 'shape'
      ? shapeLayerPixelsRegion(layer, sourceRegion)
      : layer.kind === 'text'
        ? textLayerPixelsRegion(layer, sourceRegion)
        : smartObjectPixelsRegion(layer, sourceRegion);
  const patchTransform = multiplyAffine(transform, {
    a: 1,
    b: 0,
    c: 0,
    d: 1,
    e: sourceRegion.x,
    f: sourceRegion.y,
  });
  return rasterize(
    { width: sourceRegion.width, height: sourceRegion.height },
    patchTransform,
    canvas,
    pixels,
    documentRegion,
  );
}

function pixelEffectRegion(
  region: PixelRegion,
  effects: readonly LayerEffect[],
  canvas: { readonly width: number; readonly height: number },
): PixelRegion {
  let halo = 0;
  for (const effect of effects) {
    if (!effect.enabled || effect.opacity <= 0) continue;
    if (effect.type === 'stroke') halo = Math.max(halo, Math.round(effect.size));
    if (effect.type === 'drop-shadow') {
      halo = Math.max(
        halo,
        Math.round(effect.blur) +
          Math.max(Math.abs(Math.round(effect.offsetX)), Math.abs(Math.round(effect.offsetY))),
      );
    }
  }
  if (halo <= 0) return region;
  return (
    intersectRegions(
      {
        x: region.x - halo,
        y: region.y - halo,
        width: region.width + halo * 2,
        height: region.height + halo * 2,
      },
      { x: 0, y: 0, width: canvas.width, height: canvas.height },
    ) ?? region
  );
}

function rasterizePixelLayerRegion(
  layer: PixelLayer,
  resolver: PixelSourceResolver | undefined,
  documentRegion: PixelRegion,
  transform: LayerTransform,
  canvas: { readonly width: number; readonly height: number },
  rasterize: RegionLayerRasterizer,
): Uint8ClampedArray {
  const sourceRegion = pixelLayerSourceRegion(documentRegion, transform, layer.width, layer.height);
  if (sourceRegion === null) {
    return new Uint8ClampedArray(documentRegion.width * documentRegion.height * 4);
  }
  const source = resolver?.(layer) ?? layer.pixelStore;
  assertPixelSource(source, layer.width, layer.height);
  const pixels = applyMasksRegion(
    source.read(sourceRegion),
    layer.rasterMask,
    layer.vectorMask,
    sourceRegion,
  );
  const patchTransform = multiplyAffine(transform, {
    a: 1,
    b: 0,
    c: 0,
    d: 1,
    e: sourceRegion.x,
    f: sourceRegion.y,
  });
  return rasterize(
    { width: sourceRegion.width, height: sourceRegion.height },
    patchTransform,
    canvas,
    pixels,
    documentRegion,
  );
}

function pixelLayerSourceRegion(
  documentRegion: PixelRegion,
  transform: LayerTransform,
  width: number,
  height: number,
): PixelRegion | null {
  const determinant = transform.a * transform.d - transform.b * transform.c;
  if (!Number.isFinite(determinant) || Math.abs(determinant) < Number.EPSILON) {
    return { x: 0, y: 0, width, height };
  }
  const inverse: AffineMatrix = {
    a: transform.d / determinant,
    b: -transform.b / determinant,
    c: -transform.c / determinant,
    d: transform.a / determinant,
    e: (transform.c * transform.f - transform.d * transform.e) / determinant,
    f: (transform.b * transform.e - transform.a * transform.f) / determinant,
  };
  const corners = [
    affinePoint(inverse, documentRegion.x, documentRegion.y),
    affinePoint(inverse, documentRegion.x + documentRegion.width, documentRegion.y),
    affinePoint(inverse, documentRegion.x, documentRegion.y + documentRegion.height),
    affinePoint(
      inverse,
      documentRegion.x + documentRegion.width,
      documentRegion.y + documentRegion.height,
    ),
  ];
  const left = Math.floor(Math.min(...corners.map((point) => point.x))) - 1;
  const top = Math.floor(Math.min(...corners.map((point) => point.y))) - 1;
  const right = Math.ceil(Math.max(...corners.map((point) => point.x))) + 1;
  const bottom = Math.ceil(Math.max(...corners.map((point) => point.y))) + 1;
  return intersectRegions(
    { x: left, y: top, width: right - left, height: bottom - top },
    { x: 0, y: 0, width, height },
  );
}

/** True when the transform is pure integer translation (no scale/rotate/shear). */
function isIntegerTranslationTransform(transform: LayerTransform): boolean {
  return (
    transform.a === 1 &&
    transform.b === 0 &&
    transform.c === 0 &&
    transform.d === 1 &&
    Number.isInteger(transform.e) &&
    Number.isInteger(transform.f)
  );
}

/**
 * Reads a document-space region from a pixel layer under integer translation,
 * using PixelSource.read (tile-local) instead of full-layer materialization.
 */
function readPixelLayerRegion(
  layer: PixelLayer,
  resolver: PixelSourceResolver | undefined,
  documentRegion: PixelRegion,
  transform: LayerTransform,
): Uint8ClampedArray {
  const localRegion = pixelLayerLocalRegion(documentRegion, transform);
  const source = resolver?.(layer) ?? layer.pixelStore;
  assertPixelSource(source, layer.width, layer.height);
  return source.read(localRegion);
}

function pixelLayerLocalRegion(
  documentRegion: PixelRegion,
  transform: LayerTransform,
): PixelRegion {
  return {
    x: documentRegion.x - transform.e,
    y: documentRegion.y - transform.f,
    width: documentRegion.width,
    height: documentRegion.height,
  };
}

export {
  CompositeTileCache,
  compositeTileDependencyKey,
  type CompositeTileCacheComposeOptions,
  type CompositeTileCacheOptions,
  type CompositeTileCacheStats,
} from './compositeTileCache.js';

export function renderDocument(
  context: CanvasRenderingContext2D,
  document: ImageDocument,
  options: RenderOptions = {},
): void {
  const viewport = options.viewport ?? DEFAULT_VIEWPORT;
  const zoom = clampZoom(viewport.zoom);
  const canvasWidth = context.canvas.width;
  const canvasHeight = context.canvas.height;
  context.save();
  context.setTransform(1, 0, 0, 1, 0, 0);
  const dirtyRegions = options.dirtyRegions?.flatMap((region) => {
    const clipped = intersectRegions(region, {
      x: 0,
      y: 0,
      width: document.canvas.width,
      height: document.canvas.height,
    });
    return clipped ? [clipped] : [];
  });
  if (dirtyRegions !== undefined && dirtyRegions.length === 0) {
    context.restore();
    return;
  }
  if (options.clear !== false) {
    if (dirtyRegions === undefined) context.clearRect(0, 0, canvasWidth, canvasHeight);
    else {
      for (const region of dirtyRegions) {
        const left = Math.floor(viewport.panX + region.x * zoom);
        const top = Math.floor(viewport.panY + region.y * zoom);
        const right = Math.ceil(viewport.panX + (region.x + region.width) * zoom);
        const bottom = Math.ceil(viewport.panY + (region.y + region.height) * zoom);
        context.clearRect(left, top, right - left, bottom - top);
      }
    }
  }
  if (dirtyRegions !== undefined) {
    context.beginPath();
    for (const region of dirtyRegions) {
      context.rect(
        viewport.panX + region.x * zoom,
        viewport.panY + region.y * zoom,
        region.width * zoom,
        region.height * zoom,
      );
    }
    context.clip();
  }

  const makeSurface = options.createSurface ?? createBrowserPixelSurface;
  const viewportMatrix: AffineMatrix = {
    a: zoom,
    b: 0,
    c: 0,
    d: zoom,
    e: viewport.panX,
    f: viewport.panY,
  };
  setContextTransform(context, viewportMatrix);
  if (options.checkerboard !== false) {
    drawCheckerboard(
      context,
      document.canvas.width,
      document.canvas.height,
      options.checkerboard ?? {},
    );
  }
  if (options.preview?.mode === 'alpha') {
    drawAlphaPreview(
      context,
      makeSurface,
      alphaPreviewSource(options.preview.coverage, document.canvas.width, document.canvas.height),
      document.canvas.width,
      document.canvas.height,
    );
    drawGuidesAndGrid(context, document, options.guidesOverlay, viewportMatrix);
    context.restore();
    return;
  }
  if (!requiresDeterministicComposite(document.layers)) {
    for (const { layer, opacity, transform } of flattenVisibleLayers(document.layers)) {
      context.globalAlpha = opacity;
      context.globalCompositeOperation = 'source-over';
      setContextTransform(context, multiplyAffine(viewportMatrix, transform));
      const source = options.pixelSource?.(layer);
      if (source && layer.vectorMask === null) {
        assertPixelSource(source, layer.width, layer.height);
        const requestedSourceRegion = dirtyRegions
          ? sourceRegionForDocumentDirt(dirtyRegions, transform, layer.width, layer.height)
          : undefined;
        if (dirtyRegions && requestedSourceRegion === null) continue;
        for (const chunk of source.pixelChunks(requestedSourceRegion ?? undefined)) {
          assertPixelBuffer(chunk.pixels, chunk.width, chunk.height);
          const maskedPixels = applyRasterMaskRegion(chunk.pixels, layer.rasterMask, chunk);
          const pixels =
            options.preview && options.preview.mode !== 'composite'
              ? rgbChannelPreviewPixels(maskedPixels, options.preview.mode)
              : maskedPixels;
          const surface = makeSurface(chunk.width, chunk.height);
          surface.putPixels(pixels, chunk.width, chunk.height);
          context.drawImage(surface.source, chunk.x, chunk.y);
        }
      } else {
        const pixels = sourcePixels(layer, options.pixelSource);
        const surface = makeSurface(layer.width, layer.height);
        surface.putPixels(previewPixels(layer, options.preview, pixels), layer.width, layer.height);
        context.drawImage(surface.source, 0, 0);
      }
    }
    drawGuidesAndGrid(context, document, options.guidesOverlay, viewportMatrix);
    context.restore();
    return;
  }
  const compositeOptions = {
    preview: options.preview,
    rasterize: options.rasterize,
    rasterizeRegion: options.rasterizeRegion,
    pixelSource: options.pixelSource,
  };
  const cacheCompositeOptions = {
    rasterize: options.rasterize,
    rasterizeRegion: options.rasterizeRegion,
    pixelSource: options.pixelSource,
  };
  const compositeMipLevel = Math.max(0, Math.floor(options.compositeMipLevel ?? 0));
  const compositeScale = 2 ** compositeMipLevel;
  const compositeWidth = Math.max(1, Math.ceil(document.canvas.width / compositeScale));
  const compositeHeight = Math.max(1, Math.ceil(document.canvas.height / compositeScale));
  const expectedLength = compositeWidth * compositeHeight * 4;
  const precomposed =
    options.precomposedPixels &&
    options.precomposedPixels.length === expectedLength &&
    (options.preview === undefined || options.preview.mode === 'composite')
      ? options.precomposedPixels
      : undefined;
  const pixels =
    precomposed ??
    (options.compositeTileCache
      ? options.compositeTileCache.composite(document, {
          referenceComposite: (doc) => compositeDocumentPixels(doc, cacheCompositeOptions),
          regionComposite: (doc, region) =>
            compositeDocumentRegion(doc, region, cacheCompositeOptions),
          dirtyRegions,
          viewportRegion: CompositeTileCache.viewportDocumentRegion(
            { zoom, panX: viewport.panX, panY: viewport.panY },
            canvasWidth,
            canvasHeight,
            document.canvas,
          ),
          progressiveViewportOnly: options.progressiveComposite === true,
          warmAssembleOnly: options.warmAssembleOnly === true,
          mipLevel: compositeMipLevel,
        })
      : compositeMipLevel === 0
        ? compositeDocumentPixels(document, compositeOptions)
        : new CompositeTileCache().composite(document, {
            referenceComposite: (doc) => compositeDocumentPixels(doc, compositeOptions),
            mipLevel: compositeMipLevel,
          }));
  const displayPixels =
    options.compositeTileCache && options.preview && options.preview.mode !== 'composite'
      ? rgbChannelPreviewPixels(pixels, options.preview.mode)
      : pixels;
  const surface = makeSurface(compositeWidth, compositeHeight);
  surface.putPixels(displayPixels, compositeWidth, compositeHeight);
  context.globalAlpha = 1;
  context.globalCompositeOperation = 'source-over';
  setContextTransform(context, viewportMatrix);
  context.drawImage(surface.source, 0, 0, document.canvas.width, document.canvas.height);
  drawGuidesAndGrid(context, document, options.guidesOverlay, viewportMatrix);
  context.restore();
}

/**
 * Draws document guides and grid in document space. When `viewportMatrix` is
 * provided it is applied inside a save/restore so the caller's transform is
 * unchanged (layer transform assertions remain valid).
 */
export function drawGuidesAndGrid(
  context: CanvasRenderingContext2D,
  document: ImageDocument,
  options: GuidesOverlayOptions | false | undefined,
  viewportMatrix?: AffineMatrix,
): void {
  if (options === false) return;
  const showGuides = options?.showGuides !== false;
  const showGrid = options?.showGrid !== false;
  const width = document.canvas.width;
  const height = document.canvas.height;
  if (width < 1 || height < 1) return;

  const grid = document.grid;
  const guides = document.guides;
  const smartLines = options?.smartGuideLines ?? [];
  const willDrawGrid = showGrid && grid !== null;
  const willDrawGuides = showGuides && guides.length > 0;
  const willDrawSmart = smartLines.length > 0;
  // No-op without mutating context (preserves layer transform call order in tests).
  if (!willDrawGrid && !willDrawGuides && !willDrawSmart) return;

  context.save();
  if (viewportMatrix) {
    setContextTransform(context, viewportMatrix);
  }
  context.globalAlpha = 1;
  context.globalCompositeOperation = 'source-over';
  // Keep 1 device-pixel strokes regardless of zoom (inverse scale from current transform).
  const transform = viewportMatrix
    ? viewportMatrix
    : typeof context.getTransform === 'function'
      ? context.getTransform()
      : ({ a: 1, b: 0, c: 0, d: 1, e: 0, f: 0 } as DOMMatrix);
  const scaleX = Math.hypot(transform.a, transform.b) || 1;
  const scaleY = Math.hypot(transform.c, transform.d) || 1;
  const lineWidth = 1 / Math.max(scaleX, scaleY);

  if (willDrawGrid) {
    const { horizontal, vertical } = grid;
    context.strokeStyle = options?.gridColor ?? 'rgba(90, 110, 130, 0.35)';
    context.lineWidth = lineWidth;
    context.beginPath();
    if (horizontal > 0) {
      for (let x = horizontal; x < width; x += horizontal) {
        context.moveTo(x, 0);
        context.lineTo(x, height);
      }
    }
    if (vertical > 0) {
      for (let y = vertical; y < height; y += vertical) {
        context.moveTo(0, y);
        context.lineTo(width, y);
      }
    }
    context.stroke();
  }

  if (willDrawGuides) {
    context.strokeStyle = options?.guideColor ?? 'rgba(0, 180, 255, 0.9)';
    context.lineWidth = lineWidth;
    context.beginPath();
    for (const guide of guides) {
      if (guide.direction === 'vertical') {
        const x = guide.position;
        if (!Number.isFinite(x)) continue;
        context.moveTo(x, 0);
        context.lineTo(x, height);
      } else {
        const y = guide.position;
        if (!Number.isFinite(y)) continue;
        context.moveTo(0, y);
        context.lineTo(width, y);
      }
    }
    context.stroke();
  }

  if (willDrawSmart) {
    // Magenta smart guides (distinct from cyan document guides).
    context.strokeStyle = options?.smartGuideColor ?? 'rgba(255, 64, 180, 0.95)';
    context.lineWidth = lineWidth;
    context.setLineDash([4 / Math.max(scaleX, scaleY), 3 / Math.max(scaleX, scaleY)]);
    context.beginPath();
    for (const line of smartLines) {
      if (!Number.isFinite(line.position)) continue;
      if (line.axis === 'x') {
        context.moveTo(line.position, 0);
        context.lineTo(line.position, height);
      } else {
        context.moveTo(0, line.position);
        context.lineTo(width, line.position);
      }
    }
    context.stroke();
    context.setLineDash([]);
  }
  context.restore();
}

/**
 * True when the layer stack cannot be drawn with the simple canvas path and must
 * use the deterministic compositor (adjustment/shape/text/smart-object, non-normal
 * blend, fill opacity, clipping, or isolated groups with masks).
 */
export function requiresDeterministicComposite(layers: readonly Layer[]): boolean {
  return layers.some(
    (layer) =>
      layer.kind === 'adjustment' ||
      layer.kind === 'shape' ||
      layer.kind === 'text' ||
      layer.kind === 'smart-object' ||
      layer.blendMode !== 'normal' ||
      layer.fillOpacity !== 1 ||
      layer.clipping ||
      (layer.kind === 'group' && (layer.rasterMask !== null || layer.vectorMask !== null)) ||
      (layer.kind === 'group' &&
        (layer.compositing === 'isolated' || requiresDeterministicComposite(layer.children))),
  );
}

function setContextTransform(context: CanvasRenderingContext2D, matrix: AffineMatrix): void {
  context.setTransform(matrix.a, matrix.b, matrix.c, matrix.d, matrix.e, matrix.f);
}

/**
 * Multiplies affine transforms using Canvas 2D semantics. The returned transform applies
 * `local` first, then `parent`.
 */
function multiplyAffine(parent: AffineMatrix, local: AffineMatrix): AffineMatrix {
  return {
    a: parent.a * local.a + parent.c * local.b,
    b: parent.b * local.a + parent.d * local.b,
    c: parent.a * local.c + parent.c * local.d,
    d: parent.b * local.c + parent.d * local.d,
    e: parent.a * local.e + parent.c * local.f + parent.e,
    f: parent.b * local.e + parent.d * local.f + parent.f,
  };
}

function sourceRegionForDocumentDirt(
  dirtyRegions: readonly PixelRegion[],
  transform: AffineMatrix,
  width: number,
  height: number,
): PixelRegion | null {
  const determinant = transform.a * transform.d - transform.b * transform.c;
  if (!Number.isFinite(determinant) || Math.abs(determinant) < Number.EPSILON) {
    return { x: 0, y: 0, width, height };
  }
  const inverse: AffineMatrix = {
    a: transform.d / determinant,
    b: -transform.b / determinant,
    c: -transform.c / determinant,
    d: transform.a / determinant,
    e: (transform.c * transform.f - transform.d * transform.e) / determinant,
    f: (transform.b * transform.e - transform.a * transform.f) / determinant,
  };
  let combined: PixelRegion | null = null;
  for (const region of dirtyRegions) {
    const corners = [
      affinePoint(inverse, region.x, region.y),
      affinePoint(inverse, region.x + region.width, region.y),
      affinePoint(inverse, region.x, region.y + region.height),
      affinePoint(inverse, region.x + region.width, region.y + region.height),
    ];
    const left = Math.floor(Math.min(...corners.map((point) => point.x)));
    const top = Math.floor(Math.min(...corners.map((point) => point.y)));
    const right = Math.ceil(Math.max(...corners.map((point) => point.x)));
    const bottom = Math.ceil(Math.max(...corners.map((point) => point.y)));
    const clipped = intersectRegions(
      { x: left, y: top, width: right - left, height: bottom - top },
      { x: 0, y: 0, width, height },
    );
    if (!clipped) continue;
    if (!combined) combined = clipped;
    else {
      const combinedRight = Math.max(combined.x + combined.width, clipped.x + clipped.width);
      const combinedBottom = Math.max(combined.y + combined.height, clipped.y + clipped.height);
      const x = Math.min(combined.x, clipped.x);
      const y = Math.min(combined.y, clipped.y);
      combined = { x, y, width: combinedRight - x, height: combinedBottom - y };
    }
  }
  return combined;
}

function affinePoint(matrix: AffineMatrix, x: number, y: number): Point {
  return {
    x: matrix.a * x + matrix.c * y + matrix.e,
    y: matrix.b * x + matrix.d * y + matrix.f,
  };
}

function previewPixels(
  layer: PixelLayer,
  preview: ChannelPreview | undefined,
  source = sourcePixels(layer),
): Uint8ClampedArray {
  const pixels = applyLocalMasks(source, layer.rasterMask, layer.vectorMask);
  if (preview === undefined || preview.mode === 'composite' || preview.mode === 'alpha')
    return pixels;
  return rgbChannelPreviewPixels(pixels, preview.mode);
}

function rgbChannelPreviewPixels(
  pixels: Uint8ClampedArray,
  mode: 'red' | 'green' | 'blue',
): Uint8ClampedArray {
  const channelOffset = mode === 'red' ? 0 : mode === 'green' ? 1 : 2;
  const grayscale = new Uint8ClampedArray(pixels.length);
  for (let index = 0; index < pixels.length; index += 4) {
    const intensity = pixels[index + channelOffset] ?? 0;
    grayscale[index] = intensity;
    grayscale[index + 1] = intensity;
    grayscale[index + 2] = intensity;
    grayscale[index + 3] = pixels[index + 3] ?? 0;
  }
  return grayscale;
}

function alphaPreviewPixels(coverage: Uint8ClampedArray): Uint8ClampedArray {
  const pixels = new Uint8ClampedArray(coverage.length * 4);
  for (let index = 0; index < coverage.length; index += 1) {
    const intensity = coverage[index] ?? 0;
    const offset = index * 4;
    pixels[offset] = intensity;
    pixels[offset + 1] = intensity;
    pixels[offset + 2] = intensity;
    pixels[offset + 3] = 255;
  }
  return pixels;
}

function alphaPreviewSource(
  coverage: Uint8ClampedArray | CoverageSource,
  width: number,
  height: number,
): CoverageSource {
  if (coverage instanceof Uint8ClampedArray) {
    assertCoverageBuffer(coverage, width, height);
    return new ContiguousCoverageSource(width, height, coverage);
  }
  if (coverage.width !== width || coverage.height !== height) {
    throw new RangeError(
      `Channel coverage dimensions ${String(coverage.width)} × ${String(coverage.height)} do not match document dimensions ${String(width)} × ${String(height)}.`,
    );
  }
  return coverage;
}

function drawAlphaPreview(
  context: CanvasRenderingContext2D,
  makeSurface: PixelSurfaceFactory,
  coverage: CoverageSource,
  width: number,
  height: number,
): void {
  context.globalAlpha = 1;
  context.globalCompositeOperation = 'source-over';
  context.fillStyle = '#000';
  context.fillRect(0, 0, width, height);
  for (const chunk of coverage.coverageChunks()) {
    assertCoverageBuffer(chunk.coverage, chunk.width, chunk.height);
    const surface = makeSurface(chunk.width, chunk.height);
    surface.putPixels(alphaPreviewPixels(chunk.coverage), chunk.width, chunk.height);
    context.drawImage(surface.source, chunk.x, chunk.y);
  }
}

function sourcePixels(layer: PixelLayer, resolver?: PixelSourceResolver): Uint8ClampedArray {
  const source = resolver?.(layer);
  if (!source) {
    assertPixelSource(layer.pixelStore, layer.width, layer.height);
    return layer.pixelStore.toContiguous();
  }
  assertPixelSource(source, layer.width, layer.height);
  return source.toContiguous();
}

function maskedPixels(layer: PixelLayer, resolver?: PixelSourceResolver): Uint8ClampedArray {
  return applyLocalMasks(sourcePixels(layer, resolver), layer.rasterMask, layer.vectorMask);
}

function* flattenVisibleLayers(
  layers: readonly Layer[],
  parentOpacity = 1,
  parentTransform: AffineMatrix = IDENTITY_MATRIX,
): Generator<{ layer: PixelLayer; opacity: number; transform: AffineMatrix }> {
  for (const layer of layers) {
    if (!layer.visible || layer.opacity <= 0) continue;
    const opacity = parentOpacity * Math.min(1, Math.max(0, layer.opacity));
    const transform = multiplyAffine(parentTransform, layer.transform);
    if (layer.kind === 'pixel') yield { layer, opacity, transform };
    else if (layer.kind === 'group')
      yield* flattenVisibleLayers(layer.children, opacity, transform);
  }
}

function assertPixelBuffer(pixels: Uint8ClampedArray, width: number, height: number): void {
  if (!Number.isInteger(width) || width <= 0 || !Number.isInteger(height) || height <= 0) {
    throw new RangeError('Layer dimensions must be positive integers.');
  }
  if (pixels.length !== width * height * 4) {
    throw new RangeError(
      `Expected ${String(width * height * 4)} RGBA values, received ${String(pixels.length)}.`,
    );
  }
}

function assertPixelSource(source: PixelSource, width: number, height: number): void {
  if (source.width !== width || source.height !== height) {
    throw new RangeError(
      `Pixel source dimensions ${String(source.width)} × ${String(source.height)} do not match layer dimensions ${String(width)} × ${String(height)}.`,
    );
  }
}

function assertCoverageBuffer(coverage: Uint8ClampedArray, width: number, height: number): void {
  if (coverage.length !== width * height) {
    throw new RangeError(
      `Expected ${String(width * height)} channel coverage values, received ${String(coverage.length)}.`,
    );
  }
}
