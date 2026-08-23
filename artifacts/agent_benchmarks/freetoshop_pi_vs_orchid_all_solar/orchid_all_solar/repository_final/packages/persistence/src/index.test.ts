import { describe, expect, it, vi } from 'vitest';
import {
  createSelectionMask,
  createDocument,
  createAdjustmentLayer,
  createAlphaChannel,
  createGroupLayer,
  createPixelLayer,
  createRasterMask,
  createRasterMaskFromCoverageStore,
  createShapeLayer,
  createSmartObjectLayer,
  createTextLayer,
  createVectorMask,
  findLayer,
  patchPixels,
  setLayerOpacity,
} from '@freetoshop/core-document';
import { TiledCoverageStore, TiledPixelStore } from '@freetoshop/pixel-storage';
import {
  DOCUMENT_SCHEMA_VERSION,
  InMemoryDocumentRepository,
  PersistenceError,
  createDocumentRepository,
  deserializeDocument,
  serializeDocument,
  toSerializedDocument,
} from './index.js';

function fixture() {
  return createDocument({
    id: 'doc-1',
    name: 'Persistence fixture',
    width: 2,
    height: 1,
    selection: createSelectionMask({
      width: 2,
      height: 1,
      coverage: new Uint8ClampedArray([255, 0]),
    }),
    channels: [
      createAlphaChannel({
        id: 'alpha-1',
        name: 'Subject matte',
        width: 2,
        height: 1,
        coverage: new Uint8ClampedArray([17, 231]),
      }),
    ],
    layers: [
      createGroupLayer({
        id: 'group',
        name: 'Group',
        opacity: 0.5,
        blendMode: 'hue',
        fillOpacity: 0.8,
        compositing: 'isolated',
        rasterMask: createRasterMask(2, 1, [32, 224], { density: 0.45 }),
        vectorMask: createVectorMask(2, 1, undefined, { density: 0.55 }),
        locks: { position: true },
        transform: { a: 1.5, b: 0, c: 0.25, d: 2, e: 3.5, f: -4.25 },
        children: [
          createPixelLayer({
            id: 'pixels',
            name: 'Pixels',
            width: 2,
            height: 1,
            visible: false,
            transform: { a: 0, b: 1, c: -1, d: 0, e: 1.25, f: 2.5 },
            pixels: new Uint8ClampedArray([0, 1, 127, 255, 255, 128, 2, 0]),
            rasterMask: createRasterMask(2, 1, [255, 64], { density: 0.75 }),
            vectorMask: createVectorMask(2, 1, undefined, { density: 0.65, inverted: true }),
          }),
        ],
      }),
      createAdjustmentLayer({
        id: 'tone',
        name: 'Tone',
        clipping: true,
        opacity: 0.75,
        adjustment: {
          type: 'levels',
          inputBlack: 8,
          inputWhite: 240,
          gamma: 1.2,
          outputBlack: 2,
          outputWhite: 250,
        },
        rasterMask: createRasterMask(2, 1, [255, 32], { density: 0.6 }),
        vectorMask: createVectorMask(2, 1, undefined, { density: 0.7 }),
      }),
      createShapeLayer({
        id: 'shape',
        name: 'Badge',
        geometry: { type: 'ellipse', width: 2, height: 1, cornerRadius: 0 },
        style: {
          fill: { red: 12, green: 34, blue: 56, alpha: 200 },
          stroke: { red: 255, green: 255, blue: 255, alpha: 255 },
          strokeWidth: 1,
        },
        effects: [
          {
            type: 'stroke',
            enabled: true,
            opacity: 0.75,
            size: 2,
            color: { red: 1, green: 2, blue: 3, alpha: 255 },
          },
        ],
        vectorMask: createVectorMask(2, 1, undefined, { inverted: true }),
      }),
      createTextLayer({
        id: 'text',
        name: 'Caption',
        text: 'Hello',
        width: 2,
        height: 1,
        style: {
          fontFamily: 'Georgia',
          fontSize: 18,
          fontWeight: 700,
          italic: true,
          lineHeight: 1.4,
          align: 'center',
          color: { red: 240, green: 230, blue: 220, alpha: 255 },
        },
        vectorMask: createVectorMask(2, 1, undefined, { density: 0.8 }),
      }),
      createSmartObjectLayer({
        id: 'smart',
        name: 'Embedded artwork',
        source: {
          id: 'smart-source',
          name: 'Embedded artwork contents',
          canvas: { width: 1, height: 1 },
          layers: [
            createPixelLayer({
              id: 'smart-pixels',
              name: 'Embedded pixels',
              width: 1,
              height: 1,
              pixels: [9, 8, 7, 255],
            }),
          ],
        },
        smartFilters: [
          { id: 'invert', type: 'invert', enabled: true, opacity: 0.75 },
          { id: 'blur', type: 'gaussian-blur', enabled: false, opacity: 1, radius: 3 },
        ],
        vectorMask: createVectorMask(1, 1, undefined, { enabled: false }),
      }),
    ],
  });
}

function downgradeTransformsToTranslations(
  layers: Array<Record<string, unknown>>,
  translation = { x: 7, y: -3 },
): void {
  for (const layer of layers) {
    layer.transform = { ...translation };
    if (Array.isArray(layer.children)) {
      downgradeTransformsToTranslations(
        layer.children as Array<Record<string, unknown>>,
        translation,
      );
    }
  }
}

describe('document serialization', () => {
  it('round-trips nested documents and restores pixel buffers as Uint8ClampedArray', () => {
    const serialized = serializeDocument(fixture());
    expect(serialized).toContain(`"schemaVersion":${DOCUMENT_SCHEMA_VERSION}`);
    expect(serialized).not.toContain('0,1,127,255');

    const restored = deserializeDocument(serialized);
    expect(restored).toEqual(fixture());
    const pixels = findLayer(restored, 'pixels');
    expect(pixels?.kind).toBe('pixel');
    if (pixels?.kind === 'pixel') {
      expect(pixels.pixelStore.toContiguous()).toBeInstanceOf(Uint8ClampedArray);
      expect([...pixels.pixelStore.toContiguous()]).toEqual([0, 1, 127, 255, 255, 128, 2, 0]);
      expect(pixels.vectorMask).toMatchObject({
        width: 2,
        height: 1,
        enabled: true,
        density: 0.65,
        inverted: true,
      });
    }
    expect(restored.channels[0]?.coverageStore.toContiguous()).toBeInstanceOf(Uint8ClampedArray);
    expect(restored.channels[0]?.coverageStore.toContiguous()).toEqual(
      new Uint8ClampedArray([17, 231]),
    );
    expect(findLayer(restored, 'shape')).toMatchObject({
      kind: 'shape',
      geometry: { type: 'ellipse', width: 2, height: 1 },
      effects: [{ type: 'stroke', size: 2, opacity: 0.75 }],
    });
    expect(findLayer(restored, 'text')).toMatchObject({
      kind: 'text',
      text: 'Hello',
      style: { fontFamily: 'Georgia', fontWeight: 700, align: 'center' },
    });
    expect(findLayer(restored, 'smart')).toMatchObject({
      kind: 'smart-object',
      source: { id: 'smart-source', canvas: { width: 1, height: 1 } },
      smartFilters: [
        { type: 'invert', opacity: 0.75 },
        { type: 'gaussian-blur', radius: 3 },
      ],
    });
  });

  it('round-trips opaque document preservation bags (schema ≥ 17)', () => {
    const document = createDocument({
      id: 'preserve-doc',
      name: 'Preserve',
      width: 1,
      height: 1,
      layers: [
        createPixelLayer({
          id: 'p',
          name: 'P',
          width: 1,
          height: 1,
          pixels: new Uint8ClampedArray([1, 2, 3, 255]),
        }),
      ],
      preservation: {
        kind: 'psd-import-v1',
        skippedLayerRecords: [{ name: 'Text only', kind: 'text' }],
        warnings: ['sample'],
      },
    });
    const restored = deserializeDocument(serializeDocument(document));
    expect(restored.preservation).toEqual({
      kind: 'psd-import-v1',
      skippedLayerRecords: [{ name: 'Text only', kind: 'text' }],
      warnings: ['sample'],
    });
    expect(JSON.parse(serializeDocument(document)).schemaVersion).toBe(DOCUMENT_SCHEMA_VERSION);
  });

  it('round-trips document guides and grid (schema ≥ 18)', () => {
    const document = createDocument({
      id: 'guides-doc',
      name: 'Guides',
      width: 100,
      height: 80,
      layers: [
        createPixelLayer({
          id: 'p',
          name: 'P',
          width: 1,
          height: 1,
          pixels: new Uint8ClampedArray([1, 2, 3, 255]),
        }),
      ],
      guides: [
        { id: 'g1', direction: 'vertical', position: 10 },
        { id: 'g2', direction: 'horizontal', position: 20 },
      ],
      grid: { horizontal: 16, vertical: 16 },
    });
    const restored = deserializeDocument(serializeDocument(document));
    expect(restored.guides).toEqual([
      { id: 'g1', direction: 'vertical', position: 10 },
      { id: 'g2', direction: 'horizontal', position: 20 },
    ]);
    expect(restored.grid).toEqual({ horizontal: 16, vertical: 16 });
    expect(JSON.parse(serializeDocument(document)).schemaVersion).toBe(DOCUMENT_SCHEMA_VERSION);
  });

  it('round-trips document paths (schema 19)', () => {
    const document = createDocument({
      id: 'paths-doc',
      name: 'Paths',
      width: 50,
      height: 50,
      layers: [
        createPixelLayer({
          id: 'p',
          name: 'P',
          width: 1,
          height: 1,
          pixels: new Uint8ClampedArray([1, 2, 3, 255]),
        }),
      ],
      paths: [
        {
          id: 'path-1',
          name: 'Work Path',
          open: false,
          commands: [
            { type: 'move', x: 0, y: 0 },
            { type: 'line', x: 10, y: 0 },
            { type: 'line', x: 10, y: 10 },
            { type: 'close' },
          ],
        },
      ],
    });
    const restored = deserializeDocument(serializeDocument(document));
    expect(restored.paths).toHaveLength(1);
    expect(restored.paths[0]).toMatchObject({
      id: 'path-1',
      name: 'Work Path',
      open: false,
    });
    expect(restored.paths[0]?.commands).toEqual([
      { type: 'move', x: 0, y: 0 },
      { type: 'line', x: 10, y: 0 },
      { type: 'line', x: 10, y: 10 },
      { type: 'close' },
    ]);
    expect(JSON.parse(serializeDocument(document)).schemaVersion).toBe(20);
  });

  it('encodes sparse pixel tiles without densifying a large empty canvas (schema 20)', () => {
    const width = 1024;
    const height = 1024;
    // Empty 1024² layer: only allocated tiles (none) should be serialized.
    const empty = createDocument({
      id: 'sparse-empty',
      name: 'Sparse empty',
      width,
      height,
      layers: [
        createPixelLayer({
          id: 'empty',
          name: 'Empty',
          width,
          height,
        }),
      ],
    });
    const emptySerialized = toSerializedDocument(empty);
    expect(emptySerialized.schemaVersion).toBe(20);
    const emptyLayer = emptySerialized.document.layers[0] as {
      kind: string;
      pixels: unknown;
    };
    expect(emptyLayer.kind).toBe('pixel');
    expect(emptyLayer.pixels).toMatchObject({
      format: 'sparse-tiles-v1',
      tiles: [],
    });
    // Contiguous 1024² RGBA base64 would be multi-megabyte; sparse empty is tiny.
    expect(serializeDocument(empty).length).toBeLessThan(50_000);

    // Sparse painted patch round-trips without full densify in the payload.
    const paintedStore = createPixelLayer({
      id: 'p',
      name: 'P',
      width,
      height,
    }).pixelStore.write({
      x: 100,
      y: 120,
      width: 8,
      height: 8,
      pixels: new Uint8ClampedArray(8 * 8 * 4).fill(200),
    }).store;
    const sparseDoc = createDocument({
      id: 'sparse-paint',
      name: 'Sparse paint',
      width,
      height,
      layers: [
        createPixelLayer({
          id: 'p',
          name: 'P',
          width,
          height,
          pixelStore: paintedStore,
        }),
      ],
    });
    const serialized = toSerializedDocument(sparseDoc);
    const layer = serialized.document.layers[0] as {
      pixels: { format?: string; tiles?: unknown[] };
    };
    expect(layer.pixels).toMatchObject({ format: 'sparse-tiles-v1' });
    expect(Array.isArray(layer.pixels.tiles)).toBe(true);
    expect((layer.pixels.tiles ?? []).length).toBeGreaterThan(0);
    expect(JSON.stringify(serialized).length).toBeLessThan(width * height);
    const restored = deserializeDocument(serializeDocument(sparseDoc));
    const restoredLayer = findLayer(restored, 'p');
    expect(restoredLayer?.kind).toBe('pixel');
    if (restoredLayer?.kind !== 'pixel') throw new Error('expected pixel');
    expect(restoredLayer.pixelStore.allocatedByteLength).toBeLessThan(width * height);
    expect(restoredLayer.pixelStore.read({ x: 100, y: 120, width: 8, height: 8 })).toEqual(
      new Uint8ClampedArray(8 * 8 * 4).fill(200),
    );
  });

  it('streams dense native payloads without contiguous store materialization', () => {
    const width = 1024;
    const height = 1024;
    const pixels = new Uint8ClampedArray(width * height * 4).fill(200);
    const coverage = new Uint8ClampedArray(width * height).fill(255);
    coverage[513 * width + 511] = 128;
    const pixelStore = TiledPixelStore.fromContiguous(width, height, pixels);
    const coverageStore = TiledCoverageStore.fromContiguous(width, height, coverage);
    const document = createDocument({
      id: 'dense-stream',
      name: 'Dense stream',
      width,
      height,
      selection: createSelectionMask({ width, height, coverageStore }),
      layers: [
        createPixelLayer({
          id: 'dense',
          name: 'Dense',
          width,
          height,
          pixelStore,
          rasterMask: createRasterMaskFromCoverageStore(width, height, coverageStore),
        }),
      ],
    });
    const layer = findLayer(document, 'dense');
    if (layer?.kind !== 'pixel' || !layer.rasterMask || !document.selection) {
      throw new Error('Expected dense persistence fixture.');
    }
    const pixelMaterialize = vi.spyOn(layer.pixelStore, 'toContiguous');
    const maskMaterialize = vi.spyOn(layer.rasterMask.coverageStore, 'toContiguous');
    const selectionMaterialize = vi.spyOn(document.selection.coverageStore, 'toContiguous');

    const serialized = toSerializedDocument(document);
    const serializedLayer = serialized.document.layers[0];
    if (serializedLayer?.kind !== 'pixel' || !serializedLayer.rasterMask) {
      throw new Error('Expected serialized pixel layer.');
    }

    expect(pixelMaterialize).not.toHaveBeenCalled();
    expect(maskMaterialize).not.toHaveBeenCalled();
    expect(selectionMaterialize).not.toHaveBeenCalled();
    expect(serializedLayer.pixels).toBe(Buffer.from(pixels).toString('base64'));
    expect(serializedLayer.rasterMask.coverage).toBe(Buffer.from(coverage).toString('base64'));
    expect(serialized.document.selection?.coverage).toBe(Buffer.from(coverage).toString('base64'));

    const restored = deserializeDocument(JSON.stringify(serialized));
    const restoredLayer = findLayer(restored, 'dense');
    if (restoredLayer?.kind !== 'pixel' || !restoredLayer.rasterMask || !restored.selection) {
      throw new Error('Expected restored dense fixture.');
    }
    expect(restoredLayer.pixelStore.read({ x: 511, y: 513, width: 1, height: 1 })).toEqual(
      new Uint8ClampedArray([200, 200, 200, 200]),
    );
    expect(
      restoredLayer.rasterMask.coverageStore.read({ x: 511, y: 513, width: 1, height: 1 })[0],
    ).toBe(128);
    expect(restored.selection.coverageStore.read({ x: 511, y: 513, width: 1, height: 1 })[0]).toBe(
      128,
    );
  });

  it('round-trips sparse complement coverage through native persistence', () => {
    const width = 4096;
    const height = 4096;
    const complement = TiledCoverageStore.uniform(width, height, 255, 256).write({
      x: 1024,
      y: 2048,
      width: 8,
      height: 8,
      coverage: new Uint8ClampedArray(64),
    }).store;
    const document = createDocument({
      id: 'complement-native',
      name: 'Complement native',
      width,
      height,
      selection: createSelectionMask({ width, height, coverageStore: complement }),
    });
    const materialize = vi.spyOn(complement, 'toContiguous');
    const serialized = toSerializedDocument(document);
    expect(materialize).not.toHaveBeenCalled();
    expect(serialized.document.selection?.coverage).toMatchObject({
      format: 'sparse-tiles-v1',
      defaultCoverage: 255,
    });
    const restored = deserializeDocument(JSON.stringify(serialized));
    expect(restored.selection?.coverageStore).toMatchObject({
      defaultCoverage: 255,
      tileCount: 1,
      allocatedByteLength: 256 * 256,
    });
    expect(
      restored.selection?.coverageStore.read({ x: 1024, y: 2048, width: 1, height: 1 })[0],
    ).toBe(0);
    expect(restored.selection?.coverageStore.read({ x: 0, y: 0, width: 1, height: 1 })[0]).toBe(
      255,
    );
  });

  it('migrates schema version 6 documents without structured layers', () => {
    const envelope = JSON.parse(serializeDocument(fixture())) as {
      schemaVersion: number;
      document: { layers: Array<Record<string, unknown>> };
    };
    envelope.schemaVersion = 6;
    envelope.document.layers = envelope.document.layers.slice(0, 2);
    const restored = deserializeDocument(JSON.stringify(envelope));
    expect(restored.layers).toHaveLength(2);
    expect(findLayer(restored, 'pixels')?.kind).toBe('pixel');
    expect(findLayer(restored, 'tone')?.kind).toBe('adjustment');
  });

  it('migrates schema version 7 structured layers with no effects', () => {
    const envelope = JSON.parse(serializeDocument(fixture())) as {
      schemaVersion: number;
      document: { layers: Array<Record<string, unknown>> };
    };
    envelope.schemaVersion = 7;
    envelope.document.layers = envelope.document.layers.filter(
      (layer) => layer.kind !== 'smart-object',
    );
    for (const layer of envelope.document.layers) delete layer.effects;
    const restored = deserializeDocument(JSON.stringify(envelope));
    expect(findLayer(restored, 'shape')?.effects).toEqual([]);
    expect(findLayer(restored, 'text')?.effects).toEqual([]);
  });

  it('migrates schema version 8 layers with no smart objects', () => {
    const envelope = JSON.parse(serializeDocument(fixture())) as {
      schemaVersion: number;
      document: { layers: Array<Record<string, unknown>> };
    };
    envelope.schemaVersion = 8;
    envelope.document.layers = envelope.document.layers.filter(
      (layer) => layer.kind !== 'smart-object',
    );
    const restored = deserializeDocument(JSON.stringify(envelope));
    expect(findLayer(restored, 'shape')?.kind).toBe('shape');
    expect(findLayer(restored, 'smart')).toBeUndefined();
  });

  it('migrates schema version 9 smart objects with no smart filters', () => {
    const envelope = JSON.parse(serializeDocument(fixture())) as {
      schemaVersion: number;
      document: { layers: Array<Record<string, unknown>> };
    };
    envelope.schemaVersion = 9;
    const smart = envelope.document.layers.find((layer) => layer.kind === 'smart-object');
    if (!smart) throw new Error('Expected smart object fixture');
    delete smart.smartFilters;
    const restored = deserializeDocument(JSON.stringify(envelope));
    expect(findLayer(restored, 'smart')).toMatchObject({ kind: 'smart-object', smartFilters: [] });
  });

  it('migrates schema version 10 documents with the original blend-mode set', () => {
    const envelope = JSON.parse(serializeDocument(fixture())) as {
      schemaVersion: number;
      document: { layers: Array<Record<string, unknown>> };
    };
    envelope.schemaVersion = 10;
    const group = envelope.document.layers[0];
    if (!group) throw new Error('Expected group fixture layer');
    group.blendMode = 'screen';
    const restored = deserializeDocument(JSON.stringify(envelope));
    expect(findLayer(restored, 'group')).toMatchObject({ kind: 'group', blendMode: 'screen' });
    expect(findLayer(restored, 'smart')).toMatchObject({
      kind: 'smart-object',
      smartFilters: [{ type: 'invert' }, { type: 'gaussian-blur' }],
    });
  });

  it('round-trips path commands and migrates schema version 11 shape geometry', () => {
    const pathDocument = createDocument({
      id: 'paths',
      name: 'Paths',
      width: 20,
      height: 20,
      layers: [
        createShapeLayer({
          id: 'path',
          name: 'Curve',
          geometry: {
            type: 'path',
            width: 20,
            height: 20,
            cornerRadius: 0,
            commands: [
              { type: 'move', x: 1, y: 10 },
              {
                type: 'cubic',
                control1X: 5,
                control1Y: 0,
                control2X: 15,
                control2Y: 20,
                x: 19,
                y: 10,
              },
            ],
          },
        }),
      ],
    });
    expect(deserializeDocument(serializeDocument(pathDocument))).toEqual(pathDocument);
    const malformedPath = JSON.parse(serializeDocument(pathDocument)) as {
      document: { layers: Array<{ geometry?: { commands?: Array<Record<string, unknown>> } }> };
    };
    const pathGeometry = malformedPath.document.layers[0]?.geometry;
    if (!pathGeometry?.commands?.[1]) throw new Error('Expected path command fixture');
    pathGeometry.commands[1].type = 'arc';
    expect(() => deserializeDocument(JSON.stringify(malformedPath))).toThrow(
      'Unsupported path command type',
    );

    const envelope = JSON.parse(serializeDocument(fixture())) as {
      schemaVersion: number;
      document: { layers: Array<{ geometry?: Record<string, unknown> }> };
    };
    envelope.schemaVersion = 11;
    const shape = envelope.document.layers.find((layer) => layer.geometry);
    if (!shape?.geometry) throw new Error('Expected shape fixture layer');
    delete shape.geometry.commands;
    expect(findLayer(deserializeDocument(JSON.stringify(envelope)), 'shape')).toMatchObject({
      kind: 'shape',
      geometry: { type: 'ellipse', commands: [] },
    });
  });

  it('round-trips vector masks, rejects malformed commands, and migrates schemas 12 through 15', () => {
    const serialized = JSON.parse(serializeDocument(fixture())) as {
      schemaVersion: number;
      document: {
        layers: Array<{
          children?: Array<{ vectorMask?: { commands?: Array<Record<string, unknown>> } }>;
        }>;
      };
    };
    const vectorMask = serialized.document.layers[0]?.children?.[0]?.vectorMask;
    if (!vectorMask?.commands?.[1]) throw new Error('Expected vector mask fixture');
    vectorMask.commands[1].type = 'arc';
    expect(() => deserializeDocument(JSON.stringify(serialized))).toThrow(
      'Unsupported path command type',
    );

    const legacy = JSON.parse(serializeDocument(fixture())) as typeof serialized;
    legacy.schemaVersion = 12;
    const legacyPixel = legacy.document.layers[0]?.children?.[0];
    if (!legacyPixel) throw new Error('Expected legacy pixel layer');
    delete legacyPixel.vectorMask;
    expect(findLayer(deserializeDocument(JSON.stringify(legacy)), 'pixels')).toMatchObject({
      kind: 'pixel',
      vectorMask: null,
    });

    const legacyStructured = JSON.parse(serializeDocument(fixture())) as {
      schemaVersion: number;
      document: { layers: Array<{ vectorMask?: unknown }> };
    };
    legacyStructured.schemaVersion = 13;
    for (const layer of legacyStructured.document.layers) delete layer.vectorMask;
    const migrated = deserializeDocument(JSON.stringify(legacyStructured));
    expect(findLayer(migrated, 'pixels')).toMatchObject({
      kind: 'pixel',
      vectorMask: { density: 0.65 },
    });
    expect(findLayer(migrated, 'shape')).toMatchObject({ kind: 'shape', vectorMask: null });
    expect(findLayer(migrated, 'text')).toMatchObject({ kind: 'text', vectorMask: null });
    expect(findLayer(migrated, 'smart')).toMatchObject({
      kind: 'smart-object',
      vectorMask: null,
    });

    const legacyGroup = JSON.parse(serializeDocument(fixture())) as {
      schemaVersion: number;
      document: { layers: Array<{ id?: string; vectorMask?: unknown }> };
    };
    legacyGroup.schemaVersion = 14;
    const serializedGroup = legacyGroup.document.layers.find((layer) => layer.id === 'group');
    if (!serializedGroup) throw new Error('Expected legacy group layer');
    delete serializedGroup.vectorMask;
    expect(findLayer(deserializeDocument(JSON.stringify(legacyGroup)), 'group')).toMatchObject({
      kind: 'group',
      vectorMask: null,
    });

    const legacyGroupRaster = JSON.parse(serializeDocument(fixture())) as {
      schemaVersion: number;
      document: { layers: Array<{ id?: string; rasterMask?: unknown }> };
    };
    legacyGroupRaster.schemaVersion = 15;
    const serializedRasterGroup = legacyGroupRaster.document.layers.find(
      (layer) => layer.id === 'group',
    );
    if (!serializedRasterGroup) throw new Error('Expected legacy group layer');
    delete serializedRasterGroup.rasterMask;
    expect(
      findLayer(deserializeDocument(JSON.stringify(legacyGroupRaster)), 'group'),
    ).toMatchObject({ kind: 'group', rasterMask: null });
  });

  it('rejects unknown schemas, malformed JSON, and invalid pixel byte lengths', () => {
    const envelope = toSerializedDocument(fixture());
    expect(() => deserializeDocument(JSON.stringify({ ...envelope, schemaVersion: 999 }))).toThrow(
      'Unsupported document schema version',
    );
    expect(() => deserializeDocument('{')).toThrow('not valid JSON');

    const damaged = structuredClone(envelope) as unknown as {
      document: { layers: [{ children: [{ pixels: string }] }] };
    };
    damaged.document.layers[0].children[0].pixels = 'AA==';
    expect(() => deserializeDocument(JSON.stringify(damaged))).toThrow(PersistenceError);

    const damagedChannel = structuredClone(envelope) as unknown as {
      document: { channels: [{ coverage: string }] };
    };
    damagedChannel.document.channels[0].coverage = 'not base64';
    expect(() => deserializeDocument(JSON.stringify(damagedChannel))).toThrow(PersistenceError);
  });

  it('migrates schema version 1 documents with default transforms and no selection', () => {
    const legacy = {
      schemaVersion: 1,
      document: {
        id: 'legacy',
        name: 'Legacy',
        canvas: { width: 1, height: 1 },
        layers: [
          {
            kind: 'pixel',
            id: 'pixel',
            name: 'Pixel',
            visible: true,
            opacity: 1,
            width: 1,
            height: 1,
            pixels: 'AAAAAA==',
          },
        ],
      },
    };
    const restored = deserializeDocument(JSON.stringify(legacy));
    expect(restored.selection).toBeNull();
    expect(restored.channels).toEqual([]);
    expect(findLayer(restored, 'pixel')?.transform).toEqual({
      a: 1,
      b: 0,
      c: 0,
      d: 1,
      e: 0,
      f: 0,
    });
  });

  it('migrates schema version 2 documents with no raster masks', () => {
    const envelope = JSON.parse(serializeDocument(fixture())) as {
      schemaVersion: number;
      document: { layers: Array<{ children?: Array<Record<string, unknown>> }> };
    };
    envelope.schemaVersion = 2;
    envelope.document.layers = envelope.document.layers.slice(0, 1);
    downgradeTransformsToTranslations(envelope.document.layers as Array<Record<string, unknown>>);
    const child = envelope.document.layers[0]?.children?.[0];
    if (child) delete child.rasterMask;
    const restored = deserializeDocument(JSON.stringify(envelope));
    const pixels = findLayer(restored, 'pixels');
    expect(pixels?.kind === 'pixel' ? pixels.rasterMask : undefined).toBeNull();
    expect(restored.selection?.coverageStore.toContiguous()).toEqual(
      new Uint8ClampedArray([255, 0]),
    );
    expect(restored.channels).toEqual([]);
    expect(pixels?.transform).toEqual({ a: 1, b: 0, c: 0, d: 1, e: 7, f: -3 });
  });

  it('migrates schema version 3 documents with no saved channels', () => {
    const envelope = JSON.parse(serializeDocument(fixture())) as {
      schemaVersion: number;
      document: Record<string, unknown>;
    };
    envelope.schemaVersion = 3;
    envelope.document.layers = (envelope.document.layers as Array<Record<string, unknown>>).slice(
      0,
      1,
    );
    delete envelope.document.channels;
    downgradeTransformsToTranslations(envelope.document.layers as Array<Record<string, unknown>>, {
      x: -2.5,
      y: 9.75,
    });

    const restored = deserializeDocument(JSON.stringify(envelope));
    expect(restored.channels).toEqual([]);
    expect(restored.selection?.coverageStore.toContiguous()).toEqual(
      new Uint8ClampedArray([255, 0]),
    );
    expect(findLayer(restored, 'pixels')?.transform).toEqual({
      a: 1,
      b: 0,
      c: 0,
      d: 1,
      e: -2.5,
      f: 9.75,
    });
  });

  it('migrates schema version 4 translations while preserving saved channels', () => {
    const envelope = JSON.parse(serializeDocument(fixture())) as {
      schemaVersion: number;
      document: { layers: Array<Record<string, unknown>> };
    };
    envelope.schemaVersion = 4;
    envelope.document.layers = envelope.document.layers.slice(0, 1);
    downgradeTransformsToTranslations(envelope.document.layers, { x: 11.5, y: -6.25 });

    const restored = deserializeDocument(JSON.stringify(envelope));
    expect(findLayer(restored, 'pixels')?.transform).toEqual({
      a: 1,
      b: 0,
      c: 0,
      d: 1,
      e: 11.5,
      f: -6.25,
    });
    expect(restored.channels[0]?.coverageStore.toContiguous()).toEqual(
      new Uint8ClampedArray([17, 231]),
    );
  });

  it('migrates schema version 5 layers to safe compositing defaults', () => {
    const envelope = JSON.parse(serializeDocument(fixture())) as {
      schemaVersion: number;
      document: { layers: Array<Record<string, unknown>> };
    };
    envelope.schemaVersion = 5;
    envelope.document.layers = envelope.document.layers.slice(0, 1);
    const stripV6 = (layers: Array<Record<string, unknown>>) => {
      for (const layer of layers) {
        delete layer.fillOpacity;
        delete layer.blendMode;
        delete layer.clipping;
        delete layer.locks;
        delete layer.compositing;
        if (Array.isArray(layer.children))
          stripV6(layer.children as Array<Record<string, unknown>>);
      }
    };
    stripV6(envelope.document.layers);
    const restored = deserializeDocument(JSON.stringify(envelope));
    expect(findLayer(restored, 'group')).toMatchObject({
      blendMode: 'normal',
      fillOpacity: 1,
      clipping: false,
      compositing: 'pass-through',
      locks: { transparency: false, pixels: false, position: false, all: false },
    });
    expect(findLayer(restored, 'pixels')).toMatchObject({ blendMode: 'normal', fillOpacity: 1 });
  });

  it('rejects malformed compositing, adjustment, effect, and smart-filter payloads', () => {
    const envelope = structuredClone(toSerializedDocument(fixture())) as unknown as {
      document: { layers: Array<Record<string, unknown>> };
    };
    const group = envelope.document.layers[0];
    const adjustment = envelope.document.layers[1];
    if (group === undefined || adjustment === undefined) throw new Error('Expected fixture layers');
    group.blendMode = 'imaginary';
    expect(() => deserializeDocument(JSON.stringify(envelope))).toThrow('blend mode is invalid');

    group.blendMode = 'normal';
    adjustment.adjustment = { type: 'levels', inputBlack: 0 };
    expect(() => deserializeDocument(JSON.stringify(envelope))).toThrow(PersistenceError);

    const effectsEnvelope = structuredClone(toSerializedDocument(fixture())) as unknown as {
      document: { layers: Array<Record<string, unknown>> };
    };
    const effectedLayer = effectsEnvelope.document.layers[2];
    if (!effectedLayer) throw new Error('Expected effected fixture layer');
    effectedLayer.effects = [
      { type: 'drop-shadow', enabled: true, opacity: 1, color: {}, blur: -1 },
    ];
    expect(() => deserializeDocument(JSON.stringify(effectsEnvelope))).toThrow(PersistenceError);

    const filtersEnvelope = structuredClone(toSerializedDocument(fixture())) as unknown as {
      document: { layers: Array<Record<string, unknown>> };
    };
    const smartLayer = filtersEnvelope.document.layers[4];
    if (!smartLayer) throw new Error('Expected smart-object fixture layer');
    smartLayer.smartFilters = [
      { id: 'blur', type: 'gaussian-blur', enabled: true, opacity: 1, radius: -1 },
    ];
    expect(() => deserializeDocument(JSON.stringify(filtersEnvelope))).toThrow(PersistenceError);
  });

  it('rejects non-finite and singular schema version 5 transforms', () => {
    const envelope = JSON.parse(serializeDocument(fixture())) as {
      document: { layers: Array<{ transform: Record<string, number> }> };
    };
    const firstLayer = envelope.document.layers[0];
    if (firstLayer === undefined) throw new Error('Expected fixture layer');
    firstLayer.transform = { a: 1, b: 2, c: 2, d: 4, e: 0, f: 0 };
    expect(() => deserializeDocument(JSON.stringify(envelope))).toThrow(
      'Serialized document is invalid',
    );

    firstLayer.transform = { a: 1, b: 0, c: 0, d: 1, e: Infinity, f: 0 };
    expect(() => deserializeDocument(JSON.stringify(envelope))).toThrow(PersistenceError);
  });
});

describe('in-memory document repository', () => {
  it('inspects document metadata before reading CAS resources and memoizes hydration', async () => {
    const repository = new InMemoryDocumentRepository();
    const document = fixture();
    await repository.save(document);

    const prepared = await repository.prepareLoad(document.id);
    expect(prepared?.metadata).toEqual({
      id: document.id,
      name: document.name,
      width: document.canvas.width,
      height: document.canvas.height,
      topLevelLayerCount: document.layers.length,
      referencedResourceCount: repository.lastSaveStats?.referencedResourceCount,
      legacy: false,
    });
    expect(repository.lastLoadStats).toMatchObject({
      phase: 'metadata',
      referencedResourceCount: repository.lastSaveStats?.referencedResourceCount,
      readResourceCount: 0,
      readResourceBytes: 0,
    });

    const firstHydration = prepared?.hydrate();
    const secondHydration = prepared?.hydrate();
    expect(secondHydration).toBe(firstHydration);
    await expect(firstHydration).resolves.toEqual(document);
    expect(repository.lastLoadStats).toMatchObject({
      phase: 'hydrated',
      referencedResourceCount: repository.lastSaveStats?.referencedResourceCount,
      readResourceCount: repository.lastSaveStats?.referencedResourceCount,
    });
    expect(repository.lastLoadStats?.readResourceBytes).toBeGreaterThan(0);
  });

  it('saves owned snapshots and supports load, sorted list, and delete', async () => {
    const repository = new InMemoryDocumentRepository();
    const document = fixture();
    await repository.save(document);
    await repository.save(createDocument({ id: 'aaa', name: 'A', width: 1, height: 1 }));

    const loaded = await repository.load(document.id);
    expect(loaded).toEqual(document);
    expect(loaded).not.toBe(document);
    expect(await repository.list()).toEqual(['aaa', 'doc-1']);
    expect(await repository.delete('doc-1')).toBe(true);
    expect(await repository.delete('doc-1')).toBe(false);
    expect(await repository.load('doc-1')).toBeUndefined();
  });

  it('writes only new CAS tiles across metadata and COW revisions and collects stale blobs', async () => {
    let store = TiledPixelStore.empty(512, 512);
    for (const [x, y] of [
      [10, 10],
      [266, 10],
      [10, 266],
      [266, 266],
    ] as const) {
      store = store.write({
        x,
        y,
        width: 1,
        height: 1,
        pixels: new Uint8ClampedArray([10, 20, 30, 255]),
      }).store;
    }
    const repository = new InMemoryDocumentRepository();
    const document = createDocument({
      id: 'incremental',
      name: 'Incremental',
      width: 512,
      height: 512,
      layers: [
        createPixelLayer({
          id: 'pixels',
          name: 'Pixels',
          width: 512,
          height: 512,
          pixelStore: store,
        }),
      ],
    });
    await repository.save(document);
    expect(repository.lastSaveStats).toMatchObject({
      referencedResourceCount: 1,
      writtenResourceCount: 1,
      reusedResourceCount: 0,
      removedResourceCount: 0,
    });
    expect(repository.lastSaveStats?.metadataBytes).toBeLessThan(10_000);

    const metadata = setLayerOpacity('pixels', 0.5).execute(document);
    await repository.save(metadata);
    expect(repository.lastSaveStats).toMatchObject({
      referencedResourceCount: 1,
      writtenResourceCount: 0,
      writtenResourceBytes: 0,
      reusedResourceCount: 1,
      removedResourceCount: 0,
    });

    const edited = patchPixels('pixels', {
      x: 10,
      y: 10,
      width: 1,
      height: 1,
      pixels: new Uint8ClampedArray([200, 100, 50, 255]),
    }).execute(metadata);
    await repository.save(edited);
    expect(repository.lastSaveStats).toMatchObject({
      referencedResourceCount: 2,
      writtenResourceCount: 1,
      writtenResourceBytes: 256 * 256 * 4,
      reusedResourceCount: 1,
      removedResourceCount: 0,
    });
    await expect(repository.load(document.id)).resolves.toEqual(edited);

    await repository.save(
      createDocument({ id: document.id, name: document.name, width: 512, height: 512 }),
    );
    expect(repository.lastSaveStats).toMatchObject({
      referencedResourceCount: 0,
      writtenResourceCount: 0,
      removedResourceCount: 2,
    });
  });

  it('is selected as the fallback when IndexedDB is unavailable', () => {
    expect(createDocumentRepository()).toBeInstanceOf(InMemoryDocumentRepository);
  });
});
