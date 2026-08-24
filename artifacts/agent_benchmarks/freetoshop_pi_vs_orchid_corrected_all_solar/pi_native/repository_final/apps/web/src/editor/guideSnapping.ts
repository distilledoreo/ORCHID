import type {
  DocumentGrid,
  DocumentGuide,
  ImageDocument,
  Layer,
  LayerTransform,
} from '@freetoshop/core-document';

export interface DocumentPoint {
  readonly x: number;
  readonly y: number;
}

/** Axis-aligned smart-guide candidate: vertical line at x or horizontal line at y. */
export interface SmartSnapEdge {
  readonly axis: 'x' | 'y';
  readonly position: number;
  readonly source: 'layer-edge' | 'layer-center' | 'canvas';
}

export interface SnapDocumentPointOptions {
  readonly guides?: readonly DocumentGuide[];
  readonly grid?: DocumentGrid | null;
  /** Smart-guide edges (layer bounds/centers) preferred after explicit guides. */
  readonly smartEdges?: readonly SmartSnapEdge[];
  /** Snap radius in document pixels. */
  readonly threshold: number;
  readonly snapToGuides?: boolean;
  readonly snapToGrid?: boolean;
  readonly snapToSmartGuides?: boolean;
}

export interface SnapDocumentPointResult extends DocumentPoint {
  readonly snappedX: boolean;
  readonly snappedY: boolean;
  /** Smart edges that actually applied (for temporary overlay strokes). Empty when guides/grid won. */
  readonly activeSmartEdges: readonly SmartSnapEdge[];
}

/**
 * Snaps a document-space point to nearby guides, smart edges, and/or grid lines.
 * Priority: explicit guides → smart edges → grid. Pure function: no DOM.
 */
export function snapDocumentPoint(
  point: DocumentPoint,
  options: SnapDocumentPointOptions,
): SnapDocumentPointResult {
  const threshold = Number.isFinite(options.threshold) ? Math.max(0, options.threshold) : 0;
  let x = point.x;
  let y = point.y;
  let snappedX = false;
  let snappedY = false;
  const activeSmartEdges: SmartSnapEdge[] = [];

  if (options.snapToGuides !== false && options.guides && options.guides.length > 0) {
    let bestDx = threshold + 1;
    let bestDy = threshold + 1;
    let snapX: number | null = null;
    let snapY: number | null = null;
    for (const guide of options.guides) {
      if (!Number.isFinite(guide.position)) continue;
      if (guide.direction === 'vertical') {
        const dx = Math.abs(point.x - guide.position);
        if (dx <= threshold && dx < bestDx) {
          bestDx = dx;
          snapX = guide.position;
        }
      } else {
        const dy = Math.abs(point.y - guide.position);
        if (dy <= threshold && dy < bestDy) {
          bestDy = dy;
          snapY = guide.position;
        }
      }
    }
    if (snapX !== null) {
      x = snapX;
      snappedX = true;
    }
    if (snapY !== null) {
      y = snapY;
      snappedY = true;
    }
  }

  if (options.snapToSmartGuides !== false && options.smartEdges && options.smartEdges.length > 0) {
    let bestDx = threshold + 1;
    let bestDy = threshold + 1;
    let snapEdgeX: SmartSnapEdge | null = null;
    let snapEdgeY: SmartSnapEdge | null = null;
    for (const edge of options.smartEdges) {
      if (!Number.isFinite(edge.position)) continue;
      if (edge.axis === 'x' && !snappedX) {
        const dx = Math.abs(point.x - edge.position);
        if (dx <= threshold && dx < bestDx) {
          bestDx = dx;
          snapEdgeX = edge;
        }
      } else if (edge.axis === 'y' && !snappedY) {
        const dy = Math.abs(point.y - edge.position);
        if (dy <= threshold && dy < bestDy) {
          bestDy = dy;
          snapEdgeY = edge;
        }
      }
    }
    if (snapEdgeX !== null) {
      x = snapEdgeX.position;
      snappedX = true;
      activeSmartEdges.push(snapEdgeX);
    }
    if (snapEdgeY !== null) {
      y = snapEdgeY.position;
      snappedY = true;
      activeSmartEdges.push(snapEdgeY);
    }
  }

  if (options.snapToGrid !== false && options.grid) {
    const { horizontal, vertical } = options.grid;
    if (!snappedX && horizontal > 0 && Number.isFinite(horizontal)) {
      const nearest = Math.round(point.x / horizontal) * horizontal;
      if (Math.abs(point.x - nearest) <= threshold) {
        x = nearest;
        snappedX = true;
      }
    }
    if (!snappedY && vertical > 0 && Number.isFinite(vertical)) {
      const nearest = Math.round(point.y / vertical) * vertical;
      if (Math.abs(point.y - nearest) <= threshold) {
        y = nearest;
        snappedY = true;
      }
    }
  }

  return { x, y, snappedX, snappedY, activeSmartEdges };
}

/**
 * Collects axis-aligned smart-guide edges from visible root layers that use
 * integer translation-only transforms (no scale/rotate). Includes left/right/
 * top/bottom and centers. Canvas midlines are always included.
 */
export function collectSmartSnapEdges(
  document: ImageDocument,
  options: { readonly excludeLayerIds?: ReadonlySet<string> } = {},
): readonly SmartSnapEdge[] {
  const edges: SmartSnapEdge[] = [
    { axis: 'x', position: document.canvas.width / 2, source: 'canvas' },
    { axis: 'y', position: document.canvas.height / 2, source: 'canvas' },
  ];
  const exclude = options.excludeLayerIds;
  const visit = (layers: readonly Layer[], parent: LayerTransform) => {
    for (const layer of layers) {
      if (!layer.visible) continue;
      const transform = multiplyTransforms(parent, layer.transform);
      if (layer.kind === 'group') {
        visit(layer.children, transform);
        continue;
      }
      if (exclude?.has(layer.id)) continue;
      if (!isIntegerTranslation(transform)) continue;
      const size = layerDimensions(layer);
      if (!size) continue;
      const left = transform.e;
      const top = transform.f;
      const right = left + size.width;
      const bottom = top + size.height;
      edges.push(
        { axis: 'x', position: left, source: 'layer-edge' },
        { axis: 'x', position: right, source: 'layer-edge' },
        { axis: 'x', position: left + size.width / 2, source: 'layer-center' },
        { axis: 'y', position: top, source: 'layer-edge' },
        { axis: 'y', position: bottom, source: 'layer-edge' },
        { axis: 'y', position: top + size.height / 2, source: 'layer-center' },
      );
    }
  };
  visit(document.layers, { a: 1, b: 0, c: 0, d: 1, e: 0, f: 0 });
  return edges;
}

function isIntegerTranslation(transform: LayerTransform): boolean {
  return (
    transform.a === 1 &&
    transform.b === 0 &&
    transform.c === 0 &&
    transform.d === 1 &&
    Number.isInteger(transform.e) &&
    Number.isInteger(transform.f)
  );
}

function multiplyTransforms(parent: LayerTransform, local: LayerTransform): LayerTransform {
  return {
    a: parent.a * local.a + parent.c * local.b,
    b: parent.b * local.a + parent.d * local.b,
    c: parent.a * local.c + parent.c * local.d,
    d: parent.b * local.c + parent.d * local.d,
    e: parent.a * local.e + parent.c * local.f + parent.e,
    f: parent.b * local.e + parent.d * local.f + parent.f,
  };
}

function layerDimensions(layer: Layer): { width: number; height: number } | null {
  if (layer.kind === 'pixel') return { width: layer.width, height: layer.height };
  if (layer.kind === 'text') return { width: layer.width, height: layer.height };
  if (layer.kind === 'shape') {
    return { width: layer.geometry.width, height: layer.geometry.height };
  }
  if (layer.kind === 'smart-object') {
    return { width: layer.source.canvas.width, height: layer.source.canvas.height };
  }
  return null;
}

/** Document-pixel snap radius scaled from screen pixels and zoom. */
export function snapThresholdInDocumentPixels(screenPixels: number, zoom: number): number {
  const safeZoom = Number.isFinite(zoom) && zoom > 0 ? zoom : 1;
  const safeScreen = Number.isFinite(screenPixels) ? Math.max(0, screenPixels) : 0;
  return safeScreen / safeZoom;
}
