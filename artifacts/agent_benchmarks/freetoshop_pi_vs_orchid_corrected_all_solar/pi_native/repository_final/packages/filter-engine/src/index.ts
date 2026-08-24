import {
  invertLayerTransform,
  transformPoint,
  validateLayerTransform,
  type LayerTransform,
  type PixelLayer,
  type PixelPatch,
  type SelectionMask,
} from '@freetoshop/core-document';
import { TiledPixelStore } from '@freetoshop/pixel-storage';

export interface LevelsOptions {
  readonly inputBlack: number;
  readonly gamma: number;
  readonly inputWhite: number;
  readonly outputBlack: number;
  readonly outputWhite: number;
}

export interface HueSaturationOptions {
  /** Rotation in degrees from -180 through 180. */
  readonly hue: number;
  /** Relative saturation adjustment from -100 through 100 percent. */
  readonly saturation: number;
  /** Relative lightness adjustment from -100 through 100 percent. */
  readonly lightness: number;
}

export type PointwiseRgbFilter =
  | { readonly kind: 'contrast'; readonly amount: number }
  | { readonly kind: 'levels'; readonly options: LevelsOptions }
  | { readonly kind: 'hue-saturation'; readonly options: HueSaturationOptions }
  | { readonly kind: 'invert' }
  | { readonly kind: 'grayscale' }
  | { readonly kind: 'threshold'; readonly level: number }
  | { readonly kind: 'posterize'; readonly levels: number };

export interface FilterPatchObserver {
  readonly onPatch?: (patch: PixelPatch) => void;
  readonly onProgress?: (completedTiles: number, totalTiles: number) => void;
}

const MAX_FILTER_OUTPUT_BYTES = 512 * 1024 * 1024;
const MAX_GAUSSIAN_WORKING_BYTES = 512 * 1024 * 1024;
const MAX_GAUSSIAN_RADIUS = 100;

interface Bounds {
  left: number;
  top: number;
  right: number;
  bottom: number;
}

type Rgb = readonly [number, number, number];

function validateLayer(layer: PixelLayer): void {
  if (!Number.isSafeInteger(layer.width) || layer.width <= 0) {
    throw new RangeError('Layer width must be a positive integer');
  }
  if (!Number.isSafeInteger(layer.height) || layer.height <= 0) {
    throw new RangeError('Layer height must be a positive integer');
  }
  const length = layer.width * layer.height * 4;
  if (
    !Number.isSafeInteger(length) ||
    layer.pixelStore.width !== layer.width ||
    layer.pixelStore.height !== layer.height
  ) {
    throw new RangeError('Layer pixel buffer length does not match its dimensions');
  }
  if (length > MAX_FILTER_OUTPUT_BYTES) {
    throw new RangeError('Filter output would exceed the allocation limit');
  }
}

function validateSelection(selection: SelectionMask | null | undefined): void {
  if (selection == null) return;
  if (!Number.isSafeInteger(selection.width) || selection.width <= 0) {
    throw new RangeError('Selection width must be a positive integer');
  }
  if (!Number.isSafeInteger(selection.height) || selection.height <= 0) {
    throw new RangeError('Selection height must be a positive integer');
  }
  if (
    selection.coverageStore.width !== selection.width ||
    selection.coverageStore.height !== selection.height
  ) {
    throw new RangeError('Selection coverage length does not match its dimensions');
  }
}

function validateContext(
  layer: PixelLayer,
  selection: SelectionMask | null | undefined,
  worldTransform: LayerTransform,
): void {
  validateLayer(layer);
  validateSelection(selection);
  validateLayerTransform(worldTransform);
}

function finite(value: number, label: string): void {
  if (!Number.isFinite(value)) throw new RangeError(`${label} must be finite`);
}

function integerInRange(value: number, minimum: number, maximum: number, label: string): void {
  if (!Number.isInteger(value) || value < minimum || value > maximum) {
    throw new RangeError(
      `${label} must be an integer between ${String(minimum)} and ${String(maximum)}`,
    );
  }
}

/**
 * Layer-space AABB of allocated pixel tiles. Null when the layer is empty
 * (fully transparent sparse). Used to avoid full-layer densify when unselected.
 */
function pixelStoreBounds(layer: PixelLayer): Bounds | null {
  let minX = layer.width;
  let minY = layer.height;
  let maxX = -1;
  let maxY = -1;
  for (const tile of layer.pixelStore.tiles()) {
    minX = Math.min(minX, tile.x);
    minY = Math.min(minY, tile.y);
    maxX = Math.max(maxX, tile.x + tile.width - 1);
    maxY = Math.max(maxY, tile.y + tile.height - 1);
  }
  if (maxX < minX || maxY < minY) return null;
  return { left: minX, top: minY, right: maxX, bottom: maxY };
}

/**
 * Document-space AABB of allocated selection tiles (may over-estimate non-zero
 * coverage but never under-estimate). Null when the selection is empty.
 */
function selectionDocumentBounds(selection: SelectionMask): Bounds | null {
  if (selection.coverageStore.defaultCoverage !== 0) {
    return { left: 0, top: 0, right: selection.width - 1, bottom: selection.height - 1 };
  }
  let minX = selection.width;
  let minY = selection.height;
  let maxX = -1;
  let maxY = -1;
  for (const tile of selection.coverageStore.tiles()) {
    minX = Math.min(minX, tile.x);
    minY = Math.min(minY, tile.y);
    maxX = Math.max(maxX, tile.x + tile.width - 1);
    maxY = Math.max(maxY, tile.y + tile.height - 1);
  }
  if (maxX < minX || maxY < minY) return null;
  return { left: minX, top: minY, right: maxX, bottom: maxY };
}

/**
 * Maps a document-space AABB into layer-local integer bounds via inverse transform
 * of the four corners, expanded by `pad` and clamped to the layer.
 */
function layerBoundsFromDocumentBounds(
  documentBounds: Bounds,
  worldTransform: LayerTransform,
  layerWidth: number,
  layerHeight: number,
  pad: number,
): Bounds | null {
  const inverse = invertLayerTransform(worldTransform);
  const corners = [
    transformPoint(inverse, documentBounds.left, documentBounds.top),
    transformPoint(inverse, documentBounds.right + 1, documentBounds.top),
    transformPoint(inverse, documentBounds.left, documentBounds.bottom + 1),
    transformPoint(inverse, documentBounds.right + 1, documentBounds.bottom + 1),
  ];
  let minX = corners[0].x;
  let minY = corners[0].y;
  let maxX = corners[0].x;
  let maxY = corners[0].y;
  for (let index = 1; index < corners.length; index += 1) {
    const corner = corners[index];
    minX = Math.min(minX, corner.x);
    minY = Math.min(minY, corner.y);
    maxX = Math.max(maxX, corner.x);
    maxY = Math.max(maxY, corner.y);
  }
  const left = Math.max(0, Math.floor(minX) - pad);
  const top = Math.max(0, Math.floor(minY) - pad);
  const right = Math.min(layerWidth - 1, Math.ceil(maxX) + pad);
  const bottom = Math.min(layerHeight - 1, Math.ceil(maxY) + pad);
  if (right < left || bottom < top) return null;
  return { left, top, right, bottom };
}

function intersectBounds(left: Bounds, right: Bounds): Bounds | null {
  const intersection = {
    left: Math.max(left.left, right.left),
    top: Math.max(left.top, right.top),
    right: Math.min(left.right, right.right),
    bottom: Math.min(left.bottom, right.bottom),
  };
  return intersection.right < intersection.left || intersection.bottom < intersection.top
    ? null
    : intersection;
}

/** Conservative document-space coverage read for a layer-space work rectangle. */
function selectionRegionForLayerBounds(
  bounds: Bounds,
  transform: LayerTransform,
  selection: SelectionMask,
): Bounds | null {
  const corners = [
    transformPoint(transform, bounds.left, bounds.top),
    transformPoint(transform, bounds.right, bounds.top),
    transformPoint(transform, bounds.left, bounds.bottom),
    transformPoint(transform, bounds.right, bounds.bottom),
  ];
  const left = Math.max(0, Math.floor(Math.min(...corners.map((point) => point.x))));
  const top = Math.max(0, Math.floor(Math.min(...corners.map((point) => point.y))));
  const right = Math.min(
    selection.width - 1,
    Math.floor(Math.max(...corners.map((point) => point.x))),
  );
  const bottom = Math.min(
    selection.height - 1,
    Math.floor(Math.max(...corners.map((point) => point.y))),
  );
  return right < left || bottom < top ? null : { left, top, right, bottom };
}

function readSelectionRegion(
  selection: SelectionMask,
  transform: LayerTransform,
  bounds: Bounds,
): { coverage: Uint8ClampedArray; region: Bounds } | null {
  const region = selectionRegionForLayerBounds(bounds, transform, selection);
  if (region === null) return null;
  return {
    coverage: selection.coverageStore.read({
      x: region.left,
      y: region.top,
      width: region.right - region.left + 1,
      height: region.bottom - region.top + 1,
    }),
    region,
  };
}

function selectionCoverage(
  selection: SelectionMask | null | undefined,
  transform: LayerTransform,
  x: number,
  y: number,
  denseCoverage?: Uint8ClampedArray | null,
  /** When denseCoverage is a sub-rectangle read, its document origin/size. */
  denseRegion?: Bounds | null,
): number {
  if (selection == null) return 1;
  const point = transformPoint(transform, x, y);
  const documentX = Math.floor(point.x);
  const documentY = Math.floor(point.y);
  if (
    documentX < 0 ||
    documentY < 0 ||
    documentX >= selection.width ||
    documentY >= selection.height
  ) {
    return 0;
  }
  if (denseCoverage && denseRegion) {
    if (
      documentX < denseRegion.left ||
      documentY < denseRegion.top ||
      documentX > denseRegion.right ||
      documentY > denseRegion.bottom
    ) {
      return 0;
    }
    const width = denseRegion.right - denseRegion.left + 1;
    return (
      (denseCoverage[(documentY - denseRegion.top) * width + (documentX - denseRegion.left)] ?? 0) /
      255
    );
  }
  const coverage = denseCoverage ?? selection.coverageStore.toContiguous();
  return (coverage[documentY * selection.width + documentX] ?? 0) / 255;
}

function include(bounds: Bounds | null, x: number, y: number): Bounds {
  if (bounds === null) return { left: x, top: y, right: x, bottom: y };
  bounds.left = Math.min(bounds.left, x);
  bounds.top = Math.min(bounds.top, y);
  bounds.right = Math.max(bounds.right, x);
  bounds.bottom = Math.max(bounds.bottom, y);
  return bounds;
}

function emptyPatch(): PixelPatch {
  return { x: 0, y: 0, width: 0, height: 0, pixels: new Uint8ClampedArray() };
}

function clampByte(value: number): number {
  return Math.max(0, Math.min(255, value));
}

function applyRgb(
  layer: PixelLayer,
  operation: (red: number, green: number, blue: number) => Rgb,
  selection: SelectionMask | null | undefined,
  worldTransform: LayerTransform,
): PixelPatch {
  validateContext(layer, selection, worldTransform);

  let work: Bounds = {
    left: 0,
    top: 0,
    right: layer.width - 1,
    bottom: layer.height - 1,
  };
  let selectionCoverageBuffer: Uint8ClampedArray | null = null;
  let selectionRegion: Bounds | null = null;

  if (selection != null) {
    const documentBounds = selectionDocumentBounds(selection);
    if (documentBounds === null) return emptyPatch();
    const mapped = layerBoundsFromDocumentBounds(
      documentBounds,
      worldTransform,
      layer.width,
      layer.height,
      1,
    );
    if (mapped === null) return emptyPatch();
    const sparse = pixelStoreBounds(layer);
    if (sparse === null) return emptyPatch();
    const bounded = intersectBounds(mapped, sparse);
    if (bounded === null) return emptyPatch();
    work = bounded;
    const selectionRead = readSelectionRegion(selection, worldTransform, work);
    if (selectionRead === null) return emptyPatch();
    selectionCoverageBuffer = selectionRead.coverage;
    selectionRegion = selectionRead.region;
  } else {
    // Unselected: process only allocated sparse tiles (empty layer → no-op).
    const sparse = pixelStoreBounds(layer);
    if (sparse === null) return emptyPatch();
    work = sparse;
  }

  const regionWidth = work.right - work.left + 1;
  const regionHeight = work.bottom - work.top + 1;
  const sourcePixels = layer.pixelStore.read({
    x: work.left,
    y: work.top,
    width: regionWidth,
    height: regionHeight,
  });
  const next = new Uint8ClampedArray(sourcePixels);
  let bounds: Bounds | null = null;
  for (let y = work.top; y <= work.bottom; y += 1) {
    for (let x = work.left; x <= work.right; x += 1) {
      const coverage = selectionCoverage(
        selection,
        worldTransform,
        x,
        y,
        selectionCoverageBuffer,
        selectionRegion,
      );
      if (coverage <= 0) continue;
      const offset = ((y - work.top) * regionWidth + (x - work.left)) * 4;
      const red = sourcePixels[offset] ?? 0;
      const green = sourcePixels[offset + 1] ?? 0;
      const blue = sourcePixels[offset + 2] ?? 0;
      const adjusted = operation(red, green, blue);
      const output: Rgb = [
        Math.round(red + (adjusted[0] - red) * coverage),
        Math.round(green + (adjusted[1] - green) * coverage),
        Math.round(blue + (adjusted[2] - blue) * coverage),
      ];
      if (output[0] === red && output[1] === green && output[2] === blue) continue;
      next[offset] = output[0];
      next[offset + 1] = output[1];
      next[offset + 2] = output[2];
      bounds = include(bounds, x, y);
    }
  }
  if (bounds === null) return emptyPatch();
  // Rebuild patch from the work-region buffer.
  const patchWidth = bounds.right - bounds.left + 1;
  const patchHeight = bounds.bottom - bounds.top + 1;
  const pixels = new Uint8ClampedArray(patchWidth * patchHeight * 4);
  for (let row = 0; row < patchHeight; row += 1) {
    const sourceY = bounds.top + row - work.top;
    const sourceX = bounds.left - work.left;
    const sourceOffset = (sourceY * regionWidth + sourceX) * 4;
    pixels.set(next.subarray(sourceOffset, sourceOffset + patchWidth * 4), row * patchWidth * 4);
  }
  return { x: bounds.left, y: bounds.top, width: patchWidth, height: patchHeight, pixels };
}

function applyRgbPatches(
  layer: PixelLayer,
  operation: (red: number, green: number, blue: number) => Rgb,
  selection: SelectionMask | null | undefined,
  worldTransform: LayerTransform,
  observer?: FilterPatchObserver,
): readonly PixelPatch[] {
  validateContext(layer, selection, worldTransform);

  let work: Bounds = {
    left: 0,
    top: 0,
    right: layer.width - 1,
    bottom: layer.height - 1,
  };
  if (selection != null) {
    const documentBounds = selectionDocumentBounds(selection);
    if (documentBounds === null) return [];
    const mapped = layerBoundsFromDocumentBounds(
      documentBounds,
      worldTransform,
      layer.width,
      layer.height,
      1,
    );
    if (mapped === null) return [];
    const sparse = pixelStoreBounds(layer);
    if (sparse === null) return [];
    const bounded = intersectBounds(mapped, sparse);
    if (bounded === null) return [];
    work = bounded;
  } else {
    const sparse = pixelStoreBounds(layer);
    if (sparse === null) return [];
    work = sparse;
  }

  const patches: PixelPatch[] = [];
  const tileSize = layer.pixelStore.tileSize;
  const startX = Math.floor(work.left / tileSize) * tileSize;
  const startY = Math.floor(work.top / tileSize) * tileSize;
  const totalTiles =
    (Math.floor(work.right / tileSize) - Math.floor(work.left / tileSize) + 1) *
    (Math.floor(work.bottom / tileSize) - Math.floor(work.top / tileSize) + 1);
  let completedTiles = 0;
  for (let tileY = startY; tileY <= work.bottom; tileY += tileSize) {
    const top = Math.max(work.top, tileY);
    const bottom = Math.min(work.bottom, tileY + tileSize - 1);
    for (let tileX = startX; tileX <= work.right; tileX += tileSize) {
      const left = Math.max(work.left, tileX);
      const right = Math.min(work.right, tileX + tileSize - 1);
      const width = right - left + 1;
      const height = bottom - top + 1;
      const source = layer.pixelStore.read({ x: left, y: top, width, height });
      const pixels = new Uint8ClampedArray(source);
      const selectionRead = selection
        ? readSelectionRegion(selection, worldTransform, { left, top, right, bottom })
        : null;
      if (selection && selectionRead === null) {
        completedTiles += 1;
        observer?.onProgress?.(completedTiles, totalTiles);
        continue;
      }
      let changed = false;
      for (let y = top; y <= bottom; y += 1) {
        for (let x = left; x <= right; x += 1) {
          const coverage = selectionCoverage(
            selection,
            worldTransform,
            x,
            y,
            selectionRead?.coverage,
            selectionRead?.region,
          );
          if (coverage <= 0) continue;
          const offset = ((y - top) * width + (x - left)) * 4;
          const red = source[offset] ?? 0;
          const green = source[offset + 1] ?? 0;
          const blue = source[offset + 2] ?? 0;
          const adjusted = operation(red, green, blue);
          const output: Rgb = [
            Math.round(red + (adjusted[0] - red) * coverage),
            Math.round(green + (adjusted[1] - green) * coverage),
            Math.round(blue + (adjusted[2] - blue) * coverage),
          ];
          if (output[0] === red && output[1] === green && output[2] === blue) continue;
          pixels[offset] = output[0];
          pixels[offset + 1] = output[1];
          pixels[offset + 2] = output[2];
          changed = true;
        }
      }
      if (changed) {
        const patch = { x: left, y: top, width, height, pixels };
        patches.push(patch);
        observer?.onPatch?.(patch);
      }
      completedTiles += 1;
      observer?.onProgress?.(completedTiles, totalTiles);
    }
  }
  return patches;
}

/** Applies a conventional contrast percentage from -100 through 100. */
export function contrast(
  layer: PixelLayer,
  amount: number,
  selection?: SelectionMask | null,
  worldTransform: LayerTransform = layer.transform,
): PixelPatch {
  finite(amount, 'Contrast amount');
  if (amount < -100 || amount > 100) {
    throw new RangeError('Contrast amount must be between -100 and 100');
  }
  const factor = (259 * (amount + 255)) / (255 * (259 - amount));
  return applyRgb(
    layer,
    (red, green, blue) => [
      clampByte(factor * (red - 128) + 128),
      clampByte(factor * (green - 128) + 128),
      clampByte(factor * (blue - 128) + 128),
    ],
    selection,
    worldTransform,
  );
}

export function levels(
  layer: PixelLayer,
  options: LevelsOptions,
  selection?: SelectionMask | null,
  worldTransform: LayerTransform = layer.transform,
): PixelPatch {
  integerInRange(options.inputBlack, 0, 254, 'Input black');
  integerInRange(options.inputWhite, 1, 255, 'Input white');
  if (options.inputBlack >= options.inputWhite) {
    throw new RangeError('Input black must be less than input white');
  }
  finite(options.gamma, 'Gamma');
  if (options.gamma <= 0 || options.gamma > 10) {
    throw new RangeError('Gamma must be greater than 0 and at most 10');
  }
  integerInRange(options.outputBlack, 0, 254, 'Output black');
  integerInRange(options.outputWhite, 1, 255, 'Output white');
  if (options.outputBlack >= options.outputWhite) {
    throw new RangeError('Output black must be less than output white');
  }
  const inputRange = options.inputWhite - options.inputBlack;
  const outputRange = options.outputWhite - options.outputBlack;
  const map = (value: number) => {
    const normalized = Math.max(0, Math.min(1, (value - options.inputBlack) / inputRange));
    return options.outputBlack + Math.pow(normalized, 1 / options.gamma) * outputRange;
  };
  return applyRgb(
    layer,
    (red, green, blue) => [map(red), map(green), map(blue)],
    selection,
    worldTransform,
  );
}

function rgbToHsl(red: number, green: number, blue: number): [number, number, number] {
  const r = red / 255;
  const g = green / 255;
  const b = blue / 255;
  const maximum = Math.max(r, g, b);
  const minimum = Math.min(r, g, b);
  const delta = maximum - minimum;
  const lightness = (maximum + minimum) / 2;
  if (delta === 0) return [0, 0, lightness];
  const saturation = delta / (1 - Math.abs(2 * lightness - 1));
  const hue =
    maximum === r
      ? 60 * (((g - b) / delta) % 6)
      : maximum === g
        ? 60 * ((b - r) / delta + 2)
        : 60 * ((r - g) / delta + 4);
  return [(hue + 360) % 360, saturation, lightness];
}

function hslToRgb(hue: number, saturation: number, lightness: number): Rgb {
  const chroma = (1 - Math.abs(2 * lightness - 1)) * saturation;
  const sector = hue / 60;
  const secondary = chroma * (1 - Math.abs((sector % 2) - 1));
  const [r, g, b]: Rgb =
    sector < 1
      ? [chroma, secondary, 0]
      : sector < 2
        ? [secondary, chroma, 0]
        : sector < 3
          ? [0, chroma, secondary]
          : sector < 4
            ? [0, secondary, chroma]
            : sector < 5
              ? [secondary, 0, chroma]
              : [chroma, 0, secondary];
  const match = lightness - chroma / 2;
  return [(r + match) * 255, (g + match) * 255, (b + match) * 255];
}

function adjustUnit(value: number, percentage: number): number {
  const amount = percentage / 100;
  return amount >= 0 ? value + (1 - value) * amount : value * (1 + amount);
}

export function hueSaturation(
  layer: PixelLayer,
  options: HueSaturationOptions,
  selection?: SelectionMask | null,
  worldTransform: LayerTransform = layer.transform,
): PixelPatch {
  finite(options.hue, 'Hue');
  finite(options.saturation, 'Saturation');
  finite(options.lightness, 'Lightness');
  if (options.hue < -180 || options.hue > 180) {
    throw new RangeError('Hue must be between -180 and 180');
  }
  if (options.saturation < -100 || options.saturation > 100) {
    throw new RangeError('Saturation must be between -100 and 100');
  }
  if (options.lightness < -100 || options.lightness > 100) {
    throw new RangeError('Lightness must be between -100 and 100');
  }
  return applyRgb(
    layer,
    (red, green, blue) => {
      const [hue, saturation, lightness] = rgbToHsl(red, green, blue);
      return hslToRgb(
        (hue + options.hue + 360) % 360,
        adjustUnit(saturation, options.saturation),
        adjustUnit(lightness, options.lightness),
      );
    },
    selection,
    worldTransform,
  );
}

export function invert(
  layer: PixelLayer,
  selection?: SelectionMask | null,
  worldTransform: LayerTransform = layer.transform,
): PixelPatch {
  return applyRgb(
    layer,
    (red, green, blue) => [255 - red, 255 - green, 255 - blue],
    selection,
    worldTransform,
  );
}

function luminance(red: number, green: number, blue: number): number {
  return 0.2126 * red + 0.7152 * green + 0.0722 * blue;
}

export function grayscale(
  layer: PixelLayer,
  selection?: SelectionMask | null,
  worldTransform: LayerTransform = layer.transform,
): PixelPatch {
  return applyRgb(
    layer,
    (red, green, blue) => {
      const gray = luminance(red, green, blue);
      return [gray, gray, gray];
    },
    selection,
    worldTransform,
  );
}

export function threshold(
  layer: PixelLayer,
  level: number,
  selection?: SelectionMask | null,
  worldTransform: LayerTransform = layer.transform,
): PixelPatch {
  integerInRange(level, 0, 255, 'Threshold level');
  return applyRgb(
    layer,
    (red, green, blue) => {
      const value = luminance(red, green, blue) >= level ? 255 : 0;
      return [value, value, value];
    },
    selection,
    worldTransform,
  );
}

export function posterize(
  layer: PixelLayer,
  numberOfLevels: number,
  selection?: SelectionMask | null,
  worldTransform: LayerTransform = layer.transform,
): PixelPatch {
  integerInRange(numberOfLevels, 2, 256, 'Posterize levels');
  const maximum = numberOfLevels - 1;
  const map = (value: number) => (Math.round((value / 255) * maximum) / maximum) * 255;
  return applyRgb(
    layer,
    (red, green, blue) => [map(red), map(green), map(blue)],
    selection,
    worldTransform,
  );
}

/** Executes pointwise RGB filters as deterministic tile-bounded output patches. */
export function pointwiseRgbFilterPatches(
  layer: PixelLayer,
  filter: PointwiseRgbFilter,
  selection?: SelectionMask | null,
  worldTransform: LayerTransform = layer.transform,
  observer?: FilterPatchObserver,
): readonly PixelPatch[] {
  let operation: (red: number, green: number, blue: number) => Rgb;
  switch (filter.kind) {
    case 'contrast': {
      finite(filter.amount, 'Contrast amount');
      if (filter.amount < -100 || filter.amount > 100) {
        throw new RangeError('Contrast amount must be between -100 and 100');
      }
      const factor = (259 * (filter.amount + 255)) / (255 * (259 - filter.amount));
      operation = (red, green, blue) => [
        clampByte(factor * (red - 128) + 128),
        clampByte(factor * (green - 128) + 128),
        clampByte(factor * (blue - 128) + 128),
      ];
      break;
    }
    case 'levels': {
      const options = filter.options;
      integerInRange(options.inputBlack, 0, 254, 'Input black');
      integerInRange(options.inputWhite, 1, 255, 'Input white');
      if (options.inputBlack >= options.inputWhite) {
        throw new RangeError('Input black must be less than input white');
      }
      finite(options.gamma, 'Gamma');
      if (options.gamma <= 0 || options.gamma > 10) {
        throw new RangeError('Gamma must be greater than 0 and at most 10');
      }
      integerInRange(options.outputBlack, 0, 254, 'Output black');
      integerInRange(options.outputWhite, 1, 255, 'Output white');
      if (options.outputBlack >= options.outputWhite) {
        throw new RangeError('Output black must be less than output white');
      }
      const inputRange = options.inputWhite - options.inputBlack;
      const outputRange = options.outputWhite - options.outputBlack;
      const map = (value: number) => {
        const normalized = Math.max(0, Math.min(1, (value - options.inputBlack) / inputRange));
        return options.outputBlack + Math.pow(normalized, 1 / options.gamma) * outputRange;
      };
      operation = (red, green, blue) => [map(red), map(green), map(blue)];
      break;
    }
    case 'hue-saturation': {
      const options = filter.options;
      finite(options.hue, 'Hue');
      finite(options.saturation, 'Saturation');
      finite(options.lightness, 'Lightness');
      if (options.hue < -180 || options.hue > 180)
        throw new RangeError('Hue must be between -180 and 180');
      if (options.saturation < -100 || options.saturation > 100)
        throw new RangeError('Saturation must be between -100 and 100');
      if (options.lightness < -100 || options.lightness > 100)
        throw new RangeError('Lightness must be between -100 and 100');
      operation = (red, green, blue) => {
        const [hue, saturation, lightness] = rgbToHsl(red, green, blue);
        return hslToRgb(
          (hue + options.hue + 360) % 360,
          adjustUnit(saturation, options.saturation),
          adjustUnit(lightness, options.lightness),
        );
      };
      break;
    }
    case 'invert':
      operation = (red, green, blue) => [255 - red, 255 - green, 255 - blue];
      break;
    case 'grayscale':
      operation = (red, green, blue) => {
        const gray = luminance(red, green, blue);
        return [gray, gray, gray];
      };
      break;
    case 'threshold':
      integerInRange(filter.level, 0, 255, 'Threshold level');
      operation = (red, green, blue) => {
        const value = luminance(red, green, blue) >= filter.level ? 255 : 0;
        return [value, value, value];
      };
      break;
    case 'posterize': {
      integerInRange(filter.levels, 2, 256, 'Posterize levels');
      const maximum = filter.levels - 1;
      const map = (value: number) => (Math.round((value / 255) * maximum) / maximum) * 255;
      operation = (red, green, blue) => [map(red), map(green), map(blue)];
      break;
    }
  }
  return applyRgbPatches(layer, operation, selection, worldTransform, observer);
}

function gaussianKernel(radius: number): Float64Array {
  const sigma = Math.max(0.5, radius / 2);
  const kernel = new Float64Array(radius * 2 + 1);
  let sum = 0;
  for (let offset = -radius; offset <= radius; offset += 1) {
    const weight = Math.exp(-(offset * offset) / (2 * sigma * sigma));
    kernel[offset + radius] = weight;
    sum += weight;
  }
  for (let index = 0; index < kernel.length; index += 1) kernel[index] /= sum;
  return kernel;
}

function clampIndex(value: number, maximum: number): number {
  return Math.max(0, Math.min(maximum, value));
}

function blendPremultiplied(
  original: Rgb,
  originalAlpha: number,
  blurredPremultiplied: Rgb,
  blurredAlpha: number,
  coverage: number,
): readonly [number, number, number, number] {
  const sourceAlpha = originalAlpha / 255;
  const outputAlpha = sourceAlpha + (blurredAlpha - sourceAlpha) * coverage;
  const red =
    original[0] * sourceAlpha + (blurredPremultiplied[0] - original[0] * sourceAlpha) * coverage;
  const green =
    original[1] * sourceAlpha + (blurredPremultiplied[1] - original[1] * sourceAlpha) * coverage;
  const blue =
    original[2] * sourceAlpha + (blurredPremultiplied[2] - original[2] * sourceAlpha) * coverage;
  if (outputAlpha <= 0) return [0, 0, 0, 0];
  return [red / outputAlpha, green / outputAlpha, blue / outputAlpha, outputAlpha * 255];
}

/** Applies a separable Gaussian blur with edge clamping and premultiplied-alpha convolution. */
export function gaussianBlur(
  layer: PixelLayer,
  radius: number,
  selection?: SelectionMask | null,
  worldTransform: LayerTransform = layer.transform,
): PixelPatch {
  integerInRange(radius, 0, MAX_GAUSSIAN_RADIUS, 'Gaussian blur radius');
  validateContext(layer, selection, worldTransform);
  if (radius === 0) return emptyPatch();

  // Work set: sparse pixel AABB when unselected; otherwise selection AABB mapped
  // to layer and expanded by radius so the separable kernel has correct samples.
  let writeBounds: Bounds = {
    left: 0,
    top: 0,
    right: layer.width - 1,
    bottom: layer.height - 1,
  };
  let selectionCoverageBuffer: Uint8ClampedArray | null = null;
  let selectionRegion: Bounds | null = null;

  if (selection != null) {
    const documentBounds = selectionDocumentBounds(selection);
    if (documentBounds === null) return emptyPatch();
    const mapped = layerBoundsFromDocumentBounds(
      documentBounds,
      worldTransform,
      layer.width,
      layer.height,
      radius,
    );
    if (mapped === null) return emptyPatch();
    const sparse = pixelStoreBounds(layer);
    if (sparse === null) return emptyPatch();
    const influenced = {
      left: Math.max(0, sparse.left - radius),
      top: Math.max(0, sparse.top - radius),
      right: Math.min(layer.width - 1, sparse.right + radius),
      bottom: Math.min(layer.height - 1, sparse.bottom + radius),
    };
    const bounded = intersectBounds(mapped, influenced);
    if (bounded === null) return emptyPatch();
    writeBounds = bounded;
    const selectionRead = readSelectionRegion(selection, worldTransform, writeBounds);
    if (selectionRead === null) return emptyPatch();
    selectionCoverageBuffer = selectionRead.coverage;
    selectionRegion = selectionRead.region;
  } else {
    const sparse = pixelStoreBounds(layer);
    if (sparse === null) return emptyPatch();
    // Expand by radius so edge clamp has content; clamp to layer later via read.
    writeBounds = {
      left: Math.max(0, sparse.left - radius),
      top: Math.max(0, sparse.top - radius),
      right: Math.min(layer.width - 1, sparse.right + radius),
      bottom: Math.min(layer.height - 1, sparse.bottom + radius),
    };
  }

  // Read halo: write bounds expanded by radius, clamped to the layer (edge clamp).
  const readLeft = Math.max(0, writeBounds.left - radius);
  const readTop = Math.max(0, writeBounds.top - radius);
  const readRight = Math.min(layer.width - 1, writeBounds.right + radius);
  const readBottom = Math.min(layer.height - 1, writeBounds.bottom + radius);
  const readWidth = readRight - readLeft + 1;
  const readHeight = readBottom - readTop + 1;
  const pixelCount = readWidth * readHeight;
  const sourcePixels = layer.pixelStore.read({
    x: readLeft,
    y: readTop,
    width: readWidth,
    height: readHeight,
  });
  const workingBytes = pixelCount * 4 * Float32Array.BYTES_PER_ELEMENT + sourcePixels.length;
  if (!Number.isSafeInteger(workingBytes) || workingBytes > MAX_GAUSSIAN_WORKING_BYTES) {
    throw new RangeError('Gaussian blur working set would exceed the allocation limit');
  }
  const kernel = gaussianKernel(radius);
  const horizontal = new Float32Array(pixelCount * 4);

  // Map layer coords into the read buffer. write±radius is covered by the read
  // region, and edge-clamped samples stay inside [readLeft, readRight] × …
  const sampleSource = (layerX: number, layerY: number): number => {
    const clampedX = clampIndex(layerX, layer.width - 1);
    const clampedY = clampIndex(layerY, layer.height - 1);
    return ((clampedY - readTop) * readWidth + (clampedX - readLeft)) * 4;
  };

  for (let y = writeBounds.top; y <= writeBounds.bottom; y += 1) {
    for (let x = writeBounds.left; x <= writeBounds.right; x += 1) {
      const target = ((y - readTop) * readWidth + (x - readLeft)) * 4;
      for (let kernelIndex = 0; kernelIndex < kernel.length; kernelIndex += 1) {
        const sourceX = x + kernelIndex - radius;
        const source = sampleSource(sourceX, y);
        const weight = kernel[kernelIndex] ?? 0;
        const alpha = (sourcePixels[source + 3] ?? 0) / 255;
        horizontal[target] += (sourcePixels[source] ?? 0) * alpha * weight;
        horizontal[target + 1] += (sourcePixels[source + 1] ?? 0) * alpha * weight;
        horizontal[target + 2] += (sourcePixels[source + 2] ?? 0) * alpha * weight;
        horizontal[target + 3] += alpha * weight;
      }
    }
  }

  // Also need horizontal values for vertical kernel neighbors within write±radius.
  // Fill horizontal for the full read region rows that vertical pass may sample.
  for (let y = readTop; y <= readBottom; y += 1) {
    if (y >= writeBounds.top && y <= writeBounds.bottom) continue; // already filled write rows
    for (let x = writeBounds.left; x <= writeBounds.right; x += 1) {
      const target = ((y - readTop) * readWidth + (x - readLeft)) * 4;
      for (let kernelIndex = 0; kernelIndex < kernel.length; kernelIndex += 1) {
        const sourceX = x + kernelIndex - radius;
        const source = sampleSource(sourceX, y);
        const weight = kernel[kernelIndex] ?? 0;
        const alpha = (sourcePixels[source + 3] ?? 0) / 255;
        horizontal[target] += (sourcePixels[source] ?? 0) * alpha * weight;
        horizontal[target + 1] += (sourcePixels[source + 1] ?? 0) * alpha * weight;
        horizontal[target + 2] += (sourcePixels[source + 2] ?? 0) * alpha * weight;
        horizontal[target + 3] += alpha * weight;
      }
    }
  }

  let bounds: Bounds | null = null;
  const patchWidth = writeBounds.right - writeBounds.left + 1;
  const patchHeight = writeBounds.bottom - writeBounds.top + 1;
  const patchPixels = new Uint8ClampedArray(patchWidth * patchHeight * 4);
  // Seed patch with original pixels so uncovered/unchanged stay correct.
  for (let y = writeBounds.top; y <= writeBounds.bottom; y += 1) {
    for (let x = writeBounds.left; x <= writeBounds.right; x += 1) {
      const src = sampleSource(x, y);
      const dst = ((y - writeBounds.top) * patchWidth + (x - writeBounds.left)) * 4;
      patchPixels[dst] = sourcePixels[src] ?? 0;
      patchPixels[dst + 1] = sourcePixels[src + 1] ?? 0;
      patchPixels[dst + 2] = sourcePixels[src + 2] ?? 0;
      patchPixels[dst + 3] = sourcePixels[src + 3] ?? 0;
    }
  }

  for (let y = writeBounds.top; y <= writeBounds.bottom; y += 1) {
    for (let x = writeBounds.left; x <= writeBounds.right; x += 1) {
      let red = 0;
      let green = 0;
      let blue = 0;
      let alpha = 0;
      for (let kernelIndex = 0; kernelIndex < kernel.length; kernelIndex += 1) {
        const sourceY = clampIndex(y + kernelIndex - radius, layer.height - 1);
        // Horizontal pass stored for write-column x at sourceY when sourceY is in read range.
        const hy = clampIndex(sourceY - readTop, readHeight - 1);
        const hx = clampIndex(x - readLeft, readWidth - 1);
        const source = (hy * readWidth + hx) * 4;
        const weight = kernel[kernelIndex] ?? 0;
        red += (horizontal[source] ?? 0) * weight;
        green += (horizontal[source + 1] ?? 0) * weight;
        blue += (horizontal[source + 2] ?? 0) * weight;
        alpha += (horizontal[source + 3] ?? 0) * weight;
      }
      const coverage = selectionCoverage(
        selection,
        worldTransform,
        x,
        y,
        selectionCoverageBuffer,
        selectionRegion,
      );
      if (coverage <= 0) continue;
      const src = sampleSource(x, y);
      const output = blendPremultiplied(
        [sourcePixels[src] ?? 0, sourcePixels[src + 1] ?? 0, sourcePixels[src + 2] ?? 0],
        sourcePixels[src + 3] ?? 0,
        [red, green, blue],
        alpha,
        coverage,
      );
      const rounded: readonly [number, number, number, number] = [
        Math.round(output[0]),
        Math.round(output[1]),
        Math.round(output[2]),
        Math.round(output[3]),
      ];
      if (
        rounded[0] === sourcePixels[src] &&
        rounded[1] === sourcePixels[src + 1] &&
        rounded[2] === sourcePixels[src + 2] &&
        rounded[3] === sourcePixels[src + 3]
      ) {
        continue;
      }
      const dst = ((y - writeBounds.top) * patchWidth + (x - writeBounds.left)) * 4;
      patchPixels[dst] = rounded[0];
      patchPixels[dst + 1] = rounded[1];
      patchPixels[dst + 2] = rounded[2];
      patchPixels[dst + 3] = rounded[3];
      bounds = include(bounds, x, y);
    }
  }
  if (bounds === null) return emptyPatch();
  // Trim patch to changed bounds.
  const outW = bounds.right - bounds.left + 1;
  const outH = bounds.bottom - bounds.top + 1;
  const pixels = new Uint8ClampedArray(outW * outH * 4);
  for (let row = 0; row < outH; row += 1) {
    const srcY = bounds.top + row - writeBounds.top;
    const srcX = bounds.left - writeBounds.left;
    const srcOffset = (srcY * patchWidth + srcX) * 4;
    pixels.set(patchPixels.subarray(srcOffset, srcOffset + outW * 4), row * outW * 4);
  }
  return { x: bounds.left, y: bounds.top, width: outW, height: outH, pixels };
}

/**
 * Executes Gaussian Blur as destination-tile patches. Each tile reads only its
 * radius halo and delegates to the reference kernel, then crops back to the core.
 */
export function gaussianBlurPatches(
  layer: PixelLayer,
  radius: number,
  selection?: SelectionMask | null,
  worldTransform: LayerTransform = layer.transform,
  observer?: FilterPatchObserver,
): readonly PixelPatch[] {
  integerInRange(radius, 0, MAX_GAUSSIAN_RADIUS, 'Gaussian blur radius');
  validateContext(layer, selection, worldTransform);
  if (radius === 0) return [];

  let work: Bounds;
  if (selection != null) {
    const documentBounds = selectionDocumentBounds(selection);
    if (documentBounds === null) return [];
    const mapped = layerBoundsFromDocumentBounds(
      documentBounds,
      worldTransform,
      layer.width,
      layer.height,
      radius,
    );
    if (mapped === null) return [];
    const sparse = pixelStoreBounds(layer);
    if (sparse === null) return [];
    const influenced = {
      left: Math.max(0, sparse.left - radius),
      top: Math.max(0, sparse.top - radius),
      right: Math.min(layer.width - 1, sparse.right + radius),
      bottom: Math.min(layer.height - 1, sparse.bottom + radius),
    };
    const bounded = intersectBounds(mapped, influenced);
    if (bounded === null) return [];
    work = bounded;
  } else {
    const sparse = pixelStoreBounds(layer);
    if (sparse === null) return [];
    work = {
      left: Math.max(0, sparse.left - radius),
      top: Math.max(0, sparse.top - radius),
      right: Math.min(layer.width - 1, sparse.right + radius),
      bottom: Math.min(layer.height - 1, sparse.bottom + radius),
    };
  }

  const patches: PixelPatch[] = [];
  const tileSize = layer.pixelStore.tileSize;
  const startX = Math.floor(work.left / tileSize) * tileSize;
  const startY = Math.floor(work.top / tileSize) * tileSize;
  const totalTiles =
    (Math.floor(work.right / tileSize) - Math.floor(work.left / tileSize) + 1) *
    (Math.floor(work.bottom / tileSize) - Math.floor(work.top / tileSize) + 1);
  let completedTiles = 0;
  for (let tileY = startY; tileY <= work.bottom; tileY += tileSize) {
    const coreTop = Math.max(work.top, tileY);
    const coreBottom = Math.min(work.bottom, tileY + tileSize - 1);
    for (let tileX = startX; tileX <= work.right; tileX += tileSize) {
      const coreLeft = Math.max(work.left, tileX);
      const coreRight = Math.min(work.right, tileX + tileSize - 1);
      const sourceLeft = Math.max(0, coreLeft - radius);
      const sourceTop = Math.max(0, coreTop - radius);
      const sourceRight = Math.min(layer.width - 1, coreRight + radius);
      const sourceBottom = Math.min(layer.height - 1, coreBottom + radius);
      const sourceWidth = sourceRight - sourceLeft + 1;
      const sourceHeight = sourceBottom - sourceTop + 1;
      const sourcePixels = layer.pixelStore.read({
        x: sourceLeft,
        y: sourceTop,
        width: sourceWidth,
        height: sourceHeight,
      });
      const localWorldTransform: LayerTransform = {
        ...worldTransform,
        e: worldTransform.a * sourceLeft + worldTransform.c * sourceTop + worldTransform.e,
        f: worldTransform.b * sourceLeft + worldTransform.d * sourceTop + worldTransform.f,
      };
      const localLayer: PixelLayer = {
        ...layer,
        width: sourceWidth,
        height: sourceHeight,
        transform: localWorldTransform,
        pixelStore: TiledPixelStore.fromContiguous(
          sourceWidth,
          sourceHeight,
          sourcePixels,
          tileSize,
        ),
      };
      const local = gaussianBlur(localLayer, radius, selection, localWorldTransform);
      if (local.width === 0) {
        completedTiles += 1;
        observer?.onProgress?.(completedTiles, totalTiles);
        continue;
      }
      const coreLocalLeft = coreLeft - sourceLeft;
      const coreLocalTop = coreTop - sourceTop;
      const intersectionLeft = Math.max(local.x, coreLocalLeft);
      const intersectionTop = Math.max(local.y, coreLocalTop);
      const intersectionRight = Math.min(local.x + local.width - 1, coreRight - sourceLeft);
      const intersectionBottom = Math.min(local.y + local.height - 1, coreBottom - sourceTop);
      if (intersectionRight < intersectionLeft || intersectionBottom < intersectionTop) {
        completedTiles += 1;
        observer?.onProgress?.(completedTiles, totalTiles);
        continue;
      }
      const width = intersectionRight - intersectionLeft + 1;
      const height = intersectionBottom - intersectionTop + 1;
      const pixels = new Uint8ClampedArray(width * height * 4);
      const localPixels = new Uint8ClampedArray(local.pixels);
      for (let row = 0; row < height; row += 1) {
        const sourceOffset =
          ((intersectionTop + row - local.y) * local.width + intersectionLeft - local.x) * 4;
        pixels.set(localPixels.subarray(sourceOffset, sourceOffset + width * 4), row * width * 4);
      }
      const patch = {
        x: sourceLeft + intersectionLeft,
        y: sourceTop + intersectionTop,
        width,
        height,
        pixels,
      };
      patches.push(patch);
      observer?.onPatch?.(patch);
      completedTiles += 1;
      observer?.onProgress?.(completedTiles, totalTiles);
    }
  }
  return patches;
}
