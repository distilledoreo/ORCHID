import { TiledCoverageStore, TiledPixelStore } from '@freetoshop/pixel-storage';

export type LayerId = string;

export interface LayerTransform {
  readonly a: number;
  readonly b: number;
  readonly c: number;
  readonly d: number;
  readonly e: number;
  readonly f: number;
}

export interface TransformPoint {
  readonly x: number;
  readonly y: number;
}

export const BLEND_MODES = [
  'normal',
  'multiply',
  'screen',
  'overlay',
  'darken',
  'darker-color',
  'lighten',
  'lighter-color',
  'color-dodge',
  'linear-dodge',
  'color-burn',
  'linear-burn',
  'hard-light',
  'soft-light',
  'vivid-light',
  'linear-light',
  'pin-light',
  'hard-mix',
  'difference',
  'exclusion',
  'subtract',
  'divide',
  'hue',
  'saturation',
  'color',
  'luminosity',
] as const;

export type BlendMode = (typeof BLEND_MODES)[number];
export type GroupCompositingMode = 'pass-through' | 'isolated';

export interface LayerLocks {
  readonly transparency: boolean;
  readonly pixels: boolean;
  readonly position: boolean;
  readonly all: boolean;
}

export interface ColorOverlayEffect {
  readonly type: 'color-overlay';
  readonly enabled: boolean;
  readonly opacity: number;
  readonly blendMode: BlendMode;
  readonly color: RgbaColor;
}

export interface StrokeEffect {
  readonly type: 'stroke';
  readonly enabled: boolean;
  readonly opacity: number;
  readonly size: number;
  readonly color: RgbaColor;
}

export interface DropShadowEffect {
  readonly type: 'drop-shadow';
  readonly enabled: boolean;
  readonly opacity: number;
  readonly offsetX: number;
  readonly offsetY: number;
  readonly blur: number;
  readonly color: RgbaColor;
}

export type LayerEffect = ColorOverlayEffect | StrokeEffect | DropShadowEffect;

export interface LayerBase {
  readonly id: LayerId;
  readonly name: string;
  readonly visible: boolean;
  readonly opacity: number;
  readonly fillOpacity: number;
  readonly blendMode: BlendMode;
  readonly clipping: boolean;
  readonly locks: LayerLocks;
  readonly transform: LayerTransform;
  readonly effects: readonly LayerEffect[];
}

export interface PixelLayer extends LayerBase {
  readonly kind: 'pixel';
  readonly width: number;
  readonly height: number;
  /** Canonical sparse, immutable RGBA8 pixel resource. */
  readonly pixelStore: TiledPixelStore;
  readonly rasterMask: RasterMask | null;
  readonly vectorMask: VectorMask | null;
}

export interface RasterMask {
  readonly width: number;
  readonly height: number;
  /** Canonical sparse, immutable 8-bit coverage resource. */
  readonly coverageStore: TiledCoverageStore;
  readonly enabled: boolean;
  /** Normalized strength of the mask effect. */
  readonly density: number;
}

export interface VectorMask {
  readonly width: number;
  readonly height: number;
  readonly commands: readonly PathCommand[];
  readonly enabled: boolean;
  /** Normalized strength of the mask effect. */
  readonly density: number;
  readonly inverted: boolean;
}

export interface GroupLayer extends LayerBase {
  readonly kind: 'group';
  readonly children: readonly Layer[];
  readonly compositing: GroupCompositingMode;
  /** Document-space coverage applied to the group's combined result. */
  readonly rasterMask: RasterMask | null;
  /** Document-space mask applied to the group's combined result. */
  readonly vectorMask: VectorMask | null;
}

export interface BrightnessContrastAdjustment {
  readonly type: 'brightness-contrast';
  readonly brightness: number;
  readonly contrast: number;
}

export interface InvertAdjustment {
  readonly type: 'invert';
}

export interface LevelsAdjustment {
  readonly type: 'levels';
  readonly inputBlack: number;
  readonly inputWhite: number;
  readonly gamma: number;
  readonly outputBlack: number;
  readonly outputWhite: number;
}

export interface HueSaturationAdjustment {
  readonly type: 'hue-saturation';
  readonly hue: number;
  readonly saturation: number;
  readonly lightness: number;
}

export interface PosterizeAdjustment {
  readonly type: 'posterize';
  /** Integer number of tonal levels in [2, 256]. */
  readonly levels: number;
}

export interface ThresholdAdjustment {
  readonly type: 'threshold';
  /** Integer threshold level in [0, 255]. */
  readonly level: number;
}

export type Adjustment =
  | BrightnessContrastAdjustment
  | InvertAdjustment
  | LevelsAdjustment
  | HueSaturationAdjustment
  | PosterizeAdjustment
  | ThresholdAdjustment;

export interface AdjustmentLayer extends LayerBase {
  readonly kind: 'adjustment';
  readonly adjustment: Adjustment;
  /** Document-space mask. Null applies the adjustment everywhere. */
  readonly rasterMask: RasterMask | null;
  readonly vectorMask: VectorMask | null;
}

export interface RgbaColor {
  readonly red: number;
  readonly green: number;
  readonly blue: number;
  readonly alpha: number;
}

export const SHAPE_KINDS = ['rectangle', 'ellipse', 'path'] as const;
export type ShapeKind = (typeof SHAPE_KINDS)[number];

export type PathCommand =
  | { readonly type: 'move'; readonly x: number; readonly y: number }
  | { readonly type: 'line'; readonly x: number; readonly y: number }
  | {
      readonly type: 'cubic';
      readonly control1X: number;
      readonly control1Y: number;
      readonly control2X: number;
      readonly control2Y: number;
      readonly x: number;
      readonly y: number;
    }
  | { readonly type: 'close' };

export interface ShapeGeometry {
  readonly type: ShapeKind;
  readonly width: number;
  readonly height: number;
  readonly cornerRadius: number;
  readonly commands: readonly PathCommand[];
}

export interface ShapeStyle {
  readonly fill: RgbaColor | null;
  readonly stroke: RgbaColor | null;
  readonly strokeWidth: number;
}

export interface ShapeLayer extends LayerBase {
  readonly kind: 'shape';
  readonly geometry: ShapeGeometry;
  readonly style: ShapeStyle;
  readonly rasterMask: RasterMask | null;
  readonly vectorMask: VectorMask | null;
}

export const TEXT_ALIGNMENTS = ['left', 'center', 'right'] as const;
export type TextAlign = (typeof TEXT_ALIGNMENTS)[number];

export interface TextStyle {
  readonly fontFamily: string;
  readonly fontSize: number;
  readonly fontWeight: number;
  readonly italic: boolean;
  readonly lineHeight: number;
  readonly align: TextAlign;
  readonly color: RgbaColor;
}

export interface TextLayer extends LayerBase {
  readonly kind: 'text';
  readonly text: string;
  readonly width: number;
  readonly height: number;
  readonly style: TextStyle;
  readonly rasterMask: RasterMask | null;
  readonly vectorMask: VectorMask | null;
}

export interface SmartObjectSource {
  readonly id: string;
  readonly name: string;
  readonly canvas: DocumentCanvas;
  readonly layers: readonly Layer[];
}

interface SmartFilterBase {
  readonly id: string;
  readonly enabled: boolean;
  readonly opacity: number;
}

export interface SmartInvertFilter extends SmartFilterBase {
  readonly type: 'invert';
}

export interface SmartGrayscaleFilter extends SmartFilterBase {
  readonly type: 'grayscale';
}

export interface SmartGaussianBlurFilter extends SmartFilterBase {
  readonly type: 'gaussian-blur';
  readonly radius: number;
}

export interface SmartBrightnessContrastFilter extends SmartFilterBase {
  readonly type: 'brightness-contrast';
  readonly brightness: number;
  readonly contrast: number;
}

export type SmartFilter =
  | SmartInvertFilter
  | SmartGrayscaleFilter
  | SmartGaussianBlurFilter
  | SmartBrightnessContrastFilter;

export interface SmartObjectLayer extends LayerBase {
  readonly kind: 'smart-object';
  readonly source: SmartObjectSource;
  readonly smartFilters: readonly SmartFilter[];
  readonly rasterMask: RasterMask | null;
  readonly vectorMask: VectorMask | null;
}

export type Layer =
  | PixelLayer
  | GroupLayer
  | AdjustmentLayer
  | ShapeLayer
  | TextLayer
  | SmartObjectLayer;

export interface DocumentCanvas {
  readonly width: number;
  readonly height: number;
}

/** Document guide orientation in canvas space. */
export type GuideDirection = 'horizontal' | 'vertical';

/**
 * A single ruler guide. `position` is in document pixels from the top (horizontal
 * guides) or left (vertical guides) edge of the canvas.
 */
export interface DocumentGuide {
  readonly id: string;
  readonly direction: GuideDirection;
  readonly position: number;
}

/** Optional document grid spacing in canvas pixels (Photoshop-style). */
export interface DocumentGrid {
  readonly horizontal: number;
  readonly vertical: number;
}

/**
 * Document-level work path / saved path (not a layer). Geometry is in document
 * canvas coordinates using the same PathCommand vocabulary as shape layers.
 */
export interface DocumentPath {
  readonly id: string;
  readonly name: string;
  /** True when the path is open (not a closed subpath). */
  readonly open: boolean;
  readonly commands: readonly PathCommand[];
}

/**
 * Opaque import/export preservation bag. Must be JSON-serializable.
 * Engines must not interpret unknown keys; persistence may round-trip them.
 */
export type DocumentPreservation = Readonly<Record<string, unknown>>;

export interface ImageDocument {
  readonly id: string;
  readonly name: string;
  readonly canvas: DocumentCanvas;
  readonly layers: readonly Layer[];
  /** Full-resolution 8-bit coverage; null means the entire canvas is selected. */
  readonly selection: SelectionMask | null;
  /** Saved full-resolution 8-bit alpha channels in document coordinates. */
  readonly channels: readonly AlphaChannel[];
  /** Ruler guides in document coordinates (may be empty). */
  readonly guides: readonly DocumentGuide[];
  /** Optional document grid; null when no grid is defined. */
  readonly grid: DocumentGrid | null;
  /** Document-level paths (work path / saved paths); may be empty. */
  readonly paths: readonly DocumentPath[];
  /**
   * Optional opaque payload preserved across save/reopen (e.g. PSD unsupported
   * layer records). Omitted or null when no preservation data is attached.
   */
  readonly preservation?: DocumentPreservation | null;
}

export interface SelectionMask {
  readonly width: number;
  readonly height: number;
  /** Canonical sparse coverage ownership. Contiguous views are algorithm-boundary only. */
  readonly coverageStore: TiledCoverageStore;
}

export interface AlphaChannel {
  readonly id: string;
  readonly name: string;
  readonly width: number;
  readonly height: number;
  /** Canonical sparse coverage ownership. Contiguous views are algorithm-boundary only. */
  readonly coverageStore: TiledCoverageStore;
}

/** Builds a selection that owns a sparse coverage store from dense input. */
export function createSelectionMask(options: {
  readonly width: number;
  readonly height: number;
  readonly coverage?: ArrayLike<number>;
  readonly coverageStore?: TiledCoverageStore;
}): SelectionMask {
  assertPositiveInteger(options.width, 'Selection width');
  assertPositiveInteger(options.height, 'Selection height');
  if (options.coverageStore !== undefined) {
    if (
      !(options.coverageStore instanceof TiledCoverageStore) ||
      options.coverageStore.width !== options.width ||
      options.coverageStore.height !== options.height
    ) {
      throw new DocumentInvariantError('Selection coverage store must match its dimensions');
    }
    return {
      width: options.width,
      height: options.height,
      coverageStore: options.coverageStore,
    };
  }
  const expectedLength = options.width * options.height;
  const coverage =
    options.coverage === undefined
      ? new Uint8ClampedArray(expectedLength)
      : new Uint8ClampedArray(options.coverage);
  if (coverage.length !== expectedLength) {
    throw new DocumentInvariantError(
      `Selection coverage must contain ${String(expectedLength)} values`,
    );
  }
  return {
    width: options.width,
    height: options.height,
    coverageStore: TiledCoverageStore.fromContiguous(options.width, options.height, coverage),
  };
}

/**
 * Explicit compatibility materialization for algorithms that still require dense coverage.
 * Ordinary ownership, copy, history, and transport paths must not call this.
 */
export function materializeSelectionCoverage(selection: SelectionMask): Uint8ClampedArray {
  if (
    !(selection.coverageStore instanceof TiledCoverageStore) ||
    selection.coverageStore.width !== selection.width ||
    selection.coverageStore.height !== selection.height
  ) {
    throw new DocumentInvariantError('Selection coverage store must match its dimensions');
  }
  return selection.coverageStore.toContiguous();
}

/**
 * Explicit compatibility materialization for algorithms that still require dense channel coverage.
 */
export function materializeAlphaChannelCoverage(channel: AlphaChannel): Uint8ClampedArray {
  if (
    !(channel.coverageStore instanceof TiledCoverageStore) ||
    channel.coverageStore.width !== channel.width ||
    channel.coverageStore.height !== channel.height
  ) {
    throw new DocumentInvariantError(
      `Alpha channel coverage store must match its dimensions: ${channel.id}`,
    );
  }
  return channel.coverageStore.toContiguous();
}

export interface SelectionRectangle {
  readonly x: number;
  readonly y: number;
  readonly width: number;
  readonly height: number;
}

export type CanvasAnchorPosition =
  | 'top-left'
  | 'top-center'
  | 'top-right'
  | 'center-left'
  | 'center'
  | 'center-right'
  | 'bottom-left'
  | 'bottom-center'
  | 'bottom-right';

export interface CanvasAnchorFractions {
  /** Horizontal fixed-point position from left (0) to right (1). */
  readonly x: number;
  /** Vertical fixed-point position from top (0) to bottom (1). */
  readonly y: number;
}

export type CanvasAnchor = CanvasAnchorPosition | CanvasAnchorFractions;

export type SelectionCombineMode = 'replace' | 'add' | 'subtract' | 'intersect';

export interface DocumentTransform {
  readonly label: string;
  execute(document: ImageDocument): ImageDocument;
}

export interface NewDocumentOptions {
  readonly id: string;
  readonly name: string;
  readonly width: number;
  readonly height: number;
  readonly layers?: readonly Layer[];
  readonly selection?: SelectionMask | null;
  readonly channels?: readonly AlphaChannel[];
  readonly guides?: readonly DocumentGuide[];
  readonly grid?: DocumentGrid | null;
  readonly paths?: readonly DocumentPath[];
  readonly preservation?: DocumentPreservation | null;
}

export interface NewDocumentGuideOptions {
  readonly id: string;
  readonly direction: GuideDirection;
  readonly position: number;
}

export interface NewDocumentPathOptions {
  readonly id: string;
  readonly name: string;
  readonly open?: boolean;
  readonly commands?: readonly PathCommand[];
}

export interface NewAlphaChannelOptions {
  readonly id: string;
  readonly name: string;
  readonly width: number;
  readonly height: number;
  readonly coverage?: ArrayLike<number>;
  readonly coverageStore?: TiledCoverageStore;
}

export interface NewPixelLayerOptions {
  readonly id: LayerId;
  readonly name: string;
  readonly width: number;
  readonly height: number;
  readonly pixels?: ArrayLike<number>;
  readonly pixelStore?: TiledPixelStore;
  readonly visible?: boolean;
  readonly opacity?: number;
  readonly fillOpacity?: number;
  readonly blendMode?: BlendMode;
  readonly clipping?: boolean;
  readonly locks?: Partial<LayerLocks>;
  readonly transform?: LayerTransform;
  readonly effects?: readonly LayerEffect[];
  readonly rasterMask?: RasterMask | null;
  readonly vectorMask?: VectorMask | null;
}

export interface NewGroupLayerOptions {
  readonly id: LayerId;
  readonly name: string;
  readonly children?: readonly Layer[];
  readonly visible?: boolean;
  readonly opacity?: number;
  readonly fillOpacity?: number;
  readonly blendMode?: BlendMode;
  readonly clipping?: boolean;
  readonly locks?: Partial<LayerLocks>;
  readonly transform?: LayerTransform;
  readonly effects?: readonly LayerEffect[];
  readonly compositing?: GroupCompositingMode;
  readonly rasterMask?: RasterMask | null;
  readonly vectorMask?: VectorMask | null;
}

export interface NewAdjustmentLayerOptions {
  readonly id: LayerId;
  readonly name: string;
  readonly adjustment?: Adjustment;
  readonly visible?: boolean;
  readonly opacity?: number;
  readonly fillOpacity?: number;
  readonly blendMode?: BlendMode;
  readonly clipping?: boolean;
  readonly locks?: Partial<LayerLocks>;
  readonly transform?: LayerTransform;
  readonly effects?: readonly LayerEffect[];
  readonly rasterMask?: RasterMask | null;
  readonly vectorMask?: VectorMask | null;
}

export interface NewShapeLayerOptions {
  readonly id: LayerId;
  readonly name: string;
  readonly geometry?: Partial<ShapeGeometry> & Pick<ShapeGeometry, 'width' | 'height'>;
  readonly style?: Partial<ShapeStyle>;
  readonly visible?: boolean;
  readonly opacity?: number;
  readonly fillOpacity?: number;
  readonly blendMode?: BlendMode;
  readonly clipping?: boolean;
  readonly locks?: Partial<LayerLocks>;
  readonly transform?: LayerTransform;
  readonly effects?: readonly LayerEffect[];
  readonly rasterMask?: RasterMask | null;
  readonly vectorMask?: VectorMask | null;
}

export interface NewTextLayerOptions {
  readonly id: LayerId;
  readonly name: string;
  readonly text?: string;
  readonly width: number;
  readonly height: number;
  readonly style?: Partial<TextStyle>;
  readonly visible?: boolean;
  readonly opacity?: number;
  readonly fillOpacity?: number;
  readonly blendMode?: BlendMode;
  readonly clipping?: boolean;
  readonly locks?: Partial<LayerLocks>;
  readonly transform?: LayerTransform;
  readonly effects?: readonly LayerEffect[];
  readonly rasterMask?: RasterMask | null;
  readonly vectorMask?: VectorMask | null;
}

export interface NewSmartObjectLayerOptions {
  readonly id: LayerId;
  readonly name: string;
  readonly source: SmartObjectSource;
  readonly smartFilters?: readonly SmartFilter[];
  readonly visible?: boolean;
  readonly opacity?: number;
  readonly fillOpacity?: number;
  readonly blendMode?: BlendMode;
  readonly clipping?: boolean;
  readonly locks?: Partial<LayerLocks>;
  readonly transform?: LayerTransform;
  readonly effects?: readonly LayerEffect[];
  readonly rasterMask?: RasterMask | null;
  readonly vectorMask?: VectorMask | null;
}

export interface PixelPatch {
  readonly x: number;
  readonly y: number;
  readonly width: number;
  readonly height: number;
  readonly pixels: ArrayLike<number>;
}

export interface MaskPatch {
  readonly x: number;
  readonly y: number;
  readonly width: number;
  readonly height: number;
  readonly coverage: ArrayLike<number>;
}

export class DocumentInvariantError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'DocumentInvariantError';
  }
}

function assertPositiveInteger(value: number, field: string): void {
  if (!Number.isSafeInteger(value) || value <= 0) {
    throw new DocumentInvariantError(`${field} must be a positive integer`);
  }
}

function assertOpacity(value: number): void {
  if (!Number.isFinite(value) || value < 0 || value > 1) {
    throw new DocumentInvariantError('Layer opacity must be between 0 and 1');
  }
}

function assertBlendMode(value: string): asserts value is BlendMode {
  if (!(BLEND_MODES as readonly string[]).includes(value)) {
    throw new DocumentInvariantError(`Unsupported blend mode: ${value}`);
  }
}

function copyLayerLocks(locks?: Partial<LayerLocks>): LayerLocks {
  return {
    transparency: locks?.transparency ?? false,
    pixels: locks?.pixels ?? false,
    position: locks?.position ?? false,
    all: locks?.all ?? false,
  };
}

function validateLayerLocks(locks: LayerLocks): void {
  for (const [name, value] of Object.entries(locks)) {
    if (typeof value !== 'boolean') {
      throw new DocumentInvariantError(`Layer lock ${name} must be boolean`);
    }
  }
}

function assertUnlocked(layer: Layer, lock: keyof LayerLocks, operation: string): void {
  if (layer.locks.all || layer.locks[lock]) {
    throw new DocumentInvariantError(`${operation} is blocked by the layer lock: ${layer.id}`);
  }
}

function assertNotFullyLocked(layer: Layer, operation: string): void {
  if (layer.locks.all) {
    throw new DocumentInvariantError(`${operation} is blocked by the layer lock: ${layer.id}`);
  }
}

function copyAdjustment(adjustment: Adjustment): Adjustment {
  return { ...adjustment };
}

function validateAdjustment(adjustment: Adjustment): void {
  switch (adjustment.type) {
    case 'brightness-contrast':
      assertFinite(adjustment.brightness, 'Adjustment brightness');
      assertFinite(adjustment.contrast, 'Adjustment contrast');
      if (adjustment.brightness < -255 || adjustment.brightness > 255)
        throw new DocumentInvariantError('Adjustment brightness must be between -255 and 255');
      if (adjustment.contrast < -255 || adjustment.contrast > 255)
        throw new DocumentInvariantError('Adjustment contrast must be between -255 and 255');
      return;
    case 'invert':
      return;
    case 'levels':
      for (const [field, value] of Object.entries(adjustment).filter(([key]) => key !== 'type')) {
        assertFinite(value as number, `Adjustment levels ${field}`);
      }
      if (
        adjustment.inputBlack < 0 ||
        adjustment.inputWhite > 255 ||
        adjustment.inputBlack >= adjustment.inputWhite ||
        adjustment.gamma < 0.01 ||
        adjustment.gamma > 9.99 ||
        adjustment.outputBlack < 0 ||
        adjustment.outputWhite > 255 ||
        adjustment.outputBlack > adjustment.outputWhite
      ) {
        throw new DocumentInvariantError('Adjustment levels parameters are out of range');
      }
      return;
    case 'hue-saturation':
      assertFinite(adjustment.hue, 'Adjustment hue');
      assertFinite(adjustment.saturation, 'Adjustment saturation');
      assertFinite(adjustment.lightness, 'Adjustment lightness');
      if (
        adjustment.hue < -180 ||
        adjustment.hue > 180 ||
        adjustment.saturation < -100 ||
        adjustment.saturation > 100 ||
        adjustment.lightness < -100 ||
        adjustment.lightness > 100
      ) {
        throw new DocumentInvariantError('Hue/saturation adjustment parameters are out of range');
      }
      return;
    case 'posterize':
      if (
        !Number.isInteger(adjustment.levels) ||
        adjustment.levels < 2 ||
        adjustment.levels > 256
      ) {
        throw new DocumentInvariantError('Posterize levels must be an integer between 2 and 256');
      }
      return;
    case 'threshold':
      if (!Number.isInteger(adjustment.level) || adjustment.level < 0 || adjustment.level > 255) {
        throw new DocumentInvariantError('Threshold level must be an integer between 0 and 255');
      }
      return;
  }
}

function copyColor(color: RgbaColor): RgbaColor {
  return { ...color };
}

function validateColor(color: RgbaColor, field: string): void {
  for (const [channel, value] of Object.entries(color)) {
    if (!Number.isFinite(value) || value < 0 || value > 255) {
      throw new DocumentInvariantError(`${field} ${channel} must be between 0 and 255`);
    }
  }
}

function copyLayerEffect(effect: LayerEffect): LayerEffect {
  return { ...effect, color: copyColor(effect.color) };
}

function copyLayerEffects(effects: readonly LayerEffect[] = []): readonly LayerEffect[] {
  return effects.map(copyLayerEffect);
}

function validateLayerEffect(effect: LayerEffect): void {
  if (typeof effect.enabled !== 'boolean')
    throw new DocumentInvariantError('Layer effect enabled state must be boolean');
  assertOpacity(effect.opacity);
  validateColor(effect.color, 'Layer effect color');
  if (effect.type === 'color-overlay') {
    assertBlendMode(effect.blendMode);
    return;
  }
  if (effect.type === 'stroke') {
    assertFinite(effect.size, 'Layer effect stroke size');
    if (effect.size < 0 || effect.size > 1_000)
      throw new DocumentInvariantError('Layer effect stroke size is out of range');
    return;
  }
  assertFinite(effect.offsetX, 'Layer effect shadow offset x');
  assertFinite(effect.offsetY, 'Layer effect shadow offset y');
  assertFinite(effect.blur, 'Layer effect shadow blur');
  if (effect.blur < 0 || effect.blur > 1_000)
    throw new DocumentInvariantError('Layer effect shadow blur is out of range');
}

function copySmartFilters(filters: readonly SmartFilter[] = []): readonly SmartFilter[] {
  return filters.map((filter) => ({ ...filter }));
}

function validateSmartFilter(filter: SmartFilter): void {
  assertName(filter.id, 'Smart filter id');
  if (typeof filter.enabled !== 'boolean')
    throw new DocumentInvariantError('Smart filter enabled state must be boolean');
  assertOpacity(filter.opacity);
  if (filter.type === 'gaussian-blur') {
    if (!Number.isInteger(filter.radius) || filter.radius < 0 || filter.radius > 100)
      throw new DocumentInvariantError('Smart Gaussian Blur radius must be between 0 and 100');
  } else if (filter.type === 'brightness-contrast') {
    assertFinite(filter.brightness, 'Smart filter brightness');
    assertFinite(filter.contrast, 'Smart filter contrast');
    if (
      filter.brightness < -255 ||
      filter.brightness > 255 ||
      filter.contrast < -255 ||
      filter.contrast > 255
    )
      throw new DocumentInvariantError('Smart brightness/contrast values are out of range');
  }
}

function copyShapeGeometry(geometry: ShapeGeometry): ShapeGeometry {
  return { ...geometry, commands: geometry.commands.map((command) => ({ ...command })) };
}

function validateShapeGeometry(geometry: ShapeGeometry): void {
  if (!(SHAPE_KINDS as readonly string[]).includes(geometry.type)) {
    throw new DocumentInvariantError(`Unsupported shape type: ${String(geometry.type)}`);
  }
  assertPositiveInteger(geometry.width, 'Shape width');
  assertPositiveInteger(geometry.height, 'Shape height');
  assertFinite(geometry.cornerRadius, 'Shape corner radius');
  if (
    geometry.cornerRadius < 0 ||
    geometry.cornerRadius > Math.min(geometry.width, geometry.height) / 2
  ) {
    throw new DocumentInvariantError('Shape corner radius is out of range');
  }
  if (geometry.type !== 'path') {
    if (geometry.commands.length > 0) {
      throw new DocumentInvariantError('Only path shapes can contain path commands');
    }
    return;
  }
  if (geometry.cornerRadius !== 0) {
    throw new DocumentInvariantError('Path shapes cannot have a corner radius');
  }
  if (geometry.commands.length < 2 || geometry.commands.length > 10_000) {
    throw new DocumentInvariantError('Path shapes require 2 to 10,000 commands');
  }
  let subpathOpen = false;
  let subpathDrawable = false;
  let drawableSegments = 0;
  for (const command of geometry.commands) {
    if (command.type === 'move') {
      assertFinite(command.x, 'Path move x');
      assertFinite(command.y, 'Path move y');
      subpathOpen = true;
      subpathDrawable = false;
      continue;
    }
    if (!subpathOpen) {
      throw new DocumentInvariantError('Each path subpath must begin with a move command');
    }
    if (command.type === 'close') {
      if (!subpathDrawable) {
        throw new DocumentInvariantError('A path cannot close before drawing a segment');
      }
      subpathOpen = false;
      continue;
    }
    assertFinite(command.x, 'Path anchor x');
    assertFinite(command.y, 'Path anchor y');
    if (command.type === 'cubic') {
      assertFinite(command.control1X, 'Path control 1 x');
      assertFinite(command.control1Y, 'Path control 1 y');
      assertFinite(command.control2X, 'Path control 2 x');
      assertFinite(command.control2Y, 'Path control 2 y');
    }
    subpathDrawable = true;
    drawableSegments += 1;
  }
  if (drawableSegments === 0) {
    throw new DocumentInvariantError('A path must contain a drawable segment');
  }
}

function validateVectorMask(mask: VectorMask): void {
  assertPositiveInteger(mask.width, 'Vector mask width');
  assertPositiveInteger(mask.height, 'Vector mask height');
  assertOpacity(mask.density);
  if (typeof mask.enabled !== 'boolean' || typeof mask.inverted !== 'boolean') {
    throw new DocumentInvariantError('Vector mask states must be boolean');
  }
  validateShapeGeometry({
    type: 'path',
    width: mask.width,
    height: mask.height,
    cornerRadius: 0,
    commands: mask.commands,
  });
}

function validateVectorMaskDimensions(
  mask: VectorMask | null,
  width: number,
  height: number,
  label: string,
): void {
  if (mask === null) return;
  validateVectorMask(mask);
  if (mask.width !== width || mask.height !== height) {
    throw new DocumentInvariantError(`Vector mask must match its ${label}`);
  }
}

function copyShapeStyle(style: ShapeStyle): ShapeStyle {
  return {
    fill: style.fill === null ? null : copyColor(style.fill),
    stroke: style.stroke === null ? null : copyColor(style.stroke),
    strokeWidth: style.strokeWidth,
  };
}

function validateShapeStyle(style: ShapeStyle): void {
  if (style.fill !== null) validateColor(style.fill, 'Shape fill');
  if (style.stroke !== null) validateColor(style.stroke, 'Shape stroke');
  assertFinite(style.strokeWidth, 'Shape stroke width');
  if (style.strokeWidth < 0 || style.strokeWidth > 1_000) {
    throw new DocumentInvariantError('Shape stroke width is out of range');
  }
  if (style.fill === null && (style.stroke === null || style.strokeWidth === 0)) {
    throw new DocumentInvariantError('Shape must have a visible fill or stroke');
  }
}

function copyTextStyle(style: TextStyle): TextStyle {
  return { ...style, color: copyColor(style.color) };
}

function validateTextStyle(style: TextStyle): void {
  assertName(style.fontFamily, 'Text font family');
  assertFinite(style.fontSize, 'Text font size');
  assertFinite(style.fontWeight, 'Text font weight');
  assertFinite(style.lineHeight, 'Text line height');
  if (style.fontSize <= 0 || style.fontSize > 10_000) {
    throw new DocumentInvariantError('Text font size is out of range');
  }
  if (style.fontWeight < 1 || style.fontWeight > 1_000) {
    throw new DocumentInvariantError('Text font weight is out of range');
  }
  if (style.lineHeight < 0.5 || style.lineHeight > 10) {
    throw new DocumentInvariantError('Text line height is out of range');
  }
  if (!(TEXT_ALIGNMENTS as readonly string[]).includes(style.align)) {
    throw new DocumentInvariantError(`Unsupported text alignment: ${String(style.align)}`);
  }
  if (typeof style.italic !== 'boolean') {
    throw new DocumentInvariantError('Text italic state must be boolean');
  }
  validateColor(style.color, 'Text color');
}

function layerBase(options: {
  visible?: boolean;
  opacity?: number;
  fillOpacity?: number;
  blendMode?: BlendMode;
  clipping?: boolean;
  locks?: Partial<LayerLocks>;
  transform?: LayerTransform;
  effects?: readonly LayerEffect[];
}) {
  const opacity = options.opacity ?? 1;
  const fillOpacity = options.fillOpacity ?? 1;
  const blendMode = options.blendMode ?? 'normal';
  assertOpacity(opacity);
  assertOpacity(fillOpacity);
  assertBlendMode(blendMode);
  return {
    visible: options.visible ?? true,
    opacity,
    fillOpacity,
    blendMode,
    clipping: options.clipping ?? false,
    locks: copyLayerLocks(options.locks),
    transform: copyLayerTransform(options.transform),
    effects: copyLayerEffects(options.effects),
  };
}

function assertFinite(value: number, field: string): void {
  if (!Number.isFinite(value)) throw new DocumentInvariantError(`${field} must be finite`);
}

function assertInteger(value: number, field: string): void {
  if (!Number.isSafeInteger(value)) throw new DocumentInvariantError(`${field} must be an integer`);
}

export function identityLayerTransform(): LayerTransform {
  return { a: 1, b: 0, c: 0, d: 1, e: 0, f: 0 };
}

export function validateLayerTransform(transform: LayerTransform): void {
  assertFinite(transform.a, 'Layer transform a');
  assertFinite(transform.b, 'Layer transform b');
  assertFinite(transform.c, 'Layer transform c');
  assertFinite(transform.d, 'Layer transform d');
  assertFinite(transform.e, 'Layer transform e');
  assertFinite(transform.f, 'Layer transform f');
  const determinant = transform.a * transform.d - transform.b * transform.c;
  if (!Number.isFinite(determinant) || Math.abs(determinant) <= 1e-12) {
    throw new DocumentInvariantError('Layer transform must be invertible');
  }
}

export function copyLayerTransform(transform?: LayerTransform): LayerTransform {
  const next = transform ?? identityLayerTransform();
  validateLayerTransform(next);
  return { ...next };
}

/** Returns the matrix that applies `local` first and `parent` second. */
export function multiplyLayerTransforms(
  parent: LayerTransform,
  local: LayerTransform,
): LayerTransform {
  validateLayerTransform(parent);
  validateLayerTransform(local);
  return {
    a: parent.a * local.a + parent.c * local.b,
    b: parent.b * local.a + parent.d * local.b,
    c: parent.a * local.c + parent.c * local.d,
    d: parent.b * local.c + parent.d * local.d,
    e: parent.a * local.e + parent.c * local.f + parent.e,
    f: parent.b * local.e + parent.d * local.f + parent.f,
  };
}

export function invertLayerTransform(transform: LayerTransform): LayerTransform {
  validateLayerTransform(transform);
  const determinant = transform.a * transform.d - transform.b * transform.c;
  return {
    a: transform.d / determinant,
    b: -transform.b / determinant,
    c: -transform.c / determinant,
    d: transform.a / determinant,
    e: (transform.c * transform.f - transform.d * transform.e) / determinant,
    f: (transform.b * transform.e - transform.a * transform.f) / determinant,
  };
}

export function transformPoint(transform: LayerTransform, x: number, y: number): TransformPoint {
  validateLayerTransform(transform);
  assertFinite(x, 'Point x');
  assertFinite(y, 'Point y');
  return {
    x: transform.a * x + transform.c * y + transform.e,
    y: transform.b * x + transform.d * y + transform.f,
  };
}

function copySelection(selection: SelectionMask | null | undefined): SelectionMask | null {
  if (selection == null) return null;
  // TiledCoverageStore is immutable; share the canonical store without densifying.
  return createSelectionMask({
    width: selection.width,
    height: selection.height,
    coverageStore: selection.coverageStore,
  });
}

function copyAlphaChannel(channel: AlphaChannel): AlphaChannel {
  // TiledCoverageStore is immutable; share the canonical store without densifying.
  return {
    id: channel.id,
    name: channel.name,
    width: channel.width,
    height: channel.height,
    coverageStore: channel.coverageStore,
  };
}

function copyRasterMask(mask: RasterMask | null | undefined): RasterMask | null {
  if (mask == null) return null;
  return { ...mask, coverageStore: mask.coverageStore };
}

function copyVectorMask(mask: VectorMask | null | undefined): VectorMask | null {
  if (mask == null) return null;
  return { ...mask, commands: mask.commands.map((command) => ({ ...command })) };
}

function assertName(value: string, field: string): void {
  if (value.trim().length === 0) {
    throw new DocumentInvariantError(`${field} cannot be empty`);
  }
}

function copyLayer(layer: Layer): Layer {
  if (layer.kind === 'pixel') {
    return {
      ...layer,
      transform: copyLayerTransform(layer.transform),
      pixelStore: layer.pixelStore,
      rasterMask: copyRasterMask(layer.rasterMask),
      vectorMask: copyVectorMask(layer.vectorMask),
      locks: copyLayerLocks(layer.locks),
      effects: copyLayerEffects(layer.effects),
    };
  }
  if (layer.kind === 'adjustment') {
    return {
      ...layer,
      transform: copyLayerTransform(layer.transform),
      locks: copyLayerLocks(layer.locks),
      effects: copyLayerEffects(layer.effects),
      adjustment: copyAdjustment(layer.adjustment),
      rasterMask: copyRasterMask(layer.rasterMask),
      vectorMask: copyVectorMask(layer.vectorMask),
    };
  }
  if (layer.kind === 'shape') {
    return {
      ...layer,
      transform: copyLayerTransform(layer.transform),
      locks: copyLayerLocks(layer.locks),
      effects: copyLayerEffects(layer.effects),
      geometry: copyShapeGeometry(layer.geometry),
      style: copyShapeStyle(layer.style),
      rasterMask: copyRasterMask(layer.rasterMask),
      vectorMask: copyVectorMask(layer.vectorMask),
    };
  }
  if (layer.kind === 'text') {
    return {
      ...layer,
      transform: copyLayerTransform(layer.transform),
      locks: copyLayerLocks(layer.locks),
      effects: copyLayerEffects(layer.effects),
      style: copyTextStyle(layer.style),
      rasterMask: copyRasterMask(layer.rasterMask),
      vectorMask: copyVectorMask(layer.vectorMask),
    };
  }
  if (layer.kind === 'smart-object') {
    return {
      ...layer,
      transform: copyLayerTransform(layer.transform),
      locks: copyLayerLocks(layer.locks),
      effects: copyLayerEffects(layer.effects),
      source: copySmartObjectSource(layer.source),
      smartFilters: copySmartFilters(layer.smartFilters),
      rasterMask: copyRasterMask(layer.rasterMask),
      vectorMask: copyVectorMask(layer.vectorMask),
    };
  }
  return {
    ...layer,
    transform: copyLayerTransform(layer.transform),
    locks: copyLayerLocks(layer.locks),
    effects: copyLayerEffects(layer.effects),
    children: layer.children.map(copyLayer),
    rasterMask: copyRasterMask(layer.rasterMask),
    vectorMask: copyVectorMask(layer.vectorMask),
  };
}

function copySmartObjectSource(source: SmartObjectSource): SmartObjectSource {
  return {
    id: source.id,
    name: source.name,
    canvas: { ...source.canvas },
    layers: source.layers.map(copyLayer),
  };
}

export function createPixelLayer(options: NewPixelLayerOptions): PixelLayer {
  assertPositiveInteger(options.width, 'Pixel layer width');
  assertPositiveInteger(options.height, 'Pixel layer height');
  assertName(options.id, 'Layer id');
  assertName(options.name, 'Layer name');
  if (options.pixels !== undefined && options.pixelStore !== undefined) {
    throw new DocumentInvariantError('Provide pixels or a pixel store, not both');
  }
  const pixelStore =
    options.pixelStore ??
    (options.pixels === undefined
      ? TiledPixelStore.empty(options.width, options.height)
      : TiledPixelStore.fromContiguous(options.width, options.height, options.pixels));
  if (pixelStore.width !== options.width || pixelStore.height !== options.height) {
    throw new DocumentInvariantError('Pixel store dimensions must match the pixel layer');
  }
  const layer: PixelLayer = {
    kind: 'pixel',
    id: options.id,
    name: options.name,
    width: options.width,
    height: options.height,
    pixelStore,
    ...layerBase(options),
    rasterMask: copyRasterMask(options.rasterMask),
    vectorMask: copyVectorMask(options.vectorMask),
  };
  validateLayers([layer]);
  return layer;
}

export function createGroupLayer(options: NewGroupLayerOptions): GroupLayer {
  assertName(options.id, 'Layer id');
  assertName(options.name, 'Layer name');
  const group: GroupLayer = {
    kind: 'group',
    id: options.id,
    name: options.name,
    children: (options.children ?? []).map(copyLayer),
    ...layerBase(options),
    compositing: options.compositing ?? 'pass-through',
    rasterMask: copyRasterMask(options.rasterMask),
    vectorMask: copyVectorMask(options.vectorMask),
  };
  validateLayers([group]);
  return group;
}

export function createAdjustmentLayer(options: NewAdjustmentLayerOptions): AdjustmentLayer {
  assertName(options.id, 'Layer id');
  assertName(options.name, 'Layer name');
  const adjustment = copyAdjustment(
    options.adjustment ?? { type: 'brightness-contrast', brightness: 0, contrast: 0 },
  );
  validateAdjustment(adjustment);
  const layer: AdjustmentLayer = {
    kind: 'adjustment',
    id: options.id,
    name: options.name,
    adjustment,
    ...layerBase(options),
    rasterMask: copyRasterMask(options.rasterMask),
    vectorMask: copyVectorMask(options.vectorMask),
  };
  validateLayers([layer]);
  return layer;
}

export function createShapeLayer(options: NewShapeLayerOptions): ShapeLayer {
  assertName(options.id, 'Layer id');
  assertName(options.name, 'Layer name');
  const geometry: ShapeGeometry = {
    type: options.geometry?.type ?? 'rectangle',
    width: options.geometry?.width ?? 100,
    height: options.geometry?.height ?? 100,
    cornerRadius: options.geometry?.cornerRadius ?? 0,
    commands: options.geometry?.commands ?? [],
  };
  const style: ShapeStyle = {
    fill:
      options.style?.fill === undefined
        ? { red: 52, green: 211, blue: 153, alpha: 255 }
        : options.style.fill,
    stroke: options.style?.stroke ?? null,
    strokeWidth: options.style?.strokeWidth ?? 0,
  };
  validateShapeGeometry(geometry);
  validateShapeStyle(style);
  const layer: ShapeLayer = {
    kind: 'shape',
    id: options.id,
    name: options.name,
    geometry: copyShapeGeometry(geometry),
    style: copyShapeStyle(style),
    ...layerBase(options),
    rasterMask: copyRasterMask(options.rasterMask),
    vectorMask: copyVectorMask(options.vectorMask),
  };
  validateLayers([layer]);
  return layer;
}

export function createTextLayer(options: NewTextLayerOptions): TextLayer {
  assertName(options.id, 'Layer id');
  assertName(options.name, 'Layer name');
  assertPositiveInteger(options.width, 'Text layer width');
  assertPositiveInteger(options.height, 'Text layer height');
  const style: TextStyle = {
    fontFamily: options.style?.fontFamily ?? 'Arial',
    fontSize: options.style?.fontSize ?? 48,
    fontWeight: options.style?.fontWeight ?? 400,
    italic: options.style?.italic ?? false,
    lineHeight: options.style?.lineHeight ?? 1.2,
    align: options.style?.align ?? 'left',
    color: options.style?.color ?? { red: 255, green: 255, blue: 255, alpha: 255 },
  };
  validateTextStyle(style);
  const layer: TextLayer = {
    kind: 'text',
    id: options.id,
    name: options.name,
    text: options.text ?? 'Text',
    width: options.width,
    height: options.height,
    style: copyTextStyle(style),
    ...layerBase(options),
    rasterMask: copyRasterMask(options.rasterMask),
    vectorMask: copyVectorMask(options.vectorMask),
  };
  validateLayers([layer]);
  return layer;
}

export function createSmartObjectLayer(options: NewSmartObjectLayerOptions): SmartObjectLayer {
  assertName(options.id, 'Layer id');
  assertName(options.name, 'Layer name');
  assertName(options.source.id, 'Smart object source id');
  assertName(options.source.name, 'Smart object source name');
  assertPositiveInteger(options.source.canvas.width, 'Smart object source width');
  assertPositiveInteger(options.source.canvas.height, 'Smart object source height');
  const layer: SmartObjectLayer = {
    kind: 'smart-object',
    id: options.id,
    name: options.name,
    source: copySmartObjectSource(options.source),
    smartFilters: copySmartFilters(options.smartFilters),
    ...layerBase(options),
    rasterMask: copyRasterMask(options.rasterMask),
    vectorMask: copyVectorMask(options.vectorMask),
  };
  validateLayers([layer]);
  return layer;
}

export function createDocument(options: NewDocumentOptions): ImageDocument {
  assertName(options.id, 'Document id');
  assertName(options.name, 'Document name');
  assertPositiveInteger(options.width, 'Canvas width');
  assertPositiveInteger(options.height, 'Canvas height');
  const document: ImageDocument = {
    id: options.id,
    name: options.name,
    canvas: { width: options.width, height: options.height },
    layers: (options.layers ?? []).map(copyLayer),
    selection: copySelection(options.selection),
    channels: (options.channels ?? []).map(copyAlphaChannel),
    guides: (options.guides ?? []).map(copyGuide),
    grid: copyGrid(options.grid),
    paths: (options.paths ?? []).map(copyDocumentPath),
    preservation: copyPreservation(options.preservation),
  };
  validateDocument(document);
  return document;
}

export function createDocumentGuide(options: NewDocumentGuideOptions): DocumentGuide {
  assertName(options.id, 'Guide id');
  if (!Number.isFinite(options.position)) {
    throw new DocumentInvariantError('Guide position must be a finite number');
  }
  return {
    id: options.id,
    direction: options.direction,
    position: options.position,
  };
}

export function createDocumentPath(options: NewDocumentPathOptions): DocumentPath {
  assertName(options.id, 'Document path id');
  assertName(options.name, 'Document path name');
  const commands = (options.commands ?? []).map(copyPathCommand);
  if (commands.length > 10_000) {
    throw new DocumentInvariantError('Document paths cannot exceed 10,000 commands');
  }
  for (const command of commands) {
    validatePathCommand(command);
  }
  return {
    id: options.id,
    name: options.name,
    open: options.open === true,
    commands,
  };
}

function copyPathCommand(command: PathCommand): PathCommand {
  return { ...command };
}

function validatePathCommand(command: PathCommand): void {
  if (command.type === 'close') return;
  if (command.type === 'move' || command.type === 'line') {
    assertFinite(command.x, 'Path command x');
    assertFinite(command.y, 'Path command y');
    return;
  }
  assertFinite(command.x, 'Path command x');
  assertFinite(command.y, 'Path command y');
  assertFinite(command.control1X, 'Path command control1X');
  assertFinite(command.control1Y, 'Path command control1Y');
  assertFinite(command.control2X, 'Path command control2X');
  assertFinite(command.control2Y, 'Path command control2Y');
}

function copyGuide(guide: DocumentGuide): DocumentGuide {
  return createDocumentGuide(guide);
}

function copyDocumentPath(path: DocumentPath): DocumentPath {
  return createDocumentPath(path);
}

function copyGrid(grid: DocumentGrid | null | undefined): DocumentGrid | null {
  if (grid == null) return null;
  if (
    !Number.isFinite(grid.horizontal) ||
    !Number.isFinite(grid.vertical) ||
    grid.horizontal <= 0 ||
    grid.vertical <= 0
  ) {
    throw new DocumentInvariantError('Document grid spacing must be positive finite numbers');
  }
  return { horizontal: grid.horizontal, vertical: grid.vertical };
}

function copyPreservation(
  value: DocumentPreservation | null | undefined,
): DocumentPreservation | null {
  if (value == null) return null;
  // Structured clone via JSON keeps the bag free of non-serializable handles.
  try {
    return JSON.parse(JSON.stringify(value)) as DocumentPreservation;
  } catch {
    throw new DocumentInvariantError('Document preservation must be JSON-serializable');
  }
}

export function createAlphaChannel(options: NewAlphaChannelOptions): AlphaChannel {
  assertName(options.id, 'Alpha channel id');
  assertName(options.name, 'Alpha channel name');
  assertPositiveInteger(options.width, 'Alpha channel width');
  assertPositiveInteger(options.height, 'Alpha channel height');
  if (options.coverageStore !== undefined) {
    if (
      !(options.coverageStore instanceof TiledCoverageStore) ||
      options.coverageStore.width !== options.width ||
      options.coverageStore.height !== options.height
    ) {
      throw new DocumentInvariantError('Alpha channel coverage store must match its dimensions');
    }
    return {
      id: options.id,
      name: options.name,
      width: options.width,
      height: options.height,
      coverageStore: options.coverageStore,
    };
  }
  const expectedLength = options.width * options.height;
  const coverage =
    options.coverage === undefined
      ? new Uint8ClampedArray(expectedLength)
      : new Uint8ClampedArray(options.coverage);
  if (coverage.length !== expectedLength) {
    throw new DocumentInvariantError(
      `Alpha channel coverage must contain ${String(expectedLength)} values`,
    );
  }
  return {
    id: options.id,
    name: options.name,
    width: options.width,
    height: options.height,
    coverageStore: TiledCoverageStore.fromContiguous(options.width, options.height, coverage),
  };
}

function validateLayers(
  layers: readonly Layer[],
  ids = new Set<string>(),
  smartObjectDepth = 0,
): void {
  for (const layer of layers) {
    assertName(layer.id, 'Layer id');
    assertName(layer.name, 'Layer name');
    assertOpacity(layer.opacity);
    assertOpacity(layer.fillOpacity);
    assertBlendMode(layer.blendMode);
    if (typeof layer.clipping !== 'boolean') {
      throw new DocumentInvariantError('Layer clipping state must be boolean');
    }
    validateLayerLocks(layer.locks);
    validateLayerTransform(layer.transform);
    for (const effect of layer.effects) validateLayerEffect(effect);
    if (ids.has(layer.id)) {
      throw new DocumentInvariantError(`Duplicate layer id: ${layer.id}`);
    }
    ids.add(layer.id);
    if (layer.kind === 'group') {
      const compositing: unknown = layer.compositing;
      if (compositing !== 'pass-through' && compositing !== 'isolated') {
        throw new DocumentInvariantError(`Invalid group compositing mode: ${String(compositing)}`);
      }
      if (layer.vectorMask !== null) validateVectorMask(layer.vectorMask);
      if (layer.rasterMask !== null) {
        assertPositiveInteger(layer.rasterMask.width, 'Group mask width');
        assertPositiveInteger(layer.rasterMask.height, 'Group mask height');
        assertOpacity(layer.rasterMask.density);
        if (
          !(layer.rasterMask.coverageStore instanceof TiledCoverageStore) ||
          layer.rasterMask.coverageStore.width !== layer.rasterMask.width ||
          layer.rasterMask.coverageStore.height !== layer.rasterMask.height
        ) {
          throw new DocumentInvariantError(`Invalid group mask buffer: ${layer.id}`);
        }
      }
      validateLayers(layer.children, ids, smartObjectDepth);
    } else if (layer.kind === 'adjustment') {
      validateAdjustment(layer.adjustment);
      if (layer.rasterMask !== null) {
        assertPositiveInteger(layer.rasterMask.width, 'Adjustment mask width');
        assertPositiveInteger(layer.rasterMask.height, 'Adjustment mask height');
        assertOpacity(layer.rasterMask.density);
        if (
          !(layer.rasterMask.coverageStore instanceof TiledCoverageStore) ||
          layer.rasterMask.coverageStore.width !== layer.rasterMask.width ||
          layer.rasterMask.coverageStore.height !== layer.rasterMask.height
        ) {
          throw new DocumentInvariantError(`Invalid adjustment mask buffer: ${layer.id}`);
        }
      }
      if (layer.vectorMask !== null) validateVectorMask(layer.vectorMask);
    } else if (layer.kind === 'shape') {
      validateShapeGeometry(layer.geometry);
      validateShapeStyle(layer.style);
      if (
        layer.rasterMask !== null &&
        (layer.rasterMask.width !== layer.geometry.width ||
          layer.rasterMask.height !== layer.geometry.height ||
          !(layer.rasterMask.coverageStore instanceof TiledCoverageStore) ||
          layer.rasterMask.coverageStore.width !== layer.geometry.width ||
          layer.rasterMask.coverageStore.height !== layer.geometry.height)
      ) {
        throw new DocumentInvariantError(`Raster mask must match its shape layer: ${layer.id}`);
      }
      validateVectorMaskDimensions(
        layer.vectorMask,
        layer.geometry.width,
        layer.geometry.height,
        `shape layer: ${layer.id}`,
      );
    } else if (layer.kind === 'text') {
      assertPositiveInteger(layer.width, 'Text layer width');
      assertPositiveInteger(layer.height, 'Text layer height');
      if (typeof layer.text !== 'string') {
        throw new DocumentInvariantError('Text layer content must be a string');
      }
      validateTextStyle(layer.style);
      if (
        layer.rasterMask !== null &&
        (layer.rasterMask.width !== layer.width ||
          layer.rasterMask.height !== layer.height ||
          !(layer.rasterMask.coverageStore instanceof TiledCoverageStore) ||
          layer.rasterMask.coverageStore.width !== layer.width ||
          layer.rasterMask.coverageStore.height !== layer.height)
      ) {
        throw new DocumentInvariantError(`Raster mask must match its text layer: ${layer.id}`);
      }
      validateVectorMaskDimensions(
        layer.vectorMask,
        layer.width,
        layer.height,
        `text layer: ${layer.id}`,
      );
    } else if (layer.kind === 'smart-object') {
      if (smartObjectDepth >= 16) {
        throw new DocumentInvariantError('Smart object nesting exceeds 16 levels');
      }
      assertName(layer.source.id, 'Smart object source id');
      assertName(layer.source.name, 'Smart object source name');
      assertPositiveInteger(layer.source.canvas.width, 'Smart object source width');
      assertPositiveInteger(layer.source.canvas.height, 'Smart object source height');
      validateLayers(layer.source.layers, new Set<string>(), smartObjectDepth + 1);
      const filterIds = new Set<string>();
      for (const filter of layer.smartFilters) {
        validateSmartFilter(filter);
        if (filterIds.has(filter.id))
          throw new DocumentInvariantError(`Duplicate smart filter id: ${filter.id}`);
        filterIds.add(filter.id);
      }
      if (
        layer.rasterMask !== null &&
        (layer.rasterMask.width !== layer.source.canvas.width ||
          layer.rasterMask.height !== layer.source.canvas.height ||
          !(layer.rasterMask.coverageStore instanceof TiledCoverageStore) ||
          layer.rasterMask.coverageStore.width !== layer.source.canvas.width ||
          layer.rasterMask.coverageStore.height !== layer.source.canvas.height)
      ) {
        throw new DocumentInvariantError(`Raster mask must match its smart object: ${layer.id}`);
      }
      validateVectorMaskDimensions(
        layer.vectorMask,
        layer.source.canvas.width,
        layer.source.canvas.height,
        `smart object: ${layer.id}`,
      );
    } else {
      assertPositiveInteger(layer.width, 'Pixel layer width');
      assertPositiveInteger(layer.height, 'Pixel layer height');
      if (
        !(layer.pixelStore instanceof TiledPixelStore) ||
        layer.pixelStore.width !== layer.width ||
        layer.pixelStore.height !== layer.height
      ) {
        throw new DocumentInvariantError(`Invalid pixel store for layer: ${layer.id}`);
      }
      if (layer.rasterMask !== null) {
        assertPositiveInteger(layer.rasterMask.width, 'Raster mask width');
        assertPositiveInteger(layer.rasterMask.height, 'Raster mask height');
        assertOpacity(layer.rasterMask.density);
        if (typeof layer.rasterMask.enabled !== 'boolean') {
          throw new DocumentInvariantError('Raster mask enabled state must be boolean');
        }
        if (
          layer.rasterMask.width !== layer.width ||
          layer.rasterMask.height !== layer.height ||
          !(layer.rasterMask.coverageStore instanceof TiledCoverageStore) ||
          layer.rasterMask.coverageStore.width !== layer.width ||
          layer.rasterMask.coverageStore.height !== layer.height
        ) {
          throw new DocumentInvariantError(`Raster mask must match its pixel layer: ${layer.id}`);
        }
      }
      validateVectorMaskDimensions(
        layer.vectorMask,
        layer.width,
        layer.height,
        `pixel layer: ${layer.id}`,
      );
    }
  }
}

export function validateDocument(document: ImageDocument): void {
  assertName(document.id, 'Document id');
  assertName(document.name, 'Document name');
  assertPositiveInteger(document.canvas.width, 'Canvas width');
  assertPositiveInteger(document.canvas.height, 'Canvas height');
  validateLayers(document.layers);
  const validateDocumentSpaceMasks = (layers: readonly Layer[]): void => {
    for (const layer of layers) {
      if (
        (layer.kind === 'group' || layer.kind === 'adjustment') &&
        layer.vectorMask !== null &&
        (layer.vectorMask.width !== document.canvas.width ||
          layer.vectorMask.height !== document.canvas.height)
      ) {
        throw new DocumentInvariantError(
          `${layer.kind === 'group' ? 'Group' : 'Adjustment'} vector mask must match the canvas: ${layer.id}`,
        );
      }
      if (
        layer.kind === 'group' &&
        layer.rasterMask !== null &&
        (layer.rasterMask.width !== document.canvas.width ||
          layer.rasterMask.height !== document.canvas.height)
      ) {
        throw new DocumentInvariantError(`Group raster mask must match the canvas: ${layer.id}`);
      }
      if (layer.kind === 'group') validateDocumentSpaceMasks(layer.children);
      else if (
        layer.kind === 'adjustment' &&
        layer.rasterMask !== null &&
        (layer.rasterMask.width !== document.canvas.width ||
          layer.rasterMask.height !== document.canvas.height)
      ) {
        throw new DocumentInvariantError(`Adjustment mask must match the canvas: ${layer.id}`);
      }
    }
  };
  validateDocumentSpaceMasks(document.layers);
  if (document.selection !== null) {
    assertPositiveInteger(document.selection.width, 'Selection width');
    assertPositiveInteger(document.selection.height, 'Selection height');
    if (
      document.selection.width !== document.canvas.width ||
      document.selection.height !== document.canvas.height ||
      !(document.selection.coverageStore instanceof TiledCoverageStore) ||
      document.selection.coverageStore.width !== document.canvas.width ||
      document.selection.coverageStore.height !== document.canvas.height
    ) {
      throw new DocumentInvariantError('Selection mask must match the document canvas');
    }
  }
  const channelIds = new Set<string>();
  for (const channel of document.channels) {
    assertName(channel.id, 'Alpha channel id');
    assertName(channel.name, 'Alpha channel name');
    assertPositiveInteger(channel.width, 'Alpha channel width');
    assertPositiveInteger(channel.height, 'Alpha channel height');
    if (channelIds.has(channel.id)) {
      throw new DocumentInvariantError(`Duplicate alpha channel id: ${channel.id}`);
    }
    channelIds.add(channel.id);
    if (
      channel.width !== document.canvas.width ||
      channel.height !== document.canvas.height ||
      !(channel.coverageStore instanceof TiledCoverageStore) ||
      channel.coverageStore.width !== document.canvas.width ||
      channel.coverageStore.height !== document.canvas.height
    ) {
      throw new DocumentInvariantError(
        `Alpha channel must match the document canvas: ${channel.id}`,
      );
    }
  }
  const guides: readonly DocumentGuide[] = document.guides;
  if (!Array.isArray(document.guides as unknown)) {
    throw new DocumentInvariantError('Document guides must be an array');
  }
  const guideIds = new Set<string>();
  for (const guide of guides) {
    assertName(guide.id, 'Guide id');
    if (!Number.isFinite(guide.position)) {
      throw new DocumentInvariantError(`Guide position must be finite: ${guide.id}`);
    }
    if (guideIds.has(guide.id)) {
      throw new DocumentInvariantError(`Duplicate guide id: ${guide.id}`);
    }
    guideIds.add(guide.id);
  }
  if (document.grid !== null) {
    if (
      !Number.isFinite(document.grid.horizontal) ||
      !Number.isFinite(document.grid.vertical) ||
      document.grid.horizontal <= 0 ||
      document.grid.vertical <= 0
    ) {
      throw new DocumentInvariantError('Document grid spacing must be positive finite numbers');
    }
  }
  const paths: readonly DocumentPath[] = document.paths;
  if (!Array.isArray(document.paths as unknown)) {
    throw new DocumentInvariantError('Document paths must be an array');
  }
  const pathIds = new Set<string>();
  for (const path of paths) {
    assertName(path.id, 'Document path id');
    assertName(path.name, 'Document path name');
    if (pathIds.has(path.id)) {
      throw new DocumentInvariantError(`Duplicate document path id: ${path.id}`);
    }
    pathIds.add(path.id);
    const commands: readonly PathCommand[] = path.commands;
    if (!Array.isArray(path.commands as unknown) || commands.length > 10_000) {
      throw new DocumentInvariantError(`Document path commands invalid: ${path.id}`);
    }
    for (const command of commands) {
      validatePathCommand(command);
    }
  }
}

export function createRectangularSelection(
  canvas: DocumentCanvas,
  rectangle: SelectionRectangle,
): SelectionMask {
  assertPositiveInteger(canvas.width, 'Canvas width');
  assertPositiveInteger(canvas.height, 'Canvas height');
  assertInteger(rectangle.x, 'Selection x');
  assertInteger(rectangle.y, 'Selection y');
  assertPositiveInteger(rectangle.width, 'Selection width');
  assertPositiveInteger(rectangle.height, 'Selection height');
  const left = Math.max(0, rectangle.x);
  const top = Math.max(0, rectangle.y);
  const right = Math.min(canvas.width, rectangle.x + rectangle.width);
  const bottom = Math.min(canvas.height, rectangle.y + rectangle.height);
  let store = TiledCoverageStore.empty(canvas.width, canvas.height);
  if (right > left && bottom > top) {
    const patchWidth = right - left;
    const patchHeight = bottom - top;
    store = store.write({
      x: left,
      y: top,
      width: patchWidth,
      height: patchHeight,
      coverage: new Uint8ClampedArray(patchWidth * patchHeight).fill(255),
    }).store;
  }
  return createSelectionMask({
    width: canvas.width,
    height: canvas.height,
    coverageStore: store,
  });
}

function assertSelectionMatchesCanvas(selection: SelectionMask, canvas: DocumentCanvas): void {
  if (
    selection.width !== canvas.width ||
    selection.height !== canvas.height ||
    !(selection.coverageStore instanceof TiledCoverageStore) ||
    selection.coverageStore.width !== canvas.width ||
    selection.coverageStore.height !== canvas.height
  ) {
    throw new DocumentInvariantError('Selection mask must match the document canvas');
  }
}

function multiplyCoverage(left: number, right: number): number {
  return Math.round((left * right) / 255);
}

function assertSelectionCombineMode(mode: SelectionCombineMode): void {
  const candidate: unknown = mode;
  if (
    candidate !== 'replace' &&
    candidate !== 'add' &&
    candidate !== 'subtract' &&
    candidate !== 'intersect'
  ) {
    throw new DocumentInvariantError('Selection mode is invalid');
  }
}

/**
 * Tile-local selection combine for sparse coverage stores with implicit defaults.
 * Only deviation-tile coordinates are visited; the output default is combined once.
 */
function combineSelectionSparse(
  left: TiledCoverageStore,
  right: TiledCoverageStore,
  mode: Exclude<SelectionCombineMode, 'replace'>,
): TiledCoverageStore {
  const width = left.width;
  const height = left.height;
  const tileSize = left.tileSize;
  if (right.width !== width || right.height !== height) {
    throw new DocumentInvariantError('Selection coverage stores must have matching dimensions');
  }

  const columnCount = Math.ceil(width / tileSize);
  const rowCount = Math.ceil(height / tileSize);
  const keys = new Set<number>();
  const addOutputKeys = (store: TiledCoverageStore) => {
    for (const tile of store.tiles()) {
      const firstColumn = Math.floor(tile.x / tileSize);
      const lastColumn = Math.floor((tile.x + tile.width - 1) / tileSize);
      const firstRow = Math.floor(tile.y / tileSize);
      const lastRow = Math.floor((tile.y + tile.height - 1) / tileSize);
      for (let row = firstRow; row <= lastRow; row += 1) {
        for (let column = firstColumn; column <= lastColumn; column += 1) {
          keys.add(column + row * columnCount);
        }
      }
    }
  };
  addOutputKeys(left);
  addOutputKeys(right);

  const combine = (leftCoverage: number, rightCoverage: number) => {
    if (mode === 'add') {
      return leftCoverage + rightCoverage - multiplyCoverage(leftCoverage, rightCoverage);
    }
    if (mode === 'subtract') return multiplyCoverage(leftCoverage, 255 - rightCoverage);
    return multiplyCoverage(leftCoverage, rightCoverage);
  };
  let store = TiledCoverageStore.uniform(
    width,
    height,
    combine(left.defaultCoverage, right.defaultCoverage),
    tileSize,
  );
  for (const key of keys) {
    const column = key % columnCount;
    const row = Math.floor(key / columnCount);
    if (column < 0 || row < 0 || column >= columnCount || row >= rowCount) continue;
    const x = column * tileSize;
    const y = row * tileSize;
    const tileWidth = Math.min(tileSize, width - x);
    const tileHeight = Math.min(tileSize, height - y);
    const region = { x, y, width: tileWidth, height: tileHeight };
    const leftCoverage = left.read(region);
    const rightCoverage = right.read(region);
    const coverage = new Uint8ClampedArray(leftCoverage.length);
    for (let index = 0; index < coverage.length; index += 1) {
      const l = leftCoverage[index] ?? 0;
      const r = rightCoverage[index] ?? 0;
      coverage[index] = combine(l, r);
    }
    store = store.write({ ...region, coverage }).store;
  }
  return store;
}

function combinedSelection(
  document: ImageDocument,
  operand: SelectionMask,
  mode: SelectionCombineMode,
): SelectionMask | null {
  assertSelectionMatchesCanvas(operand, document.canvas);
  if (mode === 'replace') return copySelection(operand) as SelectionMask;
  const current = document.selection;
  if (current !== null) assertSelectionMatchesCanvas(current, document.canvas);

  // Null current means full-canvas coverage (select-all).
  if (current === null) {
    if (mode === 'add') {
      // Full + anything remains full-canvas selection (null).
      return null;
    }
    if (mode === 'intersect') {
      return copySelection(operand) as SelectionMask;
    }
    // subtract from full-canvas ≡ invert(operand). Tile-local via invertCoverageStore
    // (sparse operand stays sparse; fully selected operand → empty; empty → select-all).
    const inverted = invertCoverageStore(operand.coverageStore);
    if (inverted === null) {
      return null;
    }
    return createSelectionMask({
      width: document.canvas.width,
      height: document.canvas.height,
      coverageStore: inverted,
    });
  }

  // Ordinary sparse+sparse combines stay tile-local.
  const store = combineSelectionSparse(current.coverageStore, operand.coverageStore, mode);
  return createSelectionMask({
    width: document.canvas.width,
    height: document.canvas.height,
    coverageStore: store,
  });
}

/** Combines arbitrary full-resolution selection coverage with the current document selection. */
export function combineSelection(
  selection: SelectionMask,
  mode: SelectionCombineMode = 'replace',
): DocumentTransform {
  assertSelectionCombineMode(mode);
  const ownedSelection = copySelection(selection) as SelectionMask;
  return command('Combine selection', (document) => ({
    ...document,
    selection: combinedSelection(document, ownedSelection, mode),
  }));
}

/**
 * Combines a hard-edged rectangle with the document's full-resolution selection coverage.
 * A null selection represents full-canvas coverage, matching the document model invariant.
 */
export function combineRectangularSelection(
  rectangle: SelectionRectangle,
  mode: SelectionCombineMode = 'replace',
): DocumentTransform {
  assertSelectionCombineMode(mode);
  return command('Combine selection', (document) => {
    const operand = createRectangularSelection(document.canvas, rectangle);
    return {
      ...document,
      selection: combinedSelection(document, operand, mode),
    };
  });
}

/**
 * True when every canvas tile is allocated and fully selected (255).
 * Used to invert a full selection to empty without densifying first.
 */
function isFullySelectedCoverage(store: TiledCoverageStore): boolean {
  if (store.defaultCoverage === 255) return store.tileCount === 0;
  const columnCount = Math.ceil(store.width / store.tileSize);
  const rowCount = Math.ceil(store.height / store.tileSize);
  if (store.tileCount !== columnCount * rowCount) return false;
  for (const tile of store.tiles()) {
    for (let index = 0; index < tile.coverage.length; index += 1) {
      if ((tile.coverage[index] ?? 0) !== 255) return false;
    }
  }
  return true;
}

/**
 * Inverts coverage tile-by-tile. Missing source tiles are treated as 0 and become
 * fully selected (255) after invert — that is an explicit dense boundary when the
 * source is sparse. Returns null when the inverted result is a full-canvas selection
 * (representable as document.selection === null).
 */
function invertCoverageStore(store: TiledCoverageStore): TiledCoverageStore | null {
  if (isFullySelectedCoverage(store)) {
    return TiledCoverageStore.empty(store.width, store.height, store.tileSize);
  }
  const inverted = store.invert();
  return isFullySelectedCoverage(inverted) ? null : inverted;
}

export function invertSelection(): DocumentTransform {
  return command('Invert selection', (document) => {
    const current = document.selection;
    if (current === null) {
      // Full-canvas selection inverts to an empty sparse mask without densifying.
      return {
        ...document,
        selection: createSelectionMask({
          width: document.canvas.width,
          height: document.canvas.height,
          coverageStore: TiledCoverageStore.empty(document.canvas.width, document.canvas.height),
        }),
      };
    }
    assertSelectionMatchesCanvas(current, document.canvas);
    const inverted = invertCoverageStore(current.coverageStore);
    if (inverted === null) {
      // Invert of empty (or fully-selected after invert) → select-all.
      return { ...document, selection: null };
    }
    return {
      ...document,
      selection: createSelectionMask({
        width: document.canvas.width,
        height: document.canvas.height,
        coverageStore: inverted,
      }),
    };
  });
}

export function selectAll(): DocumentTransform {
  return command('Select all', (document) => ({ ...document, selection: null }));
}

/**
 * Crops a tiled coverage store into a new canvas-sized store without densifying
 * the full source. Only tiles that intersect the crop rectangle are read/written.
 * Pixel-equivalent to cropping a full contiguous buffer of the same coverage.
 */
function cropCoverageStore(
  store: TiledCoverageStore,
  rectangle: SelectionRectangle,
): TiledCoverageStore {
  let result = TiledCoverageStore.uniform(
    rectangle.width,
    rectangle.height,
    store.defaultCoverage,
    store.tileSize,
  );
  const cropRight = rectangle.x + rectangle.width;
  const cropBottom = rectangle.y + rectangle.height;
  for (const tile of store.tiles()) {
    const left = Math.max(tile.x, rectangle.x);
    const top = Math.max(tile.y, rectangle.y);
    const right = Math.min(tile.x + tile.width, cropRight);
    const bottom = Math.min(tile.y + tile.height, cropBottom);
    if (right <= left || bottom <= top) continue;
    const width = right - left;
    const height = bottom - top;
    const patch = store.read({ x: left, y: top, width, height });
    result = result.write({
      x: left - rectangle.x,
      y: top - rectangle.y,
      width,
      height,
      coverage: patch,
    }).store;
  }
  return result;
}

const MAX_CANVAS_DIMENSION = 32_768;
const MAX_CANVAS_PIXELS = 100_000_000;

function assertSafeCanvasSize(width: number, height: number, operation: string): void {
  if (width > MAX_CANVAS_DIMENSION || height > MAX_CANVAS_DIMENSION) {
    throw new Error(`${operation} dimensions cannot exceed ${String(MAX_CANVAS_DIMENSION)} pixels`);
  }
  if (!Number.isSafeInteger(width * height) || width * height > MAX_CANVAS_PIXELS) {
    throw new Error(`${operation} area cannot exceed ${String(MAX_CANVAS_PIXELS)} pixels`);
  }
}

function resizeDocumentToRectangle(
  document: ImageDocument,
  rectangle: SelectionRectangle,
): ImageDocument {
  const resizeDocumentSpaceVectorMask = (mask: VectorMask | null): VectorMask | null =>
    mask === null
      ? null
      : {
          ...mask,
          width: rectangle.width,
          height: rectangle.height,
          commands: mask.commands.map((command) =>
            command.type === 'close'
              ? { type: 'close' as const }
              : command.type === 'cubic'
                ? {
                    ...command,
                    control1X: command.control1X - rectangle.x,
                    control1Y: command.control1Y - rectangle.y,
                    control2X: command.control2X - rectangle.x,
                    control2Y: command.control2Y - rectangle.y,
                    x: command.x - rectangle.x,
                    y: command.y - rectangle.y,
                  }
                : { ...command, x: command.x - rectangle.x, y: command.y - rectangle.y },
          ),
        };
  const resizeLayer = (layer: Layer, isRoot: boolean): Layer => {
    const transform = isRoot
      ? multiplyLayerTransforms(
          { a: 1, b: 0, c: 0, d: 1, e: -rectangle.x, f: -rectangle.y },
          layer.transform,
        )
      : layer.transform;
    if (layer.kind === 'group') {
      const children = layer.children.map((child) => resizeLayer(child, false));
      return {
        ...layer,
        transform,
        rasterMask:
          layer.rasterMask === null
            ? null
            : {
                ...layer.rasterMask,
                width: rectangle.width,
                height: rectangle.height,
                coverageStore: cropCoverageStore(layer.rasterMask.coverageStore, rectangle),
              },
        vectorMask: resizeDocumentSpaceVectorMask(layer.vectorMask),
        children: children.every((child, index) => child === layer.children[index])
          ? layer.children
          : children,
      };
    }
    if (layer.kind !== 'adjustment') return isRoot ? { ...layer, transform } : layer;
    return {
      ...layer,
      transform,
      rasterMask:
        layer.rasterMask === null
          ? null
          : {
              ...layer.rasterMask,
              width: rectangle.width,
              height: rectangle.height,
              coverageStore: cropCoverageStore(layer.rasterMask.coverageStore, rectangle),
            },
      vectorMask: resizeDocumentSpaceVectorMask(layer.vectorMask),
    };
  };
  return {
    ...document,
    canvas: { width: rectangle.width, height: rectangle.height },
    layers: document.layers.map((layer) => resizeLayer(layer, true)),
    selection:
      document.selection === null
        ? null
        : createSelectionMask({
            width: rectangle.width,
            height: rectangle.height,
            coverageStore: cropCoverageStore(document.selection.coverageStore, rectangle),
          }),
    channels: document.channels.map((channel) =>
      createAlphaChannel({
        id: channel.id,
        name: channel.name,
        width: rectangle.width,
        height: rectangle.height,
        coverageStore: cropCoverageStore(channel.coverageStore, rectangle),
      }),
    ),
  };
}

/**
 * Changes the document canvas without destructively trimming layer-local pixels.
 * Root transforms are translated in world space while selections and saved channels
 * are resampled in document space, with out-of-canvas areas initialized transparent.
 */
export function cropDocument(rectangle: SelectionRectangle): DocumentTransform {
  assertInteger(rectangle.x, 'Crop x');
  assertInteger(rectangle.y, 'Crop y');
  assertPositiveInteger(rectangle.width, 'Crop width');
  assertPositiveInteger(rectangle.height, 'Crop height');
  assertSafeCanvasSize(rectangle.width, rectangle.height, 'Crop');
  const ownedRectangle = { ...rectangle };

  return command('Crop document', (document) =>
    resizeDocumentToRectangle(document, ownedRectangle),
  );
}

const ANCHOR_FRACTIONS: Readonly<Record<CanvasAnchorPosition, CanvasAnchorFractions>> = {
  'top-left': { x: 0, y: 0 },
  'top-center': { x: 0.5, y: 0 },
  'top-right': { x: 1, y: 0 },
  'center-left': { x: 0, y: 0.5 },
  center: { x: 0.5, y: 0.5 },
  'center-right': { x: 1, y: 0.5 },
  'bottom-left': { x: 0, y: 1 },
  'bottom-center': { x: 0.5, y: 1 },
  'bottom-right': { x: 1, y: 1 },
};

function ownCanvasAnchor(anchor: CanvasAnchor): CanvasAnchorFractions {
  if (typeof anchor === 'string' && !Object.hasOwn(ANCHOR_FRACTIONS, anchor)) {
    throw new DocumentInvariantError('Canvas anchor is invalid');
  }
  const fractions = typeof anchor === 'string' ? ANCHOR_FRACTIONS[anchor] : anchor;
  assertFinite(fractions.x, 'Canvas anchor x');
  assertFinite(fractions.y, 'Canvas anchor y');
  if (fractions.x < 0 || fractions.x > 1 || fractions.y < 0 || fractions.y > 1) {
    throw new DocumentInvariantError('Canvas anchor fractions must be between 0 and 1');
  }
  return { x: fractions.x, y: fractions.y };
}

/** Resizes the canvas around a named or fractional fixed-point anchor. */
export function resizeCanvas(
  width: number,
  height: number,
  anchor: CanvasAnchor = 'center',
): DocumentTransform {
  assertPositiveInteger(width, 'Canvas width');
  assertPositiveInteger(height, 'Canvas height');
  assertSafeCanvasSize(width, height, 'Canvas');
  const ownedAnchor = ownCanvasAnchor(anchor);

  return command('Resize canvas', (document) => {
    // Canvas coverage is discrete; nearest-pixel placement makes arbitrary fractions deterministic.
    const offsetX = Math.round((width - document.canvas.width) * ownedAnchor.x);
    const offsetY = Math.round((height - document.canvas.height) * ownedAnchor.y);
    return resizeDocumentToRectangle(document, {
      x: -offsetX,
      y: -offsetY,
      width,
      height,
    });
  });
}

interface LayerBounds {
  readonly left: number;
  readonly top: number;
  readonly right: number;
  readonly bottom: number;
}

function includeLayerBounds(
  layers: readonly Layer[],
  parentTransform: LayerTransform,
  bounds: LayerBounds | undefined,
): LayerBounds | undefined {
  let nextBounds = bounds;
  for (const layer of layers) {
    if (!layer.visible || layer.opacity <= 0) continue;
    const worldTransform = multiplyLayerTransforms(parentTransform, layer.transform);
    if (layer.kind === 'group') {
      nextBounds = includeLayerBounds(layer.children, worldTransform, nextBounds);
      continue;
    }
    if (layer.kind === 'adjustment') continue;
    const width =
      layer.kind === 'shape'
        ? layer.geometry.width
        : layer.kind === 'smart-object'
          ? layer.source.canvas.width
          : layer.width;
    const height =
      layer.kind === 'shape'
        ? layer.geometry.height
        : layer.kind === 'smart-object'
          ? layer.source.canvas.height
          : layer.height;
    const corners = [
      transformPoint(worldTransform, 0, 0),
      transformPoint(worldTransform, width, 0),
      transformPoint(worldTransform, 0, height),
      transformPoint(worldTransform, width, height),
    ];
    const left = Math.min(...corners.map((point) => point.x));
    const top = Math.min(...corners.map((point) => point.y));
    const right = Math.max(...corners.map((point) => point.x));
    const bottom = Math.max(...corners.map((point) => point.y));
    nextBounds =
      nextBounds === undefined
        ? { left, top, right, bottom }
        : {
            left: Math.min(nextBounds.left, left),
            top: Math.min(nextBounds.top, top),
            right: Math.max(nextBounds.right, right),
            bottom: Math.max(nextBounds.bottom, bottom),
          };
  }
  return nextBounds;
}

/** Expands the canvas to include every visible, nonzero-opacity pixel layer. */
export function revealAll(): DocumentTransform {
  return command('Reveal all', (document) => {
    const bounds = includeLayerBounds(document.layers, identityLayerTransform(), undefined);
    if (bounds === undefined) return document;
    const left = Math.min(0, Math.floor(bounds.left));
    const top = Math.min(0, Math.floor(bounds.top));
    const right = Math.max(document.canvas.width, Math.ceil(bounds.right));
    const bottom = Math.max(document.canvas.height, Math.ceil(bounds.bottom));
    if (
      left === 0 &&
      top === 0 &&
      right === document.canvas.width &&
      bottom === document.canvas.height
    ) {
      return document;
    }
    const width = right - left;
    const height = bottom - top;
    assertSafeCanvasSize(width, height, 'Canvas');
    return resizeDocumentToRectangle(document, { x: left, y: top, width, height });
  });
}

export function findLayer(document: ImageDocument, id: LayerId): Layer | undefined {
  const visit = (layers: readonly Layer[]): Layer | undefined => {
    for (const layer of layers) {
      if (layer.id === id) return layer;
      if (layer.kind === 'group') {
        const found = visit(layer.children);
        if (found !== undefined) return found;
      }
    }
    return undefined;
  };
  return visit(document.layers);
}

export function findAlphaChannel(document: ImageDocument, id: string): AlphaChannel | undefined {
  return document.channels.find((channel) => channel.id === id);
}

function transformChildren(
  layers: readonly Layer[],
  parentId: LayerId | null,
  transform: (children: readonly Layer[]) => readonly Layer[] | undefined,
): readonly Layer[] | undefined {
  if (parentId === null) return transform(layers);
  for (let index = 0; index < layers.length; index += 1) {
    const layer = layers[index];
    if (layer === undefined || layer.kind !== 'group') continue;
    let children: readonly Layer[] | undefined;
    if (layer.id === parentId) children = transform(layer.children);
    else children = transformChildren(layer.children, parentId, transform);
    if (children === undefined) continue;
    const next = [...layers];
    next[index] = { ...layer, children };
    return next;
  }
  return undefined;
}

function mapLayer(
  layers: readonly Layer[],
  id: LayerId,
  transform: (layer: Layer) => Layer,
): readonly Layer[] | undefined {
  for (let index = 0; index < layers.length; index += 1) {
    const layer = layers[index];
    if (layer === undefined) continue;
    if (layer.id === id) {
      const next = [...layers];
      next[index] = transform(layer);
      return next;
    }
    if (layer.kind !== 'group') continue;
    const children = mapLayer(layer.children, id, transform);
    if (children === undefined) continue;
    const next = [...layers];
    next[index] = { ...layer, children };
    return next;
  }
  return undefined;
}

function command(
  label: string,
  operation: (document: ImageDocument) => ImageDocument,
): DocumentTransform {
  return {
    label,
    execute(document) {
      const next = operation(document);
      validateDocument(next);
      return next;
    },
  };
}

function withLayers(
  document: ImageDocument,
  layers: readonly Layer[] | undefined,
  message: string,
): ImageDocument {
  if (layers === undefined) throw new DocumentInvariantError(message);
  return { ...document, layers };
}

export function addLayer(
  layer: Layer,
  parentId: LayerId | null = null,
  index?: number,
): DocumentTransform {
  const ownedLayer = copyLayer(layer);
  return command('Add layer', (document) => {
    if (findLayer(document, ownedLayer.id) !== undefined) {
      throw new DocumentInvariantError(`Duplicate layer id: ${ownedLayer.id}`);
    }
    return withLayers(
      document,
      transformChildren(document.layers, parentId, (children) => {
        const insertionIndex = index ?? children.length;
        if (
          !Number.isInteger(insertionIndex) ||
          insertionIndex < 0 ||
          insertionIndex > children.length
        ) {
          throw new DocumentInvariantError('Layer insertion index is out of bounds');
        }
        const next = [...children];
        next.splice(insertionIndex, 0, copyLayer(ownedLayer));
        return next;
      }),
      `Parent group not found: ${parentId ?? 'root'}`,
    );
  });
}

function remapDuplicatedLayer(layer: Layer, rootId: string, rootName?: string): Layer {
  const duplicateId = (sourceId: string) =>
    sourceId === layer.id ? rootId : `${rootId}:${sourceId}`;
  const duplicate = (source: Layer, isRoot = false): Layer => {
    const common = {
      id: duplicateId(source.id),
      name: isRoot ? (rootName ?? `${source.name} copy`) : source.name,
      visible: source.visible,
      opacity: source.opacity,
      fillOpacity: source.fillOpacity,
      blendMode: source.blendMode,
      clipping: source.clipping,
      locks: source.locks,
      transform: source.transform,
      effects: source.effects,
    };
    if (source.kind === 'pixel') {
      return createPixelLayer({
        ...common,
        width: source.width,
        height: source.height,
        pixelStore: source.pixelStore,
        rasterMask: source.rasterMask,
        vectorMask: source.vectorMask,
      });
    }
    if (source.kind === 'adjustment') {
      return createAdjustmentLayer({
        ...common,
        adjustment: source.adjustment,
        rasterMask: source.rasterMask,
        vectorMask: source.vectorMask,
      });
    }
    if (source.kind === 'shape') {
      return createShapeLayer({
        ...common,
        geometry: source.geometry,
        style: source.style,
        rasterMask: source.rasterMask,
        vectorMask: source.vectorMask,
      });
    }
    if (source.kind === 'text') {
      return createTextLayer({
        ...common,
        text: source.text,
        width: source.width,
        height: source.height,
        style: source.style,
        rasterMask: source.rasterMask,
        vectorMask: source.vectorMask,
      });
    }
    if (source.kind === 'smart-object') {
      return createSmartObjectLayer({
        ...common,
        source: source.source,
        smartFilters: source.smartFilters,
        rasterMask: source.rasterMask,
        vectorMask: source.vectorMask,
      });
    }
    return createGroupLayer({
      ...common,
      compositing: source.compositing,
      rasterMask: source.rasterMask,
      vectorMask: source.vectorMask,
      children: source.children.map((child) => duplicate(child)),
    });
  };
  return duplicate(layer, true);
}

/** Duplicates a layer beside its source, recursively assigning stable descendant IDs. */
export function duplicateLayer(id: LayerId, newId: LayerId, name?: string): DocumentTransform {
  assertName(newId, 'Duplicate layer id');
  if (name !== undefined) assertName(name, 'Duplicate layer name');
  return command('Duplicate layer', (document) => {
    if (findLayer(document, newId) !== undefined) {
      throw new DocumentInvariantError(`Duplicate layer id: ${newId}`);
    }
    const duplicateIn = (layers: readonly Layer[]): readonly Layer[] | undefined => {
      const index = layers.findIndex((layer) => layer.id === id);
      if (index >= 0) {
        const source = layers[index];
        if (source === undefined) return undefined;
        assertNotFullyLocked(source, 'Duplicating layer');
        const next = [...layers];
        next.splice(index + 1, 0, remapDuplicatedLayer(source, newId, name));
        return next;
      }
      for (let layerIndex = 0; layerIndex < layers.length; layerIndex += 1) {
        const layer = layers[layerIndex];
        if (layer?.kind !== 'group') continue;
        const children = duplicateIn(layer.children);
        if (children === undefined) continue;
        const next = [...layers];
        next[layerIndex] = { ...layer, children };
        return next;
      }
      return undefined;
    };
    return withLayers(document, duplicateIn(document.layers), `Layer not found: ${id}`);
  });
}

/** Groups sibling layers in their existing stacking order at the first selected position. */
export function groupLayers(
  ids: readonly LayerId[],
  options: Omit<NewGroupLayerOptions, 'children'>,
): DocumentTransform {
  if (ids.length === 0) throw new DocumentInvariantError('Select at least one layer to group');
  const targetIds = new Set(ids);
  if (targetIds.size !== ids.length) {
    throw new DocumentInvariantError('Layer group selection contains duplicate ids');
  }
  const ownedOptions = { ...options, locks: copyLayerLocks(options.locks) };
  return command('Group layers', (document) => {
    if (findLayer(document, options.id) !== undefined) {
      throw new DocumentInvariantError(`Duplicate layer id: ${options.id}`);
    }
    const groupIn = (layers: readonly Layer[]): readonly Layer[] | undefined => {
      const direct = layers
        .map((layer, index) => ({ layer, index }))
        .filter(({ layer }) => targetIds.has(layer.id));
      if (direct.length > 0 && direct.length !== targetIds.size) {
        throw new DocumentInvariantError('Grouped layers must share the same parent');
      }
      if (direct.length === targetIds.size) {
        for (const { layer } of direct) assertNotFullyLocked(layer, 'Grouping layer');
        const insertionIndex = Math.min(...direct.map(({ index }) => index));
        const children = direct.map(({ layer }) => layer);
        const remaining = layers.filter((layer) => !targetIds.has(layer.id));
        const group = createGroupLayer({ ...ownedOptions, children });
        remaining.splice(insertionIndex, 0, group);
        return remaining;
      }
      for (let index = 0; index < layers.length; index += 1) {
        const layer = layers[index];
        if (layer?.kind !== 'group') continue;
        const children = groupIn(layer.children);
        if (children === undefined) continue;
        const next = [...layers];
        next[index] = { ...layer, children };
        return next;
      }
      return undefined;
    };
    return withLayers(
      document,
      groupIn(document.layers),
      'Selected layers were not found under one parent',
    );
  });
}

/** Removes a pass-through group while preserving its currently rendered child placement. */
export function ungroupLayer(id: LayerId): DocumentTransform {
  return command('Ungroup layers', (document) => {
    const ungroupIn = (layers: readonly Layer[]): readonly Layer[] | undefined => {
      const index = layers.findIndex((layer) => layer.id === id);
      if (index >= 0) {
        const group = layers[index];
        if (group?.kind !== 'group') {
          throw new DocumentInvariantError(`Layer is not a group: ${id}`);
        }
        assertNotFullyLocked(group, 'Ungrouping layer');
        if (
          group.compositing !== 'pass-through' ||
          group.blendMode !== 'normal' ||
          group.fillOpacity !== 1 ||
          group.clipping ||
          group.rasterMask !== null ||
          group.vectorMask !== null
        ) {
          throw new DocumentInvariantError(
            'Only unclipped Normal pass-through groups can be ungrouped without changing pixels',
          );
        }
        const children = group.children.map((child) => ({
          ...child,
          visible: group.visible && child.visible,
          opacity: group.opacity * child.opacity,
          transform: multiplyLayerTransforms(group.transform, child.transform),
        }));
        const next = [...layers];
        next.splice(index, 1, ...children);
        return next;
      }
      for (let layerIndex = 0; layerIndex < layers.length; layerIndex += 1) {
        const layer = layers[layerIndex];
        if (layer?.kind !== 'group') continue;
        const children = ungroupIn(layer.children);
        if (children === undefined) continue;
        const next = [...layers];
        next[layerIndex] = { ...layer, children };
        return next;
      }
      return undefined;
    };
    return withLayers(document, ungroupIn(document.layers), `Layer not found: ${id}`);
  });
}

export function removeLayer(id: LayerId): DocumentTransform {
  const removeFrom = (layers: readonly Layer[]): readonly Layer[] | undefined => {
    const directIndex = layers.findIndex((layer) => layer.id === id);
    if (directIndex >= 0) return layers.filter((_, index) => index !== directIndex);
    for (let index = 0; index < layers.length; index += 1) {
      const layer = layers[index];
      if (layer === undefined || layer.kind !== 'group') continue;
      const children = removeFrom(layer.children);
      if (children === undefined) continue;
      const next = [...layers];
      next[index] = { ...layer, children };
      return next;
    }
    return undefined;
  };
  return command('Remove layer', (document) =>
    withLayers(document, removeFrom(document.layers), `Layer not found: ${id}`),
  );
}

export function removeLayers(ids: readonly LayerId[]): DocumentTransform {
  if (ids.length === 0) throw new DocumentInvariantError('Select at least one layer to remove');
  if (new Set(ids).size !== ids.length) {
    throw new DocumentInvariantError('Layer removal contains duplicate ids');
  }
  return command(ids.length === 1 ? 'Remove layer' : 'Remove layers', (document) => {
    for (const id of ids) {
      if (findLayer(document, id) === undefined) {
        throw new DocumentInvariantError(`Layer not found: ${id}`);
      }
    }
    return ids.reduce(
      (current, id) =>
        findLayer(current, id) === undefined ? current : removeLayer(id).execute(current),
      document,
    );
  });
}

/** Replaces selected root siblings with one owned layer at their lowest stack position. */
export function replaceRootLayers(
  ids: readonly LayerId[],
  replacement: Layer,
  label = 'Merge layers',
): DocumentTransform {
  if (ids.length === 0) throw new DocumentInvariantError('Select at least one layer to replace');
  if (new Set(ids).size !== ids.length) {
    throw new DocumentInvariantError('Layer replacement contains duplicate ids');
  }
  assertName(label, 'Layer replacement command label');
  const ownedReplacement = copyLayer(replacement);
  return command(label, (document) => {
    const selected = new Set(ids);
    const indices = document.layers
      .map((layer, index) => (selected.has(layer.id) ? index : -1))
      .filter((index) => index >= 0);
    if (indices.length !== selected.size) {
      throw new DocumentInvariantError('Replacement layers must all be root siblings');
    }
    if (!selected.has(ownedReplacement.id) && findLayer(document, ownedReplacement.id)) {
      throw new DocumentInvariantError(`Duplicate layer id: ${ownedReplacement.id}`);
    }
    for (const layer of document.layers) {
      if (selected.has(layer.id)) assertNotFullyLocked(layer, 'Replacing layer');
    }
    const insertionIndex = Math.min(...indices);
    const next = document.layers.filter((layer) => !selected.has(layer.id));
    next.splice(insertionIndex, 0, copyLayer(ownedReplacement));
    return { ...document, layers: next };
  });
}

export function renameLayer(id: LayerId, name: string): DocumentTransform {
  assertName(name, 'Layer name');
  return command('Rename layer', (document) =>
    withLayers(
      document,
      mapLayer(document.layers, id, (layer) => {
        assertNotFullyLocked(layer, 'Renaming layer');
        return { ...layer, name };
      }),
      `Layer not found: ${id}`,
    ),
  );
}

export function setLayerVisibility(id: LayerId, visible: boolean): DocumentTransform {
  return command('Set layer visibility', (document) =>
    withLayers(
      document,
      mapLayer(document.layers, id, (layer) => {
        assertNotFullyLocked(layer, 'Changing layer visibility');
        return { ...layer, visible };
      }),
      `Layer not found: ${id}`,
    ),
  );
}

export function setLayerOpacity(id: LayerId, opacity: number): DocumentTransform {
  assertOpacity(opacity);
  return command('Set layer opacity', (document) =>
    withLayers(
      document,
      mapLayer(document.layers, id, (layer) => {
        assertNotFullyLocked(layer, 'Changing layer opacity');
        return { ...layer, opacity };
      }),
      `Layer not found: ${id}`,
    ),
  );
}

export function setLayerFillOpacity(id: LayerId, fillOpacity: number): DocumentTransform {
  assertOpacity(fillOpacity);
  return command('Set layer fill opacity', (document) =>
    withLayers(
      document,
      mapLayer(document.layers, id, (layer) => {
        assertNotFullyLocked(layer, 'Changing fill opacity');
        return { ...layer, fillOpacity };
      }),
      `Layer not found: ${id}`,
    ),
  );
}

export function setLayerBlendMode(id: LayerId, blendMode: BlendMode): DocumentTransform {
  assertBlendMode(blendMode);
  return command('Set layer blend mode', (document) =>
    withLayers(
      document,
      mapLayer(document.layers, id, (layer) => {
        assertNotFullyLocked(layer, 'Changing blend mode');
        return { ...layer, blendMode };
      }),
      `Layer not found: ${id}`,
    ),
  );
}

export function setLayerClipping(id: LayerId, clipping: boolean): DocumentTransform {
  return command(clipping ? 'Create clipping mask' : 'Release clipping mask', (document) =>
    withLayers(
      document,
      mapLayer(document.layers, id, (layer) => {
        assertNotFullyLocked(layer, 'Changing clipping');
        return { ...layer, clipping };
      }),
      `Layer not found: ${id}`,
    ),
  );
}

export function setLayerEffects(id: LayerId, effects: readonly LayerEffect[]): DocumentTransform {
  const ownedEffects = copyLayerEffects(effects);
  for (const effect of ownedEffects) validateLayerEffect(effect);
  return command('Edit layer effects', (document) =>
    withLayers(
      document,
      mapLayer(document.layers, id, (layer) => {
        assertNotFullyLocked(layer, 'Editing layer effects');
        return { ...layer, effects: copyLayerEffects(ownedEffects) };
      }),
      `Layer not found: ${id}`,
    ),
  );
}

export function setLayerLocks(id: LayerId, locks: LayerLocks): DocumentTransform {
  const ownedLocks = copyLayerLocks(locks);
  validateLayerLocks(ownedLocks);
  return command('Set layer locks', (document) =>
    withLayers(
      document,
      mapLayer(document.layers, id, (layer) => ({ ...layer, locks: copyLayerLocks(ownedLocks) })),
      `Layer not found: ${id}`,
    ),
  );
}

export function setLayerLock(
  id: LayerId,
  lock: keyof LayerLocks,
  enabled: boolean,
): DocumentTransform {
  return command('Set layer lock', (document) =>
    withLayers(
      document,
      mapLayer(document.layers, id, (layer) => ({
        ...layer,
        locks: { ...layer.locks, [lock]: enabled },
      })),
      `Layer not found: ${id}`,
    ),
  );
}

export function setGroupCompositing(
  id: LayerId,
  compositing: GroupCompositingMode,
): DocumentTransform {
  const candidate: unknown = compositing;
  if (candidate !== 'pass-through' && candidate !== 'isolated') {
    throw new DocumentInvariantError(`Invalid group compositing mode: ${String(candidate)}`);
  }
  return command('Set group compositing', (document) =>
    withLayers(
      document,
      mapLayer(document.layers, id, (layer) => {
        if (layer.kind !== 'group') throw new DocumentInvariantError(`Layer is not a group: ${id}`);
        return { ...layer, compositing };
      }),
      `Layer not found: ${id}`,
    ),
  );
}

export function setAdjustmentLayerParameters(
  id: LayerId,
  adjustment: Adjustment,
): DocumentTransform {
  const ownedAdjustment = copyAdjustment(adjustment);
  validateAdjustment(ownedAdjustment);
  return command('Edit adjustment layer', (document) =>
    withLayers(
      document,
      mapLayer(document.layers, id, (layer) => {
        if (layer.kind !== 'adjustment')
          throw new DocumentInvariantError(`Layer is not an adjustment layer: ${id}`);
        assertUnlocked(layer, 'pixels', 'Editing adjustment parameters');
        return { ...layer, adjustment: copyAdjustment(ownedAdjustment) };
      }),
      `Layer not found: ${id}`,
    ),
  );
}

export function setShapeGeometry(id: LayerId, geometry: ShapeGeometry): DocumentTransform {
  const ownedGeometry = copyShapeGeometry(geometry);
  validateShapeGeometry(ownedGeometry);
  return command('Edit shape geometry', (document) =>
    withLayers(
      document,
      mapLayer(document.layers, id, (layer) => {
        if (layer.kind !== 'shape')
          throw new DocumentInvariantError(`Layer is not a shape layer: ${id}`);
        assertUnlocked(layer, 'pixels', 'Editing shape geometry');
        if (
          (layer.rasterMask !== null &&
            (layer.rasterMask.width !== ownedGeometry.width ||
              layer.rasterMask.height !== ownedGeometry.height)) ||
          (layer.vectorMask !== null &&
            (layer.vectorMask.width !== ownedGeometry.width ||
              layer.vectorMask.height !== ownedGeometry.height))
        ) {
          throw new DocumentInvariantError(
            'Resize or remove the shape mask before changing dimensions',
          );
        }
        return { ...layer, geometry: copyShapeGeometry(ownedGeometry) };
      }),
      `Layer not found: ${id}`,
    ),
  );
}

export function setShapeStyle(id: LayerId, style: ShapeStyle): DocumentTransform {
  const ownedStyle = copyShapeStyle(style);
  validateShapeStyle(ownedStyle);
  return command('Edit shape style', (document) =>
    withLayers(
      document,
      mapLayer(document.layers, id, (layer) => {
        if (layer.kind !== 'shape')
          throw new DocumentInvariantError(`Layer is not a shape layer: ${id}`);
        assertUnlocked(layer, 'pixels', 'Editing shape style');
        return { ...layer, style: copyShapeStyle(ownedStyle) };
      }),
      `Layer not found: ${id}`,
    ),
  );
}

export function setTextContent(id: LayerId, text: string): DocumentTransform {
  return command('Edit text', (document) =>
    withLayers(
      document,
      mapLayer(document.layers, id, (layer) => {
        if (layer.kind !== 'text')
          throw new DocumentInvariantError(`Layer is not a text layer: ${id}`);
        assertUnlocked(layer, 'pixels', 'Editing text');
        return { ...layer, text };
      }),
      `Layer not found: ${id}`,
    ),
  );
}

export function setTextStyle(id: LayerId, style: TextStyle): DocumentTransform {
  const ownedStyle = copyTextStyle(style);
  validateTextStyle(ownedStyle);
  return command('Edit text style', (document) =>
    withLayers(
      document,
      mapLayer(document.layers, id, (layer) => {
        if (layer.kind !== 'text')
          throw new DocumentInvariantError(`Layer is not a text layer: ${id}`);
        assertUnlocked(layer, 'pixels', 'Editing text style');
        return { ...layer, style: copyTextStyle(ownedStyle) };
      }),
      `Layer not found: ${id}`,
    ),
  );
}

function smartObjectDimensions(layer: PixelLayer | ShapeLayer | TextLayer | SmartObjectLayer) {
  if (layer.kind === 'shape') return { width: layer.geometry.width, height: layer.geometry.height };
  if (layer.kind === 'smart-object') return { ...layer.source.canvas };
  return { width: layer.width, height: layer.height };
}

export function convertLayerToSmartObject(
  id: LayerId,
  sourceId: string,
  contentLayerId: LayerId,
): DocumentTransform {
  assertName(sourceId, 'Smart object source id');
  assertName(contentLayerId, 'Smart object content layer id');
  return command('Convert to smart object', (document) =>
    withLayers(
      document,
      mapLayer(document.layers, id, (layer) => {
        if (layer.kind === 'group' || layer.kind === 'adjustment') {
          throw new DocumentInvariantError('Only renderable layers can become smart objects');
        }
        assertNotFullyLocked(layer, 'Converting layer');
        const dimensions = smartObjectDimensions(layer);
        const content = {
          ...copyLayer(layer),
          id: contentLayerId,
          visible: true,
          opacity: 1,
          fillOpacity: 1,
          blendMode: 'normal' as const,
          clipping: false,
          locks: copyLayerLocks(),
          transform: identityLayerTransform(),
          effects: [],
        } as Layer;
        return createSmartObjectLayer({
          id: layer.id,
          name: layer.name,
          source: {
            id: sourceId,
            name: `${layer.name} contents`,
            canvas: dimensions,
            layers: [content],
          },
          visible: layer.visible,
          opacity: layer.opacity,
          fillOpacity: layer.fillOpacity,
          blendMode: layer.blendMode,
          clipping: layer.clipping,
          locks: layer.locks,
          transform: layer.transform,
          effects: layer.effects,
        });
      }),
      `Layer not found: ${id}`,
    ),
  );
}

export function replaceSmartObjectSource(
  id: LayerId,
  source: SmartObjectSource,
): DocumentTransform {
  const ownedSource = copySmartObjectSource(source);
  return command('Replace smart object contents', (document) =>
    withLayers(
      document,
      mapLayer(document.layers, id, (layer) => {
        if (layer.kind !== 'smart-object')
          throw new DocumentInvariantError(`Layer is not a smart object: ${id}`);
        assertUnlocked(layer, 'pixels', 'Replacing smart object contents');
        const checked = createSmartObjectLayer({
          ...layer,
          source: ownedSource,
          rasterMask: null,
          vectorMask: null,
        });
        return { ...checked, source: copySmartObjectSource(ownedSource) };
      }),
      `Layer not found: ${id}`,
    ),
  );
}

export function setSmartObjectFilters(
  id: LayerId,
  filters: readonly SmartFilter[],
): DocumentTransform {
  const ownedFilters = copySmartFilters(filters);
  const ids = new Set<string>();
  for (const filter of ownedFilters) {
    validateSmartFilter(filter);
    if (ids.has(filter.id))
      throw new DocumentInvariantError(`Duplicate smart filter id: ${filter.id}`);
    ids.add(filter.id);
  }
  return command('Edit smart filters', (document) =>
    withLayers(
      document,
      mapLayer(document.layers, id, (layer) => {
        if (layer.kind !== 'smart-object')
          throw new DocumentInvariantError(`Layer is not a smart object: ${id}`);
        assertUnlocked(layer, 'pixels', 'Editing smart filters');
        return { ...layer, smartFilters: copySmartFilters(ownedFilters) };
      }),
      `Layer not found: ${id}`,
    ),
  );
}

export function addAdjustmentLayer(
  options: NewAdjustmentLayerOptions,
  parentId: LayerId | null = null,
  index?: number,
): DocumentTransform {
  return addLayer(createAdjustmentLayer(options), parentId, index);
}

export function setLayerTranslation(id: LayerId, x: number, y: number): DocumentTransform {
  assertFinite(x, 'Layer transform e');
  assertFinite(y, 'Layer transform f');
  return command('Move layer', (document) =>
    withLayers(
      document,
      mapLayer(document.layers, id, (layer) => {
        assertUnlocked(layer, 'position', 'Moving layer');
        return { ...layer, transform: { ...layer.transform, e: x, f: y } };
      }),
      `Layer not found: ${id}`,
    ),
  );
}

export function setLayerTransform(id: LayerId, transform: LayerTransform): DocumentTransform {
  const ownedTransform = copyLayerTransform(transform);
  return command('Transform layer', (document) =>
    withLayers(
      document,
      mapLayer(document.layers, id, (layer) => {
        assertUnlocked(layer, 'position', 'Transforming layer');
        return { ...layer, transform: copyLayerTransform(ownedTransform) };
      }),
      `Layer not found: ${id}`,
    ),
  );
}

export function setSelection(selection: SelectionMask | null, label?: string): DocumentTransform {
  const ownedSelection = copySelection(selection);
  const commandLabel = label ?? (ownedSelection === null ? 'Deselect' : 'Set selection');
  assertName(commandLabel, 'Selection command label');
  return command(commandLabel, (document) => {
    if (ownedSelection !== null) assertSelectionMatchesCanvas(ownedSelection, document.canvas);
    return { ...document, selection: copySelection(ownedSelection) };
  });
}

export function addAlphaChannelFromSelection(id: string, name: string): DocumentTransform {
  assertName(id, 'Alpha channel id');
  assertName(name, 'Alpha channel name');
  return command('Save selection as alpha channel', (document) => {
    if (findAlphaChannel(document, id) !== undefined) {
      throw new DocumentInvariantError(`Duplicate alpha channel id: ${id}`);
    }
    if (document.selection === null) {
      // Explicit full-canvas coverage boundary for "select all" as a saved channel.
      const coverage = new Uint8ClampedArray(document.canvas.width * document.canvas.height).fill(
        255,
      );
      const channel = createAlphaChannel({
        id,
        name,
        width: document.canvas.width,
        height: document.canvas.height,
        coverage,
      });
      return { ...document, channels: [...document.channels, channel] };
    }
    // Share the immutable sparse store; no densifying copy of ordinary selections.
    const channel = createAlphaChannel({
      id,
      name,
      width: document.canvas.width,
      height: document.canvas.height,
      coverageStore: document.selection.coverageStore,
    });
    return { ...document, channels: [...document.channels, channel] };
  });
}

export function renameAlphaChannel(id: string, name: string): DocumentTransform {
  assertName(name, 'Alpha channel name');
  return command('Rename alpha channel', (document) => {
    if (findAlphaChannel(document, id) === undefined) {
      throw new DocumentInvariantError(`Alpha channel not found: ${id}`);
    }
    return {
      ...document,
      channels: document.channels.map((channel) =>
        channel.id === id ? { ...channel, name } : channel,
      ),
    };
  });
}

export function removeAlphaChannel(id: string): DocumentTransform {
  return command('Remove alpha channel', (document) => {
    if (findAlphaChannel(document, id) === undefined) {
      throw new DocumentInvariantError(`Alpha channel not found: ${id}`);
    }
    return { ...document, channels: document.channels.filter((channel) => channel.id !== id) };
  });
}

export function loadAlphaChannelSelection(id: string): DocumentTransform {
  return command('Load alpha channel as selection', (document) => {
    const channel = findAlphaChannel(document, id);
    if (channel === undefined) {
      throw new DocumentInvariantError(`Alpha channel not found: ${id}`);
    }
    return {
      ...document,
      selection: createSelectionMask({
        width: channel.width,
        height: channel.height,
        coverageStore: channel.coverageStore,
      }),
    };
  });
}

/** Replaces the document guide list (import/editor ownership). */
export function setDocumentGuides(guides: readonly DocumentGuide[]): DocumentTransform {
  const owned = guides.map(copyGuide);
  return command('Set document guides', (document) => {
    const next = { ...document, guides: owned };
    validateDocument(next);
    return next;
  });
}

/** Appends a single ruler guide (drag-create / New Guide commands). */
export function addDocumentGuide(guide: NewDocumentGuideOptions): DocumentTransform {
  const owned = createDocumentGuide(guide);
  return command(
    owned.direction === 'vertical' ? 'Add vertical guide' : 'Add horizontal guide',
    (document) => {
      const next = { ...document, guides: [...document.guides, owned] };
      validateDocument(next);
      return next;
    },
  );
}

/** Removes a guide by id. */
export function removeDocumentGuide(id: string): DocumentTransform {
  return command('Remove guide', (document) => {
    const next = {
      ...document,
      guides: document.guides.filter((guide) => guide.id !== id),
    };
    validateDocument(next);
    return next;
  });
}

/** Moves an existing guide to a new document-space position. */
export function moveDocumentGuide(id: string, position: number): DocumentTransform {
  if (!Number.isFinite(position)) {
    throw new DocumentInvariantError('Guide position must be a finite number');
  }
  return command('Move guide', (document) => {
    const guides = document.guides.map((guide) => {
      if (guide.id !== id) return guide;
      return createDocumentGuide({ ...guide, position });
    });
    if (guides.every((guide) => guide.id !== id)) {
      throw new DocumentInvariantError(`Guide "${id}" was not found`);
    }
    const next = { ...document, guides };
    validateDocument(next);
    return next;
  });
}

/** Clears all document guides. */
export function clearDocumentGuides(): DocumentTransform {
  return command('Clear guides', (document) => {
    const next = { ...document, guides: [] };
    validateDocument(next);
    return next;
  });
}

/** Sets or clears the document grid spacing. */
export function setDocumentGrid(grid: DocumentGrid | null): DocumentTransform {
  const owned = copyGrid(grid);
  return command('Set document grid', (document) => {
    const next = { ...document, grid: owned };
    validateDocument(next);
    return next;
  });
}

/** Replaces the document path list (import/editor ownership). */
export function setDocumentPaths(paths: readonly DocumentPath[]): DocumentTransform {
  const owned = paths.map(copyDocumentPath);
  return command('Set document paths', (document) => {
    const next = { ...document, paths: owned };
    validateDocument(next);
    return next;
  });
}

export function createRasterMask(
  width: number,
  height: number,
  coverage?: ArrayLike<number>,
  options: { readonly enabled?: boolean; readonly density?: number } = {},
): RasterMask {
  assertPositiveInteger(width, 'Raster mask width');
  assertPositiveInteger(height, 'Raster mask height');
  const pixels =
    coverage === undefined
      ? new Uint8ClampedArray(width * height).fill(255)
      : new Uint8ClampedArray(coverage);
  if (pixels.length !== width * height) {
    throw new DocumentInvariantError('Raster mask coverage must match its dimensions');
  }
  const density = options.density ?? 1;
  assertOpacity(density);
  return {
    width,
    height,
    coverageStore: TiledCoverageStore.fromContiguous(width, height, pixels),
    enabled: options.enabled ?? true,
    density,
  };
}

/**
 * Creates a raster mask that shares/copies an existing sparse coverage store without
 * densifying. Store dimensions must match the mask canvas.
 */
export function createRasterMaskFromCoverageStore(
  width: number,
  height: number,
  coverageStore: TiledCoverageStore,
  options: { readonly enabled?: boolean; readonly density?: number } = {},
): RasterMask {
  assertPositiveInteger(width, 'Raster mask width');
  assertPositiveInteger(height, 'Raster mask height');
  if (
    !(coverageStore instanceof TiledCoverageStore) ||
    coverageStore.width !== width ||
    coverageStore.height !== height
  ) {
    throw new DocumentInvariantError('Raster mask coverage store must match its dimensions');
  }
  const density = options.density ?? 1;
  assertOpacity(density);
  return {
    width,
    height,
    coverageStore: TiledCoverageStore.fromSnapshot(coverageStore.snapshot()),
    enabled: options.enabled ?? true,
    density,
  };
}

export function createVectorMask(
  width: number,
  height: number,
  commands: readonly PathCommand[] = [
    { type: 'move', x: 0, y: 0 },
    { type: 'line', x: width, y: 0 },
    { type: 'line', x: width, y: height },
    { type: 'line', x: 0, y: height },
    { type: 'close' },
  ],
  options: {
    readonly enabled?: boolean;
    readonly density?: number;
    readonly inverted?: boolean;
  } = {},
): VectorMask {
  const mask: VectorMask = {
    width,
    height,
    commands: commands.map((command) => ({ ...command })),
    enabled: options.enabled ?? true,
    density: options.density ?? 1,
    inverted: options.inverted ?? false,
  };
  validateVectorMask(mask);
  return copyVectorMask(mask) as VectorMask;
}

function vectorMaskDimensions(layer: Layer, canvas: DocumentCanvas): DocumentCanvas {
  if (layer.kind === 'shape') return { width: layer.geometry.width, height: layer.geometry.height };
  if (layer.kind === 'smart-object') return { ...layer.source.canvas };
  if (layer.kind === 'adjustment' || layer.kind === 'group') return { ...canvas };
  return { width: layer.width, height: layer.height };
}

export function setLayerVectorMask(id: LayerId, mask: VectorMask | null): DocumentTransform {
  const ownedMask = copyVectorMask(mask);
  return command(ownedMask === null ? 'Delete vector mask' : 'Set vector mask', (document) =>
    withLayers(
      document,
      mapLayer(document.layers, id, (layer) => {
        assertUnlocked(layer, 'pixels', 'Editing a vector mask');
        const dimensions = vectorMaskDimensions(layer, document.canvas);
        if (
          ownedMask !== null &&
          (ownedMask.width !== dimensions.width || ownedMask.height !== dimensions.height)
        ) {
          throw new DocumentInvariantError('Vector mask dimensions do not match the layer');
        }
        if (ownedMask !== null) validateVectorMask(ownedMask);
        return { ...layer, vectorMask: copyVectorMask(ownedMask) };
      }),
      `Layer not found: ${id}`,
    ),
  );
}

export function setLayerVectorMaskEnabled(id: LayerId, enabled: boolean): DocumentTransform {
  return command(enabled ? 'Enable vector mask' : 'Disable vector mask', (document) =>
    withLayers(
      document,
      mapLayer(document.layers, id, (layer) => {
        if (layer.vectorMask === null) {
          throw new DocumentInvariantError(`Layer has no vector mask: ${id}`);
        }
        assertUnlocked(layer, 'pixels', 'Editing a vector mask');
        return { ...layer, vectorMask: { ...layer.vectorMask, enabled } };
      }),
      `Layer not found: ${id}`,
    ),
  );
}

export function setLayerVectorMaskDensity(id: LayerId, density: number): DocumentTransform {
  assertOpacity(density);
  return command('Set vector mask density', (document) =>
    withLayers(
      document,
      mapLayer(document.layers, id, (layer) => {
        if (layer.vectorMask === null) {
          throw new DocumentInvariantError(`Layer has no vector mask: ${id}`);
        }
        assertUnlocked(layer, 'pixels', 'Editing a vector mask');
        return { ...layer, vectorMask: { ...layer.vectorMask, density } };
      }),
      `Layer not found: ${id}`,
    ),
  );
}

export function invertLayerVectorMask(id: LayerId): DocumentTransform {
  return command('Invert vector mask', (document) =>
    withLayers(
      document,
      mapLayer(document.layers, id, (layer) => {
        if (layer.vectorMask === null) {
          throw new DocumentInvariantError(`Layer has no vector mask: ${id}`);
        }
        assertUnlocked(layer, 'pixels', 'Editing a vector mask');
        return {
          ...layer,
          vectorMask: { ...layer.vectorMask, inverted: !layer.vectorMask.inverted },
        };
      }),
      `Layer not found: ${id}`,
    ),
  );
}

export function createRasterMaskFromSelection(
  layer: PixelLayer | ShapeLayer | TextLayer | SmartObjectLayer,
  selection: SelectionMask | null,
  worldTransform: LayerTransform = layer.transform,
): RasterMask {
  const dimensions = smartObjectDimensions(layer);
  if (selection === null) return createRasterMask(dimensions.width, dimensions.height);

  const isIdentity =
    worldTransform.a === 1 &&
    worldTransform.b === 0 &&
    worldTransform.c === 0 &&
    worldTransform.d === 1 &&
    worldTransform.e === 0 &&
    worldTransform.f === 0;

  // Identity layer at canvas size: share selection tiles without densifying.
  if (
    isIdentity &&
    dimensions.width === selection.width &&
    dimensions.height === selection.height
  ) {
    return createRasterMaskFromCoverageStore(
      dimensions.width,
      dimensions.height,
      selection.coverageStore,
    );
  }

  // Identity layer with layer-local bounds: tile-local crop/read of selection store.
  if (isIdentity) {
    let store = TiledCoverageStore.empty(dimensions.width, dimensions.height);
    const layerRight = Math.min(dimensions.width, selection.width);
    const layerBottom = Math.min(dimensions.height, selection.height);
    if (layerRight > 0 && layerBottom > 0) {
      // Walk selection tiles that intersect the layer origin-aligned rect.
      for (const tile of selection.coverageStore.tiles()) {
        const left = Math.max(tile.x, 0);
        const top = Math.max(tile.y, 0);
        const right = Math.min(tile.x + tile.width, layerRight);
        const bottom = Math.min(tile.y + tile.height, layerBottom);
        if (right <= left || bottom <= top) continue;
        const region = { x: left, y: top, width: right - left, height: bottom - top };
        const coverage = selection.coverageStore.read(region);
        store = store.write({ ...region, coverage }).store;
      }
    }
    return createRasterMaskFromCoverageStore(dimensions.width, dimensions.height, store);
  }

  // Pure integer translation (common nested group/world offset): tile-local remap
  // without densifying the full selection. Pixel-equivalent to floor(x+0.5+e).
  const isPureIntegerTranslation =
    worldTransform.a === 1 &&
    worldTransform.b === 0 &&
    worldTransform.c === 0 &&
    worldTransform.d === 1 &&
    Number.isInteger(worldTransform.e) &&
    Number.isInteger(worldTransform.f);
  if (isPureIntegerTranslation) {
    const offsetX = worldTransform.e;
    const offsetY = worldTransform.f;
    let store = TiledCoverageStore.empty(dimensions.width, dimensions.height);
    for (const tile of selection.coverageStore.tiles()) {
      // layer (x,y) samples document (x+offsetX, y+offsetY) under integer translation.
      const layerLeft = tile.x - offsetX;
      const layerTop = tile.y - offsetY;
      const left = Math.max(0, layerLeft);
      const top = Math.max(0, layerTop);
      const right = Math.min(dimensions.width, layerLeft + tile.width);
      const bottom = Math.min(dimensions.height, layerTop + tile.height);
      if (right <= left || bottom <= top) continue;
      const region = {
        x: left + offsetX,
        y: top + offsetY,
        width: right - left,
        height: bottom - top,
      };
      if (
        region.x < 0 ||
        region.y < 0 ||
        region.x + region.width > selection.width ||
        region.y + region.height > selection.height
      ) {
        // Clamp to selection canvas.
        const sx = Math.max(0, region.x);
        const sy = Math.max(0, region.y);
        const sr = Math.min(selection.width, region.x + region.width);
        const sb = Math.min(selection.height, region.y + region.height);
        if (sr <= sx || sb <= sy) continue;
        const clipped = { x: sx, y: sy, width: sr - sx, height: sb - sy };
        const coverage = selection.coverageStore.read(clipped);
        store = store.write({
          x: left + (sx - region.x),
          y: top + (sy - region.y),
          width: clipped.width,
          height: clipped.height,
          coverage,
        }).store;
        continue;
      }
      const coverage = selection.coverageStore.read(region);
      store = store.write({
        x: left,
        y: top,
        width: region.width,
        height: region.height,
        coverage,
      }).store;
    }
    return createRasterMaskFromCoverageStore(dimensions.width, dimensions.height, store);
  }

  // Affine (incl. rotated/skewed): sample selection without full-document densify.
  // Only read the document AABB covered by the layer under worldTransform (plus a
  // 1px pad for floor(sample-center) rounding). Pixel-equivalent to full materialize
  // for ordinary affine layer placement.
  const corners = [
    transformPoint(worldTransform, 0, 0),
    transformPoint(worldTransform, dimensions.width, 0),
    transformPoint(worldTransform, 0, dimensions.height),
    transformPoint(worldTransform, dimensions.width, dimensions.height),
  ];
  const minX = Math.min(...corners.map((corner) => corner.x));
  const minY = Math.min(...corners.map((corner) => corner.y));
  const maxX = Math.max(...corners.map((corner) => corner.x));
  const maxY = Math.max(...corners.map((corner) => corner.y));
  const pad = 1;
  const docLeft = Math.max(0, Math.floor(minX) - pad);
  const docTop = Math.max(0, Math.floor(minY) - pad);
  const docRight = Math.min(selection.width, Math.ceil(maxX) + pad);
  const docBottom = Math.min(selection.height, Math.ceil(maxY) + pad);
  if (docRight <= docLeft || docBottom <= docTop) {
    return createRasterMaskFromCoverageStore(
      dimensions.width,
      dimensions.height,
      TiledCoverageStore.empty(dimensions.width, dimensions.height),
    );
  }
  // Intersect the layer footprint with allocated selection tiles. This keeps a
  // small selection on a very large affine layer from creating either a full
  // selection read or a full layer-sized destination buffer.
  let activeLeft = selection.width;
  let activeTop = selection.height;
  let activeRight = -1;
  let activeBottom = -1;
  for (const tile of selection.coverageStore.tiles()) {
    activeLeft = Math.min(activeLeft, tile.x);
    activeTop = Math.min(activeTop, tile.y);
    activeRight = Math.max(activeRight, tile.x + tile.width);
    activeBottom = Math.max(activeBottom, tile.y + tile.height);
  }
  if (activeRight <= activeLeft || activeBottom <= activeTop) {
    return createRasterMaskFromCoverageStore(
      dimensions.width,
      dimensions.height,
      TiledCoverageStore.empty(dimensions.width, dimensions.height),
    );
  }
  const readLeft = Math.max(docLeft, activeLeft);
  const readTop = Math.max(docTop, activeTop);
  const readRight = Math.min(docRight, activeRight);
  const readBottom = Math.min(docBottom, activeBottom);
  if (readRight <= readLeft || readBottom <= readTop) {
    return createRasterMaskFromCoverageStore(
      dimensions.width,
      dimensions.height,
      TiledCoverageStore.empty(dimensions.width, dimensions.height),
    );
  }
  const regionWidth = readRight - readLeft;
  const regionHeight = readBottom - readTop;
  const selectionRegion = selection.coverageStore.read({
    x: readLeft,
    y: readTop,
    width: regionWidth,
    height: regionHeight,
  });

  // Inverse-map the active document AABB to a conservative local work AABB.
  // Affine transforms map rectangles to parallelograms, so the four corners plus
  // one sample-center pad cover every potentially non-zero destination pixel.
  const inverse = invertLayerTransform(worldTransform);
  const localCorners = [
    transformPoint(inverse, readLeft, readTop),
    transformPoint(inverse, readRight, readTop),
    transformPoint(inverse, readLeft, readBottom),
    transformPoint(inverse, readRight, readBottom),
  ];
  const localLeft = Math.max(0, Math.floor(Math.min(...localCorners.map((point) => point.x))) - 1);
  const localTop = Math.max(0, Math.floor(Math.min(...localCorners.map((point) => point.y))) - 1);
  const localRight = Math.min(
    dimensions.width,
    Math.ceil(Math.max(...localCorners.map((point) => point.x))) + 1,
  );
  const localBottom = Math.min(
    dimensions.height,
    Math.ceil(Math.max(...localCorners.map((point) => point.y))) + 1,
  );
  let store = TiledCoverageStore.empty(dimensions.width, dimensions.height);
  const tileSize = store.tileSize;
  for (let top = localTop; top < localBottom; top += tileSize) {
    for (let left = localLeft; left < localRight; left += tileSize) {
      const width = Math.min(tileSize, localRight - left);
      const height = Math.min(tileSize, localBottom - top);
      const coverage = new Uint8ClampedArray(width * height);
      for (let y = top; y < top + height; y += 1) {
        for (let x = left; x < left + width; x += 1) {
          const documentPoint = transformPoint(worldTransform, x + 0.5, y + 0.5);
          const documentX = Math.floor(documentPoint.x);
          const documentY = Math.floor(documentPoint.y);
          if (
            documentX >= readLeft &&
            documentY >= readTop &&
            documentX < readRight &&
            documentY < readBottom
          ) {
            coverage[(y - top) * width + (x - left)] =
              selectionRegion[(documentY - readTop) * regionWidth + (documentX - readLeft)] ?? 0;
          }
        }
      }
      store = store.write({ x: left, y: top, width, height, coverage }).store;
    }
  }
  return createRasterMaskFromCoverageStore(dimensions.width, dimensions.height, store);
}

function findLayerWorldContext(
  layers: readonly Layer[],
  id: LayerId,
  parentTransform: LayerTransform = identityLayerTransform(),
): { layer: Layer; transform: LayerTransform } | undefined {
  for (const layer of layers) {
    const transform = multiplyLayerTransforms(parentTransform, layer.transform);
    if (layer.id === id) return { layer, transform };
    if (layer.kind === 'group') {
      const found = findLayerWorldContext(layer.children, id, transform);
      if (found) return found;
    }
  }
  return undefined;
}

export function findLayerWorldTransform(
  document: ImageDocument,
  id: LayerId,
): LayerTransform | undefined {
  const transform = findLayerWorldContext(document.layers, id)?.transform;
  return transform ? copyLayerTransform(transform) : undefined;
}

export function addLayerRasterMaskFromSelection(id: LayerId): DocumentTransform {
  return command('Add layer mask', (document) => {
    const context = findLayerWorldContext(document.layers, id);
    if (!context) throw new DocumentInvariantError(`Layer not found: ${id}`);
    if (context.layer.kind === 'adjustment')
      throw new DocumentInvariantError(`Layer cannot have a local raster mask: ${id}`);
    if (context.layer.rasterMask !== null)
      throw new DocumentInvariantError(`Layer already has a raster mask: ${id}`);
    return withLayers(
      document,
      mapLayer(document.layers, id, (layer) => {
        if (layer.kind === 'adjustment')
          throw new DocumentInvariantError(`Layer cannot have a local raster mask: ${id}`);
        assertUnlocked(layer, 'pixels', 'Adding a layer mask');
        return {
          ...layer,
          rasterMask:
            layer.kind === 'group'
              ? document.selection === null
                ? createRasterMask(document.canvas.width, document.canvas.height)
                : createRasterMaskFromCoverageStore(
                    document.canvas.width,
                    document.canvas.height,
                    document.selection.coverageStore,
                  )
              : createRasterMaskFromSelection(layer, document.selection, context.transform),
        };
      }),
      `Layer not found: ${id}`,
    );
  });
}

export function addAdjustmentLayerRasterMaskFromSelection(id: LayerId): DocumentTransform {
  return command('Add adjustment mask', (document) =>
    withLayers(
      document,
      mapLayer(document.layers, id, (layer) => {
        if (layer.kind !== 'adjustment') {
          throw new DocumentInvariantError(`Layer is not an adjustment layer: ${id}`);
        }
        assertUnlocked(layer, 'pixels', 'Adding an adjustment mask');
        if (layer.rasterMask !== null) {
          throw new DocumentInvariantError(`Layer already has a raster mask: ${id}`);
        }
        return {
          ...layer,
          rasterMask:
            document.selection === null
              ? createRasterMask(document.canvas.width, document.canvas.height)
              : createRasterMaskFromCoverageStore(
                  document.canvas.width,
                  document.canvas.height,
                  document.selection.coverageStore,
                ),
        };
      }),
      `Layer not found: ${id}`,
    ),
  );
}

export function setLayerRasterMask(id: LayerId, mask: RasterMask | null): DocumentTransform {
  const ownedMask = copyRasterMask(mask);
  return command(ownedMask === null ? 'Delete layer mask' : 'Set layer mask', (document) =>
    withLayers(
      document,
      mapLayer(document.layers, id, (layer) => {
        assertUnlocked(layer, 'pixels', 'Editing a layer mask');
        const expectedWidth =
          layer.kind === 'group'
            ? document.canvas.width
            : layer.kind === 'shape'
              ? layer.geometry.width
              : layer.kind === 'smart-object'
                ? layer.source.canvas.width
                : layer.kind === 'adjustment'
                  ? document.canvas.width
                  : layer.width;
        const expectedHeight =
          layer.kind === 'group'
            ? document.canvas.height
            : layer.kind === 'shape'
              ? layer.geometry.height
              : layer.kind === 'smart-object'
                ? layer.source.canvas.height
                : layer.kind === 'adjustment'
                  ? document.canvas.height
                  : layer.height;
        if (
          ownedMask !== null &&
          (ownedMask.width !== expectedWidth || ownedMask.height !== expectedHeight)
        ) {
          throw new DocumentInvariantError('Raster mask dimensions do not match the layer');
        }
        return { ...layer, rasterMask: copyRasterMask(ownedMask) };
      }),
      `Layer not found: ${id}`,
    ),
  );
}

export function setLayerMaskEnabled(id: LayerId, enabled: boolean): DocumentTransform {
  return command(enabled ? 'Enable layer mask' : 'Disable layer mask', (document) =>
    withLayers(
      document,
      mapLayer(document.layers, id, (layer) => {
        if (layer.rasterMask === null)
          throw new DocumentInvariantError(`Layer has no raster mask: ${id}`);
        assertUnlocked(layer, 'pixels', 'Editing a layer mask');
        return { ...layer, rasterMask: { ...layer.rasterMask, enabled } };
      }),
      `Layer not found: ${id}`,
    ),
  );
}

export function setLayerMaskDensity(id: LayerId, density: number): DocumentTransform {
  assertOpacity(density);
  return command('Set layer mask density', (document) =>
    withLayers(
      document,
      mapLayer(document.layers, id, (layer) => {
        if (layer.rasterMask === null)
          throw new DocumentInvariantError(`Layer has no raster mask: ${id}`);
        assertUnlocked(layer, 'pixels', 'Editing a layer mask');
        return { ...layer, rasterMask: { ...layer.rasterMask, density } };
      }),
      `Layer not found: ${id}`,
    ),
  );
}

export function invertLayerMask(id: LayerId): DocumentTransform {
  return command('Invert layer mask', (document) =>
    withLayers(
      document,
      mapLayer(document.layers, id, (layer) => {
        if (layer.rasterMask === null)
          throw new DocumentInvariantError(`Layer has no raster mask: ${id}`);
        assertUnlocked(layer, 'pixels', 'Editing a layer mask');
        return {
          ...layer,
          rasterMask: {
            ...layer.rasterMask,
            coverageStore: layer.rasterMask.coverageStore.invert(),
          },
        };
      }),
      `Layer not found: ${id}`,
    ),
  );
}

export function patchLayerRasterMask(id: LayerId, patch: MaskPatch): DocumentTransform {
  assertPositiveInteger(patch.width, 'Mask patch width');
  assertPositiveInteger(patch.height, 'Mask patch height');
  if (!Number.isInteger(patch.x) || !Number.isInteger(patch.y) || patch.x < 0 || patch.y < 0) {
    throw new DocumentInvariantError('Mask patch coordinates must be non-negative integers');
  }
  const ownedCoverage = new Uint8ClampedArray(patch.coverage);
  if (ownedCoverage.length !== patch.width * patch.height) {
    throw new DocumentInvariantError(
      `Mask patch coverage must contain ${String(patch.width * patch.height)} values`,
    );
  }
  return command('Paint layer mask', (document) =>
    withLayers(
      document,
      mapLayer(document.layers, id, (layer) => {
        if (layer.rasterMask === null)
          throw new DocumentInvariantError(`Layer has no raster mask: ${id}`);
        assertUnlocked(layer, 'pixels', 'Painting a layer mask');
        if (
          patch.x + patch.width > layer.rasterMask.width ||
          patch.y + patch.height > layer.rasterMask.height
        ) {
          throw new DocumentInvariantError('Mask patch is outside the layer bounds');
        }
        const coverageStore = layer.rasterMask.coverageStore.write({
          x: patch.x,
          y: patch.y,
          width: patch.width,
          height: patch.height,
          coverage: ownedCoverage,
        }).store;
        return { ...layer, rasterMask: { ...layer.rasterMask, coverageStore } };
      }),
      `Layer not found: ${id}`,
    ),
  );
}

export function reorderLayer(
  parentId: LayerId | null,
  fromIndex: number,
  toIndex: number,
): DocumentTransform {
  return command('Reorder layer', (document) =>
    withLayers(
      document,
      transformChildren(document.layers, parentId, (children) => {
        if (
          !Number.isInteger(fromIndex) ||
          !Number.isInteger(toIndex) ||
          fromIndex < 0 ||
          fromIndex >= children.length ||
          toIndex < 0 ||
          toIndex >= children.length
        ) {
          throw new DocumentInvariantError('Layer reorder index is out of bounds');
        }
        if (fromIndex === toIndex) return children;
        const next = [...children];
        const [moved] = next.splice(fromIndex, 1);
        if (moved === undefined)
          throw new DocumentInvariantError('Layer reorder source is missing');
        next.splice(toIndex, 0, moved);
        return next;
      }),
      `Parent group not found: ${parentId ?? 'root'}`,
    ),
  );
}

export function replacePixels(id: LayerId, pixels: ArrayLike<number>): DocumentTransform {
  const ownedPixels = new Uint8ClampedArray(pixels);
  return command('Replace pixels', (document) =>
    withLayers(
      document,
      mapLayer(document.layers, id, (layer) => {
        if (layer.kind !== 'pixel')
          throw new DocumentInvariantError(`Layer is not a pixel layer: ${id}`);
        assertUnlocked(layer, 'pixels', 'Replacing pixels');
        if (ownedPixels.length !== layer.width * layer.height * 4) {
          throw new DocumentInvariantError(
            `Pixel buffer must contain ${String(layer.width * layer.height * 4)} values`,
          );
        }
        return {
          ...layer,
          pixelStore: TiledPixelStore.fromContiguous(layer.width, layer.height, ownedPixels),
        };
      }),
      `Layer not found: ${id}`,
    ),
  );
}

export function patchPixels(
  id: LayerId,
  patch: PixelPatch,
  label = 'Patch pixels',
): DocumentTransform {
  assertName(label, 'Pixel patch command label');
  assertPositiveInteger(patch.width, 'Patch width');
  assertPositiveInteger(patch.height, 'Patch height');
  if (!Number.isInteger(patch.x) || !Number.isInteger(patch.y) || patch.x < 0 || patch.y < 0) {
    throw new DocumentInvariantError('Patch coordinates must be non-negative integers');
  }
  const ownedPixels = new Uint8ClampedArray(patch.pixels);
  if (ownedPixels.length !== patch.width * patch.height * 4) {
    throw new DocumentInvariantError(
      `Patch buffer must contain ${String(patch.width * patch.height * 4)} values`,
    );
  }
  return command(label, (document) =>
    withLayers(
      document,
      mapLayer(document.layers, id, (layer) => {
        if (layer.kind !== 'pixel')
          throw new DocumentInvariantError(`Layer is not a pixel layer: ${id}`);
        assertUnlocked(layer, 'pixels', 'Editing pixels');
        if (patch.x + patch.width > layer.width || patch.y + patch.height > layer.height) {
          throw new DocumentInvariantError('Pixel patch is outside the layer bounds');
        }
        let patchPixels = ownedPixels;
        if (layer.locks.transparency) {
          patchPixels = layer.pixelStore.read({
            x: patch.x,
            y: patch.y,
            width: patch.width,
            height: patch.height,
          });
          for (let offset = 0; offset < patchPixels.length; offset += 4) {
            patchPixels[offset] = ownedPixels[offset] ?? 0;
            patchPixels[offset + 1] = ownedPixels[offset + 1] ?? 0;
            patchPixels[offset + 2] = ownedPixels[offset + 2] ?? 0;
          }
        }
        const result = layer.pixelStore.write({
          x: patch.x,
          y: patch.y,
          width: patch.width,
          height: patch.height,
          pixels: patchPixels,
        });
        return result.store === layer.pixelStore ? layer : { ...layer, pixelStore: result.store };
      }),
      `Layer not found: ${id}`,
    ),
  );
}

/** Replaces a pixel layer's immutable canonical tile store as one history command. */
export function replacePixelStore(
  id: LayerId,
  pixelStore: TiledPixelStore,
  label = 'Replace pixel store',
): DocumentTransform {
  assertName(label, 'Pixel store command label');
  if (!(pixelStore instanceof TiledPixelStore)) {
    throw new DocumentInvariantError('Pixel store command requires a tiled pixel store');
  }
  return command(label, (document) =>
    withLayers(
      document,
      mapLayer(document.layers, id, (layer) => {
        if (layer.kind !== 'pixel')
          throw new DocumentInvariantError(`Layer is not a pixel layer: ${id}`);
        assertUnlocked(layer, 'pixels', 'Replacing pixel store');
        if (pixelStore.width !== layer.width || pixelStore.height !== layer.height) {
          throw new DocumentInvariantError('Pixel store dimensions must match the layer');
        }
        return pixelStore === layer.pixelStore ? layer : { ...layer, pixelStore };
      }),
      `Layer not found: ${id}`,
    ),
  );
}
