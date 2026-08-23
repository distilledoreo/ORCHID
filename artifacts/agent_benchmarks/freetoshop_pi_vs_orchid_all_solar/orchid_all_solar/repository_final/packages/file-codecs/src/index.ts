export interface DecodedRaster {
  width: number;
  height: number;
  pixels: Uint8ClampedArray;
  name?: string;
  mimeType?: string;
}

export type ExportFormat = 'image/png' | 'image/jpeg';

export interface ExportOptions {
  format?: ExportFormat;
  quality?: number;
}

export interface ImageCodecAdapter {
  decode(
    blob: Blob,
  ): Promise<{ width: number; height: number; source: CanvasImageSource; close?: () => void }>;
  readPixels(source: CanvasImageSource, width: number, height: number): Uint8ClampedArray;
  encode(
    pixels: Uint8ClampedArray,
    width: number,
    height: number,
    format: ExportFormat,
    quality?: number,
  ): Promise<Blob>;
}

export async function importImageFile(
  file: Blob & { name?: string },
  adapter: ImageCodecAdapter = browserImageCodecAdapter,
): Promise<DecodedRaster> {
  if (!file.type.startsWith('image/'))
    throw new TypeError(`Unsupported file type: ${file.type || 'unknown'}.`);
  const decoded = await adapter.decode(file);
  try {
    const pixels = adapter.readPixels(decoded.source, decoded.width, decoded.height);
    assertRaster(pixels, decoded.width, decoded.height);
    return {
      width: decoded.width,
      height: decoded.height,
      pixels,
      name: file.name,
      mimeType: file.type,
    };
  } finally {
    decoded.close?.();
  }
}

export async function importImageDocument(
  file: Blob & { name?: string },
  options: { documentId?: string; layerId?: string } = {},
  adapter: ImageCodecAdapter = browserImageCodecAdapter,
): Promise<ImageDocument> {
  const raster = await importImageFile(file, adapter);
  const name = raster.name ?? 'Imported image';
  return createDocument({
    id: options.documentId ?? makeId('document'),
    name,
    width: raster.width,
    height: raster.height,
    layers: [
      createPixelLayer({
        id: options.layerId ?? makeId('layer'),
        name,
        width: raster.width,
        height: raster.height,
        pixels: raster.pixels,
      }),
    ],
  });
}

export async function exportImage(
  raster: Pick<DecodedRaster, 'width' | 'height' | 'pixels'>,
  options: ExportOptions = {},
  adapter: ImageCodecAdapter = browserImageCodecAdapter,
): Promise<Blob> {
  assertRaster(raster.pixels, raster.width, raster.height);
  const format = options.format ?? 'image/png';
  const quality = format === 'image/jpeg' ? clampQuality(options.quality ?? 0.92) : undefined;
  return adapter.encode(raster.pixels, raster.width, raster.height, format, quality);
}

export const browserImageCodecAdapter: ImageCodecAdapter = {
  async decode(blob) {
    if (typeof createImageBitmap !== 'function')
      throw new Error('createImageBitmap is not available.');
    const bitmap = await createImageBitmap(blob);
    return {
      width: bitmap.width,
      height: bitmap.height,
      source: bitmap,
      close: () => bitmap.close(),
    };
  },
  readPixels(source, width, height) {
    const context = createCanvasContext(width, height);
    context.drawImage(source, 0, 0);
    return context.getImageData(0, 0, width, height).data;
  },
  async encode(pixels, width, height, format, quality) {
    const { canvas, context } = createCanvas(width, height);
    context.putImageData(new ImageData(pixels, width, height), 0, 0);
    if (typeof OffscreenCanvas !== 'undefined' && canvas instanceof OffscreenCanvas) {
      return canvas.convertToBlob({ type: format, quality });
    }
    const htmlCanvas = canvas as HTMLCanvasElement;
    return new Promise<Blob>((resolve, reject) => {
      htmlCanvas.toBlob(
        (blob) => (blob ? resolve(blob) : reject(new Error(`Could not encode ${format}.`))),
        format,
        quality,
      );
    });
  },
};

function createCanvas(
  width: number,
  height: number,
): {
  canvas: OffscreenCanvas | HTMLCanvasElement;
  context: CanvasRenderingContext2D | OffscreenCanvasRenderingContext2D;
} {
  const canvas =
    typeof OffscreenCanvas !== 'undefined'
      ? new OffscreenCanvas(width, height)
      : Object.assign(document.createElement('canvas'), { width, height });
  const context = canvas.getContext('2d');
  if (!context) throw new Error('Canvas 2D is not available.');
  return { canvas, context };
}

function createCanvasContext(
  width: number,
  height: number,
): CanvasRenderingContext2D | OffscreenCanvasRenderingContext2D {
  return createCanvas(width, height).context;
}

function clampQuality(quality: number): number {
  if (!Number.isFinite(quality)) return 0.92;
  return Math.min(1, Math.max(0, quality));
}

function assertRaster(pixels: Uint8ClampedArray, width: number, height: number): void {
  if (!Number.isInteger(width) || width <= 0 || !Number.isInteger(height) || height <= 0) {
    throw new RangeError('Image dimensions must be positive integers.');
  }
  if (pixels.length !== width * height * 4)
    throw new RangeError('Pixel buffer does not match image dimensions.');
}

function makeId(prefix: string): string {
  return `${prefix}-${typeof crypto !== 'undefined' && 'randomUUID' in crypto ? crypto.randomUUID() : Date.now().toString(36)}`;
}
import { createDocument, createPixelLayer, type ImageDocument } from '@freetoshop/core-document';
