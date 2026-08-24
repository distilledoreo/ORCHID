import type { SelectionMask, SelectionRectangle } from '@freetoshop/core-document';
import { TiledCoverageStore, type PixelRegion } from '@freetoshop/pixel-storage';

export interface SelectionPoint {
  readonly x: number;
  readonly y: number;
}

export interface MagicWandOptions {
  readonly pixels: Uint8ClampedArray;
  readonly width: number;
  readonly height: number;
  readonly seedX: number;
  readonly seedY: number;
  /** Root-mean-square byte distance from the seed color, from 0 through 255. */
  readonly tolerance: number;
  /** Restrict matching pixels to the seed's four-connected region. */
  readonly contiguous: boolean;
  /** Include alpha as a fourth component in color distance. */
  readonly sampleAlpha: boolean;
}

export interface MagicWandPixelSource {
  readonly width: number;
  readonly height: number;
  read(region: PixelRegion): Uint8ClampedArray;
}

export interface MagicWandSourceOptions
  extends Omit<MagicWandOptions, 'pixels' | 'width' | 'height'> {
  readonly source: MagicWandPixelSource;
}

const MAX_SELECTION_DIMENSION = 32_768;
const MAX_SELECTION_PIXELS = 100_000_000;
const VECTOR_SUPERSAMPLING = 4;

function validateDimensions(width: number, height: number): number {
  if (!Number.isSafeInteger(width) || width <= 0) {
    throw new RangeError('Selection width must be a positive integer');
  }
  if (!Number.isSafeInteger(height) || height <= 0) {
    throw new RangeError('Selection height must be a positive integer');
  }
  if (width > MAX_SELECTION_DIMENSION || height > MAX_SELECTION_DIMENSION) {
    throw new RangeError(
      `Selection dimensions cannot exceed ${String(MAX_SELECTION_DIMENSION)} pixels`,
    );
  }
  const area = width * height;
  if (!Number.isSafeInteger(area) || area > MAX_SELECTION_PIXELS) {
    throw new RangeError(`Selection area cannot exceed ${String(MAX_SELECTION_PIXELS)} pixels`);
  }
  return area;
}

function allocateCoverage(area: number): Uint8ClampedArray {
  try {
    return new Uint8ClampedArray(area);
  } catch {
    throw new RangeError('Selection allocation failed');
  }
}

function validateMask(mask: SelectionMask): number {
  const area = validateDimensions(mask.width, mask.height);
  if (
    !(mask.coverageStore instanceof TiledCoverageStore) ||
    mask.coverageStore.width !== mask.width ||
    mask.coverageStore.height !== mask.height
  ) {
    throw new RangeError('Selection coverage must match its dimensions');
  }
  return area;
}

interface CoverageBounds {
  readonly x: number;
  readonly y: number;
  readonly width: number;
  readonly height: number;
}

function expandBounds(
  bounds: CoverageBounds,
  radius: number,
  canvasWidth: number,
  canvasHeight: number,
): CoverageBounds {
  const x = Math.max(0, bounds.x - radius);
  const y = Math.max(0, bounds.y - radius);
  const right = Math.min(canvasWidth, bounds.x + bounds.width + radius);
  const bottom = Math.min(canvasHeight, bounds.y + bounds.height + radius);
  return { x, y, width: right - x, height: bottom - y };
}

function emptySelection(width: number, height: number): SelectionMask {
  return {
    width,
    height,
    coverageStore: TiledCoverageStore.empty(width, height),
  };
}

function tileRegion(store: TiledCoverageStore, column: number, row: number): CoverageBounds {
  const x = column * store.tileSize;
  const y = row * store.tileSize;
  return {
    x,
    y,
    width: Math.min(store.tileSize, store.width - x),
    height: Math.min(store.tileSize, store.height - y),
  };
}

function addCandidateTiles(
  candidates: Set<number>,
  store: TiledCoverageStore,
  region: CoverageBounds,
): void {
  const clipped = expandBounds(region, 0, store.width, store.height);
  if (clipped.width <= 0 || clipped.height <= 0) return;
  const firstColumn = Math.floor(clipped.x / store.tileSize);
  const lastColumn = Math.floor((clipped.x + clipped.width - 1) / store.tileSize);
  const firstRow = Math.floor(clipped.y / store.tileSize);
  const lastRow = Math.floor((clipped.y + clipped.height - 1) / store.tileSize);
  for (let row = firstRow; row <= lastRow; row += 1) {
    for (let column = firstColumn; column <= lastColumn; column += 1) {
      candidates.add(row * store.columnCount + column);
    }
  }
}

function neighborhoodCandidateTiles(
  store: TiledCoverageStore,
  radius: number,
  includeCanvasEdges: boolean,
): readonly number[] {
  const candidates = new Set<number>();
  for (const tile of store.tiles()) {
    addCandidateTiles(candidates, store, expandBounds(tile, radius, store.width, store.height));
  }
  if (includeCanvasEdges && store.defaultCoverage !== 0) {
    const thickness = Math.min(radius, Math.max(store.width, store.height));
    addCandidateTiles(candidates, store, { x: 0, y: 0, width: store.width, height: thickness });
    addCandidateTiles(candidates, store, {
      x: 0,
      y: Math.max(0, store.height - thickness),
      width: store.width,
      height: thickness,
    });
    addCandidateTiles(candidates, store, { x: 0, y: 0, width: thickness, height: store.height });
    addCandidateTiles(candidates, store, {
      x: Math.max(0, store.width - thickness),
      y: 0,
      width: thickness,
      height: store.height,
    });
  }
  return [...candidates].sort((left, right) => left - right);
}

function applyNeighborhoodFilter(
  mask: SelectionMask,
  radius: number,
  includeCanvasEdges: boolean,
  filter: (source: Uint8ClampedArray, width: number, height: number) => Uint8ClampedArray,
): SelectionMask {
  const sourceStore = mask.coverageStore;
  let result = TiledCoverageStore.uniform(
    mask.width,
    mask.height,
    sourceStore.defaultCoverage,
    sourceStore.tileSize,
  );
  for (const key of neighborhoodCandidateTiles(sourceStore, radius, includeCanvasEdges)) {
    const column = key % sourceStore.columnCount;
    const row = Math.floor(key / sourceStore.columnCount);
    const destination = tileRegion(sourceStore, column, row);
    const source = expandBounds(destination, radius, mask.width, mask.height);
    const filtered = filter(sourceStore.read(source), source.width, source.height);
    const coverage = allocateCoverage(destination.width * destination.height);
    const offsetX = destination.x - source.x;
    const offsetY = destination.y - source.y;
    for (let y = 0; y < destination.height; y += 1) {
      const start = (offsetY + y) * source.width + offsetX;
      coverage.set(filtered.subarray(start, start + destination.width), y * destination.width);
    }
    result = result.write({ ...destination, coverage }).store;
  }
  return { width: mask.width, height: mask.height, coverageStore: result };
}

function cloneSelectionSparse(mask: SelectionMask): SelectionMask {
  validateMask(mask);
  return {
    width: mask.width,
    height: mask.height,
    coverageStore: TiledCoverageStore.fromSnapshot(mask.coverageStore.snapshot()),
  };
}

function validateFinite(value: number, label: string): void {
  if (!Number.isFinite(value)) throw new RangeError(`${label} must be finite`);
}

function validateRadius(radius: number, label: string, allowZero = true): void {
  if (!Number.isSafeInteger(radius) || radius < (allowZero ? 0 : 1)) {
    throw new RangeError(`${label} must be ${allowZero ? 'a non-negative' : 'a positive'} integer`);
  }
  if (radius > MAX_SELECTION_DIMENSION) {
    throw new RangeError(`${label} cannot exceed ${String(MAX_SELECTION_DIMENSION)} pixels`);
  }
}

function selection(width: number, height: number, coverage: Uint8ClampedArray): SelectionMask {
  return {
    width,
    height,
    coverageStore: TiledCoverageStore.fromContiguous(width, height, coverage),
  };
}

function sampledCoverage(predicate: (x: number, y: number) => boolean): number {
  let inside = 0;
  for (let sampleY = 0; sampleY < VECTOR_SUPERSAMPLING; sampleY += 1) {
    for (let sampleX = 0; sampleX < VECTOR_SUPERSAMPLING; sampleX += 1) {
      if (
        predicate((sampleX + 0.5) / VECTOR_SUPERSAMPLING, (sampleY + 0.5) / VECTOR_SUPERSAMPLING)
      ) {
        inside += 1;
      }
    }
  }
  return Math.round((inside * 255) / (VECTOR_SUPERSAMPLING * VECTOR_SUPERSAMPLING));
}

/** Creates a four-times-supersampled ellipse selection in document coordinates. */
export function ellipticalSelection(
  width: number,
  height: number,
  rectangle: SelectionRectangle,
): SelectionMask {
  const area = validateDimensions(width, height);
  validateFinite(rectangle.x, 'Ellipse x');
  validateFinite(rectangle.y, 'Ellipse y');
  validateFinite(rectangle.width, 'Ellipse width');
  validateFinite(rectangle.height, 'Ellipse height');
  if (rectangle.width <= 0 || rectangle.height <= 0) {
    throw new RangeError('Ellipse dimensions must be positive');
  }
  const coverage = allocateCoverage(area);
  const centerX = rectangle.x + rectangle.width / 2;
  const centerY = rectangle.y + rectangle.height / 2;
  const radiusX = rectangle.width / 2;
  const radiusY = rectangle.height / 2;
  const left = Math.max(0, Math.floor(rectangle.x));
  const top = Math.max(0, Math.floor(rectangle.y));
  const right = Math.min(width, Math.ceil(rectangle.x + rectangle.width));
  const bottom = Math.min(height, Math.ceil(rectangle.y + rectangle.height));
  for (let y = top; y < bottom; y += 1) {
    for (let x = left; x < right; x += 1) {
      coverage[y * width + x] = sampledCoverage((offsetX, offsetY) => {
        const normalizedX = (x + offsetX - centerX) / radiusX;
        const normalizedY = (y + offsetY - centerY) / radiusY;
        return normalizedX * normalizedX + normalizedY * normalizedY <= 1;
      });
    }
  }
  return selection(width, height, coverage);
}

function pointOnSegment(x: number, y: number, start: SelectionPoint, end: SelectionPoint): boolean {
  const cross = (x - start.x) * (end.y - start.y) - (y - start.y) * (end.x - start.x);
  if (Math.abs(cross) > 1e-10) return false;
  return (
    x >= Math.min(start.x, end.x) &&
    x <= Math.max(start.x, end.x) &&
    y >= Math.min(start.y, end.y) &&
    y <= Math.max(start.y, end.y)
  );
}

function pointInPolygon(x: number, y: number, points: readonly SelectionPoint[]): boolean {
  let inside = false;
  for (let index = 0, previous = points.length - 1; index < points.length; previous = index++) {
    const start = points[previous];
    const end = points[index];
    if (pointOnSegment(x, y, start, end)) return true;
    if (
      start.y > y !== end.y > y &&
      x < ((end.x - start.x) * (y - start.y)) / (end.y - start.y) + start.x
    ) {
      inside = !inside;
    }
  }
  return inside;
}

/** Creates a four-times-supersampled, even-odd polygon selection. */
export function polygonalSelection(
  width: number,
  height: number,
  points: readonly SelectionPoint[],
): SelectionMask {
  const area = validateDimensions(width, height);
  if (points.length < 3) throw new RangeError('Polygon must contain at least three points');
  if (points.length > MAX_SELECTION_PIXELS) {
    throw new RangeError(`Polygon cannot contain more than ${String(MAX_SELECTION_PIXELS)} points`);
  }
  let minimumX = Number.POSITIVE_INFINITY;
  let minimumY = Number.POSITIVE_INFINITY;
  let maximumX = Number.NEGATIVE_INFINITY;
  let maximumY = Number.NEGATIVE_INFINITY;
  for (const point of points) {
    validateFinite(point.x, 'Polygon point x');
    validateFinite(point.y, 'Polygon point y');
    minimumX = Math.min(minimumX, point.x);
    minimumY = Math.min(minimumY, point.y);
    maximumX = Math.max(maximumX, point.x);
    maximumY = Math.max(maximumY, point.y);
  }
  const coverage = allocateCoverage(area);
  const left = Math.max(0, Math.floor(minimumX));
  const top = Math.max(0, Math.floor(minimumY));
  const right = Math.min(width, Math.ceil(maximumX));
  const bottom = Math.min(height, Math.ceil(maximumY));
  for (let y = top; y < bottom; y += 1) {
    for (let x = left; x < right; x += 1) {
      coverage[y * width + x] = sampledCoverage((offsetX, offsetY) =>
        pointInPolygon(x + offsetX, y + offsetY, points),
      );
    }
  }
  return selection(width, height, coverage);
}

function validateMagicWandOptions(options: MagicWandOptions): number {
  const area = validateDimensions(options.width, options.height);
  if (options.pixels.length !== area * 4) {
    throw new RangeError('Magic wand pixel buffer must match its dimensions');
  }
  if (!Number.isSafeInteger(options.seedX) || !Number.isSafeInteger(options.seedY)) {
    throw new RangeError('Magic wand seed coordinates must be integers');
  }
  if (
    options.seedX < 0 ||
    options.seedY < 0 ||
    options.seedX >= options.width ||
    options.seedY >= options.height
  ) {
    throw new RangeError('Magic wand seed is outside the image');
  }
  if (!Number.isFinite(options.tolerance) || options.tolerance < 0 || options.tolerance > 255) {
    throw new RangeError('Magic wand tolerance must be between 0 and 255');
  }
  return area;
}

/**
 * Builds a sparse selection mask from selected pixel indices without allocating
 * a full-document coverage buffer when the match set is sparse.
 */
function selectionFromSelectedIndices(
  width: number,
  height: number,
  selected: ReadonlySet<number>,
): SelectionMask {
  if (selected.size === 0) return emptySelection(width, height);
  let store = TiledCoverageStore.empty(width, height);
  const tileSize = store.tileSize;
  const columnCount = Math.ceil(width / tileSize);
  type TileScratch = {
    readonly column: number;
    readonly row: number;
    readonly width: number;
    readonly height: number;
    readonly coverage: Uint8ClampedArray;
  };
  const tiles = new Map<number, TileScratch>();
  for (const index of selected) {
    if (!Number.isSafeInteger(index) || index < 0 || index >= width * height) continue;
    const x = index % width;
    const y = Math.floor(index / width);
    const column = Math.floor(x / tileSize);
    const row = Math.floor(y / tileSize);
    const key = column + row * columnCount;
    let tile = tiles.get(key);
    if (!tile) {
      const tileWidth = Math.min(tileSize, width - column * tileSize);
      const tileHeight = Math.min(tileSize, height - row * tileSize);
      tile = {
        column,
        row,
        width: tileWidth,
        height: tileHeight,
        coverage: new Uint8ClampedArray(tileWidth * tileHeight),
      };
      tiles.set(key, tile);
    }
    const localX = x - column * tileSize;
    const localY = y - row * tileSize;
    tile.coverage[localY * tile.width + localX] = 255;
  }
  for (const tile of tiles.values()) {
    store = store.write({
      x: tile.column * tileSize,
      y: tile.row * tileSize,
      width: tile.width,
      height: tile.height,
      coverage: tile.coverage,
    }).store;
  }
  return { width, height, coverageStore: store };
}

/**
 * Selects pixels by root-mean-square RGB or RGBA byte distance from the seed.
 * Contiguous mode uses deterministic four-neighbor connectivity.
 * Output ownership is sparse: only tiles containing selected pixels are allocated.
 */
export function magicWandSelection(options: MagicWandOptions): SelectionMask {
  validateMagicWandOptions(options);
  return magicWandSelectionFromSource({
    source: {
      width: options.width,
      height: options.height,
      read(region) {
        const pixels = new Uint8ClampedArray(region.width * region.height * 4);
        for (let row = 0; row < region.height; row += 1) {
          const sourceStart = ((region.y + row) * options.width + region.x) * 4;
          pixels.set(
            options.pixels.subarray(sourceStart, sourceStart + region.width * 4),
            row * region.width * 4,
          );
        }
        return pixels;
      },
    },
    seedX: options.seedX,
    seedY: options.seedY,
    tolerance: options.tolerance,
    contiguous: options.contiguous,
    sampleAlpha: options.sampleAlpha,
  });
}

/**
 * Selects from a region-readable rendered source. Global matching holds one RGBA
 * tile plus one coverage tile at a time; contiguous matching uses a bounded tile LRU.
 */
export function magicWandSelectionFromSource(options: MagicWandSourceOptions): SelectionMask {
  const { source } = options;
  const area = validateDimensions(source.width, source.height);
  if (!Number.isSafeInteger(options.seedX) || !Number.isSafeInteger(options.seedY)) {
    throw new RangeError('Magic wand seed coordinates must be integers');
  }
  if (
    options.seedX < 0 ||
    options.seedY < 0 ||
    options.seedX >= source.width ||
    options.seedY >= source.height
  ) {
    throw new RangeError('Magic wand seed is outside the image');
  }
  if (!Number.isFinite(options.tolerance) || options.tolerance < 0 || options.tolerance > 255) {
    throw new RangeError('Magic wand tolerance must be between 0 and 255');
  }
  const read = (region: PixelRegion): Uint8ClampedArray => {
    const pixels = source.read(region);
    if (
      !(pixels instanceof Uint8ClampedArray) ||
      pixels.length !== region.width * region.height * 4
    ) {
      throw new RangeError('Magic wand source region must match its dimensions');
    }
    return pixels;
  };
  const seed = read({ x: options.seedX, y: options.seedY, width: 1, height: 1 });
  const components = options.sampleAlpha ? 4 : 3;
  const toleranceSquared = options.tolerance * options.tolerance * components;
  const matches = (pixels: Uint8ClampedArray, offset: number): boolean => {
    let squaredDistance = 0;
    for (let component = 0; component < components; component += 1) {
      const difference = (pixels[offset + component] ?? 0) - (seed[component] ?? 0);
      squaredDistance += difference * difference;
    }
    return squaredDistance <= toleranceSquared;
  };

  const tileSize = 256;
  if (!options.contiguous) {
    let store = TiledCoverageStore.empty(source.width, source.height, tileSize);
    for (let y = 0; y < source.height; y += tileSize) {
      for (let x = 0; x < source.width; x += tileSize) {
        const region = {
          x,
          y,
          width: Math.min(tileSize, source.width - x),
          height: Math.min(tileSize, source.height - y),
        };
        const pixels = read(region);
        const coverage = new Uint8ClampedArray(region.width * region.height);
        let any = false;
        for (let index = 0; index < coverage.length; index += 1) {
          if (!matches(pixels, index * 4)) continue;
          coverage[index] = 255;
          any = true;
        }
        if (any) store = store.write({ ...region, coverage }).store;
      }
    }
    return { width: source.width, height: source.height, coverageStore: store };
  }

  const columnCount = Math.ceil(source.width / tileSize);
  const tileCache = new Map<
    number,
    { readonly region: PixelRegion; readonly pixels: Uint8ClampedArray }
  >();
  const sourcePixelMatches = (index: number): boolean => {
    const x = index % source.width;
    const y = Math.floor(index / source.width);
    const column = Math.floor(x / tileSize);
    const row = Math.floor(y / tileSize);
    const key = row * columnCount + column;
    let tile = tileCache.get(key);
    if (tile) {
      tileCache.delete(key);
      tileCache.set(key, tile);
    } else {
      const region = {
        x: column * tileSize,
        y: row * tileSize,
        width: Math.min(tileSize, source.width - column * tileSize),
        height: Math.min(tileSize, source.height - row * tileSize),
      };
      tile = { region, pixels: read(region) };
      tileCache.set(key, tile);
      if (tileCache.size > 16) {
        const oldest = tileCache.keys().next().value;
        if (oldest !== undefined) tileCache.delete(oldest);
      }
    }
    const offset = ((y - tile.region.y) * tile.region.width + x - tile.region.x) * 4;
    return matches(tile.pixels, offset);
  };

  const selected = new Set<number>();
  // Contiguous flood-fill: visit only matching pixels (no full-canvas coverage or queue buffers).
  const seedIndex = options.seedY * source.width + options.seedX;
  if (!sourcePixelMatches(seedIndex)) {
    return emptySelection(source.width, source.height);
  }
  const queue: number[] = [seedIndex];
  selected.add(seedIndex);
  let head = 0;
  while (head < queue.length) {
    const pixelIndex = queue[head];
    head += 1;
    if (typeof pixelIndex !== 'number') break;
    const x = pixelIndex % source.width;
    const candidates = [
      x > 0 ? pixelIndex - 1 : -1,
      x + 1 < source.width ? pixelIndex + 1 : -1,
      pixelIndex >= source.width ? pixelIndex - source.width : -1,
      pixelIndex + source.width < area ? pixelIndex + source.width : -1,
    ];
    for (const candidate of candidates) {
      if (candidate < 0 || selected.has(candidate) || !sourcePixelMatches(candidate)) continue;
      selected.add(candidate);
      queue.push(candidate);
    }
  }
  return selectionFromSelectedIndices(source.width, source.height, selected);
}

function boxBlurRegion(
  source: Uint8ClampedArray,
  width: number,
  height: number,
  radius: number,
): Uint8ClampedArray {
  const area = width * height;
  const temporary = allocateCoverage(area);
  const coverage = allocateCoverage(area);
  const window = radius * 2 + 1;
  for (let y = 0; y < height; y += 1) {
    let sum = 0;
    for (let x = 0; x <= radius && x < width; x += 1) {
      sum += source[y * width + x] ?? 0;
    }
    for (let x = 0; x < width; x += 1) {
      temporary[y * width + x] = Math.round(sum / window);
      const removed = x - radius;
      const added = x + radius + 1;
      if (removed >= 0) sum -= source[y * width + removed] ?? 0;
      if (added < width) sum += source[y * width + added] ?? 0;
    }
  }
  for (let x = 0; x < width; x += 1) {
    let sum = 0;
    for (let y = 0; y <= radius && y < height; y += 1) {
      sum += temporary[y * width + x] ?? 0;
    }
    for (let y = 0; y < height; y += 1) {
      coverage[y * width + x] = Math.round(sum / window);
      const removed = y - radius;
      const added = y + radius + 1;
      if (removed >= 0) sum -= temporary[removed * width + x] ?? 0;
      if (added < height) sum += temporary[added * width + x] ?? 0;
    }
  }
  return coverage;
}

/**
 * Applies a deterministic, zero-padded square box feather on the active sparse
 * bounds (+ radius halo) so large transparent canvases are not densified.
 */
export function featherSelection(mask: SelectionMask, radius: number): SelectionMask {
  validateMask(mask);
  validateRadius(radius, 'Feather radius');
  if (radius === 0) return cloneSelectionSparse(mask);
  return applyNeighborhoodFilter(mask, radius, true, (source, width, height) =>
    boxBlurRegion(source, width, height, radius),
  );
}

function morphologyRegion(
  source: Uint8ClampedArray,
  width: number,
  height: number,
  radius: number,
  mode: 'maximum' | 'minimum',
): Uint8ClampedArray {
  const area = width * height;
  const temporary = allocateCoverage(area);
  const coverage = allocateCoverage(area);
  let dequeIndices: Int32Array;
  let dequeValues: Uint8ClampedArray;
  try {
    const capacity = Math.max(width, height) + radius * 2;
    dequeIndices = new Int32Array(capacity);
    dequeValues = new Uint8ClampedArray(capacity);
  } catch {
    throw new RangeError('Selection allocation failed');
  }
  const filterLine = (
    length: number,
    read: (index: number) => number,
    write: (index: number, value: number) => void,
  ): void => {
    let head = 0;
    let tail = 0;
    const paddedLength = length + radius * 2;
    for (let paddedIndex = 0; paddedIndex < paddedLength; paddedIndex += 1) {
      const sourceIndex = paddedIndex - radius;
      const value = sourceIndex < 0 || sourceIndex >= length ? 0 : read(sourceIndex);
      while (
        tail > head &&
        (mode === 'maximum'
          ? (dequeValues[tail - 1] ?? 0) <= value
          : (dequeValues[tail - 1] ?? 0) >= value)
      ) {
        tail -= 1;
      }
      dequeIndices[tail] = paddedIndex;
      dequeValues[tail] = value;
      tail += 1;
      const windowStart = paddedIndex - radius * 2;
      while (tail > head && (dequeIndices[head] ?? 0) < windowStart) head += 1;
      if (paddedIndex >= radius * 2) {
        write(paddedIndex - radius * 2, dequeValues[head] ?? 0);
      }
    }
  };
  for (let y = 0; y < height; y += 1) {
    filterLine(
      width,
      (x) => source[y * width + x] ?? 0,
      (x, value) => {
        temporary[y * width + x] = value;
      },
    );
  }
  for (let x = 0; x < width; x += 1) {
    filterLine(
      height,
      (y) => temporary[y * width + x] ?? 0,
      (y, value) => {
        coverage[y * width + x] = value;
      },
    );
  }
  return coverage;
}

function morphology(
  mask: SelectionMask,
  radius: number,
  mode: 'maximum' | 'minimum',
): SelectionMask {
  validateMask(mask);
  validateRadius(radius, 'Selection radius');
  if (radius === 0) return cloneSelectionSparse(mask);
  return applyNeighborhoodFilter(mask, radius, mode === 'minimum', (source, width, height) =>
    morphologyRegion(source, width, height, radius, mode),
  );
}

/** Expands coverage by a square maximum filter. */
export function expandSelection(mask: SelectionMask, radius: number): SelectionMask {
  return morphology(mask, radius, 'maximum');
}

/** Contracts coverage by a square minimum filter, treating outside-canvas coverage as zero. */
export function contractSelection(mask: SelectionMask, radius: number): SelectionMask {
  return morphology(mask, radius, 'minimum');
}

/** Smooths isolated coverage changes with a radius-sized square median filter. */
export function smoothSelection(mask: SelectionMask, radius: number): SelectionMask {
  validateMask(mask);
  validateRadius(radius, 'Smooth radius');
  if (radius === 0) return cloneSelectionSparse(mask);
  const sampleCount = (radius * 2 + 1) ** 2;
  if (!Number.isSafeInteger(sampleCount) || sampleCount > MAX_SELECTION_PIXELS) {
    throw new RangeError(
      `Smooth neighborhood cannot exceed ${String(MAX_SELECTION_PIXELS)} samples`,
    );
  }
  return applyNeighborhoodFilter(mask, radius, true, (source, sourceWidth, sourceHeight) => {
    const coverage = allocateCoverage(sourceWidth * sourceHeight);
    const histogram = new Uint32Array(256);
    for (let y = 0; y < sourceHeight; y += 1) {
      for (let x = 0; x < sourceWidth; x += 1) {
        histogram.fill(0);
        for (let offsetY = -radius; offsetY <= radius; offsetY += 1) {
          for (let offsetX = -radius; offsetX <= radius; offsetX += 1) {
            const sourceX = x + offsetX;
            const sourceY = y + offsetY;
            const value =
              sourceX < 0 || sourceY < 0 || sourceX >= sourceWidth || sourceY >= sourceHeight
                ? 0
                : (source[sourceY * sourceWidth + sourceX] ?? 0);
            histogram[value] = (histogram[value] ?? 0) + 1;
          }
        }
        const target = Math.floor(sampleCount / 2) + 1;
        let accumulated = 0;
        for (let value = 0; value < histogram.length; value += 1) {
          accumulated += histogram[value] ?? 0;
          if (accumulated < target) continue;
          coverage[y * sourceWidth + x] = value;
          break;
        }
      }
    }
    return coverage;
  });
}

/** Creates a centered boundary band from the difference of expand and contract. */
export function borderSelection(mask: SelectionMask, width: number): SelectionMask {
  validateMask(mask);
  validateRadius(width, 'Border width', false);
  const expanded = expandSelection(mask, width);
  const contracted = contractSelection(mask, width);
  const defaultCoverage = Math.max(
    0,
    expanded.coverageStore.defaultCoverage - contracted.coverageStore.defaultCoverage,
  );
  let store = TiledCoverageStore.uniform(mask.width, mask.height, defaultCoverage);
  const keys = new Set<number>();
  for (const tile of expanded.coverageStore.tiles()) keys.add(tile.key);
  for (const tile of contracted.coverageStore.tiles()) keys.add(tile.key);
  for (const key of [...keys].sort((left, right) => left - right)) {
    const region = tileRegion(
      expanded.coverageStore,
      key % expanded.coverageStore.columnCount,
      Math.floor(key / expanded.coverageStore.columnCount),
    );
    const expandedCoverage = expanded.coverageStore.read(region);
    const contractedCoverage = contracted.coverageStore.read(region);
    const coverage = allocateCoverage(region.width * region.height);
    for (let index = 0; index < coverage.length; index += 1) {
      coverage[index] = Math.max(
        0,
        (expandedCoverage[index] ?? 0) - (contractedCoverage[index] ?? 0),
      );
    }
    store = store.write({ ...region, coverage }).store;
  }
  return { width: mask.width, height: mask.height, coverageStore: store };
}
