export const DEFAULT_TILE_SIZE = 256;
export const MAX_TILE_SIZE = 1024;

export interface PixelRegion {
  readonly x: number;
  readonly y: number;
  readonly width: number;
  readonly height: number;
}

export interface PixelPatch extends PixelRegion {
  readonly pixels: Uint8ClampedArray;
}

export interface PixelTile extends PixelRegion {
  readonly key: number;
  readonly column: number;
  readonly row: number;
  readonly pixels: Uint8ClampedArray;
}

export interface PixelSource {
  readonly width: number;
  readonly height: number;
  readonly allocatedByteLength: number;
  pixelChunks(region?: PixelRegion): Iterable<PixelPatch>;
  read(region: PixelRegion): Uint8ClampedArray;
  toContiguous(): Uint8ClampedArray;
}

export interface PixelWriteResult {
  readonly store: TiledPixelStore;
  readonly dirtyRegion: PixelRegion | null;
  readonly changedTileKeys: readonly number[];
}

export interface TiledPixelStoreSnapshot {
  readonly schemaVersion: 1;
  readonly width: number;
  readonly height: number;
  readonly tileSize: number;
  readonly tiles: readonly {
    readonly column: number;
    readonly row: number;
    readonly pixels: Uint8ClampedArray;
  }[];
}

export interface TiledPixelStoreDelta {
  readonly tiles: TiledPixelStoreSnapshot['tiles'];
  readonly removedTileKeys: readonly number[];
}

export interface CoveragePatch extends PixelRegion {
  readonly coverage: Uint8ClampedArray;
}

export interface CoverageSource {
  readonly width: number;
  readonly height: number;
  readonly allocatedByteLength: number;
  coverageChunks(region?: PixelRegion): Iterable<CoveragePatch>;
  read(region: PixelRegion): Uint8ClampedArray;
  toContiguous(): Uint8ClampedArray;
}

export class ContiguousCoverageSource implements CoverageSource {
  readonly allocatedByteLength: number;
  constructor(
    readonly width: number,
    readonly height: number,
    readonly coverage: Uint8ClampedArray,
  ) {
    validateDimensions(width, height, DEFAULT_TILE_SIZE);
    if (coverage.length !== width * height)
      throw new RangeError('Coverage buffer length does not match its dimensions');
    this.allocatedByteLength = coverage.byteLength;
  }
  read(region: PixelRegion): Uint8ClampedArray {
    validateReadRegion(region);
    const output = new Uint8ClampedArray(region.width * region.height);
    const clipped = intersectRegions(region, {
      x: 0,
      y: 0,
      width: this.width,
      height: this.height,
    });
    if (!clipped) return output;
    for (let y = clipped.y; y < clipped.y + clipped.height; y += 1) {
      const start = y * this.width + clipped.x;
      const destination = (y - region.y) * region.width + clipped.x - region.x;
      output.set(this.coverage.subarray(start, start + clipped.width), destination);
    }
    return output;
  }
  *coverageChunks(region?: PixelRegion): IterableIterator<CoveragePatch> {
    const requested = region
      ? intersectRegions(region, { x: 0, y: 0, width: this.width, height: this.height })
      : { x: 0, y: 0, width: this.width, height: this.height };
    if (!requested) return;
    yield { ...requested, coverage: this.read(requested) };
  }
  toContiguous(): Uint8ClampedArray {
    return new Uint8ClampedArray(this.coverage);
  }
}

export interface CoverageTile extends PixelRegion {
  readonly key: number;
  readonly column: number;
  readonly row: number;
  readonly coverage: Uint8ClampedArray;
}

export interface CoverageWriteResult {
  readonly store: TiledCoverageStore;
  readonly dirtyRegion: PixelRegion | null;
  readonly changedTileKeys: readonly number[];
}

export interface TiledCoverageStoreSnapshot {
  readonly schemaVersion: 2;
  readonly width: number;
  readonly height: number;
  readonly tileSize: number;
  readonly defaultCoverage: number;
  readonly tiles: readonly {
    readonly column: number;
    readonly row: number;
    readonly coverage: Uint8ClampedArray;
  }[];
}

export interface TiledCoverageStoreDelta {
  readonly tiles: TiledCoverageStoreSnapshot['tiles'];
  readonly removedTileKeys: readonly number[];
}

export class DirtyRegionAccumulator {
  readonly #bounds: PixelRegion;
  readonly #maxRegions: number;
  #regions: PixelRegion[] = [];

  constructor(width: number, height: number, maxRegions = 32) {
    positiveInteger(width, 'Dirty-region width');
    positiveInteger(height, 'Dirty-region height');
    positiveInteger(maxRegions, 'Dirty-region limit');
    this.#bounds = { x: 0, y: 0, width, height };
    this.#maxRegions = maxRegions;
  }

  get size(): number {
    return this.#regions.length;
  }

  add(region: PixelRegion): void {
    const clipped = intersectRegions(region, this.#bounds);
    if (!clipped) return;
    let candidate = clipped;
    const remaining = [...this.#regions];
    for (let index = 0; index < remaining.length; ) {
      const current = remaining[index];
      if (regionsTouch(candidate, current)) {
        candidate = unionRegions(candidate, current) ?? candidate;
        remaining.splice(index, 1);
        index = 0;
      } else {
        index += 1;
      }
    }
    remaining.push(candidate);
    this.#regions = remaining;
    if (this.#regions.length > this.#maxRegions) {
      this.#regions = [
        this.#regions.reduce<PixelRegion | null>(
          (combined, item) => unionRegions(combined, item),
          null,
        ) ?? clipped,
      ];
    }
  }

  invalidateAll(): void {
    this.#regions = [{ ...this.#bounds }];
  }

  peek(): readonly PixelRegion[] {
    return this.#regions.map((region) => ({ ...region }));
  }

  consume(): readonly PixelRegion[] {
    const regions = this.peek();
    this.#regions = [];
    return regions;
  }
}

function positiveInteger(value: number, label: string): void {
  if (!Number.isSafeInteger(value) || value <= 0) throw new RangeError(`${label} must be positive`);
}

function integer(value: number, label: string): void {
  if (!Number.isSafeInteger(value)) throw new RangeError(`${label} must be an integer`);
}

function snapshotInteger(value: unknown, label: string): number {
  if (typeof value !== 'number' || !Number.isSafeInteger(value)) {
    throw new RangeError(`${label} must be an integer`);
  }
  return value;
}

function tileKey(column: number, row: number, columnCount: number): number {
  return row * columnCount + column;
}

function tileIsTransparent(pixels: Uint8ClampedArray): boolean {
  return pixels.every((value) => value === 0);
}

function tileIsUniform(values: Uint8ClampedArray, expected: number): boolean {
  for (let index = 0; index < values.length; index += 1) {
    if ((values[index] ?? 0) !== expected) return false;
  }
  return true;
}

export class TiledPixelStore {
  readonly width: number;
  readonly height: number;
  readonly tileSize: number;
  readonly columnCount: number;
  readonly rowCount: number;
  readonly #tiles: ReadonlyMap<number, Uint8ClampedArray>;

  private constructor(
    width: number,
    height: number,
    tileSize: number,
    tiles: ReadonlyMap<number, Uint8ClampedArray>,
  ) {
    this.width = width;
    this.height = height;
    this.tileSize = tileSize;
    this.columnCount = Math.ceil(width / tileSize);
    this.rowCount = Math.ceil(height / tileSize);
    this.#tiles = tiles;
  }

  static empty(width: number, height: number, tileSize = DEFAULT_TILE_SIZE): TiledPixelStore {
    validateDimensions(width, height, tileSize);
    return new TiledPixelStore(width, height, tileSize, new Map());
  }

  static fromContiguous(
    width: number,
    height: number,
    pixels: ArrayLike<number>,
    tileSize = DEFAULT_TILE_SIZE,
  ): TiledPixelStore {
    validateDimensions(width, height, tileSize);
    if (pixels.length !== width * height * 4) {
      throw new RangeError('Pixel buffer length does not match its dimensions');
    }
    const store = TiledPixelStore.empty(width, height, tileSize);
    return store.write({ x: 0, y: 0, width, height, pixels: new Uint8ClampedArray(pixels) }).store;
  }

  /** Rehydrates an owned sparse store from its worker/persistence boundary representation. */
  static fromSnapshot(value: unknown): TiledPixelStore {
    if (typeof value !== 'object' || value === null) {
      throw new TypeError('Tiled pixel snapshot must be an object');
    }
    const snapshot = value as Record<string, unknown>;
    if (snapshot.schemaVersion !== 1) throw new RangeError('Unsupported tiled pixel snapshot');
    const width = snapshotInteger(snapshot.width, 'Snapshot width');
    const height = snapshotInteger(snapshot.height, 'Snapshot height');
    const tileSize = snapshotInteger(snapshot.tileSize, 'Snapshot tile size');
    validateDimensions(width, height, tileSize);
    if (!Array.isArray(snapshot.tiles)) throw new TypeError('Snapshot tiles must be an array');
    const columnCount = Math.ceil(width / tileSize);
    const rowCount = Math.ceil(height / tileSize);
    const tiles = new Map<number, Uint8ClampedArray>();
    for (const [index, tileValue] of snapshot.tiles.entries()) {
      if (typeof tileValue !== 'object' || tileValue === null) {
        throw new TypeError(`Snapshot tile ${String(index)} must be an object`);
      }
      const tile = tileValue as Record<string, unknown>;
      const column = snapshotInteger(tile.column, `Snapshot tile ${String(index)} column`);
      const row = snapshotInteger(tile.row, `Snapshot tile ${String(index)} row`);
      if (column < 0 || row < 0 || column >= columnCount || row >= rowCount) {
        throw new RangeError(`Snapshot tile ${String(index)} is outside the store`);
      }
      const key = tileKey(column, row, columnCount);
      if (tiles.has(key)) throw new RangeError(`Snapshot tile ${String(index)} is duplicated`);
      const tileWidth = Math.min(tileSize, width - column * tileSize);
      const tileHeight = Math.min(tileSize, height - row * tileSize);
      if (!(tile.pixels instanceof Uint8ClampedArray)) {
        throw new TypeError(`Snapshot tile ${String(index)} pixels must be Uint8ClampedArray`);
      }
      if (tile.pixels.length !== tileWidth * tileHeight * 4) {
        throw new RangeError(`Snapshot tile ${String(index)} length does not match its bounds`);
      }
      const owned = new Uint8ClampedArray(tile.pixels);
      if (tileIsTransparent(owned)) {
        throw new RangeError(`Snapshot tile ${String(index)} must not be fully transparent`);
      }
      tiles.set(key, owned);
    }
    return new TiledPixelStore(width, height, tileSize, tiles);
  }

  get tileCount(): number {
    return this.#tiles.size;
  }

  get allocatedByteLength(): number {
    let bytes = 0;
    for (const pixels of this.#tiles.values()) bytes += pixels.byteLength;
    return bytes;
  }

  getTile(column: number, row: number): PixelTile | null {
    integer(column, 'Tile column');
    integer(row, 'Tile row');
    if (column < 0 || row < 0 || column >= this.columnCount || row >= this.rowCount) return null;
    const key = tileKey(column, row, this.columnCount);
    const pixels = this.#tiles.get(key);
    if (!pixels) return null;
    const region = this.tileRegion(column, row);
    return { key, column, row, ...region, pixels };
  }

  *tiles(): IterableIterator<PixelTile> {
    for (const key of [...this.#tiles.keys()].sort((left, right) => left - right)) {
      const column = key % this.columnCount;
      const row = Math.floor(key / this.columnCount);
      const tile = this.getTile(column, row);
      if (tile) yield tile;
    }
  }

  /** Produces an owned, deterministic representation suitable for workers and native containers. */
  snapshot(region?: PixelRegion): TiledPixelStoreSnapshot {
    const requested = region
      ? intersectRegions(region, { x: 0, y: 0, width: this.width, height: this.height })
      : null;
    return {
      schemaVersion: 1,
      width: this.width,
      height: this.height,
      tileSize: this.tileSize,
      tiles: [...this.tiles()]
        .filter(
          (tile) =>
            region === undefined || (requested !== null && intersectRegions(tile, requested)),
        )
        .map((tile) => ({
          column: tile.column,
          row: tile.row,
          pixels: new Uint8ClampedArray(tile.pixels),
        })),
    };
  }

  *pixelChunks(region?: PixelRegion): IterableIterator<PixelPatch> {
    const requested = region
      ? intersectRegions(region, { x: 0, y: 0, width: this.width, height: this.height })
      : { x: 0, y: 0, width: this.width, height: this.height };
    if (!requested) return;
    for (const tile of this.tiles()) {
      if (intersectRegions(tile, requested)) {
        yield { x: tile.x, y: tile.y, width: tile.width, height: tile.height, pixels: tile.pixels };
      }
    }
  }

  read(region: PixelRegion): Uint8ClampedArray {
    validateReadRegion(region);
    const output = new Uint8ClampedArray(region.width * region.height * 4);
    const clipped = intersectRegions(region, {
      x: 0,
      y: 0,
      width: this.width,
      height: this.height,
    });
    if (!clipped) return output;
    for (const tile of this.pixelChunks(clipped)) {
      const overlap = intersectRegions(tile, clipped);
      if (!overlap) continue;
      for (let y = overlap.y; y < overlap.y + overlap.height; y += 1) {
        const sourceStart = ((y - tile.y) * tile.width + (overlap.x - tile.x)) * 4;
        const destinationStart = ((y - region.y) * region.width + (overlap.x - region.x)) * 4;
        output.set(
          tile.pixels.subarray(sourceStart, sourceStart + overlap.width * 4),
          destinationStart,
        );
      }
    }
    return output;
  }

  toContiguous(): Uint8ClampedArray {
    const output = new Uint8ClampedArray(this.width * this.height * 4);
    for (const tile of this.tiles()) {
      for (let localY = 0; localY < tile.height; localY += 1) {
        const sourceStart = localY * tile.width * 4;
        const destinationStart = ((tile.y + localY) * this.width + tile.x) * 4;
        output.set(
          tile.pixels.subarray(sourceStart, sourceStart + tile.width * 4),
          destinationStart,
        );
      }
    }
    return output;
  }

  write(patch: PixelPatch): PixelWriteResult {
    validatePatch(patch);
    const clipped = intersectRegions(patch, { x: 0, y: 0, width: this.width, height: this.height });
    if (!clipped) return { store: this, dirtyRegion: null, changedTileKeys: [] };

    const tiles = new Map(this.#tiles);
    const changedKeys: number[] = [];
    let dirtyLeft = this.width;
    let dirtyTop = this.height;
    let dirtyRight = -1;
    let dirtyBottom = -1;
    const firstColumn = Math.floor(clipped.x / this.tileSize);
    const lastColumn = Math.floor((clipped.x + clipped.width - 1) / this.tileSize);
    const firstRow = Math.floor(clipped.y / this.tileSize);
    const lastRow = Math.floor((clipped.y + clipped.height - 1) / this.tileSize);

    for (let row = firstRow; row <= lastRow; row += 1) {
      for (let column = firstColumn; column <= lastColumn; column += 1) {
        const key = tileKey(column, row, this.columnCount);
        const tileRegion = this.tileRegion(column, row);
        const overlap = intersectRegions(clipped, tileRegion);
        if (!overlap) continue;
        const existing = this.#tiles.get(key);
        const next = existing
          ? new Uint8ClampedArray(existing)
          : new Uint8ClampedArray(tileRegion.width * tileRegion.height * 4);
        let tileChanged = false;
        for (let y = overlap.y; y < overlap.y + overlap.height; y += 1) {
          for (let x = overlap.x; x < overlap.x + overlap.width; x += 1) {
            const sourceOffset = ((y - patch.y) * patch.width + (x - patch.x)) * 4;
            const tileOffset = ((y - tileRegion.y) * tileRegion.width + (x - tileRegion.x)) * 4;
            let pixelChanged = false;
            for (let channel = 0; channel < 4; channel += 1) {
              const value = patch.pixels[sourceOffset + channel] ?? 0;
              if ((next[tileOffset + channel] ?? 0) !== value) {
                next[tileOffset + channel] = value;
                pixelChanged = true;
              }
            }
            if (pixelChanged) {
              tileChanged = true;
              dirtyLeft = Math.min(dirtyLeft, x);
              dirtyTop = Math.min(dirtyTop, y);
              dirtyRight = Math.max(dirtyRight, x);
              dirtyBottom = Math.max(dirtyBottom, y);
            }
          }
        }
        if (!tileChanged) continue;
        changedKeys.push(key);
        if (tileIsTransparent(next)) tiles.delete(key);
        else tiles.set(key, next);
      }
    }

    if (changedKeys.length === 0) return { store: this, dirtyRegion: null, changedTileKeys: [] };
    return {
      store: new TiledPixelStore(this.width, this.height, this.tileSize, tiles),
      dirtyRegion: {
        x: dirtyLeft,
        y: dirtyTop,
        width: dirtyRight - dirtyLeft + 1,
        height: dirtyBottom - dirtyTop + 1,
      },
      changedTileKeys: changedKeys,
    };
  }

  /** Applies an owned worker delta while sharing every unchanged immutable tile. */
  applyTileDelta(delta: TiledPixelStoreDelta): TiledPixelStore {
    const tiles = new Map(this.#tiles);
    const changed = new Set<number>();
    for (const key of delta.removedTileKeys) {
      integer(key, 'Removed pixel tile key');
      if (key < 0 || key >= this.columnCount * this.rowCount || changed.has(key)) {
        throw new RangeError('Removed pixel tile key is invalid or duplicated');
      }
      changed.add(key);
      tiles.delete(key);
    }
    for (const [index, tile] of delta.tiles.entries()) {
      integer(tile.column, `Pixel delta tile ${String(index)} column`);
      integer(tile.row, `Pixel delta tile ${String(index)} row`);
      if (
        tile.column < 0 ||
        tile.row < 0 ||
        tile.column >= this.columnCount ||
        tile.row >= this.rowCount
      ) {
        throw new RangeError(`Pixel delta tile ${String(index)} is outside the store`);
      }
      const key = tileKey(tile.column, tile.row, this.columnCount);
      if (changed.has(key)) throw new RangeError(`Pixel delta tile ${String(index)} is duplicated`);
      changed.add(key);
      const region = this.tileRegion(tile.column, tile.row);
      if (!(tile.pixels instanceof Uint8ClampedArray)) {
        throw new TypeError(`Pixel delta tile ${String(index)} must use Uint8ClampedArray`);
      }
      if (tile.pixels.length !== region.width * region.height * 4) {
        throw new RangeError(`Pixel delta tile ${String(index)} length does not match its bounds`);
      }
      const owned = new Uint8ClampedArray(tile.pixels);
      if (tileIsTransparent(owned)) {
        throw new RangeError(`Pixel delta tile ${String(index)} must not be fully transparent`);
      }
      tiles.set(key, owned);
    }
    return new TiledPixelStore(this.width, this.height, this.tileSize, tiles);
  }

  private tileRegion(column: number, row: number): PixelRegion {
    const x = column * this.tileSize;
    const y = row * this.tileSize;
    return {
      x,
      y,
      width: Math.min(this.tileSize, this.width - x),
      height: Math.min(this.tileSize, this.height - y),
    };
  }
}

/** Immutable sparse one-channel storage for masks, selections, and channels. */
export class TiledCoverageStore implements CoverageSource {
  readonly width: number;
  readonly height: number;
  readonly tileSize: number;
  readonly columnCount: number;
  readonly rowCount: number;
  readonly defaultCoverage: number;
  readonly #tiles: ReadonlyMap<number, Uint8ClampedArray>;

  private constructor(
    width: number,
    height: number,
    tileSize: number,
    defaultCoverage: number,
    tiles: ReadonlyMap<number, Uint8ClampedArray>,
  ) {
    this.width = width;
    this.height = height;
    this.tileSize = tileSize;
    this.columnCount = Math.ceil(width / tileSize);
    this.rowCount = Math.ceil(height / tileSize);
    this.defaultCoverage = defaultCoverage;
    this.#tiles = tiles;
  }

  static empty(width: number, height: number, tileSize = DEFAULT_TILE_SIZE): TiledCoverageStore {
    validateDimensions(width, height, tileSize);
    return new TiledCoverageStore(width, height, tileSize, 0, new Map());
  }

  static uniform(
    width: number,
    height: number,
    defaultCoverage: number,
    tileSize = DEFAULT_TILE_SIZE,
  ): TiledCoverageStore {
    validateDimensions(width, height, tileSize);
    integer(defaultCoverage, 'Default coverage');
    if (defaultCoverage < 0 || defaultCoverage > 255) {
      throw new RangeError('Default coverage must be between 0 and 255');
    }
    return new TiledCoverageStore(width, height, tileSize, defaultCoverage, new Map());
  }

  static fromContiguous(
    width: number,
    height: number,
    coverage: ArrayLike<number>,
    tileSize = DEFAULT_TILE_SIZE,
  ): TiledCoverageStore {
    validateDimensions(width, height, tileSize);
    if (coverage.length !== width * height) {
      throw new RangeError('Coverage buffer length does not match its dimensions');
    }
    const store = TiledCoverageStore.empty(width, height, tileSize);
    return store.write({
      x: 0,
      y: 0,
      width,
      height,
      coverage: new Uint8ClampedArray(coverage),
    }).store;
  }

  static fromSnapshot(value: unknown): TiledCoverageStore {
    if (typeof value !== 'object' || value === null) {
      throw new TypeError('Tiled coverage snapshot must be an object');
    }
    const snapshot = value as Record<string, unknown>;
    if (snapshot.schemaVersion !== 1 && snapshot.schemaVersion !== 2) {
      throw new RangeError('Unsupported tiled coverage snapshot');
    }
    const width = snapshotInteger(snapshot.width, 'Coverage snapshot width');
    const height = snapshotInteger(snapshot.height, 'Coverage snapshot height');
    const tileSize = snapshotInteger(snapshot.tileSize, 'Coverage snapshot tile size');
    validateDimensions(width, height, tileSize);
    const defaultCoverage =
      snapshot.schemaVersion === 1
        ? 0
        : snapshotInteger(snapshot.defaultCoverage, 'Coverage snapshot default coverage');
    if (defaultCoverage < 0 || defaultCoverage > 255) {
      throw new RangeError('Coverage snapshot default coverage must be between 0 and 255');
    }
    if (!Array.isArray(snapshot.tiles))
      throw new TypeError('Coverage snapshot tiles must be an array');
    const columnCount = Math.ceil(width / tileSize);
    const rowCount = Math.ceil(height / tileSize);
    const tiles = new Map<number, Uint8ClampedArray>();
    for (const [index, tileValue] of snapshot.tiles.entries()) {
      if (typeof tileValue !== 'object' || tileValue === null) {
        throw new TypeError(`Coverage snapshot tile ${String(index)} must be an object`);
      }
      const tile = tileValue as Record<string, unknown>;
      const column = snapshotInteger(tile.column, `Coverage snapshot tile ${String(index)} column`);
      const row = snapshotInteger(tile.row, `Coverage snapshot tile ${String(index)} row`);
      if (column < 0 || row < 0 || column >= columnCount || row >= rowCount) {
        throw new RangeError(`Coverage snapshot tile ${String(index)} is outside the store`);
      }
      const key = tileKey(column, row, columnCount);
      if (tiles.has(key))
        throw new RangeError(`Coverage snapshot tile ${String(index)} is duplicated`);
      const tileWidth = Math.min(tileSize, width - column * tileSize);
      const tileHeight = Math.min(tileSize, height - row * tileSize);
      if (!(tile.coverage instanceof Uint8ClampedArray)) {
        throw new TypeError(`Coverage snapshot tile ${String(index)} must use Uint8ClampedArray`);
      }
      if (tile.coverage.length !== tileWidth * tileHeight) {
        throw new RangeError(
          `Coverage snapshot tile ${String(index)} length does not match its bounds`,
        );
      }
      const owned = new Uint8ClampedArray(tile.coverage);
      if (tileIsUniform(owned, defaultCoverage)) {
        throw new RangeError(
          `Coverage snapshot tile ${String(index)} must differ from default coverage`,
        );
      }
      tiles.set(key, owned);
    }
    return new TiledCoverageStore(width, height, tileSize, defaultCoverage, tiles);
  }

  get tileCount(): number {
    return this.#tiles.size;
  }

  get allocatedByteLength(): number {
    let bytes = 0;
    for (const coverage of this.#tiles.values()) bytes += coverage.byteLength;
    return bytes;
  }

  getTile(column: number, row: number): CoverageTile | null {
    integer(column, 'Tile column');
    integer(row, 'Tile row');
    if (column < 0 || row < 0 || column >= this.columnCount || row >= this.rowCount) return null;
    const key = tileKey(column, row, this.columnCount);
    const coverage = this.#tiles.get(key);
    if (!coverage) return null;
    return { key, column, row, ...this.tileRegion(column, row), coverage };
  }

  *tiles(): IterableIterator<CoverageTile> {
    for (const key of [...this.#tiles.keys()].sort((left, right) => left - right)) {
      const tile = this.getTile(key % this.columnCount, Math.floor(key / this.columnCount));
      if (tile) yield tile;
    }
  }

  *coverageChunks(region?: PixelRegion): IterableIterator<CoveragePatch> {
    const requested = region
      ? intersectRegions(region, { x: 0, y: 0, width: this.width, height: this.height })
      : { x: 0, y: 0, width: this.width, height: this.height };
    if (!requested) return;
    if (this.defaultCoverage !== 0) {
      const firstColumn = Math.floor(requested.x / this.tileSize);
      const lastColumn = Math.floor((requested.x + requested.width - 1) / this.tileSize);
      const firstRow = Math.floor(requested.y / this.tileSize);
      const lastRow = Math.floor((requested.y + requested.height - 1) / this.tileSize);
      for (let row = firstRow; row <= lastRow; row += 1) {
        for (let column = firstColumn; column <= lastColumn; column += 1) {
          const tile = this.tileRegion(column, row);
          yield { ...tile, coverage: this.read(tile) };
        }
      }
      return;
    }
    for (const tile of this.tiles()) {
      if (intersectRegions(tile, requested)) {
        yield {
          x: tile.x,
          y: tile.y,
          width: tile.width,
          height: tile.height,
          coverage: tile.coverage,
        };
      }
    }
  }

  /** Inverts the implicit default and each deviation tile without visiting missing tiles. */
  invert(): TiledCoverageStore {
    const tiles = new Map<number, Uint8ClampedArray>();
    for (const [key, source] of this.#tiles) {
      const coverage = new Uint8ClampedArray(source.length);
      for (let index = 0; index < coverage.length; index += 1) {
        coverage[index] = 255 - (source[index] ?? this.defaultCoverage);
      }
      tiles.set(key, coverage);
    }
    return new TiledCoverageStore(
      this.width,
      this.height,
      this.tileSize,
      255 - this.defaultCoverage,
      tiles,
    );
  }

  snapshot(region?: PixelRegion): TiledCoverageStoreSnapshot {
    const requested = region
      ? intersectRegions(region, { x: 0, y: 0, width: this.width, height: this.height })
      : null;
    return {
      schemaVersion: 2,
      width: this.width,
      height: this.height,
      tileSize: this.tileSize,
      defaultCoverage: this.defaultCoverage,
      tiles: [...this.tiles()]
        .filter(
          (tile) =>
            region === undefined || (requested !== null && intersectRegions(tile, requested)),
        )
        .map((tile) => ({
          column: tile.column,
          row: tile.row,
          coverage: new Uint8ClampedArray(tile.coverage),
        })),
    };
  }

  read(region: PixelRegion): Uint8ClampedArray {
    validateReadRegion(region);
    const output = new Uint8ClampedArray(region.width * region.height);
    const clipped = intersectRegions(region, {
      x: 0,
      y: 0,
      width: this.width,
      height: this.height,
    });
    if (!clipped) return output;
    if (this.defaultCoverage !== 0) {
      for (let y = clipped.y; y < clipped.y + clipped.height; y += 1) {
        const destinationStart = (y - region.y) * region.width + clipped.x - region.x;
        output.fill(this.defaultCoverage, destinationStart, destinationStart + clipped.width);
      }
    }
    for (const tile of this.tiles()) {
      const overlap = intersectRegions(tile, clipped);
      if (!overlap) continue;
      for (let y = overlap.y; y < overlap.y + overlap.height; y += 1) {
        const sourceStart = (y - tile.y) * tile.width + overlap.x - tile.x;
        const destinationStart = (y - region.y) * region.width + overlap.x - region.x;
        output.set(
          tile.coverage.subarray(sourceStart, sourceStart + overlap.width),
          destinationStart,
        );
      }
    }
    return output;
  }

  toContiguous(): Uint8ClampedArray {
    return this.read({ x: 0, y: 0, width: this.width, height: this.height });
  }

  write(patch: CoveragePatch): CoverageWriteResult {
    validateCoveragePatch(patch);
    const clipped = intersectRegions(patch, { x: 0, y: 0, width: this.width, height: this.height });
    if (!clipped) return { store: this, dirtyRegion: null, changedTileKeys: [] };
    const tiles = new Map(this.#tiles);
    const changedTileKeys: number[] = [];
    let dirtyLeft = this.width;
    let dirtyTop = this.height;
    let dirtyRight = -1;
    let dirtyBottom = -1;
    const firstColumn = Math.floor(clipped.x / this.tileSize);
    const lastColumn = Math.floor((clipped.x + clipped.width - 1) / this.tileSize);
    const firstRow = Math.floor(clipped.y / this.tileSize);
    const lastRow = Math.floor((clipped.y + clipped.height - 1) / this.tileSize);
    for (let row = firstRow; row <= lastRow; row += 1) {
      for (let column = firstColumn; column <= lastColumn; column += 1) {
        const key = tileKey(column, row, this.columnCount);
        const tileRegion = this.tileRegion(column, row);
        const overlap = intersectRegions(clipped, tileRegion);
        if (!overlap) continue;
        const existing = this.#tiles.get(key);
        const next = existing
          ? new Uint8ClampedArray(existing)
          : new Uint8ClampedArray(tileRegion.width * tileRegion.height).fill(this.defaultCoverage);
        let tileChanged = false;
        for (let y = overlap.y; y < overlap.y + overlap.height; y += 1) {
          for (let x = overlap.x; x < overlap.x + overlap.width; x += 1) {
            const sourceOffset = (y - patch.y) * patch.width + x - patch.x;
            const tileOffset = (y - tileRegion.y) * tileRegion.width + x - tileRegion.x;
            const value = patch.coverage[sourceOffset] ?? 0;
            if ((next[tileOffset] ?? 0) === value) continue;
            next[tileOffset] = value;
            tileChanged = true;
            dirtyLeft = Math.min(dirtyLeft, x);
            dirtyTop = Math.min(dirtyTop, y);
            dirtyRight = Math.max(dirtyRight, x);
            dirtyBottom = Math.max(dirtyBottom, y);
          }
        }
        if (!tileChanged) continue;
        changedTileKeys.push(key);
        if (tileIsUniform(next, this.defaultCoverage)) tiles.delete(key);
        else tiles.set(key, next);
      }
    }
    if (changedTileKeys.length === 0) {
      return { store: this, dirtyRegion: null, changedTileKeys: [] };
    }
    return {
      store: new TiledCoverageStore(
        this.width,
        this.height,
        this.tileSize,
        this.defaultCoverage,
        tiles,
      ),
      dirtyRegion: {
        x: dirtyLeft,
        y: dirtyTop,
        width: dirtyRight - dirtyLeft + 1,
        height: dirtyBottom - dirtyTop + 1,
      },
      changedTileKeys,
    };
  }

  /** Applies an owned worker delta while sharing every unchanged immutable tile. */
  applyTileDelta(delta: TiledCoverageStoreDelta): TiledCoverageStore {
    const tiles = new Map(this.#tiles);
    const changed = new Set<number>();
    for (const key of delta.removedTileKeys) {
      integer(key, 'Removed coverage tile key');
      if (key < 0 || key >= this.columnCount * this.rowCount || changed.has(key)) {
        throw new RangeError('Removed coverage tile key is invalid or duplicated');
      }
      changed.add(key);
      tiles.delete(key);
    }
    for (const [index, tile] of delta.tiles.entries()) {
      integer(tile.column, `Coverage delta tile ${String(index)} column`);
      integer(tile.row, `Coverage delta tile ${String(index)} row`);
      if (
        tile.column < 0 ||
        tile.row < 0 ||
        tile.column >= this.columnCount ||
        tile.row >= this.rowCount
      ) {
        throw new RangeError(`Coverage delta tile ${String(index)} is outside the store`);
      }
      const key = tileKey(tile.column, tile.row, this.columnCount);
      if (changed.has(key)) {
        throw new RangeError(`Coverage delta tile ${String(index)} is duplicated`);
      }
      changed.add(key);
      const region = this.tileRegion(tile.column, tile.row);
      if (!(tile.coverage instanceof Uint8ClampedArray)) {
        throw new TypeError(`Coverage delta tile ${String(index)} must use Uint8ClampedArray`);
      }
      if (tile.coverage.length !== region.width * region.height) {
        throw new RangeError(`Coverage delta tile ${String(index)} length does not match bounds`);
      }
      const owned = new Uint8ClampedArray(tile.coverage);
      if (tileIsUniform(owned, this.defaultCoverage)) {
        throw new RangeError(`Coverage delta tile ${String(index)} must differ from default`);
      }
      tiles.set(key, owned);
    }
    return new TiledCoverageStore(
      this.width,
      this.height,
      this.tileSize,
      this.defaultCoverage,
      tiles,
    );
  }

  private tileRegion(column: number, row: number): PixelRegion {
    const x = column * this.tileSize;
    const y = row * this.tileSize;
    return {
      x,
      y,
      width: Math.min(this.tileSize, this.width - x),
      height: Math.min(this.tileSize, this.height - y),
    };
  }
}

export class ContiguousPixelSource implements PixelSource {
  readonly width: number;
  readonly height: number;
  readonly #pixels: Uint8ClampedArray;

  constructor(width: number, height: number, pixels: ArrayLike<number>) {
    validateDimensions(width, height, DEFAULT_TILE_SIZE);
    if (pixels.length !== width * height * 4) {
      throw new RangeError('Pixel buffer length does not match its dimensions');
    }
    this.width = width;
    this.height = height;
    this.#pixels = new Uint8ClampedArray(pixels);
  }

  get allocatedByteLength(): number {
    return this.#pixels.byteLength;
  }

  *pixelChunks(region?: PixelRegion): IterableIterator<PixelPatch> {
    const requested = region
      ? intersectRegions(region, { x: 0, y: 0, width: this.width, height: this.height })
      : { x: 0, y: 0, width: this.width, height: this.height };
    if (!requested) return;
    yield { ...requested, pixels: this.read(requested) };
  }

  read(region: PixelRegion): Uint8ClampedArray {
    validateReadRegion(region);
    const output = new Uint8ClampedArray(region.width * region.height * 4);
    const clipped = intersectRegions(region, {
      x: 0,
      y: 0,
      width: this.width,
      height: this.height,
    });
    if (!clipped) return output;
    for (let y = clipped.y; y < clipped.y + clipped.height; y += 1) {
      const sourceStart = (y * this.width + clipped.x) * 4;
      const destinationStart = ((y - region.y) * region.width + (clipped.x - region.x)) * 4;
      output.set(
        this.#pixels.subarray(sourceStart, sourceStart + clipped.width * 4),
        destinationStart,
      );
    }
    return output;
  }

  toContiguous(): Uint8ClampedArray {
    return new Uint8ClampedArray(this.#pixels);
  }
}

export function intersectRegions(left: PixelRegion, right: PixelRegion): PixelRegion | null {
  const x = Math.max(left.x, right.x);
  const y = Math.max(left.y, right.y);
  const rightEdge = Math.min(left.x + left.width, right.x + right.width);
  const bottomEdge = Math.min(left.y + left.height, right.y + right.height);
  return rightEdge <= x || bottomEdge <= y
    ? null
    : { x, y, width: rightEdge - x, height: bottomEdge - y };
}

export function unionRegions(
  left: PixelRegion | null,
  right: PixelRegion | null,
): PixelRegion | null {
  if (!left) return right ? { ...right } : null;
  if (!right) return { ...left };
  const x = Math.min(left.x, right.x);
  const y = Math.min(left.y, right.y);
  const rightEdge = Math.max(left.x + left.width, right.x + right.width);
  const bottomEdge = Math.max(left.y + left.height, right.y + right.height);
  return { x, y, width: rightEdge - x, height: bottomEdge - y };
}

function regionsTouch(left: PixelRegion, right: PixelRegion): boolean {
  return (
    left.x <= right.x + right.width &&
    right.x <= left.x + left.width &&
    left.y <= right.y + right.height &&
    right.y <= left.y + left.height
  );
}

function validateDimensions(width: number, height: number, tileSize: number): void {
  positiveInteger(width, 'Pixel store width');
  positiveInteger(height, 'Pixel store height');
  positiveInteger(tileSize, 'Tile size');
  if (tileSize > MAX_TILE_SIZE)
    throw new RangeError(`Tile size cannot exceed ${String(MAX_TILE_SIZE)}`);
}

function validatePatch(patch: PixelPatch): void {
  integer(patch.x, 'Patch x');
  integer(patch.y, 'Patch y');
  positiveInteger(patch.width, 'Patch width');
  positiveInteger(patch.height, 'Patch height');
  if (patch.pixels.length !== patch.width * patch.height * 4) {
    throw new RangeError('Patch pixel length does not match its dimensions');
  }
}

function validateCoveragePatch(patch: CoveragePatch): void {
  integer(patch.x, 'Coverage patch x');
  integer(patch.y, 'Coverage patch y');
  positiveInteger(patch.width, 'Coverage patch width');
  positiveInteger(patch.height, 'Coverage patch height');
  if (patch.coverage.length !== patch.width * patch.height) {
    throw new RangeError('Coverage patch length does not match its dimensions');
  }
}

function validateReadRegion(region: PixelRegion): void {
  integer(region.x, 'Read x');
  integer(region.y, 'Read y');
  positiveInteger(region.width, 'Read width');
  positiveInteger(region.height, 'Read height');
}
