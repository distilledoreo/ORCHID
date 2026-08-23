export const PSD_MIME_TYPE = 'image/vnd.adobe.photoshop';
export const PSD_MAX_DIMENSION = 16_384;
export const PSD_MAX_PIXELS = 100_000_000;
export const PSD_MAX_DECODED_BYTES = 512 * 1024 * 1024;

export function hasPsdSignature(buffer: ArrayBuffer): boolean {
  if (buffer.byteLength < 4) return false;
  const bytes = new Uint8Array(buffer, 0, 4);
  return bytes[0] === 0x38 && bytes[1] === 0x42 && bytes[2] === 0x50 && bytes[3] === 0x53;
}
