import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { initializeCanvas, type Psd } from 'ag-psd';
import { beforeAll, describe, expect, it, vi } from 'vitest';
import { importPsdBuffer } from './psd.js';
import { decodePsdPathResources } from './psdPathResources.js';
import { hasPsdSignature } from './signatures.js';

const fixtures = fileURLToPath(new URL('../test-fixtures/psd/', import.meta.url));

beforeAll(() => {
  const makeImageData = (width: number, height: number) =>
    ({ width, height, data: new Uint8ClampedArray(width * height * 4) }) as ImageData;
  initializeCanvas(
    (() => ({
      getContext: () => ({ createImageData: makeImageData }),
    })) as unknown as (width: number, height: number) => HTMLCanvasElement,
    makeImageData,
  );
});

function fixture(name: string): ArrayBuffer {
  const bytes = readFileSync(`${fixtures}${name}`);
  return bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer;
}

describe('PSD import', () => {
  it('imports an 8-bit RGB composite when no layer previews exist', () => {
    const result = importPsdBuffer(fixture('flattened-rgb-8.psd'), {
      documentId: 'flattened',
      name: 'flattened-rgb-8.psd',
    });
    expect(result.document).toMatchObject({
      id: 'flattened',
      name: 'flattened-rgb-8.psd',
      canvas: { width: 3, height: 2 },
    });
    expect(result.document.layers).toHaveLength(1);
    expect(result.document.layers[0]).toMatchObject({
      kind: 'pixel',
      name: 'PSD composite',
      width: 3,
      height: 2,
    });
    expect(result.report.warnings).toContain(
      'The PSD had no importable layer previews; its composite preview was used.',
    );
  });

  it('converts pixel layers to bottom-to-top document order with placement and blending', () => {
    const result = importPsdBuffer(fixture('pixel-layers-rgb-8.psd'));
    expect(result.document.layers.map((layer) => layer.name)).toEqual(['Bottom blue', 'Top red']);
    expect(result.document.layers[0]).toMatchObject({ kind: 'pixel', width: 4, height: 4 });
    expect(result.document.layers[1]).toMatchObject({
      kind: 'pixel',
      width: 2,
      height: 2,
      blendMode: 'multiply',
      transform: { e: 1, f: 1 },
    });
    expect(result.document.layers[1]?.opacity).toBeCloseTo(0.8, 2);
    expect(result.report).toMatchObject({ layerCount: 2, skippedLayers: 0 });
  });

  it('preserves nested group structure and pass-through compositing', () => {
    const result = importPsdBuffer(fixture('nested-group-rgb-8.psd'));
    expect(result.document.layers.map((layer) => layer.name)).toEqual([
      'Background',
      'Foreground group',
    ]);
    expect(result.document.layers[1]).toMatchObject({
      kind: 'group',
      compositing: 'pass-through',
      children: [{ name: 'Green square', kind: 'pixel' }],
    });
  });

  it('imports corpus fixture with linked smart object without injects', () => {
    const result = importPsdBuffer(fixture('smart-object-linked-rgb-8.psd'), {
      documentId: 'so-linked',
      name: 'smart-object-linked-rgb-8.psd',
    });
    expect(result.document.canvas).toEqual({ width: 8, height: 8 });
    expect(result.report.importedSmartObjects).toBeGreaterThanOrEqual(1);
    const so = result.document.layers.find((layer) => layer.kind === 'smart-object');
    expect(so).toBeDefined();
    if (so?.kind !== 'smart-object') throw new Error('expected smart-object layer');
    expect(so.source.layers.length).toBeGreaterThanOrEqual(1);
    // Nested linked PSD preferred when present.
    expect(
      result.report.importedNestedSmartObjectSources >= 0 &&
        result.report.importedSmartObjects >= 1,
    ).toBe(true);
    expect(result.document.layers.map((layer) => layer.name)).toContain('Background');
    expect(result.report.skippedLayers).toBe(0);
  });

  it('imports corpus fixture with text layer without injects', () => {
    const result = importPsdBuffer(fixture('text-layer-rgb-8.psd'), {
      documentId: 'text-layer',
      name: 'text-layer-rgb-8.psd',
    });
    expect(result.document.canvas).toEqual({ width: 16, height: 8 });
    const names = result.document.layers.map((layer) => layer.name);
    expect(names).toContain('Background');
    // Text may import as text layer or pixel+text depending on ag-psd shape; require text content.
    expect(result.report.importedTextLayers).toBeGreaterThanOrEqual(1);
    const textLayer = result.document.layers.find(
      (layer) => layer.kind === 'text' || layer.name === 'Title',
    );
    expect(textLayer).toBeDefined();
    if (textLayer?.kind === 'text') {
      expect(textLayer.text).toContain('Hello');
    }
    expect(result.report.skippedLayers).toBe(0);
  });

  it('imports corpus fixture with raster mask, layer effects, and guides without injects', () => {
    // Real binary fixture (no parse injects): proves mask + effects + guides survive
    // the ag-psd → native path end-to-end for ordinary layered documents.
    const result = importPsdBuffer(fixture('mask-effects-guides-rgb-8.psd'), {
      documentId: 'mask-effects-guides',
      name: 'mask-effects-guides-rgb-8.psd',
    });
    expect(result.document.canvas).toEqual({ width: 8, height: 8 });
    expect(result.document.layers.map((layer) => layer.name)).toEqual(['Background', 'Masked red']);
    const masked = result.document.layers[1];
    expect(masked?.kind).toBe('pixel');
    if (masked?.kind !== 'pixel') throw new Error('expected Masked red pixel layer');
    expect(masked.rasterMask).not.toBeNull();
    expect(masked.rasterMask?.enabled).toBe(true);
    expect(masked.rasterMask?.coverageStore.allocatedByteLength).toBeGreaterThan(0);
    // Sparse mask must not densify a multi-megabyte canvas (fixture is tiny; guard principle).
    expect(masked.rasterMask?.coverageStore.allocatedByteLength).toBeLessThanOrEqual(8 * 8);
    expect(result.report.importedRasterMasks).toBeGreaterThanOrEqual(1);
    expect(result.report.importedEffects).toBeGreaterThanOrEqual(1);
    expect(masked.effects.length).toBeGreaterThanOrEqual(1);
    expect(masked.effects.some((effect) => effect.type === 'drop-shadow')).toBe(true);
    // Guides from gridAndGuidesInformation → first-class document.guides.
    expect(result.document.guides.length).toBeGreaterThanOrEqual(1);
    expect(result.report.importedGuides).toBeGreaterThanOrEqual(1);
    expect(result.report.layerCount).toBe(2);
    expect(result.report.skippedLayers).toBe(0);
  });

  it('reports editable text import and skipped previewless layers', () => {
    const source: Psd = {
      width: 3,
      height: 2,
      children: [
        {
          name: 'Editable text',
          left: 0,
          top: 0,
          text: { text: 'Hello' } as NonNullable<Psd['text']>,
          imageData: {
            width: 1,
            height: 1,
            data: new Uint8ClampedArray([1, 2, 3, 255]),
          },
        },
        { name: 'No preview' },
      ],
    };
    const result = importPsdBuffer(fixture('flattened-rgb-8.psd'), { parse: () => source });
    expect(result.report).toMatchObject({
      layerCount: 1,
      importedTextLayers: 1,
      skippedLayers: 1,
      importedRasterMasks: 0,
      importedVectorMasks: 0,
      importedEffects: 0,
    });
    expect(result.document.layers[0]?.kind).toBe('text');
    expect(result.report.warnings.some((warning) => warning.includes('editable text'))).toBe(true);
  });

  it('imports greyscale PSD layer masks into layer-local sparse coverage', () => {
    // Fixture header is 3×2; parse override must match.
    const pixels = new Uint8ClampedArray(3 * 2 * 4).fill(180);
    // 2×2 mask covering the top-left of the layer at document origin.
    const maskData = new Uint8ClampedArray([0, 64, 128, 255]);
    const source: Psd = {
      width: 3,
      height: 2,
      children: [
        {
          name: 'Masked layer',
          left: 0,
          top: 0,
          imageData: { width: 3, height: 2, data: pixels },
          mask: {
            left: 0,
            top: 0,
            right: 2,
            bottom: 2,
            defaultColor: 255,
            disabled: false,
            imageData: { width: 2, height: 2, data: maskData },
          },
        },
      ],
    };
    const result = importPsdBuffer(fixture('flattened-rgb-8.psd'), { parse: () => source });
    expect(result.report.importedRasterMasks).toBe(1);
    const layer = result.document.layers[0];
    expect(layer?.kind).toBe('pixel');
    if (layer?.kind !== 'pixel') throw new Error('expected pixel layer');
    if (!layer.rasterMask) throw new Error('expected raster mask');
    expect(layer.rasterMask.enabled).toBe(true);
    expect(layer.rasterMask.width).toBe(3);
    expect(layer.rasterMask.height).toBe(2);
    const contiguous = layer.rasterMask.coverageStore.toContiguous();
    // Row0: mask 0,64 then default 255; row1: mask 128,255 then default 255.
    expect([...contiguous]).toEqual([0, 64, 255, 128, 255, 255]);
  });

  it('imports relative PSD layer masks and disabled state', () => {
    const pixels = new Uint8ClampedArray(2 * 2 * 4).fill(40);
    const maskData = new Uint8ClampedArray([10, 20, 30, 40]);
    const source: Psd = {
      width: 3,
      height: 2,
      children: [
        {
          name: 'Relative mask',
          left: 1,
          top: 0,
          imageData: { width: 2, height: 2, data: pixels },
          mask: {
            left: 0,
            top: 0,
            positionRelativeToLayer: true,
            defaultColor: 0,
            disabled: true,
            userMaskDensity: 0.5,
            imageData: { width: 2, height: 2, data: maskData },
          },
        },
      ],
    };
    const result = importPsdBuffer(fixture('flattened-rgb-8.psd'), { parse: () => source });
    expect(result.report.importedRasterMasks).toBe(1);
    const layer = result.document.layers[0];
    if (layer?.kind !== 'pixel' || !layer.rasterMask) throw new Error('expected masked pixel');
    expect(layer.rasterMask.enabled).toBe(false);
    expect(layer.rasterMask.density).toBeCloseTo(0.5, 5);
    expect([...layer.rasterMask.coverageStore.toContiguous()]).toEqual([10, 20, 30, 40]);
  });

  it('imports PSD vector masks as layer-local path commands', () => {
    // Document-space rectangle path offset by layer origin (1,0).
    const source: Psd = {
      width: 3,
      height: 2,
      children: [
        {
          name: 'Vector masked',
          left: 1,
          top: 0,
          imageData: { width: 2, height: 2, data: new Uint8ClampedArray(2 * 2 * 4).fill(40) },
          vectorMask: {
            invert: false,
            disable: false,
            paths: [
              {
                open: false,
                fillRule: 'even-odd',
                knots: [
                  // points: prevCtrl, anchor, nextCtrl — axis-aligned rect in document space
                  { linked: true, points: [1, 0, 1, 0, 1, 0] },
                  { linked: true, points: [3, 0, 3, 0, 3, 0] },
                  { linked: true, points: [3, 2, 3, 2, 3, 2] },
                  { linked: true, points: [1, 2, 1, 2, 1, 2] },
                ],
              },
            ],
          },
        },
      ],
    };
    const result = importPsdBuffer(fixture('flattened-rgb-8.psd'), { parse: () => source });
    expect(result.report.importedVectorMasks).toBe(1);
    const layer = result.document.layers[0];
    if (layer?.kind !== 'pixel' || !layer.vectorMask) throw new Error('expected vector mask');
    expect(layer.vectorMask.enabled).toBe(true);
    expect(layer.vectorMask.inverted).toBe(false);
    expect(layer.vectorMask.width).toBe(2);
    expect(layer.vectorMask.height).toBe(2);
    // Layer-local: document (1,0) → local (0,0)
    expect(layer.vectorMask.commands[0]).toEqual({ type: 'move', x: 0, y: 0 });
    expect(layer.vectorMask.commands.some((command) => command.type === 'close')).toBe(true);
    expect(layer.vectorMask.commands.some((command) => command.type === 'cubic')).toBe(true);
  });

  it('imports inverted disabled PSD vector masks', () => {
    const source: Psd = {
      width: 3,
      height: 2,
      children: [
        {
          name: 'Disabled inverted vector',
          left: 0,
          top: 0,
          imageData: { width: 3, height: 2, data: new Uint8ClampedArray(3 * 2 * 4).fill(10) },
          vectorMask: {
            invert: true,
            disable: true,
            paths: [
              {
                open: true,
                fillRule: 'non-zero',
                knots: [
                  { linked: true, points: [0, 0, 0, 0, 0, 0] },
                  { linked: true, points: [2, 1, 2, 1, 2, 1] },
                ],
              },
            ],
          },
        },
      ],
    };
    const result = importPsdBuffer(fixture('flattened-rgb-8.psd'), { parse: () => source });
    expect(result.report.importedVectorMasks).toBe(1);
    const layer = result.document.layers[0];
    if (layer?.kind !== 'pixel' || !layer.vectorMask) throw new Error('expected vector mask');
    expect(layer.vectorMask.enabled).toBe(false);
    expect(layer.vectorMask.inverted).toBe(true);
    expect(layer.vectorMask.commands.at(-1)?.type).not.toBe('close');
  });

  it('preserves linkedFiles metadata and small payloads in document.preservation', () => {
    const payload = new Uint8Array([1, 2, 3, 4, 5, 6, 7, 8]);
    const source: Psd = {
      width: 3,
      height: 2,
      children: [
        {
          name: 'Background',
          left: 0,
          top: 0,
          imageData: { width: 3, height: 2, data: new Uint8ClampedArray(3 * 2 * 4).fill(1) },
        },
      ],
      linkedFiles: [
        {
          id: 'file-1',
          name: 'embed.psd',
          type: '8BPB',
          data: payload,
          time: '2026-01-01T00:00:00Z',
          childDocumentID: 'doc-uuid',
        },
        {
          id: 'file-2',
          name: 'huge.bin',
          type: 'bin',
          // Oversized payload: metadata only.
          data: new Uint8Array(5 * 1024 * 1024),
        },
      ],
    };
    const result = importPsdBuffer(fixture('flattened-rgb-8.psd'), { parse: () => source });
    expect(result.report.preservedLinkedFiles).toBe(2);
    const linked = result.document.preservation?.linkedFiles as Array<Record<string, unknown>>;
    expect(Array.isArray(linked)).toBe(true);
    expect(linked).toHaveLength(2);
    expect(linked[0]).toMatchObject({
      id: 'file-1',
      name: 'embed.psd',
      type: '8BPB',
      dataByteLength: 8,
      dataOmitted: false,
      childDocumentID: 'doc-uuid',
    });
    expect(typeof linked[0]?.dataBase64).toBe('string');
    expect((linked[0]?.dataBase64 as string).length).toBeGreaterThan(0);
    expect(linked[1]).toMatchObject({
      id: 'file-2',
      name: 'huge.bin',
      dataByteLength: 5 * 1024 * 1024,
      dataBase64: null,
      dataOmitted: true,
    });
    expect(
      result.report.warnings.some(
        (warning) => warning.includes('huge.bin') && warning.includes('preservation limit'),
      ),
    ).toBe(true);
  });

  it('imports PSD placedLayer with raster preview as embedded smart object', () => {
    const pixels = new Uint8ClampedArray(2 * 2 * 4);
    for (let i = 0; i < pixels.length; i += 4) {
      pixels[i] = 10;
      pixels[i + 1] = 20;
      pixels[i + 2] = 30;
      pixels[i + 3] = 255;
    }
    const source: Psd = {
      width: 3,
      height: 2,
      children: [
        {
          name: 'SO layer',
          left: 1,
          top: 0,
          placedLayer: {
            id: 'linked-so-1',
            type: 'raster',
            transform: [1, 0, 0, 1, 1, 0],
            width: 2,
            height: 2,
          },
          imageData: { width: 2, height: 2, data: pixels },
        },
      ],
    };
    const result = importPsdBuffer(fixture('flattened-rgb-8.psd'), { parse: () => source });
    expect(result.report.importedSmartObjects).toBe(1);
    expect(result.report.importedNestedSmartObjectSources).toBe(0);
    const layer = result.document.layers[0];
    expect(layer?.kind).toBe('smart-object');
    if (layer?.kind !== 'smart-object') throw new Error('expected smart object');
    expect(layer.source.id).toBe('linked-so-1');
    expect(layer.source.canvas).toEqual({ width: 2, height: 2 });
    expect(layer.source.layers).toHaveLength(1);
    expect(layer.source.layers[0]?.kind).toBe('pixel');
    expect(layer.transform).toMatchObject({ a: 1, e: 1, f: 0 });
  });

  it('imports nested smart-object source from a linked PSD payload', () => {
    const nestedBytes = new Uint8Array(fixture('pixel-layers-rgb-8.psd'));
    const preview = new Uint8ClampedArray(2 * 2 * 4).fill(9);
    const source: Psd = {
      width: 3,
      height: 2,
      linkedFiles: [
        {
          id: 'nested-so-id',
          name: 'pixel-layers-rgb-8.psd',
          type: '8BPB',
          data: nestedBytes,
        },
      ],
      children: [
        {
          name: 'Embedded SO',
          left: 0,
          top: 0,
          placedLayer: {
            id: 'nested-so-id',
            type: 'raster',
            transform: [1, 0, 0, 1, 0, 0],
            width: 4,
            height: 4,
          },
          // Preview exists but nested PSD must take priority.
          imageData: { width: 2, height: 2, data: preview },
        },
      ],
    };
    const result = importPsdBuffer(fixture('flattened-rgb-8.psd'), { parse: () => source });
    expect(result.report.importedSmartObjects).toBe(1);
    expect(result.report.importedNestedSmartObjectSources).toBe(1);
    expect(result.report.preservedLinkedFiles).toBe(1);
    const layer = result.document.layers[0];
    if (layer?.kind !== 'smart-object') throw new Error('expected smart object');
    expect(layer.source.id).toBe('nested-so-id');
    expect(layer.source.name).toBe('pixel-layers-rgb-8.psd');
    // Nested fixture has two pixel layers (Bottom blue, Top red).
    expect(layer.source.layers.map((child) => child.name)).toEqual(['Bottom blue', 'Top red']);
    expect(layer.source.canvas.width).toBeGreaterThan(0);
    expect(layer.source.canvas.height).toBeGreaterThan(0);
    // Nested source must not be the 2×2 preview fallback.
    expect(layer.source.canvas).not.toEqual({ width: 2, height: 2 });
  });

  it('imports smart object from linked PSD without a raster preview', () => {
    const nestedBytes = new Uint8Array(fixture('flattened-rgb-8.psd'));
    const source: Psd = {
      width: 3,
      height: 2,
      linkedFiles: [
        {
          id: 'previewless-so',
          name: 'flat.psd',
          data: nestedBytes,
        },
      ],
      children: [
        {
          name: 'SO no preview',
          placedLayer: {
            id: 'previewless-so',
            type: 'raster',
            transform: [1, 0, 0, 1, 0, 0],
            width: 3,
            height: 2,
          },
        },
      ],
    };
    const result = importPsdBuffer(fixture('flattened-rgb-8.psd'), { parse: () => source });
    expect(result.report.importedSmartObjects).toBe(1);
    expect(result.report.importedNestedSmartObjectSources).toBe(1);
    expect(result.report.skippedLayers).toBe(0);
    const layer = result.document.layers[0];
    if (layer?.kind !== 'smart-object') throw new Error('expected smart object');
    expect(layer.source.layers.length).toBeGreaterThan(0);
  });

  it('imports PSD text layers as editable native text layers', () => {
    const source: Psd = {
      width: 3,
      height: 2,
      children: [
        {
          name: 'Background',
          left: 0,
          top: 0,
          imageData: { width: 3, height: 2, data: new Uint8ClampedArray(3 * 2 * 4).fill(50) },
        },
        {
          name: 'Title',
          left: 0,
          top: 0,
          right: 3,
          bottom: 2,
          text: {
            text: 'Hello PSD',
            style: {
              fontSize: 18,
              fauxBold: true,
              fillColor: { r: 255, g: 0, b: 0, a: 1 },
            },
            paragraphStyle: { justification: 'center' },
          },
          imageData: {
            width: 3,
            height: 2,
            data: new Uint8ClampedArray(3 * 2 * 4).fill(200),
          },
        },
      ],
    };
    const result = importPsdBuffer(fixture('flattened-rgb-8.psd'), { parse: () => source });
    expect(result.report.importedTextLayers).toBe(1);
    // Topmost PSD child becomes last after reverse? Title is last in source → first after reverse → bottom
    // Source top-to-bottom: Background, Title. Reverse: Title, Background → Title bottom, Background top.
    const text = result.document.layers[0];
    expect(text?.kind).toBe('text');
    if (text?.kind !== 'text') throw new Error('expected text layer');
    expect(text.text).toBe('Hello PSD');
    expect(text.style.fontSize).toBe(18);
    expect(text.style.fontWeight).toBe(700);
    expect(text.style.align).toBe('center');
    expect(text.style.color).toEqual({ red: 255, green: 0, blue: 0, alpha: 255 });
    expect(result.report.warnings.some((warning) => warning.includes('editable text'))).toBe(true);
  });

  it('imports invert and brightness/contrast adjustment layers', () => {
    const source: Psd = {
      width: 3,
      height: 2,
      children: [
        {
          name: 'Base',
          left: 0,
          top: 0,
          imageData: { width: 3, height: 2, data: new Uint8ClampedArray(3 * 2 * 4).fill(100) },
        },
        {
          name: 'Invert adj',
          adjustment: { type: 'invert' },
        },
        {
          name: 'Bright adj',
          adjustment: { type: 'brightness/contrast', brightness: 20, contrast: -10 },
        },
        {
          name: 'Hue adj',
          adjustment: {
            type: 'hue/saturation',
            master: { a: 0, b: 0, c: 0, d: 0, hue: 30, saturation: 10, lightness: -5 },
          },
        },
        {
          name: 'Levels adj',
          adjustment: {
            type: 'levels',
            rgb: {
              shadowInput: 10,
              highlightInput: 240,
              midtoneInput: 1.2,
              shadowOutput: 0,
              highlightOutput: 255,
            },
          },
        },
        {
          name: 'Posterize adj',
          adjustment: { type: 'posterize', levels: 4 },
        },
        {
          name: 'Threshold adj',
          adjustment: { type: 'threshold', level: 100 },
        },
        {
          name: 'Unsupported curves',
          adjustment: { type: 'curves', rgb: [{ input: 0, output: 0 }] },
        },
      ],
    };
    const result = importPsdBuffer(fixture('flattened-rgb-8.psd'), { parse: () => source });
    expect(result.report.importedAdjustmentLayers).toBe(6);
    expect(result.report.skippedLayers).toBe(1);
    const kinds = result.document.layers.map((layer) =>
      layer.kind === 'adjustment' ? layer.adjustment.type : layer.kind,
    );
    // Bottom-to-top after reverse of PSD top-to-bottom children.
    expect(kinds).toEqual([
      'threshold',
      'posterize',
      'levels',
      'hue-saturation',
      'brightness-contrast',
      'invert',
      'pixel',
    ]);
    const bright = result.document.layers[4];
    if (bright?.kind !== 'adjustment' || bright.adjustment.type !== 'brightness-contrast') {
      throw new Error('expected brightness adjustment');
    }
    expect(bright.adjustment).toMatchObject({ brightness: 20, contrast: -10 });
    expect(result.document.preservation?.skippedLayerRecords).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ name: 'Unsupported curves', kind: 'adjustment' }),
      ]),
    );
  });

  it('attaches opaque preservation records for skipped PSD layers', () => {
    const source: Psd = {
      width: 3,
      height: 2,
      children: [
        {
          name: 'Visible pixels',
          left: 0,
          top: 0,
          imageData: { width: 1, height: 1, data: new Uint8ClampedArray([9, 8, 7, 255]) },
        },
        {
          name: 'Placed only',
          placedLayer: { id: 'missing', type: 'raster' } as NonNullable<
            Psd['children']
          >[number]['placedLayer'],
        },
        { name: 'Empty layer' },
      ],
    };
    const result = importPsdBuffer(fixture('flattened-rgb-8.psd'), { parse: () => source });
    expect(result.report.skippedLayers).toBe(2);
    expect(result.document.preservation).toMatchObject({
      kind: 'psd-import-v1',
      header: { width: 3, height: 2 },
    });
    const records = result.document.preservation?.skippedLayerRecords;
    expect(Array.isArray(records)).toBe(true);
    expect(records).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ name: 'Placed only', kind: 'smart-object' }),
        expect.objectContaining({ name: 'Empty layer', kind: 'empty' }),
      ]),
    );
  });

  it('imports drop-shadow, stroke, and color-overlay layer effects', () => {
    const source: Psd = {
      width: 3,
      height: 2,
      children: [
        {
          name: 'Styled',
          left: 0,
          top: 0,
          imageData: { width: 3, height: 2, data: new Uint8ClampedArray(3 * 2 * 4).fill(200) },
          effects: {
            dropShadow: [
              {
                enabled: true,
                opacity: 0.5,
                angle: 0,
                distance: { units: 'Pixels', value: 4 },
                size: { units: 'Pixels', value: 6 },
                color: { r: 0, g: 0, b: 0, a: 1 },
              },
            ],
            stroke: [
              {
                enabled: true,
                opacity: 1,
                size: { units: 'Pixels', value: 2 },
                fillType: 'color',
                color: { r: 255, g: 0, b: 0, a: 1 },
              },
            ],
            solidFill: [
              {
                enabled: true,
                opacity: 0.8,
                blendMode: 'multiply',
                color: { r: 0, g: 128, b: 255, a: 1 },
              },
            ],
            outerGlow: {
              enabled: true,
              size: { units: 'Pixels', value: 3 },
              color: { r: 255, g: 255, b: 0, a: 1 },
            },
          },
        },
      ],
    };
    const result = importPsdBuffer(fixture('flattened-rgb-8.psd'), { parse: () => source });
    expect(result.report.importedEffects).toBe(3);
    expect(result.report.warnings.some((warning) => warning.includes('outer-glow'))).toBe(true);
    const layer = result.document.layers[0];
    if (layer?.kind !== 'pixel') throw new Error('expected pixel layer');
    expect(layer.effects).toHaveLength(3);
    expect(layer.effects[0]).toMatchObject({
      type: 'drop-shadow',
      enabled: true,
      opacity: 0.5,
      offsetX: 4,
      offsetY: 0,
      blur: 6,
      color: { red: 0, green: 0, blue: 0, alpha: 255 },
    });
    expect(layer.effects[1]).toMatchObject({
      type: 'stroke',
      size: 2,
      color: { red: 255, green: 0, blue: 0, alpha: 255 },
    });
    expect(layer.effects[2]).toMatchObject({
      type: 'color-overlay',
      blendMode: 'multiply',
      opacity: 0.8,
      color: { red: 0, green: 128, blue: 255, alpha: 255 },
    });
  });

  it('imports named alpha channels with inject coverage into sparse document channels', () => {
    const width = 3;
    const height = 2;
    const coverage = new Uint8ClampedArray([0, 64, 128, 192, 255, 32]);
    const source: Psd & {
      freetoshopAlphaChannels: { name: string; coverage: Uint8ClampedArray }[];
    } = {
      width,
      height,
      channels: 5,
      children: [
        {
          name: 'Base',
          left: 0,
          top: 0,
          imageData: {
            width,
            height,
            data: new Uint8ClampedArray(width * height * 4).fill(200),
          },
        },
      ],
      imageResources: {
        alphaChannelNames: ['Selection A', 'Mask B'],
      },
      freetoshopAlphaChannels: [
        { name: 'Selection A', coverage },
        { name: 'Mask B', coverage: new Uint8ClampedArray(width * height).fill(90) },
      ],
    };
    const result = importPsdBuffer(fixture('flattened-rgb-8.psd'), { parse: () => source });
    expect(result.report.importedAlphaChannels).toBe(2);
    expect(result.report.importedAlphaChannelsWithCoverage).toBe(2);
    expect(result.document.channels).toHaveLength(2);
    expect(result.document.channels[0]).toMatchObject({
      id: 'psd-alpha-1',
      name: 'Selection A',
      width,
      height,
    });
    expect([...result.document.channels[0]!.coverageStore.toContiguous()]).toEqual([...coverage]);
    expect(result.document.channels[1]).toMatchObject({
      id: 'psd-alpha-2',
      name: 'Mask B',
    });
    expect([...result.document.channels[1]!.coverageStore.toContiguous()]).toEqual(
      Array.from({ length: width * height }, () => 90),
    );
    expect(result.document.preservation).toMatchObject({
      kind: 'psd-import-v1',
      importedAlphaChannels: 2,
      importedAlphaChannelsWithCoverage: 2,
    });
    expect(result.document.preservation?.alphaChannels).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          name: 'Selection A',
          coverageSource: 'inject',
          hasCoverage: true,
        }),
        expect.objectContaining({ name: 'Mask B', coverageSource: 'inject', hasCoverage: true }),
      ]),
    );
  });

  it('extracts single alpha coverage from composite imageData when named', () => {
    const width = 3;
    const height = 2;
    const rgba = new Uint8ClampedArray(width * height * 4);
    for (let i = 0; i < width * height; i += 1) {
      rgba[i * 4] = 10;
      rgba[i * 4 + 1] = 20;
      rgba[i * 4 + 2] = 30;
      rgba[i * 4 + 3] = i * 40;
    }
    const source: Psd = {
      width,
      height,
      channels: 4,
      imageData: { width, height, data: rgba },
      children: [],
      imageResources: {
        alphaChannelNames: ['From Composite'],
      },
    };
    const result = importPsdBuffer(fixture('flattened-rgb-8.psd'), { parse: () => source });
    expect(result.report.importedAlphaChannels).toBe(1);
    expect(result.report.importedAlphaChannelsWithCoverage).toBe(1);
    expect(result.document.channels[0]?.name).toBe('From Composite');
    expect([...result.document.channels[0]!.coverageStore.toContiguous()]).toEqual([
      0, 40, 80, 120, 160, 200,
    ]);
    expect(result.document.preservation?.alphaChannels).toEqual([
      expect.objectContaining({
        name: 'From Composite',
        coverageSource: 'composite-alpha',
        hasCoverage: true,
      }),
    ]);
  });

  it('assigns composite alpha plane to the first of multiple named channels', () => {
    const width = 3;
    const height = 2;
    const rgba = new Uint8ClampedArray(width * height * 4);
    for (let i = 0; i < width * height; i += 1) {
      rgba[i * 4 + 3] = 50 + i * 10;
    }
    const source: Psd = {
      width,
      height,
      channels: 5,
      imageData: { width, height, data: rgba },
      children: [],
      imageResources: {
        alphaChannelNames: ['First', 'Second'],
      },
    };
    const result = importPsdBuffer(fixture('flattened-rgb-8.psd'), { parse: () => source });
    expect(result.report.importedAlphaChannels).toBe(2);
    expect(result.report.importedAlphaChannelsWithCoverage).toBe(1);
    expect(result.document.channels[0]?.name).toBe('First');
    expect([...result.document.channels[0]!.coverageStore.toContiguous()]).toEqual([
      50, 60, 70, 80, 90, 100,
    ]);
    expect([...result.document.channels[1]!.coverageStore.toContiguous()]).toEqual(
      Array.from({ length: 6 }, () => 0),
    );
    expect(
      result.report.warnings.some(
        (warning) => warning.includes('only 1 plane') || warning.includes('multi-channel'),
      ),
    ).toBe(true);
  });

  it('imports empty named alpha channels with a warning when coverage is unavailable', () => {
    const source: Psd = {
      width: 3,
      height: 2,
      channels: 3,
      children: [
        {
          name: 'Base',
          left: 0,
          top: 0,
          imageData: {
            width: 3,
            height: 2,
            data: new Uint8ClampedArray(3 * 2 * 4).fill(100),
          },
        },
      ],
      imageResources: {
        alphaChannelNames: ['Name Only'],
      },
    };
    const result = importPsdBuffer(fixture('flattened-rgb-8.psd'), { parse: () => source });
    expect(result.report.importedAlphaChannels).toBe(1);
    expect(result.report.importedAlphaChannelsWithCoverage).toBe(0);
    expect(result.document.channels[0]?.name).toBe('Name Only');
    expect([...result.document.channels[0]!.coverageStore.toContiguous()]).toEqual(
      Array.from({ length: 6 }, () => 0),
    );
    expect(
      result.report.warnings.some(
        (warning) => warning.includes('Name Only') && warning.includes('without pixel coverage'),
      ),
    ).toBe(true);
  });

  it('preserves resolution, guides, global light, and XMP image resources', () => {
    const source: Psd = {
      width: 3,
      height: 2,
      children: [
        {
          name: 'Base',
          left: 0,
          top: 0,
          imageData: {
            width: 3,
            height: 2,
            data: new Uint8ClampedArray(3 * 2 * 4).fill(40),
          },
        },
      ],
      imageResources: {
        resolutionInfo: {
          horizontalResolution: 300,
          horizontalResolutionUnit: 'PPI',
          widthUnit: 'Inches',
          verticalResolution: 300,
          verticalResolutionUnit: 'PPI',
          heightUnit: 'Inches',
        },
        gridAndGuidesInformation: {
          grid: { horizontal: 32, vertical: 32 },
          guides: [
            { location: 10, direction: 'vertical' },
            { location: 20, direction: 'horizontal' },
          ],
        },
        globalAngle: 120,
        globalAltitude: 30,
        pixelAspectRatio: { aspect: 1 },
        xmpMetadata: '<x:xmpmeta>sample</x:xmpmeta>',
        iccUntaggedProfile: false,
      },
    };
    const result = importPsdBuffer(fixture('flattened-rgb-8.psd'), { parse: () => source });
    expect(result.report.importedGuides).toBe(2);
    expect(result.report.importedGrid).toBe(true);
    expect(result.document.guides).toEqual([
      { id: 'psd-guide-1', direction: 'vertical', position: 10 },
      { id: 'psd-guide-2', direction: 'horizontal', position: 20 },
    ]);
    expect(result.document.grid).toEqual({ horizontal: 32, vertical: 32 });
    expect(result.document.preservation?.imageResources).toMatchObject({
      resolutionInfo: { horizontalResolution: 300, verticalResolution: 300 },
      gridAndGuides: {
        grid: { horizontal: 32, vertical: 32 },
        guides: [
          { location: 10, direction: 'vertical' },
          { location: 20, direction: 'horizontal' },
        ],
      },
      globalAngle: 120,
      globalAltitude: 30,
      pixelAspectRatio: 1,
      xmpMetadata: '<x:xmpmeta>sample</x:xmpmeta>',
      iccUntaggedProfile: false,
    });
  });

  it('imports document.paths from PSD path resource blocks when present on the file', () => {
    // Build a minimal PSD whose Image Resources section contains work path 2000.
    // Layers come from parse inject; path geometry is read from the raw buffer.
    const width = 100;
    const height = 80;
    // Reuse the synthetic builder from the path-resource unit module via re-decode:
    // Construct via decodePsdPathResources test harness pattern inlined lightly.
    const pathData = new ArrayBuffer(26 * 4);
    const pathView = new DataView(pathData);
    let po = 0;
    pathView.setUint16(po, 0, false);
    po += 2;
    pathView.setUint16(po, 2, false);
    po += 2;
    pathView.setInt16(po, -1, false);
    po += 2;
    pathView.setUint16(po, 1, false);
    po += 2;
    po += 18;
    const writeKnot = (ax: number, ay: number) => {
      pathView.setUint16(po, 1, false);
      po += 2;
      const writeF = (f: number) => {
        pathView.setInt32(po, Math.round(f * (1 << 24)), false);
        po += 4;
      };
      writeF(ay);
      writeF(ax);
      writeF(ay);
      writeF(ax);
      writeF(ay);
      writeF(ax);
    };
    writeKnot(0.1, 0.1);
    writeKnot(0.9, 0.9);
    const pathBytes = new Uint8Array(pathData);
    const resourcesLength = 4 + 2 + 1 + 1 + 4 + pathBytes.length;
    const total = 26 + 4 + 4 + resourcesLength;
    const buffer = new ArrayBuffer(total);
    const view = new DataView(buffer);
    const bytes = new Uint8Array(buffer);
    bytes.set([0x38, 0x42, 0x50, 0x53], 0);
    view.setUint16(4, 1, false);
    view.setUint16(12, 3, false);
    view.setUint32(14, height, false);
    view.setUint32(18, width, false);
    view.setUint16(22, 8, false);
    view.setUint16(24, 3, false);
    view.setUint32(26, 0, false);
    view.setUint32(30, resourcesLength, false);
    let offset = 34;
    bytes.set([0x38, 0x42, 0x49, 0x4d], offset);
    offset += 4;
    view.setUint16(offset, 2000, false);
    offset += 2;
    bytes[offset++] = 0;
    bytes[offset++] = 0;
    view.setUint32(offset, pathBytes.length, false);
    offset += 4;
    bytes.set(pathBytes, offset);

    expect(decodePsdPathResources(buffer, width, height).length).toBe(1);

    const source: Psd = {
      width,
      height,
      children: [
        {
          name: 'Base',
          left: 0,
          top: 0,
          imageData: {
            width: 1,
            height: 1,
            data: new Uint8ClampedArray([1, 2, 3, 255]),
          },
        },
      ],
    };
    const result = importPsdBuffer(buffer, { parse: () => source });
    expect(result.report.importedDocumentPaths).toBeGreaterThanOrEqual(1);
    expect(result.document.paths.length).toBeGreaterThanOrEqual(1);
    expect(result.document.paths[0]?.name).toBe('Work Path');
    expect(result.document.paths[0]?.commands.length).toBeGreaterThan(0);
    expect(result.document.preservation?.documentPaths).toEqual(
      expect.arrayContaining([expect.objectContaining({ source: 'resource', resourceId: 2000 })]),
    );
  });

  it('preserves document path injects and path selection state in preservation', () => {
    const source: Psd & {
      freetoshopDocumentPaths: {
        name: string;
        open: boolean;
        knots: { linked: boolean; points: number[] }[];
      }[];
    } = {
      width: 3,
      height: 2,
      children: [
        {
          name: 'Base',
          left: 0,
          top: 0,
          imageData: {
            width: 3,
            height: 2,
            data: new Uint8ClampedArray(3 * 2 * 4).fill(50),
          },
        },
      ],
      imageResources: {
        pathSelectionState: ['Work Path'],
      },
      freetoshopDocumentPaths: [
        {
          name: 'Work Path',
          open: false,
          knots: [
            { linked: true, points: [0, 0, 0, 0, 1, 0] },
            { linked: true, points: [2, 0, 3, 0, 3, 1] },
          ],
        },
      ],
    };
    const result = importPsdBuffer(fixture('flattened-rgb-8.psd'), { parse: () => source });
    expect(result.report.preservedDocumentPaths).toBe(1);
    expect(result.report.importedDocumentPaths).toBe(1);
    expect(result.document.paths).toHaveLength(1);
    expect(result.document.paths[0]).toMatchObject({
      id: 'psd-path-1',
      name: 'Work Path',
      open: false,
    });
    expect(result.document.paths[0]!.commands.length).toBeGreaterThan(0);
    expect(result.document.preservation).toMatchObject({
      pathSelectionState: ['Work Path'],
      preservedDocumentPaths: 1,
      importedDocumentPaths: 1,
    });
    expect(result.document.preservation?.documentPaths).toEqual([
      expect.objectContaining({
        name: 'Work Path',
        open: false,
        knotCount: 2,
        commandCount: expect.any(Number),
      }),
    ]);
    expect(
      result.report.warnings.some((warning) =>
        warning.includes('Document-level PSD paths were imported'),
      ),
    ).toBe(true);
  });

  it('rejects truncated PSD and PSB headers before decoding', () => {
    expect(() => importPsdBuffer(fixture('truncated.psd'))).toThrow('header is truncated');
    expect(() => importPsdBuffer(fixture('psb-header.psb'))).toThrow(
      'PSB large documents are not supported yet',
    );
  });

  it('rejects oversized PSD areas before invoking the parser', () => {
    const oversized = fixture('flattened-rgb-8.psd').slice(0);
    const view = new DataView(oversized);
    view.setUint32(14, 10_001, false);
    view.setUint32(18, 10_000, false);
    const parse = vi.fn();
    expect(() => importPsdBuffer(oversized, { parse })).toThrow(
      'PSD area cannot exceed 100000000 pixels',
    );
    expect(parse).not.toHaveBeenCalled();
  });

  it('reports monotonic parser, layer, resource, and completion progress', () => {
    const source: Psd = {
      width: 3,
      height: 2,
      children: [
        {
          name: 'Group',
          children: [
            {
              name: 'Nested pixel',
              left: 0,
              top: 0,
              imageData: {
                width: 2,
                height: 2,
                data: new Uint8ClampedArray(16).fill(64),
              },
            },
          ],
        },
        {
          name: 'Top pixel',
          left: 1,
          top: 0,
          imageData: {
            width: 2,
            height: 2,
            data: new Uint8ClampedArray(16).fill(128),
          },
        },
      ],
    };
    const updates: Array<{
      phase: string;
      completed: number;
      total: number;
      progress: number;
    }> = [];

    importPsdBuffer(fixture('flattened-rgb-8.psd'), {
      parse: () => source,
      onProgress: (update) => updates.push(update),
    });

    expect(updates.map(({ progress }) => progress)).toEqual(
      [...updates.map(({ progress }) => progress)].sort((a, b) => a - b),
    );
    expect(updates.filter(({ phase }) => phase === 'parse')).toEqual([
      { phase: 'parse', completed: 0, total: 1, progress: 0 },
      { phase: 'parse', completed: 1, total: 1, progress: 0.35 },
    ]);
    expect(updates.filter(({ phase }) => phase === 'layers')).toHaveLength(3);
    expect(updates.filter(({ phase }) => phase === 'resources')).toEqual(
      [1, 2, 3, 4, 5].map((completed) => ({
        phase: 'resources',
        completed,
        total: 5,
        progress: 0.75 + 0.04 * completed,
      })),
    );
    expect(updates.at(-1)).toEqual({
      phase: 'complete',
      completed: 1,
      total: 1,
      progress: 1,
    });
  });

  it('does not invoke the opaque parser when already cancelled', () => {
    const controller = new AbortController();
    const parse = vi.fn();
    controller.abort();

    expect(() =>
      importPsdBuffer(fixture('flattened-rgb-8.psd'), {
        parse,
        signal: controller.signal,
      }),
    ).toThrow(expect.objectContaining({ name: 'AbortError' }));
    expect(parse).not.toHaveBeenCalled();
  });

  it('stops conversion after cancellation from a layer checkpoint', () => {
    const source: Psd = {
      width: 3,
      height: 2,
      children: [0, 1, 2].map((index) => ({
        name: `Pixel ${String(index + 1)}`,
        left: index,
        top: 0,
        imageData: {
          width: 1,
          height: 1,
          data: new Uint8ClampedArray([index, index, index, 255]),
        },
      })),
    };
    const controller = new AbortController();
    const completedLayers: number[] = [];

    expect(() =>
      importPsdBuffer(fixture('flattened-rgb-8.psd'), {
        parse: () => source,
        signal: controller.signal,
        onProgress: (update) => {
          if (update.phase !== 'layers') return;
          completedLayers.push(update.completed);
          controller.abort();
        },
      }),
    ).toThrow(expect.objectContaining({ name: 'AbortError' }));
    expect(completedLayers).toEqual([1]);
  });

  it('recognizes the byte signature independently of file extension or MIME type', () => {
    expect(hasPsdSignature(fixture('flattened-rgb-8.psd'))).toBe(true);
    expect(hasPsdSignature(new Uint8Array([1, 2, 3, 4]).buffer)).toBe(false);
  });
});
