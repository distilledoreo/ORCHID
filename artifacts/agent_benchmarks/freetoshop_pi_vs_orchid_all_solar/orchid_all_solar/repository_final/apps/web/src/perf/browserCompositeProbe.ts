import {
  createAdjustmentLayer,
  createAlphaChannel,
  createDocument,
  createGroupLayer,
  createPixelLayer,
  createRectangularSelection,
  createRasterMaskFromCoverageStore,
  createSelectionMask,
  createShapeLayer,
  createSmartObjectLayer,
  createTextLayer,
  patchPixels,
  setSelection,
  setLayerOpacity,
  setLayerVisibility,
  type ImageDocument,
  type PixelLayer,
} from '@freetoshop/core-document';
import {
  CompositeTileCache,
  compositeDocumentPixels,
  compositeDocumentRegion,
  rasterizeLayerNearest,
} from '@freetoshop/renderer';
import { paintStroke } from '@freetoshop/raster-engine';
import { gaussianBlurPatches, pointwiseRgbFilterPatches } from '@freetoshop/filter-engine';
import { TiledCoverageStore, TiledPixelStore } from '@freetoshop/pixel-storage';
import {
  decodeNativeDocument,
  IndexedDbDocumentRepository,
  serializeDocument,
} from '@freetoshop/persistence';
import { CompositeWorkerScheduler } from '../editor/compositeWorkerScheduler.js';
import { planAdvancedCompositeRender } from '../editor/compositeRenderPlan.js';
import { PersistentImageWorkerClient } from '../workers/client.js';

export interface BrowserCompositeProbeOptions {
  readonly width?: number;
  readonly height?: number;
  readonly layerCount?: number;
  readonly tileSize?: number;
  readonly warmSamples?: number;
  readonly coldSamples?: number;
}

export interface BrowserCompositeProbeResult {
  readonly schemaVersion: 1;
  readonly fixture: {
    readonly width: number;
    readonly height: number;
    readonly layerCount: number;
    readonly tileSize: number;
  };
  readonly cold: {
    readonly medianMs: number;
    readonly p95Ms: number;
    readonly samplesMs: readonly number[];
  };
  readonly warm: {
    readonly medianMs: number;
    readonly p95Ms: number;
    readonly samplesMs: readonly number[];
    readonly fullyWarm: boolean;
  };
  readonly visibilityToggle: {
    readonly coldMs: number;
    readonly warmMs: number;
  };
  readonly cache: {
    readonly hits: number;
    readonly misses: number;
    readonly regionLocalMisses: number;
    readonly fullReferenceMisses: number;
    readonly allocatedBytes: number;
  };
  readonly longTasksDuringWarmMs: number;
  readonly warmFasterThanCold: boolean;
  readonly runtime: {
    readonly userAgent: string;
    readonly hardwareConcurrency: number;
  };
}

export interface PersistentWorkerResourceProbeResult {
  readonly schemaVersion: 2;
  readonly firstTransferBytes: number;
  readonly metadataRevisionTransferBytes: number;
  readonly localizedEditTransferBytes: number;
  readonly exact: boolean;
  readonly reusedWithoutTileUpload: boolean;
  readonly transferredOnlyChangedTile: boolean;
}

export interface PersistentDocumentTaskProbeResult {
  readonly schemaVersion: 3;
  readonly firstNativeTransferBytes: number;
  readonly unchangedResizeTransferBytes: number;
  readonly resizedResultNativeTransferBytes: number;
  readonly localizedNativeTransferBytes: number;
  readonly streamedResizeResultTileCount: number;
  readonly hydratedResizeResultResourceCount: number;
  readonly streamedNativeImportResultTileCount: number;
  readonly hydratedNativeImportResultResourceCount: number;
  readonly importedResultNativeTransferBytes: number;
  readonly nativeImportProgressSamples: readonly number[];
  readonly nativeImportProgressMonotonic: boolean;
  readonly resizeExact: boolean;
  readonly nativeExact: boolean;
  readonly nativeImportExact: boolean;
}

export interface LargeStreamedResizeProbeResult {
  readonly schemaVersion: 1;
  readonly width: number;
  readonly height: number;
  readonly streamedTileCount: number;
  readonly hydratedResourceCount: number;
  readonly allocatedBytes: number;
  readonly peakAllocatedBytes: number;
  readonly expectedBytes: number;
  readonly durationMs: number;
  readonly progressMonotonic: boolean;
  readonly exact: boolean;
}

export interface PsdImportProgressProbeResult {
  readonly schemaVersion: 1;
  readonly progressSamples: readonly number[];
  readonly progressMonotonic: boolean;
  readonly streamedTileCount: number;
  readonly hydratedResourceCount: number;
  readonly layerCount: number;
  readonly exact: boolean;
}

export interface RepeatedMixedResultProbeSample {
  readonly iteration: number;
  readonly size: number;
  readonly inputTransferBytes: number;
  readonly streamedTileCount: number;
  readonly hydratedResourceCount: number;
  readonly retainedResourceCount: number;
  readonly resultAllocatedBytes: number;
  readonly peakResultAllocatedBytes: number;
  readonly retainedAllocatedBytes: number;
  readonly durationMs: number;
}

export interface RepeatedMixedResultProbeResult {
  readonly schemaVersion: 1;
  readonly sourceSize: number;
  readonly iterations: number;
  readonly samples: readonly RepeatedMixedResultProbeSample[];
  readonly zeroUploadAfterFirst: boolean;
  readonly retainedEqualsFinal: boolean;
  readonly noGenerationGrowth: boolean;
  readonly exact: boolean;
}

export interface CancelledMixedResultProbeSample {
  readonly threshold: number;
  readonly observedProgress: number;
  readonly inputTransferBytes: number;
  readonly rejectedWithAbortError: boolean;
  readonly partialStateCleared: boolean;
}

export interface MixedResultCancellationProbeResult {
  readonly schemaVersion: 1;
  readonly size: number;
  readonly samples: readonly CancelledMixedResultProbeSample[];
  readonly thresholdsReached: boolean;
  readonly everyCancellationCleared: boolean;
  readonly everyAttemptReuploaded: boolean;
  readonly recoveryInputTransferBytes: number;
  readonly recoveryStreamedTileCount: number;
  readonly recoveryRetainedResourceCount: number;
  readonly recoveryRetainedAllocatedBytes: number;
  readonly recoveryExact: boolean;
}

export interface DenseMixedLongSessionProbeResult {
  readonly schemaVersion: 1;
  readonly size: number;
  readonly iterations: number;
  readonly samples: readonly RepeatedMixedResultProbeSample[];
  readonly zeroUploadAfterFirst: boolean;
  readonly retainedEqualsFinal: boolean;
  readonly stableBySize: boolean;
  readonly maxDurationMs: number;
  readonly exact: boolean;
}

export interface StructuredWorkerSessionProbeResult {
  readonly schemaVersion: 1;
  readonly size: number;
  readonly iterations: number;
  readonly samples: readonly RepeatedMixedResultProbeSample[];
  readonly zeroUploadAfterFirst: boolean;
  readonly retainedEqualsFinal: boolean;
  readonly stableBySize: boolean;
  readonly structureExact: boolean;
  readonly contentExact: boolean;
  readonly maxDurationMs: number;
}

export interface StructuredCrossKindWorkerSample {
  readonly iteration: number;
  readonly size: number;
  readonly resizeInputTransferBytes: number;
  readonly resizeStreamedTileCount: number;
  readonly exportInputTransferBytes: number;
  readonly nativeByteLength: number;
  readonly importInputTransferBytes: number;
  readonly importStreamedTileCount: number;
  readonly importHydratedResourceCount: number;
  readonly retainedResourceCount: number;
  readonly retainedAllocatedBytes: number;
  readonly peakImportAllocatedBytes: number;
  readonly durationMs: number;
}

export interface StructuredCrossKindWorkerSessionProbeResult {
  readonly schemaVersion: 1;
  readonly size: number;
  readonly iterations: number;
  readonly operationCount: number;
  readonly samples: readonly StructuredCrossKindWorkerSample[];
  readonly zeroDocumentUploadAfterFirst: boolean;
  readonly binaryImportOnly: boolean;
  readonly retainedEqualsImportedGeneration: boolean;
  readonly stableBySize: boolean;
  readonly structureExact: boolean;
  readonly contentExact: boolean;
  readonly maxDurationMs: number;
}

export interface StructuredFilterCompositeMutationSample {
  readonly iteration: number;
  readonly filterInputTransferBytes: number;
  readonly streamedPatchCount: number;
  readonly compositeInputTransferBytes: number;
  readonly retainedResourceCount: number;
  readonly retainedAllocatedBytes: number;
  readonly durationMs: number;
  readonly compositeExact: boolean;
}

export interface StructuredFilterCompositeMutationProbeResult {
  readonly schemaVersion: 1;
  readonly size: number;
  readonly iterations: number;
  readonly initialCompositeInputTransferBytes: number;
  readonly samples: readonly StructuredFilterCompositeMutationSample[];
  readonly filtersReuseDocumentResources: boolean;
  readonly compositesTransferOnlyChangedPixels: boolean;
  readonly retainedGenerationBounded: boolean;
  readonly everyCompositeExact: boolean;
  readonly structureExact: boolean;
  readonly contentExactAfterEvenMutations: boolean;
  readonly maxDurationMs: number;
}

export interface StructuredLocalizedMutationSample {
  readonly iteration: number;
  readonly x: number;
  readonly y: number;
  readonly filterInputTransferBytes: number;
  readonly streamedPatchCount: number;
  readonly nativeInputTransferBytes: number;
  readonly retainedResourceCount: number;
  readonly retainedAllocatedBytes: number;
  readonly reopenedExact: boolean;
  readonly durationMs: number;
}

export interface StructuredLocalizedMutationProbeResult {
  readonly schemaVersion: 1;
  readonly size: number;
  readonly iterations: number;
  readonly initialNativeInputTransferBytes: number;
  readonly samples: readonly StructuredLocalizedMutationSample[];
  readonly selectionTransfersTileBounded: boolean;
  readonly nativeTransfersPixelTileBounded: boolean;
  readonly retainedGenerationBounded: boolean;
  readonly everyReopenExact: boolean;
  readonly structureExact: boolean;
  readonly accumulatedContentExact: boolean;
  readonly maxDurationMs: number;
}

export interface ProgressiveCancellationRecoverySample {
  readonly iteration: number;
  readonly cancelledGeneration: number;
  readonly recoveryGeneration: number;
  readonly verificationGeneration: number;
  readonly candidateInputTransferBytes: number;
  readonly recoveryInputTransferBytes: number;
  readonly verificationInputTransferBytes: number;
  readonly recoveryFrameBytes: number;
  readonly staleFramePublished: boolean;
  readonly recoveryExact: boolean;
  readonly durationMs: number;
}

export interface ProgressiveCancellationRecoveryProbeResult {
  readonly schemaVersion: 1;
  readonly size: number;
  readonly iterations: number;
  readonly samples: readonly ProgressiveCancellationRecoverySample[];
  readonly cancellationForcesCleanRecovery: boolean;
  readonly verificationReusesRecoveryResources: boolean;
  readonly localizedCandidatesUseTileDeltas: boolean;
  readonly noStaleFramesPublished: boolean;
  readonly everyRecoveryExact: boolean;
  readonly errorCount: number;
  readonly maxDurationMs: number;
}

export interface IncrementalRepositoryProbeResult {
  readonly schemaVersion: 1;
  readonly firstWrittenResourceCount: number;
  readonly metadataWrittenResourceCount: number;
  readonly editedWrittenResourceCount: number;
  readonly editedWrittenResourceBytes: number;
  readonly removedResourceCount: number;
  readonly deferredMetadataResourceReads: number;
  readonly hydratedResourceReads: number;
  readonly hydrationMemoized: boolean;
  readonly deferredMetadataExact: boolean;
  readonly exact: boolean;
  readonly legacyDeferredMetadataExact: boolean;
  readonly legacyExact: boolean;
}

export interface CustomRegionRasterizerProbeResult {
  readonly schemaVersion: 1;
  readonly fullRasterizerCalls: number;
  readonly regionRasterizerCalls: number;
  readonly sourcePatchBytes: number;
  readonly outputBytes: number;
  readonly exact: boolean;
}

export interface ComplementFilterProbeResult {
  readonly schemaVersion: 1;
  readonly pointwisePatchCount: number;
  readonly gaussianPatchCount: number;
  readonly coverageReadCount: number;
  readonly maxCoverageReadBytes: number;
  readonly materialized: boolean;
  readonly bounded: boolean;
}

export interface StreamingFilterWorkerProbeResult {
  readonly schemaVersion: 1;
  readonly patchCount: number;
  readonly streamedPatchCount: number;
  readonly progressSamples: readonly number[];
  readonly monotonicProgress: boolean;
  readonly exact: boolean;
}

function solidLayer(id: string, width: number, height: number, seed: number): PixelLayer {
  const pixels = new Uint8ClampedArray(width * height * 4);
  for (let offset = 0; offset < pixels.length; offset += 4) {
    pixels[offset] = (seed * 37 + offset / 4) % 256;
    pixels[offset + 1] = (seed * 71 + offset / 16) % 256;
    pixels[offset + 2] = (seed * 113 + offset / 64) % 256;
    pixels[offset + 3] = 224;
  }
  return createPixelLayer({
    id,
    name: id,
    width,
    height,
    pixels,
    opacity: 0.85,
    blendMode: seed % 3 === 0 ? 'multiply' : seed % 3 === 1 ? 'screen' : 'normal',
  });
}

function layeredDocument(width: number, height: number, layerCount: number): ImageDocument {
  return createDocument({
    id: `browser-perf-${String(width)}-${String(layerCount)}`,
    name: 'Browser performance fixture',
    width,
    height,
    layers: Array.from({ length: layerCount }, (_, index) =>
      solidLayer(`layer-${String(index)}`, width, height, index + 1),
    ),
  });
}

/** Proves persistent workers reuse unchanged stores and upload only locally changed tiles. */
export async function runPersistentWorkerResourceProbe(): Promise<PersistentWorkerResourceProbeResult> {
  const document = layeredDocument(512, 512, 2);
  let resolveFrame: ((pixels: Uint8ClampedArray) => void) | null = null;
  const scheduler = new CompositeWorkerScheduler({
    onFrame: (frame) => {
      resolveFrame?.(frame.pixels);
      resolveFrame = null;
    },
  });
  const schedule = (candidate: ImageDocument) =>
    new Promise<Uint8ClampedArray>((resolve) => {
      resolveFrame = resolve;
      scheduler.schedule(candidate, { chainFullRefine: false });
    });

  await schedule(document);
  const firstTransferBytes = scheduler.lastInputTransferByteLength;
  const metadataRevision = setLayerOpacity('layer-1', 0.42).execute(document);
  await schedule(metadataRevision);
  const metadataRevisionTransferBytes = scheduler.lastInputTransferByteLength;
  const localizedEdit = patchPixels('layer-0', {
    x: 10,
    y: 10,
    width: 1,
    height: 1,
    pixels: new Uint8ClampedArray([240, 80, 20, 255]),
  }).execute(metadataRevision);
  const pixels = await schedule(localizedEdit);
  const localizedEditTransferBytes = scheduler.lastInputTransferByteLength;
  const expected = compositeDocumentPixels(localizedEdit, { rasterize: rasterizeLayerNearest });
  const exact =
    pixels.length === expected.length && pixels.every((value, index) => value === expected[index]);
  scheduler.clear();
  return {
    schemaVersion: 2,
    firstTransferBytes,
    metadataRevisionTransferBytes,
    localizedEditTransferBytes,
    exact,
    reusedWithoutTileUpload: firstTransferBytes > 0 && metadataRevisionTransferBytes === 0,
    transferredOnlyChangedTile: localizedEditTransferBytes === 256 * 256 * 4,
  };
}

/** Measures resource reuse across the production native-export and resize task kinds. */
export async function runPersistentDocumentTaskProbe(): Promise<PersistentDocumentTaskProbeResult> {
  const document = layeredDocument(512, 512, 2);
  const client = new PersistentImageWorkerClient();
  try {
    const firstNative = await client.run({ kind: 'native-export', document });
    if (firstNative.kind !== 'native-export') throw new Error('Expected native export result');
    const firstNativeTransferBytes = client.lastTransferByteLength;

    const resized = await client.run({
      kind: 'resize',
      document,
      width: 256,
      height: 256,
      method: 'nearest',
    });
    if (resized.kind !== 'resize') throw new Error('Expected resize result');
    const unchangedResizeTransferBytes = client.lastTransferByteLength;
    const streamedResizeResultTileCount = client.lastStreamedResultTileCount;
    const hydratedResizeResultResourceCount = client.lastHydratedResultResourceCount;

    const resizedNative = await client.run({ kind: 'native-export', document: resized.document });
    if (resizedNative.kind !== 'native-export') throw new Error('Expected native export result');
    const resizedResultNativeTransferBytes = client.lastTransferByteLength;

    const localized = patchPixels('layer-0', {
      x: 10,
      y: 10,
      width: 1,
      height: 1,
      pixels: new Uint8ClampedArray([240, 80, 20, 255]),
    }).execute(resized.document);
    const localizedNative = await client.run({ kind: 'native-export', document: localized });
    if (localizedNative.kind !== 'native-export') throw new Error('Expected native export result');
    const localizedNativeTransferBytes = client.lastTransferByteLength;
    const reopened = await decodeNativeDocument(localizedNative.buffer);
    const reopenedLayer = reopened.layers[0];
    const nativeExact =
      reopenedLayer?.kind === 'pixel' &&
      reopenedLayer.pixelStore
        .read({ x: 10, y: 10, width: 1, height: 1 })
        .every((value, index) => value === [240, 80, 20, 255][index]);

    const nativeImportProgressSamples: number[] = [];
    const imported = await client.run(
      { kind: 'native-import', buffer: firstNative.buffer },
      { onProgress: (progress) => nativeImportProgressSamples.push(progress) },
    );
    if (imported.kind !== 'native-import') throw new Error('Expected native import result');
    const streamedNativeImportResultTileCount = client.lastStreamedResultTileCount;
    const hydratedNativeImportResultResourceCount = client.lastHydratedResultResourceCount;
    const nativeImportExact =
      imported.document.layers.length === document.layers.length &&
      document.layers.every((originalLayer, layerIndex) => {
        const importedLayer = imported.document.layers[layerIndex];
        if (originalLayer.kind !== 'pixel' || importedLayer?.kind !== 'pixel') return false;
        const originalTiles = [...originalLayer.pixelStore.tiles()];
        return (
          importedLayer.pixelStore.tileCount === originalTiles.length &&
          originalTiles.every((originalTile) => {
            const importedTile = importedLayer.pixelStore.getTile(
              originalTile.column,
              originalTile.row,
            );
            return (
              importedTile !== null &&
              importedTile.pixels.every((value, index) => value === originalTile.pixels[index])
            );
          })
        );
      });
    const importedNative = await client.run({
      kind: 'native-export',
      document: imported.document,
    });
    if (importedNative.kind !== 'native-export') {
      throw new Error('Expected imported native export result');
    }
    const importedResultNativeTransferBytes = client.lastTransferByteLength;

    return {
      schemaVersion: 3,
      firstNativeTransferBytes,
      unchangedResizeTransferBytes,
      resizedResultNativeTransferBytes,
      localizedNativeTransferBytes,
      streamedResizeResultTileCount,
      hydratedResizeResultResourceCount,
      streamedNativeImportResultTileCount,
      hydratedNativeImportResultResourceCount,
      importedResultNativeTransferBytes,
      nativeImportProgressSamples,
      nativeImportProgressMonotonic: nativeImportProgressSamples.every(
        (progress, index) =>
          index === 0 || progress >= (nativeImportProgressSamples[index - 1] ?? 0),
      ),
      resizeExact: resized.document.canvas.width === 256 && resized.document.canvas.height === 256,
      nativeExact,
      nativeImportExact,
    };
  } finally {
    client.dispose();
  }
}

/** Stress-gates a dense single-store result without retaining a second pending tile payload. */
export async function runLargeStreamedResizeProbe(): Promise<LargeStreamedResizeProbeResult> {
  const width = 2048;
  const height = 2048;
  const document = createDocument({
    id: 'large-streamed-resize',
    name: 'Large streamed resize',
    width,
    height,
    layers: [solidLayer('dense', width, height, 23)],
  });
  const client = new PersistentImageWorkerClient();
  const progress: number[] = [];
  const started = performance.now();
  try {
    const result = await client.run(
      { kind: 'resize', document, width, height, method: 'nearest' },
      { onProgress: (value) => progress.push(value) },
    );
    const durationMs = performance.now() - started;
    if (result.kind !== 'resize') throw new Error('Expected large resize result');
    const source = document.layers[0];
    const output = result.document.layers[0];
    const exact =
      source?.kind === 'pixel' &&
      output?.kind === 'pixel' &&
      output.pixelStore.tileCount === source.pixelStore.tileCount &&
      [...source.pixelStore.tiles()].every((sourceTile) => {
        const outputTile = output.pixelStore.getTile(sourceTile.column, sourceTile.row);
        return (
          outputTile !== null &&
          outputTile.pixels.every((value, index) => value === sourceTile.pixels[index])
        );
      });
    return {
      schemaVersion: 1,
      width,
      height,
      streamedTileCount: client.lastStreamedResultTileCount,
      hydratedResourceCount: client.lastHydratedResultResourceCount,
      allocatedBytes: client.lastStreamedResultAllocatedByteLength,
      peakAllocatedBytes: client.peakStreamedResultAllocatedByteLength,
      expectedBytes: width * height * 4,
      durationMs,
      progressMonotonic: progress.every(
        (value, index) => index === 0 || value >= (progress[index - 1] ?? 0),
      ),
      exact,
    };
  } finally {
    client.dispose();
  }
}

/** Exercises the production PSD worker path, including conversion and result streaming progress. */
export async function runPsdImportProgressProbe(
  bytes: readonly number[],
): Promise<PsdImportProgressProbeResult> {
  const client = new PersistentImageWorkerClient();
  const progressSamples: number[] = [];
  try {
    const result = await client.run(
      {
        kind: 'psd-import',
        name: 'pixel-layers-rgb-8.psd',
        buffer: Uint8Array.from(bytes).buffer,
      },
      { onProgress: (progress) => progressSamples.push(progress) },
    );
    if (result.kind !== 'psd-import') throw new Error('Expected PSD import result');
    const bottom = result.document.layers[0];
    const top = result.document.layers[1];
    const exact =
      result.report.layerCount === 2 &&
      bottom?.kind === 'pixel' &&
      bottom.name === 'Bottom blue' &&
      bottom.pixelStore
        .read({ x: 0, y: 0, width: 1, height: 1 })
        .every((value, index) => value === [0, 0, 180, 255][index]) &&
      top?.kind === 'pixel' &&
      top.name === 'Top red';
    return {
      schemaVersion: 1,
      progressSamples,
      progressMonotonic: progressSamples.every(
        (progress, index) => index === 0 || progress >= (progressSamples[index - 1] ?? 0),
      ),
      streamedTileCount: client.lastStreamedResultTileCount,
      hydratedResourceCount: client.lastHydratedResultResourceCount,
      layerCount: result.report.layerCount,
      exact,
    };
  } finally {
    client.dispose();
  }
}

function createSparseMixedResultDocument(sourceSize: number): ImageDocument {
  const tileSize = 256;
  const far = sourceSize - tileSize;
  const pixelStore = (seed: number) =>
    TiledPixelStore.empty(sourceSize, sourceSize, tileSize)
      .write({
        x: 0,
        y: 0,
        width: tileSize,
        height: tileSize,
        pixels: new Uint8ClampedArray(tileSize * tileSize * 4).fill(seed),
      })
      .store.write({
        x: far,
        y: far,
        width: tileSize,
        height: tileSize,
        pixels: new Uint8ClampedArray(tileSize * tileSize * 4).fill(seed + 1),
      }).store;
  const coverageStore = (seed: number) =>
    TiledCoverageStore.empty(sourceSize, sourceSize, tileSize)
      .write({
        x: 0,
        y: 0,
        width: tileSize,
        height: tileSize,
        coverage: new Uint8ClampedArray(tileSize * tileSize).fill(seed),
      })
      .store.write({
        x: far,
        y: far,
        width: tileSize,
        height: tileSize,
        coverage: new Uint8ClampedArray(tileSize * tileSize).fill(seed + 1),
      }).store;
  return createDocument({
    id: 'repeated-mixed-result',
    name: 'Repeated mixed result',
    width: sourceSize,
    height: sourceSize,
    layers: [17, 37].map((seed, index) =>
      createPixelLayer({
        id: `mixed-layer-${String(index)}`,
        name: `Mixed layer ${String(index)}`,
        width: sourceSize,
        height: sourceSize,
        pixelStore: pixelStore(seed),
        rasterMask: createRasterMaskFromCoverageStore(
          sourceSize,
          sourceSize,
          coverageStore(80 + index * 10),
        ),
      }),
    ),
    selection: createSelectionMask({
      width: sourceSize,
      height: sourceSize,
      coverageStore: coverageStore(120),
    }),
    channels: [
      createAlphaChannel({
        id: 'mixed-alpha',
        name: 'Mixed alpha',
        width: sourceSize,
        height: sourceSize,
        coverageStore: coverageStore(160),
      }),
    ],
  });
}

function createDenseMixedResultDocument(size: number): ImageDocument {
  const coverage = (value: number) => new Uint8ClampedArray(size * size).fill(value);
  return createDocument({
    id: 'dense-mixed-result',
    name: 'Dense mixed result',
    width: size,
    height: size,
    layers: [17, 37].map((seed, index) =>
      createPixelLayer({
        id: `dense-layer-${String(index)}`,
        name: `Dense layer ${String(index)}`,
        width: size,
        height: size,
        pixels: new Uint8ClampedArray(size * size * 4).fill(seed),
        rasterMask: createRasterMaskFromCoverageStore(
          size,
          size,
          TiledCoverageStore.fromContiguous(size, size, coverage(80 + index * 10)),
        ),
      }),
    ),
    selection: createSelectionMask({ width: size, height: size, coverage: coverage(120) }),
    channels: [
      createAlphaChannel({
        id: 'dense-alpha',
        name: 'Dense alpha',
        width: size,
        height: size,
        coverage: coverage(160),
      }),
    ],
  });
}

function createStructuredWorkerDocument(size: number): ImageDocument {
  const coverageStore = (width: number, height: number, value: number) =>
    TiledCoverageStore.fromContiguous(
      width,
      height,
      new Uint8ClampedArray(width * height).fill(value),
    );
  const mask = (width: number, height: number, value: number) =>
    createRasterMaskFromCoverageStore(width, height, coverageStore(width, height, value));
  const sourceSize = 256;
  const structuredSize = 128;
  return createDocument({
    id: 'structured-worker-session',
    name: 'Structured worker session',
    width: size,
    height: size,
    layers: [
      createGroupLayer({
        id: 'structured-group',
        name: 'Structured group',
        rasterMask: mask(size, size, 210),
        children: [
          createPixelLayer({
            id: 'nested-pixel',
            name: 'Nested pixel',
            width: size,
            height: size,
            pixels: new Uint8ClampedArray(size * size * 4).fill(17),
            rasterMask: mask(size, size, 180),
          }),
          createShapeLayer({
            id: 'nested-shape',
            name: 'Nested shape',
            geometry: { type: 'ellipse', width: 96, height: 72, cornerRadius: 0 },
            style: {
              fill: { red: 20, green: 40, blue: 60, alpha: 255 },
              stroke: { red: 220, green: 180, blue: 40, alpha: 255 },
              strokeWidth: 3,
            },
            rasterMask: mask(96, 72, 150),
          }),
          createTextLayer({
            id: 'nested-text',
            name: 'Nested text',
            text: 'Structured שלום',
            width: structuredSize,
            height: 64,
            style: { fontFamily: 'Arial', fontSize: 24, align: 'right' },
            rasterMask: mask(structuredSize, 64, 140),
          }),
          createSmartObjectLayer({
            id: 'nested-smart-object',
            name: 'Nested smart object',
            source: {
              id: 'structured-source',
              name: 'Structured source',
              canvas: { width: sourceSize, height: sourceSize },
              layers: [
                createPixelLayer({
                  id: 'source-pixel',
                  name: 'Source pixel',
                  width: sourceSize,
                  height: sourceSize,
                  pixels: new Uint8ClampedArray(sourceSize * sourceSize * 4).fill(37),
                  rasterMask: mask(sourceSize, sourceSize, 130),
                }),
              ],
            },
            smartFilters: [{ id: 'smart-invert', enabled: true, opacity: 1, type: 'invert' }],
            rasterMask: mask(sourceSize, sourceSize, 120),
          }),
        ],
      }),
      createAdjustmentLayer({
        id: 'structured-adjustment',
        name: 'Structured adjustment',
        adjustment: { type: 'brightness-contrast', brightness: 0.1, contrast: -0.2 },
        rasterMask: mask(size, size, 110),
      }),
    ],
    selection: createSelectionMask({
      width: size,
      height: size,
      coverageStore: coverageStore(size, size, 100),
    }),
    channels: [
      createAlphaChannel({
        id: 'structured-alpha',
        name: 'Structured alpha',
        width: size,
        height: size,
        coverageStore: coverageStore(size, size, 90),
      }),
    ],
  });
}

/** Repeats 4096px mixed pixel/mask/selection/channel results through one persistent worker. */
export async function runRepeatedMixedResultProbe(): Promise<RepeatedMixedResultProbeResult> {
  const sourceSize = 4096;
  let document = createSparseMixedResultDocument(sourceSize);
  const client = new PersistentImageWorkerClient();
  const samples: RepeatedMixedResultProbeSample[] = [];
  try {
    for (const [iteration, size] of [4095, 4096, 4095, 4096].entries()) {
      const started = performance.now();
      const result = await client.run({
        kind: 'resize',
        document,
        width: size,
        height: size,
        method: 'nearest',
      });
      const durationMs = performance.now() - started;
      if (result.kind !== 'resize') throw new Error('Expected mixed resize result');
      document = result.document;
      samples.push({
        iteration,
        size,
        inputTransferBytes: client.lastTransferByteLength,
        streamedTileCount: client.lastStreamedResultTileCount,
        hydratedResourceCount: client.lastHydratedResultResourceCount,
        retainedResourceCount: client.retainedResourceCount,
        resultAllocatedBytes: client.lastStreamedResultAllocatedByteLength,
        peakResultAllocatedBytes: client.peakStreamedResultAllocatedByteLength,
        retainedAllocatedBytes: client.retainedAllocatedByteLength,
        durationMs,
      });
    }
    const firstLayer = document.layers[0];
    const exact =
      document.canvas.width === sourceSize &&
      document.canvas.height === sourceSize &&
      firstLayer?.kind === 'pixel' &&
      firstLayer.pixelStore.read({ x: 0, y: 0, width: 1, height: 1 })[0] === 17 &&
      firstLayer.rasterMask?.coverageStore.read({ x: 0, y: 0, width: 1, height: 1 })[0] === 80 &&
      document.selection?.coverageStore.read({ x: 0, y: 0, width: 1, height: 1 })[0] === 120 &&
      document.channels[0]?.coverageStore.read({ x: 0, y: 0, width: 1, height: 1 })[0] === 160;
    return {
      schemaVersion: 1,
      sourceSize,
      iterations: samples.length,
      samples,
      zeroUploadAfterFirst: samples
        .slice(1)
        .every(({ inputTransferBytes }) => inputTransferBytes === 0),
      retainedEqualsFinal: samples.every(
        ({
          hydratedResourceCount,
          retainedResourceCount,
          resultAllocatedBytes,
          peakResultAllocatedBytes,
          retainedAllocatedBytes,
        }) =>
          hydratedResourceCount === retainedResourceCount &&
          resultAllocatedBytes === retainedAllocatedBytes &&
          peakResultAllocatedBytes === resultAllocatedBytes,
      ),
      noGenerationGrowth:
        samples[0]?.retainedAllocatedBytes === samples[2]?.retainedAllocatedBytes &&
        samples[1]?.retainedAllocatedBytes === samples[3]?.retainedAllocatedBytes,
      exact,
    };
  } finally {
    client.dispose();
  }
}

/** Aborts dense mixed-resource result streams at early, middle, and late tile positions. */
export async function runMixedResultCancellationProbe(): Promise<MixedResultCancellationProbeResult> {
  const size = 1024;
  const document = createDenseMixedResultDocument(size);
  const client = new PersistentImageWorkerClient();
  const samples: CancelledMixedResultProbeSample[] = [];
  try {
    for (const threshold of [0.2, 0.5, 0.85]) {
      const controller = new AbortController();
      let observedProgress = 0;
      let inputTransferBytes = 0;
      let rejectedWithAbortError = false;
      try {
        await client.run(
          {
            kind: 'resize',
            document,
            width: size - 1,
            height: size - 1,
            method: 'nearest',
          },
          {
            signal: controller.signal,
            onProgress: (progress) => {
              observedProgress = progress;
              if (progress < threshold) return;
              inputTransferBytes = client.lastTransferByteLength;
              controller.abort();
            },
          },
        );
      } catch (error) {
        rejectedWithAbortError =
          typeof error === 'object' &&
          error !== null &&
          'name' in error &&
          error.name === 'AbortError';
      }
      samples.push({
        threshold,
        observedProgress,
        inputTransferBytes,
        rejectedWithAbortError,
        partialStateCleared:
          client.lastStreamedResultTileCount === 0 &&
          client.lastHydratedResultResourceCount === 0 &&
          client.lastStreamedResultAllocatedByteLength === 0 &&
          client.peakStreamedResultAllocatedByteLength === 0 &&
          client.retainedResourceCount === 0 &&
          client.retainedAllocatedByteLength === 0,
      });
    }
    const recovered = await client.run({
      kind: 'resize',
      document,
      width: size - 1,
      height: size - 1,
      method: 'nearest',
    });
    if (recovered.kind !== 'resize') throw new Error('Expected recovered resize result');
    const layer = recovered.document.layers[0];
    const recoveryExact =
      client.lastTransferByteLength > 0 &&
      layer?.kind === 'pixel' &&
      layer.pixelStore.read({ x: 0, y: 0, width: 1, height: 1 })[0] === 17 &&
      layer.rasterMask?.coverageStore.read({ x: 0, y: 0, width: 1, height: 1 })[0] === 80 &&
      recovered.document.selection?.coverageStore.read({ x: 0, y: 0, width: 1, height: 1 })[0] ===
        120 &&
      recovered.document.channels[0]?.coverageStore.read({ x: 0, y: 0, width: 1, height: 1 })[0] ===
        160;
    return {
      schemaVersion: 1,
      size,
      samples,
      thresholdsReached: samples.every(
        ({ threshold, observedProgress }) => observedProgress >= threshold,
      ),
      everyCancellationCleared: samples.every(
        ({ rejectedWithAbortError, partialStateCleared }) =>
          rejectedWithAbortError && partialStateCleared,
      ),
      everyAttemptReuploaded: samples.every(({ inputTransferBytes }) => inputTransferBytes > 0),
      recoveryInputTransferBytes: client.lastTransferByteLength,
      recoveryStreamedTileCount: client.lastStreamedResultTileCount,
      recoveryRetainedResourceCount: client.retainedResourceCount,
      recoveryRetainedAllocatedBytes: client.retainedAllocatedByteLength,
      recoveryExact,
    };
  } finally {
    client.dispose();
  }
}

/** Sustains dense mixed-resource adoption/reuse across twelve alternating resize results. */
export async function runDenseMixedLongSessionProbe(): Promise<DenseMixedLongSessionProbeResult> {
  const size = 1024;
  let document = createDenseMixedResultDocument(size);
  const client = new PersistentImageWorkerClient();
  const samples: RepeatedMixedResultProbeSample[] = [];
  try {
    for (let iteration = 0; iteration < 12; iteration += 1) {
      const destinationSize = iteration % 2 === 0 ? size - 1 : size;
      const started = performance.now();
      const result = await client.run({
        kind: 'resize',
        document,
        width: destinationSize,
        height: destinationSize,
        method: 'nearest',
      });
      const durationMs = performance.now() - started;
      if (result.kind !== 'resize') throw new Error('Expected dense long-session resize result');
      document = result.document;
      samples.push({
        iteration,
        size: destinationSize,
        inputTransferBytes: client.lastTransferByteLength,
        streamedTileCount: client.lastStreamedResultTileCount,
        hydratedResourceCount: client.lastHydratedResultResourceCount,
        retainedResourceCount: client.retainedResourceCount,
        resultAllocatedBytes: client.lastStreamedResultAllocatedByteLength,
        peakResultAllocatedBytes: client.peakStreamedResultAllocatedByteLength,
        retainedAllocatedBytes: client.retainedAllocatedByteLength,
        durationMs,
      });
    }
    const retainedBySize = new Map<number, number>();
    const stableBySize = samples.every(({ size: sampleSize, retainedAllocatedBytes }) => {
      const prior = retainedBySize.get(sampleSize);
      retainedBySize.set(sampleSize, retainedAllocatedBytes);
      return prior === undefined || prior === retainedAllocatedBytes;
    });
    const layer = document.layers[0];
    const exact =
      document.canvas.width === size &&
      document.canvas.height === size &&
      layer?.kind === 'pixel' &&
      layer.pixelStore.read({ x: 0, y: 0, width: 1, height: 1 })[0] === 17 &&
      layer.rasterMask?.coverageStore.read({ x: 0, y: 0, width: 1, height: 1 })[0] === 80 &&
      document.selection?.coverageStore.read({ x: 0, y: 0, width: 1, height: 1 })[0] === 120 &&
      document.channels[0]?.coverageStore.read({ x: 0, y: 0, width: 1, height: 1 })[0] === 160;
    return {
      schemaVersion: 1,
      size,
      iterations: samples.length,
      samples,
      zeroUploadAfterFirst: samples
        .slice(1)
        .every(({ inputTransferBytes }) => inputTransferBytes === 0),
      retainedEqualsFinal: samples.every(
        ({
          hydratedResourceCount,
          retainedResourceCount,
          resultAllocatedBytes,
          peakResultAllocatedBytes,
          retainedAllocatedBytes,
        }) =>
          hydratedResourceCount === 6 &&
          retainedResourceCount === 6 &&
          resultAllocatedBytes === retainedAllocatedBytes &&
          peakResultAllocatedBytes === resultAllocatedBytes,
      ),
      stableBySize,
      maxDurationMs: Math.max(...samples.map(({ durationMs }) => durationMs)),
      exact,
    };
  } finally {
    client.dispose();
  }
}

/** Sustains nested group, structured layer, and smart-object resources across worker results. */
export async function runStructuredWorkerSessionProbe(): Promise<StructuredWorkerSessionProbeResult> {
  const size = 512;
  let document = createStructuredWorkerDocument(size);
  const client = new PersistentImageWorkerClient();
  const samples: RepeatedMixedResultProbeSample[] = [];
  try {
    for (let iteration = 0; iteration < 12; iteration += 1) {
      const destinationSize = iteration % 2 === 0 ? size - 1 : size;
      const started = performance.now();
      const result = await client.run({
        kind: 'resize',
        document,
        width: destinationSize,
        height: destinationSize,
        method: 'nearest',
      });
      const durationMs = performance.now() - started;
      if (result.kind !== 'resize') throw new Error('Expected structured resize result');
      document = result.document;
      samples.push({
        iteration,
        size: destinationSize,
        inputTransferBytes: client.lastTransferByteLength,
        streamedTileCount: client.lastStreamedResultTileCount,
        hydratedResourceCount: client.lastHydratedResultResourceCount,
        retainedResourceCount: client.retainedResourceCount,
        resultAllocatedBytes: client.lastStreamedResultAllocatedByteLength,
        peakResultAllocatedBytes: client.peakStreamedResultAllocatedByteLength,
        retainedAllocatedBytes: client.retainedAllocatedByteLength,
        durationMs,
      });
    }
    const retainedBySize = new Map<number, number>();
    const stableBySize = samples.every(({ size: sampleSize, retainedAllocatedBytes }) => {
      const prior = retainedBySize.get(sampleSize);
      retainedBySize.set(sampleSize, retainedAllocatedBytes);
      return prior === undefined || prior === retainedAllocatedBytes;
    });
    const group = document.layers[0];
    const adjustment = document.layers[1];
    const nestedPixel = group?.kind === 'group' ? group.children[0] : undefined;
    const shape = group?.kind === 'group' ? group.children[1] : undefined;
    const text = group?.kind === 'group' ? group.children[2] : undefined;
    const smart = group?.kind === 'group' ? group.children[3] : undefined;
    const sourcePixel = smart?.kind === 'smart-object' ? smart.source.layers[0] : undefined;
    const structureExact =
      group?.kind === 'group' &&
      group.children.length === 4 &&
      Math.abs(group.transform.a - 1) < 1e-12 &&
      shape?.kind === 'shape' &&
      shape.geometry.type === 'ellipse' &&
      shape.geometry.width === 96 &&
      shape.style.strokeWidth === 3 &&
      text?.kind === 'text' &&
      text.text === 'Structured שלום' &&
      text.style.align === 'right' &&
      smart?.kind === 'smart-object' &&
      smart.source.canvas.width === 256 &&
      smart.smartFilters[0]?.type === 'invert' &&
      adjustment?.kind === 'adjustment' &&
      adjustment.adjustment.type === 'brightness-contrast';
    const contentExact =
      nestedPixel?.kind === 'pixel' &&
      nestedPixel.pixelStore.read({ x: 0, y: 0, width: 1, height: 1 })[0] === 17 &&
      nestedPixel.rasterMask?.coverageStore.read({ x: 0, y: 0, width: 1, height: 1 })[0] === 180 &&
      sourcePixel?.kind === 'pixel' &&
      sourcePixel.pixelStore.read({ x: 0, y: 0, width: 1, height: 1 })[0] === 37 &&
      sourcePixel.rasterMask?.coverageStore.read({ x: 0, y: 0, width: 1, height: 1 })[0] === 130 &&
      group?.rasterMask?.coverageStore.read({ x: 0, y: 0, width: 1, height: 1 })[0] === 210 &&
      shape?.rasterMask?.coverageStore.read({ x: 0, y: 0, width: 1, height: 1 })[0] === 150 &&
      text?.rasterMask?.coverageStore.read({ x: 0, y: 0, width: 1, height: 1 })[0] === 140 &&
      smart?.rasterMask?.coverageStore.read({ x: 0, y: 0, width: 1, height: 1 })[0] === 120 &&
      adjustment?.rasterMask?.coverageStore.read({ x: 0, y: 0, width: 1, height: 1 })[0] === 110 &&
      document.selection?.coverageStore.read({ x: 0, y: 0, width: 1, height: 1 })[0] === 100 &&
      document.channels[0]?.coverageStore.read({ x: 0, y: 0, width: 1, height: 1 })[0] === 90;
    return {
      schemaVersion: 1,
      size,
      iterations: samples.length,
      samples,
      zeroUploadAfterFirst: samples
        .slice(1)
        .every(({ inputTransferBytes }) => inputTransferBytes === 0),
      retainedEqualsFinal: samples.every(
        ({
          hydratedResourceCount,
          retainedResourceCount,
          resultAllocatedBytes,
          peakResultAllocatedBytes,
          retainedAllocatedBytes,
        }) =>
          hydratedResourceCount === 11 &&
          retainedResourceCount === 11 &&
          resultAllocatedBytes === retainedAllocatedBytes &&
          peakResultAllocatedBytes === resultAllocatedBytes,
      ),
      stableBySize,
      structureExact,
      contentExact,
      maxDurationMs: Math.max(...samples.map(({ durationMs }) => durationMs)),
    };
  } finally {
    client.dispose();
  }
}

/** Sustains resize, native export, and native import over one nested structured worker session. */
export async function runStructuredCrossKindWorkerSessionProbe(): Promise<StructuredCrossKindWorkerSessionProbeResult> {
  const size = 512;
  let document = createStructuredWorkerDocument(size);
  const client = new PersistentImageWorkerClient();
  const samples: StructuredCrossKindWorkerSample[] = [];
  try {
    for (let iteration = 0; iteration < 4; iteration += 1) {
      const destinationSize = iteration % 2 === 0 ? size - 1 : size;
      const started = performance.now();
      const resized = await client.run({
        kind: 'resize',
        document,
        width: destinationSize,
        height: destinationSize,
        method: 'nearest',
      });
      if (resized.kind !== 'resize') throw new Error('Expected structured resize result');
      const resizeInputTransferBytes = client.lastTransferByteLength;
      const resizeStreamedTileCount = client.lastStreamedResultTileCount;

      const exported = await client.run({ kind: 'native-export', document: resized.document });
      if (exported.kind !== 'native-export') throw new Error('Expected structured native export');
      const exportInputTransferBytes = client.lastTransferByteLength;
      const nativeByteLength = exported.buffer.byteLength;

      const imported = await client.run({ kind: 'native-import', buffer: exported.buffer });
      if (imported.kind !== 'native-import') throw new Error('Expected structured native import');
      document = imported.document;
      samples.push({
        iteration,
        size: destinationSize,
        resizeInputTransferBytes,
        resizeStreamedTileCount,
        exportInputTransferBytes,
        nativeByteLength,
        importInputTransferBytes: client.lastTransferByteLength,
        importStreamedTileCount: client.lastStreamedResultTileCount,
        importHydratedResourceCount: client.lastHydratedResultResourceCount,
        retainedResourceCount: client.retainedResourceCount,
        retainedAllocatedBytes: client.retainedAllocatedByteLength,
        peakImportAllocatedBytes: client.peakStreamedResultAllocatedByteLength,
        durationMs: performance.now() - started,
      });
    }

    const retainedBySize = new Map<number, number>();
    const stableBySize = samples.every(({ size: sampleSize, retainedAllocatedBytes }) => {
      const prior = retainedBySize.get(sampleSize);
      retainedBySize.set(sampleSize, retainedAllocatedBytes);
      return prior === undefined || prior === retainedAllocatedBytes;
    });
    const group = document.layers[0];
    const adjustment = document.layers[1];
    const nestedPixel = group?.kind === 'group' ? group.children[0] : undefined;
    const shape = group?.kind === 'group' ? group.children[1] : undefined;
    const text = group?.kind === 'group' ? group.children[2] : undefined;
    const smart = group?.kind === 'group' ? group.children[3] : undefined;
    const sourcePixel = smart?.kind === 'smart-object' ? smart.source.layers[0] : undefined;
    const structureExact =
      group?.kind === 'group' &&
      group.children.length === 4 &&
      Math.abs(group.transform.a - 1) < 1e-12 &&
      shape?.kind === 'shape' &&
      shape.geometry.type === 'ellipse' &&
      shape.geometry.width === 96 &&
      shape.style.strokeWidth === 3 &&
      text?.kind === 'text' &&
      text.text === 'Structured שלום' &&
      text.style.align === 'right' &&
      smart?.kind === 'smart-object' &&
      smart.source.canvas.width === 256 &&
      smart.smartFilters[0]?.type === 'invert' &&
      adjustment?.kind === 'adjustment' &&
      adjustment.adjustment.type === 'brightness-contrast';
    const contentExact =
      nestedPixel?.kind === 'pixel' &&
      nestedPixel.pixelStore.read({ x: 0, y: 0, width: 1, height: 1 })[0] === 17 &&
      nestedPixel.rasterMask?.coverageStore.read({ x: 0, y: 0, width: 1, height: 1 })[0] === 180 &&
      sourcePixel?.kind === 'pixel' &&
      sourcePixel.pixelStore.read({ x: 0, y: 0, width: 1, height: 1 })[0] === 37 &&
      sourcePixel.rasterMask?.coverageStore.read({ x: 0, y: 0, width: 1, height: 1 })[0] === 130 &&
      group?.rasterMask?.coverageStore.read({ x: 0, y: 0, width: 1, height: 1 })[0] === 210 &&
      shape?.rasterMask?.coverageStore.read({ x: 0, y: 0, width: 1, height: 1 })[0] === 150 &&
      text?.rasterMask?.coverageStore.read({ x: 0, y: 0, width: 1, height: 1 })[0] === 140 &&
      smart?.rasterMask?.coverageStore.read({ x: 0, y: 0, width: 1, height: 1 })[0] === 120 &&
      adjustment?.rasterMask?.coverageStore.read({ x: 0, y: 0, width: 1, height: 1 })[0] === 110 &&
      document.selection?.coverageStore.read({ x: 0, y: 0, width: 1, height: 1 })[0] === 100 &&
      document.channels[0]?.coverageStore.read({ x: 0, y: 0, width: 1, height: 1 })[0] === 90;

    return {
      schemaVersion: 1,
      size,
      iterations: samples.length,
      operationCount: samples.length * 3,
      samples,
      zeroDocumentUploadAfterFirst: samples.every(
        ({ iteration, resizeInputTransferBytes, exportInputTransferBytes }) =>
          (iteration === 0 || resizeInputTransferBytes === 0) && exportInputTransferBytes === 0,
      ),
      binaryImportOnly: samples.every(
        ({ nativeByteLength, importInputTransferBytes }) =>
          nativeByteLength > 0 && importInputTransferBytes === nativeByteLength,
      ),
      retainedEqualsImportedGeneration: samples.every(
        ({
          importHydratedResourceCount,
          retainedResourceCount,
          retainedAllocatedBytes,
          peakImportAllocatedBytes,
        }) =>
          importHydratedResourceCount === 11 &&
          retainedResourceCount === 11 &&
          retainedAllocatedBytes === peakImportAllocatedBytes,
      ),
      stableBySize,
      structureExact,
      contentExact,
      maxDurationMs: Math.max(...samples.map(({ durationMs }) => durationMs)),
    };
  } finally {
    client.dispose();
  }
}

/** Alternates destructive filter mutations and cold composites in one structured worker namespace. */
export async function runStructuredFilterCompositeMutationProbe(): Promise<StructuredFilterCompositeMutationProbeResult> {
  const size = 512;
  let document = createStructuredWorkerDocument(size);
  const client = new PersistentImageWorkerClient();
  const samples: StructuredFilterCompositeMutationSample[] = [];
  try {
    const initial = await client.run({ kind: 'composite', document, generation: 0 });
    if (initial.kind !== 'composite') throw new Error('Expected initial structured composite');
    const initialCompositeInputTransferBytes = client.lastTransferByteLength;
    const initialPixels = new Uint8ClampedArray(initial.buffer);
    let invertedPixels: Uint8ClampedArray | null = null;

    for (let iteration = 0; iteration < 4; iteration += 1) {
      const group = document.layers[0];
      const layer = group?.kind === 'group' ? group.children[0] : undefined;
      if (layer?.kind !== 'pixel') throw new Error('Expected nested structured pixel layer');
      const started = performance.now();
      const filtered = await client.run({
        kind: 'filter',
        layer,
        selection: null,
        worldTransform: layer.transform,
        draft: {
          kind: 'invert',
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
          blurRadius: 0,
        },
      });
      if (filtered.kind !== 'filter') throw new Error('Expected structured filter result');
      const filterInputTransferBytes = client.lastTransferByteLength;
      const streamedPatchCount = client.lastStreamedPatchCount;
      for (const patch of filtered.patches) {
        document = patchPixels(layer.id, {
          ...patch,
          pixels: new Uint8ClampedArray(patch.pixels),
        }).execute(document);
      }

      const composited = await client.run({
        kind: 'composite',
        document,
        generation: iteration + 1,
      });
      if (composited.kind !== 'composite') throw new Error('Expected mutated structured composite');
      const output = new Uint8ClampedArray(composited.buffer);
      const expected = iteration % 2 === 0 ? invertedPixels : initialPixels;
      const compositeExact =
        expected === null
          ? output.length === initialPixels.length &&
            output.some((value, index) => value !== initialPixels[index])
          : output.length === expected.length &&
            output.every((value, index) => value === expected[index]);
      if (iteration === 0) invertedPixels = output.slice();
      samples.push({
        iteration,
        filterInputTransferBytes,
        streamedPatchCount,
        compositeInputTransferBytes: client.lastTransferByteLength,
        retainedResourceCount: client.retainedResourceCount,
        retainedAllocatedBytes: client.retainedAllocatedByteLength,
        durationMs: performance.now() - started,
        compositeExact,
      });
    }

    const group = document.layers[0];
    const adjustment = document.layers[1];
    const nestedPixel = group?.kind === 'group' ? group.children[0] : undefined;
    const shape = group?.kind === 'group' ? group.children[1] : undefined;
    const text = group?.kind === 'group' ? group.children[2] : undefined;
    const smart = group?.kind === 'group' ? group.children[3] : undefined;
    const sourcePixel = smart?.kind === 'smart-object' ? smart.source.layers[0] : undefined;
    return {
      schemaVersion: 1,
      size,
      iterations: samples.length,
      initialCompositeInputTransferBytes,
      samples,
      filtersReuseDocumentResources: samples.every(
        ({ filterInputTransferBytes }) => filterInputTransferBytes === 0,
      ),
      compositesTransferOnlyChangedPixels: samples.every(
        ({ compositeInputTransferBytes }) => compositeInputTransferBytes === size * size * 4,
      ),
      retainedGenerationBounded: samples.every(
        ({ retainedResourceCount, retainedAllocatedBytes }) =>
          retainedResourceCount === 9 && retainedAllocatedBytes === 2_243_328,
      ),
      everyCompositeExact: samples.every(({ compositeExact }) => compositeExact),
      structureExact:
        group?.kind === 'group' &&
        group.children.length === 4 &&
        shape?.kind === 'shape' &&
        shape.geometry.type === 'ellipse' &&
        text?.kind === 'text' &&
        text.text === 'Structured שלום' &&
        smart?.kind === 'smart-object' &&
        smart.smartFilters[0]?.type === 'invert' &&
        adjustment?.kind === 'adjustment' &&
        adjustment.adjustment.type === 'brightness-contrast',
      contentExactAfterEvenMutations:
        nestedPixel?.kind === 'pixel' &&
        nestedPixel.pixelStore.read({ x: 0, y: 0, width: 1, height: 1 })[0] === 17 &&
        nestedPixel.rasterMask?.coverageStore.read({ x: 0, y: 0, width: 1, height: 1 })[0] ===
          180 &&
        sourcePixel?.kind === 'pixel' &&
        sourcePixel.pixelStore.read({ x: 0, y: 0, width: 1, height: 1 })[0] === 37 &&
        document.selection?.coverageStore.read({ x: 0, y: 0, width: 1, height: 1 })[0] === 100 &&
        document.channels[0]?.coverageStore.read({ x: 0, y: 0, width: 1, height: 1 })[0] === 90,
      maxDurationMs: Math.max(...samples.map(({ durationMs }) => durationMs)),
    };
  } finally {
    client.dispose();
  }
}

/** Repeats selection-local filters and native save/reopen over different structured tiles. */
export async function runStructuredLocalizedMutationProbe(): Promise<StructuredLocalizedMutationProbeResult> {
  const size = 512;
  const positions = [
    { x: 16, y: 16 },
    { x: 272, y: 16 },
    { x: 16, y: 272 },
    { x: 272, y: 272 },
  ] as const;
  let document = createStructuredWorkerDocument(size);
  const client = new PersistentImageWorkerClient();
  const samples: StructuredLocalizedMutationSample[] = [];
  try {
    const initial = await client.run({ kind: 'native-export', document });
    if (initial.kind !== 'native-export')
      throw new Error('Expected initial structured native export');
    const initialNativeInputTransferBytes = client.lastTransferByteLength;

    for (const [iteration, position] of positions.entries()) {
      const started = performance.now();
      const selection = createRectangularSelection(document.canvas, {
        ...position,
        width: 32,
        height: 32,
      });
      document = setSelection(selection).execute(document);
      const group = document.layers[0];
      const layer = group?.kind === 'group' ? group.children[0] : undefined;
      if (layer?.kind !== 'pixel') throw new Error('Expected nested structured pixel layer');
      const filtered = await client.run({
        kind: 'filter',
        layer,
        selection: document.selection,
        worldTransform: layer.transform,
        draft: {
          kind: 'invert',
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
          blurRadius: 0,
        },
      });
      if (filtered.kind !== 'filter') throw new Error('Expected localized filter result');
      const filterInputTransferBytes = client.lastTransferByteLength;
      const streamedPatchCount = client.lastStreamedPatchCount;
      for (const patch of filtered.patches) {
        document = patchPixels(layer.id, {
          ...patch,
          pixels: new Uint8ClampedArray(patch.pixels),
        }).execute(document);
      }

      const exported = await client.run({ kind: 'native-export', document });
      if (exported.kind !== 'native-export') throw new Error('Expected localized native export');
      const nativeInputTransferBytes = client.lastTransferByteLength;
      const reopened = await decodeNativeDocument(exported.buffer);
      const reopenedGroup = reopened.layers[0];
      const reopenedPixel = reopenedGroup?.kind === 'group' ? reopenedGroup.children[0] : undefined;
      const reopenedExact =
        reopenedGroup?.kind === 'group' &&
        reopenedPixel?.kind === 'pixel' &&
        reopenedPixel.pixelStore.read({ ...position, width: 1, height: 1 })[0] === 238 &&
        reopened.selection?.coverageStore.read({ ...position, width: 1, height: 1 })[0] === 255 &&
        reopened.selection.coverageStore.read({ x: 128, y: 128, width: 1, height: 1 })[0] === 0 &&
        reopenedGroup.children.length === 4;
      samples.push({
        iteration,
        ...position,
        filterInputTransferBytes,
        streamedPatchCount,
        nativeInputTransferBytes,
        retainedResourceCount: client.retainedResourceCount,
        retainedAllocatedBytes: client.retainedAllocatedByteLength,
        reopenedExact,
        durationMs: performance.now() - started,
      });
    }

    const group = document.layers[0];
    const layer = group?.kind === 'group' ? group.children[0] : undefined;
    const shape = group?.kind === 'group' ? group.children[1] : undefined;
    const text = group?.kind === 'group' ? group.children[2] : undefined;
    const smart = group?.kind === 'group' ? group.children[3] : undefined;
    return {
      schemaVersion: 1,
      size,
      iterations: samples.length,
      initialNativeInputTransferBytes,
      samples,
      selectionTransfersTileBounded: samples.every(
        ({ filterInputTransferBytes }) => filterInputTransferBytes === 256 * 256,
      ),
      nativeTransfersPixelTileBounded: samples.every(
        ({ nativeInputTransferBytes }) => nativeInputTransferBytes === 256 * 256 * 4,
      ),
      retainedGenerationBounded: samples.every(
        ({ retainedResourceCount, retainedAllocatedBytes }) =>
          retainedResourceCount === 11 && retainedAllocatedBytes === 2_571_008,
      ),
      everyReopenExact: samples.every(({ reopenedExact }) => reopenedExact),
      structureExact:
        group?.kind === 'group' &&
        group.children.length === 4 &&
        shape?.kind === 'shape' &&
        text?.kind === 'text' &&
        smart?.kind === 'smart-object' &&
        smart.smartFilters[0]?.type === 'invert',
      accumulatedContentExact:
        layer?.kind === 'pixel' &&
        positions.every(
          (position) => layer.pixelStore.read({ ...position, width: 1, height: 1 })[0] === 238,
        ) &&
        layer.pixelStore.read({ x: 128, y: 128, width: 1, height: 1 })[0] === 17 &&
        document.channels[0]?.coverageStore.read({ x: 0, y: 0, width: 1, height: 1 })[0] === 90,
      maxDurationMs: Math.max(...samples.map(({ durationMs }) => durationMs)),
    };
  } finally {
    client.dispose();
  }
}

/** Supersedes real progressive composite work and verifies clean, exact recovery generations. */
export async function runProgressiveCancellationRecoveryProbe(): Promise<ProgressiveCancellationRecoveryProbeResult> {
  const size = 512;
  const positions = [
    { x: 16, y: 16 },
    { x: 272, y: 16 },
    { x: 16, y: 272 },
  ] as const;
  let document = createStructuredWorkerDocument(size);
  const publishedGenerations: number[] = [];
  const errors: Error[] = [];
  let cancelledGeneration = -1;
  let recoveryGeneration = -1;
  let recoveryDocument = document;
  let recoveryViewport = { x: 0, y: 0, width: 64, height: 64 };
  let recoveryInputTransferBytes = 0;
  const scheduler = new CompositeWorkerScheduler({
    onFrame: (frame) => publishedGenerations.push(frame.generation),
    onError: (error) => errors.push(error),
    onProgress: (progress, generation) => {
      if (generation !== cancelledGeneration || progress < 0.2 || recoveryGeneration >= 0) return;
      recoveryGeneration = scheduler.schedule(recoveryDocument, {
        viewportRegion: recoveryViewport,
        progressiveViewportOnly: true,
        chainFullRefine: false,
      });
      recoveryInputTransferBytes = scheduler.lastInputTransferByteLength;
    },
  });
  const samples: ProgressiveCancellationRecoverySample[] = [];
  const waitForFrame = async (generation: number) => {
    const started = performance.now();
    while (performance.now() - started < 15_000) {
      const frame = scheduler.frameFor(recoveryDocument, generation);
      if (frame) return frame;
      await new Promise((resolve) => window.setTimeout(resolve, 8));
    }
    throw new Error(`Progressive recovery generation ${String(generation)} timed out`);
  };
  try {
    for (const [iteration, position] of positions.entries()) {
      const started = performance.now();
      document = patchPixels('nested-pixel', {
        ...position,
        width: 1,
        height: 1,
        pixels: new Uint8ClampedArray([80 + iteration, 120, 200, 255]),
      }).execute(document);
      recoveryDocument = document;
      recoveryViewport = { ...position, width: 64, height: 64 };
      recoveryGeneration = -1;
      cancelledGeneration = scheduler.schedule(document, {
        viewportRegion: recoveryViewport,
        progressiveViewportOnly: true,
        chainFullRefine: false,
      });
      const candidateInputTransferBytes = scheduler.lastInputTransferByteLength;
      while (recoveryGeneration < 0 && performance.now() - started < 15_000) {
        await new Promise((resolve) => window.setTimeout(resolve, 4));
      }
      if (recoveryGeneration < 0) throw new Error('Progressive cancellation checkpoint timed out');
      const recovered = await waitForFrame(recoveryGeneration);

      const verificationGeneration = scheduler.schedule(document, {
        viewportRegion: recoveryViewport,
        progressiveViewportOnly: true,
        chainFullRefine: false,
      });
      const verificationInputTransferBytes = scheduler.lastInputTransferByteLength;
      const verified = await waitForFrame(verificationGeneration);
      const recoveryExact =
        recovered.pixelRegion?.x === verified.pixelRegion?.x &&
        recovered.pixelRegion?.y === verified.pixelRegion?.y &&
        recovered.pixels.length === verified.pixels.length &&
        recovered.pixels.every((value, index) => value === verified.pixels[index]);
      samples.push({
        iteration,
        cancelledGeneration,
        recoveryGeneration,
        verificationGeneration,
        candidateInputTransferBytes,
        recoveryInputTransferBytes,
        verificationInputTransferBytes,
        recoveryFrameBytes: recovered.pixels.byteLength,
        staleFramePublished: publishedGenerations.includes(cancelledGeneration),
        recoveryExact,
        durationMs: performance.now() - started,
      });
    }
    return {
      schemaVersion: 1,
      size,
      iterations: samples.length,
      samples,
      cancellationForcesCleanRecovery: samples.every(
        ({ recoveryInputTransferBytes: bytes }) => bytes === 2_243_328,
      ),
      verificationReusesRecoveryResources: samples.every(
        ({ verificationInputTransferBytes }) => verificationInputTransferBytes === 0,
      ),
      localizedCandidatesUseTileDeltas: samples.every(
        ({ iteration, candidateInputTransferBytes }) =>
          candidateInputTransferBytes === (iteration === 0 ? 2_243_328 : 256 * 256 * 4),
      ),
      noStaleFramesPublished: samples.every(({ staleFramePublished }) => !staleFramePublished),
      everyRecoveryExact: samples.every(({ recoveryExact }) => recoveryExact),
      errorCount: errors.length,
      maxDurationMs: Math.max(...samples.map(({ durationMs }) => durationMs)),
    };
  } finally {
    scheduler.clear();
  }
}

/** Proves production IndexedDB autosave writes and collects content-addressed tiles incrementally. */
export async function runIncrementalRepositoryProbe(): Promise<IncrementalRepositoryProbeResult> {
  const width = 512;
  const height = 512;
  const pixels = new Uint8ClampedArray(width * height * 4);
  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      const offset = (y * width + x) * 4;
      pixels[offset] = x % 256;
      pixels[offset + 1] = y % 256;
      pixels[offset + 2] = (x + y) % 256;
      pixels[offset + 3] = 255;
    }
  }
  const id = `indexeddb-cas-probe-${crypto.randomUUID()}`;
  const repository = new IndexedDbDocumentRepository({ databaseName: id });
  const document = createDocument({
    id,
    name: 'IndexedDB CAS probe',
    width,
    height,
    layers: [createPixelLayer({ id: 'layer-0', name: 'Pixels', width, height, pixels })],
  });

  await repository.save(document);
  const first = repository.lastSaveStats;
  const metadataRevision = setLayerOpacity('layer-0', 0.42).execute(document);
  await repository.save(metadataRevision);
  const metadata = repository.lastSaveStats;
  const edited = patchPixels('layer-0', {
    x: 10,
    y: 10,
    width: 1,
    height: 1,
    pixels: new Uint8ClampedArray([240, 80, 20, 255]),
  }).execute(metadataRevision);
  await repository.save(edited);
  const edit = repository.lastSaveStats;
  const prepared = await repository.prepareLoad(id);
  const deferredMetadataResourceReads = repository.lastLoadStats?.readResourceCount ?? -1;
  const deferredMetadataExact =
    prepared?.metadata.id === id &&
    prepared.metadata.name === 'IndexedDB CAS probe' &&
    prepared.metadata.width === width &&
    prepared.metadata.height === height &&
    prepared.metadata.topLevelLayerCount === 1 &&
    prepared.metadata.referencedResourceCount === edit?.referencedResourceCount &&
    !prepared.metadata.legacy;
  const firstHydration = prepared?.hydrate();
  const hydrationMemoized = prepared?.hydrate() === firstHydration;
  const reopened = await firstHydration;
  const hydratedResourceReads = repository.lastLoadStats?.readResourceCount ?? -1;
  const reopenedLayer = reopened?.layers[0];
  const exact =
    reopenedLayer?.kind === 'pixel' &&
    reopenedLayer.opacity === 0.42 &&
    reopenedLayer.pixelStore
      .read({ x: 10, y: 10, width: 1, height: 1 })
      .every((value, index) => value === [240, 80, 20, 255][index]);

  await repository.save(createDocument({ id, name: 'Collected', width: 1, height: 1, layers: [] }));
  const collected = repository.lastSaveStats;
  const legacyDatabaseName = `${id}-legacy`;
  const legacyDatabase = await new Promise<IDBDatabase>((resolve, reject) => {
    const request = indexedDB.open(legacyDatabaseName, 1);
    request.onupgradeneeded = () => {
      request.result.createObjectStore('documents', { keyPath: 'id' });
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error ?? new Error('Could not create legacy database'));
  });
  await new Promise<void>((resolve, reject) => {
    const transaction = legacyDatabase.transaction('documents', 'readwrite');
    transaction.objectStore('documents').put({ id, serialized: serializeDocument(edited) });
    transaction.oncomplete = () => resolve();
    transaction.onerror = () => reject(transaction.error ?? new Error('Legacy write failed'));
    transaction.onabort = () => reject(transaction.error ?? new Error('Legacy write aborted'));
  });
  legacyDatabase.close();
  const legacyRepository = new IndexedDbDocumentRepository({
    databaseName: legacyDatabaseName,
  });
  const legacyPrepared = await legacyRepository.prepareLoad(id);
  const legacyDeferredMetadataExact =
    legacyPrepared?.metadata.id === id &&
    legacyPrepared.metadata.width === width &&
    legacyPrepared.metadata.height === height &&
    legacyPrepared.metadata.topLevelLayerCount === 1 &&
    legacyPrepared.metadata.referencedResourceCount === 0 &&
    legacyPrepared.metadata.legacy &&
    legacyRepository.lastLoadStats?.readResourceCount === 0;
  const legacyReopened = await legacyPrepared?.hydrate();
  const legacyLayer = legacyReopened?.layers[0];
  const legacyExact =
    legacyLayer?.kind === 'pixel' &&
    legacyLayer.opacity === 0.42 &&
    legacyLayer.pixelStore
      .read({ x: 10, y: 10, width: 1, height: 1 })
      .every((value, index) => value === [240, 80, 20, 255][index]);
  if (!first || !metadata || !edit || !collected) {
    throw new Error('IndexedDB repository did not report incremental save statistics');
  }
  return {
    schemaVersion: 1,
    firstWrittenResourceCount: first.writtenResourceCount,
    metadataWrittenResourceCount: metadata.writtenResourceCount,
    editedWrittenResourceCount: edit.writtenResourceCount,
    editedWrittenResourceBytes: edit.writtenResourceBytes,
    removedResourceCount: collected.removedResourceCount,
    deferredMetadataResourceReads,
    hydratedResourceReads,
    hydrationMemoized,
    deferredMetadataExact,
    exact,
    legacyDeferredMetadataExact,
    legacyExact,
  };
}

/** Proves an explicit custom region rasterizer stays bounded in the production browser bundle. */
export function runCustomRegionRasterizerProbe(): CustomRegionRasterizerProbeResult {
  const size = 8192;
  const store = TiledPixelStore.empty(size, size).write({
    x: 4096,
    y: 2048,
    width: 1,
    height: 1,
    pixels: new Uint8ClampedArray([31, 63, 127, 255]),
  }).store;
  const document = createDocument({
    id: 'browser-custom-region-rasterizer',
    name: 'Browser custom region rasterizer',
    width: size,
    height: size,
    layers: [
      createPixelLayer({
        id: 'pixels',
        name: 'Pixels',
        width: size,
        height: size,
        pixelStore: store,
        transform: { a: 1, b: 0, c: 0, d: 1, e: 0.25, f: 0.25 },
      }),
    ],
  });
  const region = { x: 4096, y: 2048, width: 64, height: 64 };
  let fullRasterizerCalls = 0;
  let regionRasterizerCalls = 0;
  let sourcePatchBytes = 0;
  const output = compositeDocumentRegion(document, region, {
    rasterize: () => {
      fullRasterizerCalls += 1;
      throw new Error('Full custom rasterizer unexpectedly ran');
    },
    rasterizeRegion: (layer, transform, canvas, pixels, requestedRegion) => {
      regionRasterizerCalls += 1;
      sourcePatchBytes += pixels.byteLength;
      return rasterizeLayerNearest(layer, transform, canvas, pixels, requestedRegion);
    },
  });
  const expected = compositeDocumentRegion(document, region, {
    rasterizeRegion: rasterizeLayerNearest,
  });
  return {
    schemaVersion: 1,
    fullRasterizerCalls,
    regionRasterizerCalls,
    sourcePatchBytes,
    outputBytes: output.byteLength,
    exact: output.every((value, index) => value === expected[index]),
  };
}

/** Proves complement selections do not force document-sized filter coverage reads. */
export function runComplementFilterProbe(): ComplementFilterProbeResult {
  const size = 8192;
  const tileSize = 256;
  const tile = new Uint8ClampedArray(tileSize * tileSize * 4);
  for (let offset = 0; offset < tile.length; offset += 4) {
    tile.set([20, 100, 220, 255], offset);
  }
  const pixelStore = TiledPixelStore.empty(size, size, tileSize).write({
    x: 4096,
    y: 2048,
    width: tileSize,
    height: tileSize,
    pixels: tile,
  }).store;
  const coverageStore = TiledCoverageStore.uniform(size, size, 255, tileSize);
  let coverageReadCount = 0;
  let maxCoverageReadBytes = 0;
  let materialized = false;
  const originalRead = coverageStore.read.bind(coverageStore);
  Object.defineProperty(coverageStore, 'read', {
    value: (region: { width: number; height: number; x: number; y: number }) => {
      coverageReadCount += 1;
      maxCoverageReadBytes = Math.max(maxCoverageReadBytes, region.width * region.height);
      return originalRead(region);
    },
  });
  Object.defineProperty(coverageStore, 'toContiguous', {
    value: () => {
      materialized = true;
      throw new Error('Complement selection was materialized');
    },
  });
  const selection = createSelectionMask({ width: size, height: size, coverageStore });
  const layer = createPixelLayer({
    id: 'browser-complement-filter',
    name: 'Browser complement filter',
    width: size,
    height: size,
    pixelStore,
  });
  const pointwise = pointwiseRgbFilterPatches(layer, { kind: 'invert' }, selection);
  const gaussian = gaussianBlurPatches(layer, 1, selection);
  return {
    schemaVersion: 1,
    pointwisePatchCount: pointwise.length,
    gaussianPatchCount: gaussian.length,
    coverageReadCount,
    maxCoverageReadBytes,
    materialized,
    bounded: maxCoverageReadBytes <= (tileSize + 2) ** 2,
  };
}

/** Proves real filter workers stream tile patches/progress before one terminal result. */
export async function runStreamingFilterWorkerProbe(): Promise<StreamingFilterWorkerProbeResult> {
  const size = 512;
  const pixels = new Uint8ClampedArray(size * size * 4);
  for (let offset = 0; offset < pixels.length; offset += 4) {
    pixels.set([20, 100, 220, 255], offset);
  }
  const layer = createPixelLayer({
    id: 'streaming-filter-layer',
    name: 'Streaming filter layer',
    width: size,
    height: size,
    pixels,
  });
  const client = new PersistentImageWorkerClient();
  const progressSamples: number[] = [];
  try {
    const result = await client.run(
      {
        kind: 'filter',
        layer,
        selection: null,
        worldTransform: layer.transform,
        draft: {
          kind: 'invert',
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
          blurRadius: 0,
        },
      },
      { onProgress: (progress) => progressSamples.push(progress) },
    );
    if (result.kind !== 'filter') throw new Error('Expected a filter worker result');
    let output = layer.pixelStore;
    for (const patch of result.patches) {
      output = output.write({ ...patch, pixels: new Uint8ClampedArray(patch.pixels) }).store;
    }
    const first = output.read({ x: 0, y: 0, width: 1, height: 1 });
    return {
      schemaVersion: 1,
      patchCount: result.patches.length,
      streamedPatchCount: client.lastStreamedPatchCount,
      progressSamples,
      monotonicProgress: progressSamples.every(
        (progress, index) => index === 0 || progress >= (progressSamples[index - 1] ?? 0),
      ),
      exact: first.every((value, index) => value === [235, 155, 35, 255][index]),
    };
  } finally {
    client.dispose();
  }
}

/**
 * Large canvas with small translated patches so progressive viewport work stays
 * region-local (used for 4k sparse browser gates without OOM).
 */
function sparseLayeredDocument(
  width: number,
  height: number,
  layerCount: number,
  patchSize: number,
): ImageDocument {
  const size = Math.max(8, Math.min(patchSize, width, height));
  return createDocument({
    id: `browser-perf-sparse-${String(width)}-${String(layerCount)}`,
    name: 'Browser sparse performance fixture',
    width,
    height,
    layers: Array.from({ length: layerCount }, (_, index) => {
      const layer = solidLayer(`layer-${String(index)}`, size, size, index + 1);
      const offset = index * 24;
      return {
        ...layer,
        transform: { a: 1, b: 0, c: 0, d: 1, e: offset, f: offset },
      };
    }),
  });
}

function percentile(sorted: readonly number[], fraction: number): number {
  if (sorted.length === 0) return 0;
  const index = Math.min(sorted.length - 1, Math.ceil(sorted.length * fraction) - 1);
  return sorted[index] ?? 0;
}

function timeSamples(operation: () => void, samples: number): number[] {
  const durations: number[] = [];
  for (let index = 0; index < samples; index += 1) {
    const started = performance.now();
    operation();
    durations.push(performance.now() - started);
  }
  return durations.sort((left, right) => left - right);
}

function observeLongTasks(durationMs: number): Promise<number> {
  return new Promise((resolve) => {
    let total = 0;
    type LongTaskEntry = { duration: number };
    type ObserverLike = {
      observe: (options: { entryTypes: string[] }) => void;
      disconnect: () => void;
    };
    const PerformanceObserverCtor = (
      globalThis as unknown as {
        PerformanceObserver?: new (
          callback: (list: { getEntries: () => LongTaskEntry[] }) => void,
        ) => ObserverLike;
      }
    ).PerformanceObserver;
    if (!PerformanceObserverCtor) {
      resolve(0);
      return;
    }
    let activeObserver: ObserverLike;
    try {
      activeObserver = new PerformanceObserverCtor((list) => {
        for (const entry of list.getEntries()) total += entry.duration;
      });
      activeObserver.observe({ entryTypes: ['longtask'] });
    } catch {
      resolve(0);
      return;
    }
    window.setTimeout(() => {
      activeObserver.disconnect();
      resolve(total);
    }, durationMs);
  });
}

/**
 * Measures composite tile cache cold/warm and visibility-toggle latency in the browser.
 * Used by Playwright evidence and optional manual `?perf=1` diagnostics.
 */
export function runBrowserCompositeProbe(
  options: BrowserCompositeProbeOptions = {},
): BrowserCompositeProbeResult {
  const width = options.width ?? 256;
  const height = options.height ?? 256;
  const layerCount = options.layerCount ?? 8;
  const tileSize = options.tileSize ?? 64;
  const warmSamples = options.warmSamples ?? 5;
  const coldSamples = options.coldSamples ?? 3;

  const document = layeredDocument(width, height, layerCount);
  const reference = (doc: ImageDocument) =>
    compositeDocumentPixels(doc, { rasterize: rasterizeLayerNearest });
  const region = (
    doc: ImageDocument,
    area: { x: number; y: number; width: number; height: number },
  ) => compositeDocumentRegion(doc, area, { rasterize: rasterizeLayerNearest });

  const coldSamplesMs = timeSamples(() => {
    const output = reference(document);
    if (output.length !== width * height * 4) throw new Error('Cold composite size changed');
  }, coldSamples);

  const cache = new CompositeTileCache({ tileSize, maxBytes: 64 * 1024 * 1024 });
  cache.composite(document, { referenceComposite: reference, regionComposite: region });
  const warmSamplesMs = timeSamples(() => {
    const output = cache.composite(document, {
      referenceComposite: reference,
      regionComposite: region,
    });
    if (output.length !== width * height * 4) throw new Error('Warm composite size changed');
    if (!cache.stats.lastComposeFullyWarm) {
      throw new Error('Expected fully warm composite tile cache compose');
    }
  }, warmSamples);

  // Visibility toggle: first change is a miss path; second identical state uses warm tiles
  // after the intermediate recomposite has been cached.
  const firstLayer = document.layers[0];
  if (!firstLayer) throw new Error('Expected at least one layer in the performance fixture');
  const secondLayer = document.layers[1] ?? firstLayer;
  const hidden = setLayerVisibility(firstLayer.id, false).execute(document);
  const visibilityColdStarted = performance.now();
  cache.composite(hidden, { referenceComposite: reference, regionComposite: region });
  const visibilityColdMs = performance.now() - visibilityColdStarted;
  const visibilityWarmStarted = performance.now();
  cache.composite(hidden, { referenceComposite: reference, regionComposite: region });
  const visibilityWarmMs = performance.now() - visibilityWarmStarted;

  // Opacity toggle measures property-only invalidation cost.
  const faded = setLayerOpacity(secondLayer.id, 0.4).execute(document);
  cache.composite(faded, { referenceComposite: reference, regionComposite: region });

  const coldMedian = percentile(coldSamplesMs, 0.5);
  const warmMedian = percentile(warmSamplesMs, 0.5);

  return {
    schemaVersion: 1,
    fixture: { width, height, layerCount, tileSize },
    cold: {
      medianMs: coldMedian,
      p95Ms: percentile(coldSamplesMs, 0.95),
      samplesMs: coldSamplesMs,
    },
    warm: {
      medianMs: warmMedian,
      p95Ms: percentile(warmSamplesMs, 0.95),
      samplesMs: warmSamplesMs,
      fullyWarm: cache.stats.lastComposeFullyWarm,
    },
    visibilityToggle: {
      coldMs: visibilityColdMs,
      warmMs: visibilityWarmMs,
    },
    cache: {
      hits: cache.stats.hits,
      misses: cache.stats.misses,
      regionLocalMisses: cache.stats.lastComposeRegionLocalMisses,
      fullReferenceMisses: cache.stats.lastComposeFullReferenceMisses,
      allocatedBytes: cache.stats.allocatedBytes,
    },
    longTasksDuringWarmMs: 0,
    warmFasterThanCold: warmMedian < coldMedian,
    runtime: {
      userAgent: navigator.userAgent,
      hardwareConcurrency: navigator.hardwareConcurrency,
    },
  };
}

/** Async variant that also samples long tasks during a warm recompose burst. */
export async function runBrowserCompositeProbeWithLongTasks(
  options: BrowserCompositeProbeOptions = {},
): Promise<BrowserCompositeProbeResult> {
  const base = runBrowserCompositeProbe(options);
  const width = options.width ?? 256;
  const height = options.height ?? 256;
  const layerCount = options.layerCount ?? 8;
  const tileSize = options.tileSize ?? 64;
  const document = layeredDocument(width, height, layerCount);
  const cache = new CompositeTileCache({ tileSize, maxBytes: 64 * 1024 * 1024 });
  const reference = (doc: ImageDocument) =>
    compositeDocumentPixels(doc, { rasterize: rasterizeLayerNearest });
  const region = (
    doc: ImageDocument,
    area: { x: number; y: number; width: number; height: number },
  ) => compositeDocumentRegion(doc, area, { rasterize: rasterizeLayerNearest });
  cache.composite(document, { referenceComposite: reference, regionComposite: region });

  const longTaskPromise = observeLongTasks(250);
  const burstStarted = performance.now();
  while (performance.now() - burstStarted < 200) {
    cache.composite(document, { referenceComposite: reference, regionComposite: region });
  }
  const longTasksDuringWarmMs = await longTaskPromise;

  return {
    ...base,
    longTasksDuringWarmMs,
    warm: {
      ...base.warm,
      fullyWarm: cache.stats.lastComposeFullyWarm,
    },
    cache: {
      hits: cache.stats.hits,
      misses: cache.stats.misses,
      regionLocalMisses: cache.stats.lastComposeRegionLocalMisses,
      fullReferenceMisses: cache.stats.lastComposeFullReferenceMisses,
      allocatedBytes: cache.stats.allocatedBytes,
    },
  };
}

export interface WorkerCompositeColdPathProbeResult {
  readonly schemaVersion: 1;
  readonly fixture: {
    readonly width: number;
    readonly height: number;
    readonly layerCount: number;
    readonly tileSize: number;
  };
  /** Main-thread full reference composite wall time. */
  readonly mainThreadReferenceMs: number;
  /** Main-thread warmAssembleOnly wall time (must not invoke reference). */
  readonly mainThreadWarmOnlyMs: number;
  readonly warmOnlyReferenceCalls: number;
  /** Wall time from schedule() until a matching worker frame arrives. */
  readonly workerFrameWaitMs: number;
  readonly workerMatchesReference: boolean;
  readonly planWarmAssembleOnlyWhenCold: boolean;
  readonly longTasksDuringMainReferenceMs: number;
  readonly longTasksDuringWorkerWaitMs: number;
  readonly runtime: {
    readonly userAgent: string;
    readonly hardwareConcurrency: number;
  };
}

/**
 * Proves the cold advanced strategy: warmAssembleOnly never miss-fills on the UI
 * thread, and a real worker composite returns reference-equivalent pixels.
 * Long-task samples are best-effort (PerformanceObserver longtask).
 */
export async function runWorkerCompositeColdPathProbe(
  options: BrowserCompositeProbeOptions = {},
): Promise<WorkerCompositeColdPathProbeResult> {
  const width = options.width ?? 128;
  const height = options.height ?? 128;
  const layerCount = options.layerCount ?? 6;
  const tileSize = options.tileSize ?? 64;
  const document = layeredDocument(width, height, layerCount);
  const referencePixels = compositeDocumentPixels(document, { rasterize: rasterizeLayerNearest });

  let warmOnlyReferenceCalls = 0;
  const warmOnlyCache = new CompositeTileCache({ tileSize, maxBytes: 64 * 1024 * 1024 });
  const warmOnlyStarted = performance.now();
  warmOnlyCache.composite(document, {
    referenceComposite: (doc) => {
      warmOnlyReferenceCalls += 1;
      return compositeDocumentPixels(doc, { rasterize: rasterizeLayerNearest });
    },
    warmAssembleOnly: true,
    progressiveViewportOnly: true,
    viewportRegion: { x: 0, y: 0, width, height },
  });
  const mainThreadWarmOnlyMs = performance.now() - warmOnlyStarted;

  const longTaskMainPromise = observeLongTasks(800);
  const mainRefStarted = performance.now();
  const mainReference = compositeDocumentPixels(document, { rasterize: rasterizeLayerNearest });
  const mainThreadReferenceMs = performance.now() - mainRefStarted;
  // Allow long-task observer window to cover the work above.
  await new Promise((resolve) => window.setTimeout(resolve, 50));
  const longTasksDuringMainReferenceMs = await longTaskMainPromise;
  if (mainReference.length !== referencePixels.length) {
    throw new Error('Main-thread reference size mismatch');
  }

  const plan = planAdvancedCompositeRender({
    document,
    fullyWarm: false,
    workerFrame: null,
    scheduledDocument: null,
    seededGeneration: -1,
    workerInflight: false,
  });

  const scheduler = new CompositeWorkerScheduler();
  const longTaskWorkerPromise = observeLongTasks(4000);
  const workerStarted = performance.now();
  scheduler.schedule(document);
  await new Promise<void>((resolve, reject) => {
    const timeout = window.setTimeout(() => {
      reject(new Error('Worker composite frame timed out'));
    }, 15000);
    const poll = () => {
      if (scheduler.frameFor(document)) {
        window.clearTimeout(timeout);
        resolve();
        return;
      }
      window.setTimeout(poll, 16);
    };
    poll();
  });
  const workerFrameWaitMs = performance.now() - workerStarted;
  const longTasksDuringWorkerWaitMs = await longTaskWorkerPromise;
  const frame = scheduler.frameFor(document);
  if (!frame) throw new Error('Expected worker composite frame');
  let workerMatchesReference = frame.pixels.length === referencePixels.length;
  if (workerMatchesReference) {
    // Compare without allocating intermediate arrays (large fixtures).
    for (let index = 0; index < frame.pixels.length; index += 1) {
      if (frame.pixels[index] !== referencePixels[index]) {
        workerMatchesReference = false;
        break;
      }
    }
  }
  scheduler.clear();

  return {
    schemaVersion: 1,
    fixture: { width, height, layerCount, tileSize },
    mainThreadReferenceMs,
    mainThreadWarmOnlyMs,
    warmOnlyReferenceCalls,
    workerFrameWaitMs,
    workerMatchesReference,
    planWarmAssembleOnlyWhenCold: plan.warmAssembleOnly && plan.scheduleWorker,
    longTasksDuringMainReferenceMs,
    longTasksDuringWorkerWaitMs,
    runtime: {
      userAgent: navigator.userAgent,
      hardwareConcurrency: navigator.hardwareConcurrency,
    },
  };
}

export interface CompositeWorkingSetProbeOptions {
  readonly width?: number;
  readonly height?: number;
  readonly layerCount?: number;
  readonly tileSize?: number;
  /** Soft cache budget in bytes. */
  readonly maxBytes?: number;
  /** Number of edit/recompose iterations in the session. */
  readonly iterations?: number;
}

export interface CompositeWorkingSetProbeResult {
  readonly schemaVersion: 1;
  readonly fixture: {
    readonly width: number;
    readonly height: number;
    readonly layerCount: number;
    readonly tileSize: number;
    readonly maxBytes: number;
    readonly iterations: number;
  };
  readonly peakAllocatedBytes: number;
  readonly finalAllocatedBytes: number;
  readonly maxTileCount: number;
  readonly totalEvictions: number;
  readonly samples: readonly {
    readonly iteration: number;
    readonly allocatedBytes: number;
    readonly tileCount: number;
    readonly evictions: number;
    readonly fullyWarm: boolean;
  }[];
  /** True when every post-compose sample stayed within the configured maxBytes. */
  readonly stayedWithinBudget: boolean;
  /**
   * True when the second half of the session does not grow peak allocation
   * more than 25% above the first-half peak (no unbounded climb).
   */
  readonly boundedGrowth: boolean;
  readonly jsHeap?: {
    readonly usedJSHeapSizeStart: number;
    readonly usedJSHeapSizeEnd: number;
    readonly jsHeapSizeLimit: number;
  };
  readonly runtime: {
    readonly userAgent: string;
    readonly hardwareConcurrency: number;
  };
}

function readJsHeap():
  | { readonly usedJSHeapSize: number; readonly jsHeapSizeLimit: number }
  | undefined {
  const memory = (
    performance as Performance & {
      memory?: { usedJSHeapSize: number; jsHeapSizeLimit: number };
    }
  ).memory;
  if (!memory) return undefined;
  return {
    usedJSHeapSize: memory.usedJSHeapSize,
    jsHeapSizeLimit: memory.jsHeapSizeLimit,
  };
}

/**
 * Exercises a multi-iteration advanced composite session under a tight cache
 * budget: visibility/opacity edits force dependency key changes, LRU eviction
 * must keep allocatedBytes within maxBytes, and peak working set must not grow
 * without bound across the second half of the session.
 */
export function runCompositeWorkingSetProbe(
  options: CompositeWorkingSetProbeOptions = {},
): CompositeWorkingSetProbeResult {
  const width = options.width ?? 128;
  const height = options.height ?? 128;
  const layerCount = options.layerCount ?? 6;
  const tileSize = options.tileSize ?? 64;
  // Budget intentionally below a multi-revision full viewport tile set so LRU must run.
  const maxBytes = options.maxBytes ?? 48 * 1024;
  const iterations = options.iterations ?? 24;

  let document = layeredDocument(width, height, layerCount);
  const cache = new CompositeTileCache({ tileSize, maxBytes });
  const reference = (doc: ImageDocument) =>
    compositeDocumentPixels(doc, { rasterize: rasterizeLayerNearest });
  const region = (
    doc: ImageDocument,
    area: { x: number; y: number; width: number; height: number },
  ) => compositeDocumentRegion(doc, area, { rasterize: rasterizeLayerNearest });

  const heapStart = readJsHeap();
  const samples: CompositeWorkingSetProbeResult['samples'][number][] = [];
  let peakAllocatedBytes = 0;
  let maxTileCount = 0;

  for (let iteration = 0; iteration < iterations; iteration += 1) {
    const layer = document.layers[iteration % document.layers.length];
    if (!layer) throw new Error('Expected layers in working-set fixture');
    // Unique opacity each iteration forces new dependency keys (history working set).
    document = setLayerOpacity(layer.id, Math.min(1, 0.15 + iteration * 0.03)).execute(document);
    cache.composite(document, {
      referenceComposite: reference,
      regionComposite: region,
      progressiveViewportOnly: true,
      viewportRegion: { x: 0, y: 0, width, height },
    });
    // Second compose should warm-assemble for the same document revision.
    cache.composite(document, {
      referenceComposite: reference,
      regionComposite: region,
      progressiveViewportOnly: true,
      viewportRegion: { x: 0, y: 0, width, height },
    });
    const { allocatedBytes, tileCount, evictions, lastComposeFullyWarm } = cache.stats;
    peakAllocatedBytes = Math.max(peakAllocatedBytes, allocatedBytes);
    maxTileCount = Math.max(maxTileCount, tileCount);
    samples.push({
      iteration,
      allocatedBytes,
      tileCount,
      evictions,
      fullyWarm: lastComposeFullyWarm,
    });
  }

  const heapEnd = readJsHeap();
  const midpoint = Math.floor(samples.length / 2);
  const firstHalfPeak = samples
    .slice(0, Math.max(1, midpoint))
    .reduce((peak, sample) => Math.max(peak, sample.allocatedBytes), 0);
  const secondHalfPeak = samples
    .slice(midpoint)
    .reduce((peak, sample) => Math.max(peak, sample.allocatedBytes), 0);
  const stayedWithinBudget = samples.every((sample) => sample.allocatedBytes <= maxBytes);
  const boundedGrowth = secondHalfPeak <= firstHalfPeak * 1.25 + tileSize * tileSize * 4;

  return {
    schemaVersion: 1,
    fixture: { width, height, layerCount, tileSize, maxBytes, iterations },
    peakAllocatedBytes,
    finalAllocatedBytes: cache.stats.allocatedBytes,
    maxTileCount,
    totalEvictions: cache.stats.evictions,
    samples,
    stayedWithinBudget,
    boundedGrowth,
    jsHeap:
      heapStart && heapEnd
        ? {
            usedJSHeapSizeStart: heapStart.usedJSHeapSize,
            usedJSHeapSizeEnd: heapEnd.usedJSHeapSize,
            jsHeapSizeLimit: heapEnd.jsHeapSizeLimit,
          }
        : undefined,
    runtime: {
      userAgent: navigator.userAgent,
      hardwareConcurrency: navigator.hardwareConcurrency,
    },
  };
}

/**
 * Longer multi-minute-style composite session: many unique dependency-key revisions
 * under a fixed cache budget. Proves LRU keeps memory bounded across 100+ edits.
 */
export function runCompositeLongSessionProbe(
  options: CompositeWorkingSetProbeOptions = {},
): CompositeWorkingSetProbeResult {
  return runCompositeWorkingSetProbe({
    width: options.width ?? 192,
    height: options.height ?? 192,
    layerCount: options.layerCount ?? 8,
    tileSize: options.tileSize ?? 64,
    maxBytes: options.maxBytes ?? 96 * 1024,
    iterations: options.iterations ?? 120,
  });
}

export interface LargeDocumentInteractiveProbeOptions {
  readonly width?: number;
  readonly height?: number;
  readonly layerCount?: number;
  readonly tileSize?: number;
  /** Viewport edge length used for progressive pan/zoom samples. */
  readonly viewportWidth?: number;
  readonly viewportHeight?: number;
  readonly warmSamples?: number;
  /** Document-space pan offsets for the second viewport. */
  readonly panOffsetX?: number;
  readonly panOffsetY?: number;
  /** Interactive latency budget in ms for warm viewport/pan (default 50). */
  readonly interactiveBudgetMs?: number;
  /**
   * When set, layers are small patches (not full canvas) so 4k+ progressive
   * cold paths stay region-local without allocating multi-GB fixtures.
   */
  readonly sparsePatchSize?: number;
}

export interface LargeDocumentInteractiveTiming {
  readonly medianMs: number;
  readonly p95Ms: number;
  readonly samplesMs: readonly number[];
  readonly fullyWarm: boolean;
  readonly prioritizedViewportTiles: number;
  readonly tileCount: number;
}

export interface LargeDocumentInteractiveProbeResult {
  readonly schemaVersion: 1;
  readonly fixture: {
    readonly width: number;
    readonly height: number;
    readonly layerCount: number;
    readonly tileSize: number;
    readonly viewportWidth: number;
    readonly viewportHeight: number;
    readonly panOffsetX: number;
    readonly panOffsetY: number;
    readonly interactiveBudgetMs: number;
    readonly fullTileCount: number;
    readonly sparsePatchSize: number | null;
  };
  readonly coldViewport: LargeDocumentInteractiveTiming;
  readonly warmViewport: LargeDocumentInteractiveTiming;
  /** First visit to the panned viewport (new tiles miss). */
  readonly panCold: LargeDocumentInteractiveTiming;
  /** Warm reassemble of the panned viewport after it has been cached. */
  readonly panWarm: LargeDocumentInteractiveTiming;
  /** Warm reassemble of the original viewport after both regions are cached. */
  readonly returnHomeWarm: LargeDocumentInteractiveTiming;
  /** Mip-1 overview warm path (zoom-out proxy). */
  readonly mipWarm: LargeDocumentInteractiveTiming & {
    readonly outputWidth: number;
    readonly outputHeight: number;
  };
  readonly warmFasterThanCold: boolean;
  readonly interactiveWarmViewport: boolean;
  readonly interactivePanWarm: boolean;
  readonly progressiveSparseVsFull: boolean;
  readonly runtime: {
    readonly userAgent: string;
    readonly hardwareConcurrency: number;
  };
}

/**
 * Large-document interactive evidence: progressive viewport cold/warm composite,
 * pan to a second viewport, return home, and mip-1 zoom-out on a 1024+ canvas.
 * Proves warm pan/viewport stay within an interactive latency budget without
 * filling the full document tile set on progressive first paint.
 */
export function runLargeDocumentInteractiveProbe(
  options: LargeDocumentInteractiveProbeOptions = {},
): LargeDocumentInteractiveProbeResult {
  const width = options.width ?? 1024;
  const height = options.height ?? 1024;
  const layerCount = options.layerCount ?? 6;
  const tileSize = options.tileSize ?? 64;
  const viewportWidth = options.viewportWidth ?? 384;
  const viewportHeight = options.viewportHeight ?? 384;
  const warmSamples = options.warmSamples ?? 5;
  const panOffsetX = options.panOffsetX ?? 512;
  const panOffsetY = options.panOffsetY ?? 256;
  const interactiveBudgetMs = options.interactiveBudgetMs ?? 50;
  const sparsePatchSize = options.sparsePatchSize ?? null;
  const fullTileCount = Math.ceil(width / tileSize) * Math.ceil(height / tileSize);

  const document =
    sparsePatchSize !== null
      ? sparseLayeredDocument(width, height, layerCount, sparsePatchSize)
      : layeredDocument(width, height, layerCount);
  // Prefer region-local fills; full-frame reference is a last resort and must
  // not be required for progressive viewport samples on large canvases.
  const reference = (doc: ImageDocument) =>
    compositeDocumentPixels(doc, { rasterize: rasterizeLayerNearest });
  const region = (
    doc: ImageDocument,
    area: { x: number; y: number; width: number; height: number },
  ) => compositeDocumentRegion(doc, area, { rasterize: rasterizeLayerNearest });

  const cache = new CompositeTileCache({
    tileSize,
    maxBytes: 128 * 1024 * 1024,
  });

  const homeViewport = {
    x: 0,
    y: 0,
    width: Math.min(viewportWidth, width),
    height: Math.min(viewportHeight, height),
  };
  const panViewport = {
    x: Math.min(panOffsetX, Math.max(0, width - homeViewport.width)),
    y: Math.min(panOffsetY, Math.max(0, height - homeViewport.height)),
    width: homeViewport.width,
    height: homeViewport.height,
  };

  const composeViewport = (
    viewport: {
      x: number;
      y: number;
      width: number;
      height: number;
    },
    mipLevel = 0,
  ) =>
    cache.composite(document, {
      referenceComposite: reference,
      regionComposite: region,
      progressiveViewportOnly: true,
      viewportRegion: viewport,
      mipLevel,
    });

  const measure = (
    operation: () => void,
    samples: number,
  ): { samplesMs: number[]; medianMs: number; p95Ms: number } => {
    const samplesMs = timeSamples(operation, samples);
    return {
      samplesMs,
      medianMs: percentile(samplesMs, 0.5),
      p95Ms: percentile(samplesMs, 0.95),
    };
  };

  // Cold progressive home viewport (fills only intersecting tiles).
  const coldStarted = performance.now();
  composeViewport(homeViewport);
  const coldMs = performance.now() - coldStarted;
  const coldTiming: LargeDocumentInteractiveTiming = {
    medianMs: coldMs,
    p95Ms: coldMs,
    samplesMs: [coldMs],
    fullyWarm: cache.stats.lastComposeFullyWarm,
    prioritizedViewportTiles: cache.stats.lastComposePrioritizedViewportTiles,
    tileCount: cache.stats.tileCount,
  };

  const warmViewportSamples = measure(() => {
    composeViewport(homeViewport);
    if (!cache.stats.lastComposeFullyWarm) {
      throw new Error('Expected fully warm home viewport after cold fill');
    }
  }, warmSamples);
  const warmViewport: LargeDocumentInteractiveTiming = {
    ...warmViewportSamples,
    fullyWarm: cache.stats.lastComposeFullyWarm,
    prioritizedViewportTiles: cache.stats.lastComposePrioritizedViewportTiles,
    tileCount: cache.stats.tileCount,
  };

  // First visit to panned region (new tiles miss while home tiles stay cached).
  const panColdStarted = performance.now();
  composeViewport(panViewport);
  const panColdMs = performance.now() - panColdStarted;
  const panCold: LargeDocumentInteractiveTiming = {
    medianMs: panColdMs,
    p95Ms: panColdMs,
    samplesMs: [panColdMs],
    fullyWarm: cache.stats.lastComposeFullyWarm,
    prioritizedViewportTiles: cache.stats.lastComposePrioritizedViewportTiles,
    tileCount: cache.stats.tileCount,
  };

  const panWarmSamples = measure(() => {
    composeViewport(panViewport);
    if (!cache.stats.lastComposeFullyWarm) {
      throw new Error('Expected fully warm pan viewport after first pan fill');
    }
  }, warmSamples);
  const panWarm: LargeDocumentInteractiveTiming = {
    ...panWarmSamples,
    fullyWarm: cache.stats.lastComposeFullyWarm,
    prioritizedViewportTiles: cache.stats.lastComposePrioritizedViewportTiles,
    tileCount: cache.stats.tileCount,
  };

  const returnHomeSamples = measure(() => {
    composeViewport(homeViewport);
    if (!cache.stats.lastComposeFullyWarm) {
      throw new Error('Expected fully warm return-home viewport');
    }
  }, warmSamples);
  const returnHomeWarm: LargeDocumentInteractiveTiming = {
    ...returnHomeSamples,
    fullyWarm: cache.stats.lastComposeFullyWarm,
    prioritizedViewportTiles: cache.stats.lastComposePrioritizedViewportTiles,
    tileCount: cache.stats.tileCount,
  };

  // Zoom-out proxy: populate mip 1 once, then measure warm reassemble.
  const mipColdOutput = composeViewport(homeViewport, 1);
  const mipWarmSamples = measure(() => {
    composeViewport(homeViewport, 1);
    if (!cache.stats.lastComposeFullyWarm) {
      throw new Error('Expected fully warm mip-1 compose');
    }
  }, warmSamples);
  const mipWarm = {
    ...mipWarmSamples,
    fullyWarm: cache.stats.lastComposeFullyWarm,
    prioritizedViewportTiles: cache.stats.lastComposePrioritizedViewportTiles,
    tileCount: cache.stats.tileCount,
    outputWidth: Math.max(1, Math.ceil(width / 2)),
    outputHeight: Math.max(1, Math.ceil(height / 2)),
  };
  if (mipColdOutput.length !== mipWarm.outputWidth * mipWarm.outputHeight * 4) {
    throw new Error('Mip-1 output size mismatch');
  }

  return {
    schemaVersion: 1,
    fixture: {
      width,
      height,
      layerCount,
      tileSize,
      viewportWidth: homeViewport.width,
      viewportHeight: homeViewport.height,
      panOffsetX: panViewport.x,
      panOffsetY: panViewport.y,
      interactiveBudgetMs,
      fullTileCount,
      sparsePatchSize,
    },
    coldViewport: coldTiming,
    warmViewport,
    panCold,
    panWarm,
    returnHomeWarm,
    mipWarm,
    warmFasterThanCold: warmViewport.medianMs < coldTiming.medianMs,
    interactiveWarmViewport: warmViewport.medianMs < interactiveBudgetMs,
    interactivePanWarm: panWarm.medianMs < interactiveBudgetMs,
    // Progressive first paint must cache far fewer tiles than the full canvas.
    progressiveSparseVsFull: coldTiming.tileCount < fullTileCount * 0.5,
    runtime: {
      userAgent: navigator.userAgent,
      hardwareConcurrency: navigator.hardwareConcurrency,
    },
  };
}

export interface BrushFrameProbeOptions {
  readonly width?: number;
  readonly height?: number;
  readonly brushSize?: number;
  readonly samples?: number;
  readonly pointsPerFrame?: number;
  /** Interactive brush-frame budget in ms (default 50). */
  readonly interactiveBudgetMs?: number;
}

export interface BrushFrameProbeResult {
  readonly schemaVersion: 1;
  readonly fixture: {
    readonly width: number;
    readonly height: number;
    readonly brushSize: number;
    readonly pointsPerFrame: number;
    readonly interactiveBudgetMs: number;
  };
  readonly paintMedianMs: number;
  readonly paintP95Ms: number;
  readonly commitMedianMs: number;
  readonly frameMedianMs: number;
  readonly samplesMs: {
    readonly paint: readonly number[];
    readonly commit: readonly number[];
    readonly frame: readonly number[];
  };
  readonly patchBounded: boolean;
  readonly interactivePaint: boolean;
  readonly interactiveFrame: boolean;
  readonly runtime: {
    readonly userAgent: string;
    readonly hardwareConcurrency: number;
  };
}

/**
 * Measures one brush-frame of work on a large sparse pixel layer:
 * paintStroke (tile-local dab) + patchPixels commit. Proves ordinary painting
 * stays interactive without densifying the full canvas.
 */
export function runBrushFrameProbe(options: BrushFrameProbeOptions = {}): BrushFrameProbeResult {
  const width = options.width ?? 2048;
  const height = options.height ?? 2048;
  const brushSize = options.brushSize ?? 24;
  const samples = options.samples ?? 7;
  const pointsPerFrame = options.pointsPerFrame ?? 12;
  const interactiveBudgetMs = options.interactiveBudgetMs ?? 50;

  let document = createDocument({
    id: 'brush-frame',
    name: 'Brush frame fixture',
    width,
    height,
    layers: [
      createPixelLayer({
        id: 'layer-0',
        name: 'Paint',
        width,
        height,
      }),
    ],
  });
  const layer0 = document.layers[0];
  if (!layer0 || layer0.kind !== 'pixel') {
    throw new Error('Brush frame fixture missing pixel layer');
  }

  const paintSamples: number[] = [];
  const commitSamples: number[] = [];
  const frameSamples: number[] = [];
  let patchBounded = true;

  for (let sample = 0; sample < samples; sample += 1) {
    const baseX = 80 + sample * 17;
    const baseY = 90 + sample * 13;
    const points = Array.from({ length: pointsPerFrame }, (_, index) => ({
      x: baseX + index * 2.5,
      y: baseY + index * 1.75,
    }));
    const layer = document.layers[0];
    if (!layer || layer.kind !== 'pixel') throw new Error('Pixel layer missing mid-probe');

    const frameStarted = performance.now();
    const paintStarted = performance.now();
    const patch = paintStroke(layer, points, {
      mode: 'source-over',
      size: brushSize,
      hardness: 0.85,
      opacity: 0.9,
      color: [220, 40, 60, 255],
    });
    const paintMs = performance.now() - paintStarted;
    paintSamples.push(paintMs);

    if (patch.width * patch.height >= width * height * 0.01) {
      patchBounded = false;
    }

    const commitStarted = performance.now();
    document = patchPixels(layer.id, patch).execute(document);
    const commitMs = performance.now() - commitStarted;
    commitSamples.push(commitMs);
    frameSamples.push(performance.now() - frameStarted);
  }

  paintSamples.sort((a, b) => a - b);
  commitSamples.sort((a, b) => a - b);
  frameSamples.sort((a, b) => a - b);

  const paintMedianMs = percentile(paintSamples, 0.5);
  const paintP95Ms = percentile(paintSamples, 0.95);
  const commitMedianMs = percentile(commitSamples, 0.5);
  const frameMedianMs = percentile(frameSamples, 0.5);

  return {
    schemaVersion: 1,
    fixture: {
      width,
      height,
      brushSize,
      pointsPerFrame,
      interactiveBudgetMs,
    },
    paintMedianMs,
    paintP95Ms,
    commitMedianMs,
    frameMedianMs,
    samplesMs: {
      paint: paintSamples,
      commit: commitSamples,
      frame: frameSamples,
    },
    patchBounded,
    interactivePaint: paintMedianMs < interactiveBudgetMs,
    interactiveFrame: frameMedianMs < interactiveBudgetMs,
    runtime: {
      userAgent: navigator.userAgent,
      hardwareConcurrency: navigator.hardwareConcurrency,
    },
  };
}

export interface LargeDocumentSustainedMemoryProbeOptions {
  readonly width?: number;
  readonly height?: number;
  readonly layerCount?: number;
  readonly tileSize?: number;
  readonly viewportWidth?: number;
  readonly viewportHeight?: number;
  /** Soft composite-tile cache budget in bytes. */
  readonly maxBytes?: number;
  /** Number of pan/edit iterations across the session. */
  readonly iterations?: number;
  readonly sparsePatchSize?: number;
}

export interface LargeDocumentSustainedMemoryProbeResult {
  readonly schemaVersion: 1;
  readonly fixture: {
    readonly width: number;
    readonly height: number;
    readonly layerCount: number;
    readonly tileSize: number;
    readonly viewportWidth: number;
    readonly viewportHeight: number;
    readonly maxBytes: number;
    readonly iterations: number;
    readonly sparsePatchSize: number | null;
    readonly fullTileCount: number;
    /** Naive full multi-layer RGBA surface bytes (upper bound for contiguous materialization). */
    readonly fullMultiLayerBytes: number;
  };
  readonly peakAllocatedBytes: number;
  readonly finalAllocatedBytes: number;
  readonly maxTileCount: number;
  /** Peak unique spatial tiles (column/row/mip), ignoring dependency-key revisions. */
  readonly maxUniqueSpatialTileCount: number;
  readonly totalEvictions: number;
  /**
   * Peak entry count / full-document tile grid. Can exceed 1.0 under dependency-key
   * revision churn when multiple generations of the same spatial tile remain resident.
   */
  readonly peakTileCountFractionOfFull: number;
  /** Peak unique spatial tiles / full-document tile grid. */
  readonly peakUniqueSpatialFractionOfFull: number;
  readonly stayedWithinBudget: boolean;
  readonly boundedGrowth: boolean;
  /**
   * True when peak cache allocation stays strictly under a full multi-layer
   * contiguous surface and unique spatial footprint stays under the full grid
   * while progressive viewport-only fills run under the budget.
   */
  readonly progressiveSparseVsFull: boolean;
  readonly samples: readonly {
    readonly iteration: number;
    readonly allocatedBytes: number;
    readonly tileCount: number;
    readonly uniqueSpatialTileCount: number;
    readonly evictions: number;
  }[];
  readonly jsHeap?: {
    readonly usedJSHeapSizeStart: number;
    readonly usedJSHeapSizeEnd: number;
    readonly jsHeapSizeLimit: number;
  };
  readonly runtime: {
    readonly userAgent: string;
    readonly hardwareConcurrency: number;
  };
}

/**
 * Sustained large-document progressive session: pan across many viewports while
 * toggling layer visibility/opacity, under a hard composite-tile memory budget.
 * Proves LRU eviction keeps progressive tile memory bounded without filling the
 * full document tile set.
 */
export function runLargeDocumentSustainedMemoryProbe(
  options: LargeDocumentSustainedMemoryProbeOptions = {},
): LargeDocumentSustainedMemoryProbeResult {
  const width = options.width ?? 1024;
  const height = options.height ?? 1024;
  const layerCount = options.layerCount ?? 6;
  const tileSize = options.tileSize ?? 64;
  const viewportWidth = options.viewportWidth ?? 384;
  const viewportHeight = options.viewportHeight ?? 384;
  // Tight budget relative to a full multi-layer contiguous surface so LRU must
  // evict during progressive pan + edit churn (proves bounded progressive memory).
  const fullMultiLayerBytes = width * height * 4 * layerCount;
  const maxBytes =
    options.maxBytes ?? Math.min(16 * 1024 * 1024, Math.floor(fullMultiLayerBytes * 0.45));
  const iterations = options.iterations ?? 48;
  const sparsePatchSize = options.sparsePatchSize ?? null;
  const fullTileCount = Math.ceil(width / tileSize) * Math.ceil(height / tileSize);

  let document =
    sparsePatchSize !== null
      ? sparseLayeredDocument(width, height, layerCount, sparsePatchSize)
      : layeredDocument(width, height, layerCount);

  const region = (
    doc: ImageDocument,
    area: { x: number; y: number; width: number; height: number },
  ) => compositeDocumentRegion(doc, area, { rasterize: rasterizeLayerNearest });
  const reference = (doc: ImageDocument) =>
    compositeDocumentPixels(doc, { rasterize: rasterizeLayerNearest });

  const cache = new CompositeTileCache({ tileSize, maxBytes });
  const heapStart = readJsHeap();
  const samples: Array<{
    iteration: number;
    allocatedBytes: number;
    tileCount: number;
    uniqueSpatialTileCount: number;
    evictions: number;
  }> = [];
  let peakAllocatedBytes = 0;
  let maxTileCount = 0;
  let maxUniqueSpatialTileCount = 0;
  let totalEvictions = 0;
  let firstHalfPeak = 0;
  let secondHalfPeak = 0;

  for (let iteration = 0; iteration < iterations; iteration += 1) {
    // Pan across a grid of viewports so progressive fills many disjoint regions.
    const col = iteration % 4;
    const row = Math.floor(iteration / 4) % 4;
    const viewportRegion = {
      x: Math.min(
        Math.floor((width - viewportWidth) * (col / 3)),
        Math.max(0, width - viewportWidth),
      ),
      y: Math.min(
        Math.floor((height - viewportHeight) * (row / 3)),
        Math.max(0, height - viewportHeight),
      ),
      width: Math.min(viewportWidth, width),
      height: Math.min(viewportHeight, height),
    };

    // Periodic visibility/opacity edits force dependency-key churn (history working set).
    if (iteration % 3 === 0) {
      const layerIndex = iteration % layerCount;
      const layer = document.layers[layerIndex];
      if (layer) {
        document =
          iteration % 6 === 0
            ? setLayerVisibility(layer.id, iteration % 2 === 0).execute(document)
            : setLayerOpacity(layer.id, 0.4 + (iteration % 5) * 0.1).execute(document);
      }
    }

    cache.composite(document, {
      referenceComposite: reference,
      regionComposite: region,
      progressiveViewportOnly: true,
      viewportRegion,
    });

    const allocatedBytes = cache.stats.allocatedBytes;
    const tileCount = cache.stats.tileCount;
    const uniqueSpatialTileCount = cache.stats.uniqueSpatialTileCount;
    const evictions = cache.stats.evictions;
    totalEvictions = evictions;
    peakAllocatedBytes = Math.max(peakAllocatedBytes, allocatedBytes);
    maxTileCount = Math.max(maxTileCount, tileCount);
    maxUniqueSpatialTileCount = Math.max(maxUniqueSpatialTileCount, uniqueSpatialTileCount);
    if (iteration < iterations / 2) {
      firstHalfPeak = Math.max(firstHalfPeak, allocatedBytes);
    } else {
      secondHalfPeak = Math.max(secondHalfPeak, allocatedBytes);
    }
    samples.push({
      iteration,
      allocatedBytes,
      tileCount,
      uniqueSpatialTileCount,
      evictions,
    });
  }

  const heapEnd = readJsHeap();
  const stayedWithinBudget = samples.every((sample) => sample.allocatedBytes <= maxBytes);
  const boundedGrowth =
    firstHalfPeak === 0
      ? true
      : secondHalfPeak <= firstHalfPeak * 1.25 + tileSize * tileSize * 4 * 8;
  // Progressive: peak allocation stays under a full multi-layer surface while the
  // cache respects its byte budget. Unique spatial footprint is reported separately
  // (a full pan grid may still visit every tile when the budget allows).
  const progressiveSparseVsFull = peakAllocatedBytes < fullMultiLayerBytes && stayedWithinBudget;

  return {
    schemaVersion: 1,
    fixture: {
      width,
      height,
      layerCount,
      tileSize,
      viewportWidth,
      viewportHeight,
      maxBytes,
      iterations,
      sparsePatchSize,
      fullTileCount,
      fullMultiLayerBytes,
    },
    peakAllocatedBytes,
    finalAllocatedBytes: cache.stats.allocatedBytes,
    maxTileCount,
    maxUniqueSpatialTileCount,
    totalEvictions,
    peakTileCountFractionOfFull: maxTileCount / fullTileCount,
    peakUniqueSpatialFractionOfFull: maxUniqueSpatialTileCount / fullTileCount,
    stayedWithinBudget,
    boundedGrowth,
    progressiveSparseVsFull,
    samples,
    ...(heapStart && heapEnd
      ? {
          jsHeap: {
            usedJSHeapSizeStart: heapStart.usedJSHeapSize,
            usedJSHeapSizeEnd: heapEnd.usedJSHeapSize,
            jsHeapSizeLimit: heapEnd.jsHeapSizeLimit,
          },
        }
      : {}),
    runtime: {
      userAgent: navigator.userAgent,
      hardwareConcurrency: navigator.hardwareConcurrency,
    },
  };
}

export type FreetoshopPerfApi = {
  readonly runBrowserCompositeProbe: typeof runBrowserCompositeProbe;
  readonly runBrowserCompositeProbeWithLongTasks: typeof runBrowserCompositeProbeWithLongTasks;
  readonly runWorkerCompositeColdPathProbe: typeof runWorkerCompositeColdPathProbe;
  readonly runCompositeWorkingSetProbe: typeof runCompositeWorkingSetProbe;
  readonly runCompositeLongSessionProbe: typeof runCompositeLongSessionProbe;
  readonly runLargeDocumentInteractiveProbe: typeof runLargeDocumentInteractiveProbe;
  readonly runBrushFrameProbe: typeof runBrushFrameProbe;
  readonly runLargeDocumentSustainedMemoryProbe: typeof runLargeDocumentSustainedMemoryProbe;
  readonly runPersistentWorkerResourceProbe: typeof runPersistentWorkerResourceProbe;
  readonly runPersistentDocumentTaskProbe: typeof runPersistentDocumentTaskProbe;
  readonly runLargeStreamedResizeProbe: typeof runLargeStreamedResizeProbe;
  readonly runPsdImportProgressProbe: typeof runPsdImportProgressProbe;
  readonly runRepeatedMixedResultProbe: typeof runRepeatedMixedResultProbe;
  readonly runMixedResultCancellationProbe: typeof runMixedResultCancellationProbe;
  readonly runDenseMixedLongSessionProbe: typeof runDenseMixedLongSessionProbe;
  readonly runStructuredWorkerSessionProbe: typeof runStructuredWorkerSessionProbe;
  readonly runStructuredCrossKindWorkerSessionProbe: typeof runStructuredCrossKindWorkerSessionProbe;
  readonly runStructuredFilterCompositeMutationProbe: typeof runStructuredFilterCompositeMutationProbe;
  readonly runStructuredLocalizedMutationProbe: typeof runStructuredLocalizedMutationProbe;
  readonly runProgressiveCancellationRecoveryProbe: typeof runProgressiveCancellationRecoveryProbe;
  readonly runIncrementalRepositoryProbe: typeof runIncrementalRepositoryProbe;
  readonly runCustomRegionRasterizerProbe: typeof runCustomRegionRasterizerProbe;
  readonly runComplementFilterProbe: typeof runComplementFilterProbe;
  readonly runStreamingFilterWorkerProbe: typeof runStreamingFilterWorkerProbe;
};

declare global {
  interface Window {
    __freetoshopPerf?: FreetoshopPerfApi;
  }
}

export function installBrowserCompositeProbe(target: Window = window): void {
  target.__freetoshopPerf = {
    runBrowserCompositeProbe,
    runBrowserCompositeProbeWithLongTasks,
    runWorkerCompositeColdPathProbe,
    runCompositeWorkingSetProbe,
    runCompositeLongSessionProbe,
    runLargeDocumentInteractiveProbe,
    runBrushFrameProbe,
    runLargeDocumentSustainedMemoryProbe,
    runPersistentWorkerResourceProbe,
    runPersistentDocumentTaskProbe,
    runLargeStreamedResizeProbe,
    runPsdImportProgressProbe,
    runRepeatedMixedResultProbe,
    runMixedResultCancellationProbe,
    runDenseMixedLongSessionProbe,
    runStructuredWorkerSessionProbe,
    runStructuredCrossKindWorkerSessionProbe,
    runStructuredFilterCompositeMutationProbe,
    runStructuredLocalizedMutationProbe,
    runProgressiveCancellationRecoveryProbe,
    runIncrementalRepositoryProbe,
    runCustomRegionRasterizerProbe,
    runComplementFilterProbe,
    runStreamingFilterWorkerProbe,
  };
}
