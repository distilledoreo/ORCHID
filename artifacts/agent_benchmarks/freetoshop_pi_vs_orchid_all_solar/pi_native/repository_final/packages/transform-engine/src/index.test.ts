import {
  createPixelLayer,
  identityLayerTransform,
  multiplyLayerTransforms,
  transformPoint,
  type LayerTransform,
  type TransformPoint,
} from '@freetoshop/core-document';
import { describe, expect, it } from 'vitest';
import {
  TransformGeometryError,
  createFreeTransformSnapshot,
  getDocumentHandleGeometry,
  getLocalHandleGeometry,
  rotateTransformFromPointers,
  scaleTransformFromHandle,
  translateTransformFromPointers,
} from './index.js';

const matrix = (overrides: Partial<LayerTransform> = {}): LayerTransform => ({
  ...identityLayerTransform(),
  ...overrides,
});

const layer = (width = 10, height = 8, transform = identityLayerTransform()) =>
  createPixelLayer({ id: 'pixel', name: 'Pixel', width, height, transform });

function point(transform: LayerTransform, local: TransformPoint): TransformPoint {
  return transformPoint(transform, local.x, local.y);
}

function expectPoint(actual: TransformPoint, expected: TransformPoint): void {
  expect(actual.x).toBeCloseTo(expected.x, 10);
  expect(actual.y).toBeCloseTo(expected.y, 10);
}

function expectMatrix(actual: LayerTransform, expected: LayerTransform): void {
  expect(actual.a).toBeCloseTo(expected.a, 10);
  expect(actual.b).toBeCloseTo(expected.b, 10);
  expect(actual.c).toBeCloseTo(expected.c, 10);
  expect(actual.d).toBeCloseTo(expected.d, 10);
  expect(actual.e).toBeCloseTo(expected.e, 10);
  expect(actual.f).toBeCloseTo(expected.f, 10);
}

function required<T>(value: T | undefined): T {
  expect(value).toBeDefined();
  if (value === undefined) throw new Error('Expected test value to be defined');
  return value;
}

describe('free-transform handle geometry', () => {
  it('defines stable local corner, edge, center, and rotation handles', () => {
    expect(getLocalHandleGeometry(layer(10, 8), { rotationOffset: 12 })).toEqual([
      { id: 'nw', kind: 'corner', oppositeId: 'se', x: 0, y: 0 },
      { id: 'n', kind: 'edge', oppositeId: 's', x: 5, y: 0 },
      { id: 'ne', kind: 'corner', oppositeId: 'sw', x: 10, y: 0 },
      { id: 'e', kind: 'edge', oppositeId: 'w', x: 10, y: 4 },
      { id: 'se', kind: 'corner', oppositeId: 'nw', x: 10, y: 8 },
      { id: 's', kind: 'edge', oppositeId: 'n', x: 5, y: 8 },
      { id: 'sw', kind: 'corner', oppositeId: 'ne', x: 0, y: 8 },
      { id: 'w', kind: 'edge', oppositeId: 'e', x: 0, y: 4 },
      { id: 'center', kind: 'center', oppositeId: 'center', x: 5, y: 4 },
      { id: 'rotate', kind: 'rotate', oppositeId: 'center', x: 5, y: -12 },
    ]);
  });

  it('maps every handle through the supplied world transform', () => {
    const snapshot = createFreeTransformSnapshot(
      layer(10, 8, matrix({ a: 0, b: 2, c: -3, d: 0, e: 7, f: 11 })),
    );
    const handles = getDocumentHandleGeometry(snapshot, { rotationOffset: 12 });
    expectPoint(required(handles.find(({ id }) => id === 'nw')), { x: 7, y: 11 });
    expectPoint(required(handles.find(({ id }) => id === 'se')), { x: -17, y: 31 });
    expectPoint(required(handles.find(({ id }) => id === 'center')), { x: -5, y: 21 });
    expectPoint(required(handles.find(({ id }) => id === 'rotate')), { x: 43, y: 21 });
  });

  it('composes a nested parent before the layer-local transform', () => {
    const parent = matrix({ a: 2, d: 3, e: 100, f: 200 });
    const local = matrix({ e: 5, f: 7 });
    const snapshot = createFreeTransformSnapshot(layer(10, 8, local), parent);
    expect(snapshot.worldTransform).toEqual({ a: 2, b: 0, c: 0, d: 3, e: 110, f: 221 });
    expectPoint(required(getDocumentHandleGeometry(snapshot)[0]), { x: 110, y: 221 });
  });
});

describe('scaleTransformFromHandle', () => {
  it('scales a corner in both local axes and holds its opposite corner fixed', () => {
    const snapshot = createFreeTransformSnapshot(layer(10, 8));
    const next = scaleTransformFromHandle(snapshot, 'se', { x: 20, y: 24 });
    expect(next).toEqual({ a: 2, b: 0, c: 0, d: 3, e: 0, f: 0 });
    expectPoint(point(next, { x: 0, y: 0 }), { x: 0, y: 0 });
    expectPoint(point(next, { x: 10, y: 8 }), { x: 20, y: 24 });
  });

  it('scales an edge on one axis even when uniform scaling is requested', () => {
    const snapshot = createFreeTransformSnapshot(layer(10, 8));
    const next = scaleTransformFromHandle(snapshot, 'e', { x: 25, y: 100 }, { uniform: true });
    expect(next).toEqual({ a: 2.5, b: 0, c: 0, d: 1, e: 0, f: 0 });
    expectPoint(point(next, { x: 0, y: 4 }), { x: 0, y: 4 });
  });

  it('preserves aspect ratio for constrained corner scaling', () => {
    const snapshot = createFreeTransformSnapshot(layer(10, 20));
    const next = scaleTransformFromHandle(snapshot, 'se', { x: 30, y: 40 }, { uniform: true });
    expectMatrix(next, matrix({ a: 2.2, d: 2.2 }));
  });

  it('preserves reflections when a handle crosses its opposite anchor', () => {
    const snapshot = createFreeTransformSnapshot(layer(10, 8));
    const next = scaleTransformFromHandle(snapshot, 'e', { x: -10, y: 4 });
    expect(next).toEqual({ a: -1, b: 0, c: 0, d: 1, e: 0, f: 0 });
    expectPoint(point(next, { x: 0, y: 4 }), { x: 0, y: 4 });
    expectPoint(point(next, { x: 10, y: 4 }), { x: -10, y: 4 });
  });

  it('uses the immutable start world for pre-transformed nested layers', () => {
    const parent = matrix({ a: 2, d: 2, e: 100, f: 50 });
    const local = matrix({ a: 0, b: 1, c: -1, d: 0, e: 10, f: 20 });
    const snapshot = createFreeTransformSnapshot(layer(10, 8, local), parent);
    const anchorBefore = point(snapshot.worldTransform, { x: 0, y: 0 });
    const desiredHandle = point(snapshot.worldTransform, { x: 20, y: 24 });
    const first = scaleTransformFromHandle(snapshot, 'se', desiredHandle);
    const second = scaleTransformFromHandle(snapshot, 'se', desiredHandle);
    const nextWorld = multiplyLayerTransforms(parent, first);

    expect(first).toEqual(second);
    expectPoint(point(nextWorld, { x: 0, y: 0 }), anchorBefore);
    expectPoint(point(nextWorld, { x: 10, y: 8 }), desiredHandle);
  });

  it('holds the correct opposite anchor for every scale handle', () => {
    const snapshot = createFreeTransformSnapshot(
      layer(12, 6, matrix({ a: 1.5, b: 0.5, c: -0.25, d: 2, e: 7, f: -3 })),
    );
    for (const handle of getLocalHandleGeometry(snapshot).filter(
      ({ kind }) => kind === 'corner' || kind === 'edge',
    )) {
      const opposite = required(
        getLocalHandleGeometry(snapshot).find(({ id }) => id === handle.oppositeId),
      );
      const targetLocal = {
        x: opposite.x + (handle.x - opposite.x) * 1.7,
        y: opposite.y + (handle.y - opposite.y) * 1.4,
      };
      const pointer = point(snapshot.worldTransform, targetLocal);
      const next = scaleTransformFromHandle(
        snapshot,
        handle.id as Parameters<typeof scaleTransformFromHandle>[1],
        pointer,
      );
      expectPoint(point(next, opposite), point(snapshot.localTransform, opposite));
    }
  });
});

describe('rotation and translation', () => {
  it('rotates about the local center using document-space pointer angles', () => {
    const snapshot = createFreeTransformSnapshot(layer(10, 10));
    const next = rotateTransformFromPointers(snapshot, { x: 5, y: -5 }, { x: 15, y: 5 });
    expectMatrix(next, { a: 0, b: 1, c: -1, d: 0, e: 10, f: 0 });
    expectPoint(point(next, { x: 5, y: 5 }), { x: 5, y: 5 });
  });

  it('rotates nested and already transformed layers rigidly in document space', () => {
    const parent = matrix({ a: 2, d: 3, e: 100, f: -20 });
    const local = matrix({ a: 1.5, b: 0.25, c: 0.4, d: 0.75, e: 8, f: 5 });
    const snapshot = createFreeTransformSnapshot(layer(10, 8, local), parent);
    const center = point(snapshot.worldTransform, { x: 5, y: 4 });
    const next = rotateTransformFromPointers(
      snapshot,
      { x: center.x + 20, y: center.y },
      { x: center.x, y: center.y + 20 },
    );
    const nextWorld = multiplyLayerTransforms(parent, next);
    expectPoint(point(nextWorld, { x: 5, y: 4 }), center);
    const before = point(snapshot.worldTransform, { x: 10, y: 4 });
    const after = point(nextWorld, { x: 10, y: 4 });
    expectPoint(after, {
      x: center.x - (before.y - center.y),
      y: center.y + (before.x - center.x),
    });
  });

  it('translates in document space through a nested parent transform', () => {
    const parent = matrix({ a: 2, d: 4, e: 100, f: 200 });
    const snapshot = createFreeTransformSnapshot(layer(10, 8, matrix({ e: 3, f: 5 })), parent);
    const next = translateTransformFromPointers(snapshot, { x: 1, y: 2 }, { x: 21, y: -6 });
    expectMatrix(next, matrix({ e: 13, f: 3 }));
  });
});

describe('validation and immutability', () => {
  it('copies start matrices and never mutates the layer, parent, or snapshot', () => {
    const local = matrix({ e: 5, f: 6 });
    const parent = matrix({ a: 2, d: 2 });
    const source = layer(10, 8, local);
    const snapshot = createFreeTransformSnapshot(source, parent);
    const before = structuredClone(snapshot);
    const next = translateTransformFromPointers(snapshot, { x: 0, y: 0 }, { x: 8, y: 4 });

    expect(snapshot).toEqual(before);
    expect(source.transform).toEqual(local);
    expect(parent).toEqual(matrix({ a: 2, d: 2 }));
    expect(next).not.toBe(snapshot.localTransform);
  });

  it('rejects invalid dimensions, matrices, points, and rotation radii', () => {
    expect(() => createFreeTransformSnapshot({ ...layer(), width: 0 })).toThrow(
      TransformGeometryError,
    );
    expect(() => createFreeTransformSnapshot(layer(), matrix({ a: 1, b: 2, c: 2, d: 4 }))).toThrow(
      'must be invertible',
    );
    const snapshot = createFreeTransformSnapshot(layer(10, 8));
    expect(() => scaleTransformFromHandle(snapshot, 'se', { x: Number.NaN, y: 1 })).toThrow(
      'must be finite',
    );
    expect(() => rotateTransformFromPointers(snapshot, { x: 5, y: 4 }, { x: 6, y: 4 })).toThrow(
      'must not equal',
    );
  });

  it('rejects singular and minuscule scale gestures but accepts a configured threshold', () => {
    const snapshot = createFreeTransformSnapshot(layer(10, 8));
    expect(() => scaleTransformFromHandle(snapshot, 'se', { x: 0, y: 0 })).toThrow(
      'Scale is too small',
    );
    expect(() => scaleTransformFromHandle(snapshot, 'e', { x: 0.00001, y: 4 })).toThrow(
      'Scale is too small',
    );
    expect(
      scaleTransformFromHandle(snapshot, 'e', { x: 0.00001, y: 4 }, { minAbsoluteScale: 1e-7 }).a,
    ).toBeCloseTo(0.000001, 12);
  });
});
