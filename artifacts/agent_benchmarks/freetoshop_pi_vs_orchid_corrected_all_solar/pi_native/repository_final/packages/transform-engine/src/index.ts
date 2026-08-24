import {
  identityLayerTransform,
  invertLayerTransform,
  multiplyLayerTransforms,
  transformPoint,
  validateLayerTransform,
  type LayerTransform,
  type PixelLayer,
  type TransformPoint,
} from '@freetoshop/core-document';

export type TransformHandleId =
  | 'nw'
  | 'n'
  | 'ne'
  | 'e'
  | 'se'
  | 's'
  | 'sw'
  | 'w'
  | 'center'
  | 'rotate';

export type ScaleHandleId = Exclude<TransformHandleId, 'center' | 'rotate'>;

export interface TransformHandleGeometry extends TransformPoint {
  readonly id: TransformHandleId;
  readonly kind: 'corner' | 'edge' | 'center' | 'rotate';
  readonly oppositeId: TransformHandleId;
}

export interface HandleGeometryOptions {
  /** Distance above the top edge in layer-local units. */
  readonly rotationOffset?: number;
}

export interface ScaleTransformOptions {
  /** Preserve the starting aspect ratio for corner handles. Ignored for edge handles. */
  readonly uniform?: boolean;
  /** Smallest accepted absolute scale factor on an affected axis. */
  readonly minAbsoluteScale?: number;
}

export interface FreeTransformSnapshot {
  readonly width: number;
  readonly height: number;
  readonly localTransform: LayerTransform;
  readonly parentWorldTransform: LayerTransform;
  readonly worldTransform: LayerTransform;
}

export class TransformGeometryError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'TransformGeometryError';
  }
}

const DEFAULT_ROTATION_OFFSET = 24;
const DEFAULT_MIN_ABSOLUTE_SCALE = 1e-4;
const CLEAN_EPSILON = 1e-12;

function assertFinite(value: number, label: string): void {
  if (!Number.isFinite(value)) throw new TransformGeometryError(`${label} must be finite`);
}

function assertDimensions(width: number, height: number): void {
  if (!Number.isFinite(width) || width <= 0) {
    throw new TransformGeometryError('Layer width must be positive and finite');
  }
  if (!Number.isFinite(height) || height <= 0) {
    throw new TransformGeometryError('Layer height must be positive and finite');
  }
}

function assertPoint(point: TransformPoint, label: string): void {
  assertFinite(point.x, `${label} x`);
  assertFinite(point.y, `${label} y`);
}

function copyMatrix(matrix: LayerTransform): LayerTransform {
  validateLayerTransform(matrix);
  return { ...matrix };
}

function clean(value: number): number {
  if (Math.abs(value) < CLEAN_EPSILON || Object.is(value, -0)) return 0;
  return value;
}

function cleanMatrix(matrix: LayerTransform): LayerTransform {
  const result = {
    a: clean(matrix.a),
    b: clean(matrix.b),
    c: clean(matrix.c),
    d: clean(matrix.d),
    e: clean(matrix.e),
    f: clean(matrix.f),
  };
  validateLayerTransform(result);
  return result;
}

function translation(x: number, y: number): LayerTransform {
  return { a: 1, b: 0, c: 0, d: 1, e: x, f: y };
}

function scaling(x: number, y: number): LayerTransform {
  return { a: x, b: 0, c: 0, d: y, e: 0, f: 0 };
}

function rotation(radians: number): LayerTransform {
  const cosine = Math.cos(radians);
  const sine = Math.sin(radians);
  return { a: cosine, b: sine, c: -sine, d: cosine, e: 0, f: 0 };
}

function aroundPoint(point: TransformPoint, operation: LayerTransform): LayerTransform {
  return multiplyLayerTransforms(
    translation(point.x, point.y),
    multiplyLayerTransforms(operation, translation(-point.x, -point.y)),
  );
}

function localFromWorld(
  snapshot: FreeTransformSnapshot,
  worldTransform: LayerTransform,
): LayerTransform {
  return cleanMatrix(
    multiplyLayerTransforms(invertLayerTransform(snapshot.parentWorldTransform), worldTransform),
  );
}

export function createFreeTransformSnapshot(
  layer: Pick<PixelLayer, 'width' | 'height' | 'transform'>,
  parentWorldTransform: LayerTransform = identityLayerTransform(),
): FreeTransformSnapshot {
  assertDimensions(layer.width, layer.height);
  const local = copyMatrix(layer.transform);
  const parent = copyMatrix(parentWorldTransform);
  return {
    width: layer.width,
    height: layer.height,
    localTransform: local,
    parentWorldTransform: parent,
    worldTransform: cleanMatrix(multiplyLayerTransforms(parent, local)),
  };
}

export function getLocalHandleGeometry(
  layer: Pick<PixelLayer, 'width' | 'height'> | Pick<FreeTransformSnapshot, 'width' | 'height'>,
  options: HandleGeometryOptions = {},
): readonly TransformHandleGeometry[] {
  assertDimensions(layer.width, layer.height);
  const offset = options.rotationOffset ?? DEFAULT_ROTATION_OFFSET;
  if (!Number.isFinite(offset) || offset <= 0) {
    throw new TransformGeometryError('Rotation handle offset must be positive and finite');
  }
  const halfWidth = layer.width / 2;
  const halfHeight = layer.height / 2;
  return [
    { id: 'nw', kind: 'corner', oppositeId: 'se', x: 0, y: 0 },
    { id: 'n', kind: 'edge', oppositeId: 's', x: halfWidth, y: 0 },
    { id: 'ne', kind: 'corner', oppositeId: 'sw', x: layer.width, y: 0 },
    { id: 'e', kind: 'edge', oppositeId: 'w', x: layer.width, y: halfHeight },
    { id: 'se', kind: 'corner', oppositeId: 'nw', x: layer.width, y: layer.height },
    { id: 's', kind: 'edge', oppositeId: 'n', x: halfWidth, y: layer.height },
    { id: 'sw', kind: 'corner', oppositeId: 'ne', x: 0, y: layer.height },
    { id: 'w', kind: 'edge', oppositeId: 'e', x: 0, y: halfHeight },
    { id: 'center', kind: 'center', oppositeId: 'center', x: halfWidth, y: halfHeight },
    { id: 'rotate', kind: 'rotate', oppositeId: 'center', x: halfWidth, y: -offset },
  ];
}

export function getDocumentHandleGeometry(
  snapshot: FreeTransformSnapshot,
  options: HandleGeometryOptions = {},
): readonly TransformHandleGeometry[] {
  validateSnapshot(snapshot);
  return getLocalHandleGeometry(snapshot, options).map((handle) => ({
    ...handle,
    ...transformPoint(snapshot.worldTransform, handle.x, handle.y),
  }));
}

function validateSnapshot(snapshot: FreeTransformSnapshot): void {
  assertDimensions(snapshot.width, snapshot.height);
  validateLayerTransform(snapshot.localTransform);
  validateLayerTransform(snapshot.parentWorldTransform);
  validateLayerTransform(snapshot.worldTransform);
}

function handleById(
  snapshot: FreeTransformSnapshot,
  id: TransformHandleId,
): TransformHandleGeometry {
  const handle = getLocalHandleGeometry(snapshot).find((candidate) => candidate.id === id);
  if (!handle) throw new TransformGeometryError(`Unknown transform handle: ${String(id)}`);
  return handle;
}

export function scaleTransformFromHandle(
  snapshot: FreeTransformSnapshot,
  handleId: ScaleHandleId,
  pointer: TransformPoint,
  options: ScaleTransformOptions = {},
): LayerTransform {
  validateSnapshot(snapshot);
  assertPoint(pointer, 'Scale pointer');
  const handle = handleById(snapshot, handleId);
  const anchor = handleById(snapshot, handle.oppositeId);
  const localPointer = transformPoint(
    invertLayerTransform(snapshot.worldTransform),
    pointer.x,
    pointer.y,
  );
  const startX = handle.x - anchor.x;
  const startY = handle.y - anchor.y;
  const currentX = localPointer.x - anchor.x;
  const currentY = localPointer.y - anchor.y;

  let scaleX =
    handle.kind === 'corner' || handleId === 'e' || handleId === 'w' ? currentX / startX : 1;
  let scaleY =
    handle.kind === 'corner' || handleId === 'n' || handleId === 's' ? currentY / startY : 1;

  if (handle.kind === 'corner' && options.uniform) {
    const denominator = startX * startX + startY * startY;
    const uniformScale = (currentX * startX + currentY * startY) / denominator;
    scaleX = uniformScale;
    scaleY = uniformScale;
  }

  const minimum = options.minAbsoluteScale ?? DEFAULT_MIN_ABSOLUTE_SCALE;
  if (!Number.isFinite(minimum) || minimum <= 0) {
    throw new TransformGeometryError('Minimum absolute scale must be positive and finite');
  }
  if (!Number.isFinite(scaleX) || !Number.isFinite(scaleY)) {
    throw new TransformGeometryError('Scale must be finite');
  }
  if (Math.abs(scaleX) < minimum || Math.abs(scaleY) < minimum) {
    throw new TransformGeometryError('Scale is too small');
  }

  const localScale = aroundPoint(anchor, scaling(scaleX, scaleY));
  return cleanMatrix(multiplyLayerTransforms(snapshot.localTransform, localScale));
}

export function rotateTransformFromPointers(
  snapshot: FreeTransformSnapshot,
  startPointer: TransformPoint,
  currentPointer: TransformPoint,
): LayerTransform {
  validateSnapshot(snapshot);
  assertPoint(startPointer, 'Rotation start pointer');
  assertPoint(currentPointer, 'Rotation current pointer');
  const center = transformPoint(snapshot.worldTransform, snapshot.width / 2, snapshot.height / 2);
  const startX = startPointer.x - center.x;
  const startY = startPointer.y - center.y;
  const currentX = currentPointer.x - center.x;
  const currentY = currentPointer.y - center.y;
  if (Math.hypot(startX, startY) <= CLEAN_EPSILON) {
    throw new TransformGeometryError('Rotation start pointer must not equal the transform center');
  }
  if (Math.hypot(currentX, currentY) <= CLEAN_EPSILON) {
    throw new TransformGeometryError('Rotation pointer must not equal the transform center');
  }
  const radians = Math.atan2(currentY, currentX) - Math.atan2(startY, startX);
  const worldRotation = aroundPoint(center, rotation(radians));
  const nextWorld = multiplyLayerTransforms(worldRotation, snapshot.worldTransform);
  return localFromWorld(snapshot, nextWorld);
}

export function translateTransformFromPointers(
  snapshot: FreeTransformSnapshot,
  startPointer: TransformPoint,
  currentPointer: TransformPoint,
): LayerTransform {
  validateSnapshot(snapshot);
  assertPoint(startPointer, 'Translation start pointer');
  assertPoint(currentPointer, 'Translation current pointer');
  const worldTranslation = translation(
    currentPointer.x - startPointer.x,
    currentPointer.y - startPointer.y,
  );
  return localFromWorld(
    snapshot,
    multiplyLayerTransforms(worldTranslation, snapshot.worldTransform),
  );
}
