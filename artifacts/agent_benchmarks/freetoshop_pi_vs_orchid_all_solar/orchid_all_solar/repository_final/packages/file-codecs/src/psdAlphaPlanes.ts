/**
 * Best-effort multi-plane alpha extraction from PSD composite Image Data.
 *
 * ag-psd exposes only a single RGBA composite surface. When the header reports
 * more than four channels (RGB + alphas), extra greyscale planes are dropped.
 * This decoder walks the standard PSD section layout and, for 8-bit RGB files
 * with raw (0) or PackBits RLE (1) compression, recovers alpha planes after RGB.
 *
 * Limitations (documented; not full PSB / 16-bit / CMYK support):
 * - PSD version 1 only (not PSB)
 * - 8-bit depth, RGB color mode
 * - Composite Image Data only (not per-layer channel data)
 * - Compression 0 (raw) and 1 (RLE); zip (2/3) unsupported
 */

export interface DecodedPsdAlphaPlanes {
  readonly width: number;
  readonly height: number;
  /** Greyscale planes in document order after R,G,B (index 0 = first alpha). */
  readonly planes: readonly Uint8ClampedArray[];
  readonly channelCount: number;
  readonly compression: 0 | 1;
}

/**
 * Decodes composite alpha channel planes from a PSD/PSB file buffer.
 * Returns null when the file is not a supported multi-alpha RGB composite.
 */
export function decodePsdAlphaPlanes(buffer: ArrayBuffer): DecodedPsdAlphaPlanes | null {
  if (buffer.byteLength < 30) return null;
  const view = new DataView(buffer);

  // Signature "8BPS"
  if (
    view.getUint8(0) !== 0x38 ||
    view.getUint8(1) !== 0x42 ||
    view.getUint8(2) !== 0x50 ||
    view.getUint8(3) !== 0x53
  ) {
    return null;
  }

  const version = view.getUint16(4, false);
  // Version 1 = PSD; version 2 = PSB (larger length fields — not handled here).
  if (version !== 1) return null;

  const channels = view.getUint16(12, false);
  const height = view.getUint32(14, false);
  const width = view.getUint32(18, false);
  const depth = view.getUint16(22, false);
  const colorMode = view.getUint16(24, false);

  // Need RGB + at least one alpha plane to be multi-plane useful; still allow
  // channels === 4 (single alpha) for a consistent plane path.
  if (channels < 4 || width < 1 || height < 1) return null;
  if (depth !== 8 || colorMode !== 3) return null;
  // Guard against pathological allocations.
  if (width > 16_384 || height > 16_384 || channels > 56) return null;

  let offset = 26;

  // Color mode data
  if (offset + 4 > view.byteLength) return null;
  const colorModeLength = view.getUint32(offset, false);
  offset += 4 + colorModeLength;
  if (offset + 4 > view.byteLength || colorModeLength < 0) return null;

  // Image resources
  const resourcesLength = view.getUint32(offset, false);
  offset += 4 + resourcesLength;
  if (offset + 4 > view.byteLength || resourcesLength < 0) return null;

  // Layer and mask information
  const layerMaskLength = view.getUint32(offset, false);
  offset += 4 + layerMaskLength;
  if (offset + 2 > view.byteLength || layerMaskLength < 0) return null;

  const compression = view.getUint16(offset, false);
  offset += 2;
  if (compression !== 0 && compression !== 1) return null;

  const planeBytes = width * height;
  const alphaPlaneCount = channels - 3;
  if (alphaPlaneCount < 1) return null;

  let allPlanes: Uint8ClampedArray[];
  try {
    if (compression === 0) {
      allPlanes = decodeRawPlanes(view, offset, channels, planeBytes);
    } else {
      allPlanes = decodeRlePlanes(view, offset, channels, height, planeBytes);
    }
  } catch {
    return null;
  }
  if (allPlanes.length !== channels) return null;

  // Drop R,G,B (first three); remaining are document alpha channels in order.
  const planes = allPlanes.slice(3).map((plane) => new Uint8ClampedArray(plane));
  return {
    width,
    height,
    planes,
    channelCount: channels,
    compression: compression,
  };
}

function decodeRawPlanes(
  view: DataView,
  offset: number,
  channels: number,
  planeBytes: number,
): Uint8ClampedArray[] {
  const needed = offset + channels * planeBytes;
  if (needed > view.byteLength) {
    throw new Error('raw plane data truncated');
  }
  const planes: Uint8ClampedArray[] = [];
  let cursor = offset;
  for (let c = 0; c < channels; c += 1) {
    const plane = new Uint8ClampedArray(planeBytes);
    for (let i = 0; i < planeBytes; i += 1) {
      plane[i] = view.getUint8(cursor + i);
    }
    planes.push(plane);
    cursor += planeBytes;
  }
  return planes;
}

/**
 * PackBits RLE as used by Photoshop Image Data (compression method 1).
 * Layout: row-byte-counts (2 bytes × height × channels), then RLE scanlines.
 */
function decodeRlePlanes(
  view: DataView,
  offset: number,
  channels: number,
  height: number,
  planeBytes: number,
): Uint8ClampedArray[] {
  const width = planeBytes / height;
  if (!Number.isInteger(width) || width < 1) {
    throw new Error('invalid plane dimensions');
  }
  const rowCountEntries = height * channels;
  if (offset + rowCountEntries * 2 > view.byteLength) {
    throw new Error('RLE row counts truncated');
  }
  const rowByteCounts: number[] = [];
  let cursor = offset;
  for (let i = 0; i < rowCountEntries; i += 1) {
    rowByteCounts.push(view.getUint16(cursor, false));
    cursor += 2;
  }

  const planes: Uint8ClampedArray[] = [];
  for (let c = 0; c < channels; c += 1) {
    const plane = new Uint8ClampedArray(planeBytes);
    for (let row = 0; row < height; row += 1) {
      const countIndex = c * height + row;
      const encodedLength = rowByteCounts[countIndex] ?? 0;
      if (cursor + encodedLength > view.byteLength) {
        throw new Error('RLE scanline truncated');
      }
      const decoded = decodePackBits(view, cursor, encodedLength, width);
      plane.set(decoded, row * width);
      cursor += encodedLength;
    }
    planes.push(plane);
  }
  return planes;
}

function decodePackBits(
  view: DataView,
  offset: number,
  encodedLength: number,
  expectedWidth: number,
): Uint8ClampedArray {
  const out = new Uint8ClampedArray(expectedWidth);
  let src = offset;
  const end = offset + encodedLength;
  let dst = 0;
  while (src < end && dst < expectedWidth) {
    const n = view.getInt8(src);
    src += 1;
    if (n >= 0) {
      // Copy next n+1 bytes literally.
      const count = n + 1;
      for (let i = 0; i < count && src < end && dst < expectedWidth; i += 1) {
        out[dst] = view.getUint8(src);
        dst += 1;
        src += 1;
      }
    } else if (n > -128) {
      // Replicate next byte (-n+1) times.
      const count = 1 - n;
      if (src >= end) break;
      const value = view.getUint8(src);
      src += 1;
      for (let i = 0; i < count && dst < expectedWidth; i += 1) {
        out[dst] = value;
        dst += 1;
      }
    }
    // n === -128 is a no-op per PackBits.
  }
  if (dst !== expectedWidth) {
    throw new Error('RLE row width mismatch');
  }
  return out;
}

/**
 * Builds a minimal synthetic 8-bit RGB PSD buffer with raw planar channels.
 * Used by unit tests; not a general PSD writer.
 */
export function buildSyntheticRawRgbPsd(options: {
  readonly width: number;
  readonly height: number;
  /** Full channel planes including R,G,B then alphas (length = 3 + alphaCount). */
  readonly planes: readonly ArrayLike<number>[];
}): ArrayBuffer {
  return buildSyntheticRgbPsd({ ...options, compression: 0 });
}

/**
 * Builds a minimal synthetic 8-bit RGB PSD with PackBits RLE (compression 1)
 * planar image data. Used to prove multi-plane alpha RLE decode.
 */
export function buildSyntheticRleRgbPsd(options: {
  readonly width: number;
  readonly height: number;
  readonly planes: readonly ArrayLike<number>[];
}): ArrayBuffer {
  return buildSyntheticRgbPsd({ ...options, compression: 1 });
}

function buildSyntheticRgbPsd(options: {
  readonly width: number;
  readonly height: number;
  readonly planes: readonly ArrayLike<number>[];
  readonly compression: 0 | 1;
}): ArrayBuffer {
  const { width, height, planes, compression } = options;
  const channels = planes.length;
  const planeBytes = width * height;
  for (const plane of planes) {
    if (plane.length !== planeBytes) {
      throw new Error('synthetic plane length mismatch');
    }
  }

  let imagePayload: Uint8Array;
  if (compression === 0) {
    imagePayload = new Uint8Array(channels * planeBytes);
    let cursor = 0;
    for (const plane of planes) {
      for (let i = 0; i < planeBytes; i += 1) {
        imagePayload[cursor + i] = plane[i] ?? 0;
      }
      cursor += planeBytes;
    }
  } else {
    // Row byte counts (2 × height × channels) + PackBits scanlines.
    const encodedRows: Uint8Array[] = [];
    const rowCounts: number[] = [];
    for (const plane of planes) {
      for (let row = 0; row < height; row += 1) {
        const rowBytes = new Uint8Array(width);
        for (let x = 0; x < width; x += 1) {
          rowBytes[x] = plane[row * width + x] ?? 0;
        }
        const encoded = encodePackBitsRow(rowBytes);
        encodedRows.push(encoded);
        rowCounts.push(encoded.length);
      }
    }
    const countsBytes = rowCounts.length * 2;
    const dataBytes = rowCounts.reduce((sum, n) => sum + n, 0);
    imagePayload = new Uint8Array(countsBytes + dataBytes);
    const view = new DataView(imagePayload.buffer);
    let cursor = 0;
    for (const count of rowCounts) {
      view.setUint16(cursor, count, false);
      cursor += 2;
    }
    for (const row of encodedRows) {
      imagePayload.set(row, cursor);
      cursor += row.length;
    }
  }

  const total = 26 + 4 + 4 + 4 + 2 + imagePayload.length;
  const buffer = new ArrayBuffer(total);
  const view = new DataView(buffer);
  // 8BPS
  view.setUint8(0, 0x38);
  view.setUint8(1, 0x42);
  view.setUint8(2, 0x50);
  view.setUint8(3, 0x53);
  view.setUint16(4, 1, false); // version
  view.setUint16(12, channels, false);
  view.setUint32(14, height, false);
  view.setUint32(18, width, false);
  view.setUint16(22, 8, false); // depth
  view.setUint16(24, 3, false); // RGB
  let offset = 26;
  view.setUint32(offset, 0, false); // color mode data
  offset += 4;
  view.setUint32(offset, 0, false); // image resources
  offset += 4;
  view.setUint32(offset, 0, false); // layer/mask info
  offset += 4;
  view.setUint16(offset, compression, false);
  offset += 2;
  new Uint8Array(buffer).set(imagePayload, offset);
  return buffer;
}

/** Minimal PackBits encoder for a single scanline (test helper). */
function encodePackBitsRow(row: Uint8Array): Uint8Array {
  // Literal copy of the whole row as one or more PackBits packets (max 128).
  const chunks: number[] = [];
  let i = 0;
  while (i < row.length) {
    const remaining = row.length - i;
    const take = Math.min(128, remaining);
    chunks.push(take - 1); // n = take-1 means copy take bytes
    for (let j = 0; j < take; j += 1) {
      chunks.push(row[i + j] ?? 0);
    }
    i += take;
  }
  return Uint8Array.from(chunks);
}
