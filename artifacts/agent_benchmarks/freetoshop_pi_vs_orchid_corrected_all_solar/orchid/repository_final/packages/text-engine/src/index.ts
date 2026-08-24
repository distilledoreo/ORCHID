export const TEXT_ENGINE_MAX_FONT_BYTES = 64 * 1024 * 1024;
export const TEXT_ENGINE_MAX_TEXT_LENGTH = 1_000_000;

export type TextDirection = 'ltr' | 'rtl';

export interface GlyphPathCommand {
  readonly type: string;
  readonly values: readonly number[];
}

export interface RawShapedGlyph {
  readonly glyphId: number;
  readonly cluster: number;
  readonly xAdvance: number;
  readonly yAdvance: number;
  readonly xOffset: number;
  readonly yOffset: number;
  readonly path: readonly GlyphPathCommand[];
}

export interface ShapedGlyph extends RawShapedGlyph {
  readonly x: number;
  readonly y: number;
  readonly scale: number;
}

export interface ShapedRun {
  readonly text: string;
  readonly family: string;
  readonly direction: TextDirection;
  readonly glyphs: readonly ShapedGlyph[];
  readonly advance: number;
  readonly ascender: number;
  readonly descender: number;
}

export interface ShapedLine extends ShapedRun {
  readonly baseline: number;
}

export interface ShapedTextLayout {
  readonly lines: readonly ShapedLine[];
  readonly width: number;
  readonly height: number;
  readonly truncated: boolean;
}

export interface ShapedTextPaintOptions {
  readonly align: 'left' | 'center' | 'right';
  readonly x?: number;
  readonly y?: number;
}

export interface ShapeTextOptions {
  readonly family: string;
  readonly fontSize: number;
  readonly direction?: TextDirection | 'auto';
  readonly language?: string;
  readonly features?: readonly string[];
}

export interface LayoutTextOptions extends ShapeTextOptions {
  readonly width: number;
  readonly height: number;
  readonly lineHeight: number;
}

export interface FontRegistration {
  readonly family: string;
  readonly data: ArrayBuffer;
  readonly faceIndex?: number;
}

export interface ShapingFont {
  readonly unitsPerEm: number;
  readonly ascender: number;
  readonly descender: number;
  readonly handle: unknown;
}

export interface TextShapingBackend {
  createFont(data: ArrayBuffer, faceIndex: number): ShapingFont;
  shape(
    font: ShapingFont,
    text: string,
    options: {
      readonly direction: TextDirection;
      readonly language?: string;
      readonly features: readonly string[];
    },
  ): readonly RawShapedGlyph[];
}

interface RegisteredFont {
  readonly family: string;
  readonly backend: TextShapingBackend;
  readonly font: ShapingFont;
}

function assertFamily(family: string): string {
  const normalized = family.trim();
  let containsControlCharacter = false;
  for (let index = 0; index < normalized.length; index += 1) {
    if (normalized.charCodeAt(index) < 0x20) containsControlCharacter = true;
  }
  if (!normalized || normalized.length > 256 || containsControlCharacter) {
    throw new Error('Font family must be a non-empty name of at most 256 characters.');
  }
  return normalized;
}

function assertText(text: string): void {
  if (text.length > TEXT_ENGINE_MAX_TEXT_LENGTH) {
    throw new Error(`Text exceeds the ${String(TEXT_ENGINE_MAX_TEXT_LENGTH)} character limit.`);
  }
}

function assertFontSize(fontSize: number): void {
  if (!Number.isFinite(fontSize) || fontSize <= 0 || fontSize > 10_000) {
    throw new Error('Font size must be between 0 and 10000 pixels.');
  }
}

function inferDirection(text: string): TextDirection {
  for (const character of text) {
    const codePoint = character.codePointAt(0) ?? 0;
    if (isRtlStrong(codePoint)) return 'rtl';
    if (isLtrStrong(codePoint)) return 'ltr';
  }
  return 'ltr';
}

function isRtlStrong(codePoint: number): boolean {
  return (
    (codePoint >= 0x0590 && codePoint <= 0x08ff) ||
    (codePoint >= 0xfb1d && codePoint <= 0xfdff) ||
    (codePoint >= 0xfe70 && codePoint <= 0xfeff)
  );
}

/** Unicode nonspacing/spacing combining marks use the preceding bidi type (UBA W1). */
function isCombiningMark(character: string): boolean {
  return /^\p{M}$/u.test(character);
}

function isLtrStrong(codePoint: number): boolean {
  return (
    (codePoint >= 0x0041 && codePoint <= 0x005a) ||
    (codePoint >= 0x0061 && codePoint <= 0x007a) ||
    (codePoint >= 0x00c0 && codePoint <= 0x02af)
  );
}

export interface BidiTextRun {
  readonly text: string;
  readonly direction: TextDirection;
}

function isEuropeanNumber(codePoint: number): boolean {
  return codePoint >= 0x0030 && codePoint <= 0x0039;
}

/** Arabic-Indic digits (U+0660–U+0669) — weak numeric, associate with RTL. */
function isArabicIndicNumber(codePoint: number): boolean {
  return codePoint >= 0x0660 && codePoint <= 0x0669;
}

/** UBA Common Separator used inside European numbers: comma, period, colon. */
function isEuropeanNumberCommonSeparator(codePoint: number): boolean {
  return codePoint === 0x002c || codePoint === 0x002e || codePoint === 0x003a;
}

/** UBA European Separator: plus/minus as number prefix/infix. */
function isEuropeanNumberSign(codePoint: number): boolean {
  return codePoint === 0x002b || codePoint === 0x002d;
}

/** UBA European Number Terminator subset: percent and common currency. */
function isEuropeanNumberTerminator(codePoint: number): boolean {
  return (
    codePoint === 0x0025 || // %
    codePoint === 0x0024 || // $
    codePoint === 0x00a3 || // £
    codePoint === 0x20ac // €
  );
}

/**
 * Splits text into contiguous runs of strong LTR/RTL characters (plus neutrals
 * absorbed into the surrounding run). European digits act as weak LTR so
 * sequences like "مرحبا 123" split into RTL + LTR runs. Arabic-Indic digits
 * associate with RTL. European number separators (.,), signs (+-), and common
 * terminators (%, currency) adjacent to digits stay with the EN run. Combining
 * marks inherit the preceding run, or the paragraph base direction at the
 * start of a paragraph (UBA-lite W1/CS/ES/ET) — not a full Unicode
 * Bidirectional Algorithm implementation.
 *
 * @param baseDirection Paragraph base direction applied to leading neutrals
 *   before the first strong character (UBA-like default embedding).
 */
export function segmentBidiRuns(
  text: string,
  baseDirection: TextDirection = 'ltr',
): readonly BidiTextRun[] {
  if (text.length === 0) return [{ text: '', direction: baseDirection }];
  const characters = Array.from(text);
  const runs: BidiTextRun[] = [];
  let currentText = '';
  let currentDirection: TextDirection | null = null;
  let leadingNeutrals = '';

  const flush = () => {
    if (currentDirection === null && currentText.length === 0) return;
    runs.push({
      text: currentText,
      direction: currentDirection ?? baseDirection,
    });
    currentText = '';
    currentDirection = null;
  };

  const codeAt = (index: number): number => characters[index]?.codePointAt(0) ?? 0;

  for (let index = 0; index < characters.length; index += 1) {
    const character = characters[index];
    const codePoint = codeAt(index);
    let strong: TextDirection | null = null;
    if (isCombiningMark(character)) {
      // W1: an NSM inherits the preceding bidi type. The existing open run is
      // therefore authoritative; at paragraph start it remains buffered and
      // resolves to the paragraph base direction.
      if (currentDirection === null) leadingNeutrals += character;
      else currentText += character;
      continue;
    }
    if (isRtlStrong(codePoint) || isArabicIndicNumber(codePoint)) strong = 'rtl';
    else if (isLtrStrong(codePoint) || isEuropeanNumber(codePoint)) strong = 'ltr';
    else {
      // UBA-lite CS/ES/ET: bind number punctuation to European number (weak LTR).
      const prev = index > 0 ? codeAt(index - 1) : 0;
      const next = index + 1 < characters.length ? codeAt(index + 1) : 0;
      const prevEn = isEuropeanNumber(prev);
      const nextEn = isEuropeanNumber(next);
      if (isEuropeanNumberCommonSeparator(codePoint) && prevEn && nextEn) strong = 'ltr';
      else if (isEuropeanNumberSign(codePoint) && nextEn) strong = 'ltr';
      else if (isEuropeanNumberTerminator(codePoint) && (prevEn || nextEn)) strong = 'ltr';
    }

    if (strong === null) {
      // Neutrals (punctuation, spaces) stay with the open run, or buffer if none yet.
      if (currentDirection === null) {
        leadingNeutrals += character;
      } else {
        currentText += character;
      }
      continue;
    }
    if (currentDirection === null) {
      currentDirection = strong;
      // Leading neutrals inherit the first strong type when it matches base, else base.
      // UBA lite: leading neutrals take the paragraph base direction as their own run
      // when the first strong differs from base (e.g. "  مرحبا" in LTR paragraph).
      if (leadingNeutrals.length > 0) {
        if (strong === baseDirection) {
          currentText = leadingNeutrals + character;
        } else {
          runs.push({ text: leadingNeutrals, direction: baseDirection });
          currentText = character;
        }
        leadingNeutrals = '';
      } else {
        currentText = character;
      }
      continue;
    }
    if (strong === currentDirection) {
      currentText += character;
      continue;
    }
    // Direction change: UBA-lite N resolution for trailing neutrals.
    // Neutrals take the paragraph base direction. If that matches the previous
    // strong type, keep them on the previous run; if it matches the next strong
    // type, attach them to the new run.
    const trailingMatch = /[\s,.;:!?…]+$/u.exec(currentText);
    if (
      trailingMatch &&
      trailingMatch[0].length > 0 &&
      trailingMatch[0].length < currentText.length
    ) {
      if (baseDirection === currentDirection) {
        // N resolves like previous strong — leave trailing neutrals on this run.
        flush();
      } else if (baseDirection === strong) {
        currentText = currentText.slice(0, -trailingMatch[0].length);
        flush();
        currentDirection = strong;
        currentText = trailingMatch[0] + character;
        continue;
      } else {
        currentText = currentText.slice(0, -trailingMatch[0].length);
        flush();
        runs.push({ text: trailingMatch[0], direction: baseDirection });
      }
    } else {
      flush();
    }
    currentDirection = strong;
    currentText = character;
  }
  if (leadingNeutrals.length > 0 && currentDirection === null) {
    return [{ text: leadingNeutrals, direction: baseDirection }];
  }
  flush();
  return runs.length > 0 ? runs : [{ text, direction: baseDirection }];
}

/**
 * UBA-lite level-1 display reorder for already-segmented runs.
 * LTR base keeps logical order. RTL base reverses run order so LTR islands
 * appear on the visual left and strong RTL content on the visual right when
 * glyphs are painted left-to-right by increasing x.
 */
export function reorderBidiRunsForDisplay(
  runs: readonly BidiTextRun[],
  baseDirection: TextDirection,
): readonly BidiTextRun[] {
  if (runs.length <= 1 || baseDirection === 'ltr') return runs;
  return [...runs].reverse();
}

function normalizeFeature(feature: string): string {
  const normalized = feature.trim();
  if (!/^[A-Za-z0-9]{4}(?:=(?:0|1|on|off))?$/u.test(normalized)) {
    throw new Error(`Invalid OpenType feature: ${feature}`);
  }
  return normalized;
}

let defaultBackendPromise: Promise<TextShapingBackend> | null = null;

async function loadDefaultBackend(): Promise<TextShapingBackend> {
  defaultBackendPromise ??= import('harfbuzzjs').then((hb) => ({
    createFont(data, faceIndex) {
      const blob = new hb.Blob(data);
      const face = new hb.Face(blob, faceIndex);
      if (!Number.isFinite(face.upem) || face.upem <= 0) {
        throw new Error('The font has an invalid units-per-em value.');
      }
      const font = new hb.Font(face);
      font.setScale(face.upem, face.upem);
      const extents = font.hExtents();
      return {
        unitsPerEm: face.upem,
        ascender: extents.ascender,
        descender: extents.descender,
        handle: font,
      };
    },
    shape(fontRecord, text, options) {
      const font = fontRecord.handle as InstanceType<typeof hb.Font>;
      const buffer = new hb.Buffer();
      buffer.addText(text);
      buffer.guessSegmentProperties();
      buffer.setDirection(options.direction === 'rtl' ? hb.Direction.RTL : hb.Direction.LTR);
      if (options.language) buffer.setLanguage(options.language);
      const features = options.features.map((value) => hb.Feature.fromString(value));
      if (features.some((feature) => feature === undefined)) {
        throw new Error('An OpenType feature could not be parsed.');
      }
      hb.shape(
        font,
        buffer,
        features.filter((feature) => feature !== undefined),
      );
      return buffer.getGlyphInfosAndPositions().map((glyph) => ({
        glyphId: glyph.codepoint,
        cluster: glyph.cluster,
        xAdvance: glyph.xAdvance ?? 0,
        yAdvance: glyph.yAdvance ?? 0,
        xOffset: glyph.xOffset ?? 0,
        yOffset: glyph.yOffset ?? 0,
        path: font.glyphToJson(glyph.codepoint),
      }));
    },
  }));
  return defaultBackendPromise;
}

export class TextEngine {
  readonly #fonts = new Map<string, RegisteredFont>();
  readonly #backendLoader: () => Promise<TextShapingBackend>;

  constructor(backendLoader: () => Promise<TextShapingBackend> = loadDefaultBackend) {
    this.#backendLoader = backendLoader;
  }

  get families(): readonly string[] {
    return [...this.#fonts.values()]
      .map((entry) => entry.family)
      .sort((a, b) => a.localeCompare(b));
  }

  hasFont(family: string): boolean {
    return this.#fonts.has(family.trim().toLocaleLowerCase());
  }

  async registerFont(registration: FontRegistration): Promise<void> {
    const family = assertFamily(registration.family);
    if (!(registration.data instanceof ArrayBuffer) || registration.data.byteLength === 0) {
      throw new Error('Font data must be a non-empty ArrayBuffer.');
    }
    if (registration.data.byteLength > TEXT_ENGINE_MAX_FONT_BYTES) {
      throw new Error(`Font exceeds the ${String(TEXT_ENGINE_MAX_FONT_BYTES)} byte limit.`);
    }
    const faceIndex = registration.faceIndex ?? 0;
    if (!Number.isInteger(faceIndex) || faceIndex < 0 || faceIndex > 65_535) {
      throw new Error('Font face index must be an integer between 0 and 65535.');
    }
    const backend = await this.#backendLoader();
    const owned = registration.data.slice(0);
    const font = backend.createFont(owned, faceIndex);
    this.#fonts.set(family.toLocaleLowerCase(), { family, backend, font });
  }

  unregisterFont(family: string): boolean {
    return this.#fonts.delete(family.trim().toLocaleLowerCase());
  }

  shapeText(text: string, options: ShapeTextOptions): ShapedRun {
    assertText(text);
    assertFontSize(options.fontSize);
    const family = assertFamily(options.family);
    const registered = this.#fonts.get(family.toLocaleLowerCase());
    if (!registered) throw new Error(`Font is not registered with the text engine: ${family}`);
    const direction =
      options.direction && options.direction !== 'auto' ? options.direction : inferDirection(text);
    return this.#shapeRegistered(registered, text, direction, options);
  }

  /**
   * Shapes mixed-script text as separate bidi runs, then concatenates glyph
   * positions in display order. Each run is shaped with its own direction so
   * Arabic/Hebrew substrings reverse correctly inside Latin paragraphs.
   * RTL base paragraphs reverse run order (UBA-lite level-1 display reorder).
   */
  shapeMixedText(
    text: string,
    options: ShapeTextOptions,
  ): {
    readonly runs: readonly ShapedRun[];
    readonly glyphs: readonly ShapedGlyph[];
    readonly advance: number;
    readonly direction: TextDirection;
    readonly text: string;
    readonly family: string;
    readonly ascender: number;
    readonly descender: number;
  } {
    assertText(text);
    assertFontSize(options.fontSize);
    const family = assertFamily(options.family);
    const registered = this.#fonts.get(family.toLocaleLowerCase());
    if (!registered) throw new Error(`Font is not registered with the text engine: ${family}`);

    const forced = options.direction && options.direction !== 'auto' ? options.direction : null;
    const baseDirection = forced ?? inferDirection(text);
    const logicalSegments = forced
      ? [{ text, direction: forced }]
      : segmentBidiRuns(text, baseDirection);
    const displaySegments = reorderBidiRunsForDisplay(logicalSegments, baseDirection);
    const runs = displaySegments.map((segment) =>
      this.#shapeRegistered(registered, segment.text, segment.direction, options),
    );

    let cursor = 0;
    const glyphs: ShapedGlyph[] = [];
    for (const run of runs) {
      for (const glyph of run.glyphs) {
        glyphs.push({
          ...glyph,
          x: glyph.x + cursor,
        });
      }
      cursor += run.advance;
    }
    return {
      runs,
      glyphs,
      advance: cursor,
      direction: baseDirection,
      text,
      family: registered.family,
      ascender:
        runs[0]?.ascender ??
        registered.font.ascender * (options.fontSize / registered.font.unitsPerEm),
      descender:
        runs[0]?.descender ??
        registered.font.descender * (options.fontSize / registered.font.unitsPerEm),
    };
  }

  #shapeRegistered(
    registered: RegisteredFont,
    text: string,
    direction: TextDirection,
    options: ShapeTextOptions,
  ): ShapedRun {
    const features = (options.features ?? []).map(normalizeFeature);
    const raw = registered.backend.shape(registered.font, text, {
      direction,
      ...(options.language ? { language: options.language } : {}),
      features,
    });
    const scale = options.fontSize / registered.font.unitsPerEm;
    let cursorX = 0;
    let cursorY = 0;
    const glyphs = raw.map((glyph) => {
      const positioned: ShapedGlyph = {
        ...glyph,
        x: (cursorX + glyph.xOffset) * scale,
        y: (cursorY + glyph.yOffset) * scale,
        scale,
      };
      cursorX += glyph.xAdvance;
      cursorY += glyph.yAdvance;
      return positioned;
    });
    return {
      text,
      family: registered.family,
      direction,
      glyphs,
      advance: Math.abs(cursorX * scale),
      ascender: registered.font.ascender * scale,
      descender: registered.font.descender * scale,
    };
  }

  /**
   * Paragraph layout with mixed-script bidi shaping. Line breaks use
   * `shapeMixedText` advances so RTL islands and weak digits measure correctly
   * on the renderer text path (not a full UBA embedding stack).
   */
  layoutText(text: string, options: LayoutTextOptions): ShapedTextLayout {
    assertText(text);
    if (!Number.isFinite(options.width) || options.width <= 0 || options.width > 1_000_000) {
      throw new Error('Text layout width must be between 0 and 1000000 pixels.');
    }
    if (!Number.isFinite(options.height) || options.height <= 0 || options.height > 1_000_000) {
      throw new Error('Text layout height must be between 0 and 1000000 pixels.');
    }
    if (
      !Number.isFinite(options.lineHeight) ||
      options.lineHeight < 0.5 ||
      options.lineHeight > 10
    ) {
      throw new Error('Text line height must be between 0.5 and 10.');
    }
    const lineAdvance = options.fontSize * options.lineHeight;
    const lines: ShapedLine[] = [];
    let truncated = false;
    const pushLine = (value: string) => {
      if ((lines.length + 1) * lineAdvance > options.height + 0.001) {
        truncated = true;
        return false;
      }
      const mixed = this.shapeMixedText(value, options);
      lines.push({
        text: value,
        family: mixed.family,
        direction: mixed.direction,
        glyphs: mixed.glyphs,
        advance: mixed.advance,
        ascender: mixed.ascender,
        descender: mixed.descender,
        baseline: lines.length * lineAdvance + mixed.ascender,
      });
      return true;
    };
    paragraphs: for (const paragraph of text.split('\n')) {
      const segments = paragraph.split(/(\s+)/u).filter(Boolean);
      let line = '';
      for (const segment of segments.length > 0 ? segments : ['']) {
        const candidate = `${line}${segment}`;
        const candidateAdvance = this.shapeMixedText(candidate, options).advance;
        if (line && candidateAdvance > options.width) {
          if (!pushLine(line.trimEnd())) break paragraphs;
          line = segment.trimStart();
        } else {
          line = candidate;
        }
      }
      if (!pushLine(line.trimEnd())) break;
    }
    return { lines, width: options.width, height: options.height, truncated };
  }
}

function commandValue(values: readonly number[], index: number): number {
  const value = values[index];
  if (!Number.isFinite(value)) throw new Error('Shaped glyph path contains an invalid coordinate.');
  return value;
}

function appendGlyphPath(
  context: CanvasRenderingContext2D | OffscreenCanvasRenderingContext2D,
  glyph: ShapedGlyph,
  originX: number,
  baseline: number,
): void {
  context.save();
  context.translate(originX + glyph.x, baseline - glyph.y);
  context.scale(glyph.scale, -glyph.scale);
  for (const command of glyph.path) {
    const type = command.type.toUpperCase();
    if (type === 'M') {
      context.moveTo(commandValue(command.values, 0), commandValue(command.values, 1));
    } else if (type === 'L') {
      context.lineTo(commandValue(command.values, 0), commandValue(command.values, 1));
    } else if (type === 'Q') {
      context.quadraticCurveTo(
        commandValue(command.values, 0),
        commandValue(command.values, 1),
        commandValue(command.values, 2),
        commandValue(command.values, 3),
      );
    } else if (type === 'C') {
      context.bezierCurveTo(
        commandValue(command.values, 0),
        commandValue(command.values, 1),
        commandValue(command.values, 2),
        commandValue(command.values, 3),
        commandValue(command.values, 4),
        commandValue(command.values, 5),
      );
    } else if (type === 'Z') {
      context.closePath();
    } else {
      throw new Error(`Unsupported shaped glyph path command: ${command.type}`);
    }
  }
  context.restore();
}

export function appendShapedTextPath(
  context: CanvasRenderingContext2D | OffscreenCanvasRenderingContext2D,
  layout: ShapedTextLayout,
  options: ShapedTextPaintOptions,
): void {
  const baseX = options.x ?? 0;
  const baseY = options.y ?? 0;
  for (const line of layout.lines) {
    const alignmentOffset =
      options.align === 'center'
        ? (layout.width - line.advance) / 2
        : options.align === 'right'
          ? layout.width - line.advance
          : 0;
    for (const glyph of line.glyphs) {
      appendGlyphPath(context, glyph, baseX + alignmentOffset, baseY + line.baseline);
    }
  }
}

export const textEngine = new TextEngine();
