import { describe, expect, it, vi } from 'vitest';
import { TiledCoverageStore, TiledPixelStore } from '@freetoshop/pixel-storage';
import {
  createSelectionMask,
  DocumentInvariantError,
  addAlphaChannelFromSelection,
  addLayer,
  addLayerRasterMaskFromSelection,
  combineSelection,
  combineRectangularSelection,
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
  duplicateLayer,
  convertLayerToSmartObject,
  createRectangularSelection,
  cropDocument,
  findLayerWorldTransform,
  findLayer,
  findAlphaChannel,
  identityLayerTransform,
  groupLayers,
  invertLayerTransform,
  invertSelection,
  invertLayerMask,
  invertLayerVectorMask,
  multiplyLayerTransforms,
  patchPixels,
  patchLayerRasterMask,
  loadAlphaChannelSelection,
  removeLayer,
  replaceRootLayers,
  replacePixelStore,
  replaceSmartObjectSource,
  removeAlphaChannel,
  renameLayer,
  renameAlphaChannel,
  resizeCanvas,
  revealAll,
  reorderLayer,
  setLayerOpacity,
  setLayerBlendMode,
  setLayerClipping,
  setLayerFillOpacity,
  setLayerEffects,
  setLayerLock,
  setLayerLocks,
  setGroupCompositing,
  setAdjustmentLayerParameters,
  setLayerMaskDensity,
  setLayerMaskEnabled,
  setLayerRasterMask,
  setLayerVectorMask,
  setLayerVectorMaskDensity,
  setLayerVectorMaskEnabled,
  setLayerTransform,
  setLayerTranslation,
  setSelection,
  setShapeGeometry,
  setShapeStyle,
  setSmartObjectFilters,
  setTextContent,
  setTextStyle,
  setLayerVisibility,
  selectAll,
  transformPoint,
  ungroupLayer,
  addDocumentGuide,
  removeDocumentGuide,
  moveDocumentGuide,
  clearDocumentGuides,
  type SelectionCombineMode,
} from './index.js';

const pixel = (id: string) => createPixelLayer({ id, name: id, width: 2, height: 2 });

describe('document model', () => {
  it('owns editable shape and text data and applies structured edit commands immutably', () => {
    const fill = { red: 10, green: 20, blue: 30, alpha: 255 };
    const shape = createShapeLayer({
      id: 'shape',
      name: 'Shape',
      geometry: { type: 'rectangle', width: 20, height: 10, cornerRadius: 2 },
      style: { fill },
    });
    const text = createTextLayer({
      id: 'text',
      name: 'Text',
      text: 'Hello',
      width: 100,
      height: 40,
    });
    fill.red = 200;
    expect(shape.style.fill?.red).toBe(10);

    const original = createDocument({
      id: 'structured',
      name: 'Structured',
      width: 200,
      height: 100,
      layers: [shape, text],
    });
    const editedShape = setShapeStyle('shape', {
      fill: { red: 255, green: 0, blue: 0, alpha: 255 },
      stroke: null,
      strokeWidth: 0,
    }).execute(
      setShapeGeometry('shape', {
        type: 'ellipse',
        width: 30,
        height: 15,
        cornerRadius: 0,
        commands: [],
      }).execute(original),
    );
    const editedText = setTextStyle('text', {
      ...text.style,
      fontSize: 24,
      align: 'center',
    }).execute(setTextContent('text', 'Edited').execute(editedShape));

    expect(findLayer(original, 'shape')).toEqual(shape);
    expect(findLayer(editedText, 'shape')).toMatchObject({
      kind: 'shape',
      geometry: { type: 'ellipse', width: 30, height: 15 },
    });
    expect(findLayer(editedText, 'text')).toMatchObject({
      kind: 'text',
      text: 'Edited',
      style: { fontSize: 24, align: 'center' },
    });
  });

  it('rejects invalid structured geometry and style values', () => {
    expect(() =>
      createShapeLayer({
        id: 'shape',
        name: 'Shape',
        geometry: { width: 0, height: 10 },
      }),
    ).toThrow(DocumentInvariantError);
    expect(() =>
      createTextLayer({
        id: 'text',
        name: 'Text',
        width: 10,
        height: 10,
        style: { fontSize: 0 },
      }),
    ).toThrow(DocumentInvariantError);
  });

  it('owns and validates editable line and cubic vector paths', () => {
    const commands = [
      { type: 'move' as const, x: 1, y: 1 },
      { type: 'line' as const, x: 9, y: 1 },
      {
        type: 'cubic' as const,
        control1X: 10,
        control1Y: 3,
        control2X: 7,
        control2Y: 9,
        x: 1,
        y: 9,
      },
      { type: 'close' as const },
    ];
    const layer = createShapeLayer({
      id: 'path',
      name: 'Path',
      geometry: { type: 'path', width: 10, height: 10, cornerRadius: 0, commands },
    });
    commands[1] = { type: 'line', x: 2, y: 2 };
    expect(layer.geometry.commands[1]).toEqual({ type: 'line', x: 9, y: 1 });

    expect(() =>
      createShapeLayer({
        id: 'bad-path',
        name: 'Bad path',
        geometry: {
          type: 'path',
          width: 10,
          height: 10,
          cornerRadius: 0,
          commands: [
            { type: 'line', x: 1, y: 1 },
            { type: 'line', x: 2, y: 2 },
          ],
        },
      }),
    ).toThrow('begin with a move command');
    expect(() =>
      createShapeLayer({
        id: 'bad-rectangle',
        name: 'Bad rectangle',
        geometry: {
          type: 'rectangle',
          width: 10,
          height: 10,
          cornerRadius: 0,
          commands: [
            { type: 'move', x: 0, y: 0 },
            { type: 'line', x: 1, y: 1 },
          ],
        },
      }),
    ).toThrow('Only path shapes');
  });

  it('owns, validates, and edits nondestructive layer effects', () => {
    const layer = createPixelLayer({
      id: 'effected',
      name: 'Effected',
      width: 1,
      height: 1,
      effects: [
        {
          type: 'drop-shadow',
          enabled: true,
          opacity: 0.5,
          offsetX: 2,
          offsetY: 3,
          blur: 4,
          color: { red: 0, green: 0, blue: 0, alpha: 255 },
        },
      ],
    });
    const document = createDocument({
      id: 'doc',
      name: 'Doc',
      width: 4,
      height: 4,
      layers: [layer],
    });
    const next = setLayerEffects('effected', [
      {
        type: 'color-overlay',
        enabled: true,
        opacity: 1,
        blendMode: 'normal',
        color: { red: 255, green: 0, blue: 0, alpha: 255 },
      },
    ]).execute(document);
    expect(findLayer(document, 'effected')?.effects[0]?.type).toBe('drop-shadow');
    expect(findLayer(next, 'effected')?.effects[0]?.type).toBe('color-overlay');
    expect(() =>
      createPixelLayer({
        id: 'bad',
        name: 'Bad',
        width: 1,
        height: 1,
        effects: [
          {
            type: 'stroke',
            enabled: true,
            opacity: 1,
            size: -1,
            color: { red: 0, green: 0, blue: 0, alpha: 255 },
          },
        ],
      }),
    ).toThrow(DocumentInvariantError);
  });

  it('converts renderable layers to owned smart objects and replaces their contents', () => {
    const pixels = createPixelLayer({
      id: 'pixels',
      name: 'Pixels',
      width: 2,
      height: 1,
      pixels: [255, 0, 0, 255, 0, 255, 0, 255],
      transform: { a: 1, b: 0, c: 0, d: 1, e: 3, f: 4 },
    });
    const original = createDocument({
      id: 'doc',
      name: 'Doc',
      width: 8,
      height: 8,
      layers: [pixels],
    });
    const converted = convertLayerToSmartObject('pixels', 'source', 'embedded').execute(original);
    const smart = findLayer(converted, 'pixels');
    expect(findLayer(original, 'pixels')?.kind).toBe('pixel');
    expect(smart).toMatchObject({
      kind: 'smart-object',
      transform: { e: 3, f: 4 },
      source: { id: 'source', canvas: { width: 2, height: 1 } },
    });
    if (smart?.kind !== 'smart-object') throw new Error('Expected smart object');
    expect(smart.source.layers[0]).toMatchObject({
      id: 'embedded',
      kind: 'pixel',
      transform: { e: 0, f: 0 },
    });
    const filtered = setSmartObjectFilters('pixels', [
      { id: 'invert', type: 'invert', enabled: true, opacity: 1 },
      { id: 'blur', type: 'gaussian-blur', enabled: true, opacity: 0.5, radius: 2 },
    ]).execute(converted);
    expect(findLayer(filtered, 'pixels')).toMatchObject({
      kind: 'smart-object',
      smartFilters: [{ type: 'invert' }, { type: 'gaussian-blur', radius: 2 }],
    });
    expect(() =>
      setSmartObjectFilters('pixels', [
        { id: 'blur', type: 'gaussian-blur', enabled: true, opacity: 1, radius: 101 },
      ]),
    ).toThrow(DocumentInvariantError);
    expect(() =>
      setSmartObjectFilters('pixels', [
        { id: 'repeated', type: 'invert', enabled: true, opacity: 1 },
        { id: 'repeated', type: 'grayscale', enabled: true, opacity: 1 },
      ]),
    ).toThrow(DocumentInvariantError);

    const replaced = replaceSmartObjectSource('pixels', {
      id: 'replacement',
      name: 'Replacement',
      canvas: { width: 1, height: 1 },
      layers: [createShapeLayer({ id: 'shape', name: 'Shape', geometry: { width: 1, height: 1 } })],
    }).execute(converted);
    expect(findLayer(replaced, 'pixels')).toMatchObject({
      kind: 'smart-object',
      source: { id: 'replacement', canvas: { width: 1, height: 1 } },
    });
    expect(() =>
      createSmartObjectLayer({
        id: 'bad',
        name: 'Bad',
        source: { id: 'source', name: 'Source', canvas: { width: 0, height: 1 }, layers: [] },
      }),
    ).toThrow(DocumentInvariantError);
  });

  it('copies caller-owned pixel buffers and rejects duplicate nested ids', () => {
    const source = new Uint8ClampedArray(16);
    const layer = createPixelLayer({ id: 'a', name: 'A', width: 2, height: 2, pixels: source });
    source[0] = 255;
    expect(layer.pixelStore.toContiguous()[0]).toBe(0);
    expect(() =>
      createDocument({
        id: 'doc',
        name: 'Doc',
        width: 2,
        height: 2,
        layers: [layer, createGroupLayer({ id: 'g', name: 'G', children: [layer] })],
      }),
    ).toThrow(DocumentInvariantError);
  });

  it('applies structural and property transforms without changing the prior document', () => {
    const original = createDocument({
      id: 'doc',
      name: 'Doc',
      width: 2,
      height: 2,
      layers: [pixel('a')],
    });
    const group = createGroupLayer({
      id: 'g',
      name: 'Group',
      children: [createGroupLayer({ id: 'nested', name: 'Nested' })],
    });
    let next = addLayer(group, null, 0).execute(original);
    next = addLayer(pixel('b'), 'nested').execute(next);
    next = renameLayer('b', 'Renamed').execute(next);
    next = setLayerVisibility('b', false).execute(next);
    next = setLayerOpacity('b', 0.25).execute(next);
    expect(original.layers.map((layer) => layer.id)).toEqual(['a']);
    expect(findLayer(next, 'b')).toMatchObject({ name: 'Renamed', visible: false, opacity: 0.25 });
    next = reorderLayer(null, 0, 1).execute(next);
    expect(next.layers.map((layer) => layer.id)).toEqual(['a', 'g']);
    next = removeLayer('b').execute(next);
    expect(findLayer(next, 'b')).toBeUndefined();
  });

  it('patches only the selected rectangle and validates its bounds', () => {
    const original = createDocument({
      id: 'doc',
      name: 'Doc',
      width: 2,
      height: 2,
      layers: [pixel('a')],
    });
    const next = patchPixels('a', {
      x: 1,
      y: 0,
      width: 1,
      height: 2,
      pixels: new Uint8ClampedArray(8).fill(7),
    }).execute(original);
    const result = findLayer(next, 'a');
    expect(result?.kind).toBe('pixel');
    if (result?.kind === 'pixel')
      expect([...result.pixelStore.toContiguous()]).toEqual([
        0, 0, 0, 0, 7, 7, 7, 7, 0, 0, 0, 0, 7, 7, 7, 7,
      ]);
    expect(() =>
      patchPixels('a', { x: 2, y: 0, width: 1, height: 1, pixels: [0, 0, 0, 0] }).execute(original),
    ).toThrow('outside the layer bounds');
    expect(
      patchPixels('a', { x: 0, y: 0, width: 1, height: 1, pixels: [1, 2, 3, 4] }, 'Invert pixels')
        .label,
    ).toBe('Invert pixels');
    expect(() =>
      patchPixels('a', { x: 0, y: 0, width: 1, height: 1, pixels: [1, 2, 3, 4] }, ' '),
    ).toThrow('Pixel patch command label cannot be empty');
  });

  it('atomically replaces an immutable canonical pixel store', () => {
    const original = createDocument({
      id: 'doc',
      name: 'Doc',
      width: 2,
      height: 2,
      layers: [pixel('a')],
    });
    const replacement = TiledPixelStore.empty(2, 2).write({
      x: 1,
      y: 1,
      width: 1,
      height: 1,
      pixels: new Uint8ClampedArray([9, 8, 7, 6]),
    }).store;
    const next = replacePixelStore('a', replacement, 'Apply Brightness').execute(original);
    const layer = findLayer(next, 'a');
    const originalLayer = findLayer(original, 'a');
    expect(layer?.kind === 'pixel' ? layer.pixelStore : null).toBe(replacement);
    expect(originalLayer?.kind === 'pixel' ? originalLayer.pixelStore : null).not.toBe(replacement);
    expect(() => replacePixelStore('a', TiledPixelStore.empty(3, 2)).execute(original)).toThrow(
      'dimensions must match',
    );
    expect(() => replacePixelStore('a', replacement, ' ')).toThrow(
      'Pixel store command label cannot be empty',
    );
  });

  it('stores full-resolution selections and immutable affine layer translations', () => {
    const original = createDocument({
      id: 'doc',
      name: 'Doc',
      width: 3,
      height: 2,
      layers: [pixel('a')],
    });
    const selection = createRectangularSelection(original.canvas, {
      x: 1,
      y: 0,
      width: 2,
      height: 1,
    });
    let next = setSelection(selection).execute(original);
    next = setLayerTranslation('a', -3, 7).execute(next);
    selection.coverageStore.toContiguous().fill(0);

    expect(original.selection).toBeNull();
    expect(next.selection?.coverageStore.toContiguous()).toEqual(
      new Uint8ClampedArray([0, 255, 255, 0, 0, 0]),
    );
    expect(findLayer(next, 'a')?.transform).toEqual({ a: 1, b: 0, c: 0, d: 1, e: -3, f: 7 });
  });

  it('composes, inverts, and applies canonical affine matrices', () => {
    const parent = { a: 0, b: 1, c: -1, d: 0, e: 10, f: 20 };
    const local = { a: 2, b: 0, c: 0, d: 3, e: 4, f: 5 };
    const world = multiplyLayerTransforms(parent, local);
    const point = transformPoint(world, 2, 1);

    expect(identityLayerTransform()).toEqual({ a: 1, b: 0, c: 0, d: 1, e: 0, f: 0 });
    expect(world).toEqual({ a: 0, b: 2, c: -3, d: 0, e: 5, f: 24 });
    expect(point).toEqual({ x: 2, y: 28 });
    const restored = transformPoint(invertLayerTransform(world), point.x, point.y);
    expect(restored.x).toBeCloseTo(2);
    expect(restored.y).toBeCloseTo(1);
    expect(() => invertLayerTransform({ a: 1, b: 2, c: 2, d: 4, e: 0, f: 0 })).toThrow(
      'must be invertible',
    );
  });

  it('sets complete transforms immutably and translation preserves the linear matrix', () => {
    const original = createDocument({
      id: 'doc',
      name: 'Doc',
      width: 2,
      height: 2,
      layers: [pixel('a')],
    });
    const requested = { a: 0, b: 1, c: -1, d: 0, e: 4.5, f: -2.25 };
    const transformed = setLayerTransform('a', requested).execute(original);
    const translated = setLayerTranslation('a', 9.25, 3.5).execute(transformed);

    requested.a = 42;
    expect(findLayer(original, 'a')?.transform).toEqual(identityLayerTransform());
    expect(findLayer(transformed, 'a')?.transform).toEqual({
      a: 0,
      b: 1,
      c: -1,
      d: 0,
      e: 4.5,
      f: -2.25,
    });
    expect(findLayer(translated, 'a')?.transform).toEqual({
      a: 0,
      b: 1,
      c: -1,
      d: 0,
      e: 9.25,
      f: 3.5,
    });
    expect(() => setLayerTransform('a', { a: Number.NaN, b: 0, c: 0, d: 1, e: 0, f: 0 })).toThrow(
      'Layer transform a must be finite',
    );
    expect(() =>
      createPixelLayer({
        id: 'singular',
        name: 'Singular',
        width: 1,
        height: 1,
        transform: { a: 1, b: 2, c: 2, d: 4, e: 0, f: 0 },
      }),
    ).toThrow('Layer transform must be invertible');
  });

  it('crops the canvas by translating root world placement without flattening nested transforms', () => {
    const child = createPixelLayer({
      id: 'child',
      name: 'Child',
      width: 2,
      height: 2,
      pixels: new Uint8ClampedArray(16).fill(9),
      transform: { a: 2, b: 0, c: 0.5, d: 3, e: 4, f: 5 },
    });
    const root = createGroupLayer({
      id: 'root',
      name: 'Root',
      children: [child],
      transform: { a: 0, b: 1, c: -1, d: 0, e: 10, f: 20 },
      rasterMask: createRasterMask(
        20,
        20,
        Uint8ClampedArray.from({ length: 400 }, (_, index) => index),
      ),
      vectorMask: createVectorMask(20, 20, [
        { type: 'move', x: 3, y: 2 },
        { type: 'line', x: 10, y: 2 },
        { type: 'line', x: 10, y: 12 },
        { type: 'close' },
      ]),
    });
    const original = createDocument({
      id: 'doc',
      name: 'Doc',
      width: 20,
      height: 20,
      layers: [root],
    });
    const originalWorld = findLayerWorldTransform(original, 'child');
    const originalRoot = findLayer(original, 'root');
    const originalChild = findLayer(original, 'child');
    const cropped = cropDocument({ x: 3, y: -2, width: 8, height: 9 }).execute(original);
    const croppedWorld = findLayerWorldTransform(cropped, 'child');
    const croppedRoot = findLayer(cropped, 'root');
    const croppedChild = findLayer(cropped, 'child');

    expect(cropped.canvas).toEqual({ width: 8, height: 9 });
    expect(croppedRoot?.transform).toEqual({ a: 0, b: 1, c: -1, d: 0, e: 7, f: 22 });
    expect(croppedRoot).toMatchObject({
      kind: 'group',
      vectorMask: {
        width: 8,
        height: 9,
        commands: [
          { type: 'move', x: 0, y: 4 },
          { type: 'line', x: 7, y: 4 },
          { type: 'line', x: 7, y: 14 },
          { type: 'close' },
        ],
      },
      rasterMask: {
        width: 8,
        height: 9,
      },
    });
    expect(
      croppedRoot?.kind === 'group'
        ? croppedRoot.rasterMask?.coverageStore.toContiguous()
        : undefined,
    ).toEqual(
      Uint8ClampedArray.from({ length: 72 }, (_, index) => {
        const x = index % 8;
        const y = Math.floor(index / 8);
        const sourceX = x + 3;
        const sourceY = y - 2;
        return sourceY >= 0 && sourceY < 20 ? sourceY * 20 + sourceX : 0;
      }),
    );
    expect(croppedChild?.transform).toEqual(child.transform);
    expect(croppedWorld).toEqual({
      ...originalWorld,
      e: (originalWorld?.e ?? 0) - 3,
      f: (originalWorld?.f ?? 0) + 2,
    });
    expect(croppedRoot?.kind === 'group' ? croppedRoot.children : undefined).toBe(
      originalRoot?.kind === 'group' ? originalRoot.children : undefined,
    );
    expect(croppedChild?.kind === 'pixel' ? croppedChild.pixels : undefined).toBe(
      originalChild?.kind === 'pixel' ? originalChild.pixels : undefined,
    );
    expect(findLayer(original, 'root')?.transform).toEqual(root.transform);
  });

  it('remaps soft selection and alpha coverage when a crop extends outside the old canvas', () => {
    const mask = createRasterMask(2, 2, [1, 2, 3, 4], { density: 0.5, enabled: false });
    const layer = createPixelLayer({
      id: 'pixel',
      name: 'Pixel',
      width: 2,
      height: 2,
      pixels: new Uint8ClampedArray(16).fill(17),
      rasterMask: mask,
    });
    const original = createDocument({
      id: 'doc',
      name: 'Doc',
      width: 3,
      height: 2,
      layers: [
        layer,
        createAdjustmentLayer({
          id: 'adjustment',
          name: 'Adjustment',
          vectorMask: createVectorMask(3, 2, [
            { type: 'move', x: 0, y: 0 },
            { type: 'line', x: 2, y: 0 },
            { type: 'line', x: 1, y: 2 },
            { type: 'close' },
          ]),
        }),
      ],
      selection: createSelectionMask({
        width: 3,
        height: 2,
        coverage: new Uint8ClampedArray([1, 2, 3, 64, 128, 192]),
      }),
      channels: [
        createAlphaChannel({
          id: 'alpha',
          name: 'Alpha',
          width: 3,
          height: 2,
          coverage: [10, 20, 30, 40, 50, 60],
        }),
      ],
    });
    const crop = { x: -1, y: 1, width: 5, height: 2 };
    const operation = cropDocument(crop);
    crop.x = 99;
    const originalLayer = findLayer(original, 'pixel');
    const cropped = operation.execute(original);
    const croppedLayer = findLayer(cropped, 'pixel');

    expect(operation.label).toBe('Crop document');
    expect(cropped.selection).toMatchObject({ width: 5, height: 2 });
    expect(cropped.selection?.coverageStore.toContiguous()).toEqual(
      new Uint8ClampedArray([0, 64, 128, 192, 0, 0, 0, 0, 0, 0]),
    );
    expect(cropped.channels[0]).toMatchObject({ id: 'alpha', name: 'Alpha', width: 5, height: 2 });
    expect(cropped.channels[0]?.coverageStore.toContiguous()).toEqual(
      new Uint8ClampedArray([0, 40, 50, 60, 0, 0, 0, 0, 0, 0]),
    );
    expect(croppedLayer?.kind === 'pixel' ? croppedLayer.rasterMask : undefined).toBe(
      originalLayer?.kind === 'pixel' ? originalLayer.rasterMask : undefined,
    );
    expect(croppedLayer?.kind === 'pixel' ? croppedLayer.pixels : undefined).toBe(
      originalLayer?.kind === 'pixel' ? originalLayer.pixels : undefined,
    );
    expect(findLayer(cropped, 'adjustment')).toMatchObject({
      kind: 'adjustment',
      vectorMask: {
        width: 5,
        height: 2,
        commands: [
          { type: 'move', x: 1, y: -1 },
          { type: 'line', x: 3, y: -1 },
          { type: 'line', x: 2, y: 1 },
          { type: 'close' },
        ],
      },
    });
    expect(original.selection?.coverageStore.toContiguous()).toEqual(
      new Uint8ClampedArray([1, 2, 3, 64, 128, 192]),
    );
    expect(original.channels[0]?.coverageStore.toContiguous()).toEqual(
      new Uint8ClampedArray([10, 20, 30, 40, 50, 60]),
    );
  });

  it('preserves an implicit full-canvas selection and rejects invalid crop rectangles', () => {
    const original = createDocument({ id: 'doc', name: 'Doc', width: 2, height: 2 });
    expect(
      cropDocument({ x: 1, y: 1, width: 3, height: 4 }).execute(original).selection,
    ).toBeNull();
    expect(() => cropDocument({ x: 0.5, y: 0, width: 1, height: 1 })).toThrow(
      'Crop x must be an integer',
    );
    expect(() => cropDocument({ x: 0, y: 0, width: 0, height: 1 })).toThrow(
      'Crop width must be a positive integer',
    );
    expect(() => cropDocument({ x: 0, y: 0, width: 1, height: Number.NaN })).toThrow(
      'Crop height must be a positive integer',
    );
    expect(() => cropDocument({ x: 0, y: 0, width: 32_769, height: 1 })).toThrow(
      'Crop dimensions cannot exceed 32768 pixels',
    );
    expect(() => cropDocument({ x: 0, y: 0, width: 20_000, height: 10_000 })).toThrow(
      'Crop area cannot exceed 100000000 pixels',
    );
  });

  it('resizes the canvas around all nine named anchor positions', () => {
    const original = createDocument({
      id: 'doc',
      name: 'Doc',
      width: 4,
      height: 4,
      layers: [pixel('a')],
    });
    const anchors = [
      ['top-left', 0, 0],
      ['top-center', 1, 0],
      ['top-right', 2, 0],
      ['center-left', 0, 2],
      ['center', 1, 2],
      ['center-right', 2, 2],
      ['bottom-left', 0, 4],
      ['bottom-center', 1, 4],
      ['bottom-right', 2, 4],
    ] as const;

    for (const [anchor, expectedX, expectedY] of anchors) {
      const operation = resizeCanvas(6, 8, anchor);
      const resized = operation.execute(original);
      expect(operation.label).toBe('Resize canvas');
      expect(resized.canvas).toEqual({ width: 6, height: 8 });
      expect(findLayer(resized, 'a')?.transform).toEqual({
        a: 1,
        b: 0,
        c: 0,
        d: 1,
        e: expectedX,
        f: expectedY,
      });
    }
  });

  it('remaps canvas-space coverage and preserves layer-local data during anchored resize', () => {
    const layer = createPixelLayer({
      id: 'pixel',
      name: 'Pixel',
      width: 2,
      height: 2,
      pixels: new Uint8ClampedArray(16).fill(21),
      rasterMask: createRasterMask(2, 2, [9, 8, 7, 6]),
      transform: { a: 0, b: 1, c: -1, d: 0, e: 7, f: 8 },
    });
    const original = createDocument({
      id: 'doc',
      name: 'Doc',
      width: 2,
      height: 2,
      layers: [createGroupLayer({ id: 'group', name: 'Group', children: [layer] })],
      selection: createSelectionMask({
        width: 2,
        height: 2,
        coverage: new Uint8ClampedArray([1, 2, 3, 4]),
      }),
      channels: [
        createAlphaChannel({
          id: 'alpha',
          name: 'Alpha',
          width: 2,
          height: 2,
          coverage: [10, 20, 30, 40],
        }),
      ],
    });
    const originalGroup = findLayer(original, 'group');
    const originalLayer = findLayer(original, 'pixel');
    const resized = resizeCanvas(4, 4, 'bottom-right').execute(original);
    const resizedGroup = findLayer(resized, 'group');
    const resizedLayer = findLayer(resized, 'pixel');

    expect(resized.selection?.coverageStore.toContiguous()).toEqual(
      new Uint8ClampedArray([0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 2, 0, 0, 3, 4]),
    );
    expect(resized.channels[0]?.coverageStore.toContiguous()).toEqual(
      new Uint8ClampedArray([0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 10, 20, 0, 0, 30, 40]),
    );
    expect(resizedGroup?.transform).toEqual({ a: 1, b: 0, c: 0, d: 1, e: 2, f: 2 });
    expect(resizedGroup?.kind === 'group' ? resizedGroup.children : undefined).toBe(
      originalGroup?.kind === 'group' ? originalGroup.children : undefined,
    );
    expect(resizedLayer?.kind === 'pixel' ? resizedLayer.pixels : undefined).toBe(
      originalLayer?.kind === 'pixel' ? originalLayer.pixels : undefined,
    );
    expect(resizedLayer?.kind === 'pixel' ? resizedLayer.rasterMask : undefined).toBe(
      originalLayer?.kind === 'pixel' ? originalLayer.rasterMask : undefined,
    );
    expect(original.selection?.coverageStore.toContiguous()).toEqual(
      new Uint8ClampedArray([1, 2, 3, 4]),
    );
  });

  it('supports owned numeric anchor fractions and deterministic nearest-pixel positioning', () => {
    const anchor = { x: 0.25, y: 0.75 };
    const operation = resizeCanvas(6, 6, anchor);
    anchor.x = 1;
    const resized = operation.execute(
      createDocument({ id: 'doc', name: 'Doc', width: 2, height: 2, layers: [pixel('a')] }),
    );

    expect(findLayer(resized, 'a')?.transform).toEqual({
      a: 1,
      b: 0,
      c: 0,
      d: 1,
      e: 1,
      f: 3,
    });
    expect(() => resizeCanvas(1, 1, { x: -0.1, y: 0.5 })).toThrow(
      'Canvas anchor fractions must be between 0 and 1',
    );
    expect(() => resizeCanvas(1, 1, { x: Number.NaN, y: 0.5 })).toThrow(
      'Canvas anchor x must be finite',
    );
    expect(() => resizeCanvas(32_769, 1)).toThrow('Canvas dimensions cannot exceed 32768 pixels');
    expect(() => resizeCanvas(20_000, 10_000)).toThrow(
      'Canvas area cannot exceed 100000000 pixels',
    );
  });

  it('trims canvas-space coverage from the anchored edges when shrinking', () => {
    const original = createDocument({
      id: 'doc',
      name: 'Doc',
      width: 4,
      height: 4,
      layers: [pixel('a')],
      selection: createSelectionMask({
        width: 4,
        height: 4,
        coverage: new Uint8ClampedArray(Array.from({ length: 16 }, (_, index) => index)),
      }),
    });
    const resized = resizeCanvas(2, 2, 'center').execute(original);

    expect(resized.selection?.coverageStore.toContiguous()).toEqual(
      new Uint8ClampedArray([5, 6, 9, 10]),
    );
    expect(findLayer(resized, 'a')?.transform).toEqual({
      a: 1,
      b: 0,
      c: 0,
      d: 1,
      e: -1,
      f: -1,
    });
  });

  it('reveals nested transformed layer bounds without trimming the existing canvas', () => {
    const leftLayer = createPixelLayer({
      id: 'left',
      name: 'Left',
      width: 2,
      height: 3,
      transform: { a: 0, b: 1, c: -1, d: 0, e: 0, f: 0 },
      rasterMask: createRasterMask(2, 3, [1, 2, 3, 4, 5, 6]),
    });
    const rightLayer = createPixelLayer({
      id: 'right',
      name: 'Right',
      width: 2,
      height: 1,
      transform: { a: 1, b: 0, c: 0, d: 1, e: 5.2, f: 4.2 },
    });
    const hidden = createPixelLayer({
      id: 'hidden',
      name: 'Hidden',
      width: 1,
      height: 1,
      visible: false,
      transform: { a: 1, b: 0, c: 0, d: 1, e: -100, f: -100 },
    });
    const original = createDocument({
      id: 'doc',
      name: 'Doc',
      width: 4,
      height: 4,
      layers: [
        createGroupLayer({
          id: 'group',
          name: 'Group',
          children: [leftLayer],
          transform: { a: 1, b: 0, c: 0, d: 1, e: -2, f: 1 },
        }),
        rightLayer,
        hidden,
      ],
      selection: createSelectionMask({
        width: 4,
        height: 4,
        coverage: new Uint8ClampedArray(16).fill(64),
      }),
      channels: [
        createAlphaChannel({
          id: 'alpha',
          name: 'Alpha',
          width: 4,
          height: 4,
          coverage: new Uint8ClampedArray(16).fill(128),
        }),
      ],
    });
    const originalLeft = findLayer(original, 'left');
    const operation = revealAll();
    const revealed = operation.execute(original);

    expect(operation.label).toBe('Reveal all');
    expect(revealed.canvas).toEqual({ width: 13, height: 6 });
    expect(findLayer(revealed, 'group')?.transform).toEqual({
      a: 1,
      b: 0,
      c: 0,
      d: 1,
      e: 3,
      f: 1,
    });
    expect(findLayer(revealed, 'right')?.transform).toEqual({
      a: 1,
      b: 0,
      c: 0,
      d: 1,
      e: 10.2,
      f: 4.2,
    });
    expect(findLayer(revealed, 'left')?.transform).toEqual(leftLayer.transform);
    expect(
      findLayer(revealed, 'left')?.kind === 'pixel'
        ? findLayer(revealed, 'left')?.pixels
        : undefined,
    ).toBe(originalLeft?.kind === 'pixel' ? originalLeft.pixels : undefined);
    expect(revealed.selection).toMatchObject({ width: 13, height: 6 });
    expect(revealed.selection?.coverageStore.toContiguous()[5]).toBe(64);
    expect(revealed.selection?.coverageStore.toContiguous()[4]).toBe(0);
    expect(revealed.channels[0]).toMatchObject({ width: 13, height: 6 });
    expect(revealed.channels[0]?.coverageStore.toContiguous()[5]).toBe(128);
    expect(original.canvas).toEqual({ width: 4, height: 4 });
  });

  it('keeps Reveal All as a no-op when renderable layers are already contained', () => {
    const original = createDocument({
      id: 'doc',
      name: 'Doc',
      width: 4,
      height: 4,
      layers: [pixel('inside')],
    });
    expect(revealAll().execute(original)).toBe(original);
    const onlyHidden = createDocument({
      id: 'hidden-doc',
      name: 'Hidden',
      width: 4,
      height: 4,
      layers: [
        createPixelLayer({
          id: 'hidden',
          name: 'Hidden',
          width: 2,
          height: 2,
          visible: false,
          transform: { a: 1, b: 0, c: 0, d: 1, e: -10, f: -10 },
        }),
      ],
    });
    expect(revealAll().execute(onlyHidden)).toBe(onlyHidden);
  });

  it('combines rectangular selections with replace and 8-bit union coverage', () => {
    const original = createDocument({
      id: 'doc',
      name: 'Doc',
      width: 3,
      height: 2,
      selection: createSelectionMask({
        width: 3,
        height: 2,
        coverage: new Uint8ClampedArray([0, 64, 128, 192, 255, 32]),
      }),
    });
    const added = combineRectangularSelection({ x: 1, y: 0, width: 1, height: 2 }, 'add').execute(
      original,
    );
    const replaced = combineRectangularSelection(
      { x: 0, y: 1, width: 2, height: 1 },
      'replace',
    ).execute(original);

    expect(added.selection?.coverageStore.toContiguous()).toEqual(
      new Uint8ClampedArray([0, 255, 128, 192, 255, 32]),
    );
    expect(replaced.selection?.coverageStore.toContiguous()).toEqual(
      new Uint8ClampedArray([0, 0, 0, 255, 255, 0]),
    );
    expect(original.selection?.coverageStore.toContiguous()).toEqual(
      new Uint8ClampedArray([0, 64, 128, 192, 255, 32]),
    );
    expect(added.selection?.coverageStore.toContiguous()).not.toBe(
      original.selection?.coverageStore.toContiguous(),
    );
  });

  it('subtracts and intersects rectangular coverage without mutating soft edges', () => {
    const original = createDocument({
      id: 'doc',
      name: 'Doc',
      width: 3,
      height: 1,
      selection: createSelectionMask({
        width: 3,
        height: 1,
        coverage: new Uint8ClampedArray([63, 127, 191]),
      }),
    });
    const subtracted = combineRectangularSelection(
      { x: 1, y: 0, width: 1, height: 1 },
      'subtract',
    ).execute(original);
    const intersected = combineRectangularSelection(
      { x: 1, y: 0, width: 1, height: 1 },
      'intersect',
    ).execute(original);

    expect(subtracted.selection?.coverageStore.toContiguous()).toEqual(
      new Uint8ClampedArray([63, 0, 191]),
    );
    expect(intersected.selection?.coverageStore.toContiguous()).toEqual(
      new Uint8ClampedArray([0, 127, 0]),
    );
  });

  it('combines arbitrary owned soft masks and rejects invalid modes or canvas sizes', () => {
    const original = createDocument({
      id: 'doc',
      name: 'Doc',
      width: 3,
      height: 1,
      selection: createSelectionMask({
        width: 3,
        height: 1,
        coverage: new Uint8ClampedArray([128, 64, 0]),
      }),
    });
    const operand = createSelectionMask({
      width: 3,
      height: 1,
      coverage: new Uint8ClampedArray([64, 128, 255]),
    });
    const add = combineSelection(operand, 'add');
    operand.coverageStore.toContiguous().fill(0);
    const added = add.execute(original);
    expect(added.selection?.coverageStore.toContiguous()).toEqual(
      new Uint8ClampedArray([160, 160, 255]),
    );
    expect(original.selection?.coverageStore.toContiguous()).toEqual(
      new Uint8ClampedArray([128, 64, 0]),
    );
    expect(() =>
      combineSelection(
        createSelectionMask({ width: 2, height: 1, coverage: new Uint8ClampedArray(2) }),
        'replace',
      ).execute(original),
    ).toThrow('Selection mask must match the document canvas');
    expect(() => combineSelection(operand, 'invalid' as SelectionCombineMode)).toThrow(
      'Selection mode is invalid',
    );
  });

  it('combines sparse rectangular selections without densifying large canvases', () => {
    const width = 4096;
    const height = 4096;
    const original = createDocument({
      id: 'sparse-combine',
      name: 'Sparse combine',
      width,
      height,
      selection: createRectangularSelection(
        { width, height },
        { x: 10, y: 10, width: 64, height: 64 },
      ),
    });
    const beforeBytes = original.selection?.coverageStore.allocatedByteLength ?? 0;
    expect(beforeBytes).toBeLessThan(width * height);
    const added = combineRectangularSelection(
      { x: 100, y: 100, width: 64, height: 64 },
      'add',
    ).execute(original);
    const afterBytes = added.selection?.coverageStore.allocatedByteLength ?? 0;
    // Sparse combine may allocate whole default tiles (e.g. 256²) for the dirty patches,
    // but must remain orders of magnitude below a full 4096² coverage buffer.
    expect(afterBytes).toBeGreaterThan(0);
    expect(afterBytes).toBeLessThan((width * height) / 10);
    expect(afterBytes).toBeLessThan(256 * 256 * 4);
    expect(added.selection?.coverageStore.toContiguous()[10 * width + 10]).toBe(255);
    expect(added.selection?.coverageStore.toContiguous()[100 * width + 100]).toBe(255);
    expect(added.selection?.coverageStore.toContiguous()[0]).toBe(0);
  });

  it('combines mismatched sparse tile grids without full-canvas buffers', () => {
    const size = 8192;
    const leftStore = TiledCoverageStore.empty(size, size, 64).write({
      x: 4000,
      y: 4000,
      width: 32,
      height: 32,
      coverage: new Uint8ClampedArray(32 * 32).fill(200),
    }).store;
    const rightStore = TiledCoverageStore.empty(size, size, 256).write({
      x: 4016,
      y: 4016,
      width: 32,
      height: 32,
      coverage: new Uint8ClampedArray(32 * 32).fill(100),
    }).store;
    const document = createDocument({
      id: 'mixed-grid-combine',
      name: 'Mixed grid combine',
      width: size,
      height: size,
      selection: createSelectionMask({ width: size, height: size, coverageStore: leftStore }),
    });
    const operand = createSelectionMask({ width: size, height: size, coverageStore: rightStore });
    const execute = (mode: 'add' | 'subtract' | 'intersect') => {
      const started = performance.now();
      const result = combineSelection(operand, mode).execute(document);
      const elapsedMs = performance.now() - started;
      if (!result.selection) throw new Error('expected sparse selection');
      expect(result.selection.coverageStore.allocatedByteLength).toBeLessThan(64 * 64 * 16);
      expect(elapsedMs).toBeLessThan(250);
      return result.selection.coverageStore;
    };
    const add = execute('add');
    const subtract = execute('subtract');
    const intersect = execute('intersect');
    const sample = (store: TiledCoverageStore, x: number, y: number) =>
      store.read({ x, y, width: 1, height: 1 })[0];
    expect(sample(add, 4001, 4001)).toBe(200);
    expect(sample(add, 4020, 4020)).toBe(222);
    expect(sample(add, 4040, 4040)).toBe(100);
    expect(sample(subtract, 4001, 4001)).toBe(200);
    expect(sample(subtract, 4020, 4020)).toBe(122);
    expect(sample(subtract, 4040, 4040)).toBe(0);
    expect(sample(intersect, 4001, 4001)).toBe(0);
    expect(sample(intersect, 4020, 4020)).toBe(78);
    expect(sample(intersect, 4040, 4040)).toBe(0);
  });

  it('treats null selection as full canvas for add and intersect without densifying add', () => {
    const document = createDocument({
      id: 'full-select',
      name: 'Full',
      width: 4,
      height: 4,
      selection: null,
    });
    const added = combineRectangularSelection({ x: 0, y: 0, width: 1, height: 1 }, 'add').execute(
      document,
    );
    expect(added.selection).toBeNull();
    const intersected = combineRectangularSelection(
      { x: 1, y: 1, width: 2, height: 2 },
      'intersect',
    ).execute(document);
    expect(intersected.selection?.coverageStore.toContiguous()[1 * 4 + 1]).toBe(255);
    // 2×2 rect on a 4×4 canvas may still materialize the small document store fully.
    expect(intersected.selection?.coverageStore.allocatedByteLength).toBeLessThanOrEqual(4 * 4);
  });

  it('subtracts from full-canvas via invert without intermediate operand materialization', () => {
    const document = createDocument({
      id: 'full-subtract',
      name: 'Full subtract',
      width: 4,
      height: 4,
      selection: null,
    });
    // Subtract empty operand from select-all → still select-all (null).
    const emptyOperand = createSelectionMask({
      width: 4,
      height: 4,
      coverageStore: TiledCoverageStore.empty(4, 4),
    });
    const stillFull = combineSelection(emptyOperand, 'subtract').execute(document);
    expect(stillFull.selection).toBeNull();

    // Subtract fully selected operand from select-all → empty sparse (no tiles).
    const fullOperand = createSelectionMask({
      width: 4,
      height: 4,
      coverage: new Uint8ClampedArray(16).fill(255),
    });
    const emptied = combineSelection(fullOperand, 'subtract').execute(document);
    expect(emptied.selection?.coverageStore.tileCount).toBe(0);
    expect(emptied.selection?.coverageStore.toContiguous()).toEqual(new Uint8ClampedArray(16));

    // Subtract partial: equivalent to invert of the operand.
    const partial = combineRectangularSelection(
      { x: 0, y: 0, width: 1, height: 1 },
      'subtract',
    ).execute(document);
    expect(partial.selection?.coverageStore.read({ x: 0, y: 0, width: 1, height: 1 })[0]).toBe(0);
    expect(partial.selection?.coverageStore.read({ x: 1, y: 0, width: 1, height: 1 })[0]).toBe(255);
  });

  it('bakes sparse selection into identity raster masks without densifying large canvases', () => {
    const width = 4096;
    const height = 4096;
    const selection = createRectangularSelection(
      { width, height },
      { x: 32, y: 48, width: 64, height: 32 },
    );
    const selectionBytes = selection.coverageStore.allocatedByteLength;
    expect(selectionBytes).toBeLessThan(width * height);

    const document = createDocument({
      id: 'sparse-mask',
      name: 'Sparse mask',
      width,
      height,
      selection,
      layers: [
        createPixelLayer({
          id: 'pixel',
          name: 'Pixel',
          width,
          height,
        }),
        createGroupLayer({
          id: 'group',
          name: 'Group',
          children: [],
        }),
      ],
    });

    const maskedPixel = addLayerRasterMaskFromSelection('pixel').execute(document);
    const pixelLayer = findLayer(maskedPixel, 'pixel');
    const pixelMaskBytes =
      pixelLayer?.kind === 'pixel'
        ? (pixelLayer.rasterMask?.coverageStore.allocatedByteLength ?? 0)
        : 0;
    expect(pixelMaskBytes).toBeGreaterThan(0);
    expect(pixelMaskBytes).toBeLessThan((width * height) / 10);
    expect(pixelMaskBytes).toBeLessThanOrEqual(selectionBytes * 2);
    expect(
      pixelLayer?.kind === 'pixel'
        ? pixelLayer.rasterMask?.coverageStore.read({ x: 32, y: 48, width: 64, height: 32 })
        : undefined,
    ).toEqual(new Uint8ClampedArray(64 * 32).fill(255));

    const maskedGroup = addLayerRasterMaskFromSelection('group').execute(document);
    const groupLayer = findLayer(maskedGroup, 'group');
    const groupMaskBytes = groupLayer?.rasterMask?.coverageStore.allocatedByteLength ?? 0;
    expect(groupMaskBytes).toBeGreaterThan(0);
    expect(groupMaskBytes).toBeLessThan((width * height) / 10);
    expect(
      groupLayer?.rasterMask?.coverageStore.read({ x: 32, y: 48, width: 64, height: 32 }),
    ).toEqual(new Uint8ClampedArray(64 * 32).fill(255));
  });

  it('bakes a sparse complement selection into an identity raster mask without densifying', () => {
    const size = 4096;
    const complement = TiledCoverageStore.uniform(size, size, 255, 256).write({
      x: 512,
      y: 768,
      width: 16,
      height: 8,
      coverage: new Uint8ClampedArray(16 * 8),
    }).store;
    const document = createDocument({
      id: 'complement-mask',
      name: 'Complement mask',
      width: size,
      height: size,
      selection: createSelectionMask({ width: size, height: size, coverageStore: complement }),
      layers: [createPixelLayer({ id: 'pixel', name: 'Pixel', width: size, height: size })],
    });
    const masked = addLayerRasterMaskFromSelection('pixel').execute(document);
    const layer = findLayer(masked, 'pixel');
    expect(layer?.kind).toBe('pixel');
    if (layer?.kind !== 'pixel' || !layer.rasterMask) throw new Error('Expected raster mask');
    expect(layer.rasterMask.coverageStore).toMatchObject({
      defaultCoverage: 255,
      tileCount: 1,
      allocatedByteLength: 256 * 256,
    });
  });

  it('owns rectangular selections sparsely without a contiguous coverage mirror', () => {
    const selection = createRectangularSelection(
      { width: 512, height: 512 },
      {
        x: 10,
        y: 20,
        width: 4,
        height: 3,
      },
    );
    expect(selection.coverageStore.tileCount).toBeGreaterThan(0);
    expect(selection.coverageStore.allocatedByteLength).toBeLessThan(512 * 512);
    expect(selection.coverageStore.read({ x: 10, y: 20, width: 4, height: 3 })).toEqual(
      new Uint8ClampedArray(12).fill(255),
    );
    expect(selection.coverageStore.read({ x: 0, y: 0, width: 1, height: 1 })[0]).toBe(0);
    // SelectionMask no longer exposes a dual contiguous mirror field.
    expect('coverage' in selection).toBe(false);

    const document = createDocument({
      id: 'sparse-selection',
      name: 'Sparse selection',
      width: 512,
      height: 512,
      selection,
    });
    const channel = addAlphaChannelFromSelection('alpha', 'Alpha').execute(document).channels[0];
    expect(channel?.coverageStore).toBe(selection.coverageStore);
    expect(channel?.coverageStore.allocatedByteLength).toBe(
      selection.coverageStore.allocatedByteLength,
    );
    expect('coverage' in (channel ?? {})).toBe(false);
  });

  it('uses full-canvas coverage for select all and inverts it to an empty mask', () => {
    const original = createDocument({ id: 'doc', name: 'Doc', width: 2, height: 2 });
    const inverted = invertSelection().execute(original);
    expect(inverted.selection?.coverageStore.toContiguous()).toEqual(new Uint8ClampedArray(4));
    expect(inverted.selection?.coverageStore.toContiguous()).toEqual(new Uint8ClampedArray(4));

    const selected = selectAll().execute(inverted);
    expect(selected.selection).toBeNull();
    expect(invertSelection().execute(selected).selection?.coverageStore.toContiguous()).toEqual(
      new Uint8ClampedArray(4),
    );
  });

  it('inverts an empty sparse selection to select-all without densifying', () => {
    const empty = createDocument({
      id: 'empty-sel',
      name: 'Empty',
      width: 256,
      height: 256,
      selection: createSelectionMask({
        width: 256,
        height: 256,
        coverageStore: TiledCoverageStore.empty(256, 256),
      }),
    });
    expect(empty.selection?.coverageStore.tileCount).toBe(0);
    const inverted = invertSelection().execute(empty);
    // Empty inverted becomes select-all (null) — no full-canvas allocation.
    expect(inverted.selection).toBeNull();
  });

  it('inverts a partial 4096px selection as a sparse complement', () => {
    const size = 4096;
    const selection = createRectangularSelection(
      { width: size, height: size },
      { x: 1024, y: 2048, width: 32, height: 16 },
    );
    const document = createDocument({
      id: 'partial-complement',
      name: 'Partial complement',
      width: size,
      height: size,
      selection,
    });
    const materialize = vi.spyOn(selection.coverageStore, 'toContiguous');
    const inverted = invertSelection().execute(document).selection;
    expect(materialize).not.toHaveBeenCalled();
    expect(inverted?.coverageStore).toMatchObject({
      defaultCoverage: 255,
      tileCount: 1,
      allocatedByteLength: 256 * 256,
    });
    expect(inverted?.coverageStore.read({ x: 1024, y: 2048, width: 1, height: 1 })[0]).toBe(0);
    expect(inverted?.coverageStore.read({ x: 0, y: 0, width: 1, height: 1 })[0]).toBe(255);
    const restored = invertSelection().execute({ ...document, selection: inverted }).selection;
    expect(restored?.coverageStore).toMatchObject({
      defaultCoverage: 0,
      tileCount: 1,
      allocatedByteLength: 256 * 256,
    });
    expect(restored?.coverageStore.read({ x: 1024, y: 2048, width: 1, height: 1 })[0]).toBe(255);
    expect(restored?.coverageStore.read({ x: 0, y: 0, width: 1, height: 1 })[0]).toBe(0);
  });

  it('crops sparse selection coverage without densifying the full source canvas', () => {
    const selection = createRectangularSelection(
      { width: 1024, height: 1024 },
      { x: 100, y: 200, width: 32, height: 16 },
    );
    const document = createDocument({
      id: 'crop-sparse',
      name: 'Crop sparse',
      width: 1024,
      height: 1024,
      selection,
    });
    const beforeBytes = selection.coverageStore.allocatedByteLength;
    expect(beforeBytes).toBeLessThan(1024 * 1024);
    const cropped = cropDocument({ x: 90, y: 190, width: 64, height: 48 }).execute(document);
    expect(cropped.canvas).toEqual({ width: 64, height: 48 });
    const afterBytes = cropped.selection?.coverageStore.allocatedByteLength ?? 0;
    expect(afterBytes).toBeGreaterThan(0);
    // Dest canvas is 64×48; sparse crop may allocate that entire small canvas as one tile.
    expect(afterBytes).toBeLessThanOrEqual(64 * 48);
    expect(afterBytes).toBeLessThan(1024 * 1024);
    // Selection content shifted into crop coordinates.
    expect(cropped.selection?.coverageStore.read({ x: 10, y: 10, width: 32, height: 16 })).toEqual(
      new Uint8ClampedArray(32 * 16).fill(255),
    );
    expect(cropped.selection?.coverageStore.read({ x: 0, y: 0, width: 1, height: 1 })[0]).toBe(0);
  });

  it('inverts a fully selected tiled mask to empty sparse coverage', () => {
    const fullCoverage = new Uint8ClampedArray(4 * 4).fill(255);
    const full = createDocument({
      id: 'full-sel',
      name: 'Full',
      width: 4,
      height: 4,
      selection: createSelectionMask({
        width: 4,
        height: 4,
        coverage: fullCoverage,
      }),
    });
    const inverted = invertSelection().execute(full);
    expect(inverted.selection?.coverageStore.tileCount).toBe(0);
    expect(inverted.selection?.coverageStore.toContiguous()).toEqual(new Uint8ClampedArray(16));
  });

  it('accepts an explicit selection history label and rejects empty labels', () => {
    const selection = createSelectionMask({
      width: 1,
      height: 1,
      coverage: new Uint8ClampedArray([255]),
    });
    expect(setSelection(selection, 'Feather selection').label).toBe('Feather selection');
    const stored = setSelection(selection).execute(
      createDocument({ id: 'selection-store', name: 'Selection store', width: 1, height: 1 }),
    );
    expect(stored.selection?.coverageStore.toContiguous()).toEqual(
      selection.coverageStore.toContiguous(),
    );
    expect(() => setSelection(selection, ' ')).toThrow('Selection command label cannot be empty');
  });

  it('clips combined rectangles and rejects invalid rectangle or mask dimensions', () => {
    const original = createDocument({ id: 'doc', name: 'Doc', width: 3, height: 2 });
    const clipped = combineRectangularSelection({ x: -1, y: 1, width: 3, height: 2 }).execute(
      original,
    );
    expect(clipped.selection?.coverageStore.toContiguous()).toEqual(
      new Uint8ClampedArray([0, 0, 0, 255, 255, 0]),
    );
    expect(() =>
      combineRectangularSelection({ x: 0, y: 0, width: 0, height: 1 }).execute(original),
    ).toThrow('Selection width must be a positive integer');
    expect(() =>
      setSelection(
        createSelectionMask({ width: 2, height: 2, coverage: new Uint8ClampedArray(4) }),
      ).execute(original),
    ).toThrow('Selection mask must match the document canvas');
  });

  it('creates an owned raster mask from selection using nested world translation', () => {
    const sourceCoverage = new Uint8ClampedArray([255, 64]);
    const sourceMask = createRasterMask(2, 1, sourceCoverage);
    const sourceLayer = createPixelLayer({
      id: 'source',
      name: 'Source',
      width: 2,
      height: 1,
      rasterMask: sourceMask,
    });
    sourceCoverage.fill(0);
    expect(sourceLayer.rasterMask?.coverageStore.toContiguous()).toEqual(
      new Uint8ClampedArray([255, 64]),
    );

    const document = createDocument({
      id: 'doc',
      name: 'Doc',
      width: 4,
      height: 3,
      selection: createRectangularSelection(
        { width: 4, height: 3 },
        { x: 2, y: 1, width: 1, height: 1 },
      ),
      layers: [
        createShapeLayer({
          id: 'shape',
          name: 'Shape',
          geometry: { type: 'rectangle', width: 2, height: 1, cornerRadius: 0 },
          transform: { a: 1, b: 0, c: 0, d: 1, e: 2, f: 1 },
        }),
        createGroupLayer({
          id: 'group',
          name: 'Group',
          transform: { a: 1, b: 0, c: 0, d: 1, e: 1, f: 1 },
          children: [
            createPixelLayer({
              id: 'pixel',
              name: 'Pixel',
              width: 2,
              height: 1,
              transform: { a: 1, b: 0, c: 0, d: 1, e: 1, f: 0 },
            }),
          ],
        }),
      ],
    });
    const masked = addLayerRasterMaskFromSelection('pixel').execute(document);
    const layer = findLayer(masked, 'pixel');
    expect(
      layer?.kind === 'pixel' ? layer.rasterMask?.coverageStore.toContiguous() : undefined,
    ).toEqual(new Uint8ClampedArray([255, 0]));
    expect(() => addLayerRasterMaskFromSelection('pixel').execute(masked)).toThrow(
      'already has a raster mask',
    );
    expect(findLayerWorldTransform(masked, 'pixel')).toEqual({
      a: 1,
      b: 0,
      c: 0,
      d: 1,
      e: 2,
      f: 1,
    });
    const structured = addLayerRasterMaskFromSelection('shape').execute(document);
    const structuredLayer = findLayer(structured, 'shape');
    expect(structuredLayer).toMatchObject({
      kind: 'shape',
      rasterMask: { width: 2, height: 1 },
    });
    expect(structuredLayer?.rasterMask?.coverageStore.toContiguous()).toEqual(
      new Uint8ClampedArray([255, 0]),
    );
    const grouped = addLayerRasterMaskFromSelection('group').execute(document);
    expect(findLayer(grouped, 'group')).toMatchObject({
      kind: 'group',
      rasterMask: {
        width: 4,
        height: 3,
      },
    });
    const groupedLayer = findLayer(grouped, 'group');
    expect(groupedLayer?.rasterMask?.coverageStore.toContiguous()).toEqual(
      document.selection?.coverageStore.toContiguous(),
    );
  });

  it('samples document selections through nested affine world transforms', () => {
    const document = createDocument({
      id: 'doc',
      name: 'Doc',
      width: 4,
      height: 4,
      selection: createRectangularSelection(
        { width: 4, height: 4 },
        { x: 2, y: 2, width: 1, height: 1 },
      ),
      layers: [
        createGroupLayer({
          id: 'group',
          name: 'Group',
          transform: { a: 0, b: 1, c: -1, d: 0, e: 3, f: 1 },
          children: [createPixelLayer({ id: 'pixel', name: 'Pixel', width: 2, height: 1 })],
        }),
      ],
    });

    const masked = addLayerRasterMaskFromSelection('pixel').execute(document);
    const layer = findLayer(masked, 'pixel');
    expect(
      layer?.kind === 'pixel' ? layer.rasterMask?.coverageStore.toContiguous() : undefined,
    ).toEqual(new Uint8ClampedArray([0, 255]));
  });

  it('bakes rotated affine mask samples without densifying a large sparse selection', () => {
    const width = 4096;
    const height = 4096;
    const selection = createRectangularSelection(
      { width, height },
      { x: 2000, y: 1500, width: 8, height: 8 },
    );
    const selectionBytes = selection.coverageStore.allocatedByteLength;
    expect(selectionBytes).toBeLessThan(width * height);

    // 90° rotation-like placement via nested group (same as existing affine unit).
    const document = createDocument({
      id: 'rotated-mask',
      name: 'Rotated mask',
      width,
      height,
      selection,
      layers: [
        createGroupLayer({
          id: 'group',
          name: 'Group',
          // Rotate 90° CW around origin then translate so local samples hit the rect.
          transform: { a: 0, b: 1, c: -1, d: 0, e: 2008, f: 1500 },
          children: [createPixelLayer({ id: 'pixel', name: 'Pixel', width: 8, height: 4 })],
        }),
      ],
    });

    const beforeBytes = selection.coverageStore.allocatedByteLength;
    const masked = addLayerRasterMaskFromSelection('pixel').execute(document);
    const layer = findLayer(masked, 'pixel');
    if (layer?.kind !== 'pixel' || !layer.rasterMask) throw new Error('expected masked pixel');
    // Selection store is immutable and must remain sparse after bake.
    expect(selection.coverageStore.allocatedByteLength).toBe(beforeBytes);
    // Layer mask is only 8×4 — must not allocate a 4096² buffer.
    expect(layer.rasterMask.coverageStore.allocatedByteLength).toBeLessThanOrEqual(8 * 4);
    expect(layer.rasterMask.width).toBe(8);
    expect(layer.rasterMask.height).toBe(4);
  });

  it('keeps a rotated 4096px destination mask sparse and bounded', () => {
    const size = 4096;
    const selection = createRectangularSelection(
      { width: size, height: size },
      { x: 2000, y: 1500, width: 8, height: 8 },
    );
    const document = createDocument({
      id: 'large-affine-mask',
      name: 'Large affine mask',
      width: size,
      height: size,
      selection,
      layers: [
        createPixelLayer({
          id: 'pixel',
          name: 'Pixel',
          width: size,
          height: size,
          transform: { a: 0, b: 1, c: -1, d: 0, e: 2008, f: 1500 },
        }),
      ],
    });
    const started = performance.now();
    const masked = addLayerRasterMaskFromSelection('pixel').execute(document);
    const elapsedMs = performance.now() - started;
    const layer = findLayer(masked, 'pixel');
    if (layer?.kind !== 'pixel' || !layer.rasterMask) throw new Error('expected mask');
    expect(layer.rasterMask.coverageStore.read({ x: 0, y: 0, width: 8, height: 8 })).toEqual(
      new Uint8ClampedArray(64).fill(255),
    );
    expect(layer.rasterMask.coverageStore.allocatedByteLength).toBeLessThanOrEqual(256 * 256);
    expect(layer.rasterMask.coverageStore.allocatedByteLength).toBeLessThan((size * size) / 100);
    expect(elapsedMs).toBeLessThan(250);
  });

  it('matches brute-force affine selection sampling for a skewed layer', () => {
    const selection = createRectangularSelection(
      { width: 32, height: 24 },
      { x: 8, y: 6, width: 12, height: 10 },
    );
    const transform = { a: 1, b: 0.2, c: 0.35, d: 1, e: 3, f: 2 };
    const document = createDocument({
      id: 'skew-mask',
      name: 'Skew mask',
      width: 32,
      height: 24,
      selection,
      layers: [createPixelLayer({ id: 'pixel', name: 'Pixel', width: 17, height: 13, transform })],
    });
    const masked = addLayerRasterMaskFromSelection('pixel').execute(document);
    const layer = findLayer(masked, 'pixel');
    if (layer?.kind !== 'pixel' || !layer.rasterMask) throw new Error('expected mask');
    const expected = new Uint8ClampedArray(17 * 13);
    for (let y = 0; y < 13; y += 1) {
      for (let x = 0; x < 17; x += 1) {
        const point = transformPoint(transform, x + 0.5, y + 0.5);
        expected[y * 17 + x] =
          selection.coverageStore.read({
            x: Math.floor(point.x),
            y: Math.floor(point.y),
            width: 1,
            height: 1,
          })[0] ?? 0;
      }
    }
    expect(layer.rasterMask.coverageStore.toContiguous()).toEqual(expected);
  });

  it('bakes pure-translation world offsets into masks without densifying large selections', () => {
    const width = 4096;
    const height = 4096;
    // Selection is a small rect far from origin; layer is translated so local (0,0) hits it.
    const selection = createRectangularSelection(
      { width, height },
      { x: 2000, y: 1500, width: 32, height: 16 },
    );
    const selectionBytes = selection.coverageStore.allocatedByteLength;
    expect(selectionBytes).toBeLessThan(width * height);

    const document = createDocument({
      id: 'translated-mask',
      name: 'Translated mask',
      width,
      height,
      selection,
      layers: [
        createPixelLayer({
          id: 'pixel',
          name: 'Pixel',
          width: 64,
          height: 48,
          transform: { a: 1, b: 0, c: 0, d: 1, e: 2000, f: 1500 },
        }),
      ],
    });

    const masked = addLayerRasterMaskFromSelection('pixel').execute(document);
    const layer = findLayer(masked, 'pixel');
    if (layer?.kind !== 'pixel' || !layer.rasterMask) throw new Error('expected masked pixel');
    const maskBytes = layer.rasterMask.coverageStore.allocatedByteLength;
    expect(maskBytes).toBeGreaterThan(0);
    // Must not densify the 4096² selection or a full-canvas mask.
    expect(maskBytes).toBeLessThan((width * height) / 100);
    expect(maskBytes).toBeLessThanOrEqual(64 * 48);
    // Local (0,0) maps to document (2000,1500) under pure translation.
    expect(layer.rasterMask.coverageStore.read({ x: 0, y: 0, width: 32, height: 16 })).toEqual(
      new Uint8ClampedArray(32 * 16).fill(255),
    );
    expect(layer.rasterMask.coverageStore.read({ x: 40, y: 40, width: 1, height: 1 })[0]).toBe(0);
  });

  it('owns, validates, edits, and removes pixel-layer vector masks immutably', () => {
    const commands = [
      { type: 'move' as const, x: 0, y: 0 },
      { type: 'line' as const, x: 2, y: 0 },
      { type: 'line' as const, x: 1, y: 2 },
      { type: 'close' as const },
    ];
    const mask = createVectorMask(2, 2, commands);
    const original = createDocument({
      id: 'vector-mask',
      name: 'Vector mask',
      width: 2,
      height: 2,
      layers: [createPixelLayer({ id: 'pixels', name: 'Pixels', width: 2, height: 2 })],
    });
    const added = setLayerVectorMask('pixels', mask).execute(original);
    commands[1] = { type: 'line', x: 0, y: 0 };
    expect(findLayer(original, 'pixels')).toMatchObject({ vectorMask: null });
    expect(findLayer(added, 'pixels')).toMatchObject({
      vectorMask: { density: 1, enabled: true, inverted: false },
    });
    expect(
      (findLayer(added, 'pixels') as ReturnType<typeof createPixelLayer>).vectorMask?.commands[1],
    ).toEqual({ type: 'line', x: 2, y: 0 });

    const softened = setLayerVectorMaskDensity('pixels', 0.4).execute(added);
    const disabled = setLayerVectorMaskEnabled('pixels', false).execute(softened);
    const inverted = invertLayerVectorMask('pixels').execute(disabled);
    expect(findLayer(inverted, 'pixels')).toMatchObject({
      vectorMask: { density: 0.4, enabled: false, inverted: true },
    });
    expect(findLayer(setLayerVectorMask('pixels', null).execute(inverted), 'pixels')).toMatchObject(
      { vectorMask: null },
    );
    expect(() =>
      createVectorMask(3, 3, [
        { type: 'line', x: 1, y: 1 },
        { type: 'line', x: 2, y: 2 },
      ]),
    ).toThrowError('Each path subpath must begin with a move command');
    expect(() =>
      setLayerVectorMask('pixels', createVectorMask(3, 3)).execute(original),
    ).toThrowError('Vector mask dimensions do not match');
  });

  it('owns and edits vector masks across structured and adjustment layers', () => {
    const full2 = createVectorMask(2, 2);
    const original = createDocument({
      id: 'structured-vector-masks',
      name: 'Structured vector masks',
      width: 4,
      height: 4,
      layers: [
        createAdjustmentLayer({
          id: 'adjustment-mask',
          name: 'Adjustment',
          vectorMask: createVectorMask(4, 4),
        }),
        createShapeLayer({
          id: 'shape-mask',
          name: 'Shape',
          geometry: { type: 'rectangle', width: 2, height: 2, cornerRadius: 0 },
          vectorMask: full2,
        }),
        createTextLayer({
          id: 'text-mask',
          name: 'Text',
          width: 2,
          height: 2,
          vectorMask: full2,
        }),
        createSmartObjectLayer({
          id: 'smart-mask',
          name: 'Smart',
          source: {
            id: 'source',
            name: 'Source',
            canvas: { width: 2, height: 2 },
            layers: [],
          },
          vectorMask: full2,
        }),
      ],
    });
    const edited = setLayerVectorMaskDensity('shape-mask', 0.25).execute(original);
    expect(findLayer(edited, 'shape-mask')).toMatchObject({
      kind: 'shape',
      vectorMask: { density: 0.25 },
    });
    expect(findLayer(original, 'shape-mask')).toMatchObject({
      kind: 'shape',
      vectorMask: { density: 1 },
    });
    expect(() =>
      setLayerVectorMask('adjustment-mask', createVectorMask(2, 2)).execute(original),
    ).toThrow('Vector mask dimensions do not match the layer');
    const groupDocument = createDocument({
      id: 'group-mask',
      name: 'Group',
      width: 4,
      height: 4,
      layers: [createGroupLayer({ id: 'group', name: 'Group' })],
    });
    expect(
      findLayer(
        setLayerVectorMask('group', createVectorMask(4, 4)).execute(groupDocument),
        'group',
      ),
    ).toMatchObject({ kind: 'group', vectorMask: { width: 4, height: 4 } });
    expect(() =>
      setLayerVectorMask('group', createVectorMask(2, 2)).execute(groupDocument),
    ).toThrow('Vector mask dimensions do not match the layer');
    expect(() =>
      createDocument({
        id: 'bad-adjustment-mask',
        name: 'Bad',
        width: 4,
        height: 4,
        layers: [
          createAdjustmentLayer({
            id: 'bad',
            name: 'Bad',
            vectorMask: createVectorMask(2, 2),
          }),
        ],
      }),
    ).toThrow('Adjustment vector mask must match the canvas');
    expect(() =>
      createDocument({
        id: 'bad-group-mask',
        name: 'Bad group',
        width: 4,
        height: 4,
        layers: [
          createGroupLayer({
            id: 'bad-group',
            name: 'Bad group',
            vectorMask: createVectorMask(2, 2),
          }),
        ],
      }),
    ).toThrow('Group vector mask must match the canvas');

    const groupRasterMask = setLayerRasterMask(
      'group',
      createRasterMask(4, 4, new Uint8ClampedArray(16).fill(255)),
    ).execute(groupDocument);
    expect(findLayer(groupRasterMask, 'group')).toMatchObject({
      kind: 'group',
      rasterMask: { width: 4, height: 4 },
    });
    expect(() =>
      setLayerRasterMask('group', createRasterMask(2, 2)).execute(groupDocument),
    ).toThrow('Raster mask dimensions do not match the layer');
    expect(() =>
      createDocument({
        id: 'bad-group-raster-mask',
        name: 'Bad group raster mask',
        width: 4,
        height: 4,
        layers: [
          createGroupLayer({
            id: 'bad-raster-group',
            name: 'Bad raster group',
            rasterMask: createRasterMask(2, 2),
          }),
        ],
      }),
    ).toThrow('Group raster mask must match the canvas');
  });

  it('updates, inverts, disables, and removes raster masks immutably', () => {
    const document = createDocument({
      id: 'doc',
      name: 'Doc',
      width: 2,
      height: 1,
      layers: [
        createPixelLayer({
          id: 'pixel',
          name: 'Pixel',
          width: 2,
          height: 1,
          rasterMask: createRasterMask(2, 1, [0, 255]),
        }),
      ],
    });
    let next = setLayerMaskDensity('pixel', 0.5).execute(document);
    next = setLayerMaskEnabled('pixel', false).execute(next);
    next = invertLayerMask('pixel').execute(next);
    const mask = findLayer(next, 'pixel');
    expect(mask?.kind === 'pixel' ? mask.rasterMask : undefined).toMatchObject({
      density: 0.5,
      enabled: false,
    });
    expect(
      mask?.kind === 'pixel' ? mask.rasterMask?.coverageStore.toContiguous() : undefined,
    ).toEqual(new Uint8ClampedArray([255, 0]));
    expect(findLayer(document, 'pixel')).toMatchObject({
      rasterMask: { density: 1, enabled: true },
    });
    const originalMaskLayer = findLayer(document, 'pixel');
    expect(
      originalMaskLayer?.kind === 'pixel'
        ? originalMaskLayer.rasterMask?.coverageStore.toContiguous()
        : undefined,
    ).toEqual(new Uint8ClampedArray([0, 255]));
    next = setLayerRasterMask('pixel', null).execute(next);
    expect(findLayer(next, 'pixel')).toMatchObject({ rasterMask: null });
    expect(() => createRasterMask(2, 1, [255])).toThrow('coverage must match');
  });

  it('inverts a large sparse raster mask without contiguous intermediates', () => {
    const size = 4096;
    const coverageStore = TiledCoverageStore.empty(size, size).write({
      x: 2048,
      y: 1024,
      width: 1,
      height: 1,
      coverage: new Uint8ClampedArray([128]),
    }).store;
    const document = createDocument({
      id: 'large-mask-invert',
      name: 'Large mask invert',
      width: size,
      height: size,
      layers: [
        createPixelLayer({
          id: 'pixel',
          name: 'Pixel',
          width: size,
          height: size,
          rasterMask: createRasterMaskFromCoverageStore(size, size, coverageStore),
        }),
      ],
    });
    const originalLayer = findLayer(document, 'pixel');
    if (originalLayer?.kind !== 'pixel' || !originalLayer.rasterMask) {
      throw new Error('Expected raster mask fixture.');
    }
    const materialize = vi.spyOn(originalLayer.rasterMask.coverageStore, 'toContiguous');

    const inverted = invertLayerMask('pixel').execute(document);
    const invertedLayer = findLayer(inverted, 'pixel');
    if (invertedLayer?.kind !== 'pixel' || !invertedLayer.rasterMask) {
      throw new Error('Expected inverted raster mask.');
    }

    expect(materialize).not.toHaveBeenCalled();
    expect(originalLayer.rasterMask.coverageStore.allocatedByteLength).toBe(256 * 256);
    expect(invertedLayer.rasterMask.coverageStore).toMatchObject({
      defaultCoverage: 255,
      allocatedByteLength: 256 * 256,
    });
    expect(
      invertedLayer.rasterMask.coverageStore.read({ x: 2048, y: 1024, width: 1, height: 1 })[0],
    ).toBe(127);
    expect(
      invertedLayer.rasterMask.coverageStore.read({ x: 0, y: 0, width: 1, height: 1 })[0],
    ).toBe(255);
  });

  it('patches raster-mask coverage without mutating prior state or caller buffers', () => {
    const document = createDocument({
      id: 'doc',
      name: 'Doc',
      width: 3,
      height: 2,
      layers: [
        createPixelLayer({
          id: 'pixel',
          name: 'Pixel',
          width: 3,
          height: 2,
          rasterMask: createRasterMask(3, 2),
        }),
      ],
    });
    const coverage = new Uint8ClampedArray([0, 64]);
    const next = patchLayerRasterMask('pixel', {
      x: 1,
      y: 1,
      width: 2,
      height: 1,
      coverage,
    }).execute(document);
    coverage.fill(255);
    const nextLayer = findLayer(next, 'pixel');
    const originalLayer = findLayer(document, 'pixel');
    expect(
      nextLayer?.kind === 'pixel' ? nextLayer.rasterMask?.coverageStore.toContiguous() : undefined,
    ).toEqual(new Uint8ClampedArray([255, 255, 255, 255, 0, 64]));
    expect(
      originalLayer?.kind === 'pixel'
        ? originalLayer.rasterMask?.coverageStore.toContiguous()
        : undefined,
    ).toEqual(new Uint8ClampedArray(6).fill(255));
    expect(() =>
      patchLayerRasterMask('pixel', { x: 3, y: 0, width: 1, height: 1, coverage: [0] }).execute(
        document,
      ),
    ).toThrow('outside the layer bounds');
  });

  it('owns and validates saved alpha-channel coverage', () => {
    const coverage = new Uint8ClampedArray([0, 64, 128, 255]);
    const channel = createAlphaChannel({
      id: 'alpha-1',
      name: 'Subject',
      width: 2,
      height: 2,
      coverage,
    });
    const document = createDocument({
      id: 'doc',
      name: 'Doc',
      width: 2,
      height: 2,
      channels: [channel],
    });
    coverage.fill(255);
    channel.coverageStore.toContiguous().fill(0);

    expect(findAlphaChannel(document, 'alpha-1')?.coverageStore.toContiguous()).toEqual(
      new Uint8ClampedArray([0, 64, 128, 255]),
    );
    expect(findAlphaChannel(document, 'alpha-1')?.coverageStore.toContiguous()).toEqual(
      new Uint8ClampedArray([0, 64, 128, 255]),
    );
    expect(() =>
      createDocument({
        id: 'doc',
        name: 'Doc',
        width: 2,
        height: 2,
        channels: [
          createAlphaChannel({ id: 'duplicate', name: 'One', width: 2, height: 2 }),
          createAlphaChannel({ id: 'duplicate', name: 'Two', width: 2, height: 2 }),
        ],
      }),
    ).toThrow('Duplicate alpha channel id');
    expect(() =>
      createDocument({
        id: 'doc',
        name: 'Doc',
        width: 2,
        height: 2,
        channels: [createAlphaChannel({ id: 'wrong-size', name: 'Wrong', width: 1, height: 1 })],
      }),
    ).toThrow('must match the document canvas');
    expect(() =>
      createAlphaChannel({ id: 'bad', name: 'Bad', width: 2, height: 2, coverage: [1] }),
    ).toThrow('must contain 4 values');
  });

  it('saves and loads exact soft selections without aliasing mutable coverage', () => {
    const source = new Uint8ClampedArray([0, 63, 127, 255]);
    const original = createDocument({
      id: 'doc',
      name: 'Doc',
      width: 2,
      height: 2,
      selection: createSelectionMask({ width: 2, height: 2, coverage: source }),
    });
    const saved = addAlphaChannelFromSelection('soft', 'Soft edge').execute(original);
    const loaded = loadAlphaChannelSelection('soft').execute(selectAll().execute(saved));
    source.fill(255);

    expect(original.channels).toEqual([]);
    expect(findAlphaChannel(saved, 'soft')?.coverageStore.toContiguous()).toEqual(
      new Uint8ClampedArray([0, 63, 127, 255]),
    );
    expect(findAlphaChannel(saved, 'soft')?.coverageStore.toContiguous()).toEqual(
      new Uint8ClampedArray([0, 63, 127, 255]),
    );
    expect(loaded.selection?.coverageStore.toContiguous()).toEqual(
      new Uint8ClampedArray([0, 63, 127, 255]),
    );
    expect(loaded.selection?.coverageStore.toContiguous()).toEqual(
      new Uint8ClampedArray([0, 63, 127, 255]),
    );
    loaded.selection?.coverageStore.toContiguous().fill(0);
    expect(findAlphaChannel(saved, 'soft')?.coverageStore.toContiguous()).toEqual(
      new Uint8ClampedArray([0, 63, 127, 255]),
    );
  });

  it('saves null selection as full coverage and renames and removes channels immutably', () => {
    const original = createDocument({ id: 'doc', name: 'Doc', width: 2, height: 1 });
    const saved = addAlphaChannelFromSelection('all', 'Everything').execute(original);
    const renamed = renameAlphaChannel('all', 'Entire canvas').execute(saved);
    const removed = removeAlphaChannel('all').execute(renamed);

    expect(findAlphaChannel(saved, 'all')).toMatchObject({ name: 'Everything' });
    expect(findAlphaChannel(renamed, 'all')?.name).toBe('Entire canvas');
    expect(findAlphaChannel(saved, 'all')?.name).toBe('Everything');
    expect(removed.channels).toEqual([]);
    expect(() => addAlphaChannelFromSelection('all', 'Duplicate').execute(saved)).toThrow(
      'Duplicate alpha channel id',
    );
    expect(() => renameAlphaChannel('missing', 'Name').execute(saved)).toThrow(
      'Alpha channel not found',
    );
    expect(() => removeAlphaChannel('missing').execute(saved)).toThrow('Alpha channel not found');
    expect(() => loadAlphaChannelSelection('missing').execute(saved)).toThrow(
      'Alpha channel not found',
    );
  });
});

describe('professional layer properties', () => {
  it('creates owned layer defaults and supports compositing properties immutably', () => {
    const layer = createPixelLayer({ id: 'pixels', name: 'Pixels', width: 1, height: 1 });
    const original = createDocument({
      id: 'doc',
      name: 'Doc',
      width: 1,
      height: 1,
      layers: [layer],
    });
    expect(layer).toMatchObject({
      blendMode: 'normal',
      fillOpacity: 1,
      clipping: false,
      locks: { transparency: false, pixels: false, position: false, all: false },
    });
    const blended = setLayerBlendMode('pixels', 'luminosity').execute(original);
    const filled = setLayerFillOpacity('pixels', 0.35).execute(blended);
    const clipped = setLayerClipping('pixels', true).execute(filled);
    expect(findLayer(clipped, 'pixels')).toMatchObject({
      blendMode: 'luminosity',
      fillOpacity: 0.35,
      clipping: true,
    });
    expect(findLayer(original, 'pixels')).toMatchObject({
      blendMode: 'normal',
      fillOpacity: 1,
      clipping: false,
    });
    expect(() => setLayerBlendMode('pixels', 'bogus' as 'normal')).toThrow('Unsupported blend');
    expect(() => setLayerFillOpacity('pixels', 1.1)).toThrow('between 0 and 1');
  });

  it('enforces pixel, transparency, position, and complete locks', () => {
    const original = createDocument({
      id: 'doc',
      name: 'Doc',
      width: 1,
      height: 1,
      layers: [
        createPixelLayer({
          id: 'pixels',
          name: 'Pixels',
          width: 1,
          height: 1,
          pixels: [10, 20, 30, 40],
        }),
      ],
    });
    const transparencyLocked = setLayerLock('pixels', 'transparency', true).execute(original);
    const painted = patchPixels('pixels', {
      x: 0,
      y: 0,
      width: 1,
      height: 1,
      pixels: [100, 110, 120, 250],
    }).execute(transparencyLocked);
    const paintedLayer = findLayer(painted, 'pixels');
    expect(
      paintedLayer?.kind === 'pixel' ? [...paintedLayer.pixelStore.toContiguous()] : [],
    ).toEqual([100, 110, 120, 40]);

    const pixelLocked = setLayerLock('pixels', 'pixels', true).execute(original);
    expect(() =>
      patchPixels('pixels', { x: 0, y: 0, width: 1, height: 1, pixels: [0, 0, 0, 0] }).execute(
        pixelLocked,
      ),
    ).toThrow('blocked by the layer lock');
    const positionLocked = setLayerLock('pixels', 'position', true).execute(original);
    expect(() => setLayerTranslation('pixels', 2, 3).execute(positionLocked)).toThrow(
      'blocked by the layer lock',
    );
    const fullyLocked = setLayerLocks('pixels', {
      transparency: false,
      pixels: false,
      position: false,
      all: true,
    }).execute(original);
    expect(() => renameLayer('pixels', 'Nope').execute(fullyLocked)).toThrow(
      'blocked by the layer lock',
    );
  });

  it('owns typed adjustment parameters and validates adjustment masks and groups', () => {
    const source = { type: 'brightness-contrast' as const, brightness: 12, contrast: -8 };
    const adjustment = createAdjustmentLayer({
      id: 'adjustment',
      name: 'Tone',
      adjustment: source,
      rasterMask: createRasterMask(2, 1, [255, 64]),
    });
    const group = createGroupLayer({
      id: 'group',
      name: 'Group',
      children: [adjustment],
      compositing: 'isolated',
    });
    const document = createDocument({
      id: 'doc',
      name: 'Doc',
      width: 2,
      height: 1,
      layers: [group],
    });
    const edited = setAdjustmentLayerParameters('adjustment', { type: 'invert' }).execute(document);
    const passed = setGroupCompositing('group', 'pass-through').execute(edited);
    expect(findLayer(document, 'adjustment')).toMatchObject({ adjustment: source });
    expect(findLayer(passed, 'adjustment')).toMatchObject({ adjustment: { type: 'invert' } });
    expect(findLayer(passed, 'group')).toMatchObject({ compositing: 'pass-through' });
    expect(() =>
      createDocument({
        id: 'bad',
        name: 'Bad',
        width: 1,
        height: 1,
        layers: [adjustment],
      }),
    ).toThrow('Adjustment mask must match the canvas');
    expect(() =>
      createAdjustmentLayer({
        id: 'bad-levels',
        name: 'Bad levels',
        adjustment: {
          type: 'levels',
          inputBlack: 200,
          inputWhite: 100,
          gamma: 1,
          outputBlack: 0,
          outputWhite: 255,
        },
      }),
    ).toThrow('out of range');
  });
});

describe('professional layer-tree operations', () => {
  it('duplicates pixel, adjustment, and nested group resources without aliasing', () => {
    const group = createGroupLayer({
      id: 'group',
      name: 'Group',
      children: [
        createPixelLayer({
          id: 'pixels',
          name: 'Pixels',
          width: 1,
          height: 1,
          pixels: [1, 2, 3, 4],
          rasterMask: createRasterMask(1, 1, [55]),
          vectorMask: createVectorMask(1, 1),
        }),
        createAdjustmentLayer({
          id: 'tone',
          name: 'Tone',
          adjustment: { type: 'invert' },
        }),
      ],
    });
    const original = createDocument({
      id: 'doc',
      name: 'Doc',
      width: 1,
      height: 1,
      layers: [group],
    });
    const duplicated = duplicateLayer('group', 'group-copy').execute(original);
    const copy = findLayer(duplicated, 'group-copy');
    expect(copy).toMatchObject({ kind: 'group', name: 'Group copy' });
    expect(findLayer(duplicated, 'group-copy:pixels')).toMatchObject({ kind: 'pixel' });
    expect(findLayer(duplicated, 'group-copy:tone')).toMatchObject({
      kind: 'adjustment',
      adjustment: { type: 'invert' },
    });
    const copiedPixels = findLayer(duplicated, 'group-copy:pixels');
    if (copiedPixels?.kind !== 'pixel') throw new Error('Expected copied pixels');
    const originalPixels = findLayer(original, 'pixels');
    if (originalPixels?.kind !== 'pixel') throw new Error('Expected original pixels');
    expect(copiedPixels.vectorMask).toEqual(originalPixels.vectorMask);
    expect(copiedPixels.vectorMask).not.toBe(originalPixels.vectorMask);
    expect(copiedPixels.vectorMask?.commands).not.toBe(originalPixels.vectorMask?.commands);
    const editedDuplicate = patchPixels('group-copy:pixels', {
      x: 0,
      y: 0,
      width: 1,
      height: 1,
      pixels: [200, 2, 3, 4],
    }).execute(duplicated);
    expect(
      findLayer(original, 'pixels')?.kind === 'pixel'
        ? findLayer(original, 'pixels')?.pixelStore.toContiguous()
        : undefined,
    ).toEqual(new Uint8ClampedArray([1, 2, 3, 4]));
    expect(
      findLayer(editedDuplicate, 'group-copy:pixels')?.kind === 'pixel'
        ? findLayer(editedDuplicate, 'group-copy:pixels')?.pixelStore.toContiguous()
        : undefined,
    ).toEqual(new Uint8ClampedArray([200, 2, 3, 4]));
    expect(
      findLayer(original, 'pixels')?.kind === 'pixel'
        ? findLayer(original, 'pixels')?.rasterMask?.coverageStore.toContiguous()
        : undefined,
    ).toEqual(new Uint8ClampedArray([55]));
  });

  it('groups sibling layers in stack order and rejects cross-parent selections', () => {
    const original = createDocument({
      id: 'doc',
      name: 'Doc',
      width: 1,
      height: 1,
      layers: [
        pixel('bottom'),
        pixel('middle'),
        createGroupLayer({ id: 'existing', name: 'Existing', children: [pixel('nested')] }),
        pixel('top'),
      ],
    });
    const grouped = groupLayers(['middle', 'top'], { id: 'new-group', name: 'New group' }).execute(
      original,
    );
    expect(grouped.layers.map((layer) => layer.id)).toEqual(['bottom', 'new-group', 'existing']);
    expect(findLayer(grouped, 'new-group')).toMatchObject({
      kind: 'group',
      children: [{ id: 'middle' }, { id: 'top' }],
    });
    expect(() =>
      groupLayers(['bottom', 'nested'], { id: 'bad', name: 'Bad' }).execute(original),
    ).toThrow('same parent');
  });

  it('ungroups pass-through groups while composing transforms and opacity', () => {
    const original = createDocument({
      id: 'doc',
      name: 'Doc',
      width: 1,
      height: 1,
      layers: [
        createGroupLayer({
          id: 'group',
          name: 'Group',
          opacity: 0.5,
          transform: { a: 2, b: 0, c: 0, d: 2, e: 4, f: 6 },
          children: [
            createPixelLayer({
              id: 'child',
              name: 'Child',
              width: 1,
              height: 1,
              opacity: 0.8,
              transform: { a: 1, b: 0, c: 0, d: 1, e: 3, f: 5 },
            }),
          ],
        }),
      ],
    });
    const ungrouped = ungroupLayer('group').execute(original);
    expect(ungrouped.layers).toHaveLength(1);
    expect(ungrouped.layers[0]).toMatchObject({
      id: 'child',
      opacity: 0.4,
      transform: { a: 2, b: 0, c: 0, d: 2, e: 10, f: 16 },
    });
    const isolated = createDocument({
      id: 'isolated-doc',
      name: 'Isolated',
      width: 1,
      height: 1,
      layers: [createGroupLayer({ id: 'isolated', name: 'Isolated', compositing: 'isolated' })],
    });
    expect(() => ungroupLayer('isolated').execute(isolated)).toThrow('without changing pixels');
    const masked = createDocument({
      id: 'masked-group-doc',
      name: 'Masked group',
      width: 4,
      height: 4,
      layers: [
        createGroupLayer({
          id: 'masked-group',
          name: 'Masked group',
          vectorMask: createVectorMask(4, 4),
        }),
      ],
    });
    expect(() => ungroupLayer('masked-group').execute(masked)).toThrow('without changing pixels');
    const rasterMasked = createDocument({
      id: 'raster-masked-group-doc',
      name: 'Raster masked group',
      width: 4,
      height: 4,
      layers: [
        createGroupLayer({
          id: 'raster-masked-group',
          name: 'Raster masked group',
          rasterMask: createRasterMask(4, 4),
        }),
      ],
    });
    expect(() => ungroupLayer('raster-masked-group').execute(rasterMasked)).toThrow(
      'without changing pixels',
    );
  });

  it('replaces root siblings at the lowest stack position as one transaction', () => {
    const original = createDocument({
      id: 'doc',
      name: 'Doc',
      width: 1,
      height: 1,
      layers: [pixel('bottom'), pixel('middle'), pixel('top')],
    });
    const merged = createPixelLayer({
      id: 'merged',
      name: 'Merged',
      width: 1,
      height: 1,
      pixels: [1, 2, 3, 255],
    });
    const command = replaceRootLayers(['bottom', 'top'], merged);
    const replaced = command.execute(original);
    expect(command.label).toBe('Merge layers');
    expect(replaced.layers.map((layer) => layer.id)).toEqual(['merged', 'middle']);
    expect(original.layers.map((layer) => layer.id)).toEqual(['bottom', 'middle', 'top']);
    expect(
      findLayer(replaced, 'merged')?.kind === 'pixel'
        ? findLayer(replaced, 'merged')?.pixelStore.toContiguous()
        : undefined,
    ).toEqual(new Uint8ClampedArray([1, 2, 3, 255]));
    expect(() =>
      replaceRootLayers(['bottom', 'nested'], merged).execute(
        createDocument({
          id: 'nested-doc',
          name: 'Nested',
          width: 1,
          height: 1,
          layers: [
            pixel('bottom'),
            createGroupLayer({ id: 'group', name: 'Group', children: [pixel('nested')] }),
          ],
        }),
      ),
    ).toThrow('root siblings');
  });
});

describe('document guides', () => {
  it('adds, removes, and clears guides immutably', () => {
    const document = createDocument({
      id: 'g',
      name: 'Guides',
      width: 100,
      height: 80,
      layers: [pixel('bg')],
    });
    const withVertical = addDocumentGuide({
      id: 'v1',
      direction: 'vertical',
      position: 40,
    }).execute(document);
    expect(withVertical.guides).toEqual([{ id: 'v1', direction: 'vertical', position: 40 }]);
    expect(document.guides).toEqual([]);

    const withBoth = addDocumentGuide({
      id: 'h1',
      direction: 'horizontal',
      position: 20,
    }).execute(withVertical);
    expect(withBoth.guides).toHaveLength(2);

    const removed = removeDocumentGuide('v1').execute(withBoth);
    expect(removed.guides).toEqual([{ id: 'h1', direction: 'horizontal', position: 20 }]);

    const cleared = clearDocumentGuides().execute(withBoth);
    expect(cleared.guides).toEqual([]);
    expect(withBoth.guides).toHaveLength(2);

    const moved = moveDocumentGuide('h1', 55).execute(withBoth);
    expect(moved.guides.find((guide) => guide.id === 'h1')?.position).toBe(55);
    expect(withBoth.guides.find((guide) => guide.id === 'h1')?.position).toBe(20);
  });
});
