import { describe, expect, it, vi } from 'vitest';
import { exportImage, importImageFile, type ImageCodecAdapter } from './index.js';

function adapter(): ImageCodecAdapter {
  return {
    decode: vi.fn(async () => ({ width: 1, height: 1, source: {} as CanvasImageSource })),
    readPixels: vi.fn(() => new Uint8ClampedArray([1, 2, 3, 255])),
    encode: vi.fn(
      async (_pixels, _width, _height, format) => new Blob(['encoded'], { type: format }),
    ),
  };
}

describe('image codecs', () => {
  it('imports an image through the injected browser boundary', async () => {
    const codec = adapter();
    const file = Object.assign(new Blob(['image'], { type: 'image/png' }), { name: 'test.png' });
    await expect(importImageFile(file, codec)).resolves.toMatchObject({
      width: 1,
      height: 1,
      name: 'test.png',
    });
  });

  it('exports JPEG with a safe quality value', async () => {
    const codec = adapter();
    const blob = await exportImage(
      { width: 1, height: 1, pixels: new Uint8ClampedArray(4) },
      { format: 'image/jpeg', quality: 5 },
      codec,
    );
    expect(blob.type).toBe('image/jpeg');
    expect(codec.encode).toHaveBeenCalledWith(expect.any(Uint8ClampedArray), 1, 1, 'image/jpeg', 1);
  });

  it('rejects non-image files', async () => {
    await expect(
      importImageFile(new Blob(['x'], { type: 'text/plain' }), adapter()),
    ).rejects.toThrow('Unsupported file type');
  });
});
