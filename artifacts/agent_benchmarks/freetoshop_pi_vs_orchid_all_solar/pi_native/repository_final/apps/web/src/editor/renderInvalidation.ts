import { transformPoint, type LayerTransform } from '@freetoshop/core-document';
import type { PixelRegion } from '@freetoshop/pixel-storage';

export interface RenderInvalidation<TDocument> {
  readonly document: TDocument;
  readonly regions: readonly PixelRegion[];
}

/** Converts an exact layer-local patch rectangle to its clipped document-space AABB. */
export function documentRegionForLocalPatch(
  patch: PixelRegion,
  transform: LayerTransform,
  canvas: { readonly width: number; readonly height: number },
): PixelRegion | null {
  if (patch.width <= 0 || patch.height <= 0) return null;
  const corners = [
    transformPoint(transform, patch.x, patch.y),
    transformPoint(transform, patch.x + patch.width, patch.y),
    transformPoint(transform, patch.x, patch.y + patch.height),
    transformPoint(transform, patch.x + patch.width, patch.y + patch.height),
  ];
  const left = Math.max(0, Math.floor(Math.min(...corners.map((point) => point.x))));
  const top = Math.max(0, Math.floor(Math.min(...corners.map((point) => point.y))));
  const right = Math.min(canvas.width, Math.ceil(Math.max(...corners.map((point) => point.x))));
  const bottom = Math.min(canvas.height, Math.ceil(Math.max(...corners.map((point) => point.y))));
  if (right <= left || bottom <= top) return null;
  return { x: left, y: top, width: right - left, height: bottom - top };
}
