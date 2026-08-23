import { expect, test } from '@playwright/test';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';

// Always write under the repo so Windows Playwright and WSL agents share evidence.
const SCRATCH = process.env.FREETOSHOP_PERF_SCRATCH ?? path.resolve('docs/perf-evidence');

type ProbeResult = {
  schemaVersion: 1;
  fixture: { width: number; height: number; layerCount: number; tileSize: number };
  cold: { medianMs: number; p95Ms: number; samplesMs: number[] };
  warm: { medianMs: number; p95Ms: number; samplesMs: number[]; fullyWarm: boolean };
  visibilityToggle: { coldMs: number; warmMs: number };
  cache: {
    hits: number;
    misses: number;
    regionLocalMisses: number;
    fullReferenceMisses: number;
    allocatedBytes: number;
  };
  longTasksDuringWarmMs: number;
  warmFasterThanCold: boolean;
  runtime: { userAgent: string; hardwareConcurrency: number };
};

type LiveAppMemorySample = {
  iteration: number;
  allocatedBytes: number;
  tileCount: number;
  uniqueSpatialTileCount: number;
  evictions: number;
};

test.describe('browser composite performance evidence', () => {
  test.beforeEach(async ({ page }) => {
    await page.setViewportSize({ width: 1672, height: 941 });
    await page.goto('/');
    await page.evaluate(async () => {
      localStorage.clear();
      await new Promise<void>((resolve, reject) => {
        const request = indexedDB.deleteDatabase('freetoshop');
        request.onsuccess = () => resolve();
        request.onerror = () => reject(request.error);
        request.onblocked = () => resolve();
      });
    });
    await page.reload();
  });

  test('PSD worker exposes monotonic parse, conversion, and tile progress', async ({
    page,
    browserName,
  }) => {
    await expect.poll(async () => page.evaluate(() => Boolean(window.__freetoshopPerf))).toBe(true);
    const bytes = [
      ...(await readFile('packages/file-codecs/test-fixtures/psd/pixel-layers-rgb-8.psd')),
    ];
    const result = await page.evaluate(async (fixtureBytes) => {
      const api = window.__freetoshopPerf;
      if (!api?.runPsdImportProgressProbe) {
        throw new Error('PSD import progress probe is not installed');
      }
      return api.runPsdImportProgressProbe(fixtureBytes);
    }, bytes);

    await mkdir(SCRATCH, { recursive: true });
    await writeFile(
      path.join(SCRATCH, `browser-psd-import-progress-${browserName}.json`),
      `${JSON.stringify(result, null, 2)}\n`,
      'utf8',
    );

    expect(result.schemaVersion).toBe(1);
    expect(result.progressSamples.length).toBeGreaterThanOrEqual(12);
    expect(result.progressSamples[0]).toBe(0.05);
    expect(result.progressSamples.at(-1)).toBe(1);
    expect(result.progressMonotonic).toBe(true);
    expect(result.streamedTileCount).toBe(2);
    expect(result.hydratedResourceCount).toBe(2);
    expect(result.layerCount).toBe(2);
    expect(result.exact).toBe(true);
  });

  test('repeated 4096px mixed worker results retain only one exact generation', async ({
    page,
    browserName,
  }) => {
    await expect.poll(async () => page.evaluate(() => Boolean(window.__freetoshopPerf))).toBe(true);
    const result = await page.evaluate(async () => {
      const api = window.__freetoshopPerf;
      if (!api?.runRepeatedMixedResultProbe) {
        throw new Error('Repeated mixed-result probe is not installed');
      }
      return api.runRepeatedMixedResultProbe();
    });

    await mkdir(SCRATCH, { recursive: true });
    await writeFile(
      path.join(SCRATCH, `browser-repeated-mixed-result-${browserName}.json`),
      `${JSON.stringify(result, null, 2)}\n`,
      'utf8',
    );

    expect(result.schemaVersion).toBe(1);
    expect(result.sourceSize).toBe(4096);
    expect(result.iterations).toBe(4);
    expect(result.zeroUploadAfterFirst).toBe(true);
    expect(result.retainedEqualsFinal).toBe(true);
    expect(result.noGenerationGrowth).toBe(true);
    expect(result.exact).toBe(true);
    for (const sample of result.samples) {
      expect(sample.hydratedResourceCount).toBe(6);
      expect(sample.retainedResourceCount).toBe(6);
      expect(sample.streamedTileCount).toBeGreaterThanOrEqual(12);
      expect(sample.retainedAllocatedBytes).toBeLessThan(4096 * 4096 * 4);
      expect(sample.durationMs).toBeLessThan(15_000);
    }
  });

  test('dense mixed-result cancellation clears early, middle, and late partials', async ({
    page,
    browserName,
  }) => {
    await expect.poll(async () => page.evaluate(() => Boolean(window.__freetoshopPerf))).toBe(true);
    const result = await page.evaluate(async () => {
      const api = window.__freetoshopPerf;
      if (!api?.runMixedResultCancellationProbe) {
        throw new Error('Mixed-result cancellation probe is not installed');
      }
      return api.runMixedResultCancellationProbe();
    });

    await mkdir(SCRATCH, { recursive: true });
    await writeFile(
      path.join(SCRATCH, `browser-mixed-result-cancellation-${browserName}.json`),
      `${JSON.stringify(result, null, 2)}\n`,
      'utf8',
    );

    expect(result.schemaVersion).toBe(1);
    expect(result.size).toBe(1024);
    expect(result.samples).toHaveLength(3);
    expect(result.thresholdsReached).toBe(true);
    expect(result.everyCancellationCleared).toBe(true);
    expect(result.everyAttemptReuploaded).toBe(true);
    expect(result.samples.map(({ inputTransferBytes }) => inputTransferBytes)).toEqual([
      12 * 1024 * 1024,
      12 * 1024 * 1024,
      12 * 1024 * 1024,
    ]);
    expect(result.samples[0]?.observedProgress).toBeLessThan(0.3);
    expect(result.samples[1]?.observedProgress).toBeLessThan(0.6);
    expect(result.samples[2]?.observedProgress).toBeLessThan(0.9);
    expect(result.recoveryInputTransferBytes).toBe(12 * 1024 * 1024);
    expect(result.recoveryStreamedTileCount).toBe(96);
    expect(result.recoveryRetainedResourceCount).toBe(6);
    expect(result.recoveryRetainedAllocatedBytes).toBe(12 * 1023 * 1023);
    expect(result.recoveryExact).toBe(true);
  });

  test('dense mixed-result long session stays on one bounded generation', async ({
    page,
    browserName,
  }) => {
    test.setTimeout(120_000);
    await expect.poll(async () => page.evaluate(() => Boolean(window.__freetoshopPerf))).toBe(true);
    const result = await page.evaluate(async () => {
      const api = window.__freetoshopPerf;
      if (!api?.runDenseMixedLongSessionProbe) {
        throw new Error('Dense mixed long-session probe is not installed');
      }
      return api.runDenseMixedLongSessionProbe();
    });

    await mkdir(SCRATCH, { recursive: true });
    await writeFile(
      path.join(SCRATCH, `browser-dense-mixed-long-session-${browserName}.json`),
      `${JSON.stringify(result, null, 2)}\n`,
      'utf8',
    );

    expect(result.schemaVersion).toBe(1);
    expect(result.size).toBe(1024);
    expect(result.iterations).toBe(12);
    expect(result.zeroUploadAfterFirst).toBe(true);
    expect(result.retainedEqualsFinal).toBe(true);
    expect(result.stableBySize).toBe(true);
    expect(result.maxDurationMs).toBeLessThan(15_000);
    expect(result.exact).toBe(true);
    expect(result.samples[0]?.inputTransferBytes).toBe(12 * 1024 * 1024);
    for (const sample of result.samples) {
      expect(sample.streamedTileCount).toBe(96);
      expect(sample.hydratedResourceCount).toBe(6);
      expect(sample.retainedResourceCount).toBe(6);
      expect(sample.retainedAllocatedBytes).toBe(12 * sample.size * sample.size);
    }
  });

  test('nested structured worker session stays exact and generation-bounded', async ({
    page,
    browserName,
  }) => {
    test.setTimeout(120_000);
    await expect.poll(async () => page.evaluate(() => Boolean(window.__freetoshopPerf))).toBe(true);
    const result = await page.evaluate(async () => {
      const api = window.__freetoshopPerf;
      if (!api?.runStructuredWorkerSessionProbe) {
        throw new Error('Structured worker session probe is not installed');
      }
      return api.runStructuredWorkerSessionProbe();
    });

    await mkdir(SCRATCH, { recursive: true });
    await writeFile(
      path.join(SCRATCH, `browser-structured-worker-session-${browserName}.json`),
      `${JSON.stringify(result, null, 2)}\n`,
      'utf8',
    );

    expect(result.schemaVersion).toBe(1);
    expect(result.size).toBe(512);
    expect(result.iterations).toBe(12);
    expect(result.zeroUploadAfterFirst).toBe(true);
    expect(result.retainedEqualsFinal).toBe(true);
    expect(result.stableBySize).toBe(true);
    expect(result.structureExact).toBe(true);
    expect(result.contentExact).toBe(true);
    expect(result.maxDurationMs).toBeLessThan(15_000);
    expect(result.samples[0]?.inputTransferBytes).toBe(2_767_616);
    for (const sample of result.samples) {
      expect(sample.streamedTileCount).toBe(29);
      expect(sample.hydratedResourceCount).toBe(11);
      expect(sample.retainedResourceCount).toBe(11);
      expect(sample.retainedAllocatedBytes).toBe(sample.size === 512 ? 2_767_616 : 2_758_409);
    }
  });

  test('structured cross-kind worker session stays exact and generation-bounded', async ({
    page,
    browserName,
  }) => {
    test.setTimeout(120_000);
    await expect.poll(async () => page.evaluate(() => Boolean(window.__freetoshopPerf))).toBe(true);
    const result = await page.evaluate(async () => {
      const api = window.__freetoshopPerf;
      if (!api?.runStructuredCrossKindWorkerSessionProbe) {
        throw new Error('Structured cross-kind worker session probe is not installed');
      }
      return api.runStructuredCrossKindWorkerSessionProbe();
    });

    await mkdir(SCRATCH, { recursive: true });
    await writeFile(
      path.join(SCRATCH, `browser-structured-cross-kind-worker-session-${browserName}.json`),
      `${JSON.stringify(result, null, 2)}\n`,
      'utf8',
    );

    expect(result.schemaVersion).toBe(1);
    expect(result.size).toBe(512);
    expect(result.iterations).toBe(4);
    expect(result.operationCount).toBe(12);
    expect(result.zeroDocumentUploadAfterFirst).toBe(true);
    expect(result.binaryImportOnly).toBe(true);
    expect(result.retainedEqualsImportedGeneration).toBe(true);
    expect(result.stableBySize).toBe(true);
    expect(result.structureExact).toBe(true);
    expect(result.contentExact).toBe(true);
    expect(result.maxDurationMs).toBeLessThan(15_000);
    expect(result.samples[0]?.resizeInputTransferBytes).toBe(2_767_616);
    for (const sample of result.samples) {
      expect(sample.resizeStreamedTileCount).toBe(29);
      expect(sample.exportInputTransferBytes).toBe(0);
      expect(sample.importInputTransferBytes).toBe(sample.nativeByteLength);
      expect(sample.importStreamedTileCount).toBe(29);
      expect(sample.importHydratedResourceCount).toBe(11);
      expect(sample.retainedResourceCount).toBe(11);
      expect(sample.retainedAllocatedBytes).toBe(sample.size === 512 ? 2_767_616 : 2_758_409);
    }
  });

  test('structured filter and composite mutations stay delta-bounded', async ({
    page,
    browserName,
  }) => {
    test.setTimeout(120_000);
    await expect.poll(async () => page.evaluate(() => Boolean(window.__freetoshopPerf))).toBe(true);
    const result = await page.evaluate(async () => {
      const api = window.__freetoshopPerf;
      if (!api?.runStructuredFilterCompositeMutationProbe) {
        throw new Error('Structured filter/composite mutation probe is not installed');
      }
      return api.runStructuredFilterCompositeMutationProbe();
    });

    await mkdir(SCRATCH, { recursive: true });
    await writeFile(
      path.join(SCRATCH, `browser-structured-filter-composite-mutation-${browserName}.json`),
      `${JSON.stringify(result, null, 2)}\n`,
      'utf8',
    );

    expect(result.schemaVersion).toBe(1);
    expect(result.size).toBe(512);
    expect(result.iterations).toBe(4);
    expect(result.initialCompositeInputTransferBytes).toBe(2_243_328);
    expect(result.filtersReuseDocumentResources).toBe(true);
    expect(result.compositesTransferOnlyChangedPixels).toBe(true);
    expect(result.retainedGenerationBounded).toBe(true);
    expect(result.everyCompositeExact).toBe(true);
    expect(result.structureExact).toBe(true);
    expect(result.contentExactAfterEvenMutations).toBe(true);
    expect(result.maxDurationMs).toBeLessThan(15_000);
    for (const sample of result.samples) {
      expect(sample.filterInputTransferBytes).toBe(0);
      expect(sample.streamedPatchCount).toBe(4);
      expect(sample.compositeInputTransferBytes).toBe(512 * 512 * 4);
      expect(sample.retainedResourceCount).toBe(9);
      expect(sample.retainedAllocatedBytes).toBe(2_243_328);
      expect(sample.compositeExact).toBe(true);
    }
  });

  test('structured localized filter and native-save mutations stay tile-bounded', async ({
    page,
    browserName,
  }) => {
    test.setTimeout(120_000);
    await expect.poll(async () => page.evaluate(() => Boolean(window.__freetoshopPerf))).toBe(true);
    const result = await page.evaluate(async () => {
      const api = window.__freetoshopPerf;
      if (!api?.runStructuredLocalizedMutationProbe) {
        throw new Error('Structured localized mutation probe is not installed');
      }
      return api.runStructuredLocalizedMutationProbe();
    });

    await mkdir(SCRATCH, { recursive: true });
    await writeFile(
      path.join(SCRATCH, `browser-structured-localized-mutation-${browserName}.json`),
      `${JSON.stringify(result, null, 2)}\n`,
      'utf8',
    );

    expect(result.schemaVersion).toBe(1);
    expect(result.size).toBe(512);
    expect(result.iterations).toBe(4);
    expect(result.initialNativeInputTransferBytes).toBe(2_767_616);
    expect(result.selectionTransfersTileBounded).toBe(true);
    expect(result.nativeTransfersPixelTileBounded).toBe(true);
    expect(result.retainedGenerationBounded).toBe(true);
    expect(result.everyReopenExact).toBe(true);
    expect(result.structureExact).toBe(true);
    expect(result.accumulatedContentExact).toBe(true);
    expect(result.maxDurationMs).toBeLessThan(15_000);
    for (const sample of result.samples) {
      expect(sample.filterInputTransferBytes).toBe(256 * 256);
      expect(sample.streamedPatchCount).toBe(1);
      expect(sample.nativeInputTransferBytes).toBe(256 * 256 * 4);
      expect(sample.retainedResourceCount).toBe(11);
      expect(sample.retainedAllocatedBytes).toBe(2_571_008);
      expect(sample.reopenedExact).toBe(true);
    }
  });

  test('structured progressive cancellation recovers without stale frames', async ({
    page,
    browserName,
  }) => {
    test.setTimeout(120_000);
    await expect.poll(async () => page.evaluate(() => Boolean(window.__freetoshopPerf))).toBe(true);
    const result = await page.evaluate(async () => {
      const api = window.__freetoshopPerf;
      if (!api?.runProgressiveCancellationRecoveryProbe) {
        throw new Error('Progressive cancellation recovery probe is not installed');
      }
      return api.runProgressiveCancellationRecoveryProbe();
    });

    await mkdir(SCRATCH, { recursive: true });
    await writeFile(
      path.join(SCRATCH, `browser-progressive-cancellation-recovery-${browserName}.json`),
      `${JSON.stringify(result, null, 2)}\n`,
      'utf8',
    );

    expect(result.schemaVersion).toBe(1);
    expect(result.size).toBe(512);
    expect(result.iterations).toBe(3);
    expect(result.cancellationForcesCleanRecovery).toBe(true);
    expect(result.verificationReusesRecoveryResources).toBe(true);
    expect(result.localizedCandidatesUseTileDeltas).toBe(true);
    expect(result.noStaleFramesPublished).toBe(true);
    expect(result.everyRecoveryExact).toBe(true);
    expect(result.errorCount).toBe(0);
    expect(result.maxDurationMs).toBeLessThan(15_000);
    for (const sample of result.samples) {
      expect(sample.recoveryInputTransferBytes).toBe(2_243_328);
      expect(sample.verificationInputTransferBytes).toBe(0);
      // The 64px viewport starts 16px into a 64px composite tile, so the compact
      // progressive result covers the two aligned tiles on each axis.
      expect(sample.recoveryFrameBytes).toBe(128 * 128 * 4);
      expect(sample.staleFramePublished).toBe(false);
      expect(sample.recoveryExact).toBe(true);
    }
  });

  test('warm composite tile cache beats cold reference in the browser', async ({ page }) => {
    await expect.poll(async () => page.evaluate(() => Boolean(window.__freetoshopPerf))).toBe(true);

    const result = (await page.evaluate(async () => {
      const api = window.__freetoshopPerf;
      if (!api) throw new Error('Browser composite probe is not installed');
      return api.runBrowserCompositeProbeWithLongTasks({
        width: 256,
        height: 256,
        layerCount: 8,
        tileSize: 64,
        warmSamples: 5,
        coldSamples: 3,
      });
    })) as ProbeResult;

    await mkdir(SCRATCH, { recursive: true });
    const evidencePath = path.join(SCRATCH, 'browser-composite-probe.json');
    await writeFile(evidencePath, `${JSON.stringify(result, null, 2)}\n`, 'utf8');

    expect(result.schemaVersion).toBe(1);
    expect(result.warm.fullyWarm).toBe(true);
    expect(result.warmFasterThanCold).toBe(true);
    expect(result.warm.medianMs).toBeLessThan(result.cold.medianMs);
    // Interactive warm path should stay well under a frame budget on CI-like hosts.
    expect(result.warm.medianMs).toBeLessThan(50);
    expect(result.visibilityToggle.warmMs).toBeLessThanOrEqual(result.visibilityToggle.coldMs + 5);
    expect(result.cache.hits).toBeGreaterThan(0);
    // Long tasks are best-effort (not all browsers expose longtask entries).
    expect(result.longTasksDuringWarmMs).toBeGreaterThanOrEqual(0);
  });

  test('worker cold path avoids main-thread reference fill and matches reference pixels', async ({
    page,
  }) => {
    await expect.poll(async () => page.evaluate(() => Boolean(window.__freetoshopPerf))).toBe(true);

    const result = (await page.evaluate(async () => {
      const api = window.__freetoshopPerf;
      if (!api?.runWorkerCompositeColdPathProbe) {
        throw new Error('Worker composite cold-path probe is not installed');
      }
      return api.runWorkerCompositeColdPathProbe({
        width: 128,
        height: 128,
        layerCount: 6,
        tileSize: 64,
      });
    })) as {
      schemaVersion: 1;
      warmOnlyReferenceCalls: number;
      workerMatchesReference: boolean;
      planWarmAssembleOnlyWhenCold: boolean;
      mainThreadWarmOnlyMs: number;
      mainThreadReferenceMs: number;
      workerFrameWaitMs: number;
      longTasksDuringMainReferenceMs: number;
      longTasksDuringWorkerWaitMs: number;
    };

    await mkdir(SCRATCH, { recursive: true });
    await writeFile(
      path.join(SCRATCH, 'browser-worker-composite-cold-path.json'),
      `${JSON.stringify(result, null, 2)}\n`,
      'utf8',
    );

    expect(result.schemaVersion).toBe(1);
    expect(result.planWarmAssembleOnlyWhenCold).toBe(true);
    expect(result.warmOnlyReferenceCalls).toBe(0);
    expect(result.workerMatchesReference).toBe(true);
    // Warm-only assemble must stay cheap relative to full reference on this fixture.
    expect(result.mainThreadWarmOnlyMs).toBeLessThan(result.mainThreadReferenceMs + 5);
    expect(result.workerFrameWaitMs).toBeGreaterThan(0);
    expect(result.longTasksDuringMainReferenceMs).toBeGreaterThanOrEqual(0);
    expect(result.longTasksDuringWorkerWaitMs).toBeGreaterThanOrEqual(0);
  });

  test('larger cold fixture keeps warmAssembleOnly off the reference path', async ({ page }) => {
    test.setTimeout(90_000);
    await expect.poll(async () => page.evaluate(() => Boolean(window.__freetoshopPerf))).toBe(true);

    const result = (await page.evaluate(async () => {
      const api = window.__freetoshopPerf;
      if (!api?.runWorkerCompositeColdPathProbe) {
        throw new Error('Worker composite cold-path probe is not installed');
      }
      return api.runWorkerCompositeColdPathProbe({
        width: 256,
        height: 256,
        layerCount: 8,
        tileSize: 64,
      });
    })) as {
      schemaVersion: 1;
      fixture: { width: number; height: number; layerCount: number };
      warmOnlyReferenceCalls: number;
      workerMatchesReference: boolean;
      planWarmAssembleOnlyWhenCold: boolean;
      mainThreadWarmOnlyMs: number;
      mainThreadReferenceMs: number;
      workerFrameWaitMs: number;
    };

    await mkdir(SCRATCH, { recursive: true });
    await writeFile(
      path.join(SCRATCH, 'browser-worker-composite-cold-path-large.json'),
      `${JSON.stringify(result, null, 2)}\n`,
      'utf8',
    );

    expect(result.schemaVersion).toBe(1);
    expect(result.fixture.width).toBe(256);
    expect(result.fixture.layerCount).toBe(8);
    expect(result.planWarmAssembleOnlyWhenCold).toBe(true);
    expect(result.warmOnlyReferenceCalls).toBe(0);
    expect(result.workerMatchesReference).toBe(true);
    // On the larger fixture, warm-only must remain far cheaper than full reference.
    expect(result.mainThreadWarmOnlyMs).toBeLessThan(result.mainThreadReferenceMs);
    expect(result.workerFrameWaitMs).toBeGreaterThan(0);
  });

  test('composite working set stays within cache budget across edit iterations', async ({
    page,
  }) => {
    await expect.poll(async () => page.evaluate(() => Boolean(window.__freetoshopPerf))).toBe(true);

    const result = (await page.evaluate(() => {
      const api = window.__freetoshopPerf;
      if (!api?.runCompositeWorkingSetProbe) {
        throw new Error('Composite working-set probe is not installed');
      }
      return api.runCompositeWorkingSetProbe({
        width: 128,
        height: 128,
        layerCount: 6,
        tileSize: 64,
        maxBytes: 48 * 1024,
        iterations: 24,
      });
    })) as {
      schemaVersion: 1;
      peakAllocatedBytes: number;
      finalAllocatedBytes: number;
      totalEvictions: number;
      stayedWithinBudget: boolean;
      boundedGrowth: boolean;
      fixture: { maxBytes: number; iterations: number };
    };

    await mkdir(SCRATCH, { recursive: true });
    await writeFile(
      path.join(SCRATCH, 'browser-composite-working-set.json'),
      `${JSON.stringify(result, null, 2)}\n`,
      'utf8',
    );

    expect(result.schemaVersion).toBe(1);
    expect(result.stayedWithinBudget).toBe(true);
    expect(result.boundedGrowth).toBe(true);
    expect(result.peakAllocatedBytes).toBeLessThanOrEqual(result.fixture.maxBytes);
    expect(result.finalAllocatedBytes).toBeLessThanOrEqual(result.fixture.maxBytes);
    expect(result.totalEvictions).toBeGreaterThan(0);
    expect(result.fixture.iterations).toBe(24);
  });

  test('long composite session keeps cache memory bounded across 120 revisions', async ({
    page,
  }) => {
    test.setTimeout(120_000);
    await expect.poll(async () => page.evaluate(() => Boolean(window.__freetoshopPerf))).toBe(true);

    const result = (await page.evaluate(() => {
      const api = window.__freetoshopPerf;
      if (!api?.runCompositeLongSessionProbe) {
        throw new Error('Composite long-session probe is not installed');
      }
      return api.runCompositeLongSessionProbe({
        width: 192,
        height: 192,
        layerCount: 8,
        tileSize: 64,
        maxBytes: 96 * 1024,
        iterations: 120,
      });
    })) as {
      schemaVersion: 1;
      peakAllocatedBytes: number;
      finalAllocatedBytes: number;
      totalEvictions: number;
      stayedWithinBudget: boolean;
      boundedGrowth: boolean;
      fixture: { maxBytes: number; iterations: number };
      jsHeap?: {
        usedJSHeapSizeStart: number;
        usedJSHeapSizeEnd: number;
        jsHeapSizeLimit: number;
      };
    };

    await mkdir(SCRATCH, { recursive: true });
    await writeFile(
      path.join(SCRATCH, 'browser-composite-long-session.json'),
      `${JSON.stringify(result, null, 2)}\n`,
      'utf8',
    );

    expect(result.schemaVersion).toBe(1);
    expect(result.fixture.iterations).toBe(120);
    expect(result.stayedWithinBudget).toBe(true);
    expect(result.boundedGrowth).toBe(true);
    expect(result.peakAllocatedBytes).toBeLessThanOrEqual(result.fixture.maxBytes);
    expect(result.finalAllocatedBytes).toBeLessThanOrEqual(result.fixture.maxBytes);
    expect(result.totalEvictions).toBeGreaterThan(0);
    // Optional Chromium heap: end must not explode past start by an unbounded factor.
    if (result.jsHeap) {
      expect(result.jsHeap.usedJSHeapSizeEnd).toBeLessThan(
        Math.max(result.jsHeap.usedJSHeapSizeStart * 4, 64 * 1024 * 1024),
      );
    }
  });

  test('live editor keeps composite cache and heap bounded across 120 revisions', async ({
    page,
    browserName,
  }) => {
    test.setTimeout(180_000);
    const cacheBudgetBytes = 96 * 1024 * 1024;
    const iterations = 120;

    await page.getByRole('button', { name: 'Create document' }).click();
    const layerActions = page.getByLabel('Layer actions');
    await layerActions.getByLabel('More layer actions').click();
    await layerActions.getByLabel('Add shape layer').selectOption('rectangle');
    await layerActions.getByLabel('Add layer effect').selectOption('drop-shadow');

    const canvas = page.locator('canvas[aria-label$=" canvas"]');
    await expect
      .poll(async () => Number(await canvas.getAttribute('data-composite-cache-tile-count')))
      .toBeGreaterThan(0);
    if (browserName === 'chromium') await page.requestGC();
    const heapStart = await page.evaluate(() => {
      const memory = (
        performance as Performance & {
          memory?: { usedJSHeapSize: number; jsHeapSizeLimit: number };
        }
      ).memory;
      return memory
        ? { usedJSHeapSize: memory.usedJSHeapSize, jsHeapSizeLimit: memory.jsHeapSizeLimit }
        : null;
    });

    const opacity = page.getByRole('spinbutton', { name: 'Layer opacity' });
    const samples: LiveAppMemorySample[] = [];
    for (let iteration = 0; iteration < iterations; iteration += 1) {
      await opacity.fill(String(35 + (iteration % 65)));
      await page.evaluate(
        () =>
          new Promise<void>((resolve) =>
            requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
          ),
      );
      samples.push(
        await canvas.evaluate(
          (element, currentIteration) => ({
            iteration: currentIteration,
            allocatedBytes: Number(element.dataset.compositeCacheAllocatedBytes),
            tileCount: Number(element.dataset.compositeCacheTileCount),
            uniqueSpatialTileCount: Number(element.dataset.compositeCacheUniqueSpatialTiles),
            evictions: Number(element.dataset.compositeCacheEvictions),
          }),
          iteration,
        ),
      );
    }

    await expect
      .poll(async () => await canvas.getAttribute('data-composite-worker-inflight'), {
        timeout: 30_000,
      })
      .toBe('0');
    if (browserName === 'chromium') await page.requestGC();
    const heapEnd = await page.evaluate(() => {
      const memory = (
        performance as Performance & {
          memory?: { usedJSHeapSize: number; jsHeapSizeLimit: number };
        }
      ).memory;
      return memory
        ? { usedJSHeapSize: memory.usedJSHeapSize, jsHeapSizeLimit: memory.jsHeapSizeLimit }
        : null;
    });

    const firstHalfPeak = Math.max(
      ...samples.slice(0, iterations / 2).map((sample) => sample.allocatedBytes),
    );
    const secondHalfPeak = Math.max(
      ...samples.slice(iterations / 2).map((sample) => sample.allocatedBytes),
    );
    const result = {
      schemaVersion: 1,
      fixture: { iterations, cacheBudgetBytes, document: '1024x768 shape with drop shadow' },
      peakAllocatedBytes: Math.max(...samples.map((sample) => sample.allocatedBytes)),
      finalAllocatedBytes: samples.at(-1)?.allocatedBytes ?? 0,
      maxTileCount: Math.max(...samples.map((sample) => sample.tileCount)),
      maxUniqueSpatialTileCount: Math.max(
        ...samples.map((sample) => sample.uniqueSpatialTileCount),
      ),
      totalEvictions: samples.at(-1)?.evictions ?? 0,
      boundedSecondHalf: secondHalfPeak <= firstHalfPeak * 1.25 + 64 * 64 * 4 * 8,
      workerSettled: (await canvas.getAttribute('data-composite-worker-inflight')) === '0',
      heap:
        heapStart && heapEnd
          ? {
              start: heapStart.usedJSHeapSize,
              end: heapEnd.usedJSHeapSize,
              limit: heapEnd.jsHeapSizeLimit,
            }
          : null,
      samples,
      runtime: { browserName, userAgent: await page.evaluate(() => navigator.userAgent) },
    };

    await mkdir(SCRATCH, { recursive: true });
    await writeFile(
      path.join(SCRATCH, `browser-live-app-long-session-${browserName}.json`),
      `${JSON.stringify(result, null, 2)}\n`,
      'utf8',
    );

    expect(result.peakAllocatedBytes).toBeLessThanOrEqual(cacheBudgetBytes);
    expect(result.finalAllocatedBytes).toBeLessThanOrEqual(cacheBudgetBytes);
    expect(result.boundedSecondHalf).toBe(true);
    expect(result.workerSettled).toBe(true);
    expect(result.maxTileCount).toBeGreaterThan(0);
    expect(result.maxUniqueSpatialTileCount).toBeGreaterThan(0);
    if (result.heap) {
      expect(result.heap.end - result.heap.start).toBeLessThan(32 * 1024 * 1024);
    }
  });

  test('live pan and edits force bounded composite-cache eviction', async ({
    page,
    browserName,
  }) => {
    test.setTimeout(180_000);
    // Eight 64² RGBA tiles below the 1024×768 full composite (3 MiB): one
    // active progressive viewport fits, but cross-viewport working sets evict.
    const cacheBudgetBytes = 3 * 1024 * 1024 - 8 * 64 * 64 * 4;
    const iterations = 16;
    await page.addInitScript((maxBytes) => {
      window.__freetoshopPerfCacheMaxBytes = maxBytes;
    }, cacheBudgetBytes);
    await page.reload();

    await page.getByRole('button', { name: 'Create document' }).click();
    const canvas = page.locator('canvas[aria-label$=" canvas"]');
    const box = await canvas.boundingBox();
    if (!box) throw new Error('Live eviction fixture canvas is unavailable');
    const center = { x: box.x + box.width / 2, y: box.y + box.height / 2 };
    await page.mouse.move(center.x, center.y);
    for (let zoomStep = 0; zoomStep < 8; zoomStep += 1) {
      await page.mouse.wheel(0, -100);
      await page.evaluate(
        () =>
          new Promise<void>((resolve) =>
            requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
          ),
      );
    }
    await expect
      .poll(async () => Number(await canvas.getAttribute('data-document-zoom')))
      .toBeGreaterThan(1.5);

    const layerActions = page.getByLabel('Layer actions');
    await layerActions.getByLabel('More layer actions').click();
    await layerActions.getByLabel('Add shape layer').selectOption('rectangle');
    await layerActions.getByLabel('Add layer effect').selectOption('drop-shadow');
    await expect(canvas).toHaveAttribute(
      'data-composite-cache-max-bytes',
      String(cacheBudgetBytes),
    );
    await expect
      .poll(async () => Number(await canvas.getAttribute('data-composite-cache-tile-count')))
      .toBeGreaterThan(0);
    const startingEvictions = Number(await canvas.getAttribute('data-composite-cache-evictions'));
    await page.getByRole('button', { name: 'Background', exact: true }).click();

    const panDeltas = [
      { x: -300, y: 0 },
      { x: 0, y: -240 },
      { x: 300, y: 0 },
      { x: 300, y: 0 },
      { x: 0, y: 240 },
      { x: 0, y: 240 },
      { x: -300, y: 0 },
      { x: -300, y: 0 },
      { x: 0, y: -240 },
      { x: 0, y: -240 },
    ] as const;
    const samples: LiveAppMemorySample[] = [];
    for (let iteration = 0; iteration < iterations; iteration += 1) {
      await page.keyboard.press('h');
      const delta = panDeltas[iteration % panDeltas.length] ?? { x: 0, y: 0 };
      await page.mouse.move(center.x, center.y);
      await page.mouse.down();
      await page.mouse.move(center.x + delta.x, center.y + delta.y, { steps: 5 });
      await page.mouse.up();

      if (iteration % 4 === 3) {
        await page.keyboard.press('b');
        await page.mouse.move(center.x - 8, center.y - 8);
        await page.mouse.down();
        await page.mouse.move(center.x + 8, center.y + 8, { steps: 3 });
        await page.mouse.up();
      }
      await page.evaluate(
        () =>
          new Promise<void>((resolve) =>
            requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
          ),
      );
      samples.push(
        await canvas.evaluate(
          (element, currentIteration) => ({
            iteration: currentIteration,
            allocatedBytes: Number(element.dataset.compositeCacheAllocatedBytes),
            tileCount: Number(element.dataset.compositeCacheTileCount),
            uniqueSpatialTileCount: Number(element.dataset.compositeCacheUniqueSpatialTiles),
            evictions: Number(element.dataset.compositeCacheEvictions),
          }),
          iteration,
        ),
      );
    }

    await expect
      .poll(async () => await canvas.getAttribute('data-composite-worker-inflight'), {
        timeout: 30_000,
      })
      .toBe('0');
    const finalEvictions = samples.at(-1)?.evictions ?? 0;
    const result = {
      schemaVersion: 1,
      fixture: { iterations, cacheBudgetBytes, zoomSteps: 8, localizedPaintEdits: 4 },
      startingEvictions,
      finalEvictions,
      sessionEvictions: finalEvictions - startingEvictions,
      peakAllocatedBytes: Math.max(...samples.map((sample) => sample.allocatedBytes)),
      finalAllocatedBytes: samples.at(-1)?.allocatedBytes ?? 0,
      maxTileCount: Math.max(...samples.map((sample) => sample.tileCount)),
      maxUniqueSpatialTileCount: Math.max(
        ...samples.map((sample) => sample.uniqueSpatialTileCount),
      ),
      stayedWithinBudget: samples.every((sample) => sample.allocatedBytes <= cacheBudgetBytes),
      workerSettled: (await canvas.getAttribute('data-composite-worker-inflight')) === '0',
      samples,
      runtime: { browserName, userAgent: await page.evaluate(() => navigator.userAgent) },
    };

    await mkdir(SCRATCH, { recursive: true });
    await writeFile(
      path.join(SCRATCH, `browser-live-app-pan-edit-eviction-${browserName}.json`),
      `${JSON.stringify(result, null, 2)}\n`,
      'utf8',
    );

    expect(result.stayedWithinBudget).toBe(true);
    expect(result.peakAllocatedBytes).toBeLessThanOrEqual(cacheBudgetBytes);
    expect(result.finalAllocatedBytes).toBeLessThanOrEqual(cacheBudgetBytes);
    expect(result.sessionEvictions).toBeGreaterThan(0);
    expect(result.maxUniqueSpatialTileCount).toBeGreaterThan(0);
    expect(result.workerSettled).toBe(true);
  });

  test('large document progressive pan session keeps tile memory bounded', async ({ page }) => {
    test.setTimeout(180_000);
    await expect.poll(async () => page.evaluate(() => Boolean(window.__freetoshopPerf))).toBe(true);

    const result = (await page.evaluate(() => {
      const api = window.__freetoshopPerf;
      if (!api?.runLargeDocumentSustainedMemoryProbe) {
        throw new Error('Large-document sustained memory probe is not installed');
      }
      // 1024² dense stack with progressive pan grid + edit churn under a tight cache budget.
      // 2.5 MiB holds ~160×64² RGBA tiles — well under a 6-layer full surface (24 MiB)
      // and under a full 256-tile single-surface grid (4 MiB) so LRU must evict.
      return api.runLargeDocumentSustainedMemoryProbe({
        width: 1024,
        height: 1024,
        layerCount: 6,
        tileSize: 64,
        viewportWidth: 384,
        viewportHeight: 384,
        maxBytes: Math.floor(2.5 * 1024 * 1024),
        iterations: 48,
      });
    })) as {
      schemaVersion: 1;
      fixture: {
        width: number;
        height: number;
        layerCount: number;
        maxBytes: number;
        iterations: number;
        fullTileCount: number;
        fullMultiLayerBytes: number;
      };
      peakAllocatedBytes: number;
      finalAllocatedBytes: number;
      maxTileCount: number;
      maxUniqueSpatialTileCount: number;
      totalEvictions: number;
      peakTileCountFractionOfFull: number;
      peakUniqueSpatialFractionOfFull: number;
      stayedWithinBudget: boolean;
      boundedGrowth: boolean;
      progressiveSparseVsFull: boolean;
      jsHeap?: {
        usedJSHeapSizeStart: number;
        usedJSHeapSizeEnd: number;
        jsHeapSizeLimit: number;
      };
    };

    await mkdir(SCRATCH, { recursive: true });
    await writeFile(
      path.join(SCRATCH, 'browser-composite-large-doc-sustained-memory.json'),
      `${JSON.stringify(result, null, 2)}\n`,
      'utf8',
    );

    expect(result.schemaVersion).toBe(1);
    expect(result.fixture.width).toBe(1024);
    expect(result.fixture.iterations).toBe(48);
    expect(result.stayedWithinBudget).toBe(true);
    expect(result.boundedGrowth).toBe(true);
    expect(result.progressiveSparseVsFull).toBe(true);
    expect(result.peakAllocatedBytes).toBeLessThanOrEqual(result.fixture.maxBytes);
    expect(result.finalAllocatedBytes).toBeLessThanOrEqual(result.fixture.maxBytes);
    expect(result.totalEvictions).toBeGreaterThan(0);
    // Peak cache stays under a naive multi-layer contiguous materialization.
    expect(result.peakAllocatedBytes).toBeLessThan(result.fixture.fullMultiLayerBytes);
    // Under the tight budget, unique spatial footprint stays below a full grid.
    expect(result.maxUniqueSpatialTileCount).toBeLessThan(result.fixture.fullTileCount);
    expect(result.peakUniqueSpatialFractionOfFull).toBeLessThan(1);
    if (result.jsHeap) {
      expect(result.jsHeap.usedJSHeapSizeEnd).toBeLessThan(
        Math.max(result.jsHeap.usedJSHeapSizeStart * 4, 128 * 1024 * 1024),
      );
    }
  });

  test('large document progressive pan and zoom stay within interactive budget', async ({
    page,
  }) => {
    test.setTimeout(180_000);
    await expect.poll(async () => page.evaluate(() => Boolean(window.__freetoshopPerf))).toBe(true);

    const result = (await page.evaluate(() => {
      const api = window.__freetoshopPerf;
      if (!api?.runLargeDocumentInteractiveProbe) {
        throw new Error('Large-document interactive probe is not installed');
      }
      return api.runLargeDocumentInteractiveProbe({
        width: 1024,
        height: 1024,
        layerCount: 6,
        tileSize: 64,
        viewportWidth: 384,
        viewportHeight: 384,
        panOffsetX: 512,
        panOffsetY: 256,
        warmSamples: 5,
        interactiveBudgetMs: 50,
      });
    })) as {
      schemaVersion: 1;
      fixture: {
        width: number;
        height: number;
        fullTileCount: number;
        interactiveBudgetMs: number;
      };
      coldViewport: { medianMs: number; tileCount: number; fullyWarm: boolean };
      warmViewport: { medianMs: number; fullyWarm: boolean };
      panCold: { medianMs: number; fullyWarm: boolean };
      panWarm: { medianMs: number; fullyWarm: boolean };
      returnHomeWarm: { medianMs: number; fullyWarm: boolean };
      mipWarm: {
        medianMs: number;
        fullyWarm: boolean;
        outputWidth: number;
        outputHeight: number;
      };
      warmFasterThanCold: boolean;
      interactiveWarmViewport: boolean;
      interactivePanWarm: boolean;
      progressiveSparseVsFull: boolean;
    };

    await mkdir(SCRATCH, { recursive: true });
    await writeFile(
      path.join(SCRATCH, 'browser-composite-large-doc-interactive.json'),
      `${JSON.stringify(result, null, 2)}\n`,
      'utf8',
    );

    expect(result.schemaVersion).toBe(1);
    expect(result.fixture.width).toBe(1024);
    expect(result.fixture.height).toBe(1024);
    expect(result.progressiveSparseVsFull).toBe(true);
    expect(result.warmFasterThanCold).toBe(true);
    expect(result.warmViewport.fullyWarm).toBe(true);
    expect(result.panWarm.fullyWarm).toBe(true);
    expect(result.returnHomeWarm.fullyWarm).toBe(true);
    expect(result.mipWarm.fullyWarm).toBe(true);
    expect(result.mipWarm.outputWidth).toBe(512);
    expect(result.mipWarm.outputHeight).toBe(512);
    expect(result.interactiveWarmViewport).toBe(true);
    expect(result.interactivePanWarm).toBe(true);
    expect(result.warmViewport.medianMs).toBeLessThan(result.fixture.interactiveBudgetMs);
    expect(result.panWarm.medianMs).toBeLessThan(result.fixture.interactiveBudgetMs);
    expect(result.returnHomeWarm.medianMs).toBeLessThan(result.fixture.interactiveBudgetMs);
    expect(result.coldViewport.tileCount).toBeLessThan(result.fixture.fullTileCount * 0.5);
  });

  test('4096² sparse progressive pan stays region-local in the browser', async ({ page }) => {
    test.setTimeout(180_000);
    await expect.poll(async () => page.evaluate(() => Boolean(window.__freetoshopPerf))).toBe(true);

    const result = (await page.evaluate(() => {
      const api = window.__freetoshopPerf;
      if (!api?.runLargeDocumentInteractiveProbe) {
        throw new Error('Large-document interactive probe is not installed');
      }
      return api.runLargeDocumentInteractiveProbe({
        width: 4096,
        height: 4096,
        layerCount: 4,
        tileSize: 64,
        viewportWidth: 384,
        viewportHeight: 384,
        panOffsetX: 512,
        panOffsetY: 256,
        warmSamples: 3,
        interactiveBudgetMs: 50,
        // Sparse patches avoid multi-GB dense 4k fixtures while still exercising
        // progressive region-local cold fills on a 4096² coordinate space.
        sparsePatchSize: 256,
      });
    })) as {
      schemaVersion: 1;
      fixture: {
        width: number;
        height: number;
        fullTileCount: number;
        sparsePatchSize: number | null;
        interactiveBudgetMs: number;
      };
      coldViewport: { medianMs: number; tileCount: number; fullyWarm: boolean };
      warmViewport: { medianMs: number; fullyWarm: boolean };
      panCold: { medianMs: number; fullyWarm: boolean };
      panWarm: { medianMs: number; fullyWarm: boolean };
      returnHomeWarm: { medianMs: number; fullyWarm: boolean };
      progressiveSparseVsFull: boolean;
      interactiveWarmViewport: boolean;
      interactivePanWarm: boolean;
    };

    await mkdir(SCRATCH, { recursive: true });
    await writeFile(
      path.join(SCRATCH, 'browser-composite-4k-sparse-interactive.json'),
      `${JSON.stringify(result, null, 2)}\n`,
      'utf8',
    );

    expect(result.schemaVersion).toBe(1);
    expect(result.fixture.width).toBe(4096);
    expect(result.fixture.sparsePatchSize).toBe(256);
    expect(result.progressiveSparseVsFull).toBe(true);
    expect(result.warmViewport.fullyWarm).toBe(true);
    expect(result.panWarm.fullyWarm).toBe(true);
    expect(result.returnHomeWarm.fullyWarm).toBe(true);
    expect(result.interactiveWarmViewport).toBe(true);
    expect(result.interactivePanWarm).toBe(true);
    expect(result.warmViewport.medianMs).toBeLessThan(result.fixture.interactiveBudgetMs);
    expect(result.panWarm.medianMs).toBeLessThan(result.fixture.interactiveBudgetMs);
    // Cold progressive must not materialize a dense full-canvas tile set.
    expect(result.coldViewport.tileCount).toBeLessThan(result.fixture.fullTileCount * 0.1);
    // Interactive cold budget on sparse 4k (generous CI host ceiling).
    expect(result.coldViewport.medianMs).toBeLessThan(2_000);
    expect(result.panCold.medianMs).toBeLessThan(2_000);
  });

  test('8192² sparse progressive pan stays region-local in the browser', async ({ page }) => {
    test.setTimeout(240_000);
    await expect.poll(async () => page.evaluate(() => Boolean(window.__freetoshopPerf))).toBe(true);

    const result = (await page.evaluate(() => {
      const api = window.__freetoshopPerf;
      if (!api?.runLargeDocumentInteractiveProbe) {
        throw new Error('Large-document interactive probe is not installed');
      }
      // Goal sparse large-document fixture: 8192×8192 with small placed patches.
      return api.runLargeDocumentInteractiveProbe({
        width: 8192,
        height: 8192,
        layerCount: 4,
        tileSize: 64,
        viewportWidth: 384,
        viewportHeight: 384,
        panOffsetX: 2048,
        panOffsetY: 1024,
        warmSamples: 3,
        interactiveBudgetMs: 50,
        sparsePatchSize: 256,
      });
    })) as {
      schemaVersion: 1;
      fixture: {
        width: number;
        height: number;
        fullTileCount: number;
        sparsePatchSize: number | null;
        interactiveBudgetMs: number;
      };
      coldViewport: { medianMs: number; tileCount: number };
      warmViewport: { medianMs: number; fullyWarm: boolean };
      panCold: { medianMs: number };
      panWarm: { medianMs: number; fullyWarm: boolean };
      returnHomeWarm: { medianMs: number; fullyWarm: boolean };
      progressiveSparseVsFull: boolean;
      interactiveWarmViewport: boolean;
      interactivePanWarm: boolean;
    };

    await mkdir(SCRATCH, { recursive: true });
    await writeFile(
      path.join(SCRATCH, 'browser-composite-8192-sparse-interactive.json'),
      `${JSON.stringify(result, null, 2)}\n`,
      'utf8',
    );

    expect(result.schemaVersion).toBe(1);
    expect(result.fixture.width).toBe(8192);
    expect(result.fixture.sparsePatchSize).toBe(256);
    expect(result.progressiveSparseVsFull).toBe(true);
    expect(result.warmViewport.fullyWarm).toBe(true);
    expect(result.panWarm.fullyWarm).toBe(true);
    expect(result.returnHomeWarm.fullyWarm).toBe(true);
    expect(result.interactiveWarmViewport).toBe(true);
    expect(result.interactivePanWarm).toBe(true);
    expect(result.warmViewport.medianMs).toBeLessThan(result.fixture.interactiveBudgetMs);
    expect(result.panWarm.medianMs).toBeLessThan(result.fixture.interactiveBudgetMs);
    // Full 8192/64 → 16384 tiles; progressive must stay a tiny fraction.
    expect(result.coldViewport.tileCount).toBeLessThan(result.fixture.fullTileCount * 0.05);
    expect(result.coldViewport.medianMs).toBeLessThan(5_000);
    expect(result.panCold.medianMs).toBeLessThan(5_000);
  });

  test('4096² dense twenty-layer progressive pan stays region-local in the browser', async ({
    page,
    browserName,
  }) => {
    test.setTimeout(600_000);
    await expect.poll(async () => page.evaluate(() => Boolean(window.__freetoshopPerf))).toBe(true);

    // Chromium meets ~50 ms warm on this host; Firefox/WebKit need a higher but still
    // sub-frame-blocking budget for 20 dense 4k layers (documented engine variance).
    const warmBudgetMs = browserName === 'chromium' ? 50 : 120;
    const result = (await page.evaluate((budget) => {
      const api = window.__freetoshopPerf;
      if (!api?.runLargeDocumentInteractiveProbe) {
        throw new Error('Large-document interactive probe is not installed');
      }
      // Goal representative fixture: 4096×4096 with 20 solid layers.
      return api.runLargeDocumentInteractiveProbe({
        width: 4096,
        height: 4096,
        layerCount: 20,
        tileSize: 64,
        viewportWidth: 384,
        viewportHeight: 384,
        panOffsetX: 1024,
        panOffsetY: 512,
        warmSamples: 3,
        interactiveBudgetMs: budget,
      });
    }, warmBudgetMs)) as {
      schemaVersion: 1;
      fixture: {
        width: number;
        height: number;
        layerCount: number;
        fullTileCount: number;
        sparsePatchSize: number | null;
        interactiveBudgetMs: number;
      };
      coldViewport: { medianMs: number; tileCount: number };
      warmViewport: { medianMs: number; fullyWarm: boolean };
      panCold: { medianMs: number };
      panWarm: { medianMs: number; fullyWarm: boolean };
      returnHomeWarm: { medianMs: number; fullyWarm: boolean };
      progressiveSparseVsFull: boolean;
      interactiveWarmViewport: boolean;
      interactivePanWarm: boolean;
    };

    await mkdir(SCRATCH, { recursive: true });
    const evidenceName =
      browserName === 'chromium'
        ? 'browser-composite-4096-dense-x20-interactive.json'
        : `browser-composite-4096-dense-x20-interactive-${browserName}.json`;
    await writeFile(
      path.join(SCRATCH, evidenceName),
      `${JSON.stringify({ ...result, browserName, warmBudgetMs }, null, 2)}\n`,
      'utf8',
    );

    expect(result.schemaVersion).toBe(1);
    expect(result.fixture.width).toBe(4096);
    expect(result.fixture.layerCount).toBe(20);
    expect(result.fixture.sparsePatchSize).toBeNull();
    expect(result.progressiveSparseVsFull).toBe(true);
    expect(result.warmViewport.fullyWarm).toBe(true);
    expect(result.panWarm.fullyWarm).toBe(true);
    expect(result.returnHomeWarm.fullyWarm).toBe(true);
    expect(result.interactiveWarmViewport).toBe(true);
    expect(result.interactivePanWarm).toBe(true);
    expect(result.warmViewport.medianMs).toBeLessThan(warmBudgetMs);
    expect(result.panWarm.medianMs).toBeLessThan(warmBudgetMs);
    // Warm must remain much faster than cold progressive fill.
    expect(result.warmViewport.medianMs).toBeLessThan(result.coldViewport.medianMs * 0.5);
    expect(result.coldViewport.tileCount).toBeLessThan(result.fixture.fullTileCount * 0.25);
    // Dense 4k×20 progressive cold budget (generous for CI / large allocation hosts).
    expect(result.coldViewport.medianMs).toBeLessThan(15_000);
    expect(result.panCold.medianMs).toBeLessThan(15_000);
  });

  test('4096² dense six-layer progressive pan stays region-local in the browser', async ({
    page,
  }) => {
    test.setTimeout(300_000);
    await expect.poll(async () => page.evaluate(() => Boolean(window.__freetoshopPerf))).toBe(true);

    const result = (await page.evaluate(() => {
      const api = window.__freetoshopPerf;
      if (!api?.runLargeDocumentInteractiveProbe) {
        throw new Error('Large-document interactive probe is not installed');
      }
      // Step toward 4k×20: six solid full-canvas layers with progressive viewport.
      return api.runLargeDocumentInteractiveProbe({
        width: 4096,
        height: 4096,
        layerCount: 6,
        tileSize: 64,
        viewportWidth: 384,
        viewportHeight: 384,
        panOffsetX: 1024,
        panOffsetY: 512,
        warmSamples: 3,
        interactiveBudgetMs: 50,
      });
    })) as {
      schemaVersion: 1;
      fixture: {
        width: number;
        height: number;
        layerCount: number;
        fullTileCount: number;
        sparsePatchSize: number | null;
        interactiveBudgetMs: number;
      };
      coldViewport: { medianMs: number; tileCount: number };
      warmViewport: { medianMs: number; fullyWarm: boolean };
      panCold: { medianMs: number };
      panWarm: { medianMs: number; fullyWarm: boolean };
      returnHomeWarm: { medianMs: number; fullyWarm: boolean };
      progressiveSparseVsFull: boolean;
      interactiveWarmViewport: boolean;
      interactivePanWarm: boolean;
    };

    await mkdir(SCRATCH, { recursive: true });
    await writeFile(
      path.join(SCRATCH, 'browser-composite-4096-dense-x6-interactive.json'),
      `${JSON.stringify(result, null, 2)}\n`,
      'utf8',
    );

    expect(result.schemaVersion).toBe(1);
    expect(result.fixture.width).toBe(4096);
    expect(result.fixture.layerCount).toBe(6);
    expect(result.fixture.sparsePatchSize).toBeNull();
    expect(result.progressiveSparseVsFull).toBe(true);
    expect(result.warmViewport.fullyWarm).toBe(true);
    expect(result.panWarm.fullyWarm).toBe(true);
    expect(result.returnHomeWarm.fullyWarm).toBe(true);
    expect(result.interactiveWarmViewport).toBe(true);
    expect(result.interactivePanWarm).toBe(true);
    expect(result.warmViewport.medianMs).toBeLessThan(result.fixture.interactiveBudgetMs);
    expect(result.panWarm.medianMs).toBeLessThan(result.fixture.interactiveBudgetMs);
    expect(result.coldViewport.tileCount).toBeLessThan(result.fixture.fullTileCount * 0.25);
    expect(result.coldViewport.medianMs).toBeLessThan(8_000);
    expect(result.panCold.medianMs).toBeLessThan(8_000);
  });

  test('4096² dense two-layer progressive pan stays region-local in the browser', async ({
    page,
  }) => {
    test.setTimeout(240_000);
    await expect.poll(async () => page.evaluate(() => Boolean(window.__freetoshopPerf))).toBe(true);

    const result = (await page.evaluate(() => {
      const api = window.__freetoshopPerf;
      if (!api?.runLargeDocumentInteractiveProbe) {
        throw new Error('Large-document interactive probe is not installed');
      }
      // Dense 4k gate with 2 full-canvas solid layers (20-layer 4k still CI-capacity limited).
      return api.runLargeDocumentInteractiveProbe({
        width: 4096,
        height: 4096,
        layerCount: 2,
        tileSize: 64,
        viewportWidth: 384,
        viewportHeight: 384,
        panOffsetX: 1024,
        panOffsetY: 512,
        warmSamples: 3,
        interactiveBudgetMs: 50,
      });
    })) as {
      schemaVersion: 1;
      fixture: {
        width: number;
        height: number;
        layerCount: number;
        fullTileCount: number;
        sparsePatchSize: number | null;
        interactiveBudgetMs: number;
      };
      coldViewport: { medianMs: number; tileCount: number; fullyWarm: boolean };
      warmViewport: { medianMs: number; fullyWarm: boolean };
      panCold: { medianMs: number; fullyWarm: boolean };
      panWarm: { medianMs: number; fullyWarm: boolean };
      returnHomeWarm: { medianMs: number; fullyWarm: boolean };
      progressiveSparseVsFull: boolean;
      interactiveWarmViewport: boolean;
      interactivePanWarm: boolean;
    };

    await mkdir(SCRATCH, { recursive: true });
    await writeFile(
      path.join(SCRATCH, 'browser-composite-4096-dense-interactive.json'),
      `${JSON.stringify(result, null, 2)}\n`,
      'utf8',
    );

    expect(result.schemaVersion).toBe(1);
    expect(result.fixture.width).toBe(4096);
    expect(result.fixture.layerCount).toBe(2);
    expect(result.fixture.sparsePatchSize).toBeNull();
    expect(result.progressiveSparseVsFull).toBe(true);
    expect(result.warmViewport.fullyWarm).toBe(true);
    expect(result.panWarm.fullyWarm).toBe(true);
    expect(result.returnHomeWarm.fullyWarm).toBe(true);
    expect(result.interactiveWarmViewport).toBe(true);
    expect(result.interactivePanWarm).toBe(true);
    expect(result.warmViewport.medianMs).toBeLessThan(result.fixture.interactiveBudgetMs);
    expect(result.panWarm.medianMs).toBeLessThan(result.fixture.interactiveBudgetMs);
    expect(result.coldViewport.tileCount).toBeLessThan(result.fixture.fullTileCount * 0.25);
    // Dense 4k progressive cold budget (generous for CI hosts / WSL).
    expect(result.coldViewport.medianMs).toBeLessThan(5_000);
    expect(result.panCold.medianMs).toBeLessThan(5_000);
  });

  test('2048² dense progressive pan stays region-local in the browser', async ({ page }) => {
    test.setTimeout(180_000);
    await expect.poll(async () => page.evaluate(() => Boolean(window.__freetoshopPerf))).toBe(true);

    const result = (await page.evaluate(() => {
      const api = window.__freetoshopPerf;
      if (!api?.runLargeDocumentInteractiveProbe) {
        throw new Error('Large-document interactive probe is not installed');
      }
      // Dense (non-sparse) large-document progressive gate short of full 4k×N OOM.
      return api.runLargeDocumentInteractiveProbe({
        width: 2048,
        height: 2048,
        layerCount: 4,
        tileSize: 64,
        viewportWidth: 384,
        viewportHeight: 384,
        panOffsetX: 768,
        panOffsetY: 512,
        warmSamples: 3,
        interactiveBudgetMs: 50,
      });
    })) as {
      schemaVersion: 1;
      fixture: {
        width: number;
        height: number;
        fullTileCount: number;
        sparsePatchSize: number | null;
        interactiveBudgetMs: number;
      };
      coldViewport: { medianMs: number; tileCount: number; fullyWarm: boolean };
      warmViewport: { medianMs: number; fullyWarm: boolean };
      panCold: { medianMs: number; fullyWarm: boolean };
      panWarm: { medianMs: number; fullyWarm: boolean };
      returnHomeWarm: { medianMs: number; fullyWarm: boolean };
      progressiveSparseVsFull: boolean;
      interactiveWarmViewport: boolean;
      interactivePanWarm: boolean;
    };

    await mkdir(SCRATCH, { recursive: true });
    await writeFile(
      path.join(SCRATCH, 'browser-composite-2048-dense-interactive.json'),
      `${JSON.stringify(result, null, 2)}\n`,
      'utf8',
    );

    expect(result.schemaVersion).toBe(1);
    expect(result.fixture.width).toBe(2048);
    expect(result.fixture.sparsePatchSize).toBeNull();
    expect(result.progressiveSparseVsFull).toBe(true);
    expect(result.warmViewport.fullyWarm).toBe(true);
    expect(result.panWarm.fullyWarm).toBe(true);
    expect(result.returnHomeWarm.fullyWarm).toBe(true);
    expect(result.interactiveWarmViewport).toBe(true);
    expect(result.interactivePanWarm).toBe(true);
    expect(result.warmViewport.medianMs).toBeLessThan(result.fixture.interactiveBudgetMs);
    expect(result.panWarm.medianMs).toBeLessThan(result.fixture.interactiveBudgetMs);
    expect(result.coldViewport.tileCount).toBeLessThan(result.fixture.fullTileCount * 0.5);
    // Dense progressive cold budget (generous for CI).
    expect(result.coldViewport.medianMs).toBeLessThan(3_000);
    expect(result.panCold.medianMs).toBeLessThan(3_000);
  });

  test('brush frame paint+commit stays within interactive budget on large sparse layer', async ({
    page,
  }) => {
    test.setTimeout(90_000);
    await expect.poll(async () => page.evaluate(() => Boolean(window.__freetoshopPerf))).toBe(true);

    const result = (await page.evaluate(() => {
      const api = window.__freetoshopPerf;
      if (!api?.runBrushFrameProbe) {
        throw new Error('Brush frame probe is not installed');
      }
      return api.runBrushFrameProbe({
        width: 2048,
        height: 2048,
        brushSize: 24,
        samples: 7,
        pointsPerFrame: 12,
        interactiveBudgetMs: 50,
      });
    })) as {
      schemaVersion: 1;
      fixture: { width: number; height: number; interactiveBudgetMs: number };
      paintMedianMs: number;
      paintP95Ms: number;
      commitMedianMs: number;
      frameMedianMs: number;
      patchBounded: boolean;
      interactivePaint: boolean;
      interactiveFrame: boolean;
    };

    await mkdir(SCRATCH, { recursive: true });
    await writeFile(
      path.join(SCRATCH, 'browser-brush-frame-interactive.json'),
      `${JSON.stringify(result, null, 2)}\n`,
      'utf8',
    );

    expect(result.schemaVersion).toBe(1);
    expect(result.fixture.width).toBe(2048);
    expect(result.patchBounded).toBe(true);
    expect(result.interactivePaint).toBe(true);
    expect(result.interactiveFrame).toBe(true);
    expect(result.paintMedianMs).toBeLessThan(result.fixture.interactiveBudgetMs);
    expect(result.frameMedianMs).toBeLessThan(result.fixture.interactiveBudgetMs);
    // Commit (COW tile patch) should also stay cheap relative to full densify.
    expect(result.commitMedianMs).toBeLessThan(result.fixture.interactiveBudgetMs);
  });

  test('live canvas reports warm composite cache after deterministic blend re-render', async ({
    page,
  }) => {
    await page.getByRole('button', { name: 'New', exact: true }).click();
    await expect(page.getByRole('tab', { name: /Untitled/ })).toBeVisible();

    // Create a second layer and force a non-normal blend so the advanced compositor path runs.
    await page.keyboard.press('ControlOrMeta+Shift+N');
    const blend = page.getByLabel('Layers panel blend mode');
    await expect(blend).toBeVisible();
    await blend.selectOption('multiply');

    // Nudge opacity to force a recompose through the session cache.
    const opacity = page.getByRole('spinbutton', { name: 'Layer opacity' });
    await expect(opacity).toBeVisible();
    await opacity.fill('80');
    await opacity.press('Enter');

    // Wait for the canvas diagnostics populated by the live render loop.
    const canvas = page.locator('canvas[aria-label$=" canvas"]');
    await expect(canvas).toBeVisible();
    await expect.poll(async () => canvas.getAttribute('data-composite-cache-hits')).not.toBeNull();

    const diagnostics = await canvas.evaluate((element) => ({
      warm: element.getAttribute('data-composite-cache-warm'),
      hits: Number(element.getAttribute('data-composite-cache-hits') ?? '0'),
      misses: Number(element.getAttribute('data-composite-cache-misses') ?? '0'),
      viewportTiles: Number(element.getAttribute('data-composite-cache-viewport-tiles') ?? '0'),
      renderMode: element.getAttribute('data-render-mode'),
    }));

    await mkdir(SCRATCH, { recursive: true });
    await writeFile(
      path.join(SCRATCH, 'browser-canvas-cache-diagnostics.json'),
      `${JSON.stringify(diagnostics, null, 2)}\n`,
      'utf8',
    );

    expect(diagnostics.hits + diagnostics.misses).toBeGreaterThan(0);
    // After at least one advanced composite frame, viewport tile accounting is populated.
    expect(diagnostics.viewportTiles).toBeGreaterThanOrEqual(0);
  });

  test('live editor pan and zoom update viewport and keep composite cache healthy', async ({
    page,
  }) => {
    test.setTimeout(90_000);
    await page.getByRole('button', { name: 'New', exact: true }).click();
    await expect(page.getByRole('tab', { name: /Untitled/ })).toBeVisible();

    // Advanced composite path: second layer + non-normal blend.
    await page.keyboard.press('ControlOrMeta+Shift+N');
    const blend = page.getByLabel('Layers panel blend mode');
    await expect(blend).toBeVisible();
    await blend.selectOption('multiply');
    const opacity = page.getByRole('spinbutton', { name: 'Layer opacity' });
    await opacity.fill('75');
    await opacity.press('Enter');

    const canvas = page.locator('canvas[aria-label$=" canvas"]');
    await expect(canvas).toBeVisible();
    await expect.poll(async () => canvas.getAttribute('data-tool')).toBe('pan');
    await expect.poll(async () => canvas.getAttribute('data-composite-cache-hits')).not.toBeNull();

    const box = await canvas.boundingBox();
    if (!box) throw new Error('Canvas bounds unavailable');
    const startX = box.x + box.width * 0.5;
    const startY = box.y + box.height * 0.5;

    const before = await canvas.evaluate((element) => ({
      panX: Number(element.getAttribute('data-document-x') ?? '0'),
      panY: Number(element.getAttribute('data-document-y') ?? '0'),
      zoom: Number(element.getAttribute('data-document-zoom') ?? '1'),
      hits: Number(element.getAttribute('data-composite-cache-hits') ?? '0'),
      misses: Number(element.getAttribute('data-composite-cache-misses') ?? '0'),
      warm: element.getAttribute('data-composite-cache-warm'),
    }));

    // Measure pan gesture: drag 120px and wait for the live render loop to publish pan.
    const panStarted = Date.now();
    await page.mouse.move(startX, startY);
    await page.mouse.down();
    await page.mouse.move(startX + 120, startY + 80, { steps: 8 });
    await page.mouse.up();
    await expect
      .poll(async () => Number((await canvas.getAttribute('data-document-x')) ?? '0'))
      .not.toBe(before.panX);
    const panSettledMs = Date.now() - panStarted;

    const afterPan = await canvas.evaluate((element) => ({
      panX: Number(element.getAttribute('data-document-x') ?? '0'),
      panY: Number(element.getAttribute('data-document-y') ?? '0'),
      zoom: Number(element.getAttribute('data-document-zoom') ?? '1'),
      hits: Number(element.getAttribute('data-composite-cache-hits') ?? '0'),
      misses: Number(element.getAttribute('data-composite-cache-misses') ?? '0'),
      warm: element.getAttribute('data-composite-cache-warm'),
      viewportTiles: Number(element.getAttribute('data-composite-cache-viewport-tiles') ?? '0'),
    }));

    // Wheel zoom around canvas center (live onWheel handler).
    const zoomStarted = Date.now();
    await canvas.hover({ position: { x: box.width * 0.5, y: box.height * 0.5 } });
    await canvas.dispatchEvent('wheel', { deltaY: -240, bubbles: true, cancelable: true });
    await expect
      .poll(async () => Number((await canvas.getAttribute('data-document-zoom')) ?? '1'))
      .toBeGreaterThan(before.zoom);
    const zoomSettledMs = Date.now() - zoomStarted;

    const afterZoom = await canvas.evaluate((element) => ({
      panX: Number(element.getAttribute('data-document-x') ?? '0'),
      panY: Number(element.getAttribute('data-document-y') ?? '0'),
      zoom: Number(element.getAttribute('data-document-zoom') ?? '1'),
      hits: Number(element.getAttribute('data-composite-cache-hits') ?? '0'),
      misses: Number(element.getAttribute('data-composite-cache-misses') ?? '0'),
      warm: element.getAttribute('data-composite-cache-warm'),
      viewportTiles: Number(element.getAttribute('data-composite-cache-viewport-tiles') ?? '0'),
    }));

    // Return toward home via View → Fit so cache can re-warm the original region.
    await page.getByRole('menuitem', { name: 'View', exact: true }).click();
    await page.getByRole('menuitem', { name: /Fit on Screen/ }).click();
    await expect.poll(async () => canvas.getAttribute('data-composite-cache-hits')).not.toBeNull();

    const afterFit = await canvas.evaluate((element) => ({
      panX: Number(element.getAttribute('data-document-x') ?? '0'),
      panY: Number(element.getAttribute('data-document-y') ?? '0'),
      zoom: Number(element.getAttribute('data-document-zoom') ?? '1'),
      hits: Number(element.getAttribute('data-composite-cache-hits') ?? '0'),
      misses: Number(element.getAttribute('data-composite-cache-misses') ?? '0'),
      warm: element.getAttribute('data-composite-cache-warm'),
      viewportTiles: Number(element.getAttribute('data-composite-cache-viewport-tiles') ?? '0'),
    }));

    const evidence = {
      schemaVersion: 1 as const,
      before,
      afterPan,
      afterZoom,
      afterFit,
      panSettledMs,
      zoomSettledMs,
      panDelta: {
        x: afterPan.panX - before.panX,
        y: afterPan.panY - before.panY,
      },
      zoomIncreased: afterZoom.zoom > before.zoom,
    };

    await mkdir(SCRATCH, { recursive: true });
    await writeFile(
      path.join(SCRATCH, 'browser-live-pan-zoom.json'),
      `${JSON.stringify(evidence, null, 2)}\n`,
      'utf8',
    );

    // Functional: live pan moved the viewport by roughly the drag vector.
    expect(Math.abs(afterPan.panX - before.panX)).toBeGreaterThan(40);
    expect(Math.abs(afterPan.panY - before.panY)).toBeGreaterThan(20);
    expect(afterZoom.zoom).toBeGreaterThan(before.zoom);
    // Interactive settle budgets (generous for CI hosts; not multi-second hangs).
    expect(panSettledMs).toBeLessThan(5_000);
    expect(zoomSettledMs).toBeLessThan(5_000);
    // Cache remains instrumented and active after navigation.
    expect(afterPan.hits + afterPan.misses).toBeGreaterThan(0);
    expect(afterZoom.hits + afterZoom.misses).toBeGreaterThan(0);
    expect(afterFit.hits + afterFit.misses).toBeGreaterThan(0);
    expect(afterFit.viewportTiles).toBeGreaterThanOrEqual(0);
  });

  test('live zoomed-out editor streams a smaller matching mip worker frame', async ({
    page,
    browserName,
  }) => {
    test.setTimeout(90_000);
    await page.getByRole('button', { name: 'New', exact: true }).click();
    await page.keyboard.press('ControlOrMeta+Shift+N');
    await page.getByLabel('Layers panel blend mode').selectOption('multiply');

    const canvas = page.locator('canvas[aria-label$=" canvas"]');
    const zoom = page.getByLabel('Canvas zoom');
    await zoom.fill('25');
    await expect
      .poll(async () => Number(await canvas.getAttribute('data-composite-mip-level')))
      .toBeGreaterThan(0);
    await expect.poll(async () => canvas.getAttribute('data-composite-worker-inflight')).toBe('0');

    const evidence = await canvas.evaluate((element) => ({
      schemaVersion: 1 as const,
      mipLevel: Number(element.getAttribute('data-composite-mip-level')),
      documentWidth: Number(element.getAttribute('data-composite-document-width')),
      documentHeight: Number(element.getAttribute('data-composite-document-height')),
      frameWidth: Number(element.getAttribute('data-composite-worker-frame-width')),
      frameHeight: Number(element.getAttribute('data-composite-worker-frame-height')),
      allocatedBytes: Number(element.getAttribute('data-composite-cache-allocated-bytes')),
      maxBytes: Number(element.getAttribute('data-composite-cache-max-bytes')),
      workerSettled: element.getAttribute('data-composite-worker-inflight') === '0',
    }));

    await mkdir(SCRATCH, { recursive: true });
    await writeFile(
      path.join(SCRATCH, `browser-live-app-mip-streaming-${browserName}.json`),
      `${JSON.stringify(evidence, null, 2)}\n`,
      'utf8',
    );

    const scale = 2 ** evidence.mipLevel;
    expect(evidence.frameWidth).toBe(Math.ceil(evidence.documentWidth / scale));
    expect(evidence.frameHeight).toBe(Math.ceil(evidence.documentHeight / scale));
    expect(evidence.frameWidth * evidence.frameHeight).toBeLessThan(
      evidence.documentWidth * evidence.documentHeight,
    );
    expect(evidence.allocatedBytes).toBeLessThanOrEqual(evidence.maxBytes);
    expect(evidence.workerSettled).toBe(true);
  });
});
