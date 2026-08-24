import type { ImageDocument, Layer, LayerTransform } from '@freetoshop/core-document';
import {
  DEFAULT_TILE_SIZE,
  intersectRegions,
  unionRegions,
  type PixelRegion,
} from '@freetoshop/pixel-storage';

export interface CompositeTileCacheOptions {
  /** Soft memory budget for cached composite tile buffers. */
  readonly maxBytes?: number;
  /** Composite tile edge length in document pixels. */
  readonly tileSize?: number;
}

export interface CompositeTileCacheStats {
  readonly hits: number;
  readonly misses: number;
  readonly evictions: number;
  readonly allocatedBytes: number;
  readonly tileCount: number;
  /**
   * Unique spatial tile coordinates currently resident (column/row/mip),
   * ignoring dependency-key revisions. Under edit churn `tileCount` can exceed
   * this when multiple revisions of the same location remain until LRU eviction.
   */
  readonly uniqueSpatialTileCount: number;
  /** True when the last compose assembled entirely from warm tiles. */
  readonly lastComposeFullyWarm: boolean;
  /** Viewport-intersecting tiles processed before off-screen tiles on the last compose. */
  readonly lastComposePrioritizedViewportTiles: number;
  /** Miss tiles filled via region-local composite rather than a full-frame reference. */
  readonly lastComposeRegionLocalMisses: number;
  /** Miss tiles filled by cropping a full-frame reference composite. */
  readonly lastComposeFullReferenceMisses: number;
}

export interface CompositeTileCacheComposeOptions {
  /**
   * Deterministic full-document reference composite. Used when many tiles miss
   * or region-local composition is unavailable.
   */
  readonly referenceComposite: (document: ImageDocument) => Uint8ClampedArray;
  /**
   * Optional already-downsampled full-document composite at `mipLevel`. This is
   * used to seed matching cache tiles from a worker without expanding the frame
   * back to full resolution first.
   */
  readonly mipReferenceComposite?: (document: ImageDocument) => Uint8ClampedArray;
  /**
   * Optional region-local composite. When provided and only a minority of tiles
   * miss, each miss is filled without a full-frame recompose.
   */
  readonly regionComposite?: (document: ImageDocument, region: PixelRegion) => Uint8ClampedArray;
  /**
   * Optional document-space dirty regions. Callers may also call `invalidate`
   * before compose; dependency keys independently drop stale content.
   */
  readonly dirtyRegions?: readonly PixelRegion[];
  /**
   * Document-space viewport. When set, tiles intersecting the viewport are
   * scheduled before off-screen tiles so progressive display can prioritize
   * interactive feedback.
   */
  readonly viewportRegion?: PixelRegion;
  /**
   * When true with `viewportRegion`, only tiles that intersect the viewport (or
   * dirty regions) are assembled into the returned buffer; off-screen tiles may
   * remain unpopulated until a later full compose. Default false for
   * export-equivalent full surfaces.
   */
  readonly progressiveViewportOnly?: boolean;
  /**
   * Mip level (0 = full resolution). Higher levels store 2^-mip scale tiles for
   * overview/zoomed-out display. Default 0.
   */
  readonly mipLevel?: number;
  /**
   * Optional cancellation. Checked between tile populations; throws DOMException
   * named AbortError when aborted.
   */
  readonly signal?: AbortSignal;
  /**
   * When true, miss tiles are left transparent and never filled via
   * `referenceComposite` / `regionComposite`. Used while a worker cold composite
   * is in flight so the UI thread does not re-run full-frame recomposition.
   */
  readonly warmAssembleOnly?: boolean;
}

interface CacheEntry {
  readonly dependencyKey: string;
  readonly pixels: Uint8ClampedArray;
  readonly width: number;
  readonly height: number;
  readonly byteLength: number;
  lastUsed: number;
}

const IDENTITY: LayerTransform = { a: 1, b: 0, c: 0, d: 1, e: 0, f: 0 };

const bufferIds = new WeakMap<object, number>();
let nextBufferId = 1;

function bufferId(value: object): number {
  const existing = bufferIds.get(value);
  if (existing !== undefined) return existing;
  const id = nextBufferId;
  nextBufferId += 1;
  bufferIds.set(value, id);
  return id;
}

function multiplyAffine(parent: LayerTransform, child: LayerTransform): LayerTransform {
  return {
    a: parent.a * child.a + parent.c * child.b,
    b: parent.b * child.a + parent.d * child.b,
    c: parent.a * child.c + parent.c * child.d,
    d: parent.b * child.c + parent.d * child.d,
    e: parent.a * child.e + parent.c * child.f + parent.e,
    f: parent.b * child.e + parent.d * child.f + parent.f,
  };
}

function tileRegion(
  column: number,
  row: number,
  tileSize: number,
  canvas: { readonly width: number; readonly height: number },
): PixelRegion {
  const x = column * tileSize;
  const y = row * tileSize;
  return {
    x,
    y,
    width: Math.min(tileSize, canvas.width - x),
    height: Math.min(tileSize, canvas.height - y),
  };
}

function regionsOverlap(left: PixelRegion, right: PixelRegion): boolean {
  return intersectRegions(left, right) !== null;
}

function sourceRegionForDocumentRegion(
  region: PixelRegion,
  transform: LayerTransform,
  width: number,
  height: number,
): PixelRegion | null {
  const determinant = transform.a * transform.d - transform.b * transform.c;
  if (!Number.isFinite(determinant) || Math.abs(determinant) < Number.EPSILON) {
    return { x: 0, y: 0, width, height };
  }
  const inverse = {
    a: transform.d / determinant,
    b: -transform.b / determinant,
    c: -transform.c / determinant,
    d: transform.a / determinant,
    e: (transform.c * transform.f - transform.d * transform.e) / determinant,
    f: (transform.b * transform.e - transform.a * transform.f) / determinant,
  };
  const map = (x: number, y: number) => ({
    x: inverse.a * x + inverse.c * y + inverse.e,
    y: inverse.b * x + inverse.d * y + inverse.f,
  });
  const corners = [
    map(region.x, region.y),
    map(region.x + region.width, region.y),
    map(region.x, region.y + region.height),
    map(region.x + region.width, region.y + region.height),
  ];
  const left = Math.floor(Math.min(...corners.map((point) => point.x)));
  const top = Math.floor(Math.min(...corners.map((point) => point.y)));
  const right = Math.ceil(Math.max(...corners.map((point) => point.x)));
  const bottom = Math.ceil(Math.max(...corners.map((point) => point.y)));
  return intersectRegions(
    { x: left, y: top, width: right - left, height: bottom - top },
    { x: 0, y: 0, width, height },
  );
}

function assertNotAborted(signal: AbortSignal | undefined): void {
  if (signal?.aborted) {
    const error = new Error('Composite tile cache work was cancelled');
    error.name = 'AbortError';
    throw error;
  }
}

function downsampleRgba(
  source: Uint8ClampedArray,
  sourceWidth: number,
  sourceHeight: number,
  mipLevel: number,
): { pixels: Uint8ClampedArray; width: number; height: number } {
  if (mipLevel <= 0) return { pixels: source, width: sourceWidth, height: sourceHeight };
  let pixels = source;
  let width = sourceWidth;
  let height = sourceHeight;
  for (let level = 0; level < mipLevel; level += 1) {
    const nextWidth = Math.max(1, Math.ceil(width / 2));
    const nextHeight = Math.max(1, Math.ceil(height / 2));
    const next = new Uint8ClampedArray(nextWidth * nextHeight * 4);
    for (let y = 0; y < nextHeight; y += 1) {
      for (let x = 0; x < nextWidth; x += 1) {
        const sourceX = Math.min(width - 1, x * 2);
        const sourceY = Math.min(height - 1, y * 2);
        const sourceOffset = (sourceY * width + sourceX) * 4;
        const destOffset = (y * nextWidth + x) * 4;
        next[destOffset] = pixels[sourceOffset] ?? 0;
        next[destOffset + 1] = pixels[sourceOffset + 1] ?? 0;
        next[destOffset + 2] = pixels[sourceOffset + 2] ?? 0;
        next[destOffset + 3] = pixels[sourceOffset + 3] ?? 0;
      }
    }
    pixels = next;
    width = nextWidth;
    height = nextHeight;
  }
  return { pixels, width, height };
}

/**
 * Builds a dependency signature for the layer stack over a document-space region.
 * Includes visibility, opacity, blend, clipping, transforms, effects, and source
 * tile buffer identities that intersect the region so COW pixel edits invalidate
 * only dependent composite tiles.
 */
export function compositeTileDependencyKey(
  layers: readonly Layer[],
  region: PixelRegion,
  parentTransform: LayerTransform = IDENTITY,
  parentOpacity = 1,
): string {
  const parts: string[] = [
    `r:${String(region.x)},${String(region.y)},${String(region.width)}x${String(region.height)}`,
  ];
  for (const layer of layers) {
    const transform = multiplyAffine(parentTransform, layer.transform);
    const xf = [transform.a, transform.b, transform.c, transform.d, transform.e, transform.f]
      .map((value) => String(value))
      .join(',');
    parts.push(
      [
        layer.id,
        layer.kind,
        layer.visible ? 1 : 0,
        layer.opacity * parentOpacity,
        layer.fillOpacity,
        layer.blendMode,
        layer.clipping ? 1 : 0,
        xf,
      ].join(':'),
    );
    if (!layer.visible || layer.opacity <= 0) continue;
    if (layer.kind === 'pixel') {
      const tiles: string[] = [];
      const localRegion = sourceRegionForDocumentRegion(
        region,
        transform,
        layer.width,
        layer.height,
      );
      const chunks = localRegion
        ? layer.pixelStore.pixelChunks(localRegion)
        : layer.pixelStore.pixelChunks({
            x: 0,
            y: 0,
            width: layer.width,
            height: layer.height,
          });
      for (const chunk of chunks) {
        // Buffer identity preserves COW hits for unchanged source tiles.
        tiles.push(`${String(chunk.x)},${String(chunk.y)}:${String(bufferId(chunk.pixels))}`);
      }
      parts.push(`px:[${tiles.join('|')}]`);
      if (layer.rasterMask) {
        parts.push(
          `rm:${String(bufferId(layer.rasterMask.coverageStore))}:d${String(layer.rasterMask.density)}`,
        );
      }
      parts.push(`fx:${String(layer.effects.length)}`);
    } else if (layer.kind === 'group') {
      parts.push(
        compositeTileDependencyKey(
          layer.children,
          region,
          transform,
          layer.compositing === 'pass-through' ? parentOpacity * layer.opacity : 1,
        ),
      );
      parts.push(`comp:${layer.compositing}`);
    } else if (layer.kind === 'adjustment') {
      parts.push(`adj:${JSON.stringify(layer.adjustment)}`);
    } else if (layer.kind === 'shape') {
      parts.push(
        `shape:${layer.geometry.type}:${String(layer.geometry.width)}x${String(layer.geometry.height)}:fx${String(layer.effects.length)}`,
      );
    } else if (layer.kind === 'text') {
      parts.push(
        `text:${String(layer.width)}x${String(layer.height)}:c${layer.text}:fx${String(layer.effects.length)}`,
      );
    } else {
      parts.push(
        `smart:${layer.source.id}:f${String(layer.smartFilters.length)}:fx${String(layer.effects.length)}`,
      );
    }
  }
  return parts.join('||');
}

/**
 * Dependency-keyed composite tile cache.
 *
 * Cache keys include layer dependency signatures, tile coordinates, and mip level
 * (currently mip 0). On miss, the deterministic reference composite populates tiles.
 * Warm re-composites with unchanged dependencies assemble from cached tiles without
 * invoking the reference compositor.
 */
export class CompositeTileCache {
  readonly tileSize: number;
  readonly maxBytes: number;
  #hits = 0;
  #misses = 0;
  #evictions = 0;
  #clock = 0;
  #lastComposeFullyWarm = false;
  #lastComposePrioritizedViewportTiles = 0;
  #lastComposeRegionLocalMisses = 0;
  #lastComposeFullReferenceMisses = 0;
  readonly #tiles = new Map<string, CacheEntry>();

  constructor(options: CompositeTileCacheOptions = {}) {
    this.tileSize = options.tileSize ?? Math.min(DEFAULT_TILE_SIZE, 64);
    this.maxBytes = options.maxBytes ?? 64 * 1024 * 1024;
    if (!Number.isSafeInteger(this.tileSize) || this.tileSize <= 0) {
      throw new RangeError('Composite tile size must be a positive integer');
    }
    if (!Number.isSafeInteger(this.maxBytes) || this.maxBytes <= 0) {
      throw new RangeError('Composite cache maxBytes must be a positive integer');
    }
  }

  get stats(): CompositeTileCacheStats {
    return {
      hits: this.#hits,
      misses: this.#misses,
      evictions: this.#evictions,
      allocatedBytes: this.#allocatedBytes(),
      tileCount: this.#tiles.size,
      uniqueSpatialTileCount: this.#uniqueSpatialTileCount(),
      lastComposeFullyWarm: this.#lastComposeFullyWarm,
      lastComposePrioritizedViewportTiles: this.#lastComposePrioritizedViewportTiles,
      lastComposeRegionLocalMisses: this.#lastComposeRegionLocalMisses,
      lastComposeFullReferenceMisses: this.#lastComposeFullReferenceMisses,
    };
  }

  clear(): void {
    this.#tiles.clear();
    this.#hits = 0;
    this.#misses = 0;
    this.#evictions = 0;
    this.#lastComposeFullyWarm = false;
    this.#lastComposePrioritizedViewportTiles = 0;
    this.#lastComposeRegionLocalMisses = 0;
    this.#lastComposeFullReferenceMisses = 0;
  }

  /**
   * Returns how many planned tiles would hit the cache without computing pixels.
   * Used by the UI thread to choose warm main-thread assemble vs worker-scheduled cold work.
   */
  probeWarmth(
    document: ImageDocument,
    options: {
      readonly viewportRegion?: PixelRegion;
      readonly progressiveViewportOnly?: boolean;
      readonly mipLevel?: number;
    } = {},
  ): { readonly total: number; readonly missing: number; readonly fullyWarm: boolean } {
    const { width, height } = document.canvas;
    const mipLevel = options.mipLevel ?? 0;
    const scale = 2 ** mipLevel;
    const mipWidth = Math.max(1, Math.ceil(width / scale));
    const mipHeight = Math.max(1, Math.ceil(height / scale));
    const columns = Math.ceil(mipWidth / this.tileSize);
    const rows = Math.ceil(mipHeight / this.tileSize);
    const viewport = options.viewportRegion
      ? intersectRegions(options.viewportRegion, { x: 0, y: 0, width, height })
      : null;
    let total = 0;
    let missing = 0;
    for (let row = 0; row < rows; row += 1) {
      for (let column = 0; column < columns; column += 1) {
        const region = tileRegion(column, row, this.tileSize, {
          width: mipWidth,
          height: mipHeight,
        });
        const documentRegion = {
          x: region.x * scale,
          y: region.y * scale,
          width: Math.min(region.width * scale, width - region.x * scale),
          height: Math.min(region.height * scale, height - region.y * scale),
        };
        const inViewport = viewport ? regionsOverlap(documentRegion, viewport) : true;
        if (options.progressiveViewportOnly && viewport && !inViewport) continue;
        total += 1;
        const dependencyKey = `${compositeTileDependencyKey(document.layers, documentRegion)}:mip${String(mipLevel)}`;
        const cacheKey = `t:${String(column)}:${String(row)}:m${String(mipLevel)}:${dependencyKey}`;
        if (!this.#tiles.has(cacheKey)) missing += 1;
      }
    }
    return { total, missing, fullyWarm: total > 0 && missing === 0 };
  }

  /**
   * Drops composite tiles that intersect any region so the next compose recomputes them.
   */
  invalidate(regions: readonly PixelRegion[], canvas: { width: number; height: number }): void {
    const columns = Math.ceil(canvas.width / this.tileSize);
    const rows = Math.ceil(canvas.height / this.tileSize);
    for (let row = 0; row < rows; row += 1) {
      for (let column = 0; column < columns; column += 1) {
        const region = tileRegion(column, row, this.tileSize, canvas);
        if (!regions.some((item) => regionsOverlap(item, region))) continue;
        // Delete every entry for this tile coordinate regardless of dependency key.
        for (const key of [...this.#tiles.keys()]) {
          if (key.startsWith(`t:${String(column)}:${String(row)}:`)) this.#tiles.delete(key);
        }
      }
    }
  }

  /**
   * Seeds dependency-keyed mip tiles directly from a compact mip-space worker region.
   * The supplied region must follow this cache's tile grid; no full-canvas buffer is created.
   */
  seedRegion(
    document: ImageDocument,
    pixels: Uint8ClampedArray,
    pixelRegion: PixelRegion,
    mipLevel = 0,
  ): void {
    if (!Number.isSafeInteger(mipLevel) || mipLevel < 0) {
      throw new RangeError('mipLevel must be a non-negative integer');
    }
    const scale = 2 ** mipLevel;
    const mipWidth = Math.max(1, Math.ceil(document.canvas.width / scale));
    const mipHeight = Math.max(1, Math.ceil(document.canvas.height / scale));
    const clipped = intersectRegions(pixelRegion, {
      x: 0,
      y: 0,
      width: mipWidth,
      height: mipHeight,
    });
    if (
      !clipped ||
      clipped.x !== pixelRegion.x ||
      clipped.y !== pixelRegion.y ||
      clipped.width !== pixelRegion.width ||
      clipped.height !== pixelRegion.height
    ) {
      throw new RangeError('Composite seed region must be inside the mip canvas');
    }
    if (pixels.length !== pixelRegion.width * pixelRegion.height * 4) {
      throw new RangeError('Composite seed pixels do not match the region dimensions');
    }
    const firstColumn = Math.floor(pixelRegion.x / this.tileSize);
    const lastColumn = Math.floor((pixelRegion.x + pixelRegion.width - 1) / this.tileSize);
    const firstRow = Math.floor(pixelRegion.y / this.tileSize);
    const lastRow = Math.floor((pixelRegion.y + pixelRegion.height - 1) / this.tileSize);
    for (let row = firstRow; row <= lastRow; row += 1) {
      for (let column = firstColumn; column <= lastColumn; column += 1) {
        const region = tileRegion(column, row, this.tileSize, {
          width: mipWidth,
          height: mipHeight,
        });
        const overlap = intersectRegions(region, pixelRegion);
        if (!overlap || overlap.width !== region.width || overlap.height !== region.height) {
          throw new RangeError('Composite seed region must contain complete cache tiles');
        }
        const documentRegion = {
          x: region.x * scale,
          y: region.y * scale,
          width: Math.min(region.width * scale, document.canvas.width - region.x * scale),
          height: Math.min(region.height * scale, document.canvas.height - region.y * scale),
        };
        const dependencyKey = `${compositeTileDependencyKey(document.layers, documentRegion)}:mip${String(mipLevel)}`;
        const cacheKey = `t:${String(column)}:${String(row)}:m${String(mipLevel)}:${dependencyKey}`;
        const tilePixels = cropRgba(pixels, pixelRegion.width, pixelRegion.height, {
          x: region.x - pixelRegion.x,
          y: region.y - pixelRegion.y,
          width: region.width,
          height: region.height,
        });
        this.#tiles.set(cacheKey, {
          dependencyKey,
          pixels: tilePixels,
          width: region.width,
          height: region.height,
          byteLength: tilePixels.byteLength,
          lastUsed: this.#touch(),
        });
        this.#evictIfNeeded();
      }
    }
  }

  /**
   * Composites the document into a full-canvas buffer using dependency-keyed tiles.
   * When `progressiveViewportOnly` is false (default), output is pixel-equivalent
   * to `options.referenceComposite(document)`.
   */
  composite(document: ImageDocument, options: CompositeTileCacheComposeOptions): Uint8ClampedArray {
    assertNotAborted(options.signal);
    const { width, height } = document.canvas;
    const mipLevel =
      options.mipLevel === undefined
        ? 0
        : Number.isSafeInteger(options.mipLevel) && options.mipLevel >= 0
          ? options.mipLevel
          : (() => {
              throw new RangeError('mipLevel must be a non-negative integer');
            })();
    const scale = 2 ** mipLevel;
    const mipWidth = Math.max(1, Math.ceil(width / scale));
    const mipHeight = Math.max(1, Math.ceil(height / scale));
    const columns = Math.ceil(mipWidth / this.tileSize);
    const rows = Math.ceil(mipHeight / this.tileSize);
    const dirty = options.dirtyRegions?.length
      ? options.dirtyRegions
          .map((region) => intersectRegions(region, { x: 0, y: 0, width, height }))
          .filter((region): region is PixelRegion => region !== null)
      : null;
    const viewport = options.viewportRegion
      ? intersectRegions(options.viewportRegion, { x: 0, y: 0, width, height })
      : null;

    type Plan = {
      readonly column: number;
      readonly row: number;
      readonly region: PixelRegion;
      readonly documentRegion: PixelRegion;
      readonly dependencyKey: string;
      readonly cacheKey: string;
      readonly entry: CacheEntry | undefined;
      readonly inViewport: boolean;
      readonly inDirty: boolean;
      readonly priority: number;
    };

    const plan: Plan[] = [];
    for (let row = 0; row < rows; row += 1) {
      for (let column = 0; column < columns; column += 1) {
        const region = tileRegion(column, row, this.tileSize, {
          width: mipWidth,
          height: mipHeight,
        });
        const documentRegion = {
          x: region.x * scale,
          y: region.y * scale,
          width: Math.min(region.width * scale, width - region.x * scale),
          height: Math.min(region.height * scale, height - region.y * scale),
        };
        const inViewport = viewport ? regionsOverlap(documentRegion, viewport) : true;
        const inDirty = dirty ? dirty.some((item) => regionsOverlap(item, documentRegion)) : false;
        if (options.progressiveViewportOnly && viewport && !inViewport && !inDirty) {
          continue;
        }
        const dependencyKey = `${compositeTileDependencyKey(document.layers, documentRegion)}:mip${String(mipLevel)}`;
        const cacheKey = `t:${String(column)}:${String(row)}:m${String(mipLevel)}:${dependencyKey}`;
        const entry = this.#tiles.get(cacheKey);
        const centerX = documentRegion.x + documentRegion.width / 2;
        const centerY = documentRegion.y + documentRegion.height / 2;
        const viewCenterX = viewport ? viewport.x + viewport.width / 2 : centerX;
        const viewCenterY = viewport ? viewport.y + viewport.height / 2 : centerY;
        const distance =
          (centerX - viewCenterX) * (centerX - viewCenterX) +
          (centerY - viewCenterY) * (centerY - viewCenterY);
        // Lower priority value = earlier scheduling: viewport first, then dirt, then distance.
        const priority = (inViewport ? 0 : 2) + (inDirty ? 0 : 1) + distance / 1e12;
        plan.push({
          column,
          row,
          region,
          documentRegion,
          dependencyKey,
          cacheKey,
          entry,
          inViewport,
          inDirty,
          priority,
        });
      }
    }

    plan.sort((left, right) => left.priority - right.priority);
    this.#lastComposePrioritizedViewportTiles = plan.filter((item) => item.inViewport).length;

    const missing = plan.reduce((count, item) => count + (item.entry ? 0 : 1), 0);
    this.#lastComposeFullyWarm = missing === 0 && plan.length > 0;
    this.#lastComposeRegionLocalMisses = 0;
    this.#lastComposeFullReferenceMisses = 0;

    // Prefer a single batched region-local fill when the miss footprint is smaller
    // than half the canvas, when only a minority of planned tiles miss, or when
    // progressive viewport-only mode is active (never pay full-frame for a viewport).
    // warmAssembleOnly never fills misses (worker / progressive placeholder path).
    const warmAssembleOnly = options.warmAssembleOnly === true;
    const regionComposite = options.regionComposite;
    const missItems = plan.filter((item) => !item.entry);
    let missUnion: PixelRegion | null = null;
    for (const missItem of missItems) {
      missUnion = missUnion
        ? (unionRegions(missUnion, missItem.documentRegion) ?? missUnion)
        : missItem.documentRegion;
    }
    const missArea = missUnion ? missUnion.width * missUnion.height : 0;
    const canvasArea = width * height;
    const preferRegionLocal =
      options.progressiveViewportOnly === true ||
      missing <= Math.max(1, Math.floor(plan.length / 2)) ||
      missArea <= canvasArea * 0.5;

    let reference: Uint8ClampedArray | null = null;
    let batchRegionPixels: Uint8ClampedArray | null = null;
    let batchRegion: PixelRegion | null = null;
    if (!warmAssembleOnly && missing > 0 && preferRegionLocal && regionComposite && missUnion) {
      assertNotAborted(options.signal);
      batchRegion = missUnion;
      batchRegionPixels = regionComposite(document, missUnion);
      if (batchRegionPixels.length !== missUnion.width * missUnion.height * 4) {
        throw new RangeError(
          'Region composite size does not match the requested miss-union region',
        );
      }
    } else if (!warmAssembleOnly && missing > 0) {
      assertNotAborted(options.signal);
      reference = options.mipReferenceComposite
        ? options.mipReferenceComposite(document)
        : options.referenceComposite(document);
      const expectedReferenceLength = options.mipReferenceComposite
        ? mipWidth * mipHeight * 4
        : width * height * 4;
      if (reference.length !== expectedReferenceLength) {
        throw new RangeError('Reference composite size does not match the requested mip canvas');
      }
      this.#lastComposeFullReferenceMisses = missing;
    }

    const output = new Uint8ClampedArray(mipWidth * mipHeight * 4);
    for (const item of plan) {
      assertNotAborted(options.signal);
      let entry = this.#tiles.get(item.cacheKey);
      if (entry) {
        this.#hits += 1;
        entry.lastUsed = this.#touch();
      } else {
        this.#misses += 1;
        if (warmAssembleOnly) {
          // Leave transparent holes; do not cache empty miss entries.
          continue;
        }
        let cropped: Uint8ClampedArray;
        if (batchRegionPixels && batchRegion) {
          const relative: PixelRegion = {
            x: item.documentRegion.x - batchRegion.x,
            y: item.documentRegion.y - batchRegion.y,
            width: item.documentRegion.width,
            height: item.documentRegion.height,
          };
          cropped = cropRgba(batchRegionPixels, batchRegion.width, batchRegion.height, relative);
          this.#lastComposeRegionLocalMisses += 1;
        } else {
          if (!reference) {
            throw new Error('Composite tile miss without a reference composite');
          }
          if (options.mipReferenceComposite) {
            cropped = cropRgba(reference, mipWidth, mipHeight, item.region);
          } else {
            cropped = cropRgba(reference, width, height, item.documentRegion);
          }
        }
        const mip = options.mipReferenceComposite
          ? { pixels: cropped, width: item.region.width, height: item.region.height }
          : downsampleRgba(
              cropped,
              item.documentRegion.width,
              item.documentRegion.height,
              mipLevel,
            );
        entry = {
          dependencyKey: item.dependencyKey,
          pixels: mip.pixels,
          width: mip.width,
          height: mip.height,
          byteLength: mip.pixels.byteLength,
          lastUsed: this.#touch(),
        };
        this.#tiles.set(item.cacheKey, entry);
        this.#evictIfNeeded();
      }
      this.#blit(output, mipWidth, entry.pixels, {
        x: item.region.x,
        y: item.region.y,
        width: entry.width,
        height: entry.height,
      });
    }
    return output;
  }

  /**
   * Document-space region currently covered by a screen viewport transform.
   * Used for viewport-priority scheduling and progressive display.
   */
  static viewportDocumentRegion(
    viewport: { readonly zoom: number; readonly panX: number; readonly panY: number },
    screenWidth: number,
    screenHeight: number,
    canvas: { readonly width: number; readonly height: number },
  ): PixelRegion {
    const zoom = viewport.zoom === 0 ? 1 : viewport.zoom;
    const x = Math.floor(-viewport.panX / zoom);
    const y = Math.floor(-viewport.panY / zoom);
    const width = Math.ceil(screenWidth / zoom);
    const height = Math.ceil(screenHeight / zoom);
    return (
      intersectRegions(
        { x, y, width, height },
        { x: 0, y: 0, width: canvas.width, height: canvas.height },
      ) ?? { x: 0, y: 0, width: 0, height: 0 }
    );
  }

  #blit(
    destination: Uint8ClampedArray,
    destinationWidth: number,
    tile: Uint8ClampedArray,
    region: PixelRegion,
  ): void {
    for (let row = 0; row < region.height; row += 1) {
      const sourceStart = row * region.width * 4;
      const destStart = ((region.y + row) * destinationWidth + region.x) * 4;
      destination.set(tile.subarray(sourceStart, sourceStart + region.width * 4), destStart);
    }
  }

  #allocatedBytes(): number {
    let total = 0;
    for (const entry of this.#tiles.values()) total += entry.byteLength;
    return total;
  }

  /**
   * Count distinct spatial tile locations currently resident.
   * Cache keys are `t:{col}:{row}:m{mip}:{dependencyKey}`.
   */
  #uniqueSpatialTileCount(): number {
    const seen = new Set<string>();
    for (const key of this.#tiles.keys()) {
      // t:col:row:mN:... → spatial id "col:row:mN"
      const match = /^t:(\d+):(\d+):(m\d+):/.exec(key);
      if (match) {
        seen.add(`${match[1]}:${match[2]}:${match[3]}`);
      }
    }
    return seen.size;
  }

  #touch(): number {
    this.#clock += 1;
    return this.#clock;
  }

  #evictIfNeeded(): void {
    while (this.#allocatedBytes() > this.maxBytes && this.#tiles.size > 0) {
      let oldestKey: string | null = null;
      let oldestTime = Number.POSITIVE_INFINITY;
      for (const [key, entry] of this.#tiles) {
        if (entry.lastUsed < oldestTime) {
          oldestTime = entry.lastUsed;
          oldestKey = key;
        }
      }
      if (!oldestKey) break;
      this.#tiles.delete(oldestKey);
      this.#evictions += 1;
    }
  }
}

function cropRgba(
  source: Uint8ClampedArray,
  sourceWidth: number,
  sourceHeight: number,
  region: PixelRegion,
): Uint8ClampedArray {
  const output = new Uint8ClampedArray(region.width * region.height * 4);
  for (let row = 0; row < region.height; row += 1) {
    const sourceY = region.y + row;
    if (sourceY < 0 || sourceY >= sourceHeight) continue;
    if (region.x >= sourceWidth || region.x + region.width <= 0) continue;
    const copyX = Math.max(0, region.x);
    const copyWidth = Math.min(region.width, sourceWidth - copyX);
    if (copyWidth <= 0) continue;
    const sourceStart = (sourceY * sourceWidth + copyX) * 4;
    const destStart = (row * region.width + (copyX - region.x)) * 4;
    output.set(source.subarray(sourceStart, sourceStart + copyWidth * 4), destStart);
  }
  return output;
}
