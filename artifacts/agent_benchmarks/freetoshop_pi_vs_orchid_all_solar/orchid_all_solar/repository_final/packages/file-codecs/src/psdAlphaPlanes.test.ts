import { describe, expect, it } from 'vitest';
import {
  buildSyntheticRawRgbPsd,
  buildSyntheticRleRgbPsd,
  decodePsdAlphaPlanes,
} from './psdAlphaPlanes.js';
import { importPsdBuffer } from './psd.js';
import type { Psd } from 'ag-psd';

function makePlanes(width: number, height: number, alphaFills: readonly number[]) {
  const planeBytes = width * height;
  const rgb = [
    new Uint8Array(planeBytes).fill(10),
    new Uint8Array(planeBytes).fill(20),
    new Uint8Array(planeBytes).fill(30),
  ];
  const alphas = alphaFills.map((fill) => new Uint8Array(planeBytes).fill(fill));
  return [...rgb, ...alphas];
}

describe('decodePsdAlphaPlanes', () => {
  it('recovers multiple raw alpha planes after RGB', () => {
    const width = 4;
    const height = 2;
    const planeBytes = width * height;
    const a2 = new Uint8Array(planeBytes);
    for (let i = 0; i < planeBytes; i += 1) a2[i] = i * 20;
    const planes = makePlanes(width, height, [111]);
    planes.push(a2);
    const buffer = buildSyntheticRawRgbPsd({ width, height, planes });
    const decoded = decodePsdAlphaPlanes(buffer);
    expect(decoded).not.toBeNull();
    expect(decoded!.channelCount).toBe(5);
    expect(decoded!.compression).toBe(0);
    expect(decoded!.planes).toHaveLength(2);
    expect([...decoded!.planes[0]!]).toEqual(Array(planeBytes).fill(111));
    expect([...decoded!.planes[1]!]).toEqual([...a2]);
  });

  it('recovers multiple PackBits RLE alpha planes after RGB', () => {
    const width = 8;
    const height = 3;
    const planeBytes = width * height;
    const planes = makePlanes(width, height, [200, 55]);
    const buffer = buildSyntheticRleRgbPsd({ width, height, planes });
    const decoded = decodePsdAlphaPlanes(buffer);
    expect(decoded).not.toBeNull();
    expect(decoded!.compression).toBe(1);
    expect(decoded!.planes).toHaveLength(2);
    expect([...decoded!.planes[0]!]).toEqual(Array(planeBytes).fill(200));
    expect([...decoded!.planes[1]!]).toEqual(Array(planeBytes).fill(55));
  });

  it('returns null for insufficient channels', () => {
    const width = 2;
    const height = 2;
    const planeBytes = width * height;
    const buffer = buildSyntheticRawRgbPsd({
      width,
      height,
      planes: [
        new Uint8Array(planeBytes).fill(1),
        new Uint8Array(planeBytes).fill(2),
        new Uint8Array(planeBytes).fill(3),
      ],
    });
    expect(decodePsdAlphaPlanes(buffer)).toBeNull();
  });
});

describe('importPsdBuffer multi-plane alpha', () => {
  it('imports multi-plane alphas from corpus-style bytes without injects or names', () => {
    // Repository-safe corpus entry: raw multi-alpha RGB with no freetoshop injects.
    // Names are invented when image resources omit alphaChannelNames.
    const width = 8;
    const height = 4;
    const planeBytes = width * height;
    const a1 = new Uint8Array(planeBytes).fill(180);
    const a2 = new Uint8Array(planeBytes).fill(40);
    const buffer = buildSyntheticRawRgbPsd({
      width,
      height,
      planes: [
        new Uint8Array(planeBytes).fill(10),
        new Uint8Array(planeBytes).fill(20),
        new Uint8Array(planeBytes).fill(30),
        a1,
        a2,
      ],
    });
    // Real parse would fail on minimal synthetic (no layer structure); use stub
    // parse that only provides geometry/channel count — coverage comes from decoder.
    const source: Psd = {
      width,
      height,
      channels: 5,
      children: [],
      imageData: {
        width,
        height,
        data: new Uint8ClampedArray(width * height * 4).fill(255),
      },
    };
    const result = importPsdBuffer(buffer, { parse: () => source });
    expect(result.report.importedAlphaChannels).toBe(2);
    expect(result.report.importedAlphaChannelsWithCoverage).toBe(2);
    expect(result.document.channels.map((channel) => channel.name)).toEqual(['Alpha 1', 'Alpha 2']);
    expect([...result.document.channels[0]!.coverageStore.toContiguous()]).toEqual(
      Array(planeBytes).fill(180),
    );
    expect([...result.document.channels[1]!.coverageStore.toContiguous()]).toEqual(
      Array(planeBytes).fill(40),
    );
    const alphaRecords = result.document.preservation?.alphaChannels;
    expect(Array.isArray(alphaRecords)).toBe(true);
    expect(
      (alphaRecords as Array<{ coverageSource?: string }>).every(
        (record) => record.coverageSource === 'multi-plane',
      ),
    ).toBe(true);
  });

  it('imports two alpha coverages from synthetic multi-channel PSD bytes', () => {
    const width = 3;
    const height = 2;
    const planeBytes = width * height;
    const a1 = new Uint8Array(planeBytes).fill(200);
    const a2 = new Uint8Array(planeBytes).fill(50);
    const buffer = buildSyntheticRawRgbPsd({
      width,
      height,
      planes: [
        new Uint8Array(planeBytes).fill(1),
        new Uint8Array(planeBytes).fill(2),
        new Uint8Array(planeBytes).fill(3),
        a1,
        a2,
      ],
    });
    const source: Psd = {
      width,
      height,
      channels: 5,
      children: [],
      imageResources: {
        alphaChannelNames: ['Selection', 'Spot-ish'],
      },
      // Minimal imageData so ag-psd path is not required for RGB content.
      imageData: {
        width,
        height,
        data: new Uint8ClampedArray(width * height * 4).fill(255),
      },
    };
    const result = importPsdBuffer(buffer, { parse: () => source });
    expect(result.report.importedAlphaChannels).toBe(2);
    expect(result.report.importedAlphaChannelsWithCoverage).toBe(2);
    expect(result.document.channels).toHaveLength(2);
    expect([...result.document.channels[0]!.coverageStore.toContiguous()]).toEqual(
      Array(planeBytes).fill(200),
    );
    expect([...result.document.channels[1]!.coverageStore.toContiguous()]).toEqual(
      Array(planeBytes).fill(50),
    );
    expect(result.document.preservation?.alphaChannels).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          name: 'Selection',
          coverageSource: 'multi-plane',
          hasCoverage: true,
          multiPlaneCompression: 0,
        }),
        expect.objectContaining({
          name: 'Spot-ish',
          coverageSource: 'multi-plane',
          hasCoverage: true,
        }),
      ]),
    );
  });
});
