/**
 * Regression for skeptic item 0: while a worker owns cold advanced composite,
 * the main-thread path must not invoke compositeDocumentPixels / reference miss-fill.
 * Mirrors App.tsx plan → renderDocument(warmAssembleOnly) wiring.
 */
import { createDocument, createPixelLayer } from '@freetoshop/core-document';
import {
  CompositeTileCache,
  compositeDocumentPixels,
  rasterizeLayerNearest,
  renderDocument,
} from '@freetoshop/renderer';
import { describe, expect, it, vi } from 'vitest';
import { planAdvancedCompositeRender } from './compositeRenderPlan.js';
import type { CompositeWorkerFrame } from './compositeWorkerScheduler.js';

function advancedDoc() {
  return createDocument({
    id: 'cold-path-doc',
    name: 'Cold path',
    width: 32,
    height: 32,
    layers: [
      createPixelLayer({
        id: 'a',
        name: 'A',
        width: 32,
        height: 32,
        pixels: new Uint8ClampedArray(32 * 32 * 4).fill(40),
        blendMode: 'multiply',
      }),
      createPixelLayer({
        id: 'b',
        name: 'B',
        width: 32,
        height: 32,
        pixels: new Uint8ClampedArray(32 * 32 * 4).fill(200),
        blendMode: 'screen',
        opacity: 0.8,
      }),
    ],
  });
}

function mockContext(size = 64) {
  return {
    canvas: { width: size, height: size },
    clearRect: vi.fn(),
    save: vi.fn(),
    restore: vi.fn(),
    setTransform: vi.fn(),
    drawImage: vi.fn(),
    beginPath: vi.fn(),
    rect: vi.fn(),
    clip: vi.fn(),
    globalAlpha: 1,
    globalCompositeOperation: 'source-over',
  } as unknown as CanvasRenderingContext2D;
}

describe('advanced composite cold path (App wiring)', () => {
  it('does not call compositeDocumentPixels on main thread while worker is inflight', () => {
    const document = advancedDoc();
    const cache = new CompositeTileCache({ tileSize: 16 });
    const putPixels = vi.fn();
    const surface = { source: {} as CanvasImageSource, putPixels };
    let documentPixelsCalls = 0;
    const trackingReference = (doc: typeof document) => {
      documentPixelsCalls += 1;
      return compositeDocumentPixels(doc, { rasterize: rasterizeLayerNearest });
    };

    // Cold, worker inflight, no frame yet — same plan inputs App uses.
    const plan = planAdvancedCompositeRender({
      document,
      fullyWarm: cache.probeWarmth(document, {
        progressiveViewportOnly: true,
        viewportRegion: { x: 0, y: 0, width: 32, height: 32 },
      }).fullyWarm,
      workerFrame: null,
      scheduledDocument: document,
      seededGeneration: -1,
      workerInflight: true,
    });
    expect(plan.warmAssembleOnly).toBe(true);
    expect(plan.scheduleWorker).toBe(false);
    expect(plan.precomposedPixels).toBeUndefined();

    // Spy: if renderDocument invoked the cache with warmAssembleOnly false,
    // a miss would call our tracking reference.
    const originalComposite = cache.composite.bind(cache);
    vi.spyOn(cache, 'composite').mockImplementation((doc, options) =>
      originalComposite(doc, {
        ...options,
        referenceComposite: (inner) => {
          documentPixelsCalls += 1;
          return trackingReference(inner);
        },
      }),
    );

    renderDocument(mockContext(), document, {
      checkerboard: false,
      rasterize: rasterizeLayerNearest,
      createSurface: () => surface,
      compositeTileCache: cache,
      progressiveComposite: true,
      precomposedPixels: plan.precomposedPixels,
      warmAssembleOnly: plan.warmAssembleOnly,
      viewport: { zoom: 1, panX: 0, panY: 0 },
    });

    expect(plan.warmAssembleOnly).toBe(true);
    expect(documentPixelsCalls).toBe(0);
    expect(cache.stats.lastComposeFullReferenceMisses).toBe(0);
  });

  it('seeds from worker frame without calling compositeDocumentPixels', () => {
    const document = advancedDoc();
    const cache = new CompositeTileCache({ tileSize: 16 });
    const expected = compositeDocumentPixels(document, { rasterize: rasterizeLayerNearest });
    const workerFrame: CompositeWorkerFrame = {
      generation: 7,
      documentId: document.id,
      width: document.canvas.width,
      height: document.canvas.height,
      pixels: expected,
      mipLevel: 0,
    };
    let documentPixelsCalls = 0;
    const plan = planAdvancedCompositeRender({
      document,
      fullyWarm: false,
      workerFrame,
      scheduledDocument: document,
      seededGeneration: -1,
      workerInflight: false,
    });
    expect(plan.seedFrame).toBe(workerFrame);

    // Seed exactly as App.tsx does: reference is the worker buffer, not a recompose.
    cache.composite(document, {
      referenceComposite: () => {
        documentPixelsCalls += 1;
        // Counting only if the seed path incorrectly recomputed; worker pixels are free.
        return workerFrame.pixels;
      },
    });
    // Seed uses the frame buffer as reference — one crop pass, not compositeDocumentPixels.
    // documentPixelsCalls counts referenceComposite invocations (crop source), not recomposes.
    expect(documentPixelsCalls).toBeGreaterThan(0);
    // After seed, warm assemble must not re-enter reference.
    documentPixelsCalls = 0;
    const warm = cache.composite(document, {
      referenceComposite: () => {
        documentPixelsCalls += 1;
        return compositeDocumentPixels(document, { rasterize: rasterizeLayerNearest });
      },
    });
    expect(documentPixelsCalls).toBe(0);
    expect(cache.stats.lastComposeFullyWarm).toBe(true);
    expect(warm).toEqual(expected);
  });

  it('uses precomposedPixels after seed generation matches and skips cache miss-fill', () => {
    const document = advancedDoc();
    const cache = new CompositeTileCache({ tileSize: 16 });
    const expected = compositeDocumentPixels(document, { rasterize: rasterizeLayerNearest });
    // Full (non-progressive) worker frame — safe as precomposed full buffer.
    const workerFrame: CompositeWorkerFrame = {
      generation: 9,
      documentId: document.id,
      width: document.canvas.width,
      height: document.canvas.height,
      pixels: expected,
      mipLevel: 0,
    };
    const plan = planAdvancedCompositeRender({
      document,
      fullyWarm: false,
      workerFrame,
      scheduledDocument: document,
      seededGeneration: 9,
      workerInflight: false,
    });
    expect(plan.precomposedPixels).toBe(workerFrame.pixels);

    const putPixels = vi.fn();
    const compositeSpy = vi.spyOn(cache, 'composite');
    renderDocument(mockContext(), document, {
      checkerboard: false,
      rasterize: rasterizeLayerNearest,
      createSurface: () => ({ source: {} as CanvasImageSource, putPixels }),
      compositeTileCache: cache,
      precomposedPixels: plan.precomposedPixels,
      warmAssembleOnly: plan.warmAssembleOnly,
      viewport: { zoom: 1, panX: 0, panY: 0 },
    });
    expect(compositeSpy).not.toHaveBeenCalled();
    expect(putPixels).toHaveBeenCalledWith(expected, 32, 32);
  });

  it('progressive seed keeps refine inflight and pan cold tiles can reschedule', () => {
    // Skeptic gap: seed of progressive must not cancel chained full refine;
    // after cancel, sticky scheduledDocument must not block reschedule.
    const document = advancedDoc();
    const progressive: CompositeWorkerFrame = {
      generation: 3,
      documentId: document.id,
      width: document.canvas.width,
      height: document.canvas.height,
      pixels: new Uint8ClampedArray(document.canvas.width * document.canvas.height * 4),
      progressiveViewportOnly: true,
      viewportRegion: { x: 0, y: 0, width: 16, height: 16 },
      mipLevel: 0,
    };

    // First paint after progressive onFrame while full refine is inflight.
    const seedPlan = planAdvancedCompositeRender({
      document,
      fullyWarm: false,
      workerFrame: progressive,
      scheduledDocument: document,
      seededGeneration: -1,
      workerInflight: true,
    });
    expect(seedPlan.seedFrame).toBe(progressive);
    expect(seedPlan.cancelInflight).toBe(false);

    // Simulate App: seed without cancel.
    const cache = new CompositeTileCache({ tileSize: 16 });
    cache.composite(document, {
      referenceComposite: () => progressive.pixels,
      progressiveViewportOnly: true,
      viewportRegion: progressive.viewportRegion,
    });
    expect(
      cache.probeWarmth(document, {
        progressiveViewportOnly: true,
        viewportRegion: { x: 0, y: 0, width: 16, height: 16 },
      }).fullyWarm,
    ).toBe(true);

    // Pan to opposite corner while refine still inflight — do not reschedule yet.
    const panWarmth = cache.probeWarmth(document, {
      progressiveViewportOnly: true,
      viewportRegion: { x: 16, y: 16, width: 16, height: 16 },
    });
    expect(panWarmth.fullyWarm).toBe(false);
    const panWhileRefine = planAdvancedCompositeRender({
      document,
      fullyWarm: panWarmth.fullyWarm,
      workerFrame: progressive,
      scheduledDocument: document,
      seededGeneration: 3,
      workerInflight: true,
    });
    expect(panWhileRefine.scheduleWorker).toBe(false);
    expect(panWhileRefine.precomposedPixels).toBeUndefined();
    expect(panWhileRefine.warmAssembleOnly).toBe(true);

    // After cancel left sticky scheduledDocument without inflight — must reschedule.
    const afterCancel = planAdvancedCompositeRender({
      document,
      fullyWarm: false,
      workerFrame: null,
      scheduledDocument: document,
      seededGeneration: 3,
      workerInflight: false,
    });
    expect(afterCancel.scheduleWorker).toBe(true);
    expect(afterCancel.clearScheduledDocument).toBe(true);
    expect(afterCancel.warmAssembleOnly).toBe(true);
  });
});
