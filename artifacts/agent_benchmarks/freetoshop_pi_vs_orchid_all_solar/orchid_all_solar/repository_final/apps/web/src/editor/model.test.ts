import { createSelectionMask, createDocument } from '@freetoshop/core-document';
import { TiledCoverageStore } from '@freetoshop/pixel-storage';
import { describe, expect, it, vi } from 'vitest';
import {
  movePathHandle,
  parseSizeDraft,
  parseTransformDraft,
  selectionBounds,
  selectionRectangle,
} from './model.js';

describe('editor model helpers', () => {
  it('normalizes selection rectangles and finds nonzero mask bounds', () => {
    expect(selectionRectangle({ x: 8.8, y: 5.2 }, { x: 2.1, y: 9.01 })).toEqual({
      x: 2,
      y: 5,
      width: 7,
      height: 5,
    });
    const document = createDocument({
      id: 'selection',
      name: 'Selection',
      width: 4,
      height: 3,
      selection: createSelectionMask({
        width: 4,
        height: 3,
        coverage: new Uint8ClampedArray([0, 0, 0, 0, 0, 64, 255, 0, 0, 255, 0, 0]),
      }),
    });
    expect(selectionBounds(document)).toEqual({ x: 1, y: 1, width: 2, height: 2 });
  });

  it('reports sparse complement selections as full-canvas without enumerating implicit tiles', () => {
    const store = TiledCoverageStore.uniform(4096, 4096, 255, 256).write({
      x: 1024,
      y: 2048,
      width: 1,
      height: 1,
      coverage: new Uint8ClampedArray([0]),
    }).store;
    const chunks = vi.spyOn(store, 'coverageChunks');
    const document = createDocument({
      id: 'complement-bounds',
      name: 'Complement bounds',
      width: 4096,
      height: 4096,
      selection: createSelectionMask({ width: 4096, height: 4096, coverageStore: store }),
    });
    expect(selectionBounds(document)).toEqual({ x: 0, y: 0, width: 4096, height: 4096 });
    expect(chunks).not.toHaveBeenCalled();
  });

  it('moves path anchors with their adjacent cubic controls without mutating the source', () => {
    const geometry = {
      type: 'path' as const,
      width: 20,
      height: 20,
      cornerRadius: 0,
      commands: [
        { type: 'move' as const, x: 2, y: 3 },
        {
          type: 'cubic' as const,
          control1X: 4,
          control1Y: 5,
          control2X: 8,
          control2Y: 9,
          x: 10,
          y: 11,
        },
      ],
    };
    const moved = movePathHandle(geometry, 0, 'anchor', { x: 5, y: 7 });
    expect(moved.commands).toEqual([
      { type: 'move', x: 5, y: 7 },
      {
        type: 'cubic',
        control1X: 7,
        control1Y: 9,
        control2X: 8,
        control2Y: 9,
        x: 10,
        y: 11,
      },
    ]);
    expect(geometry.commands[0]).toEqual({ type: 'move', x: 2, y: 3 });
  });

  it('validates transform and bounded document-size drafts', () => {
    expect(parseTransformDraft({ a: '1', b: '0', c: '0', d: '1', e: '12.5', f: '-4' })).toEqual({
      a: 1,
      b: 0,
      c: 0,
      d: 1,
      e: 12.5,
      f: -4,
    });
    expect(parseTransformDraft({ a: '1', b: '2', c: '2', d: '4', e: '0', f: '0' })).toBeNull();
    expect(parseSizeDraft('1024', '768')).toEqual({ width: 1024, height: 768, error: null });
    expect(parseSizeDraft('0', '768').error).toContain('positive whole numbers');
    expect(parseSizeDraft('32769', '1').error).toContain('cannot exceed');
  });
});
