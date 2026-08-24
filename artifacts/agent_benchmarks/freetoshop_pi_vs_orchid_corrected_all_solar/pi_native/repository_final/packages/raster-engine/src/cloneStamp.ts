import type { BrushPoint } from '../index.js';
import { TiledPixelStore } from '@freetoshop/pixel-storage';
import type { LayerTransform } from '@freetoshop/core-document';

/**
 * Clone Stamp source state.
 *
 * This is the explicit, testable data model for the clone source.
 * It is separate from the brush/eraser tools and from UI handlers.
 */
export interface CloneSource {
  /** Identifier of the source document. Must match the active document during a stroke. */
  readonly documentId: string;
  /** Layer from which pixels are sampled. */
  readonly layerId: string;
  /** Document-space point where the source was established (Alt/Option-click). */
  readonly originX: number;
  readonly originY: number;
  /** When true, the source-to-destination offset is preserved across the gesture. */
  readonly aligned: boolean;
}

/**
 * Explicit representation of whether a valid source exists and, when present, its parameters.
 * This is the UI-visible state for the Clone Stamp tool.
 */
export interface CloneState {
  readonly hasSource: boolean;
  readonly source: CloneSource | null;
}

/**
 * Rectangle of source pixels that must be read for a tile write to remain tile-bounded.
 */
export interface CloneSourceRegion {
  readonly x: number;
  readonly y: number;
  readonly width: number;
  readonly height: number;
  readonly pixels: Uint8ClampedArray;
}

/**
 * Destination patch produced by a clone stamp stroke.
 * Reuses the existing PixelPatch shape so it plugs into the existing patchPixels command.
 */
export interface ClonePatch {
  readonly x: number;
  readonly y: number;
  readonly width: number;
  readonly height: number;
  readonly pixels: Uint8ClampedArray;
}

/** Sentinel for an empty patch. */
export function emptyClonePatch(): ClonePatch {
  return { x: 0, y: 0, width: 0, height: 0, pixels: new Uint8ClampedArray() };
}

/** Sentinel for an empty source region. */
export function emptyCloneSourceRegion(): CloneSourceRegion {
  return { x: 0, y: 0, width: 0, height: 0, pixels: new Uint8ClampedArray() };
}

/** Brush-point shape reused by clone strokes (document-space position plus optional pressure). */
export type CloneBrushPoint = BrushPoint;

/**
 * Clone stroke brush options.
 * Reuses size/hardness/opacity semantics from the existing brush tooling.
 */
export interface CloneBrushOptions {
  readonly size: number;
  readonly hardness: number;
  readonly opacity: number;
}

/**
 * Parameters needed to establish a clone source from a source document and layer.
 */
export interface EstablishCloneSourceParams {
  readonly sourceDocumentId: string;
  readonly sourceLayerId: string;
  readonly sourcePointX: number;
  readonly sourcePointY: number;
  readonly aligned: boolean;
}

/**
 * The source-to-destination offset established at the Alt/Option-click moment.
 * In aligned mode this offset is preserved for the entire gesture.
 */
export interface CloneOffset {
  readonly sourceX: number;
  readonly sourceY: number;
}

/**
 * Compute the source offset established at the Alt/Option-click.
 * Both points are in document space of the same document.
 */
export function computeCloneOffset(
  sourcePoint: { x: number; y: number },
  destinationPoint: { x: number; y: number },
): CloneOffset {
  return {
    sourceX: sourcePoint.x - destinationPoint.x,
    sourceY: sourcePoint.y - destinationPoint.y,
  };
}

/**
 * Determine the source document-space point to sample for a destination point.
 *
 * - Aligned: `source = destination + offset`, preserving the established offset.
 * - Unaligned: `source = sourceOrigin`, restarting from the source origin each dab.
 */
export function sampleSourcePoint(
  destinationX: number,
  destinationY: number,
  sourceOriginX: number,
  sourceOriginY: number,
  offset: CloneOffset | null,
  aligned: boolean,
): { sourceX: number; sourceY: number } {
  if (aligned && offset !== null) {
    return {
      sourceX: destinationX + offset.sourceX,
      sourceY: destinationY + offset.sourceY,
    };
  }
  return { sourceX: sourceOriginX, sourceY: sourceOriginY };
}

/**
 * Map a source document-space point into source-layer local space using the source world transform inverse.
 */
export function sourceDocumentToLocal(
  transform: LayerTransform,
  inverse: LayerTransform,
  sourceX: number,
  sourceY: number,
): { localX: number; localY: number } {
  // A point in document space of the source document is transformed by the inverse of the source world transform
  // to get the layer-local coordinate.
  // Note: sourceWorldTransform maps local -> document, so inverse maps document -> local.
  const localX = inverse.a * sourceX + inverse.c * sourceY + inverse.e;
  const localY = inverse.b * sourceX + inverse.d * sourceY + inverse.f;
  return { localX, localY };
}

/**
 * Read a bounded region from the source pixel store.
 * The region is the union of dab footprints in source-local space.
 * This stays tile-bounded because it uses TiledPixelStore.read.
 */
export function readSourceRegion(
  store: TiledPixelStore,
  dabs: readonly { localX: number; localY: number }[],
  radius: number,
  sourceLayerSize: { width: number; height: number },
): CloneSourceRegion {
  if (dabs.length === 0) return emptyCloneSourceRegion();

  let bounds: { left: number; top: number; right: number; bottom: number } | null = null;
  for (const dab of dabs) {
    const b = documentDabBoundsLocal(dab.localX, dab.localY, radius, sourceLayerSize.width, sourceLayerSize.height);
    bounds = unionBoundsLocal(bounds, b);
  }
  if (bounds === null || bounds.right < bounds.left) return emptyCloneSourceRegion();

  const region = store.read({
    x: bounds.left,
    y: bounds.top,
    width: bounds.right - bounds.left + 1,
    height: bounds.bottom - bounds.top + 1,
  });
  return {
    x: bounds.left,
    y: bounds.top,
    width: bounds.right - bounds.left + 1,
    height: bounds.bottom - bounds.top + 1,
    pixels: new Uint8ClampedArray(region),
  };
}

/**
 * Local-space dab bounds (mirrors documentDabBounds but for local coordinates).
 */
function documentDabBoundsLocal(
  x: number,
  y: number,
  radius: number,
  width: number,
  height: number,
): { left: number; top: number; right: number; bottom: number } {
  return {
    left: Math.max(0, Math.floor(x - radius)),
    top: Math.max(0, Math.floor(y - radius)),
    right: Math.min(width - 1, Math.ceil(x + radius)),
    bottom: Math.min(height - 1, Math.ceil(y + radius)),
  };
}

function unionBoundsLocal(
  left: { left: number; top: number; right: number; bottom: number } | null,
  right: { left: number; top: number; right: number; bottom: number },
): { left: number; top: number; right: number; bottom: number } | null {
  if (left === null) return { ...right };
  return {
    left: Math.min(left.left, right.left),
    top: Math.min(left.top, right.top),
    right: Math.max(left.right, right.right),
    bottom: Math.max(left.bottom, right.bottom),
  };
}
