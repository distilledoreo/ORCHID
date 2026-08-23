import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import {
  createDocument,
  createPixelLayer,
  type ImageDocument,
  type PixelLayer,
} from '@freetoshop/core-document';
import { paintStroke } from '@freetoshop/raster-engine';
import { CompositeTileCache, compositeDocumentPixels } from '@freetoshop/renderer';
import { TiledCoverageStore, TiledPixelStore } from '@freetoshop/pixel-storage';

interface TimingBudget {
  readonly maxMedianMs: number;
  readonly maxP95Ms: number;
}

interface Baselines {
  readonly schemaVersion: 1;
  readonly profile: string;
  readonly scenarios: {
    readonly 'composite-512-12': TimingBudget;
    readonly 'brush-2048-diagonal': TimingBudget;
    readonly 'tiled-sparse-32768-patch': TimingBudget & { readonly maxAllocatedBytes: number };
    readonly 'coverage-sparse-32768-patch': TimingBudget & { readonly maxAllocatedBytes: number };
    readonly 'document-1024-8-memory': { readonly maxArrayBufferAmplification: number };
    readonly 'composite-tile-cache-warm-128-4'?: TimingBudget;
    readonly 'composite-tile-cache-warm-512-8'?: TimingBudget;
  };
}

interface TimingResult {
  readonly medianMs: number;
  readonly p95Ms: number;
  readonly samplesMs: readonly number[];
}

const baselinePath = fileURLToPath(new URL('../baselines.json', import.meta.url));
const baselines = JSON.parse(await readFile(baselinePath, 'utf8')) as Baselines;

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
    opacity: 0.8,
    blendMode: seed % 3 === 0 ? 'multiply' : seed % 3 === 1 ? 'screen' : 'normal',
  });
}

function layeredDocument(width: number, height: number, layerCount: number): ImageDocument {
  return createDocument({
    id: `benchmark-${String(width)}-${String(layerCount)}`,
    name: 'Benchmark document',
    width,
    height,
    layers: Array.from({ length: layerCount }, (_, index) =>
      solidLayer(`layer-${String(index)}`, width, height, index + 1),
    ),
  });
}

function percentile(sorted: readonly number[], fraction: number): number {
  const index = Math.min(sorted.length - 1, Math.ceil(sorted.length * fraction) - 1);
  return sorted[index] ?? 0;
}

function benchmark(operation: () => void, warmups = 1, samples = 5): TimingResult {
  for (let index = 0; index < warmups; index += 1) operation();
  const durations: number[] = [];
  for (let index = 0; index < samples; index += 1) {
    const started = performance.now();
    operation();
    durations.push(performance.now() - started);
  }
  durations.sort((left, right) => left - right);
  return {
    medianMs: percentile(durations, 0.5),
    p95Ms: percentile(durations, 0.95),
    samplesMs: durations,
  };
}

function assertTiming(name: string, result: TimingResult, budget: TimingBudget): void {
  if (result.medianMs > budget.maxMedianMs || result.p95Ms > budget.maxP95Ms) {
    throw new Error(
      `${name} exceeded its baseline: median ${result.medianMs.toFixed(1)} ms, p95 ${result.p95Ms.toFixed(1)} ms`,
    );
  }
}

async function settleMemoryAccounting(): Promise<void> {
  globalThis.gc?.();
  await new Promise<void>((resolve) => setImmediate(resolve));
  globalThis.gc?.();
  await new Promise<void>((resolve) => setImmediate(resolve));
  globalThis.gc?.();
}

const compositeDocument = layeredDocument(512, 512, 12);
const composite = benchmark(() => {
  const output = compositeDocumentPixels(compositeDocument);
  if (output.length !== 512 * 512 * 4) throw new Error('Composite output size changed');
});
assertTiming('composite-512-12', composite, baselines.scenarios['composite-512-12']);

// Dependency-keyed composite tile cache: warm re-composites must beat cold reference
// recomputes and remain fully warm (zero reference calls after the first populate).
const tileCacheDocument = layeredDocument(128, 128, 4);
const tileCache = new CompositeTileCache({ tileSize: 32, maxBytes: 16 * 1024 * 1024 });
const tileCacheReference = (document: ImageDocument) => compositeDocumentPixels(document);
tileCache.composite(tileCacheDocument, { referenceComposite: tileCacheReference });
const compositeWarm = benchmark(() => {
  const output = tileCache.composite(tileCacheDocument, {
    referenceComposite: tileCacheReference,
  });
  if (output.length !== 128 * 128 * 4) throw new Error('Warm composite tile cache size changed');
  if (!tileCache.stats.lastComposeFullyWarm) {
    throw new Error('Warm composite tile cache expected a fully warm compose');
  }
});
const compositeCold = benchmark(() => {
  const output = compositeDocumentPixels(tileCacheDocument);
  if (output.length !== 128 * 128 * 4) throw new Error('Cold composite size changed');
});
if (compositeWarm.medianMs >= compositeCold.medianMs) {
  throw new Error(
    `composite-tile-cache-warm-128-4 was not faster than cold reference: warm ${compositeWarm.medianMs.toFixed(2)} ms vs cold ${compositeCold.medianMs.toFixed(2)} ms`,
  );
}
const warmBudget = baselines.scenarios['composite-tile-cache-warm-128-4'];
if (warmBudget) assertTiming('composite-tile-cache-warm-128-4', compositeWarm, warmBudget);

// Larger warm-cache fixture representative of ordinary layered documents.
const largeCacheDocument = layeredDocument(512, 512, 8);
const largeCache = new CompositeTileCache({ tileSize: 64, maxBytes: 64 * 1024 * 1024 });
const largeReference = (document: ImageDocument) => compositeDocumentPixels(document);
largeCache.composite(largeCacheDocument, { referenceComposite: largeReference });
const compositeWarmLarge = benchmark(() => {
  const output = largeCache.composite(largeCacheDocument, {
    referenceComposite: largeReference,
  });
  if (output.length !== 512 * 512 * 4) throw new Error('Large warm composite size changed');
  if (!largeCache.stats.lastComposeFullyWarm) {
    throw new Error('Large warm composite expected fullyWarm');
  }
});
const compositeColdLarge = benchmark(() => {
  const output = compositeDocumentPixels(largeCacheDocument);
  if (output.length !== 512 * 512 * 4) throw new Error('Large cold composite size changed');
});
if (compositeWarmLarge.medianMs >= compositeColdLarge.medianMs) {
  throw new Error(
    `composite-tile-cache-warm-512-8 was not faster than cold: warm ${compositeWarmLarge.medianMs.toFixed(2)} ms vs cold ${compositeColdLarge.medianMs.toFixed(2)} ms`,
  );
}
const largeWarmBudget = baselines.scenarios['composite-tile-cache-warm-512-8'];
if (largeWarmBudget) {
  assertTiming('composite-tile-cache-warm-512-8', compositeWarmLarge, largeWarmBudget);
}

const brushLayer = solidLayer('brush', 2048, 2048, 5);
const brush = benchmark(() => {
  const patch = paintStroke(
    brushLayer,
    [
      { x: 128, y: 128, pressure: 0.5 },
      { x: 1920, y: 1920, pressure: 1 },
    ],
    { mode: 'source-over', size: 48, hardness: 0.8, opacity: 0.75, color: [20, 180, 120, 255] },
  );
  if (patch.width === 0 || patch.height === 0) throw new Error('Brush benchmark produced no patch');
});
assertTiming('brush-2048-diagonal', brush, baselines.scenarios['brush-2048-diagonal']);

const sparseStore = TiledPixelStore.empty(32_768, 32_768);
const sparsePatch = {
  x: 16_000,
  y: 16_000,
  width: 64,
  height: 64,
  pixels: new Uint8ClampedArray(64 * 64 * 4).fill(180),
};
let sparseAllocatedBytes = 0;
const tiled = benchmark(() => {
  const result = sparseStore.write(sparsePatch);
  sparseAllocatedBytes = result.store.allocatedByteLength;
  if (result.changedTileKeys.length !== 1)
    throw new Error('Sparse patch crossed an unexpected tile');
});
const tiledBudget = baselines.scenarios['tiled-sparse-32768-patch'];
assertTiming('tiled-sparse-32768-patch', tiled, tiledBudget);
if (sparseAllocatedBytes > tiledBudget.maxAllocatedBytes) {
  throw new Error(`tiled-sparse-32768-patch allocated ${String(sparseAllocatedBytes)} bytes`);
}

const sparseCoverageStore = TiledCoverageStore.empty(32_768, 32_768);
const sparseCoveragePatch = {
  x: 16_000,
  y: 16_000,
  width: 64,
  height: 64,
  coverage: new Uint8ClampedArray(64 * 64).fill(180),
};
let sparseCoverageAllocatedBytes = 0;
const tiledCoverage = benchmark(() => {
  const result = sparseCoverageStore.write(sparseCoveragePatch);
  sparseCoverageAllocatedBytes = result.store.allocatedByteLength;
  if (result.changedTileKeys.length !== 1) {
    throw new Error('Sparse coverage patch crossed an unexpected tile');
  }
});
const tiledCoverageBudget = baselines.scenarios['coverage-sparse-32768-patch'];
assertTiming('coverage-sparse-32768-patch', tiledCoverage, tiledCoverageBudget);
if (sparseCoverageAllocatedBytes > tiledCoverageBudget.maxAllocatedBytes) {
  throw new Error(
    `coverage-sparse-32768-patch allocated ${String(sparseCoverageAllocatedBytes)} bytes`,
  );
}

await settleMemoryAccounting();
const memoryBefore = process.memoryUsage();
const memoryDocument = layeredDocument(1024, 1024, 8);
await settleMemoryAccounting();
const memoryAfter = process.memoryUsage();
const logicalPayloadBytes = 1024 * 1024 * 4 * 8;
const arrayBufferDeltaBytes = Math.max(0, memoryAfter.arrayBuffers - memoryBefore.arrayBuffers);
const arrayBufferAmplification = arrayBufferDeltaBytes / logicalPayloadBytes;
if (
  arrayBufferAmplification >
  baselines.scenarios['document-1024-8-memory'].maxArrayBufferAmplification
) {
  throw new Error(
    `document-1024-8-memory exceeded its baseline: ${arrayBufferAmplification.toFixed(2)}x array-buffer amplification`,
  );
}
if (memoryDocument.layers.length !== 8) throw new Error('Memory benchmark fixture changed');

const report = {
  schemaVersion: 1,
  profile: baselines.profile,
  runtime: { node: process.version, platform: process.platform, architecture: process.arch },
  results: {
    'composite-512-12': composite,
    'composite-tile-cache-warm-128-4': {
      ...compositeWarm,
      coldMedianMs: compositeCold.medianMs,
      fullyWarm: tileCache.stats.lastComposeFullyWarm,
      hits: tileCache.stats.hits,
      misses: tileCache.stats.misses,
    },
    'composite-tile-cache-warm-512-8': {
      ...compositeWarmLarge,
      coldMedianMs: compositeColdLarge.medianMs,
      fullyWarm: largeCache.stats.lastComposeFullyWarm,
      hits: largeCache.stats.hits,
      misses: largeCache.stats.misses,
    },
    'brush-2048-diagonal': brush,
    'tiled-sparse-32768-patch': { ...tiled, allocatedBytes: sparseAllocatedBytes },
    'coverage-sparse-32768-patch': {
      ...tiledCoverage,
      allocatedBytes: sparseCoverageAllocatedBytes,
    },
    'document-1024-8-memory': {
      logicalPayloadBytes,
      arrayBufferDeltaBytes,
      arrayBufferAmplification,
      heapDeltaBytes: memoryAfter.heapUsed - memoryBefore.heapUsed,
    },
  },
};

process.stdout.write(`${JSON.stringify(report, null, 2)}\n`);
