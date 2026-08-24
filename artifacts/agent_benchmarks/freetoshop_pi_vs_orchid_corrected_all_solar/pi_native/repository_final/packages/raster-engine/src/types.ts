/**
 * Clone Stamp tool types.
 *
 * These types deliberately capture the testable data model (source, offset, aligned/unaligned)
 * rather than leaving that state implicit in UI handlers.
 */

export interface BrushPoint {
  readonly x: number;
  readonly y: number;
  readonly pressure?: number;
}

/**
 * Explicit representation of a clone source.
 *
 * - documentId: identifier of the source document (must match the active document during a stroke).
 * - layerId: identifier of the source layer within that document.
 * - origin: document-space point where the source was established (Alt/Option-click).
 * - aligned: when true, the source-to-destination offset is preserved across the gesture.
 */
export interface CloneSource {
  readonly documentId: string;
  readonly layerId: string;
  readonly origin: BrushPoint;
  readonly aligned: boolean;
}

/**
 * The UI-visible source state.
 * hasSource indicates whether a source has been established.
 * source is the active source when hasSource is true.
 */
export interface CloneState {
  readonly hasSource: boolean;
  readonly source: CloneSource | null;
}

/**
 * A bounded read from the source pixel store.
 * This is the unit of tile-bounded work for clone sampling.
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
 * Reuses the existing PixelPatch shape so it plugs into patchPixels.
 */
export interface ClonePatch {
  readonly x: number;
  readonly y: number;
  readonly width: number;
  readonly height: number;
  readonly pixels: Uint8ClampedArray;
}

/** Empty patch sentinel. */
export function emptyClonePatch(): ClonePatch {
  return { x: 0, y: 0, width: 0, height: 0, pixels: new Uint8ClampedArray() };
}

/** Empty source region sentinel. */
export function emptyCloneSourceRegion(): CloneSourceRegion {
  return { x: 0, y: 0, width: 0, height: 0, pixels: new Uint8ClampedArray() };
}
