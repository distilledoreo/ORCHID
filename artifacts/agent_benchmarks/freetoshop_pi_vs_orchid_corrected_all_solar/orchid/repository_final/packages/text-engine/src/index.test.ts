import { describe, expect, it, vi } from 'vitest';
import { readFile } from 'node:fs/promises';
import {
  TEXT_ENGINE_MAX_FONT_BYTES,
  TextEngine,
  reorderBidiRunsForDisplay,
  segmentBidiRuns,
  type RawShapedGlyph,
  type ShapingFont,
  type TextShapingBackend,
} from './index.js';

function backend(): TextShapingBackend {
  return {
    createFont(): ShapingFont {
      return { unitsPerEm: 1000, ascender: 800, descender: -200, handle: null };
    },
    shape(_font, text, options): readonly RawShapedGlyph[] {
      const glyphs = [...text].map((character, index) => ({
        glyphId: character.codePointAt(0) ?? 0,
        cluster: index,
        xAdvance: 500,
        yAdvance: 0,
        xOffset: 0,
        yOffset: 0,
        path: [{ type: 'M', values: [0, 0] }],
      }));
      return options.direction === 'rtl' ? glyphs.reverse() : glyphs;
    },
  };
}

async function engine() {
  const instance = new TextEngine(async () => backend());
  await instance.registerFont({ family: 'Fixture Sans', data: new ArrayBuffer(8) });
  return instance;
}

describe('TextEngine', () => {
  it('owns registrations and exposes deterministic family lookup', async () => {
    const create = vi.fn(() => backend());
    const instance = new TextEngine(async () => create());
    const data = new ArrayBuffer(8);
    await instance.registerFont({ family: 'Fixture Sans', data });
    expect(instance.families).toEqual(['Fixture Sans']);
    expect(instance.hasFont('fixture sans')).toBe(true);
    expect(create).toHaveBeenCalledOnce();
    expect(instance.unregisterFont('FIXTURE SANS')).toBe(true);
  });

  it('positions scaled glyphs and infers RTL direction', async () => {
    const instance = await engine();
    const latin = instance.shapeText('fi', { family: 'Fixture Sans', fontSize: 20 });
    expect(latin.direction).toBe('ltr');
    expect(latin.advance).toBe(20);
    expect(latin.glyphs.map((glyph) => glyph.x)).toEqual([0, 10]);
    expect(latin.ascender).toBe(16);
    expect(latin.descender).toBe(-4);

    const arabic = instance.shapeText('مرحبا', { family: 'Fixture Sans', fontSize: 10 });
    expect(arabic.direction).toBe('rtl');
    expect(arabic.glyphs[0]?.glyphId).toBe('ا'.codePointAt(0));
  });

  it('segments mixed LTR/RTL text into bidi runs', () => {
    const runs = segmentBidiRuns('Hello مرحبا world');
    expect(runs.map((run) => run.direction)).toEqual(['ltr', 'rtl', 'ltr']);
    expect(runs.map((run) => run.text.trim())).toEqual(['Hello', 'مرحبا', 'world']);
  });

  it('treats European digits as weak LTR and applies paragraph base to leading neutrals', () => {
    const withDigits = segmentBidiRuns('مرحبا 123');
    expect(withDigits.map((run) => run.direction)).toEqual(['rtl', 'ltr']);
    expect(withDigits[1]?.text.trim()).toBe('123');

    const leading = segmentBidiRuns('  مرحبا', 'ltr');
    expect(leading[0]).toEqual({ text: '  ', direction: 'ltr' });
    expect(leading[1]?.direction).toBe('rtl');
  });

  it('shapes mixed-script paragraphs with per-run direction', async () => {
    const instance = await engine();
    const mixed = instance.shapeMixedText('AB مرحبا CD', {
      family: 'Fixture Sans',
      fontSize: 10,
    });
    expect(mixed.runs).toHaveLength(3);
    expect(mixed.runs.map((run) => run.direction)).toEqual(['ltr', 'rtl', 'ltr']);
    const arabicRun = mixed.runs.find((run) => run.direction === 'rtl');
    expect(arabicRun).toBeDefined();
    // Fixture RTL backend reverses glyphs; trailing neutrals may lead the run.
    expect(arabicRun!.glyphs.some((glyph) => glyph.glyphId === 'ا'.codePointAt(0))).toBe(true);
    expect(mixed.advance).toBeGreaterThan(0);
    expect(mixed.glyphs.length).toBeGreaterThan(4);
  });

  it('associates Arabic-Indic digits with RTL and reorders runs for RTL base display', () => {
    const withArabicDigits = segmentBidiRuns('مرحبا \u0661\u0662\u0663');
    expect(withArabicDigits.map((run) => run.direction)).toEqual(['rtl']);
    expect(withArabicDigits[0]?.text).toContain('\u0661');

    const logical = segmentBidiRuns('مرحبا Hello', 'rtl');
    expect(logical.map((run) => run.direction)).toEqual(['rtl', 'ltr']);
    const display = reorderBidiRunsForDisplay(logical, 'rtl');
    // RTL base: LTR island first in left-to-right paint order.
    expect(display.map((run) => run.direction)).toEqual(['ltr', 'rtl']);
    expect(reorderBidiRunsForDisplay(logical, 'ltr')).toEqual(logical);
  });

  it('resolves neutrals between opposing strong runs with paragraph base direction', () => {
    // Space after RTL before LTR in an LTR paragraph attaches to the LTR run (N→base L).
    const mixed = segmentBidiRuns('مرحبا Hello', 'ltr');
    expect(mixed.map((run) => run.direction)).toEqual(['rtl', 'ltr']);
    expect(mixed[0]?.text).toBe('مرحبا');
    expect(mixed[1]?.text).toBe(' Hello');

    // Space after LTR before RTL in LTR paragraph stays with the LTR run (N→base L).
    const helloArabic = segmentBidiRuns('Hello, مرحبا', 'ltr');
    expect(helloArabic.map((run) => run.direction)).toEqual(['ltr', 'rtl']);
    expect(helloArabic[0]?.text).toBe('Hello, ');
    expect(helloArabic[1]?.text).toBe('مرحبا');
  });

  it('keeps European number separators, signs, and terminators with EN runs', () => {
    // Decimal and grouping separators bind into a single LTR number run.
    const decimal = segmentBidiRuns('مرحبا 1,234.56');
    expect(decimal.map((run) => run.direction)).toEqual(['rtl', 'ltr']);
    expect(decimal[1]?.text.trim()).toBe('1,234.56');

    // Signed number and percent stay with EN.
    const signed = segmentBidiRuns('سعر -12.5%');
    expect(signed.some((run) => run.direction === 'ltr' && run.text.includes('-12.5%'))).toBe(true);

    // Currency terminator before digits.
    const currency = segmentBidiRuns('Hello $99');
    expect(currency.map((run) => run.direction)).toEqual(['ltr']);
    expect(currency[0]?.text).toContain('$99');
  });

  it('inherits bidi direction for combining marks instead of treating them as strong RTL', () => {
    // U+064E ARABIC FATHA follows the European digit run under UBA W1.
    const afterNumber = segmentBidiRuns('مرحبا 12\u064e3', 'ltr');
    expect(afterNumber.map((run) => run.direction)).toEqual(['rtl', 'ltr']);
    expect(afterNumber[1]?.text).toBe(' 12\u064e3');

    // A paragraph-leading NSM inherits the paragraph base type, not the broad
    // RTL Unicode block occupied by the mark itself.
    const leading = segmentBidiRuns('\u064eمرحبا', 'ltr');
    expect(leading).toEqual([
      { text: '\u064e', direction: 'ltr' },
      { text: 'مرحبا', direction: 'rtl' },
    ]);

    // Marks following RTL letters remain in the same RTL shaping run.
    expect(segmentBidiRuns('م\u064eر').map((run) => run.direction)).toEqual(['rtl']);
  });

  it('shapes RTL-base mixed text with display-reordered runs', async () => {
    const instance = await engine();
    const mixed = instance.shapeMixedText('مرحبا Hello', {
      family: 'Fixture Sans',
      fontSize: 10,
      direction: 'rtl',
    });
    // Forced RTL still shapes as a single run (explicit direction override).
    expect(mixed.direction).toBe('rtl');
    expect(mixed.runs).toHaveLength(1);

    const autoMixed = instance.shapeMixedText('مرحبا Hello', {
      family: 'Fixture Sans',
      fontSize: 10,
      direction: 'auto',
    });
    expect(autoMixed.direction).toBe('rtl');
    expect(autoMixed.runs.map((run) => run.direction)).toEqual(['ltr', 'rtl']);
    expect(autoMixed.advance).toBeGreaterThan(0);
  });

  it('layouts mixed bidi paragraphs using shapeMixedText advances', async () => {
    const instance = await engine();
    const layout = instance.layoutText('Hello مرحبا world more', {
      family: 'Fixture Sans',
      fontSize: 10,
      lineHeight: 1.2,
      width: 55,
      height: 48,
      direction: 'auto',
    });
    expect(layout.lines.length).toBeGreaterThan(0);
    // At least one line should retain mixed-script content from bidi shaping.
    const joined = layout.lines.map((line) => line.text).join(' ');
    expect(joined).toContain('Hello');
    expect(joined).toMatch(/مرحبا/);
    expect(layout.lines.every((line) => line.glyphs.length > 0)).toBe(true);
  });

  it('wraps shaped lines by glyph advances and reports truncation', async () => {
    const instance = await engine();
    const layout = instance.layoutText('one two three', {
      family: 'Fixture Sans',
      fontSize: 10,
      lineHeight: 1.2,
      width: 28,
      height: 24,
    });
    expect(layout.lines.map((line) => line.text)).toEqual(['one', 'two']);
    expect(layout.lines.map((line) => line.baseline)).toEqual([8, 20]);
    expect(layout.truncated).toBe(true);
  });

  it('rejects unsafe inputs before invoking the backend', async () => {
    const instance = new TextEngine(async () => backend());
    await expect(
      instance.registerFont({
        family: 'Too large',
        data: new ArrayBuffer(TEXT_ENGINE_MAX_FONT_BYTES + 1),
      }),
    ).rejects.toThrow('byte limit');
    await instance.registerFont({ family: 'Fixture Sans', data: new ArrayBuffer(8) });
    expect(() =>
      instance.shapeText('text', {
        family: 'Fixture Sans',
        fontSize: 12,
        features: ['not-a-feature'],
      }),
    ).toThrow('Invalid OpenType feature');
    expect(() =>
      instance.layoutText('text', {
        family: 'Fixture Sans',
        fontSize: 12,
        lineHeight: 0,
        width: 100,
        height: 100,
      }),
    ).toThrow('line height');
  });

  it('uses real HarfBuzz OpenType substitution and RTL shaping', async () => {
    const bytes = await readFile(
      new URL('../../../node_modules/dejavu-fonts-ttf/ttf/DejaVuSans.ttf', import.meta.url),
    );
    const data = Uint8Array.from(bytes).buffer;
    const instance = new TextEngine();
    await instance.registerFont({ family: 'DejaVu Sans', data });

    const ligatures = instance.shapeText('office', {
      family: 'DejaVu Sans',
      fontSize: 24,
      features: ['liga=1'],
    });
    const unligated = instance.shapeText('office', {
      family: 'DejaVu Sans',
      fontSize: 24,
      features: ['liga=0'],
    });
    expect(ligatures.glyphs.length).toBeLessThan(unligated.glyphs.length);
    expect(ligatures.glyphs.every((glyph) => glyph.path.length > 0)).toBe(true);

    const arabic = instance.shapeText('مرحبا', { family: 'DejaVu Sans', fontSize: 24 });
    expect(arabic.direction).toBe('rtl');
    expect(arabic.glyphs).toHaveLength(5);
    expect(arabic.glyphs.map((glyph) => glyph.glyphId)).not.toEqual(
      [...'مرحبا'].map((character) => character.codePointAt(0)),
    );
  });
});
