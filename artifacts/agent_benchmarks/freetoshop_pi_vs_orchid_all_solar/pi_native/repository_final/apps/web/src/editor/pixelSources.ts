import type { PixelLayer } from '@freetoshop/core-document';
import type { PixelSource } from '@freetoshop/pixel-storage';

export type SparsePixelSourceResolver = (layer: PixelLayer) => PixelSource;

/** Resolves the canonical tile-owned resource without display-time conversion. */
export function createSparsePixelSourceResolver(): SparsePixelSourceResolver {
  return (layer) => layer.pixelStore;
}
