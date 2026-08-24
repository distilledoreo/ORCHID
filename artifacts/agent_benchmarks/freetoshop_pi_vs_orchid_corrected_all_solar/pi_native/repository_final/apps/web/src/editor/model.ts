import {
  createDocument,
  createPixelLayer,
  identityLayerTransform,
  multiplyLayerTransforms,
  type ImageDocument,
  type Layer,
  type LayerTransform,
  type PathCommand,
  type RgbaColor,
  type ShapeGeometry,
} from '@freetoshop/core-document';
import {
  rotateTransformFromPointers,
  scaleTransformFromHandle,
  translateTransformFromPointers,
} from '@freetoshop/transform-engine';
import type {
  DocumentPoint,
  DocumentRectangle,
  Gesture,
  PathHandleRole,
  TransformDraft,
} from './types.js';

export const NEW_DOCUMENT_SIZE = { width: 1024, height: 768 } as const;
export const MAX_DOCUMENT_DIMENSION = 32_768;
export const MAX_DOCUMENT_PIXELS = 100_000_000;

export function makeId(prefix: string): string {
  return `${prefix}-${crypto.randomUUID()}`;
}

export function colorToHex(color: RgbaColor): string {
  return `#${[color.red, color.green, color.blue]
    .map((channel) => channel.toString(16).padStart(2, '0'))
    .join('')}`;
}

export function hexToColor(value: string, alpha = 255): RgbaColor {
  const hex = value.replace('#', '');
  return {
    red: Number.parseInt(hex.slice(0, 2), 16),
    green: Number.parseInt(hex.slice(2, 4), 16),
    blue: Number.parseInt(hex.slice(4, 6), 16),
    alpha,
  };
}

export function pathCommandAnchor(command: PathCommand): DocumentPoint | null {
  return command.type === 'close' ? null : { x: command.x, y: command.y };
}

export function movePathHandle(
  geometry: ShapeGeometry,
  commandIndex: number,
  role: PathHandleRole,
  point: DocumentPoint,
): ShapeGeometry {
  const command = geometry.commands[commandIndex];
  if (!command || command.type === 'close') return geometry;
  const commands = geometry.commands.map((candidate) => ({ ...candidate })) as PathCommand[];
  const owned = commands[commandIndex];
  if (!owned || owned.type === 'close') return geometry;
  if (role === 'anchor') {
    const deltaX = point.x - owned.x;
    const deltaY = point.y - owned.y;
    commands[commandIndex] =
      owned.type === 'cubic'
        ? {
            ...owned,
            x: point.x,
            y: point.y,
            control2X: owned.control2X + deltaX,
            control2Y: owned.control2Y + deltaY,
          }
        : { ...owned, x: point.x, y: point.y };
    const next = commands[commandIndex + 1];
    if (next?.type === 'cubic') {
      commands[commandIndex + 1] = {
        ...next,
        control1X: next.control1X + deltaX,
        control1Y: next.control1Y + deltaY,
      };
    }
  } else if (owned.type === 'cubic') {
    commands[commandIndex] =
      role === 'control1'
        ? { ...owned, control1X: point.x, control1Y: point.y }
        : { ...owned, control2X: point.x, control2Y: point.y };
  }
  return { ...geometry, commands };
}

export function createBlankDocument(): ImageDocument {
  const layer = createPixelLayer({
    id: makeId('layer'),
    name: 'Background',
    width: NEW_DOCUMENT_SIZE.width,
    height: NEW_DOCUMENT_SIZE.height,
  });
  return createDocument({
    id: makeId('document'),
    name: 'Untitled artwork',
    ...NEW_DOCUMENT_SIZE,
    layers: [layer],
  });
}

export function flattenLayers(layers: readonly Layer[]): Layer[] {
  return layers.flatMap((layer) =>
    layer.kind === 'group' ? [layer, ...flattenLayers(layer.children)] : [layer],
  );
}

export function displayLayers(layers: readonly Layer[]): Layer[] {
  return [...layers]
    .reverse()
    .flatMap((layer) =>
      layer.kind === 'group' ? [layer, ...displayLayers(layer.children)] : [layer],
    );
}

export function layerDepth(layers: readonly Layer[], id: string, depth = 0): number {
  for (const layer of layers) {
    if (layer.id === id) return depth;
    if (layer.kind === 'group') {
      const childDepth = layerDepth(layer.children, id, depth + 1);
      if (childDepth >= 0) return childDepth;
    }
  }
  return -1;
}

export function sameTransform(left: LayerTransform, right: LayerTransform): boolean {
  return (Object.keys(left) as Array<keyof LayerTransform>).every(
    (key) => Math.abs(left[key] - right[key]) <= 1e-12,
  );
}

export function selectionRectangle(start: DocumentPoint, end: DocumentPoint): DocumentRectangle {
  const left = Math.floor(Math.min(start.x, end.x));
  const top = Math.floor(Math.min(start.y, end.y));
  const right = Math.ceil(Math.max(start.x, end.x));
  const bottom = Math.ceil(Math.max(start.y, end.y));
  return { x: left, y: top, width: Math.max(1, right - left), height: Math.max(1, bottom - top) };
}

export function selectionBounds(document: ImageDocument): DocumentRectangle | null {
  const selection = document.selection;
  if (!selection) return null;
  const store = selection.coverageStore;
  if (store.defaultCoverage !== 0 && store.tileCount < store.columnCount * store.rowCount) {
    return { x: 0, y: 0, width: selection.width, height: selection.height };
  }
  let left = selection.width;
  let top = selection.height;
  let right = -1;
  let bottom = -1;
  const chunks = selection.coverageStore.coverageChunks();
  for (const chunk of chunks) {
    for (let localY = 0; localY < chunk.height; localY += 1) {
      for (let localX = 0; localX < chunk.width; localX += 1) {
        if ((chunk.coverage[localY * chunk.width + localX] ?? 0) === 0) continue;
        const x = chunk.x + localX;
        const y = chunk.y + localY;
        left = Math.min(left, x);
        top = Math.min(top, y);
        right = Math.max(right, x);
        bottom = Math.max(bottom, y);
      }
    }
  }
  return right < left
    ? null
    : { x: left, y: top, width: right - left + 1, height: bottom - top + 1 };
}

export function hexColor(value: string): readonly [number, number, number, number] {
  const match = /^#([\da-f]{2})([\da-f]{2})([\da-f]{2})$/i.exec(value);
  if (!match) return [0, 0, 0, 255];
  return [
    Number.parseInt(match[1] ?? '0', 16),
    Number.parseInt(match[2] ?? '0', 16),
    Number.parseInt(match[3] ?? '0', 16),
    255,
  ];
}

export function transformDraft(transform: LayerTransform): TransformDraft {
  return {
    a: String(transform.a),
    b: String(transform.b),
    c: String(transform.c),
    d: String(transform.d),
    e: String(transform.e),
    f: String(transform.f),
  };
}

export function parseTransformDraft(draft: TransformDraft): LayerTransform | null {
  const transform = {
    a: Number(draft.a),
    b: Number(draft.b),
    c: Number(draft.c),
    d: Number(draft.d),
    e: Number(draft.e),
    f: Number(draft.f),
  };
  const determinant = transform.a * transform.d - transform.b * transform.c;
  return Object.values(transform).every(Number.isFinite) && Math.abs(determinant) > 1e-12
    ? transform
    : null;
}

export function parseSizeDraft(widthDraft: string, heightDraft: string) {
  const width = Number(widthDraft);
  const height = Number(heightDraft);
  if (!Number.isSafeInteger(width) || width <= 0 || !Number.isSafeInteger(height) || height <= 0) {
    return { width, height, error: 'Width and height must be positive whole numbers.' } as const;
  }
  if (width > MAX_DOCUMENT_DIMENSION || height > MAX_DOCUMENT_DIMENSION) {
    return {
      width,
      height,
      error: `Width and height cannot exceed ${String(MAX_DOCUMENT_DIMENSION)} pixels.`,
    } as const;
  }
  if (width * height > MAX_DOCUMENT_PIXELS) {
    return {
      width,
      height,
      error: `The document cannot exceed ${String(MAX_DOCUMENT_PIXELS)} pixels.`,
    } as const;
  }
  return { width, height, error: null } as const;
}

export function renderableLayerDimensions(
  layer: Layer,
): { readonly width: number; readonly height: number } | null {
  if (layer.kind === 'pixel' || layer.kind === 'text')
    return { width: layer.width, height: layer.height };
  if (layer.kind === 'shape') return { width: layer.geometry.width, height: layer.geometry.height };
  if (layer.kind === 'smart-object') return { ...layer.source.canvas };
  return null;
}

export function transformAroundLayerCenter(
  layer: Layer,
  operation: LayerTransform,
): LayerTransform {
  const dimensions = renderableLayerDimensions(layer);
  if (!dimensions) return layer.transform;
  const centerX = dimensions.width / 2;
  const centerY = dimensions.height / 2;
  const toCenter = { ...identityLayerTransform(), e: centerX, f: centerY };
  const fromCenter = { ...identityLayerTransform(), e: -centerX, f: -centerY };
  return multiplyLayerTransforms(
    layer.transform,
    multiplyLayerTransforms(toCenter, multiplyLayerTransforms(operation, fromCenter)),
  );
}

export function movedLayerPosition(
  gesture: Extract<Gesture, { kind: 'move' }>,
  point: DocumentPoint,
): DocumentPoint {
  const deltaX = point.x - gesture.start.x;
  const deltaY = point.y - gesture.start.y;
  return {
    x: Math.round(
      gesture.origin.e + gesture.parentInverse.a * deltaX + gesture.parentInverse.c * deltaY,
    ),
    y: Math.round(
      gesture.origin.f + gesture.parentInverse.b * deltaX + gesture.parentInverse.d * deltaY,
    ),
  };
}

export function freeTransformAtPoint(
  gesture: Extract<Gesture, { kind: 'free-transform' }>,
  point: DocumentPoint,
  uniform: boolean,
): LayerTransform {
  return gesture.handle === 'rotate'
    ? rotateTransformFromPointers(gesture.snapshot, gesture.start, point)
    : gesture.handle === 'center'
      ? translateTransformFromPointers(gesture.snapshot, gesture.start, point)
      : scaleTransformFromHandle(gesture.snapshot, gesture.handle, point, { uniform });
}
