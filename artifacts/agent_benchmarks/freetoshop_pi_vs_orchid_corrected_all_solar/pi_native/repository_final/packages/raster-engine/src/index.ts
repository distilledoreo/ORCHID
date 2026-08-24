import {
  createAlphaChannel,
  createSelectionMask,
  type DocumentTransform,
  type ImageDocument,
  type Layer,
  type LayerTransform,
  type PathCommand,
  type PixelLayer,
  type PixelPatch,
  type RasterMask,
  type SelectionMask,
} from '@freetoshop/core-document';
import { TiledCoverageStore, TiledPixelStore } from '@freetoshop/pixel-storage';

export type ResamplingMethod = 'nearest' | 'bilinear';

export interface ResizeImageOptions {
  readonly width: number;
  readonly height: number;
  readonly method: ResamplingMethod;
}

export interface BrushPoint {
  /** Document-space x coordinate. */
  readonly x: number;
  /** Document-space y coordinate. */
  readonly y: number;
  /** Optional normalized tablet pressure. Defaults to 1. */
  readonly pressure?: number;
}

export interface BrushOptions {
  readonly mode: 'source-over' | 'erase';
  /** Diameter in document pixels. */
  readonly size: number;
  /** Normalized hard-core radius. */
  readonly hardness: number;
  readonly opacity: number;
  readonly color: readonly [number, number, number, number];
}

export interface MaskBrushOptions {
  readonly mode: 'reveal' | 'conceal';
  /** Diameter in document pixels. A size of zero is a no-op. */
  readonly size: number;
  /** Normalized hard-core radius. */
  readonly hardness: number;
  readonly opacity: number;
}

/** Clone Stamp source state and sampling parameters. */
export interface CloneSource {
  /** Identifier of the source document. Must match the active editing document during a stroke. */
  readonly documentId: string;
  /** Layer from which pixels are sampled. */
  readonly layerId: string;
  /** Document-space origin used to establish the sampling anchor. */
  readonly originX: number;
  readonly originY: number;
  /** When true, the source-to-destination offset established at origin time is preserved for the whole stroke. */
  readonly aligned: boolean;
}

/** Explicit representation of whether a valid source exists and, when present, how sampling is configured. */
export interface CloneState {
  readonly hasSource: boolean;
  readonly source: CloneSource | null;
}

/** Rectangle of source pixels that must be read for a tile write to remain tile-bounded. */
export interface CloneSourceRegion {
  readonly x: number;
  readonly y: number;
  readonly width: number;
  readonly height: number;
  /** Local RGBA pixels from the source layer in the region above. */
  readonly pixels: Uint8ClampedArray;
}

/** Destination patch produced by a clone stroke. Unlike a brush patch, its pixels come from the source layer. */
export interface ClonePatch extends PixelPatch {}

export interface CoveragePatch {
  readonly x: number;
  readonly y: number;
  readonly width: number;
  readonly height: number;
  readonly coverage: Uint8ClampedArray;
}

export interface PixelPatch {
  readonly x: number;
  readonly y: number;
  readonly width: number;
  readonly height: number;
  readonly pixels: Uint8ClampedArray;
}

export interface ClonePatch {
  readonly x: number;
  readonly y: number;
  readonly width: number;
  readonly height: number;
  readonly pixels: Uint8ClampedArray;
}

export interface CloneSourceRegion {
  readonly x: number;
  readonly y: number;
  readonly width: number;
  readonly height: number;
  readonly pixels: Uint8ClampedArray;
}

export function emptyClonePatch(): ClonePatch {
  return { x: 0, y: 0, width: 0, height: 0, pixels: new Uint8ClampedArray() };
}

export function emptyCloneSourceRegion(): CloneSourceRegion {
  return { x: 0, y: 0, width: 0, height: 0, pixels: new Uint8ClampedArray() };
}

interface Bounds {
  left: number;
  top: number;
  right: number;
  bottom: number;
}

const MAX_IMAGE_DIMENSION = 32_768;
const MAX_IMAGE_PIXELS = 100_000_000;
const MAX_RESIZE_WORKING_BYTES = 1_000_000_000;

function validateOutputSize(width: number, height: number, label = 'Image'): void {
  if (!Number.isSafeInteger(width) || width <= 0) {
    throw new RangeError(`${label} width must be a positive integer`);
  }
  if (!Number.isSafeInteger(height) || height <= 0) {
    throw new RangeError(`${label} height must be a positive integer`);
  }
  if (width > MAX_IMAGE_DIMENSION || height > MAX_IMAGE_DIMENSION) {
    throw new RangeError(`${label} dimensions cannot exceed ${String(MAX_IMAGE_DIMENSION)} pixels`);
  }
  if (!Number.isSafeInteger(width * height) || width * height > MAX_IMAGE_PIXELS) {
    throw new RangeError(`${label} area cannot exceed ${String(MAX_IMAGE_PIXELS)} pixels`);
  }
}

function validateResamplingMethod(method: string): asserts method is ResamplingMethod {
  if (method !== 'nearest' && method !== 'bilinear') {
    throw new RangeError('Resampling method must be nearest or bilinear');
  }
}

function sourceCoordinate(
  destination: number,
  sourceSize: number,
  destinationSize: number,
): number {
  return ((destination + 0.5) * sourceSize) / destinationSize - 0.5;
}

function clampIndex(value: number, size: number): number {
  return Math.max(0, Math.min(size - 1, value));
}

/** Resamples single-channel 8-bit coverage at pixel centers. */
export function resampleCoverage(
  source: ArrayLike<number>,
  sourceWidth: number,
  sourceHeight: number,
  destinationWidth: number,
  destinationHeight: number,
  method: ResamplingMethod,
): Uint8ClampedArray {
  validateOutputSize(sourceWidth, sourceHeight, 'Source coverage');
  validateOutputSize(destinationWidth, destinationHeight, 'Destination coverage');
  if (source.length !== sourceWidth * sourceHeight) {
    throw new RangeError('Coverage buffer length does not match its dimensions');
  }
  validateResamplingMethod(method);
  const result = new Uint8ClampedArray(destinationWidth * destinationHeight);
  for (let y = 0; y < destinationHeight; y += 1) {
    const sourceY = sourceCoordinate(y, sourceHeight, destinationHeight);
    if (method === 'nearest') {
      const sampleY = clampIndex(Math.round(sourceY), sourceHeight);
      for (let x = 0; x < destinationWidth; x += 1) {
        const sampleX = clampIndex(
          Math.round(sourceCoordinate(x, sourceWidth, destinationWidth)),
          sourceWidth,
        );
        result[y * destinationWidth + x] = source[sampleY * sourceWidth + sampleX] ?? 0;
      }
      continue;
    }
    const y0 = Math.floor(sourceY);
    const y1 = y0 + 1;
    const yWeight = sourceY - y0;
    for (let x = 0; x < destinationWidth; x += 1) {
      const sourceX = sourceCoordinate(x, sourceWidth, destinationWidth);
      const x0 = Math.floor(sourceX);
      const x1 = x0 + 1;
      const xWeight = sourceX - x0;
      const sample = (sampleX: number, sampleY: number) =>
        source[
          clampIndex(sampleY, sourceHeight) * sourceWidth + clampIndex(sampleX, sourceWidth)
        ] ?? 0;
      const top = sample(x0, y0) * (1 - xWeight) + sample(x1, y0) * xWeight;
      const bottom = sample(x0, y1) * (1 - xWeight) + sample(x1, y1) * xWeight;
      result[y * destinationWidth + x] = Math.round(top * (1 - yWeight) + bottom * yWeight);
    }
  }
  return result;
}

function destinationTileKeys(
  sourceTiles: Iterable<{ x: number; y: number; width: number; height: number }>,
  sourceWidth: number,
  sourceHeight: number,
  destinationWidth: number,
  destinationHeight: number,
  destinationTileSize: number,
): number[] {
  const columnCount = Math.ceil(destinationWidth / destinationTileSize);
  const keys = new Set<number>();
  for (const tile of sourceTiles) {
    const left = Math.max(0, Math.floor((tile.x * destinationWidth) / sourceWidth) - 1);
    const top = Math.max(0, Math.floor((tile.y * destinationHeight) / sourceHeight) - 1);
    const right = Math.min(
      destinationWidth - 1,
      Math.ceil(((tile.x + tile.width) * destinationWidth) / sourceWidth) + 1,
    );
    const bottom = Math.min(
      destinationHeight - 1,
      Math.ceil(((tile.y + tile.height) * destinationHeight) / sourceHeight) + 1,
    );
    for (
      let row = Math.floor(top / destinationTileSize);
      row <= Math.floor(bottom / destinationTileSize);
      row += 1
    ) {
      for (
        let column = Math.floor(left / destinationTileSize);
        column <= Math.floor(right / destinationTileSize);
        column += 1
      ) {
        keys.add(row * columnCount + column);
      }
    }
  }
  return [...keys].sort((left, right) => left - right);
}

function destinationTileRegion(
  key: number,
  width: number,
  height: number,
  tileSize: number,
): { x: number; y: number; width: number; height: number } {
  const columnCount = Math.ceil(width / tileSize);
  const column = key % columnCount;
  const row = Math.floor(key / columnCount);
  const x = column * tileSize;
  const y = row * tileSize;
  return {
    x,
    y,
    width: Math.min(tileSize, width - x),
    height: Math.min(tileSize, height - y),
  };
}

function coverageStoreSample(store: TiledCoverageStore, x: number, y: number): number {
  const sampleX = clampIndex(x, store.width);
  const sampleY = clampIndex(y, store.height);
  const tile = store.getTile(
    Math.floor(sampleX / store.tileSize),
    Math.floor(sampleY / store.tileSize),
  );
  if (tile === null) return store.defaultCoverage;
  return tile.coverage[(sampleY - tile.y) * tile.width + sampleX - tile.x] ?? store.defaultCoverage;
}

/** Resamples coverage one destination tile at a time without a source-span intermediate. */
export function resampleCoverageStore(
  store: TiledCoverageStore,
  destinationWidth: number,
  destinationHeight: number,
  method: ResamplingMethod,
): TiledCoverageStore {
  validateOutputSize(store.width, store.height, 'Source coverage');
  validateOutputSize(destinationWidth, destinationHeight, 'Destination coverage');
  validateResamplingMethod(method);
  let result = TiledCoverageStore.uniform(
    destinationWidth,
    destinationHeight,
    store.defaultCoverage,
    store.tileSize,
  );
  for (const key of destinationTileKeys(
    store.tiles(),
    store.width,
    store.height,
    destinationWidth,
    destinationHeight,
    result.tileSize,
  )) {
    const region = destinationTileRegion(key, destinationWidth, destinationHeight, result.tileSize);
    const coverage = new Uint8ClampedArray(region.width * region.height);
    for (let y = 0; y < region.height; y += 1) {
      const destinationY = region.y + y;
      const sourceY = sourceCoordinate(destinationY, store.height, destinationHeight);
      for (let x = 0; x < region.width; x += 1) {
        const destinationX = region.x + x;
        const sourceX = sourceCoordinate(destinationX, store.width, destinationWidth);
        if (method === 'nearest') {
          coverage[y * region.width + x] = coverageStoreSample(
            store,
            Math.round(sourceX),
            Math.round(sourceY),
          );
          continue;
        }
        const x0 = Math.floor(sourceX);
        const y0 = Math.floor(sourceY);
        const xWeight = sourceX - x0;
        const yWeight = sourceY - y0;
        const top =
          coverageStoreSample(store, x0, y0) * (1 - xWeight) +
          coverageStoreSample(store, x0 + 1, y0) * xWeight;
        const bottom =
          coverageStoreSample(store, x0, y0 + 1) * (1 - xWeight) +
          coverageStoreSample(store, x0 + 1, y0 + 1) * xWeight;
        coverage[y * region.width + x] = Math.round(top * (1 - yWeight) + bottom * yWeight);
      }
    }
    result = result.write({ ...region, coverage }).store;
  }
  return result;
}

/** Resamples straight-alpha RGBA without introducing transparent-color fringes. */
export function resamplePixels(
  source: ArrayLike<number>,
  sourceWidth: number,
  sourceHeight: number,
  destinationWidth: number,
  destinationHeight: number,
  method: ResamplingMethod,
): Uint8ClampedArray {
  validateOutputSize(sourceWidth, sourceHeight, 'Source image');
  validateOutputSize(destinationWidth, destinationHeight, 'Destination image');
  if (source.length !== sourceWidth * sourceHeight * 4) {
    throw new RangeError('Pixel buffer length does not match its dimensions');
  }
  validateResamplingMethod(method);
  const result = new Uint8ClampedArray(destinationWidth * destinationHeight * 4);
  for (let y = 0; y < destinationHeight; y += 1) {
    const sourceY = sourceCoordinate(y, sourceHeight, destinationHeight);
    for (let x = 0; x < destinationWidth; x += 1) {
      const sourceX = sourceCoordinate(x, sourceWidth, destinationWidth);
      const destinationOffset = (y * destinationWidth + x) * 4;
      if (method === 'nearest') {
        const sampleX = clampIndex(Math.round(sourceX), sourceWidth);
        const sampleY = clampIndex(Math.round(sourceY), sourceHeight);
        const sourceOffset = (sampleY * sourceWidth + sampleX) * 4;
        for (let channel = 0; channel < 4; channel += 1) {
          result[destinationOffset + channel] = source[sourceOffset + channel] ?? 0;
        }
        continue;
      }
      const x0 = Math.floor(sourceX);
      const y0 = Math.floor(sourceY);
      const xWeight = sourceX - x0;
      const yWeight = sourceY - y0;
      const samples = [
        { x: x0, y: y0, weight: (1 - xWeight) * (1 - yWeight) },
        { x: x0 + 1, y: y0, weight: xWeight * (1 - yWeight) },
        { x: x0, y: y0 + 1, weight: (1 - xWeight) * yWeight },
        { x: x0 + 1, y: y0 + 1, weight: xWeight * yWeight },
      ];
      let alpha = 0;
      const premultiplied = [0, 0, 0];
      for (const sample of samples) {
        const sampleOffset =
          (clampIndex(sample.y, sourceHeight) * sourceWidth + clampIndex(sample.x, sourceWidth)) *
          4;
        const sampleAlpha = (source[sampleOffset + 3] ?? 0) / 255;
        alpha += sampleAlpha * sample.weight;
        for (let channel = 0; channel < 3; channel += 1) {
          premultiplied[channel] =
            (premultiplied[channel] ?? 0) +
            (source[sampleOffset + channel] ?? 0) * sampleAlpha * sample.weight;
        }
      }
      result[destinationOffset + 3] = Math.round(alpha * 255);
      for (let channel = 0; channel < 3; channel += 1) {
        result[destinationOffset + channel] =
          alpha === 0 ? 0 : Math.round((premultiplied[channel] ?? 0) / alpha);
      }
    }
  }
  return result;
}

function pixelStoreSample(store: TiledPixelStore, x: number, y: number, channel: number): number {
  const sampleX = clampIndex(x, store.width);
  const sampleY = clampIndex(y, store.height);
  const tile = store.getTile(
    Math.floor(sampleX / store.tileSize),
    Math.floor(sampleY / store.tileSize),
  );
  if (tile === null) return 0;
  return tile.pixels[((sampleY - tile.y) * tile.width + sampleX - tile.x) * 4 + channel] ?? 0;
}

/**
 * Resamples a tiled pixel store one destination tile at a time. Sampling reads
 * canonical source tiles directly, so distant sparse tiles never create a dense span.
 */
export function resamplePixelStore(
  store: TiledPixelStore,
  destinationWidth: number,
  destinationHeight: number,
  method: ResamplingMethod,
): TiledPixelStore {
  validateOutputSize(store.width, store.height, 'Source image');
  validateOutputSize(destinationWidth, destinationHeight, 'Destination image');
  validateResamplingMethod(method);
  let result = TiledPixelStore.empty(destinationWidth, destinationHeight, store.tileSize);
  for (const key of destinationTileKeys(
    store.tiles(),
    store.width,
    store.height,
    destinationWidth,
    destinationHeight,
    result.tileSize,
  )) {
    const region = destinationTileRegion(key, destinationWidth, destinationHeight, result.tileSize);
    const pixels = new Uint8ClampedArray(region.width * region.height * 4);
    for (let y = 0; y < region.height; y += 1) {
      const sourceY = sourceCoordinate(region.y + y, store.height, destinationHeight);
      for (let x = 0; x < region.width; x += 1) {
        const sourceX = sourceCoordinate(region.x + x, store.width, destinationWidth);
        const destinationOffset = (y * region.width + x) * 4;
        if (method === 'nearest') {
          for (let channel = 0; channel < 4; channel += 1) {
            pixels[destinationOffset + channel] = pixelStoreSample(
              store,
              Math.round(sourceX),
              Math.round(sourceY),
              channel,
            );
          }
          continue;
        }
        const x0 = Math.floor(sourceX);
        const y0 = Math.floor(sourceY);
        const xWeight = sourceX - x0;
        const yWeight = sourceY - y0;
        const samples = [
          { x: x0, y: y0, weight: (1 - xWeight) * (1 - yWeight) },
          { x: x0 + 1, y: y0, weight: xWeight * (1 - yWeight) },
          { x: x0, y: y0 + 1, weight: (1 - xWeight) * yWeight },
          { x: x0 + 1, y: y0 + 1, weight: xWeight * yWeight },
        ];
        let alpha = 0;
        const premultiplied = [0, 0, 0];
        for (const sample of samples) {
          const sampleAlpha = pixelStoreSample(store, sample.x, sample.y, 3) / 255;
          alpha += sampleAlpha * sample.weight;
          for (let channel = 0; channel < 3; channel += 1) {
            premultiplied[channel] =
              (premultiplied[channel] ?? 0) +
              pixelStoreSample(store, sample.x, sample.y, channel) * sampleAlpha * sample.weight;
          }
        }
        pixels[destinationOffset + 3] = Math.round(alpha * 255);
        for (let channel = 0; channel < 3; channel += 1) {
          pixels[destinationOffset + channel] =
            alpha === 0 ? 0 : Math.round((premultiplied[channel] ?? 0) / alpha);
        }
      }
    }
    result = result.write({ ...region, pixels }).store;
  }
  return result;
}

function multiplyTransforms(left: LayerTransform, right: LayerTransform): LayerTransform {
  return {
    a: left.a * right.a + left.c * right.b,
    b: left.b * right.a + left.d * right.b,
    c: left.a * right.c + left.c * right.d,
    d: left.b * right.c + left.d * right.d,
    e: left.a * right.e + left.c * right.f + left.e,
    f: left.b * right.e + left.d * right.f + left.f,
  };
}

function scalePathCommand(command: PathCommand, scaleX: number, scaleY: number): PathCommand {
  if (command.type === 'close') return { type: 'close' };
  if (command.type === 'move' || command.type === 'line') {
    return { type: command.type, x: command.x * scaleX, y: command.y * scaleY };
  }
  return {
    type: 'cubic',
    control1X: command.control1X * scaleX,
    control1Y: command.control1Y * scaleY,
    control2X: command.control2X * scaleX,
    control2Y: command.control2Y * scaleY,
    x: command.x * scaleX,
    y: command.y * scaleY,
  };
}

function resizeLayer(
  layer: Layer,
  documentScale: LayerTransform,
  scaleX: number,
  scaleY: number,
  method: ResamplingMethod,
  isRoot: boolean,
): Layer {
  if (layer.kind === 'group') {
    return {
      ...layer,
      transform: isRoot ? multiplyTransforms(documentScale, layer.transform) : layer.transform,
      rasterMask:
        layer.rasterMask === null
          ? null
          : {
              ...layer.rasterMask,
              width: Math.max(1, Math.round(layer.rasterMask.width * scaleX)),
              height: Math.max(1, Math.round(layer.rasterMask.height * scaleY)),
              coverageStore: resampleCoverageStore(
                layer.rasterMask.coverageStore,
                Math.max(1, Math.round(layer.rasterMask.width * scaleX)),
                Math.max(1, Math.round(layer.rasterMask.height * scaleY)),
                method,
              ),
            },
      vectorMask:
        layer.vectorMask === null
          ? null
          : {
              ...layer.vectorMask,
              width: Math.max(1, Math.round(layer.vectorMask.width * scaleX)),
              height: Math.max(1, Math.round(layer.vectorMask.height * scaleY)),
              commands: layer.vectorMask.commands.map((command) =>
                scalePathCommand(command, scaleX, scaleY),
              ),
            },
      children: layer.children.map((child) =>
        resizeLayer(child, documentScale, scaleX, scaleY, method, false),
      ),
    };
  }
  if (layer.kind === 'adjustment') {
    return {
      ...layer,
      transform: isRoot ? multiplyTransforms(documentScale, layer.transform) : layer.transform,
      rasterMask:
        layer.rasterMask === null
          ? null
          : {
              ...layer.rasterMask,
              width: Math.max(1, Math.round(layer.rasterMask.width * scaleX)),
              height: Math.max(1, Math.round(layer.rasterMask.height * scaleY)),
              coverageStore: resampleCoverageStore(
                layer.rasterMask.coverageStore,
                Math.max(1, Math.round(layer.rasterMask.width * scaleX)),
                Math.max(1, Math.round(layer.rasterMask.height * scaleY)),
                method,
              ),
            },
      vectorMask:
        layer.vectorMask === null
          ? null
          : {
              ...layer.vectorMask,
              width: Math.max(1, Math.round(layer.vectorMask.width * scaleX)),
              height: Math.max(1, Math.round(layer.vectorMask.height * scaleY)),
              commands: layer.vectorMask.commands.map((command) =>
                scalePathCommand(command, scaleX, scaleY),
              ),
            },
    };
  }
  if (layer.kind === 'shape' || layer.kind === 'text' || layer.kind === 'smart-object') {
    return {
      ...layer,
      transform: isRoot ? multiplyTransforms(documentScale, layer.transform) : layer.transform,
    };
  }
  const width = Math.max(1, Math.round(layer.width * scaleX));
  const height = Math.max(1, Math.round(layer.height * scaleY));
  validateOutputSize(width, height, `Layer ${layer.name}`);
  const localInverseScale: LayerTransform = {
    a: layer.width / width,
    b: 0,
    c: 0,
    d: layer.height / height,
    e: 0,
    f: 0,
  };
  return {
    ...layer,
    width,
    height,
    pixelStore: resamplePixelStore(layer.pixelStore, width, height, method),
    transform: multiplyTransforms(
      isRoot ? documentScale : { a: 1, b: 0, c: 0, d: 1, e: 0, f: 0 },
      multiplyTransforms(layer.transform, localInverseScale),
    ),
    rasterMask: layer.rasterMask
      ? {
          ...layer.rasterMask,
          width,
          height,
          coverageStore: resampleCoverageStore(
            layer.rasterMask.coverageStore,
            width,
            height,
            method,
          ),
        }
      : null,
    vectorMask: layer.vectorMask
      ? {
          ...layer.vectorMask,
          width,
          height,
          commands: layer.vectorMask.commands.map((command) =>
            scalePathCommand(command, width / layer.width, height / layer.height),
          ),
        }
      : null,
  };
}

function estimateLayerResizeBytes(
  layers: readonly Layer[],
  scaleX: number,
  scaleY: number,
): number {
  let bytes = 0;
  for (const layer of layers) {
    if (layer.kind === 'group') {
      bytes += estimateLayerResizeBytes(layer.children, scaleX, scaleY);
      continue;
    }
    if (layer.kind === 'adjustment') {
      if (layer.rasterMask !== null) {
        const width = Math.max(1, Math.round(layer.rasterMask.width * scaleX));
        const height = Math.max(1, Math.round(layer.rasterMask.height * scaleY));
        validateOutputSize(width, height, `Layer ${layer.name} mask`);
        bytes += width * height;
      }
      continue;
    }
    if (layer.kind === 'shape' || layer.kind === 'text' || layer.kind === 'smart-object') continue;
    const width = Math.max(1, Math.round(layer.width * scaleX));
    const height = Math.max(1, Math.round(layer.height * scaleY));
    validateOutputSize(width, height, `Layer ${layer.name}`);
    bytes += width * height * (layer.rasterMask ? 5 : 4);
    if (!Number.isSafeInteger(bytes) || bytes > MAX_RESIZE_WORKING_BYTES) {
      throw new RangeError(
        `Resize working set cannot exceed ${String(MAX_RESIZE_WORKING_BYTES)} bytes`,
      );
    }
  }
  return bytes;
}

/** Resamples all raster content and document-space masks as one undoable image-size operation. */
export function resizeImage(options: ResizeImageOptions): DocumentTransform {
  validateOutputSize(options.width, options.height);
  validateResamplingMethod(options.method);
  const ownedOptions = { ...options };
  return {
    label: 'Resize image',
    execute(document: ImageDocument): ImageDocument {
      if (
        ownedOptions.width === document.canvas.width &&
        ownedOptions.height === document.canvas.height
      ) {
        return document;
      }
      const scaleX = ownedOptions.width / document.canvas.width;
      const scaleY = ownedOptions.height / document.canvas.height;
      const documentScale: LayerTransform = {
        a: scaleX,
        b: 0,
        c: 0,
        d: scaleY,
        e: 0,
        f: 0,
      };
      const coverageBytes =
        ownedOptions.width *
        ownedOptions.height *
        (document.channels.length + (document.selection === null ? 0 : 1));
      const workingBytes =
        estimateLayerResizeBytes(document.layers, scaleX, scaleY) + coverageBytes;
      if (!Number.isSafeInteger(workingBytes) || workingBytes > MAX_RESIZE_WORKING_BYTES) {
        throw new RangeError(
          `Resize working set cannot exceed ${String(MAX_RESIZE_WORKING_BYTES)} bytes`,
        );
      }
      return {
        ...document,
        canvas: { width: ownedOptions.width, height: ownedOptions.height },
        layers: document.layers.map((layer) =>
          resizeLayer(layer, documentScale, scaleX, scaleY, ownedOptions.method, true),
        ),
        selection:
          document.selection === null
            ? null
            : createSelectionMask({
                width: ownedOptions.width,
                height: ownedOptions.height,
                // Sparse-aware resample: empty stays empty; active bounds only densify.
                coverageStore: resampleCoverageStore(
                  document.selection.coverageStore,
                  ownedOptions.width,
                  ownedOptions.height,
                  ownedOptions.method,
                ),
              }),
        channels: document.channels.map((channel) =>
          createAlphaChannel({
            id: channel.id,
            name: channel.name,
            width: ownedOptions.width,
            height: ownedOptions.height,
            coverageStore: resampleCoverageStore(
              channel.coverageStore,
              ownedOptions.width,
              ownedOptions.height,
              ownedOptions.method,
            ),
          }),
        ),
      };
    },
  };
}

function assertFinite(value: number, name: string): void {
  if (!Number.isFinite(value)) throw new RangeError(`${name} must be finite`);
}

function assertUnit(value: number, name: string): void {
  assertFinite(value, name);
  if (value < 0 || value > 1) throw new RangeError(`${name} must be between 0 and 1`);
}

function validateLayer(layer: PixelLayer): void {
  if (!Number.isSafeInteger(layer.width) || layer.width <= 0) {
    throw new RangeError('Layer width must be a positive integer');
  }
  if (!Number.isSafeInteger(layer.height) || layer.height <= 0) {
    throw new RangeError('Layer height must be a positive integer');
  }
  if (layer.pixelStore.width !== layer.width || layer.pixelStore.height !== layer.height) {
    throw new RangeError('Layer pixel buffer length does not match its dimensions');
  }
  validateTransform(layer.transform);
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

function validateRasterMask(mask: RasterMask): void {
  if (!Number.isSafeInteger(mask.width) || mask.width <= 0) {
    throw new RangeError('Mask width must be a positive integer');
  }
  if (!Number.isSafeInteger(mask.height) || mask.height <= 0) {
    throw new RangeError('Mask height must be a positive integer');
  }
  if (
    !(mask.coverageStore instanceof TiledCoverageStore) ||
    mask.coverageStore.width !== mask.width ||
    mask.coverageStore.height !== mask.height
  ) {
    throw new RangeError('Mask coverage length does not match its dimensions');
  }
  assertUnit(mask.density, 'Mask density');
}

function validateTransform(transform: LayerTransform): void {
  for (const component of ['a', 'b', 'c', 'd', 'e', 'f'] as const) {
    assertFinite(transform[component], `Layer transform ${component}`);
  }
  const determinant = transform.a * transform.d - transform.b * transform.c;
  if (!Number.isFinite(determinant) || determinant === 0) {
    throw new RangeError('Layer transform must be invertible');
  }
  const inverseComponents = [
    transform.d / determinant,
    -transform.b / determinant,
    -transform.c / determinant,
    transform.a / determinant,
    (transform.c * transform.f - transform.d * transform.e) / determinant,
    (transform.b * transform.e - transform.a * transform.f) / determinant,
  ];
  if (inverseComponents.some((component) => !Number.isFinite(component))) {
    throw new RangeError('Layer transform must be invertible');
  }
}

interface Point {
  readonly x: number;
  readonly y: number;
}

function transformPoint(transform: LayerTransform, x: number, y: number): Point {
  return {
    x: transform.a * x + transform.c * y + transform.e,
    y: transform.b * x + transform.d * y + transform.f,
  };
}

function invertTransform(transform: LayerTransform): LayerTransform {
  const determinant = transform.a * transform.d - transform.b * transform.c;
  return {
    a: transform.d / determinant,
    b: -transform.b / determinant,
    c: -transform.c / determinant,
    d: transform.a / determinant,
    e: (transform.c * transform.f - transform.d * transform.e) / determinant,
    f: (transform.b * transform.e - transform.a * transform.f) / determinant,
  };
}

function selectionCoverage(
  selection: SelectionMask | null | undefined,
  documentX: number,
  documentY: number,
  regionBuffer?: Uint8ClampedArray | null,
  region?: Bounds | null,
): number {
  if (selection == null) return 1;
  const sampleX = Math.floor(documentX);
  const sampleY = Math.floor(documentY);
  if (sampleX < 0 || sampleY < 0 || sampleX >= selection.width || sampleY >= selection.height) {
    return 0;
  }
  if (regionBuffer && region) {
    const localX = sampleX - region.left;
    const localY = sampleY - region.top;
    if (
      localX < 0 ||
      localY < 0 ||
      localX > region.right - region.left ||
      localY > region.bottom - region.top
    ) {
      return 0;
    }
    const width = region.right - region.left + 1;
    return (regionBuffer[localY * width + localX] ?? 0) / 255;
  }
  // Single-pixel sparse sample; avoids full-document densify for isolated queries.
  const sample = selection.coverageStore.read({ x: sampleX, y: sampleY, width: 1, height: 1 });
  const value = sample[0];
  return (typeof value === 'number' ? value : 0) / 255;
}

function unionBounds(left: Bounds | null, right: Bounds): Bounds {
  if (left === null) return { ...right };
  return {
    left: Math.min(left.left, right.left),
    top: Math.min(left.top, right.top),
    right: Math.max(left.right, right.right),
    bottom: Math.max(left.bottom, right.bottom),
  };
}

function documentDabBounds(
  documentX: number,
  documentY: number,
  radius: number,
  canvasWidth: number,
  canvasHeight: number,
): Bounds {
  return {
    left: Math.max(0, Math.floor(documentX - radius)),
    top: Math.max(0, Math.floor(documentY - radius)),
    right: Math.min(canvasWidth - 1, Math.ceil(documentX + radius)),
    bottom: Math.min(canvasHeight - 1, Math.ceil(documentY + radius)),
  };
}

function readSelectionRegion(
  selection: SelectionMask | null | undefined,
  dabs: readonly { x: number; y: number }[],
  radius: number,
): { buffer: Uint8ClampedArray | null; region: Bounds | null } {
  if (selection == null || dabs.length === 0) return { buffer: null, region: null };
  let bounds: Bounds | null = null;
  for (const dab of dabs) {
    bounds = unionBounds(
      bounds,
      documentDabBounds(dab.x, dab.y, radius, selection.width, selection.height),
    );
  }
  if (bounds === null || bounds.right < bounds.left || bounds.bottom < bounds.top) {
    return { buffer: null, region: null };
  }
  const width = bounds.right - bounds.left + 1;
  const height = bounds.bottom - bounds.top + 1;
  return {
    buffer: selection.coverageStore.read({
      x: bounds.left,
      y: bounds.top,
      width,
      height,
    }),
    region: bounds,
  };
}

function makePatchFromRegion(
  regionLeft: number,
  regionTop: number,
  regionWidth: number,
  pixels: Uint8ClampedArray,
  bounds: Bounds | null,
): PixelPatch {
  if (bounds === null) return emptyPatch();
  const width = bounds.right - bounds.left + 1;
  const height = bounds.bottom - bounds.top + 1;
  const patchPixels = new Uint8ClampedArray(width * height * 4);
  for (let row = 0; row < height; row += 1) {
    const sourceY = bounds.top - regionTop + row;
    const sourceStart = (sourceY * regionWidth + (bounds.left - regionLeft)) * 4;
    patchPixels.set(pixels.subarray(sourceStart, sourceStart + width * 4), row * width * 4);
  }
  return { x: bounds.left, y: bounds.top, width, height, pixels: patchPixels };
}

function makeCoveragePatchFromRegion(
  regionLeft: number,
  regionTop: number,
  regionWidth: number,
  coverage: Uint8ClampedArray,
  bounds: Bounds | null,
): CoveragePatch {
  if (bounds === null) return emptyCoveragePatch();
  const width = bounds.right - bounds.left + 1;
  const height = bounds.bottom - bounds.top + 1;
  const patchCoverage = new Uint8ClampedArray(width * height);
  for (let row = 0; row < height; row += 1) {
    const sourceY = bounds.top - regionTop + row;
    const sourceStart = sourceY * regionWidth + (bounds.left - regionLeft);
    patchCoverage.set(coverage.subarray(sourceStart, sourceStart + width), row * width);
  }
  return { x: bounds.left, y: bounds.top, width, height, coverage: patchCoverage };
}

function localDabBounds(
  transform: LayerTransform,
  inverse: LayerTransform,
  documentX: number,
  documentY: number,
  radius: number,
  width: number,
  height: number,
): Bounds {
  const center = transformPoint(inverse, documentX, documentY);
  // The inverse linear rows project a document-space circle into a local-space ellipse.
  // Their Euclidean lengths give tight axis-aligned extents for that ellipse.
  const extentX = radius * Math.hypot(inverse.a, inverse.c);
  const extentY = radius * Math.hypot(inverse.b, inverse.d);
  return {
    left: Math.max(0, Math.ceil(center.x - extentX)),
    top: Math.max(0, Math.ceil(center.y - extentY)),
    right: Math.min(width - 1, Math.floor(center.x + extentX)),
    bottom: Math.min(height - 1, Math.floor(center.y + extentY)),
  };
}

function emptyPatch(): PixelPatch {
  return { x: 0, y: 0, width: 0, height: 0, pixels: new Uint8ClampedArray() };
}

function emptyCoveragePatch(): CoveragePatch {
  return { x: 0, y: 0, width: 0, height: 0, coverage: new Uint8ClampedArray() };
}

function include(bounds: Bounds | null, x: number, y: number): Bounds {
  if (bounds === null) return { left: x, top: y, right: x, bottom: y };
  bounds.left = Math.min(bounds.left, x);
  bounds.top = Math.min(bounds.top, y);
  bounds.right = Math.max(bounds.right, x);
  bounds.bottom = Math.max(bounds.bottom, y);
  return bounds;
}

function compositeSourceOver(
  pixels: Uint8ClampedArray,
  offset: number,
  color: readonly [number, number, number, number],
  coverage: number,
): void {
  const sourceAlpha = (color[3] / 255) * coverage;
  const destinationAlpha = (pixels[offset + 3] ?? 0) / 255;
  const outputAlpha = sourceAlpha + destinationAlpha * (1 - sourceAlpha);
  if (outputAlpha === 0) return;
  for (let channel = 0; channel < 3; channel += 1) {
    const source = color[channel] ?? 0;
    const destination = pixels[offset + channel] ?? 0;
    pixels[offset + channel] = Math.round(
      (source * sourceAlpha + destination * destinationAlpha * (1 - sourceAlpha)) / outputAlpha,
    );
  }
  pixels[offset + 3] = Math.round(outputAlpha * 255);
}

function dabCoverage(distance: number, radius: number, hardness: number): number {
  if (distance > radius) return 0;
  const hardRadius = radius * hardness;
  if (distance <= hardRadius || hardRadius === radius) return 1;
  return 1 - (distance - hardRadius) / (radius - hardRadius);
}

/**
 * Paints a deterministic mask stroke in document space and returns the smallest mask-local
 * coverage patch containing changed samples. The input mask and its coverage remain untouched.
 */
export function paintMaskStroke(
  mask: RasterMask,
  layerTransform: LayerTransform,
  points: readonly BrushPoint[],
  options: MaskBrushOptions,
  selection?: SelectionMask | null,
): CoveragePatch {
  validateRasterMask(mask);
  validateTransform(layerTransform);
  validateSelection(selection);
  assertFinite(options.size, 'Mask brush size');
  if (options.size < 0) throw new RangeError('Mask brush size must be at least 0');
  assertUnit(options.hardness, 'Mask brush hardness');
  assertUnit(options.opacity, 'Mask brush opacity');
  const mode: unknown = options.mode;
  if (mode !== 'reveal' && mode !== 'conceal') {
    throw new RangeError('Mask brush mode must be reveal or conceal');
  }
  for (const [index, point] of points.entries()) {
    assertFinite(point.x, `Mask stroke point ${String(index)} x`);
    assertFinite(point.y, `Mask stroke point ${String(index)} y`);
    assertUnit(point.pressure ?? 1, `Mask stroke point ${String(index)} pressure`);
  }
  if (points.length === 0 || options.size === 0 || options.opacity === 0) {
    return emptyCoveragePatch();
  }

  const radius = options.size / 2;
  const inverse = invertTransform(layerTransform);
  const spacing = Math.max(0.25, radius * 0.5);
  const first = points[0];
  const dabs: Array<{ x: number; y: number; pressure: number }> = [
    { x: first.x, y: first.y, pressure: first.pressure ?? 1 },
  ];
  for (let index = 1; index < points.length; index += 1) {
    const start = points[index - 1];
    const end = points[index];
    const distance = Math.hypot(end.x - start.x, end.y - start.y);
    const steps = Math.max(1, Math.ceil(distance / spacing));
    for (let step = 1; step <= steps; step += 1) {
      const progress = step / steps;
      dabs.push({
        x: start.x + (end.x - start.x) * progress,
        y: start.y + (end.y - start.y) * progress,
        pressure: (start.pressure ?? 1) + ((end.pressure ?? 1) - (start.pressure ?? 1)) * progress,
      });
    }
  }

  // Stroke working set: union of local dab bounds only (no full-mask densify).
  let strokeBounds: Bounds | null = null;
  for (const dab of dabs) {
    strokeBounds = unionBounds(
      strokeBounds,
      localDabBounds(layerTransform, inverse, dab.x, dab.y, radius, mask.width, mask.height),
    );
  }
  if (strokeBounds === null || strokeBounds.right < strokeBounds.left) {
    return emptyCoveragePatch();
  }
  const regionLeft = strokeBounds.left;
  const regionTop = strokeBounds.top;
  const regionWidth = strokeBounds.right - strokeBounds.left + 1;
  const regionHeight = strokeBounds.bottom - strokeBounds.top + 1;
  const next = mask.coverageStore.read({
    x: regionLeft,
    y: regionTop,
    width: regionWidth,
    height: regionHeight,
  });
  const selectionRegion = readSelectionRegion(selection, dabs, radius);
  let changedBounds: Bounds | null = null;

  for (const dab of dabs) {
    const bounds = localDabBounds(
      layerTransform,
      inverse,
      dab.x,
      dab.y,
      radius,
      mask.width,
      mask.height,
    );
    const { left, top, right, bottom } = bounds;
    for (let y = top; y <= bottom; y += 1) {
      for (let x = left; x <= right; x += 1) {
        const documentPoint = transformPoint(layerTransform, x, y);
        const falloff = dabCoverage(
          Math.hypot(documentPoint.x - dab.x, documentPoint.y - dab.y),
          radius,
          options.hardness,
        );
        const selected = selectionCoverage(
          selection,
          documentPoint.x,
          documentPoint.y,
          selectionRegion.buffer,
          selectionRegion.region,
        );
        const strength = falloff * options.opacity * dab.pressure * selected;
        if (strength <= 0) continue;
        const offset = (y - regionTop) * regionWidth + (x - regionLeft);
        const original = next[offset] ?? 0;
        const target = options.mode === 'reveal' ? 255 : 0;
        const value = Math.round(original + (target - original) * strength);
        if (value === original) continue;
        next[offset] = value;
        changedBounds = include(changedBounds, x, y);
      }
    }
  }

  return makeCoveragePatchFromRegion(regionLeft, regionTop, regionWidth, next, changedBounds);
}

/**
 * Paints a deterministic, continuously interpolated document-space stroke and returns the
 * smallest layer-local patch containing changed pixels. A no-op returns a 0 x 0 patch.
 */
export function paintStroke(
  layer: PixelLayer,
  points: readonly BrushPoint[],
  options: BrushOptions,
  selection?: SelectionMask | null,
  worldTransform: LayerTransform = layer.transform,
): PixelPatch {
  validateLayer(layer);
  validateTransform(worldTransform);
  validateSelection(selection);
  if (points.length === 0) throw new RangeError('Stroke must contain at least one point');
  assertFinite(options.size, 'Brush size');
  if (options.size <= 0) throw new RangeError('Brush size must be greater than 0');
  assertUnit(options.hardness, 'Brush hardness');
  assertUnit(options.opacity, 'Brush opacity');
  const mode: unknown = options.mode;
  if (mode !== 'source-over' && mode !== 'erase') {
    throw new RangeError('Brush mode must be source-over or erase');
  }
  const color: readonly unknown[] = options.color;
  if (color.length !== 4) throw new RangeError('Brush color must contain four channels');
  for (const [index, channel] of color.entries()) {
    if (typeof channel !== 'number' || !Number.isInteger(channel) || channel < 0 || channel > 255) {
      throw new RangeError(`Brush color channel ${String(index)} must be an integer from 0 to 255`);
    }
  }
  for (const [index, point] of points.entries()) {
    assertFinite(point.x, `Stroke point ${String(index)} x`);
    assertFinite(point.y, `Stroke point ${String(index)} y`);
    assertUnit(point.pressure ?? 1, `Stroke point ${String(index)} pressure`);
  }

  const radius = options.size / 2;
  const inverse = invertTransform(worldTransform);
  const spacing = Math.max(0.25, radius * 0.5);
  const dabs: Array<{ x: number; y: number; pressure: number }> = [];
  const first = points[0];
  dabs.push({ x: first.x, y: first.y, pressure: first.pressure ?? 1 });
  for (let index = 1; index < points.length; index += 1) {
    const start = points[index - 1];
    const end = points[index];
    const distance = Math.hypot(end.x - start.x, end.y - start.y);
    const steps = Math.max(1, Math.ceil(distance / spacing));
    for (let step = 1; step <= steps; step += 1) {
      const progress = step / steps;
      dabs.push({
        x: start.x + (end.x - start.x) * progress,
        y: start.y + (end.y - start.y) * progress,
        pressure: (start.pressure ?? 1) + ((end.pressure ?? 1) - (start.pressure ?? 1)) * progress,
      });
    }
  }

  // Stroke working set: union of local dab bounds only (no full-layer densify).
  let strokeBounds: Bounds | null = null;
  for (const dab of dabs) {
    strokeBounds = unionBounds(
      strokeBounds,
      localDabBounds(worldTransform, inverse, dab.x, dab.y, radius, layer.width, layer.height),
    );
  }
  if (strokeBounds === null || strokeBounds.right < strokeBounds.left) {
    return emptyPatch();
  }
  const regionLeft = strokeBounds.left;
  const regionTop = strokeBounds.top;
  const regionWidth = strokeBounds.right - strokeBounds.left + 1;
  const regionHeight = strokeBounds.bottom - strokeBounds.top + 1;
  const next = layer.pixelStore.read({
    x: regionLeft,
    y: regionTop,
    width: regionWidth,
    height: regionHeight,
  });
  const selectionRegion = readSelectionRegion(selection, dabs, radius);
  let changedBounds: Bounds | null = null;

  for (const dab of dabs) {
    const bounds = localDabBounds(
      worldTransform,
      inverse,
      dab.x,
      dab.y,
      radius,
      layer.width,
      layer.height,
    );
    const { left, top, right, bottom } = bounds;
    for (let y = top; y <= bottom; y += 1) {
      for (let x = left; x <= right; x += 1) {
        const documentPoint = transformPoint(worldTransform, x, y);
        const falloff = dabCoverage(
          Math.hypot(documentPoint.x - dab.x, documentPoint.y - dab.y),
          radius,
          options.hardness,
        );
        const selected = selectionCoverage(
          selection,
          documentPoint.x,
          documentPoint.y,
          selectionRegion.buffer,
          selectionRegion.region,
        );
        const coverage = falloff * options.opacity * dab.pressure * selected;
        if (coverage <= 0) continue;
        const offset = ((y - regionTop) * regionWidth + (x - regionLeft)) * 4;
        const before = [next[offset], next[offset + 1], next[offset + 2], next[offset + 3]];
        if (options.mode === 'erase') {
          next[offset + 3] = Math.round((next[offset + 3] ?? 0) * (1 - coverage));
        } else {
          compositeSourceOver(next, offset, options.color, coverage);
        }
        if (
          before[0] !== next[offset] ||
          before[1] !== next[offset + 1] ||
          before[2] !== next[offset + 2] ||
          before[3] !== next[offset + 3]
        ) {
          changedBounds = include(changedBounds, x, y);
        }
      }
    }
  }
  return makePatchFromRegion(regionLeft, regionTop, regionWidth, next, changedBounds);
}

function coverageStoreBounds(store: TiledCoverageStore): Bounds | null {
  if (store.defaultCoverage !== 0) {
    return { left: 0, top: 0, right: store.width - 1, bottom: store.height - 1 };
  }
  let minX = store.width;
  let minY = store.height;
  let maxX = -1;
  let maxY = -1;
  for (const tile of store.tiles()) {
    minX = Math.min(minX, tile.x);
    minY = Math.min(minY, tile.y);
    maxX = Math.max(maxX, tile.x + tile.width - 1);
    maxY = Math.max(maxY, tile.y + tile.height - 1);
  }
  if (maxX < minX || maxY < minY) return null;
  return { left: minX, top: minY, right: maxX, bottom: maxY };
}

function layerBoundsFromDocumentBounds(
  documentBounds: Bounds,
  inverse: LayerTransform,
  layerWidth: number,
  layerHeight: number,
): Bounds | null {
  const corners = [
    transformPoint(inverse, documentBounds.left, documentBounds.top),
    transformPoint(inverse, documentBounds.right + 1, documentBounds.top),
    transformPoint(inverse, documentBounds.left, documentBounds.bottom + 1),
    transformPoint(inverse, documentBounds.right + 1, documentBounds.bottom + 1),
  ];
  let minX = Number.POSITIVE_INFINITY;
  let minY = Number.POSITIVE_INFINITY;
  let maxX = Number.NEGATIVE_INFINITY;
  let maxY = Number.NEGATIVE_INFINITY;
  for (const corner of corners) {
    minX = Math.min(minX, corner.x);
    minY = Math.min(minY, corner.y);
    maxX = Math.max(maxX, corner.x);
    maxY = Math.max(maxY, corner.y);
  }
  // Expand by one pixel for inverse-map aliasing of soft coverage edges.
  const left = Math.max(0, Math.floor(minX) - 1);
  const top = Math.max(0, Math.floor(minY) - 1);
  const right = Math.min(layerWidth - 1, Math.ceil(maxX) + 1);
  const bottom = Math.min(layerHeight - 1, Math.ceil(maxY) + 1);
  if (right < left || bottom < top) return null;
  return { left, top, right, bottom };
}

/**
 * Adjusts RGB channels by -255..255, blended through selection coverage.
 * With a sparse selection, only the inverse-mapped selection bounds are densified
 * (not the full layer). Null selection still processes the full layer (full-frame edit).
 */
export function adjustBrightness(
  layer: PixelLayer,
  amount: number,
  selection?: SelectionMask | null,
  worldTransform: LayerTransform = layer.transform,
): PixelPatch {
  validateLayer(layer);
  validateTransform(worldTransform);
  validateSelection(selection);
  if (!Number.isInteger(amount) || amount < -255 || amount > 255) {
    throw new RangeError('Brightness amount must be an integer between -255 and 255');
  }
  if (amount === 0) return emptyPatch();

  const inverse = invertTransform(worldTransform);
  let workBounds: Bounds | null;
  let selectionBuffer: Uint8ClampedArray | null = null;
  let selectionRegion: Bounds | null = null;

  if (selection == null) {
    // Full-layer adjust: entire layer is the working set by definition.
    workBounds = {
      left: 0,
      top: 0,
      right: layer.width - 1,
      bottom: layer.height - 1,
    };
  } else {
    const documentBounds = coverageStoreBounds(selection.coverageStore);
    if (documentBounds === null) return emptyPatch();
    workBounds = layerBoundsFromDocumentBounds(documentBounds, inverse, layer.width, layer.height);
    if (workBounds === null) return emptyPatch();
    const selWidth = documentBounds.right - documentBounds.left + 1;
    const selHeight = documentBounds.bottom - documentBounds.top + 1;
    selectionBuffer = selection.coverageStore.read({
      x: documentBounds.left,
      y: documentBounds.top,
      width: selWidth,
      height: selHeight,
    });
    selectionRegion = documentBounds;
  }

  const regionLeft = workBounds.left;
  const regionTop = workBounds.top;
  const regionWidth = workBounds.right - workBounds.left + 1;
  const regionHeight = workBounds.bottom - workBounds.top + 1;
  const next = layer.pixelStore.read({
    x: regionLeft,
    y: regionTop,
    width: regionWidth,
    height: regionHeight,
  });
  let changedBounds: Bounds | null = null;

  for (let y = workBounds.top; y <= workBounds.bottom; y += 1) {
    for (let x = workBounds.left; x <= workBounds.right; x += 1) {
      const documentPoint = transformPoint(worldTransform, x, y);
      const coverage = selectionCoverage(
        selection,
        documentPoint.x,
        documentPoint.y,
        selectionBuffer,
        selectionRegion,
      );
      if (coverage <= 0) continue;
      const offset = ((y - regionTop) * regionWidth + (x - regionLeft)) * 4;
      let changed = false;
      for (let channel = 0; channel < 3; channel += 1) {
        const original = next[offset + channel] ?? 0;
        const adjusted = Math.max(0, Math.min(255, original + amount));
        const value = Math.round(original + (adjusted - original) * coverage);
        if (value !== original) changed = true;
        next[offset + channel] = value;
      }
      if (changed) changedBounds = include(changedBounds, x, y);
    }
  }
  return makePatchFromRegion(regionLeft, regionTop, regionWidth, next, changedBounds);
}

/**
 * Applies Brightness directly into an immutable canonical tile store. Full-layer
 * edits visit one destination tile at a time, avoiding the rectangular PixelPatch
 * allocation while preserving hidden RGB and transparent-pixel semantics exactly.
 * Selection-bounded edits reuse the compact patch implementation above.
 */
export function adjustBrightnessPixelStore(
  layer: PixelLayer,
  amount: number,
  selection?: SelectionMask | null,
  worldTransform: LayerTransform = layer.transform,
): TiledPixelStore {
  validateLayer(layer);
  validateTransform(worldTransform);
  validateSelection(selection);
  if (!Number.isInteger(amount) || amount < -255 || amount > 255) {
    throw new RangeError('Brightness amount must be an integer between -255 and 255');
  }
  if (amount === 0) return layer.pixelStore;

  if (selection != null) {
    const patch = adjustBrightness(layer, amount, selection, worldTransform);
    if (patch.width === 0) return layer.pixelStore;
    return layer.pixelStore.write({ ...patch, pixels: new Uint8ClampedArray(patch.pixels) }).store;
  }

  let result = layer.pixelStore;
  const tileSize = layer.pixelStore.tileSize;
  for (let y = 0; y < layer.height; y += tileSize) {
    const height = Math.min(tileSize, layer.height - y);
    for (let x = 0; x < layer.width; x += tileSize) {
      const width = Math.min(tileSize, layer.width - x);
      const source = layer.pixelStore.read({ x, y, width, height });
      const pixels = new Uint8ClampedArray(source);
      let changed = false;
      for (let offset = 0; offset < pixels.length; offset += 4) {
        for (let channel = 0; channel < 3; channel += 1) {
          const original = source[offset + channel] ?? 0;
          const adjusted = Math.max(0, Math.min(255, original + amount));
          if (adjusted !== original) changed = true;
          pixels[offset + channel] = adjusted;
        }
      }
      if (changed) result = result.write({ x, y, width, height, pixels }).store;
    }
  }
  return result;
}
