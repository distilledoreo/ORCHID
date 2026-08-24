import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import { decodePsdPathResources } from './psdPathResources.js';

const fixtures = fileURLToPath(new URL('../test-fixtures/psd/', import.meta.url));

/** Write a big-endian 8.24 fixed-point path coordinate (fraction 0–1). */
function writeFixedPath(view: DataView, offset: number, fraction: number): number {
  view.setInt32(offset, Math.round(fraction * (1 << 24)), false);
  return offset + 4;
}

/** Closed subpath length record (26 bytes). */
function writeClosedLength(view: DataView, offset: number, knotCount: number): number {
  view.setUint16(offset, 0, false); // closed length
  offset += 2;
  view.setUint16(offset, knotCount, false);
  offset += 2;
  view.setInt16(offset, -1, false); // boolOp
  offset += 2;
  view.setUint16(offset, 1, false); // flags
  offset += 2;
  // 18 bytes pad
  for (let i = 0; i < 18; i += 1) view.setUint8(offset + i, 0);
  return offset + 18;
}

/** Linked closed knot record (26 bytes). points: [prevCtrl, anchor, nextCtrl] as fractions {x,y}. */
function writeLinkedKnot(
  view: DataView,
  offset: number,
  prev: { x: number; y: number },
  anchor: { x: number; y: number },
  next: { x: number; y: number },
): number {
  view.setUint16(offset, 1, false); // closed linked knot
  offset += 2;
  offset = writeFixedPath(view, offset, prev.y);
  offset = writeFixedPath(view, offset, prev.x);
  offset = writeFixedPath(view, offset, anchor.y);
  offset = writeFixedPath(view, offset, anchor.x);
  offset = writeFixedPath(view, offset, next.y);
  offset = writeFixedPath(view, offset, next.x);
  return offset;
}

/**
 * Minimal PSD bytes with header + empty color mode + one path resource 2000.
 * Layer/image sections are omitted (decoder stops after resources).
 */
function buildPsdWithWorkPath(width: number, height: number): ArrayBuffer {
  // Path data: length record + 3 knots
  const pathData = new ArrayBuffer(26 * 4);
  const pathView = new DataView(pathData);
  let po = 0;
  po = writeClosedLength(pathView, po, 3);
  // Triangle-ish anchors at 0.25, 0.5, 0.75 fractions
  po = writeLinkedKnot(
    pathView,
    po,
    { x: 0.25, y: 0.25 },
    { x: 0.25, y: 0.25 },
    { x: 0.3, y: 0.25 },
  );
  po = writeLinkedKnot(pathView, po, { x: 0.45, y: 0.5 }, { x: 0.5, y: 0.5 }, { x: 0.55, y: 0.5 });
  writeLinkedKnot(pathView, po, { x: 0.7, y: 0.75 }, { x: 0.75, y: 0.75 }, { x: 0.25, y: 0.25 });

  // Resource block: 8BIM + id + name(0) + pad + size + data
  // name empty: length 0, need pad to even for (1+0)=1 → pad 1
  const pathBytes = new Uint8Array(pathData);
  const resourceBodySize = 4 + 2 + 1 + 1 + 4 + pathBytes.length; // sig+id+name+pad+size+data
  const resourcesLength = resourceBodySize;

  // Header 26 + colorMode(4+0) + resources(4+resourcesLength)
  const total = 26 + 4 + 4 + resourcesLength;
  const buffer = new ArrayBuffer(total);
  const view = new DataView(buffer);
  const bytes = new Uint8Array(buffer);

  // Signature 8BPS
  bytes[0] = 0x38;
  bytes[1] = 0x42;
  bytes[2] = 0x50;
  bytes[3] = 0x53;
  view.setUint16(4, 1, false); // version
  // 6 reserved zeros
  view.setUint16(12, 3, false); // channels
  view.setUint32(14, height, false);
  view.setUint32(18, width, false);
  view.setUint16(22, 8, false); // depth
  view.setUint16(24, 3, false); // RGB

  let offset = 26;
  view.setUint32(offset, 0, false); // color mode length
  offset += 4;
  view.setUint32(offset, resourcesLength, false);
  offset += 4;

  // Resource
  bytes[offset++] = 0x38; // 8
  bytes[offset++] = 0x42; // B
  bytes[offset++] = 0x49; // I
  bytes[offset++] = 0x4d; // M
  view.setUint16(offset, 2000, false); // Work Path
  offset += 2;
  bytes[offset++] = 0; // name length
  bytes[offset++] = 0; // pad to even
  view.setUint32(offset, pathBytes.length, false);
  offset += 4;
  bytes.set(pathBytes, offset);

  return buffer;
}

describe('decodePsdPathResources', () => {
  it('decodes a closed work path from the corpus fixture file', () => {
    // Committed binary corpus (no runtime builder) — 100×80 with resource 2000.
    const bytes = readFileSync(`${fixtures}work-path-resource.psd`);
    const buffer = bytes.buffer.slice(
      bytes.byteOffset,
      bytes.byteOffset + bytes.byteLength,
    ) as ArrayBuffer;
    const paths = decodePsdPathResources(buffer, 100, 80);
    expect(paths.length).toBe(1);
    expect(paths[0]).toMatchObject({
      resourceId: 2000,
      name: 'Work Path',
      open: false,
      knotCount: 3,
    });
    expect(paths[0]?.commands[0]).toMatchObject({
      type: 'move',
      x: expect.closeTo(25, 0),
      y: expect.closeTo(20, 0),
    });
  });

  it('decodes a closed work path from image resource 2000', () => {
    const width = 100;
    const height = 80;
    const buffer = buildPsdWithWorkPath(width, height);
    const paths = decodePsdPathResources(buffer, width, height);
    expect(paths.length).toBe(1);
    const path = paths[0]!;
    expect(path.resourceId).toBe(2000);
    expect(path.name).toBe('Work Path');
    expect(path.open).toBe(false);
    expect(path.knotCount).toBe(3);
    expect(path.commands.length).toBeGreaterThanOrEqual(2);
    expect(path.commands[0]).toMatchObject({ type: 'move' });
    // First anchor at 25% of canvas
    expect(path.commands[0]).toMatchObject({
      type: 'move',
      x: expect.closeTo(25, 0),
      y: expect.closeTo(20, 0),
    });
    // Closed path ends with close
    expect(path.commands.some((command) => command.type === 'close')).toBe(true);
  });

  it('returns empty for buffers without path resources', () => {
    const buffer = new ArrayBuffer(40);
    const view = new DataView(buffer);
    const bytes = new Uint8Array(buffer);
    bytes[0] = 0x38;
    bytes[1] = 0x42;
    bytes[2] = 0x50;
    bytes[3] = 0x53;
    view.setUint16(4, 1, false);
    view.setUint16(12, 3, false);
    view.setUint32(14, 2, false);
    view.setUint32(18, 2, false);
    view.setUint16(22, 8, false);
    view.setUint16(24, 3, false);
    view.setUint32(26, 0, false); // color mode
    view.setUint32(30, 0, false); // no resources
    expect(decodePsdPathResources(buffer, 2, 2)).toEqual([]);
  });
});
