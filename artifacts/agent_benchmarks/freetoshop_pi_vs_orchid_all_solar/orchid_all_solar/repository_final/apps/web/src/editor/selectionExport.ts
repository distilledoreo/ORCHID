import type { ImageDocument } from '@freetoshop/core-document';
import { compositeDocumentRegion, type PixelSourceResolver } from '@freetoshop/renderer';
import { selectionBounds } from './model.js';

export interface SelectionExportRaster {
  readonly x: number;
  readonly y: number;
  readonly width: number;
  readonly height: number;
  readonly pixels: Uint8ClampedArray;
}

/** Composites only the selected bounds and applies soft selection coverage to output alpha. */
export function selectionExportRaster(
  document: ImageDocument,
  pixelSource?: PixelSourceResolver,
): SelectionExportRaster | null {
  const selection = document.selection;
  if (!selection) return null;
  const bounds = selectionBounds(document);
  if (!bounds) return null;
  const pixels = compositeDocumentRegion(document, bounds, { pixelSource });
  const coverage = selection.coverageStore.read(bounds);
  for (let index = 0; index < coverage.length; index += 1) {
    const alphaOffset = index * 4 + 3;
    pixels[alphaOffset] = Math.round(((pixels[alphaOffset] ?? 0) * (coverage[index] ?? 0)) / 255);
  }
  return { ...bounds, pixels };
}
