import type { ImageDocument } from '@freetoshop/core-document';
import {
  CompositeTileCache,
  compositeDocumentPixels,
  compositeDocumentRegion,
  rasterizeLayerNearest,
} from '@freetoshop/renderer';
import { intersectRegions, type PixelRegion } from '@freetoshop/pixel-storage';

export interface CompositeWorkerComputeOptions {
  readonly tileSize?: number;
  readonly maxBytes?: number;
  readonly viewportRegion?: {
    readonly x: number;
    readonly y: number;
    readonly width: number;
    readonly height: number;
  };
  readonly progressiveViewportOnly?: boolean;
  readonly mipLevel?: number;
}

/**
 * Shipped composite worker algorithm: dependency-keyed tile cache over the
 * deterministic reference compositor. Extracted so unit tests exercise the real
 * path without a browser Worker host.
 *
 * Compatibility compute API: progressive calls return the historical full-canvas
 * buffer. The shipped result path below emits compact region buffers instead.
 */
export function computeCompositeWorkerPixels(
  document: ImageDocument,
  options: CompositeWorkerComputeOptions = {},
): Uint8ClampedArray {
  const cache = new CompositeTileCache({
    tileSize: options.tileSize ?? 64,
    maxBytes: options.maxBytes ?? 96 * 1024 * 1024,
  });
  const reference = () => compositeDocumentPixels(document, { rasterize: rasterizeLayerNearest });
  const region = (
    _document: ImageDocument,
    area: { x: number; y: number; width: number; height: number },
  ) => compositeDocumentRegion(document, area, { rasterize: rasterizeLayerNearest });
  return cache.composite(document, {
    referenceComposite: reference,
    regionComposite: region,
    viewportRegion: options.viewportRegion,
    progressiveViewportOnly: options.progressiveViewportOnly === true,
    mipLevel: options.mipLevel,
  });
}

function progressiveMipPixelRegion(
  document: ImageDocument,
  viewportRegion: PixelRegion,
  tileSize: number,
  mipLevel: number,
): PixelRegion | null {
  const viewport = intersectRegions(viewportRegion, {
    x: 0,
    y: 0,
    width: document.canvas.width,
    height: document.canvas.height,
  });
  if (!viewport) return null;
  const scale = 2 ** mipLevel;
  const mipWidth = Math.max(1, Math.ceil(document.canvas.width / scale));
  const mipHeight = Math.max(1, Math.ceil(document.canvas.height / scale));
  const left = Math.max(0, Math.floor(viewport.x / scale));
  const top = Math.max(0, Math.floor(viewport.y / scale));
  const right = Math.min(mipWidth, Math.ceil((viewport.x + viewport.width) / scale));
  const bottom = Math.min(mipHeight, Math.ceil((viewport.y + viewport.height) / scale));
  const x = Math.floor(left / tileSize) * tileSize;
  const y = Math.floor(top / tileSize) * tileSize;
  const alignedRight = Math.min(mipWidth, Math.ceil(right / tileSize) * tileSize);
  const alignedBottom = Math.min(mipHeight, Math.ceil(bottom / tileSize) * tileSize);
  return { x, y, width: alignedRight - x, height: alignedBottom - y };
}

function downsampleRegion(
  source: Uint8ClampedArray,
  sourceWidth: number,
  sourceHeight: number,
  mipLevel: number,
): { readonly pixels: Uint8ClampedArray; readonly width: number; readonly height: number } {
  let pixels = source;
  let width = sourceWidth;
  let height = sourceHeight;
  for (let level = 0; level < mipLevel; level += 1) {
    const nextWidth = Math.max(1, Math.ceil(width / 2));
    const nextHeight = Math.max(1, Math.ceil(height / 2));
    const next = new Uint8ClampedArray(nextWidth * nextHeight * 4);
    for (let y = 0; y < nextHeight; y += 1) {
      for (let x = 0; x < nextWidth; x += 1) {
        const sourceOffset = (Math.min(height - 1, y * 2) * width + Math.min(width - 1, x * 2)) * 4;
        next.set(pixels.subarray(sourceOffset, sourceOffset + 4), (y * nextWidth + x) * 4);
      }
    }
    pixels = next;
    width = nextWidth;
    height = nextHeight;
  }
  return { pixels, width, height };
}

export function compositeWorkerResultBuffer(
  document: ImageDocument,
  generation: number,
  options: CompositeWorkerComputeOptions = {},
): {
  readonly kind: 'composite';
  readonly buffer: ArrayBuffer;
  readonly width: number;
  readonly height: number;
  readonly generation: number;
  readonly canvasWidth?: number;
  readonly canvasHeight?: number;
  readonly pixelRegion?: PixelRegion;
  readonly viewportRegion?: {
    readonly x: number;
    readonly y: number;
    readonly width: number;
    readonly height: number;
  };
  readonly progressiveViewportOnly?: boolean;
  readonly mipLevel?: number;
} {
  const mipLevel = options.mipLevel ?? 0;
  const canvasWidth = Math.max(1, Math.ceil(document.canvas.width / 2 ** mipLevel));
  const canvasHeight = Math.max(1, Math.ceil(document.canvas.height / 2 ** mipLevel));
  const compactRegion =
    options.progressiveViewportOnly === true && options.viewportRegion
      ? progressiveMipPixelRegion(
          document,
          options.viewportRegion,
          options.tileSize ?? 64,
          mipLevel,
        )
      : null;
  let pixels: Uint8ClampedArray;
  if (compactRegion) {
    const scale = 2 ** mipLevel;
    const documentRegion = {
      x: compactRegion.x * scale,
      y: compactRegion.y * scale,
      width: Math.min(compactRegion.width * scale, document.canvas.width - compactRegion.x * scale),
      height: Math.min(
        compactRegion.height * scale,
        document.canvas.height - compactRegion.y * scale,
      ),
    };
    const fullResolution = compositeDocumentRegion(document, documentRegion, {
      rasterize: rasterizeLayerNearest,
    });
    pixels = downsampleRegion(
      fullResolution,
      documentRegion.width,
      documentRegion.height,
      mipLevel,
    ).pixels;
  } else {
    pixels = computeCompositeWorkerPixels(document, options);
  }
  const buffer = pixels.buffer.slice(pixels.byteOffset, pixels.byteOffset + pixels.byteLength);
  return {
    kind: 'composite',
    buffer,
    width: compactRegion?.width ?? canvasWidth,
    height: compactRegion?.height ?? canvasHeight,
    canvasWidth,
    canvasHeight,
    pixelRegion: compactRegion ?? undefined,
    generation,
    viewportRegion: options.viewportRegion,
    progressiveViewportOnly: options.progressiveViewportOnly === true ? true : undefined,
    mipLevel: options.mipLevel,
  };
}
