import type {
  AlphaChannel,
  GroupLayer,
  ImageDocument,
  Layer,
  LayerTransform,
  PixelLayer,
  PixelPatch,
  RasterMask,
  SelectionMask,
  SmartObjectLayer,
} from '@freetoshop/core-document';
import {
  identityLayerTransform,
  invertLayerTransform,
  multiplyLayerTransforms,
  transformPoint,
} from '@freetoshop/core-document';
import {
  TiledCoverageStore,
  TiledPixelStore,
  type TiledCoverageStoreSnapshot,
  type TiledPixelStoreSnapshot,
  type PixelRegion,
} from '@freetoshop/pixel-storage';
import type { PsdImportReport } from '@freetoshop/file-codecs/psd';
import type { ResamplingMethod } from '@freetoshop/raster-engine';
import type { FilterDraft } from '../editor/filterOperations.js';

export type ImageWorkerTask =
  | {
      readonly kind: 'filter';
      readonly layer: PixelLayer;
      readonly selection: SelectionMask | null;
      readonly worldTransform: LayerTransform;
      readonly draft: FilterDraft;
    }
  | {
      readonly kind: 'resize';
      readonly document: ImageDocument;
      readonly width: number;
      readonly height: number;
      readonly method: ResamplingMethod;
    }
  | {
      readonly kind: 'psd-import';
      readonly buffer: ArrayBuffer;
      readonly name: string;
    }
  | {
      readonly kind: 'native-import';
      readonly buffer: ArrayBuffer;
    }
  | {
      readonly kind: 'native-export';
      readonly document: ImageDocument;
    }
  | {
      /** Deterministic composite for off-main-thread cold frames. */
      readonly kind: 'composite';
      readonly document: ImageDocument;
      /** Client generation used for stale-result rejection. */
      readonly generation: number;
      /**
       * Optional progressive viewport. When set with progressiveViewportOnly,
       * the worker returns only viewport-intersecting mip tiles with pixelRegion metadata.
       */
      readonly viewportRegion?: {
        readonly x: number;
        readonly y: number;
        readonly width: number;
        readonly height: number;
      };
      readonly progressiveViewportOnly?: boolean;
      readonly mipLevel?: number;
    };

export type ImageWorkerResult =
  | { readonly kind: 'filter'; readonly patches: readonly PixelPatch[] }
  | { readonly kind: 'resize'; readonly document: ImageDocument }
  | {
      readonly kind: 'psd-import';
      readonly document: ImageDocument;
      readonly report: PsdImportReport;
    }
  | { readonly kind: 'native-import'; readonly document: ImageDocument }
  | { readonly kind: 'native-export'; readonly buffer: ArrayBuffer }
  | {
      readonly kind: 'composite';
      readonly buffer: ArrayBuffer;
      readonly width: number;
      readonly height: number;
      readonly generation: number;
      /** Full mip-canvas dimensions when `pixelRegion` makes width/height compact. */
      readonly canvasWidth?: number;
      readonly canvasHeight?: number;
      /** Mip-space origin and dimensions of a compact progressive buffer. */
      readonly pixelRegion?: PixelRegion;
      readonly viewportRegion?: {
        readonly x: number;
        readonly y: number;
        readonly width: number;
        readonly height: number;
      };
      readonly progressiveViewportOnly?: boolean;
      readonly mipLevel?: number;
    };

type SerializedRasterMask = Omit<RasterMask, 'coverageStore'> & {
  readonly coverageStore: SerializedCoverageResource;
};
type SerializedSelectionMask = Omit<SelectionMask, 'coverageStore'> & {
  readonly coverageStore: SerializedCoverageResource;
};
type SerializedAlphaChannel = Omit<AlphaChannel, 'coverageStore'> & {
  readonly coverageStore: SerializedCoverageResource;
};
type WithSerializedRasterMask<T extends Layer> = T extends Layer
  ? Omit<T, 'rasterMask'> & { readonly rasterMask: SerializedRasterMask | null }
  : never;
type SerializedPixelLayer = Omit<PixelLayer, 'pixelStore' | 'rasterMask'> & {
  readonly pixelStore: SerializedPixelResource;
  readonly rasterMask: SerializedRasterMask | null;
};
type SerializedGroupLayer = Omit<GroupLayer, 'children' | 'rasterMask'> & {
  readonly children: readonly SerializedLayer[];
  readonly rasterMask: SerializedRasterMask | null;
};
type SerializedSmartObjectLayer = Omit<SmartObjectLayer, 'source' | 'rasterMask'> & {
  readonly source: Omit<SmartObjectLayer['source'], 'layers'> & {
    readonly layers: readonly SerializedLayer[];
  };
  readonly rasterMask: SerializedRasterMask | null;
};
type UnchangedLayer = Exclude<Layer, PixelLayer | GroupLayer | SmartObjectLayer>;
type SerializedLayer =
  | SerializedPixelLayer
  | SerializedGroupLayer
  | SerializedSmartObjectLayer
  | WithSerializedRasterMask<UnchangedLayer>;
type SerializedDocument = Omit<ImageDocument, 'layers' | 'selection' | 'channels'> & {
  readonly layers: readonly SerializedLayer[];
  readonly selection: SerializedSelectionMask | null;
  readonly channels: readonly SerializedAlphaChannel[];
};

export type SerializedPixelResource =
  | {
      readonly resource: 'snapshot';
      readonly schemaVersion: 1;
      readonly id: string;
      readonly snapshot: TiledPixelStoreSnapshot;
    }
  | { readonly resource: 'reference'; readonly schemaVersion: 1; readonly id: string }
  | {
      readonly resource: 'delta';
      readonly schemaVersion: 2;
      readonly id: string;
      readonly baseId: string;
      readonly tiles: TiledPixelStoreSnapshot['tiles'];
      readonly removedTileKeys: readonly number[];
    };

export type SerializedCoverageResource =
  | {
      readonly resource: 'snapshot';
      readonly schemaVersion: 1;
      readonly id: string;
      readonly snapshot: TiledCoverageStoreSnapshot;
    }
  | { readonly resource: 'reference'; readonly schemaVersion: 1; readonly id: string }
  | {
      readonly resource: 'delta';
      readonly schemaVersion: 2;
      readonly id: string;
      readonly baseId: string;
      readonly tiles: TiledCoverageStoreSnapshot['tiles'];
      readonly removedTileKeys: readonly number[];
    };

interface PixelResourceSlot {
  readonly id: string;
  readonly store: TiledPixelStore;
}

interface CoverageResourceSlot {
  readonly id: string;
  readonly store: TiledCoverageStore;
}

export interface WorkerTaskSerializationContext {
  readonly pixelResources: WeakMap<TiledPixelStore, Map<string, string>>;
  readonly coverageResources: WeakMap<TiledCoverageStore, Map<string, string>>;
  readonly availableResources: Set<string>;
  readonly resourceOwners: Map<string, object>;
  readonly activeOwners: Set<object>;
  readonly pixelSlots: Map<string, Map<string, PixelResourceSlot>>;
  readonly coverageSlots: Map<string, Map<string, CoverageResourceSlot>>;
  readonly resourceIdPrefix: string;
  nextResourceId: number;
}

export interface WorkerTaskDeserializationContext {
  readonly pixelResources: Map<string, TiledPixelStore>;
  readonly coverageResources: Map<string, TiledCoverageStore>;
}

export function createWorkerTaskSerializationContext(
  resourceIdPrefix = '',
): WorkerTaskSerializationContext {
  return {
    pixelResources: new WeakMap(),
    coverageResources: new WeakMap(),
    availableResources: new Set(),
    resourceOwners: new Map(),
    activeOwners: new Set(),
    pixelSlots: new Map(),
    coverageSlots: new Map(),
    resourceIdPrefix,
    nextResourceId: 1,
  };
}

export function retainedWorkerTaskResourceIds(
  context: WorkerTaskSerializationContext,
): readonly string[] {
  return [...context.resourceOwners]
    .filter(([, owner]) => context.activeOwners.has(owner))
    .map(([id]) => id)
    .sort();
}

export function workerTaskRetainedResourceStats(context: WorkerTaskSerializationContext): {
  readonly resourceCount: number;
  readonly allocatedByteLength: number;
} {
  const stores = new Set(context.resourceOwners.values());
  let allocatedByteLength = 0;
  for (const store of stores) {
    if (store instanceof TiledPixelStore || store instanceof TiledCoverageStore) {
      allocatedByteLength += store.allocatedByteLength;
    }
  }
  return { resourceCount: stores.size, allocatedByteLength };
}

function pruneWorkerTaskSerializationContext(
  context: WorkerTaskSerializationContext,
  retained: ReadonlySet<string>,
): void {
  for (const id of context.availableResources) {
    if (!retained.has(id)) context.availableResources.delete(id);
  }
  for (const id of context.resourceOwners.keys()) {
    if (!retained.has(id)) context.resourceOwners.delete(id);
  }
  for (const [slot, scopes] of context.pixelSlots) {
    for (const [scope, value] of scopes) {
      if (!retained.has(value.id)) scopes.delete(scope);
    }
    if (scopes.size === 0) context.pixelSlots.delete(slot);
  }
  for (const [slot, scopes] of context.coverageSlots) {
    for (const [scope, value] of scopes) {
      if (!retained.has(value.id)) scopes.delete(scope);
    }
    if (scopes.size === 0) context.coverageSlots.delete(slot);
  }
}

/** Marks snapshots from the last serialized request as resolvable by the persistent worker. */
export function commitWorkerTaskResources(
  task: SerializedImageWorkerTask,
  context: WorkerTaskSerializationContext,
): void {
  const visitCoverage = (resource: SerializedCoverageResource) => {
    if (resource.resource !== 'reference') context.availableResources.add(resource.id);
  };
  const visitLayer = (layer: SerializedLayer) => {
    if (layer.rasterMask !== null) visitCoverage(layer.rasterMask.coverageStore);
    if (layer.kind === 'pixel') {
      if (layer.pixelStore.resource !== 'reference') {
        context.availableResources.add(layer.pixelStore.id);
      }
    } else if (layer.kind === 'group') layer.children.forEach(visitLayer);
    else if (layer.kind === 'smart-object') layer.source.layers.forEach(visitLayer);
  };
  if (task.kind === 'filter') {
    visitLayer(task.layer);
    if (task.selection !== null) visitCoverage(task.selection.coverageStore);
  } else if (task.kind === 'resize' || task.kind === 'native-export' || task.kind === 'composite') {
    task.document.layers.forEach(visitLayer);
    if (task.document.selection !== null) visitCoverage(task.document.selection.coverageStore);
    task.document.channels.forEach((channel) => visitCoverage(channel.coverageStore));
  }
  const retained = new Set(retainedWorkerTaskResourceIds(context));
  pruneWorkerTaskSerializationContext(context, retained);
  context.activeOwners.clear();
}

export function createWorkerTaskDeserializationContext(): WorkerTaskDeserializationContext {
  return { pixelResources: new Map(), coverageResources: new Map() };
}

export function pruneWorkerTaskDeserializationContext(
  context: WorkerTaskDeserializationContext,
  retainedIds: readonly string[],
): void {
  const retained = new Set(retainedIds);
  for (const id of context.pixelResources.keys()) {
    if (!retained.has(id)) context.pixelResources.delete(id);
  }
  for (const id of context.coverageResources.keys()) {
    if (!retained.has(id)) context.coverageResources.delete(id);
  }
}

export function serializedPixelSnapshot(
  resource: SerializedPixelResource,
): TiledPixelStoreSnapshot {
  if (resource.resource !== 'snapshot') throw new RangeError('Expected worker pixel snapshot');
  return resource.snapshot;
}

export function serializedCoverageSnapshot(
  resource: SerializedCoverageResource,
): TiledCoverageStoreSnapshot {
  if (resource.resource !== 'snapshot') throw new RangeError('Expected worker coverage snapshot');
  return resource.snapshot;
}

export type SerializedImageWorkerTask =
  | (Omit<Extract<ImageWorkerTask, { kind: 'filter' }>, 'layer' | 'selection'> & {
      readonly layer: SerializedPixelLayer;
      readonly selection: SerializedSelectionMask | null;
    })
  | (Omit<Extract<ImageWorkerTask, { kind: 'resize' }>, 'document'> & {
      readonly document: SerializedDocument;
    })
  | Extract<ImageWorkerTask, { kind: 'psd-import' | 'native-import' }>
  | (Omit<Extract<ImageWorkerTask, { kind: 'native-export' }>, 'document'> & {
      readonly document: SerializedDocument;
    })
  | (Omit<Extract<ImageWorkerTask, { kind: 'composite' }>, 'document'> & {
      readonly document: SerializedDocument;
    });

export type SerializedImageWorkerResult =
  | Exclude<ImageWorkerResult, { kind: 'resize' | 'psd-import' | 'native-import' }>
  | (Omit<Extract<ImageWorkerResult, { kind: 'resize' }>, 'document'> & {
      readonly document: SerializedDocument;
    })
  | (Omit<Extract<ImageWorkerResult, { kind: 'psd-import' }>, 'document'> & {
      readonly document: SerializedDocument;
    })
  | (Omit<Extract<ImageWorkerResult, { kind: 'native-import' }>, 'document'> & {
      readonly document: SerializedDocument;
    });

export type ImageWorkerRequest = {
  readonly id: string;
  readonly task: SerializedImageWorkerTask;
  readonly retainResourceIds?: readonly string[];
};

export type ImageWorkerEvent =
  | { readonly id: string; readonly type: 'progress'; readonly progress: number }
  | { readonly id: string; readonly type: 'filter-patch'; readonly patch: PixelPatch }
  | {
      readonly id: string;
      readonly type: 'result-resource';
      readonly resourceKind: 'pixel' | 'coverage';
      readonly resourceId: string;
      readonly width: number;
      readonly height: number;
      readonly tileSize: number;
      readonly defaultCoverage?: number;
      readonly totalTiles: number;
    }
  | {
      readonly id: string;
      readonly type: 'result-resource-tile';
      readonly resourceKind: 'pixel';
      readonly resourceId: string;
      readonly tileIndex: number;
      readonly column: number;
      readonly row: number;
      readonly pixels: Uint8ClampedArray;
    }
  | {
      readonly id: string;
      readonly type: 'result-resource-tile';
      readonly resourceKind: 'coverage';
      readonly resourceId: string;
      readonly tileIndex: number;
      readonly column: number;
      readonly row: number;
      readonly coverage: Uint8ClampedArray;
    }
  | { readonly id: string; readonly type: 'result'; readonly result: SerializedImageWorkerResult }
  | { readonly id: string; readonly type: 'error'; readonly message: string };

type ResultResourceEvent = Extract<
  ImageWorkerEvent,
  { readonly type: 'result-resource' | 'result-resource-tile' }
>;

type PendingResultResource =
  | {
      readonly kind: 'pixel';
      readonly totalTiles: number;
      receivedTiles: number;
      readonly tileKeys: Set<string>;
      store: TiledPixelStore;
    }
  | {
      readonly kind: 'coverage';
      readonly totalTiles: number;
      receivedTiles: number;
      readonly tileKeys: Set<string>;
      store: TiledCoverageStore;
    };

export interface WorkerResultStreamContext {
  readonly resources: Map<string, PendingResultResource>;
  readonly hydratedResources: WorkerTaskDeserializationContext;
}

export function createWorkerResultStreamContext(): WorkerResultStreamContext {
  return { resources: new Map(), hydratedResources: createWorkerTaskDeserializationContext() };
}

export function workerResultStreamAllocatedByteLength(context: WorkerResultStreamContext): number {
  let bytes = 0;
  for (const resource of context.resources.values()) bytes += resource.store.allocatedByteLength;
  return bytes;
}

function resourceScope(
  store: TiledPixelStore | TiledCoverageStore,
  region: PixelRegion | undefined,
): string {
  if (region === undefined) return 'full';
  const left = Math.max(0, region.x);
  const top = Math.max(0, region.y);
  const right = Math.min(store.width, region.x + region.width);
  const bottom = Math.min(store.height, region.y + region.height);
  if (right <= left || bottom <= top) return 'empty';
  return `${String(Math.floor(left / store.tileSize))},${String(Math.floor(top / store.tileSize))}:${String(Math.floor((right - 1) / store.tileSize))},${String(Math.floor((bottom - 1) / store.tileSize))}`;
}

function tileInRegion(tile: PixelRegion, region: PixelRegion | undefined): boolean {
  return (
    region === undefined ||
    (tile.x < region.x + region.width &&
      tile.x + tile.width > region.x &&
      tile.y < region.y + region.height &&
      tile.y + tile.height > region.y)
  );
}

function pixelDelta(
  store: TiledPixelStore,
  base: TiledPixelStore,
  region: PixelRegion | undefined,
) {
  const current = new Map(
    [...store.tiles()]
      .filter((tile) => tileInRegion(tile, region))
      .map((tile) => [tile.key, tile] as const),
  );
  const previous = new Map(
    [...base.tiles()]
      .filter((tile) => tileInRegion(tile, region))
      .map((tile) => [tile.key, tile] as const),
  );
  return {
    tiles: [...current.values()]
      .filter((tile) => previous.get(tile.key)?.pixels !== tile.pixels)
      .map((tile) => ({
        column: tile.column,
        row: tile.row,
        pixels: new Uint8ClampedArray(tile.pixels),
      })),
    removedTileKeys: [...previous.keys()].filter((key) => !current.has(key)),
  };
}

function coverageDelta(
  store: TiledCoverageStore,
  base: TiledCoverageStore,
  region: PixelRegion | undefined,
) {
  const current = new Map(
    [...store.tiles()]
      .filter((tile) => tileInRegion(tile, region))
      .map((tile) => [tile.key, tile] as const),
  );
  const previous = new Map(
    [...base.tiles()]
      .filter((tile) => tileInRegion(tile, region))
      .map((tile) => [tile.key, tile] as const),
  );
  return {
    tiles: [...current.values()]
      .filter((tile) => previous.get(tile.key)?.coverage !== tile.coverage)
      .map((tile) => ({
        column: tile.column,
        row: tile.row,
        coverage: new Uint8ClampedArray(tile.coverage),
      })),
    removedTileKeys: [...previous.keys()].filter((key) => !current.has(key)),
  };
}

function nextResourceId(context: WorkerTaskSerializationContext, prefix: string): string {
  const id = `${context.resourceIdPrefix}${prefix}-${String(context.nextResourceId)}`;
  context.nextResourceId += 1;
  return id;
}

function serializePixelResource(
  store: TiledPixelStore,
  context: WorkerTaskSerializationContext,
  slot: string,
  region?: PixelRegion,
): SerializedPixelResource {
  context.activeOwners.add(store);
  const scope = resourceScope(store, region);
  let scopes = context.pixelResources.get(store);
  const existing = scopes?.get(scope);
  if (existing !== undefined && context.availableResources.has(existing)) {
    context.resourceOwners.set(existing, store);
    return { resource: 'reference', schemaVersion: 1, id: existing };
  }
  const fullResource = region === undefined ? undefined : scopes?.get('full');
  if (fullResource !== undefined && context.availableResources.has(fullResource)) {
    context.resourceOwners.set(fullResource, store);
    return { resource: 'reference', schemaVersion: 1, id: fullResource };
  }
  const fullSlot = region === undefined ? undefined : context.pixelSlots.get(slot)?.get('full');
  if (
    fullSlot !== undefined &&
    fullSlot.store === store &&
    context.availableResources.has(fullSlot.id)
  ) {
    context.resourceOwners.set(fullSlot.id, store);
    return { resource: 'reference', schemaVersion: 1, id: fullSlot.id };
  }
  const prior = context.pixelSlots.get(slot)?.get(scope);
  if (
    prior !== undefined &&
    context.availableResources.has(prior.id) &&
    prior.store.width === store.width &&
    prior.store.height === store.height &&
    prior.store.tileSize === store.tileSize
  ) {
    const id = nextResourceId(context, 'pixel');
    const delta = pixelDelta(store, prior.store, region);
    if (scopes === undefined) {
      scopes = new Map();
      context.pixelResources.set(store, scopes);
    }
    scopes.set(scope, id);
    context.resourceOwners.set(id, store);
    let slotScopes = context.pixelSlots.get(slot);
    if (slotScopes === undefined) {
      slotScopes = new Map();
      context.pixelSlots.set(slot, slotScopes);
    }
    slotScopes.set(scope, { id, store });
    return {
      resource: 'delta',
      schemaVersion: 2,
      id,
      baseId: prior.id,
      ...delta,
    };
  }
  const snapshot = store.snapshot(region);
  if (existing !== undefined) {
    context.resourceOwners.set(existing, store);
    return { resource: 'snapshot', schemaVersion: 1, id: existing, snapshot };
  }
  const id = nextResourceId(context, 'pixel');
  if (scopes === undefined) {
    scopes = new Map();
    context.pixelResources.set(store, scopes);
  }
  scopes.set(scope, id);
  context.resourceOwners.set(id, store);
  let slotScopes = context.pixelSlots.get(slot);
  if (slotScopes === undefined) {
    slotScopes = new Map();
    context.pixelSlots.set(slot, slotScopes);
  }
  slotScopes.set(scope, { id, store });
  return { resource: 'snapshot', schemaVersion: 1, id, snapshot };
}

function serializeCoverageResource(
  store: TiledCoverageStore,
  context: WorkerTaskSerializationContext,
  slot: string,
  region?: PixelRegion,
): SerializedCoverageResource {
  context.activeOwners.add(store);
  const scope = resourceScope(store, region);
  let scopes = context.coverageResources.get(store);
  const existing = scopes?.get(scope);
  if (existing !== undefined && context.availableResources.has(existing)) {
    context.resourceOwners.set(existing, store);
    return { resource: 'reference', schemaVersion: 1, id: existing };
  }
  const fullResource = region === undefined ? undefined : scopes?.get('full');
  if (fullResource !== undefined && context.availableResources.has(fullResource)) {
    context.resourceOwners.set(fullResource, store);
    return { resource: 'reference', schemaVersion: 1, id: fullResource };
  }
  const fullSlot = region === undefined ? undefined : context.coverageSlots.get(slot)?.get('full');
  if (
    fullSlot !== undefined &&
    fullSlot.store === store &&
    context.availableResources.has(fullSlot.id)
  ) {
    context.resourceOwners.set(fullSlot.id, store);
    return { resource: 'reference', schemaVersion: 1, id: fullSlot.id };
  }
  const prior = context.coverageSlots.get(slot)?.get(scope);
  if (
    prior !== undefined &&
    context.availableResources.has(prior.id) &&
    prior.store.width === store.width &&
    prior.store.height === store.height &&
    prior.store.tileSize === store.tileSize &&
    prior.store.defaultCoverage === store.defaultCoverage
  ) {
    const id = nextResourceId(context, 'coverage');
    const delta = coverageDelta(store, prior.store, region);
    if (scopes === undefined) {
      scopes = new Map();
      context.coverageResources.set(store, scopes);
    }
    scopes.set(scope, id);
    context.resourceOwners.set(id, store);
    let slotScopes = context.coverageSlots.get(slot);
    if (slotScopes === undefined) {
      slotScopes = new Map();
      context.coverageSlots.set(slot, slotScopes);
    }
    slotScopes.set(scope, { id, store });
    return {
      resource: 'delta',
      schemaVersion: 2,
      id,
      baseId: prior.id,
      ...delta,
    };
  }
  const snapshot = store.snapshot(region);
  if (existing !== undefined) {
    context.resourceOwners.set(existing, store);
    return { resource: 'snapshot', schemaVersion: 1, id: existing, snapshot };
  }
  const id = nextResourceId(context, 'coverage');
  if (scopes === undefined) {
    scopes = new Map();
    context.coverageResources.set(store, scopes);
  }
  scopes.set(scope, id);
  context.resourceOwners.set(id, store);
  let slotScopes = context.coverageSlots.get(slot);
  if (slotScopes === undefined) {
    slotScopes = new Map();
    context.coverageSlots.set(slot, slotScopes);
  }
  slotScopes.set(scope, { id, store });
  return { resource: 'snapshot', schemaVersion: 1, id, snapshot };
}

function deserializePixelResource(
  resource: SerializedPixelResource,
  context: WorkerTaskDeserializationContext,
): TiledPixelStore {
  if (resource.resource === 'delta') {
    if ((resource as { readonly schemaVersion?: unknown }).schemaVersion !== 2) {
      throw new RangeError('Unsupported worker pixel delta');
    }
    const base = context.pixelResources.get(resource.baseId);
    if (base === undefined)
      throw new RangeError(`Unknown worker pixel resource ${resource.baseId}`);
    const store = base.applyTileDelta(resource);
    context.pixelResources.set(resource.id, store);
    return store;
  }
  if ((resource as { readonly schemaVersion?: unknown }).schemaVersion !== 1) {
    throw new RangeError('Unsupported worker pixel resource');
  }
  if (resource.resource === 'reference') {
    const store = context.pixelResources.get(resource.id);
    if (store === undefined) throw new RangeError(`Unknown worker pixel resource ${resource.id}`);
    return store;
  }
  const store = TiledPixelStore.fromSnapshot(resource.snapshot);
  context.pixelResources.set(resource.id, store);
  return store;
}

function deserializeCoverageResource(
  resource: SerializedCoverageResource,
  context: WorkerTaskDeserializationContext,
): TiledCoverageStore {
  if (resource.resource === 'delta') {
    if ((resource as { readonly schemaVersion?: unknown }).schemaVersion !== 2) {
      throw new RangeError('Unsupported worker coverage delta');
    }
    const base = context.coverageResources.get(resource.baseId);
    if (base === undefined) {
      throw new RangeError(`Unknown worker coverage resource ${resource.baseId}`);
    }
    const store = base.applyTileDelta(resource);
    context.coverageResources.set(resource.id, store);
    return store;
  }
  if ((resource as { readonly schemaVersion?: unknown }).schemaVersion !== 1) {
    throw new RangeError('Unsupported worker coverage resource');
  }
  if (resource.resource === 'reference') {
    const store = context.coverageResources.get(resource.id);
    if (store === undefined)
      throw new RangeError(`Unknown worker coverage resource ${resource.id}`);
    return store;
  }
  const store = TiledCoverageStore.fromSnapshot(resource.snapshot);
  context.coverageResources.set(resource.id, store);
  return store;
}

function serializePixelLayer(
  layer: PixelLayer,
  context: WorkerTaskSerializationContext,
  path: string,
): SerializedPixelLayer {
  return {
    ...layer,
    pixelStore: serializePixelResource(layer.pixelStore, context, `${path}/pixels`),
    rasterMask: serializeRasterMask(layer.rasterMask, context, `${path}/mask`),
  };
}

function selectedFilterLayerRegion(
  layer: PixelLayer,
  selection: SelectionMask,
  worldTransform: LayerTransform,
  draft: FilterDraft,
): { x: number; y: number; width: number; height: number } | null {
  if (selection.coverageStore.defaultCoverage !== 0) {
    return { x: 0, y: 0, width: layer.width, height: layer.height };
  }
  let left = selection.width;
  let top = selection.height;
  let right = -1;
  let bottom = -1;
  for (const tile of selection.coverageStore.tiles()) {
    left = Math.min(left, tile.x);
    top = Math.min(top, tile.y);
    right = Math.max(right, tile.x + tile.width);
    bottom = Math.max(bottom, tile.y + tile.height);
  }
  if (right <= left || bottom <= top) return null;
  const inverse = invertLayerTransform(worldTransform);
  const corners = [
    transformPoint(inverse, left, top),
    transformPoint(inverse, right, top),
    transformPoint(inverse, left, bottom),
    transformPoint(inverse, right, bottom),
  ];
  const halo = draft.kind === 'gaussian-blur' ? draft.blurRadius * 2 : 1;
  const minX = Math.max(0, Math.floor(Math.min(...corners.map((point) => point.x))) - halo);
  const minY = Math.max(0, Math.floor(Math.min(...corners.map((point) => point.y))) - halo);
  const maxX = Math.min(
    layer.width,
    Math.ceil(Math.max(...corners.map((point) => point.x))) + halo,
  );
  const maxY = Math.min(
    layer.height,
    Math.ceil(Math.max(...corners.map((point) => point.y))) + halo,
  );
  if (maxX <= minX || maxY <= minY) return null;
  return { x: minX, y: minY, width: maxX - minX, height: maxY - minY };
}

function serializeFilterPixelLayer(
  layer: PixelLayer,
  selection: SelectionMask | null,
  worldTransform: LayerTransform,
  draft: FilterDraft,
  context: WorkerTaskSerializationContext,
  path: string,
): SerializedPixelLayer {
  const region =
    selection === null
      ? undefined
      : (selectedFilterLayerRegion(layer, selection, worldTransform, draft) ?? {
          x: 0,
          y: 0,
          width: 0,
          height: 0,
        });
  return {
    ...layer,
    pixelStore: serializePixelResource(layer.pixelStore, context, `${path}/pixels`, region),
    // Filter patch computation does not read the layer mask. Avoid transferring it.
    rasterMask: null,
  };
}

function serializeRasterMask(
  mask: RasterMask | null,
  context: WorkerTaskSerializationContext,
  slot: string,
  region?: PixelRegion,
): SerializedRasterMask | null {
  return mask === null
    ? null
    : {
        ...mask,
        coverageStore: serializeCoverageResource(mask.coverageStore, context, slot, region),
      };
}

function deserializeRasterMask(
  mask: SerializedRasterMask | null,
  context: WorkerTaskDeserializationContext,
): RasterMask | null {
  return mask === null
    ? null
    : { ...mask, coverageStore: deserializeCoverageResource(mask.coverageStore, context) };
}

function serializeSelection(
  selection: SelectionMask | null,
  context: WorkerTaskSerializationContext,
  slot: string,
): SerializedSelectionMask | null {
  if (selection === null) return null;
  return {
    width: selection.width,
    height: selection.height,
    coverageStore: serializeCoverageResource(selection.coverageStore, context, slot),
  };
}

function deserializeSelection(
  selection: SerializedSelectionMask | null,
  context: WorkerTaskDeserializationContext,
): SelectionMask | null {
  return selection === null
    ? null
    : {
        ...selection,
        coverageStore: deserializeCoverageResource(selection.coverageStore, context),
      };
}

function serializeAlphaChannel(
  channel: AlphaChannel,
  context: WorkerTaskSerializationContext,
  slot: string,
): SerializedAlphaChannel {
  return {
    id: channel.id,
    name: channel.name,
    width: channel.width,
    height: channel.height,
    coverageStore: serializeCoverageResource(channel.coverageStore, context, slot),
  };
}

function deserializeAlphaChannel(
  channel: SerializedAlphaChannel,
  context: WorkerTaskDeserializationContext,
): AlphaChannel {
  return { ...channel, coverageStore: deserializeCoverageResource(channel.coverageStore, context) };
}

function serializeLayer(
  layer: Layer,
  context: WorkerTaskSerializationContext,
  path: string,
  documentLayerSlots = false,
): SerializedLayer {
  if (layer.kind === 'pixel') return serializePixelLayer(layer, context, path);
  if (layer.kind === 'group') {
    return {
      ...layer,
      rasterMask: serializeRasterMask(layer.rasterMask, context, `${path}/mask`),
      children: layer.children.map((child) =>
        serializeLayer(
          child,
          context,
          documentLayerSlots ? `document/layers/${child.id}` : `${path}/children/${child.id}`,
          documentLayerSlots,
        ),
      ),
    };
  }
  if (layer.kind === 'smart-object') {
    return {
      ...layer,
      rasterMask: serializeRasterMask(layer.rasterMask, context, `${path}/mask`),
      source: {
        ...layer.source,
        layers: layer.source.layers.map((child) =>
          serializeLayer(child, context, `${path}/source/${child.id}`),
        ),
      },
    };
  }
  return {
    ...layer,
    rasterMask: serializeRasterMask(layer.rasterMask, context, `${path}/mask`),
  };
}

function deserializeLayer(
  layer: SerializedLayer,
  context: WorkerTaskDeserializationContext,
): Layer {
  if (layer.kind === 'pixel') {
    return {
      ...layer,
      pixelStore: deserializePixelResource(layer.pixelStore, context),
      rasterMask: deserializeRasterMask(layer.rasterMask, context),
    };
  }
  if (layer.kind === 'group') {
    return {
      ...layer,
      rasterMask: deserializeRasterMask(layer.rasterMask, context),
      children: layer.children.map((child) => deserializeLayer(child, context)),
    };
  }
  if (layer.kind === 'smart-object') {
    return {
      ...layer,
      rasterMask: deserializeRasterMask(layer.rasterMask, context),
      source: {
        ...layer.source,
        layers: layer.source.layers.map((child) => deserializeLayer(child, context)),
      },
    };
  }
  return { ...layer, rasterMask: deserializeRasterMask(layer.rasterMask, context) };
}

function serializeDocument(
  document: ImageDocument,
  context: WorkerTaskSerializationContext,
): SerializedDocument {
  return {
    ...document,
    layers: document.layers.map((layer) =>
      serializeLayer(layer, context, `document/layers/${layer.id}`, true),
    ),
    selection: serializeSelection(document.selection, context, 'document/selection'),
    channels: document.channels.map((channel) =>
      serializeAlphaChannel(channel, context, `document/channels/${channel.id}`),
    ),
  };
}

function supportsCompositeViewportSnapshot(layers: readonly Layer[]): boolean {
  for (const layer of layers) {
    if (layer.kind === 'group') {
      if (!supportsCompositeViewportSnapshot(layer.children)) return false;
      continue;
    }
    if (layer.kind === 'smart-object') {
      if (!supportsCompositeViewportSnapshot(layer.source.layers)) return false;
      continue;
    }
    if (layer.kind !== 'pixel' && layer.kind !== 'shape' && layer.kind !== 'text') {
      return false;
    }
  }
  return true;
}

function compositeEffectViewport(
  viewport: PixelRegion,
  effects: Layer['effects'],
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
  if (halo <= 0) return viewport;
  const x = Math.max(0, viewport.x - halo);
  const y = Math.max(0, viewport.y - halo);
  const right = Math.min(canvas.width, viewport.x + viewport.width + halo);
  const bottom = Math.min(canvas.height, viewport.y + viewport.height + halo);
  return { x, y, width: right - x, height: bottom - y };
}

function compositeLayerSourceRegion(viewport: PixelRegion, transform: LayerTransform): PixelRegion {
  if (
    transform.a === 1 &&
    transform.b === 0 &&
    transform.c === 0 &&
    transform.d === 1 &&
    Number.isInteger(transform.e) &&
    Number.isInteger(transform.f)
  ) {
    return {
      x: viewport.x - transform.e,
      y: viewport.y - transform.f,
      width: viewport.width,
      height: viewport.height,
    };
  }
  const inverse = invertLayerTransform(transform);
  const corners = [
    transformPoint(inverse, viewport.x, viewport.y),
    transformPoint(inverse, viewport.x + viewport.width, viewport.y),
    transformPoint(inverse, viewport.x, viewport.y + viewport.height),
    transformPoint(inverse, viewport.x + viewport.width, viewport.y + viewport.height),
  ];
  const left = Math.floor(Math.min(...corners.map((point) => point.x))) - 1;
  const top = Math.floor(Math.min(...corners.map((point) => point.y))) - 1;
  const right = Math.ceil(Math.max(...corners.map((point) => point.x))) + 1;
  const bottom = Math.ceil(Math.max(...corners.map((point) => point.y))) + 1;
  return { x: left, y: top, width: right - left, height: bottom - top };
}

function smartFilterSourceRegion(
  region: PixelRegion,
  filters: SmartObjectLayer['smartFilters'],
  canvas: { readonly width: number; readonly height: number },
): PixelRegion {
  const halo = filters.reduce(
    (total, filter) =>
      filter.enabled && filter.opacity > 0 && filter.type === 'gaussian-blur'
        ? total + Math.max(0, Math.round(filter.radius))
        : total,
    0,
  );
  const x = Math.max(0, region.x - halo);
  const y = Math.max(0, region.y - halo);
  const right = Math.min(canvas.width, region.x + region.width + halo);
  const bottom = Math.min(canvas.height, region.y + region.height + halo);
  return { x, y, width: Math.max(0, right - x), height: Math.max(0, bottom - y) };
}

function serializeCompositeViewportLayer(
  layer: Layer,
  viewport: PixelRegion,
  parentTransform: LayerTransform,
  canvas: { readonly width: number; readonly height: number },
  context: WorkerTaskSerializationContext,
  path: string,
  documentLayerSlots = false,
): SerializedLayer {
  const worldTransform = multiplyLayerTransforms(parentTransform, layer.transform);
  if (layer.kind === 'pixel') {
    const effectViewport = compositeEffectViewport(viewport, layer.effects, canvas);
    const sourceRegion = compositeLayerSourceRegion(effectViewport, worldTransform);
    return {
      ...layer,
      pixelStore: serializePixelResource(layer.pixelStore, context, `${path}/pixels`, sourceRegion),
      rasterMask: serializeRasterMask(layer.rasterMask, context, `${path}/mask`, sourceRegion),
    };
  }
  if (layer.kind === 'shape' || layer.kind === 'text') {
    const effectViewport = compositeEffectViewport(viewport, layer.effects, canvas);
    const sourceRegion = compositeLayerSourceRegion(effectViewport, worldTransform);
    return {
      ...layer,
      rasterMask: serializeRasterMask(layer.rasterMask, context, `${path}/mask`, sourceRegion),
    };
  }
  if (layer.kind === 'smart-object') {
    const effectViewport = compositeEffectViewport(viewport, layer.effects, canvas);
    const mappedRegion = compositeLayerSourceRegion(effectViewport, worldTransform);
    const sourceRegion = smartFilterSourceRegion(
      mappedRegion,
      layer.smartFilters,
      layer.source.canvas,
    );
    return {
      ...layer,
      rasterMask: serializeRasterMask(layer.rasterMask, context, `${path}/mask`, sourceRegion),
      source: {
        ...layer.source,
        layers: layer.source.layers.map((child) =>
          serializeCompositeViewportLayer(
            child,
            sourceRegion,
            identityLayerTransform(),
            layer.source.canvas,
            context,
            `${path}/source/${child.id}`,
          ),
        ),
      },
    };
  }
  if (layer.kind === 'group') {
    return {
      ...layer,
      rasterMask: serializeRasterMask(layer.rasterMask, context, `${path}/mask`, viewport),
      children: layer.children.map((child) =>
        serializeCompositeViewportLayer(
          child,
          viewport,
          worldTransform,
          canvas,
          context,
          documentLayerSlots ? `document/layers/${child.id}` : `${path}/children/${child.id}`,
          documentLayerSlots,
        ),
      ),
    };
  }
  return serializeLayer(layer, context, path);
}

function serializeCompositeDocument(
  document: ImageDocument,
  viewport: PixelRegion | undefined,
  progressiveViewportOnly: boolean,
  context: WorkerTaskSerializationContext,
): SerializedDocument {
  const pruneToViewport =
    progressiveViewportOnly &&
    viewport !== undefined &&
    supportsCompositeViewportSnapshot(document.layers);
  return {
    ...document,
    layers: pruneToViewport
      ? document.layers.map((layer) =>
          serializeCompositeViewportLayer(
            layer,
            viewport,
            identityLayerTransform(),
            document.canvas,
            context,
            `document/layers/${layer.id}`,
            true,
          ),
        )
      : document.layers.map((layer) =>
          serializeLayer(layer, context, `document/layers/${layer.id}`, true),
        ),
    // Composite calculation never reads editor selection or saved alpha channels.
    selection: null,
    channels: [],
  };
}

function deserializeDocument(
  document: SerializedDocument,
  context: WorkerTaskDeserializationContext,
): ImageDocument {
  return {
    ...document,
    layers: document.layers.map((layer) => deserializeLayer(layer, context)),
    selection: deserializeSelection(document.selection, context),
    channels: document.channels.map((channel) => deserializeAlphaChannel(channel, context)),
  };
}

export function serializeWorkerTask(
  task: ImageWorkerTask,
  context = createWorkerTaskSerializationContext(),
): SerializedImageWorkerTask {
  // Filter tasks expose only one layer plus an optional selection. Preserve the
  // rest of the current document namespace so the next full-document task can
  // reference unchanged resources instead of uploading them again. A whole-
  // document task remains authoritative and prunes resources no longer present.
  if (task.kind === 'filter') {
    for (const owner of context.resourceOwners.values()) context.activeOwners.add(owner);
  } else {
    context.activeOwners.clear();
  }
  if (task.kind === 'filter') {
    return {
      ...task,
      layer: serializeFilterPixelLayer(
        task.layer,
        task.selection,
        task.worldTransform,
        task.draft,
        context,
        `document/layers/${task.layer.id}`,
      ),
      selection: serializeSelection(task.selection, context, 'document/selection'),
    };
  }
  if (task.kind === 'composite') {
    return {
      ...task,
      document: serializeCompositeDocument(
        task.document,
        task.viewportRegion,
        task.progressiveViewportOnly === true,
        context,
      ),
    };
  }
  if (task.kind === 'resize' || task.kind === 'native-export') {
    return { ...task, document: serializeDocument(task.document, context) };
  }
  return task;
}

export function deserializeWorkerTask(
  task: SerializedImageWorkerTask,
  context = createWorkerTaskDeserializationContext(),
): ImageWorkerTask {
  if (task.kind === 'filter') {
    return {
      ...task,
      layer: deserializeLayer(task.layer, context) as PixelLayer,
      selection: deserializeSelection(task.selection, context),
    };
  }
  if (task.kind === 'resize' || task.kind === 'native-export' || task.kind === 'composite') {
    return { ...task, document: deserializeDocument(task.document, context) };
  }
  return task;
}

export function serializeWorkerResult(
  result: ImageWorkerResult,
  context = createWorkerTaskSerializationContext(),
): SerializedImageWorkerResult {
  if (result.kind === 'resize' || result.kind === 'psd-import' || result.kind === 'native-import') {
    return {
      ...result,
      document: serializeDocument(result.document, context),
    };
  }
  return result;
}

export function deserializeWorkerResult(
  result: SerializedImageWorkerResult,
  context = createWorkerTaskDeserializationContext(),
): ImageWorkerResult {
  if (result.kind === 'resize' || result.kind === 'psd-import' || result.kind === 'native-import') {
    return {
      ...result,
      document: deserializeDocument(result.document, context),
    };
  }
  return result;
}

function visitResultDocumentResources(
  result: SerializedImageWorkerResult,
  visitPixel: (resource: SerializedPixelResource) => SerializedPixelResource,
  visitCoverage: (resource: SerializedCoverageResource) => SerializedCoverageResource,
): void {
  if (result.kind !== 'resize' && result.kind !== 'psd-import' && result.kind !== 'native-import') {
    return;
  }
  const visitLayer = (layer: SerializedLayer) => {
    if (layer.rasterMask !== null) {
      (layer.rasterMask as { coverageStore: SerializedCoverageResource }).coverageStore =
        visitCoverage(layer.rasterMask.coverageStore);
    }
    if (layer.kind === 'pixel') {
      (layer as { pixelStore: SerializedPixelResource }).pixelStore = visitPixel(layer.pixelStore);
    } else if (layer.kind === 'group') {
      layer.children.forEach(visitLayer);
    } else if (layer.kind === 'smart-object') {
      layer.source.layers.forEach(visitLayer);
    }
  };
  result.document.layers.forEach(visitLayer);
  if (result.document.selection !== null) {
    (result.document.selection as { coverageStore: SerializedCoverageResource }).coverageStore =
      visitCoverage(result.document.selection.coverageStore);
  }
  result.document.channels.forEach((channel) => {
    (channel as { coverageStore: SerializedCoverageResource }).coverageStore = visitCoverage(
      channel.coverageStore,
    );
  });
}

/**
 * Moves each owned document-result tile into an ordered event and rewrites the
 * terminal result to resource references. The caller must retain/hydrate the
 * original snapshots before this function transfers their buffers.
 */
export function streamWorkerResultTiles(
  requestId: string,
  result: SerializedImageWorkerResult,
  emit: (event: ResultResourceEvent, transfer?: Transferable[]) => void,
  onProgress?: (completedTiles: number, totalTiles: number) => void,
): SerializedImageWorkerResult {
  const resources = new Map<
    string,
    | {
        readonly kind: 'pixel';
        readonly resource: Extract<SerializedPixelResource, { resource: 'snapshot' }>;
      }
    | {
        readonly kind: 'coverage';
        readonly resource: Extract<SerializedCoverageResource, { resource: 'snapshot' }>;
      }
  >();
  const pixelReference = (resource: SerializedPixelResource): SerializedPixelResource => {
    if (resource.resource !== 'snapshot') {
      throw new RangeError('Streamed worker pixel results must be owned snapshots');
    }
    const existing = resources.get(resource.id);
    if (existing !== undefined) {
      if (existing.kind !== 'pixel') {
        throw new RangeError(`Duplicate streamed worker resource ${resource.id}`);
      }
      return { resource: 'reference', schemaVersion: 1, id: resource.id };
    }
    resources.set(resource.id, { kind: 'pixel', resource });
    return { resource: 'reference', schemaVersion: 1, id: resource.id };
  };
  const coverageReference = (resource: SerializedCoverageResource): SerializedCoverageResource => {
    if (resource.resource !== 'snapshot') {
      throw new RangeError('Streamed worker coverage results must be owned snapshots');
    }
    const existing = resources.get(resource.id);
    if (existing !== undefined) {
      if (existing.kind !== 'coverage') {
        throw new RangeError(`Duplicate streamed worker resource ${resource.id}`);
      }
      return { resource: 'reference', schemaVersion: 1, id: resource.id };
    }
    resources.set(resource.id, { kind: 'coverage', resource });
    return { resource: 'reference', schemaVersion: 1, id: resource.id };
  };
  visitResultDocumentResources(result, pixelReference, coverageReference);
  const totalTiles = [...resources.values()].reduce(
    (total, entry) => total + entry.resource.snapshot.tiles.length,
    0,
  );
  let completedTiles = 0;
  for (const [resourceId, entry] of resources) {
    if (entry.kind === 'pixel') {
      const snapshot = entry.resource.snapshot;
      emit({
        id: requestId,
        type: 'result-resource',
        resourceKind: 'pixel',
        resourceId,
        width: snapshot.width,
        height: snapshot.height,
        tileSize: snapshot.tileSize,
        totalTiles: snapshot.tiles.length,
      });
      snapshot.tiles.forEach((tile, tileIndex) => {
        const pixels = tile.pixels;
        emit(
          {
            id: requestId,
            type: 'result-resource-tile',
            resourceKind: 'pixel',
            resourceId,
            tileIndex,
            column: tile.column,
            row: tile.row,
            pixels,
          },
          [pixels.buffer as ArrayBuffer],
        );
        completedTiles += 1;
        onProgress?.(completedTiles, totalTiles);
      });
    } else {
      const snapshot = entry.resource.snapshot;
      emit({
        id: requestId,
        type: 'result-resource',
        resourceKind: 'coverage',
        resourceId,
        width: snapshot.width,
        height: snapshot.height,
        tileSize: snapshot.tileSize,
        defaultCoverage: snapshot.defaultCoverage,
        totalTiles: snapshot.tiles.length,
      });
      snapshot.tiles.forEach((tile, tileIndex) => {
        const coverage = tile.coverage;
        emit(
          {
            id: requestId,
            type: 'result-resource-tile',
            resourceKind: 'coverage',
            resourceId,
            tileIndex,
            column: tile.column,
            row: tile.row,
            coverage,
          },
          [coverage.buffer as ArrayBuffer],
        );
        completedTiles += 1;
        onProgress?.(completedTiles, totalTiles);
      });
    }
  }
  return result;
}

export function acceptWorkerResultStreamEvent(
  context: WorkerResultStreamContext,
  event: ResultResourceEvent,
): void {
  if (event.type === 'result-resource') {
    if (context.resources.has(event.resourceId)) {
      throw new RangeError(`Duplicate streamed worker resource ${event.resourceId}`);
    }
    if (
      !Number.isSafeInteger(event.totalTiles) ||
      event.totalTiles < 0 ||
      (event.resourceKind === 'coverage' &&
        (!Number.isSafeInteger(event.defaultCoverage) ||
          (event.defaultCoverage ?? -1) < 0 ||
          (event.defaultCoverage ?? 256) > 255))
    ) {
      throw new RangeError('Streamed worker resource manifest is invalid');
    }
    if (event.resourceKind === 'pixel') {
      context.resources.set(event.resourceId, {
        kind: 'pixel',
        totalTiles: event.totalTiles,
        receivedTiles: 0,
        tileKeys: new Set(),
        store: TiledPixelStore.empty(event.width, event.height, event.tileSize),
      });
    } else {
      context.resources.set(event.resourceId, {
        kind: 'coverage',
        totalTiles: event.totalTiles,
        receivedTiles: 0,
        tileKeys: new Set(),
        store: TiledCoverageStore.uniform(
          event.width,
          event.height,
          event.defaultCoverage ?? 0,
          event.tileSize,
        ),
      });
    }
    const resource = context.resources.get(event.resourceId);
    if (resource === undefined) throw new RangeError('Streamed worker resource manifest was lost');
    const maximumTileCount =
      Math.ceil(resource.store.width / resource.store.tileSize) *
      Math.ceil(resource.store.height / resource.store.tileSize);
    if (resource.totalTiles > maximumTileCount) {
      context.resources.delete(event.resourceId);
      throw new RangeError('Streamed worker resource tile count exceeds its store capacity');
    }
    if (resource.totalTiles === 0) {
      if (resource.kind === 'pixel') {
        context.hydratedResources.pixelResources.set(event.resourceId, resource.store);
      } else {
        context.hydratedResources.coverageResources.set(event.resourceId, resource.store);
      }
    }
    return;
  }
  const resource = context.resources.get(event.resourceId);
  if (resource === undefined) {
    throw new RangeError(`Unknown streamed worker resource ${event.resourceId}`);
  }
  if (resource.kind !== event.resourceKind || event.tileIndex !== resource.receivedTiles) {
    throw new RangeError('Streamed worker resource tile is out of order');
  }
  if (resource.receivedTiles >= resource.totalTiles) {
    throw new RangeError('Streamed worker resource has too many tiles');
  }
  if (!Number.isSafeInteger(event.column) || !Number.isSafeInteger(event.row)) {
    throw new RangeError('Streamed worker resource tile coordinates are invalid');
  }
  const key = `${String(event.column)},${String(event.row)}`;
  if (resource.tileKeys.has(key)) {
    throw new RangeError('Streamed worker resource tile is duplicated');
  }
  resource.tileKeys.add(key);
  const tileRegion = (store: TiledPixelStore | TiledCoverageStore) => {
    const columnCount = Math.ceil(store.width / store.tileSize);
    const rowCount = Math.ceil(store.height / store.tileSize);
    if (event.column < 0 || event.row < 0 || event.column >= columnCount || event.row >= rowCount) {
      throw new RangeError('Streamed worker resource tile is outside the store');
    }
    return {
      x: event.column * store.tileSize,
      y: event.row * store.tileSize,
      width: Math.min(store.tileSize, store.width - event.column * store.tileSize),
      height: Math.min(store.tileSize, store.height - event.row * store.tileSize),
    };
  };
  if (resource.kind === 'pixel' && event.resourceKind === 'pixel') {
    const region = tileRegion(resource.store);
    const written = resource.store.write({ ...region, pixels: event.pixels });
    if (written.changedTileKeys.length !== 1) {
      throw new RangeError('Streamed worker pixel tile must contain allocated pixels');
    }
    resource.store = written.store;
  } else if (resource.kind === 'coverage' && event.resourceKind === 'coverage') {
    const region = tileRegion(resource.store);
    const written = resource.store.write({ ...region, coverage: event.coverage });
    if (written.changedTileKeys.length !== 1) {
      throw new RangeError('Streamed worker coverage tile must differ from its default');
    }
    resource.store = written.store;
  } else {
    throw new RangeError('Streamed worker resource tile kind does not match its manifest');
  }
  resource.receivedTiles += 1;
  if (resource.receivedTiles === resource.totalTiles) {
    if (resource.kind === 'pixel') {
      context.hydratedResources.pixelResources.set(event.resourceId, resource.store);
    } else {
      context.hydratedResources.coverageResources.set(event.resourceId, resource.store);
    }
  }
}

export function deserializeStreamedWorkerResult(
  result: SerializedImageWorkerResult,
  stream: WorkerResultStreamContext,
): ImageWorkerResult {
  const expected = new Map<string, 'pixel' | 'coverage'>();
  visitResultDocumentResources(
    result,
    (resource) => {
      if (resource.resource !== 'reference')
        throw new RangeError('Streamed terminal pixel resource must be a reference');
      expected.set(resource.id, 'pixel');
      return resource;
    },
    (resource) => {
      if (resource.resource !== 'reference')
        throw new RangeError('Streamed terminal coverage resource must be a reference');
      expected.set(resource.id, 'coverage');
      return resource;
    },
  );
  if (expected.size !== stream.resources.size) {
    throw new RangeError('Streamed worker resource manifest does not match terminal result');
  }
  for (const [id, kind] of expected) {
    const resource = stream.resources.get(id);
    if (
      resource === undefined ||
      resource.kind !== kind ||
      resource.receivedTiles !== resource.totalTiles
    ) {
      throw new RangeError(`Streamed worker resource ${id} is incomplete`);
    }
    if (
      (kind === 'pixel' && !stream.hydratedResources.pixelResources.has(id)) ||
      (kind === 'coverage' && !stream.hydratedResources.coverageResources.has(id))
    ) {
      throw new RangeError(`Streamed worker resource ${id} was not hydrated`);
    }
  }
  return deserializeWorkerResult(result, stream.hydratedResources);
}

function adoptPixelResource(
  resource: SerializedPixelResource,
  store: TiledPixelStore,
  slot: string,
  context: WorkerTaskSerializationContext,
): void {
  if (resource.resource !== 'snapshot' && resource.resource !== 'reference') {
    throw new RangeError('Worker result pixel resources must be snapshots or references');
  }
  context.pixelResources.set(store, new Map([['full', resource.id]]));
  context.availableResources.add(resource.id);
  context.resourceOwners.set(resource.id, store);
  context.pixelSlots.set(slot, new Map([['full', { id: resource.id, store }]]));
}

function adoptCoverageResource(
  resource: SerializedCoverageResource,
  store: TiledCoverageStore,
  slot: string,
  context: WorkerTaskSerializationContext,
): void {
  if (resource.resource !== 'snapshot' && resource.resource !== 'reference') {
    throw new RangeError('Worker result coverage resources must be snapshots or references');
  }
  context.coverageResources.set(store, new Map([['full', resource.id]]));
  context.availableResources.add(resource.id);
  context.resourceOwners.set(resource.id, store);
  context.coverageSlots.set(slot, new Map([['full', { id: resource.id, store }]]));
}

function adoptResultLayer(
  serialized: SerializedLayer,
  hydrated: Layer,
  path: string,
  context: WorkerTaskSerializationContext,
): void {
  if (serialized.id !== hydrated.id || serialized.kind !== hydrated.kind) {
    throw new RangeError('Worker result layer structure does not match its hydrated document');
  }
  if (serialized.rasterMask !== null) {
    if (hydrated.rasterMask === null) throw new RangeError('Worker result raster mask is missing');
    adoptCoverageResource(
      serialized.rasterMask.coverageStore,
      hydrated.rasterMask.coverageStore,
      `${path}/mask`,
      context,
    );
  } else if (hydrated.rasterMask !== null) {
    throw new RangeError('Worker result raster mask structure does not match');
  }
  if (serialized.kind === 'pixel' && hydrated.kind === 'pixel') {
    adoptPixelResource(serialized.pixelStore, hydrated.pixelStore, `${path}/pixels`, context);
    return;
  }
  if (serialized.kind === 'group' && hydrated.kind === 'group') {
    if (serialized.children.length !== hydrated.children.length) {
      throw new RangeError('Worker result group structure does not match');
    }
    serialized.children.forEach((child, index) => {
      const hydratedChild = hydrated.children[index];
      if (hydratedChild === undefined) throw new RangeError('Worker result child is missing');
      adoptResultLayer(child, hydratedChild, `${path}/children/${child.id}`, context);
    });
    return;
  }
  if (serialized.kind === 'smart-object' && hydrated.kind === 'smart-object') {
    if (serialized.source.layers.length !== hydrated.source.layers.length) {
      throw new RangeError('Worker result smart-object structure does not match');
    }
    serialized.source.layers.forEach((child, index) => {
      const hydratedChild = hydrated.source.layers[index];
      if (hydratedChild === undefined)
        throw new RangeError('Worker result source child is missing');
      adoptResultLayer(child, hydratedChild, `${path}/source/${child.id}`, context);
    });
  }
}

/** Registers a transferred document result as a future persistent task input. */
export function adoptWorkerResultResources(
  serialized: SerializedImageWorkerResult,
  hydrated: ImageWorkerResult,
  context: WorkerTaskSerializationContext,
): void {
  if (
    serialized.kind !== 'resize' &&
    serialized.kind !== 'psd-import' &&
    serialized.kind !== 'native-import'
  ) {
    return;
  }
  if (hydrated.kind !== serialized.kind) {
    throw new RangeError('Worker result kind does not match its hydrated result');
  }
  if (serialized.document.layers.length !== hydrated.document.layers.length) {
    throw new RangeError('Worker result document structure does not match');
  }
  serialized.document.layers.forEach((layer, index) => {
    const hydratedLayer = hydrated.document.layers[index];
    if (hydratedLayer === undefined) throw new RangeError('Worker result layer is missing');
    adoptResultLayer(layer, hydratedLayer, `document/layers/${layer.id}`, context);
  });
  if (serialized.document.selection !== null) {
    if (hydrated.document.selection === null)
      throw new RangeError('Worker result selection is missing');
    adoptCoverageResource(
      serialized.document.selection.coverageStore,
      hydrated.document.selection.coverageStore,
      'document/selection',
      context,
    );
  } else if (hydrated.document.selection !== null) {
    throw new RangeError('Worker result selection structure does not match');
  }
  if (serialized.document.channels.length !== hydrated.document.channels.length) {
    throw new RangeError('Worker result channel structure does not match');
  }
  serialized.document.channels.forEach((channel, index) => {
    const hydratedChannel = hydrated.document.channels[index];
    if (hydratedChannel === undefined || hydratedChannel.id !== channel.id) {
      throw new RangeError('Worker result channel structure does not match');
    }
    adoptCoverageResource(
      channel.coverageStore,
      hydratedChannel.coverageStore,
      `document/channels/${channel.id}`,
      context,
    );
  });
  const adoptedIds = new Set<string>();
  visitResultDocumentResources(
    serialized,
    (resource) => {
      adoptedIds.add(resource.id);
      return resource;
    },
    (resource) => {
      adoptedIds.add(resource.id);
      return resource;
    },
  );
  pruneWorkerTaskSerializationContext(context, adoptedIds);
}

function documentTransferables(document: SerializedDocument): ArrayBuffer[] {
  const buffers: ArrayBuffer[] = [];
  const seen = new Set<ArrayBuffer>();
  const add = (buffer: ArrayBufferLike) => {
    const transferable = buffer as ArrayBuffer;
    if (seen.has(transferable)) return;
    seen.add(transferable);
    buffers.push(transferable);
  };
  const visitCoverage = (resource: SerializedCoverageResource) => {
    if (resource.resource === 'reference') return;
    const tiles = resource.resource === 'snapshot' ? resource.snapshot.tiles : resource.tiles;
    for (const tile of tiles) add(tile.coverage.buffer);
  };
  const visit = (layer: SerializedLayer) => {
    if (layer.rasterMask !== null) visitCoverage(layer.rasterMask.coverageStore);
    if (layer.kind === 'pixel') {
      if (layer.pixelStore.resource !== 'reference') {
        const tiles =
          layer.pixelStore.resource === 'snapshot'
            ? layer.pixelStore.snapshot.tiles
            : layer.pixelStore.tiles;
        for (const tile of tiles) add(tile.pixels.buffer);
      }
    } else if (layer.kind === 'group') layer.children.forEach(visit);
    else if (layer.kind === 'smart-object') layer.source.layers.forEach(visit);
  };
  document.layers.forEach(visit);
  if (document.selection !== null) visitCoverage(document.selection.coverageStore);
  document.channels.forEach((channel) => visitCoverage(channel.coverageStore));
  return buffers;
}

export function workerResultTransferables(result: SerializedImageWorkerResult): Transferable[] {
  if (result.kind === 'filter') {
    return [
      ...new Set(
        result.patches.map((patch) => (patch.pixels as Uint8ClampedArray).buffer as ArrayBuffer),
      ),
    ];
  }
  if (result.kind === 'native-export' || result.kind === 'composite') return [result.buffer];
  return documentTransferables(result.document);
}

export function workerTaskTransferables(task: SerializedImageWorkerTask): Transferable[] {
  if (task.kind === 'psd-import' || task.kind === 'native-import') return [task.buffer];
  if (task.kind === 'resize' || task.kind === 'native-export' || task.kind === 'composite') {
    return documentTransferables(task.document);
  }
  const buffers: ArrayBuffer[] = [];
  const visit = (layer: SerializedLayer) => {
    if (layer.rasterMask !== null) {
      if (layer.rasterMask.coverageStore.resource !== 'reference') {
        const tiles =
          layer.rasterMask.coverageStore.resource === 'snapshot'
            ? layer.rasterMask.coverageStore.snapshot.tiles
            : layer.rasterMask.coverageStore.tiles;
        for (const tile of tiles) {
          buffers.push(tile.coverage.buffer as ArrayBuffer);
        }
      }
    }
    if (layer.kind === 'pixel') {
      if (layer.pixelStore.resource !== 'reference') {
        const tiles =
          layer.pixelStore.resource === 'snapshot'
            ? layer.pixelStore.snapshot.tiles
            : layer.pixelStore.tiles;
        for (const tile of tiles) {
          buffers.push(tile.pixels.buffer as ArrayBuffer);
        }
      }
    } else if (layer.kind === 'group') layer.children.forEach(visit);
    else if (layer.kind === 'smart-object') layer.source.layers.forEach(visit);
  };
  visit(task.layer);
  if (task.selection !== null) {
    if (task.selection.coverageStore.resource !== 'reference') {
      const tiles =
        task.selection.coverageStore.resource === 'snapshot'
          ? task.selection.coverageStore.snapshot.tiles
          : task.selection.coverageStore.tiles;
      for (const tile of tiles) {
        buffers.push(tile.coverage.buffer as ArrayBuffer);
      }
    }
  }
  return buffers;
}
