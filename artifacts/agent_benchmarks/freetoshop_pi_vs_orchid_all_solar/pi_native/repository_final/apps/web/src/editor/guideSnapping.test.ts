import { createDocument, createPixelLayer } from '@freetoshop/core-document';
import { describe, expect, it } from 'vitest';
import {
  collectSmartSnapEdges,
  snapDocumentPoint,
  snapThresholdInDocumentPixels,
} from './guideSnapping.js';

describe('snapDocumentPoint', () => {
  it('snaps to the nearest vertical and horizontal guides within threshold', () => {
    const result = snapDocumentPoint(
      { x: 11, y: 49 },
      {
        guides: [
          { id: 'v', direction: 'vertical', position: 10 },
          { id: 'h', direction: 'horizontal', position: 50 },
        ],
        threshold: 4,
      },
    );
    expect(result).toEqual({
      x: 10,
      y: 50,
      snappedX: true,
      snappedY: true,
      activeSmartEdges: [],
    });
  });

  it('does not snap when outside threshold', () => {
    const result = snapDocumentPoint(
      { x: 20, y: 20 },
      {
        guides: [{ id: 'v', direction: 'vertical', position: 10 }],
        threshold: 2,
      },
    );
    expect(result).toEqual({
      x: 20,
      y: 20,
      snappedX: false,
      snappedY: false,
      activeSmartEdges: [],
    });
  });

  it('snaps to grid when guides miss', () => {
    const result = snapDocumentPoint(
      { x: 17, y: 31 },
      {
        guides: [],
        grid: { horizontal: 16, vertical: 16 },
        threshold: 4,
      },
    );
    expect(result).toEqual({
      x: 16,
      y: 32,
      snappedX: true,
      snappedY: true,
      activeSmartEdges: [],
    });
  });

  it('prefers guides over grid when both qualify', () => {
    const result = snapDocumentPoint(
      { x: 15, y: 15 },
      {
        guides: [{ id: 'v', direction: 'vertical', position: 14 }],
        grid: { horizontal: 16, vertical: 16 },
        threshold: 4,
      },
    );
    expect(result.x).toBe(14);
    expect(result.snappedX).toBe(true);
  });

  it('respects snapToGuides / snapToGrid flags', () => {
    const noGuides = snapDocumentPoint(
      { x: 14, y: 14 },
      {
        guides: [{ id: 'v', direction: 'vertical', position: 10 }],
        grid: { horizontal: 16, vertical: 16 },
        threshold: 4,
        snapToGuides: false,
        snapToGrid: true,
      },
    );
    expect(noGuides.x).toBe(16);
    expect(noGuides.y).toBe(16);
    const none = snapDocumentPoint(
      { x: 14, y: 14 },
      {
        guides: [{ id: 'v', direction: 'vertical', position: 10 }],
        grid: { horizontal: 16, vertical: 16 },
        threshold: 4,
        snapToGuides: false,
        snapToGrid: false,
      },
    );
    expect(none).toEqual({
      x: 14,
      y: 14,
      snappedX: false,
      snappedY: false,
      activeSmartEdges: [],
    });
  });
});

describe('snapThresholdInDocumentPixels', () => {
  it('converts screen pixels by zoom', () => {
    expect(snapThresholdInDocumentPixels(8, 2)).toBe(4);
    expect(snapThresholdInDocumentPixels(8, 0.5)).toBe(16);
    expect(snapThresholdInDocumentPixels(8, 0)).toBe(8);
  });
});

describe('collectSmartSnapEdges + smart snap', () => {
  it('snaps to another layer edge and canvas center', () => {
    const document = createDocument({
      id: 'smart',
      name: 'Smart',
      width: 100,
      height: 80,
      layers: [
        createPixelLayer({
          id: 'a',
          name: 'A',
          width: 20,
          height: 10,
          pixels: new Uint8ClampedArray(20 * 10 * 4).fill(200),
          transform: { a: 1, b: 0, c: 0, d: 1, e: 30, f: 20 },
        }),
        createPixelLayer({
          id: 'b',
          name: 'B',
          width: 10,
          height: 10,
          pixels: new Uint8ClampedArray(10 * 10 * 4).fill(100),
          transform: { a: 1, b: 0, c: 0, d: 1, e: 0, f: 0 },
        }),
      ],
    });
    const edges = collectSmartSnapEdges(document, {
      excludeLayerIds: new Set(['b']),
    });
    // Layer A left=30, right=50, top=20, bottom=30; canvas center 50/40
    expect(edges.some((edge) => edge.axis === 'x' && edge.position === 30)).toBe(true);
    expect(edges.some((edge) => edge.axis === 'x' && edge.position === 50)).toBe(true);
    expect(edges.some((edge) => edge.axis === 'y' && edge.position === 20)).toBe(true);

    const snapped = snapDocumentPoint(
      { x: 31, y: 21 },
      { smartEdges: edges, threshold: 4, snapToGuides: false, snapToGrid: false },
    );
    expect(snapped.x).toBe(30);
    expect(snapped.y).toBe(20);
    expect(snapped.snappedX).toBe(true);
    expect(snapped.snappedY).toBe(true);
    expect(snapped.activeSmartEdges).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ axis: 'x', position: 30 }),
        expect.objectContaining({ axis: 'y', position: 20 }),
      ]),
    );
  });
});
