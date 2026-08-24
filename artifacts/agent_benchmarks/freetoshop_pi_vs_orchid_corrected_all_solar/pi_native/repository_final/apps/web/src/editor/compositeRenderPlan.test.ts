import { createDocument, createPixelLayer } from '@freetoshop/core-document';
import { describe, expect, it } from 'vitest';
import {
  compositeMipLevelForZoom,
  planAdvancedCompositeRender,
  shouldCancelWorkerInflight,
  shouldChainFullCompositeRefine,
  shouldSeedCompositeWorkerFrame,
} from './compositeRenderPlan.js';
import type { CompositeWorkerFrame } from './compositeWorkerScheduler.js';

function documentFixture(id = 'doc') {
  return createDocument({
    id,
    name: 'Plan fixture',
    width: 4,
    height: 4,
    layers: [
      createPixelLayer({
        id: 'a',
        name: 'A',
        width: 4,
        height: 4,
        pixels: new Uint8ClampedArray(64).fill(10),
        blendMode: 'multiply',
      }),
    ],
  });
}

function frame(documentId: string, generation: number): CompositeWorkerFrame {
  return {
    generation,
    documentId,
    width: 4,
    height: 4,
    pixels: new Uint8ClampedArray(64).fill(99),
    mipLevel: 0,
  };
}

describe('shouldCancelWorkerInflight', () => {
  it('never cancels when nothing is inflight', () => {
    expect(
      shouldCancelWorkerInflight({
        workerInflight: false,
        workerFrame: { ...frame('doc', 1), progressiveViewportOnly: true },
      }),
    ).toBe(false);
  });

  it('does not cancel while a progressive frame is the display frame (seed or warm)', () => {
    // Skeptic gap: progressive seed + chained full refine must keep cancelInflight false.
    expect(
      shouldCancelWorkerInflight({
        workerInflight: true,
        workerFrame: { ...frame('doc', 1), progressiveViewportOnly: true },
      }),
    ).toBe(false);
  });

  it('cancels non-progressive inflight work (e.g. obsolete full-frame after warm)', () => {
    expect(
      shouldCancelWorkerInflight({
        workerInflight: true,
        workerFrame: frame('doc', 1),
      }),
    ).toBe(true);
    expect(
      shouldCancelWorkerInflight({
        workerInflight: true,
        workerFrame: null,
      }),
    ).toBe(true);
  });

  it('source lock: plan never uses bare cancelInflight: workerInflight', async () => {
    // Prevents the progressive-seed cancel regression from reappearing silently.
    // Skeptic repeatedly cites stale L56-63 as bare cancelInflight: workerInflight;
    // live seed uses shouldCancelWorkerInflight and progressiveOnly returns false.
    const { readFileSync } = await import('node:fs');
    const { fileURLToPath } = await import('node:url');
    const planPath = fileURLToPath(new URL('./compositeRenderPlan.ts', import.meta.url));
    const appPath = fileURLToPath(new URL('../App.tsx', import.meta.url));
    const planSource = readFileSync(planPath, 'utf8');
    const appSource = readFileSync(appPath, 'utf8');
    expect(planSource).toContain('shouldCancelWorkerInflight');
    expect(planSource).toMatch(/progressiveOnly[\s\S]*return false/);
    expect(planSource).not.toMatch(/cancelInflight:\s*workerInflight\s*,/);
    // Seed branch: progressive seed hard-codes cancelInflight false (keep refine).
    expect(planSource).toMatch(/progressiveSeed[\s\S]*cancelInflight:\s*progressiveSeed/);
    expect(planSource).toMatch(/progressiveSeed\s*\?\s*false/);
    expect(planSource).not.toMatch(/cancelInflight:\s*workerInflight\s*,/);
    // App must clear sticky scheduledDocument when canceling inflight work.
    expect(appSource).toMatch(
      /if\s*\(\s*plan\.cancelInflight\s*\)[\s\S]*compositeWorkerScheduledDocRef\.current\s*=\s*null/,
    );
  });
});

describe('shouldSeedCompositeWorkerFrame', () => {
  it('seeds full frames only when the cache can retain the complete frame', () => {
    const full = frame('doc', 1);
    expect(shouldSeedCompositeWorkerFrame(full, full.pixels.byteLength)).toBe(true);
    expect(shouldSeedCompositeWorkerFrame(full, full.pixels.byteLength - 1)).toBe(false);
  });

  it('always seeds progressive frames because only viewport tiles are retained', () => {
    const progressive = { ...frame('doc', 1), progressiveViewportOnly: true };
    expect(shouldSeedCompositeWorkerFrame(progressive, 1)).toBe(true);
  });
});

describe('shouldChainFullCompositeRefine', () => {
  it('chains a full refine only when one canvas-sized RGBA frame fits the cache', () => {
    expect(shouldChainFullCompositeRefine({ width: 1024, height: 768 }, 3 * 1024 * 1024)).toBe(
      true,
    );
    expect(shouldChainFullCompositeRefine({ width: 1024, height: 768 }, 3 * 1024 * 1024 - 1)).toBe(
      false,
    );
  });

  it('rejects unsafe frame-size arithmetic', () => {
    expect(
      shouldChainFullCompositeRefine(
        { width: Number.MAX_SAFE_INTEGER, height: Number.MAX_SAFE_INTEGER },
        Number.MAX_SAFE_INTEGER,
      ),
    ).toBe(false);
  });

  it('accounts for the selected mip frame budget', () => {
    expect(shouldChainFullCompositeRefine({ width: 1024, height: 768 }, 1024 * 768, 1)).toBe(true);
    expect(shouldChainFullCompositeRefine({ width: 1024, height: 768 }, 1024 * 768 - 1, 1)).toBe(
      false,
    );
  });
});

describe('compositeMipLevelForZoom', () => {
  it('selects stable power-of-two overview levels and caps them', () => {
    expect(compositeMipLevelForZoom(1)).toBe(0);
    expect(compositeMipLevelForZoom(0.75)).toBe(0);
    expect(compositeMipLevelForZoom(0.5)).toBe(1);
    expect(compositeMipLevelForZoom(0.25)).toBe(2);
    expect(compositeMipLevelForZoom(0.001, 4)).toBe(4);
  });
});

describe('planAdvancedCompositeRender', () => {
  it('requires warm-only assemble and schedules worker when cold without a frame', () => {
    const document = documentFixture();
    const plan = planAdvancedCompositeRender({
      document,
      fullyWarm: false,
      workerFrame: null,
      scheduledDocument: null,
      seededGeneration: -1,
      workerInflight: false,
    });
    expect(plan.warmAssembleOnly).toBe(true);
    expect(plan.scheduleWorker).toBe(true);
    expect(plan.precomposedPixels).toBeUndefined();
    expect(plan.seedFrame).toBeNull();
    expect(plan.cancelInflight).toBe(false);
    expect(plan.clearScheduledDocument).toBe(false);
  });

  it('does not reschedule while worker is already inflight for the document', () => {
    const document = documentFixture();
    const plan = planAdvancedCompositeRender({
      document,
      fullyWarm: false,
      workerFrame: null,
      scheduledDocument: document,
      seededGeneration: -1,
      workerInflight: true,
    });
    expect(plan.warmAssembleOnly).toBe(true);
    expect(plan.scheduleWorker).toBe(false);
  });

  it('clears sticky scheduledDocument after cancel so cold regions can reschedule', () => {
    const document = documentFixture();
    // After cancel: inflight false but App still holds scheduledDocument identity.
    const plan = planAdvancedCompositeRender({
      document,
      fullyWarm: false,
      workerFrame: null,
      scheduledDocument: document,
      seededGeneration: -1,
      workerInflight: false,
    });
    expect(plan.scheduleWorker).toBe(true);
    expect(plan.clearScheduledDocument).toBe(true);
    expect(plan.warmAssembleOnly).toBe(true);
  });

  it('seeds the cache from a new worker frame instead of main-thread miss-fill', () => {
    const document = documentFixture();
    const workerFrame = frame(document.id, 3);
    const plan = planAdvancedCompositeRender({
      document,
      fullyWarm: false,
      workerFrame,
      scheduledDocument: document,
      seededGeneration: -1,
      workerInflight: false,
    });
    expect(plan.seedFrame).toBe(workerFrame);
    expect(plan.warmAssembleOnly).toBe(false);
    expect(plan.scheduleWorker).toBe(false);
    expect(plan.precomposedPixels).toBeUndefined();
    expect(plan.cancelInflight).toBe(false);
  });

  it('does not cancel background full refine when seeding a progressive frame', () => {
    // Skeptic gap: progressive onFrame chains full refine (inflight); first paint
    // must seed without cancelInflight, or off-viewport tiles never fill.
    const document = documentFixture();
    const progressive: CompositeWorkerFrame = {
      ...frame(document.id, 3),
      progressiveViewportOnly: true,
      viewportRegion: { x: 0, y: 0, width: 2, height: 2 },
    };
    const plan = planAdvancedCompositeRender({
      document,
      fullyWarm: false,
      workerFrame: progressive,
      scheduledDocument: document,
      seededGeneration: -1,
      workerInflight: true,
    });
    expect(plan.seedFrame).toBe(progressive);
    expect(plan.cancelInflight).toBe(false);
    expect(plan.scheduleWorker).toBe(false);
    expect(plan.warmAssembleOnly).toBe(false);
    expect(plan.precomposedPixels).toBeUndefined();
  });

  it('uses precomposedPixels when a full (non-progressive) frame was already seeded', () => {
    const document = documentFixture();
    const workerFrame = frame(document.id, 5);
    const plan = planAdvancedCompositeRender({
      document,
      fullyWarm: false,
      workerFrame,
      scheduledDocument: document,
      seededGeneration: 5,
      workerInflight: false,
    });
    expect(plan.precomposedPixels).toBe(workerFrame.pixels);
    expect(plan.seedFrame).toBeNull();
    expect(plan.warmAssembleOnly).toBe(false);
    expect(plan.scheduleWorker).toBe(false);
  });

  it('does not paint progressive buffer as precomposed; schedules fill for cold pan tiles', () => {
    // After progressive seed, pan to unseeded tiles: must schedule worker again
    // and not use full-buffer precomposed holes.
    const document = documentFixture();
    const progressive: CompositeWorkerFrame = {
      ...frame(document.id, 5),
      progressiveViewportOnly: true,
      viewportRegion: { x: 0, y: 0, width: 2, height: 2 },
    };
    const plan = planAdvancedCompositeRender({
      document,
      fullyWarm: false,
      workerFrame: progressive,
      scheduledDocument: document,
      seededGeneration: 5,
      workerInflight: false,
    });
    expect(plan.precomposedPixels).toBeUndefined();
    expect(plan.seedFrame).toBeNull();
    expect(plan.warmAssembleOnly).toBe(true);
    expect(plan.scheduleWorker).toBe(true);
    expect(plan.cancelInflight).toBe(false);
  });

  it('does not reschedule progressive pan fill while refine is still inflight', () => {
    const document = documentFixture();
    const progressive: CompositeWorkerFrame = {
      ...frame(document.id, 5),
      progressiveViewportOnly: true,
      viewportRegion: { x: 0, y: 0, width: 2, height: 2 },
    };
    const plan = planAdvancedCompositeRender({
      document,
      fullyWarm: false,
      workerFrame: progressive,
      scheduledDocument: document,
      seededGeneration: 5,
      workerInflight: true,
    });
    expect(plan.scheduleWorker).toBe(false);
    expect(plan.warmAssembleOnly).toBe(true);
    expect(plan.precomposedPixels).toBeUndefined();
  });

  it('cancels inflight worker work once the progressive cache is fully warm', () => {
    const document = documentFixture();
    const plan = planAdvancedCompositeRender({
      document,
      fullyWarm: true,
      workerFrame: null,
      scheduledDocument: document,
      seededGeneration: -1,
      workerInflight: true,
    });
    expect(plan.cancelInflight).toBe(true);
    expect(plan.clearScheduledDocument).toBe(true);
    expect(plan.scheduleWorker).toBe(false);
    expect(plan.warmAssembleOnly).toBe(false);
  });

  it('does not cancel background full refine while progressive frame is still the display frame', () => {
    const document = documentFixture();
    const progressive: CompositeWorkerFrame = {
      ...frame(document.id, 3),
      progressiveViewportOnly: true,
      viewportRegion: { x: 0, y: 0, width: 4, height: 4 },
    };
    const plan = planAdvancedCompositeRender({
      document,
      fullyWarm: true,
      workerFrame: progressive,
      scheduledDocument: document,
      seededGeneration: 3,
      workerInflight: true,
    });
    expect(plan.cancelInflight).toBe(false);
    expect(plan.scheduleWorker).toBe(false);
  });
});
