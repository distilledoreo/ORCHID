import {
  createSelectionMask,
  createAlphaChannel,
  createDocument,
  createGroupLayer,
  createPixelLayer,
  createRasterMask,
  createTextLayer,
} from '@freetoshop/core-document';
import { describe, expect, it } from 'vitest';
import { TiledPixelStore } from '@freetoshop/pixel-storage';
import {
  decodeNativeDocument,
  encodeNativeDocument,
  hasNativeDocumentSignature,
  NATIVE_DOCUMENT_FORMAT_VERSION,
  serializeDocument,
} from './index.js';

const HEADER_BYTES = 49;

function casLayout(encoded: Uint8Array) {
  const view = new DataView(encoded.buffer, encoded.byteOffset, encoded.byteLength);
  const manifestLength = view.getUint32(HEADER_BYTES, false);
  const manifestOffset = HEADER_BYTES + 4;
  const resourceCountOffset = manifestOffset + manifestLength;
  const resourceCount = view.getUint32(resourceCountOffset, false);
  return { manifestLength, manifestOffset, resourceCountOffset, resourceCount };
}

async function refreshOuterChecksum(encoded: Uint8Array): Promise<void> {
  const checksum = new Uint8Array(
    await crypto.subtle.digest('SHA-256', encoded.subarray(HEADER_BYTES)),
  );
  encoded.set(checksum, 17);
}

async function legacyV1(document: ReturnType<typeof editableDocument>): Promise<Uint8Array> {
  const payload = new TextEncoder().encode(serializeDocument(document));
  const encoded = new Uint8Array(HEADER_BYTES + payload.byteLength);
  encoded.set(new TextEncoder().encode('FREETOSHOP\0'));
  const view = new DataView(encoded.buffer);
  view.setUint16(11, 1, false);
  view.setUint32(13, payload.byteLength, false);
  encoded.set(new Uint8Array(await crypto.subtle.digest('SHA-256', payload)), 17);
  encoded.set(payload, HEADER_BYTES);
  return encoded;
}

function editableDocument() {
  return createDocument({
    id: 'native-document',
    name: 'Native document',
    width: 2,
    height: 2,
    selection: createSelectionMask({
      width: 2,
      height: 2,
      coverage: new Uint8ClampedArray([255, 128, 0, 255]),
    }),
    channels: [
      createAlphaChannel({
        id: 'alpha-1',
        name: 'Saved selection',
        width: 2,
        height: 2,
        coverage: [0, 64, 128, 255],
      }),
    ],
    layers: [
      createPixelLayer({
        id: 'pixels',
        name: 'Pixels',
        width: 2,
        height: 2,
        pixels: [255, 0, 0, 255, 0, 255, 0, 255, 0, 0, 255, 255, 0, 0, 0, 0],
        opacity: 0.75,
        blendMode: 'multiply',
        rasterMask: createRasterMask(2, 2, [255, 255, 128, 0], { density: 0.8 }),
      }),
      createGroupLayer({
        id: 'group',
        name: 'Editable group',
        children: [
          createTextLayer({
            id: 'text',
            name: 'Text',
            text: 'Local document',
            width: 120,
            height: 40,
          }),
        ],
      }),
    ],
  });
}

describe('native document format', () => {
  it('round-trips editable document state through a signed checksummed container', async () => {
    const source = editableDocument();
    const encoded = await encodeNativeDocument(source);
    expect(hasNativeDocumentSignature(encoded.buffer as ArrayBuffer)).toBe(true);
    expect(new DataView(encoded.buffer).getUint16(11, false)).toBe(2);
    await expect(decodeNativeDocument(encoded)).resolves.toEqual(source);
  });

  it('deduplicates identical binary tiles by content hash and preserves COW edits', async () => {
    let store = TiledPixelStore.empty(512, 512);
    for (const [x, y] of [
      [10, 10],
      [266, 10],
      [10, 266],
      [266, 266],
    ] as const) {
      store = store.write({
        x,
        y,
        width: 1,
        height: 1,
        pixels: new Uint8ClampedArray([10, 20, 30, 255]),
      }).store;
    }
    const shared = createDocument({
      id: 'cas-document',
      name: 'CAS document',
      width: 512,
      height: 512,
      layers: [
        createPixelLayer({ id: 'a', name: 'A', width: 512, height: 512, pixelStore: store }),
        createPixelLayer({ id: 'b', name: 'B', width: 512, height: 512, pixelStore: store }),
      ],
    });
    const first = await encodeNativeDocument(shared);
    const firstLayout = casLayout(first);
    expect(firstLayout.resourceCount).toBe(1);
    const manifest = new TextDecoder().decode(
      first.subarray(
        firstLayout.manifestOffset,
        firstLayout.manifestOffset + firstLayout.manifestLength,
      ),
    );
    expect(manifest).toContain('cas-tiles-v1');
    expect(manifest).not.toContain('"data"');

    const editedStore = store.write({
      x: 10,
      y: 10,
      width: 1,
      height: 1,
      pixels: new Uint8ClampedArray([200, 100, 50, 255]),
    }).store;
    const edited = createDocument({
      id: shared.id,
      name: shared.name,
      width: shared.canvas.width,
      height: shared.canvas.height,
      layers: [
        createPixelLayer({ id: 'a', name: 'A', width: 512, height: 512, pixelStore: store }),
        createPixelLayer({
          id: 'b',
          name: 'B',
          width: 512,
          height: 512,
          pixelStore: editedStore,
        }),
      ],
    });
    const second = await encodeNativeDocument(edited);
    expect(casLayout(second).resourceCount).toBe(2);
    await expect(decodeNativeDocument(second)).resolves.toEqual(edited);
  });

  it('opens legacy v1 JSON containers', async () => {
    const source = editableDocument();
    await expect(decodeNativeDocument(await legacyV1(source))).resolves.toEqual(source);
  });

  it('reports monotonic checksum and per-resource decode progress', async () => {
    const source = editableDocument();
    const encoded = await encodeNativeDocument(source);
    const progress: number[] = [];
    await expect(
      decodeNativeDocument(encoded, { onProgress: (value) => progress.push(value) }),
    ).resolves.toEqual(source);
    expect(progress[0]).toBe(0);
    expect(progress[1]).toBe(0.1);
    expect(progress.at(-1)).toBe(1);
    expect(progress.some((value) => value > 0.1 && value < 1)).toBe(true);
    expect(
      progress.every((value, index) => index === 0 || value >= (progress[index - 1] ?? 0)),
    ).toBe(true);
  });

  it('cancels CAS decoding between verified resources before manifest hydration', async () => {
    const encoded = await encodeNativeDocument(editableDocument());
    const controller = new AbortController();
    const progress: number[] = [];
    const pending = decodeNativeDocument(encoded, {
      signal: controller.signal,
      onProgress: (value) => {
        progress.push(value);
        if (value > 0.1 && value < 1) controller.abort();
      },
    });
    await expect(pending).rejects.toMatchObject({ name: 'AbortError' });
    expect(progress.at(-1)).toBeGreaterThan(0.1);
    expect(progress).not.toContain(1);
  });

  it('rejects a CAS resource whose inner content hash is invalid', async () => {
    const encoded = await encodeNativeDocument(editableDocument());
    const layout = casLayout(encoded);
    expect(layout.resourceCount).toBeGreaterThan(0);
    const firstResourceData = layout.resourceCountOffset + 4 + 32 + 4;
    encoded[firstResourceData] = (encoded[firstResourceData] ?? 0) ^ 1;
    await refreshOuterChecksum(encoded);
    await expect(decodeNativeDocument(encoded)).rejects.toThrow('resource hash does not match');
  });

  it('rejects payload tampering by checksum', async () => {
    const encoded = await encodeNativeDocument(editableDocument());
    encoded[encoded.length - 1] = (encoded[encoded.length - 1] ?? 0) ^ 1;
    await expect(decodeNativeDocument(encoded)).rejects.toThrow('checksum does not match');
  });

  it('rejects invalid signatures, versions, lengths, and truncated headers', async () => {
    const encoded = await encodeNativeDocument(editableDocument());

    const invalidSignature = encoded.slice();
    invalidSignature[0] = 0;
    await expect(decodeNativeDocument(invalidSignature)).rejects.toThrow(
      'does not have a FreetoShop document signature',
    );

    const unsupportedVersion = encoded.slice();
    new DataView(unsupportedVersion.buffer).setUint16(
      11,
      NATIVE_DOCUMENT_FORMAT_VERSION + 1,
      false,
    );
    await expect(decodeNativeDocument(unsupportedVersion)).rejects.toThrow(
      'Unsupported FreetoShop file format version',
    );

    const invalidLength = encoded.slice();
    new DataView(invalidLength.buffer).setUint32(13, 1, false);
    await expect(decodeNativeDocument(invalidLength)).rejects.toThrow('payload length is invalid');

    await expect(decodeNativeDocument(encoded.subarray(0, 12))).rejects.toThrow(
      'header is truncated',
    );
  });
});
