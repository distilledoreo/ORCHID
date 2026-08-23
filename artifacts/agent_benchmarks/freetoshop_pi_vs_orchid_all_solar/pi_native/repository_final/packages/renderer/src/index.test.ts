import {
  createSelectionMask,
  BLEND_MODES as DOCUMENT_BLEND_MODES,
  createAdjustmentLayer,
  createDocument,
  createGroupLayer,
  createPixelLayer,
  createRasterMask,
  createRasterMaskFromCoverageStore,
  createShapeLayer,
  createSmartObjectLayer,
  createVectorMask,
  type LayerTransform,
} from '@freetoshop/core-document';
import { BLEND_MODES as COMPOSITOR_BLEND_MODES } from '@freetoshop/compositor';
import { TiledCoverageStore, TiledPixelStore } from '@freetoshop/pixel-storage';
import { describe, expect, it, vi } from 'vitest';
import {
  clampZoom,
  CompositeTileCache,
  compositeDocumentPixels,
  drawGuidesAndGrid,
  renderDocument,
  screenToWorld,
  selectionOutlinePixels,
  selectionOutlineSegments,
  supportsDocumentRegionComposite,
  rasterizeLayerNearest,
  worldToScreen,
  zoomAtPoint,
} from './index.js';

function affine(a = 1, b = 0, c = 0, d = 1, e = 0, f = 0): LayerTransform {
  return { a, b, c, d, e, f };
}

it('reports exact region-composite eligibility for wand and cache consumers', () => {
  const pixels = createPixelLayer({ id: 'pixels', name: 'Pixels', width: 2, height: 2 });
  expect(
    supportsDocumentRegionComposite(
      createDocument({ id: 'simple', name: 'Simple', width: 2, height: 2, layers: [pixels] }),
    ),
  ).toBe(true);
  expect(
    supportsDocumentRegionComposite(
      createDocument({
        id: 'masked',
        name: 'Masked',
        width: 2,
        height: 2,
        layers: [{ ...pixels, rasterMask: createRasterMask(2, 2) }],
      }),
    ),
  ).toBe(true);
});

const layerDefaults = {
  fillOpacity: 1,
  blendMode: 'normal' as const,
  clipping: false,
  effects: [],
  locks: { transparency: false, pixels: false, position: false, all: false },
  rasterMask: null,
  vectorMask: null,
};

describe('viewport helpers', () => {
  it('keeps the document and compositor blend-mode contracts synchronized', () => {
    expect(DOCUMENT_BLEND_MODES).toEqual(COMPOSITOR_BLEND_MODES);
  });

  it('round-trips coordinates', () => {
    const viewport = { zoom: 2, panX: 10, panY: -4 };
    expect(screenToWorld(worldToScreen({ x: 3, y: 7 }, viewport), viewport)).toEqual({
      x: 3,
      y: 7,
    });
  });

  it('keeps the cursor anchor stable while zooming', () => {
    const point = { x: 80, y: 50 };
    const next = zoomAtPoint({ zoom: 1, panX: 10, panY: 10 }, point, 4);
    expect(worldToScreen(screenToWorld(point, { zoom: 1, panX: 10, panY: 10 }), next)).toEqual(
      point,
    );
    expect(clampZoom(Number.NaN)).toBe(1);
  });
});

describe('deterministic document compositing', () => {
  const composite = (layers: Parameters<typeof createDocument>[0]['layers'], width = 1) =>
    compositeDocumentPixels(createDocument({ id: 'doc', name: 'Doc', width, height: 1, layers }), {
      rasterize: rasterizeLayerNearest,
    });

  it('applies blend mode, layer opacity, and fill opacity through premultiplied alpha', () => {
    const output = composite([
      createPixelLayer({
        id: 'backdrop',
        name: 'Backdrop',
        width: 1,
        height: 1,
        pixels: [40, 80, 120, 255],
      }),
      createPixelLayer({
        id: 'source',
        name: 'Source',
        width: 1,
        height: 1,
        pixels: [200, 100, 50, 255],
        blendMode: 'multiply',
      }),
    ]);
    expect(output).toEqual(new Uint8ClampedArray([31, 31, 24, 255]));
  });

  it('uses the nearest preceding unclipped layer as clipping coverage', () => {
    const output = composite([
      createPixelLayer({
        id: 'base',
        name: 'Base',
        width: 1,
        height: 1,
        pixels: [0, 0, 255, 128],
      }),
      createPixelLayer({
        id: 'clip',
        name: 'Clip',
        width: 1,
        height: 1,
        pixels: [255, 0, 0, 255],
        clipping: true,
      }),
    ]);
    expect(output).toEqual(new Uint8ClampedArray([170, 0, 85, 192]));
  });

  it('rasterizes editable rectangle and ellipse shape layers deterministically', () => {
    const rectangle = compositeDocumentPixels(
      createDocument({
        id: 'shape-doc',
        name: 'Shape',
        width: 3,
        height: 2,
        layers: [
          createShapeLayer({
            id: 'rect',
            name: 'Rectangle',
            geometry: { type: 'rectangle', width: 2, height: 2, cornerRadius: 0 },
            style: { fill: { red: 255, green: 0, blue: 0, alpha: 255 } },
            transform: { ...affine(), e: 1 },
          }),
        ],
      }),
      { rasterize: rasterizeLayerNearest },
    );
    expect(rectangle).toEqual(
      new Uint8ClampedArray([
        0, 0, 0, 0, 255, 0, 0, 255, 255, 0, 0, 255, 0, 0, 0, 0, 255, 0, 0, 255, 255, 0, 0, 255,
      ]),
    );

    const ellipse = composite(
      [
        createShapeLayer({
          id: 'ellipse',
          name: 'Ellipse',
          geometry: { type: 'ellipse', width: 3, height: 1, cornerRadius: 0 },
          style: { fill: { red: 0, green: 255, blue: 0, alpha: 255 } },
        }),
      ],
      3,
    );
    expect(ellipse).toEqual(
      new Uint8ClampedArray([0, 255, 0, 255, 0, 255, 0, 255, 0, 255, 0, 255]),
    );
  });

  it('composites editable color overlay, stroke, and drop shadow effects', () => {
    const output = composite(
      [
        createPixelLayer({
          id: 'effected',
          name: 'Effected',
          width: 1,
          height: 1,
          pixels: [20, 40, 60, 255],
          transform: { ...affine(), e: 1 },
          effects: [
            {
              type: 'drop-shadow',
              enabled: true,
              opacity: 1,
              offsetX: 1,
              offsetY: 0,
              blur: 0,
              color: { red: 0, green: 0, blue: 255, alpha: 255 },
            },
            {
              type: 'stroke',
              enabled: true,
              opacity: 1,
              size: 1,
              color: { red: 0, green: 255, blue: 0, alpha: 255 },
            },
            {
              type: 'color-overlay',
              enabled: true,
              opacity: 1,
              blendMode: 'normal',
              color: { red: 255, green: 0, blue: 0, alpha: 255 },
            },
          ],
        }),
      ],
      4,
    );
    expect(output).toEqual(
      new Uint8ClampedArray([0, 255, 0, 255, 255, 0, 0, 255, 0, 255, 0, 255, 0, 0, 0, 0]),
    );
  });

  it('composites nested editable smart-object sources through outer transforms', () => {
    const output = composite(
      [
        createSmartObjectLayer({
          id: 'smart',
          name: 'Smart',
          source: {
            id: 'source',
            name: 'Source',
            canvas: { width: 2, height: 1 },
            layers: [
              createPixelLayer({
                id: 'embedded',
                name: 'Embedded',
                width: 2,
                height: 1,
                pixels: [255, 0, 0, 255, 0, 255, 0, 255],
              }),
            ],
          },
          transform: { ...affine(), e: 1 },
        }),
      ],
      4,
    );
    expect(output).toEqual(
      new Uint8ClampedArray([0, 0, 0, 0, 255, 0, 0, 255, 0, 255, 0, 255, 0, 0, 0, 0]),
    );
  });

  it('applies ordered smart filters without changing embedded source pixels', () => {
    const embedded = createPixelLayer({
      id: 'embedded',
      name: 'Embedded',
      width: 1,
      height: 1,
      pixels: [10, 20, 30, 255],
    });
    const output = composite([
      createSmartObjectLayer({
        id: 'smart',
        name: 'Smart',
        source: {
          id: 'source',
          name: 'Source',
          canvas: { width: 1, height: 1 },
          layers: [embedded],
        },
        smartFilters: [
          { id: 'invert', type: 'invert', enabled: true, opacity: 1 },
          { id: 'gray', type: 'grayscale', enabled: true, opacity: 1 },
        ],
      }),
    ]);
    expect(output).toEqual(new Uint8ClampedArray([236, 236, 236, 255]));
    expect(embedded.pixelStore.toContiguous()).toEqual(new Uint8ClampedArray([10, 20, 30, 255]));
  });

  it('rasterizes editable vector paths with even-odd fill and stroke', () => {
    const output = compositeDocumentPixels(
      createDocument({
        id: 'path-document',
        name: 'Path document',
        width: 10,
        height: 10,
        layers: [
          createShapeLayer({
            id: 'path',
            name: 'Triangle',
            geometry: {
              type: 'path',
              width: 10,
              height: 10,
              cornerRadius: 0,
              commands: [
                { type: 'move', x: 1, y: 1 },
                { type: 'line', x: 9, y: 1 },
                { type: 'line', x: 1, y: 9 },
                { type: 'close' },
              ],
            },
            style: {
              fill: { red: 255, green: 0, blue: 0, alpha: 255 },
              stroke: { red: 0, green: 0, blue: 255, alpha: 255 },
              strokeWidth: 1,
            },
          }),
        ],
      }),
      { rasterize: rasterizeLayerNearest },
    );
    const pixel = (x: number, y: number) => [
      ...output.slice((y * 10 + x) * 4, (y * 10 + x) * 4 + 4),
    ];
    expect(pixel(1, 1)).toEqual([0, 0, 255, 255]);
    expect(pixel(3, 3)).toEqual([255, 0, 0, 255]);
    expect(pixel(8, 8)).toEqual([0, 0, 0, 0]);
  });

  it('renders masked adjustment layers without mutating source pixels', () => {
    const source = createPixelLayer({
      id: 'pixels',
      name: 'Pixels',
      width: 2,
      height: 1,
      pixels: [10, 20, 30, 255, 100, 110, 120, 255],
    });
    const output = composite(
      [
        source,
        createAdjustmentLayer({
          id: 'invert',
          name: 'Invert',
          adjustment: { type: 'invert' },
          rasterMask: createRasterMask(2, 1, [255, 0]),
        }),
      ],
      2,
    );
    expect(output).toEqual(new Uint8ClampedArray([245, 235, 225, 255, 100, 110, 120, 255]));
    expect(source.pixelStore.toContiguous()).toEqual(
      new Uint8ClampedArray([10, 20, 30, 255, 100, 110, 120, 255]),
    );
  });

  it('isolates group children before applying the group blend mode', () => {
    const output = composite([
      createPixelLayer({
        id: 'backdrop',
        name: 'Backdrop',
        width: 1,
        height: 1,
        pixels: [100, 150, 200, 255],
      }),
      createGroupLayer({
        id: 'group',
        name: 'Group',
        compositing: 'isolated',
        blendMode: 'screen',
        children: [
          createPixelLayer({
            id: 'child',
            name: 'Child',
            width: 1,
            height: 1,
            pixels: [50, 100, 150, 255],
          }),
        ],
      }),
    ]);
    expect(output).toEqual(new Uint8ClampedArray([130, 191, 232, 255]));
  });

  it('composites nonseparable color modes through the document renderer', () => {
    const output = composite([
      createPixelLayer({
        id: 'backdrop',
        name: 'Backdrop',
        width: 1,
        height: 1,
        pixels: [40, 80, 120, 255],
      }),
      createPixelLayer({
        id: 'source',
        name: 'Source',
        width: 1,
        height: 1,
        pixels: [200, 100, 50, 255],
        blendMode: 'hue',
      }),
    ]);
    expect(output).toEqual(new Uint8ClampedArray([113, 59, 33, 255]));
  });
});

describe('selection outlines', () => {
  it('traces arbitrary 50-percent coverage boundaries without filling interiors', () => {
    const pixels = selectionOutlinePixels(
      createSelectionMask({
        width: 3,
        height: 3,
        coverage: new Uint8ClampedArray([0, 128, 0, 255, 255, 255, 0, 255, 0]),
      }),
    );
    const alpha = Array.from({ length: 9 }, (_, index) => pixels[index * 4 + 3]);
    expect(alpha).toEqual([0, 255, 0, 255, 0, 255, 0, 255, 0]);
    expect(pixels[4]).toBe(255);
    expect(
      selectionOutlinePixels(
        createSelectionMask({ width: 1, height: 1, coverage: new Uint8ClampedArray([127]) }),
      ),
    ).toEqual(new Uint8ClampedArray(4));
  });

  it('animates the dash phase and validates coverage geometry', () => {
    const selection = createSelectionMask({
      width: 1,
      height: 1,
      coverage: new Uint8ClampedArray([255]),
    });
    expect(selectionOutlinePixels(selection, 0)[0]).toBe(255);
    expect(selectionOutlinePixels(selection, 4)[0]).toBe(16);
    expect(() => selectionOutlinePixels({ ...selection, width: 2 })).toThrow(
      'Expected 2 channel coverage values',
    );
    expect(() => selectionOutlinePixels(selection, 0.5)).toThrow(
      'Selection outline phase must be an integer',
    );
  });

  it('extracts pixel-edge segments for arbitrary shapes', () => {
    const segments = Array.from(
      selectionOutlineSegments(
        createSelectionMask({ width: 2, height: 1, coverage: new Uint8ClampedArray([255, 255]) }),
      ),
    );
    expect(segments).toHaveLength(6);
    expect(segments).toContainEqual({ x1: 0, y1: 0, x2: 1, y2: 0 });
    expect(segments).toContainEqual({ x1: 2, y1: 0, x2: 2, y2: 1 });
    expect(segments).not.toContainEqual({ x1: 1, y1: 0, x2: 1, y2: 1 });
  });

  it('traces sparse tile boundaries without contiguous materialization', () => {
    const store = TiledCoverageStore.fromContiguous(
      4,
      2,
      new Uint8ClampedArray([0, 255, 255, 0, 0, 0, 255, 0]),
      2,
    );
    const segments = Array.from(
      selectionOutlineSegments(createSelectionMask({ width: 4, height: 2, coverageStore: store })),
    );
    expect(segments).toHaveLength(8);
    expect(segments).not.toContainEqual({ x1: 2, y1: 0, x2: 2, y2: 1 });
    expect(segments).not.toContainEqual({ x1: 3, y1: 1, x2: 2, y2: 1 });
  });

  it('traces a 4096px sparse complement through bounded deviation neighborhoods', () => {
    const size = 4096;
    const store = TiledCoverageStore.uniform(size, size, 255, 256).write({
      x: 1024,
      y: 2048,
      width: 1,
      height: 1,
      coverage: new Uint8ClampedArray([0]),
    }).store;
    const read = vi.spyOn(store, 'read');
    const materialize = vi.spyOn(store, 'toContiguous');
    const segments = Array.from(
      selectionOutlineSegments(
        createSelectionMask({ width: size, height: size, coverageStore: store }),
      ),
    );
    expect(materialize).not.toHaveBeenCalled();
    expect(segments).toHaveLength(size * 4 + 4);
    expect(segments).toContainEqual({ x1: 1025, y1: 2048, x2: 1024, y2: 2048 });
    expect(segments).toContainEqual({ x1: size, y1: size - 1, x2: size, y2: size });
    expect(
      Math.max(...read.mock.calls.map(([region]) => region.width * region.height)),
    ).toBeLessThanOrEqual(258 * 258);
  });
});

describe('drawGuidesAndGrid', () => {
  it('strokes vertical and horizontal guides and grid lines in document space', () => {
    const moveTo = vi.fn();
    const lineTo = vi.fn();
    const stroke = vi.fn();
    const beginPath = vi.fn();
    const context = {
      save: vi.fn(),
      restore: vi.fn(),
      beginPath,
      moveTo,
      lineTo,
      stroke,
      getTransform: () => ({ a: 2, b: 0, c: 0, d: 2, e: 0, f: 0 }),
      strokeStyle: '',
      lineWidth: 1,
      globalAlpha: 1,
      globalCompositeOperation: 'source-over',
    } as unknown as CanvasRenderingContext2D;
    const document = createDocument({
      id: 'g',
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
        { id: 'v', direction: 'vertical', position: 10 },
        { id: 'h', direction: 'horizontal', position: 20 },
      ],
      grid: { horizontal: 50, vertical: 40 },
    });
    drawGuidesAndGrid(context, document, {});
    expect(beginPath).toHaveBeenCalled();
    expect(stroke).toHaveBeenCalled();
    // Vertical guide at x=10
    expect(moveTo).toHaveBeenCalledWith(10, 0);
    expect(lineTo).toHaveBeenCalledWith(10, 80);
    // Horizontal guide at y=20
    expect(moveTo).toHaveBeenCalledWith(0, 20);
    expect(lineTo).toHaveBeenCalledWith(100, 20);
    // Grid lines
    expect(moveTo).toHaveBeenCalledWith(50, 0);
    expect(lineTo).toHaveBeenCalledWith(50, 80);
    expect(moveTo).toHaveBeenCalledWith(0, 40);
    expect(lineTo).toHaveBeenCalledWith(100, 40);
  });

  it('skips drawing when guidesOverlay is false', () => {
    const stroke = vi.fn();
    const context = {
      save: vi.fn(),
      restore: vi.fn(),
      beginPath: vi.fn(),
      moveTo: vi.fn(),
      lineTo: vi.fn(),
      stroke,
    } as unknown as CanvasRenderingContext2D;
    const document = createDocument({
      id: 'g',
      name: 'Guides',
      width: 10,
      height: 10,
      layers: [
        createPixelLayer({
          id: 'p',
          name: 'P',
          width: 1,
          height: 1,
          pixels: new Uint8ClampedArray([1, 2, 3, 255]),
        }),
      ],
      guides: [{ id: 'v', direction: 'vertical', position: 5 }],
      grid: { horizontal: 5, vertical: 5 },
    });
    drawGuidesAndGrid(context, document, false);
    expect(stroke).not.toHaveBeenCalled();
  });

  it('strokes temporary smart guide overlay lines', () => {
    const moveTo = vi.fn();
    const lineTo = vi.fn();
    const stroke = vi.fn();
    const setLineDash = vi.fn();
    const context = {
      save: vi.fn(),
      restore: vi.fn(),
      beginPath: vi.fn(),
      moveTo,
      lineTo,
      stroke,
      setLineDash,
      getTransform: () => ({ a: 1, b: 0, c: 0, d: 1, e: 0, f: 0 }),
      strokeStyle: '',
      lineWidth: 1,
      globalAlpha: 1,
      globalCompositeOperation: 'source-over',
    } as unknown as CanvasRenderingContext2D;
    const document = createDocument({
      id: 'smart',
      name: 'Smart',
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
    });
    drawGuidesAndGrid(context, document, {
      smartGuideLines: [
        { axis: 'x', position: 30 },
        { axis: 'y', position: 20 },
      ],
    });
    expect(stroke).toHaveBeenCalled();
    expect(setLineDash).toHaveBeenCalled();
    expect(moveTo).toHaveBeenCalledWith(30, 0);
    expect(lineTo).toHaveBeenCalledWith(30, 80);
    expect(moveTo).toHaveBeenCalledWith(0, 20);
    expect(lineTo).toHaveBeenCalledWith(100, 20);
  });
});

describe('renderDocument', () => {
  it('draws sparse pixel-source chunks without materializing transparent tiles', () => {
    const context = {
      canvas: { width: 100, height: 80 },
      clearRect: vi.fn(),
      save: vi.fn(),
      restore: vi.fn(),
      setTransform: vi.fn(),
      drawImage: vi.fn(),
      globalAlpha: 1,
      globalCompositeOperation: 'source-over',
    } as unknown as CanvasRenderingContext2D;
    const layer = createPixelLayer({ id: 'pixel', name: 'Pixel', width: 8, height: 8 });
    const store = TiledPixelStore.empty(8, 8, 4).write({
      x: 5,
      y: 5,
      width: 1,
      height: 1,
      pixels: new Uint8ClampedArray([10, 20, 30, 255]),
    }).store;
    const putPixels = vi.fn();
    const source = {} as CanvasImageSource;
    const document = createDocument({
      id: 'tiled',
      name: 'Tiled',
      width: 8,
      height: 8,
      layers: [layer],
    });

    renderDocument(context, document, {
      checkerboard: false,
      createSurface: () => ({ source, putPixels }),
      pixelSource: (candidate) => (candidate.id === layer.id ? store : undefined),
    });

    expect(putPixels).toHaveBeenCalledOnce();
    expect(putPixels).toHaveBeenCalledWith(store.getTile(1, 1)?.pixels, 4, 4);
    expect(context.drawImage).toHaveBeenCalledWith(source, 4, 4);

    const composite = compositeDocumentPixels(document, {
      rasterize: rasterizeLayerNearest,
      pixelSource: () => store,
    });
    expect([...composite.slice((5 * 8 + 5) * 4, (5 * 8 + 5) * 4 + 4)]).toEqual([10, 20, 30, 255]);
  });

  it('applies sparse raster masks to source chunks without full-surface materialization', () => {
    const width = 8192;
    const height = 8192;
    const pixelStore = TiledPixelStore.empty(width, height).write({
      x: 4097,
      y: 2049,
      width: 2,
      height: 1,
      pixels: new Uint8ClampedArray([10, 20, 30, 255, 40, 50, 60, 255]),
    }).store;
    const coverageStore = TiledCoverageStore.empty(width, height).write({
      x: 4097,
      y: 2049,
      width: 2,
      height: 1,
      coverage: new Uint8ClampedArray([128, 255]),
    }).store;
    const layer = createPixelLayer({
      id: 'masked-sparse',
      name: 'Masked sparse',
      width,
      height,
      rasterMask: createRasterMaskFromCoverageStore(width, height, coverageStore, { density: 0.5 }),
    });
    const rasterMask = layer.rasterMask;
    if (!rasterMask) throw new Error('Expected sparse raster mask fixture.');
    const pixelMaterialize = vi.spyOn(pixelStore, 'toContiguous');
    const maskMaterialize = vi.spyOn(rasterMask.coverageStore, 'toContiguous');
    const context = {
      canvas: { width: 32, height: 32 },
      clearRect: vi.fn(),
      save: vi.fn(),
      restore: vi.fn(),
      setTransform: vi.fn(),
      drawImage: vi.fn(),
      globalAlpha: 1,
      globalCompositeOperation: 'source-over',
    } as unknown as CanvasRenderingContext2D;
    const putPixels = vi.fn();
    const surface = {} as CanvasImageSource;

    renderDocument(
      context,
      createDocument({ id: 'masked-large', name: 'Masked large', width, height, layers: [layer] }),
      {
        checkerboard: false,
        pixelSource: () => pixelStore,
        createSurface: (surfaceWidth, surfaceHeight) => {
          expect([surfaceWidth, surfaceHeight]).toEqual([256, 256]);
          return { source: surface, putPixels };
        },
      },
    );

    expect(pixelMaterialize).not.toHaveBeenCalled();
    expect(maskMaterialize).not.toHaveBeenCalled();
    expect(pixelStore.allocatedByteLength + rasterMask.coverageStore.allocatedByteLength).toBe(
      256 * 256 * 5,
    );
    expect(putPixels).toHaveBeenCalledOnce();
    const masked = putPixels.mock.calls[0]?.[0] as Uint8ClampedArray;
    const paintedOffset = (1 * 256 + 1) * 4;
    expect(Array.from(masked.slice(paintedOffset, paintedOffset + 8))).toEqual([
      10, 20, 30, 192, 40, 50, 60, 255,
    ]);
    expect(context.drawImage).toHaveBeenCalledWith(surface, 4096, 2048);
  });

  it('uses composite tile cache for deterministic advanced path with warm re-renders', () => {
    const putPixels = vi.fn();
    const context = {
      canvas: { width: 64, height: 64 },
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
    const document = createDocument({
      id: 'cached-render',
      name: 'Cached render',
      width: 32,
      height: 32,
      layers: [
        createPixelLayer({
          id: 'a',
          name: 'A',
          width: 32,
          height: 32,
          pixels: new Uint8ClampedArray(32 * 32 * 4).fill(180),
          blendMode: 'multiply',
          opacity: 0.9,
        }),
        createPixelLayer({
          id: 'b',
          name: 'B',
          width: 32,
          height: 32,
          pixels: new Uint8ClampedArray(32 * 32 * 4).fill(90),
          blendMode: 'screen',
          opacity: 0.8,
        }),
      ],
    });
    const cache = new CompositeTileCache({ tileSize: 16 });
    const surface = { source: {} as CanvasImageSource, putPixels };
    renderDocument(context, document, {
      checkerboard: false,
      rasterize: rasterizeLayerNearest,
      createSurface: () => surface,
      compositeTileCache: cache,
      viewport: { zoom: 1, panX: 0, panY: 0 },
    });
    expect(putPixels).toHaveBeenCalled();
    const first = putPixels.mock.calls.at(-1)?.[0] as Uint8ClampedArray;
    expect(first).toEqual(compositeDocumentPixels(document, { rasterize: rasterizeLayerNearest }));
    const missesAfterCold = cache.stats.misses;
    putPixels.mockClear();
    renderDocument(context, document, {
      checkerboard: false,
      rasterize: rasterizeLayerNearest,
      createSurface: () => surface,
      compositeTileCache: cache,
      viewport: { zoom: 1, panX: 0, panY: 0 },
    });
    const second = putPixels.mock.calls.at(-1)?.[0] as Uint8ClampedArray;
    expect(second).toEqual(first);
    expect(cache.stats.lastComposeFullyWarm).toBe(true);
    expect(cache.stats.misses).toBe(missesAfterCold);
  });

  it('reuses warm advanced composite tiles across exact RGB channel previews', () => {
    const putPixels = vi.fn();
    const context = {
      canvas: { width: 8, height: 8 },
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
    const pixels = Uint8ClampedArray.from({ length: 8 * 8 * 4 }, (_, index) =>
      index % 4 === 3 ? 255 : (index * 17) % 256,
    );
    const document = createDocument({
      id: 'channel-cache',
      name: 'Channel cache',
      width: 8,
      height: 8,
      layers: [
        createPixelLayer({
          id: 'advanced',
          name: 'Advanced',
          width: 8,
          height: 8,
          pixels,
          blendMode: 'multiply',
        }),
      ],
    });
    const cache = new CompositeTileCache({ tileSize: 4 });
    const render = (mode: 'composite' | 'red' | 'green') => {
      renderDocument(context, document, {
        checkerboard: false,
        rasterize: rasterizeLayerNearest,
        createSurface: () => ({ source: {} as CanvasImageSource, putPixels }),
        compositeTileCache: cache,
        preview: { mode },
      });
      return putPixels.mock.calls.at(-1)?.[0] as Uint8ClampedArray;
    };

    expect(render('composite')).toEqual(
      compositeDocumentPixels(document, { rasterize: rasterizeLayerNearest }),
    );
    const missesAfterCold = cache.stats.misses;
    expect(render('red')).toEqual(
      compositeDocumentPixels(document, {
        preview: { mode: 'red' },
        rasterize: rasterizeLayerNearest,
      }),
    );
    expect(render('green')).toEqual(
      compositeDocumentPixels(document, {
        preview: { mode: 'green' },
        rasterize: rasterizeLayerNearest,
      }),
    );
    expect(cache.stats.misses).toBe(missesAfterCold);
    expect(cache.stats.lastComposeFullyWarm).toBe(true);
  });

  it('uses precomposedPixels and skips composite tile cache recomposition', () => {
    const putPixels = vi.fn();
    const context = {
      canvas: { width: 16, height: 16 },
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
    const document = createDocument({
      id: 'precomposed-render',
      name: 'Precomposed',
      width: 8,
      height: 8,
      layers: [
        createPixelLayer({
          id: 'a',
          name: 'A',
          width: 8,
          height: 8,
          pixels: new Uint8ClampedArray(8 * 8 * 4).fill(40),
          blendMode: 'multiply',
        }),
        createPixelLayer({
          id: 'b',
          name: 'B',
          width: 8,
          height: 8,
          pixels: new Uint8ClampedArray(8 * 8 * 4).fill(200),
          blendMode: 'screen',
        }),
      ],
    });
    const cache = new CompositeTileCache({ tileSize: 8 });
    const precomposed = new Uint8ClampedArray(8 * 8 * 4).fill(77);
    const surface = { source: {} as CanvasImageSource, putPixels };
    let referenceCalls = 0;
    const originalComposite = cache.composite.bind(cache);
    vi.spyOn(cache, 'composite').mockImplementation((doc, options) => {
      referenceCalls += 1;
      return originalComposite(doc, {
        ...options,
        referenceComposite: (inner) => {
          referenceCalls += 10;
          return options.referenceComposite(inner);
        },
      });
    });
    renderDocument(context, document, {
      checkerboard: false,
      rasterize: rasterizeLayerNearest,
      createSurface: () => surface,
      compositeTileCache: cache,
      precomposedPixels: precomposed,
      viewport: { zoom: 1, panX: 0, panY: 0 },
    });
    expect(putPixels).toHaveBeenCalledWith(precomposed, 8, 8);
    expect(referenceCalls).toBe(0);
    expect(cache.composite).not.toHaveBeenCalled();
  });

  it('draws mip precomposed pixels at document dimensions', () => {
    const putPixels = vi.fn();
    const source = {} as CanvasImageSource;
    const context = {
      canvas: { width: 16, height: 16 },
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
    const document = createDocument({
      id: 'mip-precomposed',
      name: 'Mip precomposed',
      width: 8,
      height: 8,
      layers: [
        createPixelLayer({
          id: 'a',
          name: 'A',
          width: 8,
          height: 8,
          pixels: new Uint8ClampedArray(8 * 8 * 4).fill(80),
          blendMode: 'multiply',
        }),
      ],
    });
    const mip = new Uint8ClampedArray(4 * 4 * 4).fill(77);
    renderDocument(context, document, {
      checkerboard: false,
      createSurface: () => ({ source, putPixels }),
      precomposedPixels: mip,
      compositeMipLevel: 1,
      viewport: { zoom: 0.5, panX: 0, panY: 0 },
    });
    expect(putPixels).toHaveBeenCalledWith(mip, 4, 4);
    expect(context.drawImage).toHaveBeenCalledWith(source, 0, 0, 8, 8);
  });

  it('ignores wrong-length precomposedPixels and falls back to the cache path', () => {
    const putPixels = vi.fn();
    const context = {
      canvas: { width: 16, height: 16 },
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
    const document = createDocument({
      id: 'bad-precomposed',
      name: 'Bad precomposed',
      width: 4,
      height: 4,
      layers: [
        createPixelLayer({
          id: 'a',
          name: 'A',
          width: 4,
          height: 4,
          pixels: new Uint8ClampedArray(4 * 4 * 4).fill(10),
          blendMode: 'multiply',
        }),
      ],
    });
    const cache = new CompositeTileCache({ tileSize: 4 });
    const surface = { source: {} as CanvasImageSource, putPixels };
    const expected = compositeDocumentPixels(document, { rasterize: rasterizeLayerNearest });
    renderDocument(context, document, {
      checkerboard: false,
      rasterize: rasterizeLayerNearest,
      createSurface: () => surface,
      compositeTileCache: cache,
      precomposedPixels: new Uint8ClampedArray(4),
      viewport: { zoom: 1, panX: 0, panY: 0 },
    });
    const drawn = putPixels.mock.calls.at(-1)?.[0] as Uint8ClampedArray;
    expect(drawn).toEqual(expected);
    expect(cache.stats.misses).toBeGreaterThan(0);
  });

  it('requests only source chunks intersecting transformed document dirt', () => {
    const context = {
      canvas: { width: 200, height: 160 },
      clearRect: vi.fn(),
      save: vi.fn(),
      restore: vi.fn(),
      setTransform: vi.fn(),
      beginPath: vi.fn(),
      rect: vi.fn(),
      clip: vi.fn(),
      drawImage: vi.fn(),
      globalAlpha: 1,
      globalCompositeOperation: 'source-over',
    } as unknown as CanvasRenderingContext2D;
    const layer = createPixelLayer({
      id: 'translated',
      name: 'Translated',
      width: 16,
      height: 16,
      transform: { a: 1, b: 0, c: 0, d: 1, e: 100, f: 50 },
    });
    const store = TiledPixelStore.empty(16, 16, 4).write({
      x: 4,
      y: 8,
      width: 1,
      height: 1,
      pixels: new Uint8ClampedArray([1, 2, 3, 255]),
    }).store;
    const chunks = vi.spyOn(store, 'pixelChunks');

    renderDocument(
      context,
      createDocument({
        id: 'dirty-source',
        name: 'Dirty source',
        width: 140,
        height: 100,
        layers: [layer],
      }),
      {
        checkerboard: false,
        dirtyRegions: [{ x: 104, y: 58, width: 2, height: 3 }],
        createSurface: () => ({ source: {} as CanvasImageSource, putPixels: vi.fn() }),
        pixelSource: () => store,
      },
    );

    expect(chunks).toHaveBeenCalledOnce();
    expect(chunks).toHaveBeenCalledWith({ x: 4, y: 8, width: 2, height: 3 });
    expect(context.drawImage).toHaveBeenCalledOnce();
  });

  it('clips clearing and drawing to document-space dirty regions', () => {
    const context = {
      canvas: { width: 100, height: 80 },
      clearRect: vi.fn(),
      save: vi.fn(),
      restore: vi.fn(),
      setTransform: vi.fn(),
      beginPath: vi.fn(),
      rect: vi.fn(),
      clip: vi.fn(),
      drawImage: vi.fn(),
      globalAlpha: 1,
      globalCompositeOperation: 'source-over',
    } as unknown as CanvasRenderingContext2D;
    const document = createDocument({
      id: 'dirty',
      name: 'Dirty',
      width: 20,
      height: 20,
      layers: [createPixelLayer({ id: 'pixel', name: 'Pixel', width: 20, height: 20 })],
    });

    renderDocument(context, document, {
      checkerboard: false,
      viewport: { zoom: 2, panX: 5, panY: 7 },
      dirtyRegions: [
        { x: 2, y: 3, width: 4, height: 5 },
        { x: 30, y: 30, width: 2, height: 2 },
      ],
      createSurface: () => ({ source: {} as CanvasImageSource, putPixels: vi.fn() }),
    });

    expect(context.clearRect).toHaveBeenCalledOnce();
    expect(context.clearRect).toHaveBeenCalledWith(9, 13, 8, 10);
    expect(context.beginPath).toHaveBeenCalledOnce();
    expect(context.rect).toHaveBeenCalledWith(9, 13, 8, 10);
    expect(context.clip).toHaveBeenCalledOnce();
    expect(context.setTransform).toHaveBeenNthCalledWith(1, 1, 0, 0, 1, 0, 0);
  });

  it('draws the transparency grid inside the transformed document bounds', () => {
    const context = {
      canvas: { width: 320, height: 200 },
      clearRect: vi.fn(),
      fillRect: vi.fn(),
      save: vi.fn(),
      restore: vi.fn(),
      setTransform: vi.fn(),
      drawImage: vi.fn(),
      fillStyle: '',
      globalAlpha: 1,
      globalCompositeOperation: 'source-over',
    } as unknown as CanvasRenderingContext2D;

    renderDocument(
      context,
      {
        id: 'doc',
        name: 'Test',
        canvas: { width: 4, height: 3 },
        layers: [],
        selection: null,
        channels: [],
        guides: [],
        grid: null,
        paths: [],
      },
      { viewport: { zoom: 2, panX: 3, panY: 4 }, createSurface: vi.fn() },
    );

    expect(context.setTransform).toHaveBeenCalledWith(2, 0, 0, 2, 3, 4);
    expect(context.fillRect).toHaveBeenNthCalledWith(1, 0, 0, 4, 3);
    expect(context.fillRect).not.toHaveBeenCalledWith(0, 0, 320, 200);
  });

  it('composites only visible layers with the viewport transform', () => {
    const context = {
      canvas: { width: 320, height: 200 },
      clearRect: vi.fn(),
      fillRect: vi.fn(),
      save: vi.fn(),
      restore: vi.fn(),
      setTransform: vi.fn(),
      drawImage: vi.fn(),
      fillStyle: '',
      globalAlpha: 1,
      globalCompositeOperation: 'source-over',
    } as unknown as CanvasRenderingContext2D;
    const putPixels = vi.fn();
    const source = {} as CanvasImageSource;
    renderDocument(
      context,
      {
        id: 'doc',
        name: 'Test',
        canvas: { width: 1, height: 1 },
        selection: null,
        channels: [],
        guides: [],
        grid: null,
        paths: [],
        layers: [
          {
            id: 'hidden',
            name: 'Hidden',
            kind: 'pixel',
            ...layerDefaults,
            width: 1,
            height: 1,
            pixelStore: TiledPixelStore.empty(1, 1),
            visible: false,
            opacity: 1,
            transform: affine(),
            rasterMask: null,
          },
          {
            id: 'shown',
            name: 'Shown',
            kind: 'pixel',
            ...layerDefaults,
            width: 1,
            height: 1,
            pixelStore: TiledPixelStore.fromContiguous(1, 1, new Uint8ClampedArray([1, 2, 3, 255])),
            visible: true,
            opacity: 0.5,
            transform: affine(1, 0, 0, 1, 5, 6),
            rasterMask: null,
          },
        ],
      },
      {
        checkerboard: false,
        viewport: { zoom: 2, panX: 3, panY: 4 },
        createSurface: () => ({ source, putPixels }),
      },
    );
    expect(context.setTransform).toHaveBeenLastCalledWith(2, 0, 0, 2, 13, 16);
    expect(putPixels).toHaveBeenCalledOnce();
    expect(context.drawImage).toHaveBeenCalledWith(source, 0, 0);
  });

  it('composes layer scale and translation after the viewport transform', () => {
    const context = {
      canvas: { width: 320, height: 200 },
      clearRect: vi.fn(),
      save: vi.fn(),
      restore: vi.fn(),
      setTransform: vi.fn(),
      drawImage: vi.fn(),
      globalAlpha: 1,
      globalCompositeOperation: 'source-over',
    } as unknown as CanvasRenderingContext2D;
    const source = {} as CanvasImageSource;

    renderDocument(
      context,
      {
        id: 'doc',
        name: 'Scaled',
        canvas: { width: 10, height: 10 },
        selection: null,
        channels: [],
        guides: [],
        grid: null,
        paths: [],
        layers: [
          {
            id: 'pixel',
            name: 'Pixel',
            kind: 'pixel',
            ...layerDefaults,
            width: 1,
            height: 1,
            pixelStore: TiledPixelStore.empty(1, 1),
            visible: true,
            opacity: 1,
            transform: affine(2, 0, 0, 3, 4, 5),
            rasterMask: null,
          },
        ],
      },
      {
        checkerboard: false,
        viewport: { zoom: 2, panX: 3, panY: 7 },
        createSurface: () => ({ source, putPixels: vi.fn() }),
      },
    );

    expect(context.setTransform).toHaveBeenLastCalledWith(4, 0, 0, 6, 11, 17);
    expect(context.drawImage).toHaveBeenCalledWith(source, 0, 0);
  });

  it('applies rotation without reducing it to an axis-aligned translation', () => {
    const context = {
      canvas: { width: 20, height: 20 },
      clearRect: vi.fn(),
      save: vi.fn(),
      restore: vi.fn(),
      setTransform: vi.fn(),
      drawImage: vi.fn(),
      globalAlpha: 1,
      globalCompositeOperation: 'source-over',
    } as unknown as CanvasRenderingContext2D;

    renderDocument(
      context,
      {
        id: 'doc',
        name: 'Rotated',
        canvas: { width: 10, height: 10 },
        selection: null,
        channels: [],
        guides: [],
        grid: null,
        paths: [],
        layers: [
          {
            id: 'pixel',
            name: 'Pixel',
            kind: 'pixel',
            ...layerDefaults,
            width: 1,
            height: 1,
            pixelStore: TiledPixelStore.empty(1, 1),
            visible: true,
            opacity: 1,
            transform: affine(0, 1, -1, 0, 8, 9),
            rasterMask: null,
          },
        ],
      },
      {
        checkerboard: false,
        createSurface: () => ({ source: {} as CanvasImageSource, putPixels: vi.fn() }),
      },
    );

    expect(context.setTransform).toHaveBeenLastCalledWith(0, 1, -1, 0, 8, 9);
  });

  it('multiplies nested group transforms in parent-to-child order', () => {
    const context = {
      canvas: { width: 100, height: 100 },
      clearRect: vi.fn(),
      save: vi.fn(),
      restore: vi.fn(),
      setTransform: vi.fn(),
      drawImage: vi.fn(),
      globalAlpha: 1,
      globalCompositeOperation: 'source-over',
    } as unknown as CanvasRenderingContext2D;

    renderDocument(
      context,
      {
        id: 'doc',
        name: 'Nested',
        canvas: { width: 10, height: 10 },
        selection: null,
        channels: [],
        guides: [],
        grid: null,
        paths: [],
        layers: [
          {
            id: 'group',
            name: 'Group',
            kind: 'group',
            ...layerDefaults,
            compositing: 'pass-through',
            visible: true,
            opacity: 0.5,
            transform: affine(0, 1, -1, 0, 10, 20),
            children: [
              {
                id: 'pixel',
                name: 'Pixel',
                kind: 'pixel',
                ...layerDefaults,
                width: 1,
                height: 1,
                pixelStore: TiledPixelStore.empty(1, 1),
                visible: true,
                opacity: 0.4,
                transform: affine(2, 0, 0, 3, 4, 5),
                rasterMask: null,
              },
            ],
          },
        ],
      },
      {
        checkerboard: false,
        viewport: { zoom: 2, panX: 3, panY: 4 },
        createSurface: () => ({ source: {} as CanvasImageSource, putPixels: vi.fn() }),
      },
    );

    expect(context.setTransform).toHaveBeenLastCalledWith(0, 4, -6, 0, 13, 52);
    expect(context.globalAlpha).toBeCloseTo(0.2);
  });

  it('applies enabled mask density to alpha without mutating source pixels', () => {
    const context = {
      canvas: { width: 2, height: 1 },
      clearRect: vi.fn(),
      save: vi.fn(),
      restore: vi.fn(),
      setTransform: vi.fn(),
      drawImage: vi.fn(),
      globalAlpha: 1,
      globalCompositeOperation: 'source-over',
    } as unknown as CanvasRenderingContext2D;
    const sourcePixels = new Uint8ClampedArray([255, 0, 0, 255, 0, 0, 255, 255]);
    const putPixels = vi.fn();
    renderDocument(
      context,
      {
        id: 'doc',
        name: 'Masked',
        canvas: { width: 2, height: 1 },
        selection: null,
        channels: [],
        guides: [],
        grid: null,
        paths: [],
        layers: [
          {
            id: 'pixel',
            name: 'Pixel',
            kind: 'pixel',
            ...layerDefaults,
            width: 2,
            height: 1,
            pixelStore: TiledPixelStore.fromContiguous(2, 1, sourcePixels),
            visible: true,
            opacity: 1,
            transform: affine(),
            rasterMask: createRasterMask(2, 1, [0, 255], { density: 0.5 }),
          },
        ],
      },
      {
        checkerboard: false,
        createSurface: () => ({ source: {} as CanvasImageSource, putPixels }),
      },
    );
    expect(putPixels).toHaveBeenCalledWith(
      new Uint8ClampedArray([255, 0, 0, 128, 0, 0, 255, 255]),
      2,
      1,
    );
    expect(sourcePixels).toEqual(new Uint8ClampedArray([255, 0, 0, 255, 0, 0, 255, 255]));
  });

  it.each([
    ['red', 11],
    ['green', 22],
    ['blue', 33],
  ] as const)(
    'renders the %s channel as grayscale without mutating source pixels',
    (mode, value) => {
      const context = {
        canvas: { width: 4, height: 3 },
        clearRect: vi.fn(),
        save: vi.fn(),
        restore: vi.fn(),
        setTransform: vi.fn(),
        drawImage: vi.fn(),
        globalAlpha: 1,
        globalCompositeOperation: 'source-over',
      } as unknown as CanvasRenderingContext2D;
      const sourcePixels = new Uint8ClampedArray([11, 22, 33, 200]);
      const originalPixels = new Uint8ClampedArray(sourcePixels);
      const putPixels = vi.fn();
      const source = {} as CanvasImageSource;

      renderDocument(
        context,
        {
          id: 'doc',
          name: 'Channel preview',
          canvas: { width: 4, height: 3 },
          selection: null,
          channels: [],
          guides: [],
          grid: null,
          paths: [],
          layers: [
            {
              id: 'group',
              name: 'Group',
              kind: 'group',
              ...layerDefaults,
              compositing: 'pass-through',
              children: [
                {
                  id: 'pixel',
                  name: 'Pixel',
                  kind: 'pixel',
                  ...layerDefaults,
                  width: 1,
                  height: 1,
                  pixelStore: TiledPixelStore.fromContiguous(1, 1, sourcePixels),
                  visible: true,
                  opacity: 0.5,
                  transform: affine(1, 0, 0, 1, 2, 3),
                  rasterMask: createRasterMask(1, 1, [0], { density: 0.5 }),
                },
              ],
              visible: true,
              opacity: 0.5,
              transform: affine(1, 0, 0, 1, 4, 5),
            },
          ],
        },
        {
          checkerboard: false,
          preview: { mode },
          createSurface: () => ({ source, putPixels }),
        },
      );

      expect(putPixels).toHaveBeenCalledWith(
        new Uint8ClampedArray([value, value, value, 100]),
        1,
        1,
      );
      expect(context.setTransform).toHaveBeenLastCalledWith(1, 0, 0, 1, 6, 8);
      expect(context.drawImage).toHaveBeenCalledWith(source, 0, 0);
      expect(context.globalAlpha).toBe(0.25);
      expect(sourcePixels).toEqual(originalPixels);
    },
  );

  it('renders supplied alpha coverage as opaque grayscale and ignores layers', () => {
    const context = {
      canvas: { width: 8, height: 6 },
      clearRect: vi.fn(),
      save: vi.fn(),
      restore: vi.fn(),
      setTransform: vi.fn(),
      drawImage: vi.fn(),
      fillRect: vi.fn(),
      globalAlpha: 0.4,
      globalCompositeOperation: 'multiply',
    } as unknown as CanvasRenderingContext2D;
    const coverage = new Uint8ClampedArray([0, 64, 128, 255]);
    const originalCoverage = new Uint8ClampedArray(coverage);
    const putPixels = vi.fn();
    const source = {} as CanvasImageSource;

    renderDocument(
      context,
      {
        id: 'doc',
        name: 'Alpha preview',
        canvas: { width: 2, height: 2 },
        selection: null,
        channels: [],
        guides: [],
        grid: null,
        paths: [],
        layers: [
          {
            id: 'pixel',
            name: 'Ignored',
            kind: 'pixel',
            ...layerDefaults,
            width: 1,
            height: 1,
            pixelStore: TiledPixelStore.fromContiguous(
              1,
              1,
              new Uint8ClampedArray([255, 0, 0, 255]),
            ),
            visible: true,
            opacity: 1,
            transform: affine(0, 1, -1, 0, 7, 9),
            rasterMask: null,
          },
        ],
      },
      {
        checkerboard: false,
        viewport: { zoom: 2, panX: 3, panY: 4 },
        preview: { mode: 'alpha', coverage },
        createSurface: () => ({ source, putPixels }),
      },
    );

    expect(putPixels).toHaveBeenCalledOnce();
    expect(putPixels).toHaveBeenCalledWith(
      new Uint8ClampedArray([
        0, 0, 0, 255, 64, 64, 64, 255, 128, 128, 128, 255, 255, 255, 255, 255,
      ]),
      2,
      2,
    );
    expect(context.setTransform).toHaveBeenCalledWith(2, 0, 0, 2, 3, 4);
    expect(context.drawImage).toHaveBeenCalledOnce();
    expect(context.drawImage).toHaveBeenCalledWith(source, 0, 0);
    expect(context.fillRect).toHaveBeenCalledWith(0, 0, 2, 2);
    expect(context.fillStyle).toBe('#000');
    expect(context.globalAlpha).toBe(1);
    expect(context.globalCompositeOperation).toBe('source-over');
    expect(coverage).toEqual(originalCoverage);
  });

  it('streams sparse alpha preview chunks without document-sized materialization', () => {
    const width = 8192;
    const height = 8192;
    const coverageStore = TiledCoverageStore.empty(width, height).write({
      x: 4097,
      y: 2049,
      width: 2,
      height: 2,
      coverage: new Uint8ClampedArray([32, 64, 128, 255]),
    }).store;
    const materialize = vi.spyOn(coverageStore, 'toContiguous');
    const context = {
      canvas: { width: 32, height: 32 },
      clearRect: vi.fn(),
      save: vi.fn(),
      restore: vi.fn(),
      setTransform: vi.fn(),
      drawImage: vi.fn(),
      fillRect: vi.fn(),
      globalAlpha: 1,
      globalCompositeOperation: 'source-over',
    } as unknown as CanvasRenderingContext2D;
    const surfaces: { width: number; height: number; putPixels: ReturnType<typeof vi.fn> }[] = [];

    renderDocument(
      context,
      createDocument({ id: 'large-alpha', name: 'Large alpha', width, height, layers: [] }),
      {
        checkerboard: false,
        preview: { mode: 'alpha', coverage: coverageStore },
        createSurface: (surfaceWidth, surfaceHeight) => {
          const putPixels = vi.fn();
          surfaces.push({ width: surfaceWidth, height: surfaceHeight, putPixels });
          return { source: {} as CanvasImageSource, putPixels };
        },
      },
    );

    expect(materialize).not.toHaveBeenCalled();
    expect(coverageStore.allocatedByteLength).toBe(256 * 256);
    expect(surfaces).toHaveLength(1);
    expect(surfaces[0]).toMatchObject({ width: 256, height: 256 });
    expect(surfaces[0]?.putPixels).toHaveBeenCalledWith(expect.any(Uint8ClampedArray), 256, 256);
    const previewPixels = surfaces[0]?.putPixels.mock.calls[0]?.[0] as Uint8ClampedArray;
    const firstPaintedOffset = (1 * 256 + 1) * 4;
    expect(Array.from(previewPixels.slice(firstPaintedOffset, firstPaintedOffset + 8))).toEqual([
      32, 32, 32, 255, 64, 64, 64, 255,
    ]);
    expect(context.fillRect).toHaveBeenCalledWith(0, 0, width, height);
    expect(context.drawImage).toHaveBeenCalledWith(expect.anything(), 4096, 2048);
  });

  it('combines editable vector-mask coverage with raster masks', () => {
    const pixels = new Uint8ClampedArray(4 * 4 * 4);
    for (let offset = 0; offset < pixels.length; offset += 4) {
      pixels.set([200, 100, 50, 255], offset);
    }
    const layer = createPixelLayer({
      id: 'masked',
      name: 'Masked',
      width: 4,
      height: 4,
      pixels,
      rasterMask: createRasterMask(4, 4, new Uint8ClampedArray(16).fill(255), { density: 0.5 }),
      vectorMask: createVectorMask(4, 4, [
        { type: 'move', x: 1, y: 1 },
        { type: 'line', x: 3, y: 1 },
        { type: 'line', x: 3, y: 3 },
        { type: 'line', x: 1, y: 3 },
        { type: 'close' },
      ]),
    });
    const rendered = compositeDocumentPixels(
      createDocument({ id: 'doc', name: 'Vector mask', width: 4, height: 4, layers: [layer] }),
    );
    expect(rendered[(0 * 4 + 0) * 4 + 3]).toBe(0);
    expect(rendered[(1 * 4 + 1) * 4 + 3]).toBe(255);
    expect(rendered[(3 * 4 + 3) * 4 + 3]).toBe(0);

    if (layer.vectorMask === null) throw new Error('Expected vector mask fixture');
    const inverted = createPixelLayer({
      ...layer,
      vectorMask: { ...layer.vectorMask, inverted: true, density: 0.5 },
    });
    const invertedPixels = compositeDocumentPixels(
      createDocument({ id: 'inverse', name: 'Inverse', width: 4, height: 4, layers: [inverted] }),
    );
    expect(invertedPixels[3]).toBe(255);
    expect(invertedPixels[(1 * 4 + 1) * 4 + 3]).toBe(128);
  });

  it('applies vector masks to structured content and document-space adjustments', () => {
    const halfMask = createVectorMask(4, 4, [
      { type: 'move', x: 0, y: 0 },
      { type: 'line', x: 2, y: 0 },
      { type: 'line', x: 2, y: 4 },
      { type: 'line', x: 0, y: 4 },
      { type: 'close' },
    ]);
    const shape = createShapeLayer({
      id: 'shape-mask',
      name: 'Shape mask',
      geometry: { type: 'rectangle', width: 4, height: 4, cornerRadius: 0 },
      style: { fill: { red: 255, green: 0, blue: 0, alpha: 255 } },
      vectorMask: halfMask,
    });
    const shaped = compositeDocumentPixels(
      createDocument({ id: 'shape-doc', name: 'Shape', width: 4, height: 4, layers: [shape] }),
    );
    expect(shaped.slice(0, 4)).toEqual(new Uint8ClampedArray([255, 0, 0, 255]));
    expect(shaped.slice(3 * 4, 4 * 4)).toEqual(new Uint8ClampedArray([0, 0, 0, 0]));

    const base = createPixelLayer({
      id: 'base',
      name: 'Base',
      width: 4,
      height: 4,
      pixels: Uint8ClampedArray.from({ length: 4 * 4 * 4 }, (_, index) =>
        index % 4 === 3 ? 255 : 32,
      ),
    });
    const adjustment = createAdjustmentLayer({
      id: 'masked-adjustment',
      name: 'Masked adjustment',
      adjustment: { type: 'invert' },
      vectorMask: halfMask,
    });
    const adjusted = compositeDocumentPixels(
      createDocument({
        id: 'adjustment-doc',
        name: 'Adjustment',
        width: 4,
        height: 4,
        layers: [base, adjustment],
      }),
    );
    expect([...adjusted.slice(0, 4)]).toEqual([223, 223, 223, 255]);
    expect([...adjusted.slice(3 * 4, 4 * 4)]).toEqual([32, 32, 32, 255]);
  });

  it('combines document-space raster and vector masks on pass-through and isolated groups', () => {
    const halfMask = createVectorMask(4, 2, [
      { type: 'move', x: 0, y: 0 },
      { type: 'line', x: 2, y: 0 },
      { type: 'line', x: 2, y: 2 },
      { type: 'line', x: 0, y: 2 },
      { type: 'close' },
    ]);
    const base = createPixelLayer({
      id: 'base',
      name: 'Base',
      width: 4,
      height: 2,
      pixels: Uint8ClampedArray.from({ length: 4 * 2 * 4 }, (_, index) =>
        index % 4 === 3 ? 255 : index % 4 === 2 ? 255 : 0,
      ),
    });
    const red = createPixelLayer({
      id: 'red',
      name: 'Red',
      width: 4,
      height: 2,
      pixels: Uint8ClampedArray.from({ length: 4 * 2 * 4 }, (_, index) =>
        index % 4 === 0 || index % 4 === 3 ? 255 : 0,
      ),
    });
    for (const compositing of ['pass-through', 'isolated'] as const) {
      const rendered = compositeDocumentPixels(
        createDocument({
          id: compositing,
          name: compositing,
          width: 4,
          height: 2,
          layers: [
            base,
            createGroupLayer({
              id: `group-${compositing}`,
              name: 'Masked group',
              compositing,
              rasterMask: createRasterMask(4, 2, [255, 255, 255, 255, 0, 0, 0, 0]),
              vectorMask: halfMask,
              children: [red],
            }),
          ],
        }),
      );
      expect([...rendered.slice(0, 4)]).toEqual([255, 0, 0, 255]);
      expect([...rendered.slice(3 * 4, 4 * 4)]).toEqual([0, 0, 255, 255]);
      expect([...rendered.slice(4 * 4, 5 * 4)]).toEqual([0, 0, 255, 255]);
    }
  });

  it('rejects alpha coverage that does not match the document canvas', () => {
    const context = {
      canvas: { width: 2, height: 2 },
      clearRect: vi.fn(),
      save: vi.fn(),
      restore: vi.fn(),
      setTransform: vi.fn(),
      drawImage: vi.fn(),
      globalAlpha: 1,
      globalCompositeOperation: 'source-over',
    } as unknown as CanvasRenderingContext2D;

    expect(() =>
      renderDocument(
        context,
        {
          id: 'doc',
          name: 'Alpha',
          canvas: { width: 2, height: 2 },
          selection: null,
          channels: [],
          guides: [],
          grid: null,
          paths: [],
          layers: [],
        },
        {
          checkerboard: false,
          preview: { mode: 'alpha', coverage: new Uint8ClampedArray(3) },
          createSurface: vi.fn(),
        },
      ),
    ).toThrowError('Expected 4 channel coverage values, received 3.');
  });
});
