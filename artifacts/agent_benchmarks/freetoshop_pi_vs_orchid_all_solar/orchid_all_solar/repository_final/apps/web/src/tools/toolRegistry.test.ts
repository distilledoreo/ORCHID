import { describe, expect, it } from 'vitest';
import { TOOL_FAMILIES, toolForShortcut } from './toolRegistry.js';

describe('professional tool family registry', () => {
  it('keeps implemented families in stable conventional slots', () => {
    expect(TOOL_FAMILIES.map(({ family, slot }) => [family, slot])).toEqual([
      ['move', 1],
      ['marquee', 2],
      ['lasso', 3],
      ['selection', 4],
      ['crop', 5],
      ['brush', 8],
      ['clone-stamp', 8],
      ['eraser', 11],
      ['pen', 15],
      ['hand', 19],
    ]);
  });

  it('cycles implemented members of a family with Shift', () => {
    expect(toolForShortcut('m', 'select', true)).toBe('ellipse');
    expect(toolForShortcut('m', 'ellipse', true)).toBe('select');
    expect(toolForShortcut('b', 'pan', false)).toBe('brush');
  });
});
