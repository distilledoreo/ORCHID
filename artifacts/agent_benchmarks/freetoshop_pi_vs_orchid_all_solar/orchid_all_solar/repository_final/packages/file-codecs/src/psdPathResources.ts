import type { PathCommand } from '@freetoshop/core-document';

/**
 * Decoded document-level path from PSD image resources 2000–2998.
 * Coordinates are in document pixels (converted from 8.24 fixed-point fractions).
 */
export interface DecodedPsdPathResource {
  readonly resourceId: number;
  readonly name: string;
  readonly open: boolean;
  readonly commands: readonly PathCommand[];
  readonly knotCount: number;
}

/**
 * Scans a PSD file's Image Resources section for path resources (IDs 2000–2998)
 * and decodes bezier path records into PathCommands.
 *
 * Adobe path resources use 26-byte records and 8.24 fixed-point coordinates
 * as fractions of document width/height (same layout as vector-mask `vmsk`).
 */
export function decodePsdPathResources(
  buffer: ArrayBuffer,
  width: number,
  height: number,
): readonly DecodedPsdPathResource[] {
  if (width < 1 || height < 1 || buffer.byteLength < 26) return [];
  const view = new DataView(buffer);
  // Signature 8BPS already validated by caller.
  let offset = 26; // end of file header
  if (offset + 4 > view.byteLength) return [];

  // Color mode data section
  const colorModeLength = view.getUint32(offset, false);
  offset += 4 + colorModeLength;
  if (offset + 4 > view.byteLength) return [];

  // Image resources section
  const resourcesLength = view.getUint32(offset, false);
  offset += 4;
  const resourcesEnd = offset + resourcesLength;
  if (resourcesEnd > view.byteLength || resourcesLength < 0) return [];

  const results: DecodedPsdPathResource[] = [];
  while (offset + 12 <= resourcesEnd) {
    const sig0 = view.getUint8(offset);
    const sig1 = view.getUint8(offset + 1);
    const sig2 = view.getUint8(offset + 2);
    const sig3 = view.getUint8(offset + 3);
    // 8BIM
    if (sig0 !== 0x38 || sig1 !== 0x42 || sig2 !== 0x49 || sig3 !== 0x4d) {
      // Some files use 8B64 for large resources; stop cleanly.
      break;
    }
    offset += 4;
    const resourceId = view.getUint16(offset, false);
    offset += 2;

    // Pascal name, padded to even length including length byte
    if (offset >= resourcesEnd) break;
    const nameLen = view.getUint8(offset);
    offset += 1;
    let name = '';
    if (nameLen > 0 && offset + nameLen <= resourcesEnd) {
      const bytes = new Uint8Array(buffer, offset, nameLen);
      name = new TextDecoder('latin1').decode(bytes);
      offset += nameLen;
    }
    // Pad name field to even size (length byte + name)
    if ((1 + nameLen) % 2 === 1) offset += 1;

    if (offset + 4 > resourcesEnd) break;
    const dataSize = view.getUint32(offset, false);
    offset += 4;
    if (offset + dataSize > resourcesEnd || dataSize < 0) break;

    const isPathResource = resourceId >= 2000 && resourceId <= 2998;
    if (isPathResource && dataSize >= 26) {
      const data = new DataView(buffer, offset, dataSize);
      const decoded = decodePathResourceData(data, width, height, resourceId, name);
      if (decoded) results.push(decoded);
    }

    offset += dataSize;
    if (dataSize % 2 === 1) offset += 1; // pad data to even
  }

  return results;
}

function decodePathResourceData(
  data: DataView,
  width: number,
  height: number,
  resourceId: number,
  resourceName: string,
): DecodedPsdPathResource | null {
  const commands: PathCommand[] = [];
  let open = false;
  let knotCount = 0;
  let subpathOpen = false;
  let firstAnchor: { x: number; y: number } | null = null;
  let prevNext: { x: number; y: number } | null = null;
  let offset = 0;

  while (offset + 26 <= data.byteLength) {
    const selector = data.getUint16(offset, false);
    offset += 2;

    if (selector === 0 || selector === 3) {
      // Subpath length record
      const count = data.getUint16(offset, false);
      offset += 2;
      // boolOp + flags + padding
      offset += 22;
      open = selector === 3;
      subpathOpen = false;
      firstAnchor = null;
      prevNext = null;
      knotCount += count;
      continue;
    }

    if (selector === 1 || selector === 2 || selector === 4 || selector === 5) {
      // Bezier knot: y0,x0,y1,x1,y2,x2 as 8.24 fixed
      const y0 = readFixedPath(data, offset) * height;
      const x0 = readFixedPath(data, offset + 4) * width;
      const y1 = readFixedPath(data, offset + 8) * height;
      const x1 = readFixedPath(data, offset + 12) * width;
      const y2 = readFixedPath(data, offset + 16) * height;
      const x2 = readFixedPath(data, offset + 20) * width;
      offset += 24;

      if (!subpathOpen) {
        commands.push({ type: 'move', x: x1, y: y1 });
        firstAnchor = { x: x1, y: y1 };
        prevNext = { x: x2, y: y2 };
        subpathOpen = true;
      } else {
        const c1 = prevNext ?? { x: x1, y: y1 };
        commands.push({
          type: 'cubic',
          control1X: c1.x,
          control1Y: c1.y,
          control2X: x0,
          control2Y: y0,
          x: x1,
          y: y1,
        });
        prevNext = { x: x2, y: y2 };
      }
      continue;
    }

    if (selector === 6 || selector === 7 || selector === 8) {
      offset += 24;
      continue;
    }

    // Unknown selector — stop this resource to avoid corrupt loops
    break;
  }

  // Close subpath when path is closed and we have a first anchor.
  if (!open && firstAnchor && prevNext) {
    const last = commands[commands.length - 1];
    if (last.type !== 'close') {
      // Close cubic back to first if needed is handled by close command for fill;
      // emit close for PathCommand consumers.
      commands.push({ type: 'close' });
    }
  }

  if (commands.length === 0) return null;
  const name =
    resourceName.trim() ||
    (resourceId === 2000 ? 'Work Path' : `Path ${String(resourceId - 2000)}`);
  return {
    resourceId,
    name,
    open,
    commands,
    knotCount,
  };
}

function readFixedPath(data: DataView, offset: number): number {
  // 8.24 fixed-point, big-endian
  return data.getInt32(offset, false) / (1 << 24);
}
