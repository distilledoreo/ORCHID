import {
  createSelectionMask,
  createAlphaChannel,
  createDocument,
  createGroupLayer,
  createPixelLayer,
  createRasterMask,
  createRectangularSelection,
} from '@freetoshop/core-document';
import { TiledCoverageStore, TiledPixelStore } from '@freetoshop/pixel-storage';
import {
  createFilterPatch,
  createFilterPatches,
  type FilterDraft,
} from '../editor/filterOperations.js';
import { describe, expect, it } from 'vitest';
import {
  adoptWorkerResultResources,
  acceptWorkerResultStreamEvent,
  commitWorkerTaskResources,
  createWorkerResultStreamContext,
  createWorkerTaskDeserializationContext,
  createWorkerTaskSerializationContext,
  deserializeWorkerTask,
  deserializeWorkerResult,
  deserializeStreamedWorkerResult,
  pruneWorkerTaskDeserializationContext,
  retainedWorkerTaskResourceIds,
  serializeWorkerResult,
  serializeWorkerTask,
  serializedCoverageSnapshot,
  serializedPixelSnapshot,
  streamWorkerResultTiles,
  workerResultTransferables,
  workerTaskRetainedResourceStats,
  workerTaskTransferables,
  type SerializedImageWorkerTask,
  type SerializedImageWorkerResult,
  type ImageWorkerEvent,
} from './protocol.js';

describe('image worker tile protocol', () => {
  const filterDraft = (kind: FilterDraft['kind'], blurRadius = 0): FilterDraft => ({
    kind,
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
    blurRadius,
  });
  it('round-trips nested canonical tile resources through structured clone and transfer', () => {
    const task = serializeWorkerTask({
      kind: 'resize',
      width: 4,
      height: 4,
      method: 'nearest',
      document: createDocument({
        id: 'document',
        name: 'Document',
        width: 2,
        height: 2,
        selection: createSelectionMask({
          width: 2,
          height: 2,
          coverage: new Uint8ClampedArray([0, 64, 128, 255]),
        }),
        channels: [
          createAlphaChannel({
            id: 'alpha',
            name: 'Alpha',
            width: 2,
            height: 2,
            coverage: [255, 128, 64, 0],
          }),
        ],
        layers: [
          createGroupLayer({
            id: 'group',
            name: 'Group',
            children: [
              createPixelLayer({
                id: 'pixel',
                name: 'Pixel',
                width: 2,
                height: 2,
                pixels: new Uint8ClampedArray(16).fill(75),
                rasterMask: createRasterMask(2, 2, [0, 64, 128, 255]),
              }),
            ],
          }),
        ],
      }),
    });
    const transferables = workerTaskTransferables(task);
    expect(transferables).toHaveLength(4);
    const cloned = structuredClone(task, { transfer: transferables }) as SerializedImageWorkerTask;
    expect((transferables[0] as ArrayBuffer).byteLength).toBe(0);
    const hydrated = deserializeWorkerTask(cloned);
    if (hydrated.kind !== 'resize') throw new Error('Expected resize task');
    const group = hydrated.document.layers[0];
    const pixel = group?.kind === 'group' ? group.children[0] : undefined;
    expect(pixel?.kind).toBe('pixel');
    if (pixel?.kind === 'pixel') {
      expect(pixel.pixelStore.toContiguous()).toEqual(new Uint8ClampedArray(16).fill(75));
      expect(pixel.rasterMask?.coverageStore.toContiguous()).toEqual(
        new Uint8ClampedArray([0, 64, 128, 255]),
      );
    }
    expect(hydrated.document.selection?.coverageStore?.toContiguous()).toEqual(
      new Uint8ClampedArray([0, 64, 128, 255]),
    );
    expect(hydrated.document.channels[0]?.coverageStore?.toContiguous()).toEqual(
      new Uint8ClampedArray([255, 128, 64, 0]),
    );
  });

  it('transfers every allocated tile in a 4096px resize result without cloning buffers', () => {
    let pixels = TiledPixelStore.empty(4096, 4096, 256);
    pixels = pixels.write({
      x: 0,
      y: 0,
      width: 1,
      height: 1,
      pixels: new Uint8ClampedArray([10, 20, 30, 255]),
    }).store;
    pixels = pixels.write({
      x: 4095,
      y: 4095,
      width: 1,
      height: 1,
      pixels: new Uint8ClampedArray([40, 50, 60, 255]),
    }).store;
    let coverage = TiledCoverageStore.empty(4096, 4096, 256);
    coverage = coverage.write({
      x: 4095,
      y: 4095,
      width: 1,
      height: 1,
      coverage: new Uint8ClampedArray([255]),
    }).store;
    const result = serializeWorkerResult({
      kind: 'resize',
      document: createDocument({
        id: 'large-result',
        name: 'Large result',
        width: 4096,
        height: 4096,
        layers: [
          createPixelLayer({
            id: 'pixels',
            name: 'Pixels',
            width: 4096,
            height: 4096,
            pixelStore: pixels,
          }),
        ],
        selection: createSelectionMask({ width: 4096, height: 4096, coverageStore: coverage }),
      }),
    });
    const transferables = workerResultTransferables(result);
    expect(transferables).toHaveLength(3);
    expect(
      transferables.reduce<number>(
        (total, buffer) => total + (buffer as ArrayBuffer).byteLength,
        0,
      ),
    ).toBe(2 * 256 * 256 * 4 + 256 * 256);
    const cloned = structuredClone(result, {
      transfer: transferables,
    }) as SerializedImageWorkerResult;
    expect(transferables.every((buffer) => (buffer as ArrayBuffer).byteLength === 0)).toBe(true);
    const hydrated = deserializeWorkerResult(cloned);
    if (hydrated.kind !== 'resize') throw new Error('Expected resize result');
    const layer = hydrated.document.layers[0];
    expect(layer?.kind).toBe('pixel');
    if (layer?.kind === 'pixel') {
      expect(layer.pixelStore.read({ x: 4095, y: 4095, width: 1, height: 1 })).toEqual(
        new Uint8ClampedArray([40, 50, 60, 255]),
      );
    }
    expect(
      hydrated.document.selection?.coverageStore.read({
        x: 4095,
        y: 4095,
        width: 1,
        height: 1,
      }),
    ).toEqual(new Uint8ClampedArray([255]));
  });

  it('streams resize result resources tile-by-tile before a metadata-only terminal result', () => {
    let pixels = TiledPixelStore.empty(512, 512, 256);
    pixels = pixels.write({
      x: 0,
      y: 0,
      width: 1,
      height: 1,
      pixels: new Uint8ClampedArray([10, 20, 30, 255]),
    }).store;
    pixels = pixels.write({
      x: 300,
      y: 300,
      width: 1,
      height: 1,
      pixels: new Uint8ClampedArray([40, 50, 60, 255]),
    }).store;
    const coverage = TiledCoverageStore.uniform(512, 512, 255, 256).write({
      x: 300,
      y: 300,
      width: 1,
      height: 1,
      coverage: new Uint8ClampedArray([0]),
    }).store;
    const serialized = serializeWorkerResult(
      {
        kind: 'resize',
        document: createDocument({
          id: 'stream-result',
          name: 'Stream result',
          width: 512,
          height: 512,
          layers: [
            createPixelLayer({
              id: 'pixels',
              name: 'Pixels',
              width: 512,
              height: 512,
              pixelStore: pixels,
            }),
          ],
          selection: createSelectionMask({ width: 512, height: 512, coverageStore: coverage }),
        }),
      },
      createWorkerTaskSerializationContext('stream-'),
    );
    const events: ImageWorkerEvent[] = [];
    const progress: Array<readonly [number, number]> = [];
    const terminal = streamWorkerResultTiles(
      'request',
      serialized,
      (event, transfer = []) =>
        events.push(structuredClone(event, { transfer }) as ImageWorkerEvent),
      (completed, total) => progress.push([completed, total]),
    );
    expect(workerResultTransferables(terminal)).toEqual([]);
    expect(events.filter((event) => event.type === 'result-resource')).toHaveLength(2);
    expect(events.filter((event) => event.type === 'result-resource-tile')).toHaveLength(3);
    expect(progress).toEqual([
      [1, 3],
      [2, 3],
      [3, 3],
    ]);

    const stream = createWorkerResultStreamContext();
    for (const event of events) {
      if (event.type === 'result-resource' || event.type === 'result-resource-tile') {
        acceptWorkerResultStreamEvent(stream, event);
      }
    }
    expect(stream.hydratedResources.pixelResources.size).toBe(1);
    expect(stream.hydratedResources.coverageResources.size).toBe(1);
    const hydrated = deserializeStreamedWorkerResult(terminal, stream);
    if (hydrated.kind !== 'resize') throw new Error('Expected streamed resize result');
    const layer = hydrated.document.layers[0];
    if (layer?.kind !== 'pixel') throw new Error('Expected streamed pixel layer');
    expect(layer.pixelStore.tileCount).toBe(2);
    expect(layer.pixelStore.read({ x: 300, y: 300, width: 1, height: 1 })).toEqual(
      new Uint8ClampedArray([40, 50, 60, 255]),
    );
    expect(hydrated.document.selection?.coverageStore.defaultCoverage).toBe(255);
    expect(
      hydrated.document.selection?.coverageStore.read({ x: 300, y: 300, width: 1, height: 1 }),
    ).toEqual(new Uint8ClampedArray([0]));

    const incomplete = createWorkerResultStreamContext();
    for (const event of events.slice(0, -1)) {
      if (event.type === 'result-resource' || event.type === 'result-resource-tile') {
        acceptWorkerResultStreamEvent(incomplete, event);
      }
    }
    expect(() => deserializeStreamedWorkerResult(terminal, incomplete)).toThrow('incomplete');

    const outOfOrder = createWorkerResultStreamContext();
    const pixelManifest = events.find(
      (event) => event.type === 'result-resource' && event.resourceKind === 'pixel',
    );
    const secondPixelTile = events.find(
      (event) =>
        event.type === 'result-resource-tile' &&
        event.resourceKind === 'pixel' &&
        event.tileIndex === 1,
    );
    if (
      pixelManifest?.type !== 'result-resource' ||
      secondPixelTile?.type !== 'result-resource-tile'
    ) {
      throw new Error('Expected streamed pixel fixture events');
    }
    acceptWorkerResultStreamEvent(outOfOrder, pixelManifest);
    expect(() => acceptWorkerResultStreamEvent(outOfOrder, secondPixelTile)).toThrow(
      'out of order',
    );

    const malformed = createWorkerResultStreamContext();
    acceptWorkerResultStreamEvent(malformed, {
      id: 'request',
      type: 'result-resource',
      resourceKind: 'pixel',
      resourceId: 'transparent',
      width: 1,
      height: 1,
      tileSize: 256,
      totalTiles: 1,
    });
    expect(() =>
      acceptWorkerResultStreamEvent(malformed, {
        id: 'request',
        type: 'result-resource-tile',
        resourceKind: 'pixel',
        resourceId: 'transparent',
        tileIndex: 0,
        column: 0,
        row: 0,
        pixels: new Uint8ClampedArray(4),
      }),
    ).toThrow('must contain allocated pixels');
    expect(malformed.hydratedResources.pixelResources.size).toBe(0);

    const empty = createWorkerResultStreamContext();
    acceptWorkerResultStreamEvent(empty, {
      id: 'request',
      type: 'result-resource',
      resourceKind: 'coverage',
      resourceId: 'empty-coverage',
      width: 4096,
      height: 4096,
      tileSize: 256,
      defaultCoverage: 255,
      totalTiles: 0,
    });
    expect(empty.hydratedResources.coverageResources.get('empty-coverage')?.tileCount).toBe(0);

    const excessive = createWorkerResultStreamContext();
    expect(() =>
      acceptWorkerResultStreamEvent(excessive, {
        id: 'request',
        type: 'result-resource',
        resourceKind: 'pixel',
        resourceId: 'excessive',
        width: 1,
        height: 1,
        tileSize: 256,
        totalTiles: 2,
      }),
    ).toThrow('exceeds its store capacity');
    expect(excessive.resources.size).toBe(0);
  });

  it('preserves PSD import reports while streaming the imported document resources', () => {
    const report = {
      layerCount: 1,
      rasterizedStructuredLayers: 0,
      skippedLayers: 0,
      importedRasterMasks: 0,
      importedVectorMasks: 0,
      importedEffects: 0,
      importedAdjustmentLayers: 0,
      importedTextLayers: 0,
      importedSmartObjects: 0,
      importedNestedSmartObjectSources: 0,
      preservedLinkedFiles: 0,
      importedAlphaChannels: 0,
      importedAlphaChannelsWithCoverage: 0,
      preservedDocumentPaths: 0,
      importedGuides: 0,
      importedGrid: false,
      importedDocumentPaths: 0,
      warnings: ['Unsupported PSD feature preserved as metadata.'],
    };
    const serialized = serializeWorkerResult(
      {
        kind: 'psd-import',
        document: createDocument({
          id: 'psd-stream',
          name: 'PSD stream',
          width: 1,
          height: 1,
          layers: [
            createPixelLayer({
              id: 'pixel',
              name: 'Pixel',
              width: 1,
              height: 1,
              pixels: new Uint8ClampedArray([20, 40, 60, 255]),
            }),
          ],
        }),
        report,
      },
      createWorkerTaskSerializationContext('psd-stream-'),
    );
    const stream = createWorkerResultStreamContext();
    const terminal = streamWorkerResultTiles('psd-request', serialized, (event) => {
      acceptWorkerResultStreamEvent(stream, structuredClone(event));
    });
    expect(workerResultTransferables(terminal)).toEqual([]);
    const hydrated = deserializeStreamedWorkerResult(terminal, stream);
    if (hydrated.kind !== 'psd-import') throw new Error('Expected streamed PSD result');
    expect(hydrated.report).toEqual(report);
    const layer = hydrated.document.layers[0];
    if (layer?.kind !== 'pixel') throw new Error('Expected imported pixel layer');
    expect(layer.pixelStore.read({ x: 0, y: 0, width: 1, height: 1 })).toEqual(
      new Uint8ClampedArray([20, 40, 60, 255]),
    );
  });

  it('adopts transferred document results as zero-upload persistent inputs', () => {
    const document = createDocument({
      id: 'adopted-result',
      name: 'Adopted result',
      width: 256,
      height: 256,
      layers: [
        createPixelLayer({
          id: 'pixels',
          name: 'Pixels',
          width: 256,
          height: 256,
          pixelStore: TiledPixelStore.empty(256, 256).write({
            x: 10,
            y: 10,
            width: 1,
            height: 1,
            pixels: new Uint8ClampedArray([40, 50, 60, 255]),
          }).store,
        }),
      ],
    });
    const serialized = serializeWorkerResult(
      { kind: 'resize', document },
      createWorkerTaskSerializationContext('result-request-'),
    );
    if (serialized.kind !== 'resize') throw new Error('Expected resize result');
    const serializedLayer = serialized.document.layers[0];
    if (serializedLayer?.kind !== 'pixel') throw new Error('Expected pixel result layer');
    expect(serializedLayer.pixelStore.id).toMatch(/^result-request-pixel-/);

    const workerResources = createWorkerTaskDeserializationContext();
    deserializeWorkerResult(serialized, workerResources);
    const cloned = structuredClone(serialized, {
      transfer: workerResultTransferables(serialized),
    }) as SerializedImageWorkerResult;
    const hydrated = deserializeWorkerResult(cloned);
    const clientResources = createWorkerTaskSerializationContext();
    const priorDocument = createDocument({
      id: 'prior-generation',
      name: 'Prior generation',
      width: 256,
      height: 256,
      layers: [
        createPixelLayer({
          id: 'prior-pixels',
          name: 'Prior pixels',
          width: 256,
          height: 256,
          pixels: new Uint8ClampedArray(256 * 256 * 4).fill(7),
        }),
      ],
    });
    const priorTask = serializeWorkerTask(
      { kind: 'native-export', document: priorDocument },
      clientResources,
    );
    if (priorTask.kind !== 'native-export') throw new Error('Expected prior native export');
    const priorLayer = priorTask.document.layers[0];
    if (priorLayer?.kind !== 'pixel') throw new Error('Expected prior pixel layer');
    const priorResourceId = priorLayer.pixelStore.id;
    commitWorkerTaskResources(priorTask, clientResources);
    expect(workerTaskRetainedResourceStats(clientResources)).toEqual({
      resourceCount: 1,
      allocatedByteLength: 256 * 256 * 4,
    });
    adoptWorkerResultResources(cloned, hydrated, clientResources);
    if (hydrated.kind !== 'resize') throw new Error('Expected hydrated resize result');
    expect(workerTaskRetainedResourceStats(clientResources)).toEqual({
      resourceCount: 1,
      allocatedByteLength: 256 * 256 * 4,
    });
    expect(clientResources.resourceOwners.has(priorResourceId)).toBe(false);

    const nextTask = serializeWorkerTask(
      { kind: 'native-export', document: hydrated.document },
      clientResources,
    );
    expect(workerTaskTransferables(nextTask)).toHaveLength(0);
    if (nextTask.kind !== 'native-export') throw new Error('Expected native export task');
    const nextLayer = nextTask.document.layers[0];
    if (nextLayer?.kind !== 'pixel') throw new Error('Expected referenced pixel layer');
    expect(nextLayer.pixelStore).toMatchObject({
      resource: 'reference',
      id: serializedLayer.pixelStore.id,
    });
    const resolved = deserializeWorkerTask(nextTask, workerResources);
    if (resolved.kind !== 'native-export') throw new Error('Expected hydrated native export');
    const resolvedLayer = resolved.document.layers[0];
    if (resolvedLayer?.kind !== 'pixel') throw new Error('Expected resolved pixel layer');
    expect(resolvedLayer.pixelStore.read({ x: 10, y: 10, width: 1, height: 1 })).toEqual(
      new Uint8ClampedArray([40, 50, 60, 255]),
    );
  });

  it('deduplicates shared result buffers before structured-clone transfer', () => {
    const pixels = new Uint8ClampedArray([10, 20, 30, 255]);
    expect(
      workerResultTransferables({
        kind: 'filter',
        patches: [
          { x: 0, y: 0, width: 1, height: 1, pixels },
          { x: 1, y: 0, width: 1, height: 1, pixels },
        ],
      }),
    ).toEqual([pixels.buffer]);
  });

  it('preserves complement defaults through worker snapshots without full-canvas tiles', () => {
    const coverage = TiledCoverageStore.uniform(4096, 4096, 255, 256).write({
      x: 1024,
      y: 2048,
      width: 1,
      height: 1,
      coverage: new Uint8ClampedArray([0]),
    }).store;
    const task = serializeWorkerTask({
      kind: 'resize',
      width: 2048,
      height: 2048,
      method: 'nearest',
      document: createDocument({
        id: 'complement-worker',
        name: 'Complement worker',
        width: 4096,
        height: 4096,
        selection: createSelectionMask({ width: 4096, height: 4096, coverageStore: coverage }),
      }),
    });
    if (task.kind !== 'resize') throw new Error('Expected resize task');
    const serializedSelection = task.document.selection;
    if (serializedSelection === null) throw new Error('Expected serialized selection');
    expect(serializedCoverageSnapshot(serializedSelection.coverageStore)).toMatchObject({
      schemaVersion: 2,
      defaultCoverage: 255,
    });
    expect(serializedCoverageSnapshot(serializedSelection.coverageStore).tiles).toHaveLength(1);
    const transferables = workerTaskTransferables(task);
    expect(transferables).toHaveLength(1);
    const cloned = structuredClone(task, { transfer: transferables }) as SerializedImageWorkerTask;
    const hydrated = deserializeWorkerTask(cloned);
    if (hydrated.kind !== 'resize') throw new Error('Expected resize task');
    expect(hydrated.document.selection?.coverageStore).toMatchObject({
      defaultCoverage: 255,
      tileCount: 1,
    });
    expect(
      hydrated.document.selection?.coverageStore.read({ x: 0, y: 0, width: 1, height: 1 })[0],
    ).toBe(255);
  });

  it('rejects malformed tile snapshots received from a worker boundary', () => {
    const task = serializeWorkerTask({
      kind: 'filter',
      layer: createPixelLayer({ id: 'pixel', name: 'Pixel', width: 1, height: 1 }),
      selection: null,
      worldTransform: { a: 1, b: 0, c: 0, d: 1, e: 0, f: 0 },
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
    if (task.kind !== 'filter') throw new Error('Expected filter task');
    const malformed = {
      ...task,
      layer: {
        ...task.layer,
        pixelStore: {
          resource: 'snapshot',
          schemaVersion: 1,
          id: 'malformed',
          snapshot: { ...serializedPixelSnapshot(task.layer.pixelStore), schemaVersion: 99 },
        },
      },
    } as unknown as SerializedImageWorkerTask;
    expect(() => deserializeWorkerTask(malformed)).toThrow('Unsupported tiled pixel snapshot');
  });

  it('round-trips composite tiles while omitting editor-only selection data', () => {
    const task = serializeWorkerTask({
      kind: 'composite',
      generation: 7,
      document: createDocument({
        id: 'document',
        name: 'Document',
        width: 2,
        height: 2,
        layers: [
          createPixelLayer({
            id: 'pixel',
            name: 'Pixel',
            width: 2,
            height: 2,
            pixels: new Uint8ClampedArray(16).fill(33),
          }),
        ],
        selection: createSelectionMask({
          width: 2,
          height: 2,
          coverage: new Uint8ClampedArray([0, 64, 128, 255]),
        }),
      }),
    });
    expect(task.kind).toBe('composite');
    if (task.kind !== 'composite') throw new Error('Expected composite task');
    expect(task.generation).toBe(7);
    const transferables = workerTaskTransferables(task);
    expect(transferables.length).toBeGreaterThan(0);
    const cloned = structuredClone(task, { transfer: transferables }) as SerializedImageWorkerTask;
    const hydrated = deserializeWorkerTask(cloned);
    if (hydrated.kind !== 'composite') throw new Error('Expected composite task');
    expect(hydrated.generation).toBe(7);
    const pixel = hydrated.document.layers[0];
    expect(pixel?.kind).toBe('pixel');
    if (pixel?.kind === 'pixel') {
      expect(pixel.pixelStore.toContiguous()).toEqual(new Uint8ClampedArray(16).fill(33));
    }
    expect(hydrated.document.selection).toBeNull();
  });

  it('transfers only changed pixel and coverage tiles across immutable store revisions', () => {
    const width = 1024;
    const height = 1024;
    const points = [
      [10, 10],
      [300, 20],
      [20, 700],
      [900, 900],
    ] as const;
    let pixels = TiledPixelStore.empty(width, height);
    let coverage = TiledCoverageStore.empty(width, height);
    for (const [x, y] of points) {
      pixels = pixels.write({
        x,
        y,
        width: 1,
        height: 1,
        pixels: new Uint8ClampedArray([10, 20, 30, 255]),
      }).store;
      coverage = coverage.write({
        x,
        y,
        width: 1,
        height: 1,
        coverage: new Uint8ClampedArray([255]),
      }).store;
    }
    const layer = createPixelLayer({
      id: 'delta-layer',
      name: 'Delta layer',
      width,
      height,
      pixelStore: pixels,
      rasterMask: { width, height, coverageStore: coverage, enabled: true, density: 1 },
    });
    const document = createDocument({
      id: 'delta-document',
      name: 'Delta document',
      width,
      height,
      layers: [layer],
    });
    const serialization = createWorkerTaskSerializationContext();
    const deserialization = createWorkerTaskDeserializationContext();
    const dispatch = (candidate: typeof document, generation: number) => {
      const task = serializeWorkerTask(
        { kind: 'composite', document: candidate, generation },
        serialization,
      );
      const retained = retainedWorkerTaskResourceIds(serialization);
      const transferables = workerTaskTransferables(task);
      const transferredBytes = transferables.reduce<number>(
        (total, value) => total + (value instanceof ArrayBuffer ? value.byteLength : 0),
        0,
      );
      const cloned = structuredClone(task, {
        transfer: transferables,
      }) as SerializedImageWorkerTask;
      commitWorkerTaskResources(task, serialization);
      const hydrated = deserializeWorkerTask(cloned, deserialization);
      pruneWorkerTaskDeserializationContext(deserialization, retained);
      if (hydrated.kind !== 'composite') throw new Error('Expected composite task');
      return { task, hydrated: hydrated.document, transferredBytes };
    };

    const initial = dispatch(document, 1);
    expect(initial.transferredBytes).toBe(4 * 256 * 256 * 4 + 4 * 256 * 256);

    const revisedPixels = pixels.write({
      x: 10,
      y: 10,
      width: 1,
      height: 1,
      pixels: new Uint8ClampedArray([200, 100, 50, 255]),
    }).store;
    const revisedCoverage = coverage.write({
      x: 10,
      y: 10,
      width: 1,
      height: 1,
      coverage: new Uint8ClampedArray([128]),
    }).store;
    if (layer.rasterMask === null) throw new Error('Expected masked pixel layer');
    const revisedLayer = {
      ...layer,
      pixelStore: revisedPixels,
      rasterMask: { ...layer.rasterMask, coverageStore: revisedCoverage },
    };
    const revised = { ...document, layers: [revisedLayer] };
    const delta = dispatch(revised, 2);
    if (delta.task.kind !== 'composite') throw new Error('Expected composite task');
    const serializedLayer = delta.task.document.layers[0];
    if (serializedLayer?.kind !== 'pixel' || serializedLayer.rasterMask === null) {
      throw new Error('Expected masked pixel layer');
    }
    expect(serializedLayer.pixelStore).toMatchObject({ resource: 'delta', schemaVersion: 2 });
    expect(serializedLayer.rasterMask.coverageStore).toMatchObject({
      resource: 'delta',
      schemaVersion: 2,
    });
    expect(delta.transferredBytes).toBe(256 * 256 * 4 + 256 * 256);
    const hydratedLayer = delta.hydrated.layers[0];
    if (hydratedLayer?.kind !== 'pixel' || hydratedLayer.rasterMask === null) {
      throw new Error('Expected hydrated masked pixel layer');
    }
    expect(hydratedLayer.pixelStore.read({ x: 10, y: 10, width: 1, height: 1 })).toEqual(
      new Uint8ClampedArray([200, 100, 50, 255]),
    );
    expect(
      hydratedLayer.rasterMask.coverageStore.read({ x: 10, y: 10, width: 1, height: 1 }),
    ).toEqual(new Uint8ClampedArray([128]));

    const removedPixels = revisedPixels.write({
      x: 10,
      y: 10,
      width: 1,
      height: 1,
      pixels: new Uint8ClampedArray(4),
    }).store;
    const removedCoverage = revisedCoverage.write({
      x: 10,
      y: 10,
      width: 1,
      height: 1,
      coverage: new Uint8ClampedArray(1),
    }).store;
    const removed = dispatch(
      {
        ...document,
        layers: [
          {
            ...revisedLayer,
            pixelStore: removedPixels,
            rasterMask: { ...revisedLayer.rasterMask, coverageStore: removedCoverage },
          },
        ],
      },
      3,
    );
    expect(removed.transferredBytes).toBe(0);
    const removedLayer = removed.hydrated.layers[0];
    if (removedLayer?.kind !== 'pixel' || removedLayer.rasterMask === null) {
      throw new Error('Expected hydrated removed-tile layer');
    }
    expect(removedLayer.pixelStore.tileCount).toBe(3);
    expect(removedLayer.rasterMask.coverageStore.tileCount).toBe(3);
  });

  it('hydrates selection coverage stores for committed filter work', () => {
    const task = serializeWorkerTask({
      kind: 'filter',
      layer: createPixelLayer({ id: 'pixel', name: 'Pixel', width: 2, height: 1 }),
      selection: createSelectionMask({
        width: 2,
        height: 1,
        coverage: new Uint8ClampedArray([255, 32]),
      }),
      worldTransform: { a: 1, b: 0, c: 0, d: 1, e: 0, f: 0 },
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
    if (task.kind !== 'filter') throw new Error('Expected filter task');
    const transferables = workerTaskTransferables(task);
    expect(transferables).toHaveLength(1);
    const cloned = structuredClone(task, { transfer: transferables }) as SerializedImageWorkerTask;
    const hydrated = deserializeWorkerTask(cloned);
    if (hydrated.kind !== 'filter') throw new Error('Expected filter task');
    expect(hydrated.selection?.coverageStore?.toContiguous()).toEqual(
      new Uint8ClampedArray([255, 32]),
    );
  });

  it('preserves unrelated document resources across filter work and sends only the changed tile', () => {
    const width = 512;
    const height = 512;
    const mask = () =>
      createRasterMask(width, height, new Uint8ClampedArray(width * height).fill(200));
    const layers = [17, 37].map((value, index) =>
      createPixelLayer({
        id: `cross-kind-${String(index)}`,
        name: `Cross kind ${String(index)}`,
        width,
        height,
        pixels: new Uint8ClampedArray(width * height * 4).fill(value),
        rasterMask: mask(),
      }),
    );
    const document = createDocument({
      id: 'cross-kind-document',
      name: 'Cross kind document',
      width,
      height,
      layers: [
        createGroupLayer({ id: 'cross-kind-group', name: 'Cross kind group', children: layers }),
      ],
    });
    const firstLayer = layers[0];
    const secondLayer = layers[1];
    if (!firstLayer || !secondLayer) throw new Error('Expected two cross-kind layers');
    const context = createWorkerTaskSerializationContext();
    const dispatch = (task: Parameters<typeof serializeWorkerTask>[0]) => {
      const serialized = serializeWorkerTask(task, context);
      const bytes = workerTaskTransferables(serialized).reduce<number>(
        (total, value) => total + (value instanceof ArrayBuffer ? value.byteLength : 0),
        0,
      );
      commitWorkerTaskResources(serialized, context);
      return bytes;
    };

    expect(dispatch({ kind: 'composite', document, generation: 1 })).toBe(
      2 * width * height * 4 + 2 * width * height,
    );
    expect(workerTaskRetainedResourceStats(context).resourceCount).toBe(4);
    expect(
      dispatch({
        kind: 'filter',
        layer: firstLayer,
        selection: createRectangularSelection(
          { width, height },
          { x: 10, y: 10, width: 16, height: 16 },
        ),
        worldTransform: firstLayer.transform,
        draft: filterDraft('invert'),
      }),
    ).toBe(256 * 256);
    expect(workerTaskRetainedResourceStats(context).resourceCount).toBe(5);
    expect(dispatch({ kind: 'composite', document, generation: 2 })).toBe(0);

    const changedStore = firstLayer.pixelStore.write({
      x: 10,
      y: 10,
      width: 1,
      height: 1,
      pixels: new Uint8ClampedArray([220, 120, 20, 255]),
    }).store;
    const changed = {
      ...document,
      layers: [
        createGroupLayer({
          id: 'cross-kind-group',
          name: 'Cross kind group',
          children: [{ ...firstLayer, pixelStore: changedStore }, secondLayer],
        }),
      ],
    };
    expect(dispatch({ kind: 'composite', document: changed, generation: 3 })).toBe(256 * 256 * 4);
    expect(workerTaskRetainedResourceStats(context).resourceCount).toBe(4);
  });

  it('transfers only selection-intersecting source tiles for a 4096px filter', () => {
    let store = TiledPixelStore.empty(4096, 4096, 64);
    for (let index = 0; index < 32; index += 1) {
      const x = (index % 8) * 512 + 8;
      const y = Math.floor(index / 8) * 512 + 8;
      store = store.write({
        x,
        y,
        width: 1,
        height: 1,
        pixels: new Uint8ClampedArray([20 + index, 80, 140, 255]),
      }).store;
    }
    // Put actual pixels inside the selected tile.
    store = store.write({
      x: 520,
      y: 520,
      width: 8,
      height: 8,
      pixels: new Uint8ClampedArray(8 * 8 * 4).fill(120),
    }).store;
    const layer = createPixelLayer({
      id: 'large-filter',
      name: 'Large filter',
      width: 4096,
      height: 4096,
      pixelStore: store,
      rasterMask: createRasterMask(4096, 4096, new Uint8ClampedArray(4096 * 4096).fill(255)),
    });
    const selection = createRectangularSelection(
      { width: 4096, height: 4096 },
      { x: 520, y: 520, width: 8, height: 8 },
    );
    const draft = filterDraft('invert');
    const direct = createFilterPatch(layer, selection, layer.transform, draft);
    const directPatches = createFilterPatches(layer, selection, layer.transform, draft);
    const task = serializeWorkerTask({
      kind: 'filter',
      layer,
      selection,
      worldTransform: layer.transform,
      draft,
    });
    if (task.kind !== 'filter') throw new Error('Expected filter task');
    expect(serializedPixelSnapshot(task.layer.pixelStore).tiles).toHaveLength(1);
    expect(task.layer.rasterMask).toBeNull();
    const transferables = workerTaskTransferables(task);
    expect(transferables).toHaveLength(2); // one RGBA source tile + one selection tile
    const transferredBytes = transferables.reduce<number>(
      (total, buffer) => total + (buffer as ArrayBuffer).byteLength,
      0,
    );
    expect(transferredBytes).toBeLessThan(store.allocatedByteLength / 4);
    expect(transferredBytes).toBe(64 * 64 * 4 + 256 * 256);
    const cloned = structuredClone(task, { transfer: transferables }) as SerializedImageWorkerTask;
    const hydrated = deserializeWorkerTask(cloned);
    if (hydrated.kind !== 'filter') throw new Error('Expected filter task');
    expect(
      createFilterPatch(
        hydrated.layer,
        hydrated.selection,
        hydrated.worldTransform,
        hydrated.draft,
      ),
    ).toEqual(direct);
    expect(
      createFilterPatches(
        hydrated.layer,
        hydrated.selection,
        hydrated.worldTransform,
        hydrated.draft,
      ),
    ).toEqual(directPatches);
    expect(directPatches.every((patch) => patch.width <= 256 && patch.height <= 256)).toBe(true);
  });

  it('includes the full two-radius source halo for selected Gaussian blur transfer', () => {
    const pixels = new Uint8ClampedArray(256 * 256 * 4);
    for (let index = 0; index < pixels.length; index += 4) {
      pixels[index] = (index / 4) % 251;
      pixels[index + 1] = 90;
      pixels[index + 2] = 180;
      pixels[index + 3] = 255;
    }
    const layer = createPixelLayer({ id: 'blur', name: 'Blur', width: 256, height: 256, pixels });
    const selection = createRectangularSelection(
      { width: 256, height: 256 },
      { x: 120, y: 120, width: 16, height: 16 },
    );
    const draft = filterDraft('gaussian-blur', 12);
    const direct = createFilterPatch(layer, selection, layer.transform, draft);
    const directPatches = createFilterPatches(layer, selection, layer.transform, draft);
    const task = serializeWorkerTask({
      kind: 'filter',
      layer,
      selection,
      worldTransform: layer.transform,
      draft,
    });
    const hydrated = deserializeWorkerTask(task);
    if (hydrated.kind !== 'filter') throw new Error('Expected filter task');
    expect(
      createFilterPatch(
        hydrated.layer,
        hydrated.selection,
        hydrated.worldTransform,
        hydrated.draft,
      ),
    ).toEqual(direct);
    expect(
      createFilterPatches(
        hydrated.layer,
        hydrated.selection,
        hydrated.worldTransform,
        hydrated.draft,
      ),
    ).toEqual(directPatches);
    expect(directPatches.every((patch) => patch.width <= 256 && patch.height <= 256)).toBe(true);
  });
});
