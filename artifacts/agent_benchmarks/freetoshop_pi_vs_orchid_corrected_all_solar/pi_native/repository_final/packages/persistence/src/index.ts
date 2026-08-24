import {
  BLEND_MODES,
  SHAPE_KINDS,
  createDocument,
  createAdjustmentLayer,
  createAlphaChannel,
  createDocumentGuide,
  createDocumentPath,
  createGroupLayer,
  createPixelLayer,
  createRasterMask,
  createRasterMaskFromCoverageStore,
  createSelectionMask,
  createShapeLayer,
  createSmartObjectLayer,
  createTextLayer,
  validateDocument,
  type ImageDocument,
  type Layer,
  type LayerTransform,
  type Adjustment,
  type BlendMode,
  type GroupCompositingMode,
  type LayerLocks,
  type LayerEffect,
  type PathCommand,
  type RasterMask,
  type ShapeGeometry,
  type ShapeStyle,
  type SmartFilter,
  type TextStyle,
  type VectorMask,
} from '@freetoshop/core-document';
import { TiledCoverageStore, TiledPixelStore } from '@freetoshop/pixel-storage';

export const DOCUMENT_SCHEMA_VERSION = 20 as const;
export const NATIVE_DOCUMENT_FORMAT_VERSION = 2 as const;
export const NATIVE_DOCUMENT_EXTENSION = '.freetoshop';
export const NATIVE_DOCUMENT_MIME_TYPE = 'application/x-freetoshop';
export const NATIVE_DOCUMENT_MAX_BYTES = 512 * 1024 * 1024;

const NATIVE_DOCUMENT_MAGIC = new TextEncoder().encode('FREETOSHOP\0');
const NATIVE_DOCUMENT_CHECKSUM_BYTES = 32;
const NATIVE_DOCUMENT_HEADER_BYTES =
  NATIVE_DOCUMENT_MAGIC.length + 2 + 4 + NATIVE_DOCUMENT_CHECKSUM_BYTES;

interface SerializedLayerBase {
  readonly id: string;
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

/**
 * Sparse tile payload for schema ≥20. Avoids materializing empty regions of
 * large documents into the native JSON container.
 */
export interface SerializedSparseTiles {
  readonly format: 'sparse-tiles-v1';
  readonly tileSize: number;
  /** Coverage-only implicit value; omitted legacy and pixel payloads mean zero. */
  readonly defaultCoverage?: number;
  readonly tiles: readonly {
    readonly column: number;
    readonly row: number;
    readonly data: string;
  }[];
}

export interface SerializedCasTiles {
  readonly format: 'cas-tiles-v1';
  readonly tileSize: number;
  readonly defaultCoverage?: number;
  readonly tiles: readonly {
    readonly column: number;
    readonly row: number;
    readonly resource: string;
  }[];
}

/** Contiguous base64 buffer or sparse tile list (schema ≥20). */
export type SerializedPixelPayload = string | SerializedSparseTiles | SerializedCasTiles;
/** Contiguous base64 coverage or sparse tile list (schema ≥20). */
export type SerializedCoveragePayload = string | SerializedSparseTiles | SerializedCasTiles;

interface SerializedRasterMask {
  readonly width: number;
  readonly height: number;
  readonly coverage: SerializedCoveragePayload;
  readonly enabled: boolean;
  readonly density: number;
}

interface SerializedVectorMask {
  readonly width: number;
  readonly height: number;
  readonly commands: readonly PathCommand[];
  readonly enabled: boolean;
  readonly density: number;
  readonly inverted: boolean;
}

export interface SerializedPixelLayer extends SerializedLayerBase {
  readonly kind: 'pixel';
  readonly width: number;
  readonly height: number;
  /**
   * Base64 contiguous RGBA (schema ≤19 and dense schema 20) or sparse-tiles-v1
   * when the allocated store is substantially smaller than a full buffer.
   */
  readonly pixels: SerializedPixelPayload;
  readonly rasterMask: SerializedRasterMask | null;
  readonly vectorMask: SerializedVectorMask | null;
}

export interface SerializedGroupLayer extends SerializedLayerBase {
  readonly kind: 'group';
  readonly children: readonly SerializedLayer[];
  readonly compositing: GroupCompositingMode;
  readonly rasterMask: SerializedRasterMask | null;
  readonly vectorMask: SerializedVectorMask | null;
}

export interface SerializedAdjustmentLayer extends SerializedLayerBase {
  readonly kind: 'adjustment';
  readonly adjustment: Adjustment;
  readonly rasterMask: SerializedRasterMask | null;
  readonly vectorMask: SerializedVectorMask | null;
}

export interface SerializedShapeLayer extends SerializedLayerBase {
  readonly kind: 'shape';
  readonly geometry: ShapeGeometry;
  readonly style: ShapeStyle;
  readonly rasterMask: SerializedRasterMask | null;
  readonly vectorMask: SerializedVectorMask | null;
}

export interface SerializedTextLayer extends SerializedLayerBase {
  readonly kind: 'text';
  readonly text: string;
  readonly width: number;
  readonly height: number;
  readonly style: TextStyle;
  readonly rasterMask: SerializedRasterMask | null;
  readonly vectorMask: SerializedVectorMask | null;
}

export interface SerializedSmartObjectLayer extends SerializedLayerBase {
  readonly kind: 'smart-object';
  readonly source: {
    readonly id: string;
    readonly name: string;
    readonly canvas: { readonly width: number; readonly height: number };
    readonly layers: readonly SerializedLayer[];
  };
  readonly smartFilters: readonly SmartFilter[];
  readonly rasterMask: SerializedRasterMask | null;
  readonly vectorMask: SerializedVectorMask | null;
}

export type SerializedLayer =
  | SerializedPixelLayer
  | SerializedGroupLayer
  | SerializedAdjustmentLayer
  | SerializedShapeLayer
  | SerializedTextLayer
  | SerializedSmartObjectLayer;

export interface SerializedAlphaChannel {
  readonly kind: 'alpha';
  readonly id: string;
  readonly name: string;
  readonly width: number;
  readonly height: number;
  readonly coverage: SerializedCoveragePayload;
}

export interface SerializedDocumentGuide {
  readonly id: string;
  readonly direction: 'horizontal' | 'vertical';
  readonly position: number;
}

export interface SerializedDocumentGrid {
  readonly horizontal: number;
  readonly vertical: number;
}

export interface SerializedImageDocument {
  readonly schemaVersion: typeof DOCUMENT_SCHEMA_VERSION;
  readonly document: {
    readonly id: string;
    readonly name: string;
    readonly canvas: { readonly width: number; readonly height: number };
    readonly layers: readonly SerializedLayer[];
    readonly channels: readonly SerializedAlphaChannel[];
    readonly selection: {
      readonly width: number;
      readonly height: number;
      readonly coverage: SerializedCoveragePayload;
    } | null;
    /** Opaque JSON-serializable import preservation bag (schema ≥ 17). */
    readonly preservation?: Record<string, unknown> | null;
    /** Ruler guides (schema ≥ 18). */
    readonly guides?: readonly SerializedDocumentGuide[];
    /** Optional document grid (schema ≥ 18). */
    readonly grid?: SerializedDocumentGrid | null;
    /** Document-level paths (schema ≥ 19). */
    readonly paths?: readonly SerializedDocumentPath[];
  };
}

export interface SerializedDocumentPath {
  readonly id: string;
  readonly name: string;
  readonly open: boolean;
  readonly commands: readonly PathCommand[];
}

export class PersistenceError extends Error {
  constructor(message: string, options?: ErrorOptions) {
    super(message, options);
    this.name = 'PersistenceError';
  }
}

export class NativeDocumentError extends Error {
  constructor(message: string, options?: ErrorOptions) {
    super(message, options);
    this.name = 'NativeDocumentError';
  }
}

const BASE64_ALPHABET = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/';
const DENSE_ENCODE_STRIPE_BYTES = 64 * 1024;

function encodeByteChunks(chunks: Iterable<Iterable<number>>): string {
  let result = '';
  let first: number | undefined;
  let second: number | undefined;
  for (const bytes of chunks) {
    for (const byte of bytes) {
      if (first === undefined) {
        first = byte;
        continue;
      }
      if (second === undefined) {
        second = byte;
        continue;
      }
      const packed = (first << 16) | (second << 8) | byte;
      result += BASE64_ALPHABET.charAt((packed >>> 18) & 63);
      result += BASE64_ALPHABET.charAt((packed >>> 12) & 63);
      result += BASE64_ALPHABET.charAt((packed >>> 6) & 63);
      result += BASE64_ALPHABET.charAt(packed & 63);
      first = undefined;
      second = undefined;
    }
  }
  if (first !== undefined) {
    const packed = (first << 16) | ((second ?? 0) << 8);
    result += BASE64_ALPHABET.charAt((packed >>> 18) & 63);
    result += BASE64_ALPHABET.charAt((packed >>> 12) & 63);
    result += second === undefined ? '=' : BASE64_ALPHABET.charAt((packed >>> 6) & 63);
    result += '=';
  }
  return result;
}

function encodeBytes(bytes: Iterable<number>): string {
  return encodeByteChunks([bytes]);
}

function* storeRowStripes(
  store: TiledPixelStore | TiledCoverageStore,
  bytesPerPixel: 1 | 4,
): IterableIterator<Uint8ClampedArray> {
  const rowBytes = store.width * bytesPerPixel;
  const rowsPerStripe = Math.max(1, Math.floor(DENSE_ENCODE_STRIPE_BYTES / rowBytes));
  for (let y = 0; y < store.height; y += rowsPerStripe) {
    const height = Math.min(rowsPerStripe, store.height - y);
    yield store.read({ x: 0, y, width: store.width, height });
  }
}

function decodeBytes(encoded: string): Uint8ClampedArray {
  const padding = encoded.endsWith('==') ? 2 : encoded.endsWith('=') ? 1 : 0;
  const contentLength = encoded.length - padding;
  let valid = encoded.length % 4 === 0;
  for (let index = 0; valid && index < contentLength; index += 1) {
    valid = BASE64_ALPHABET.includes(encoded[index] ?? '');
  }
  for (let index = contentLength; valid && index < encoded.length; index += 1) {
    valid = encoded[index] === '=';
  }
  if (!valid) {
    throw new PersistenceError('Invalid base64 pixel buffer');
  }
  const bytes = new Uint8ClampedArray((encoded.length / 4) * 3 - padding);
  let output = 0;
  for (let index = 0; index < encoded.length; index += 4) {
    const first = BASE64_ALPHABET.indexOf(encoded[index] ?? '');
    const second = BASE64_ALPHABET.indexOf(encoded[index + 1] ?? '');
    const thirdCharacter = encoded[index + 2];
    const fourthCharacter = encoded[index + 3];
    const third = thirdCharacter === '=' ? 0 : BASE64_ALPHABET.indexOf(thirdCharacter ?? '');
    const fourth = fourthCharacter === '=' ? 0 : BASE64_ALPHABET.indexOf(fourthCharacter ?? '');
    const packed = (first << 18) | (second << 12) | (third << 6) | fourth;
    if (output < bytes.length) bytes[output++] = (packed >>> 16) & 255;
    if (output < bytes.length) bytes[output++] = (packed >>> 8) & 255;
    if (output < bytes.length) bytes[output++] = packed & 255;
  }
  return bytes;
}

/** Prefer sparse when allocated storage is under half a full surface (or empty). */
function shouldEncodeSparse(allocatedBytes: number, fullBytes: number): boolean {
  if (fullBytes <= 0) return false;
  return allocatedBytes === 0 || allocatedBytes < fullBytes * 0.5;
}

function isSparseTilesPayload(value: unknown): value is SerializedSparseTiles {
  if (value === null || typeof value !== 'object') return false;
  const candidate = value as Record<string, unknown>;
  return candidate.format === 'sparse-tiles-v1' && Array.isArray(candidate.tiles);
}

function encodePixelStore(store: TiledPixelStore): SerializedPixelPayload {
  const fullBytes = store.width * store.height * 4;
  if (!shouldEncodeSparse(store.allocatedByteLength, fullBytes)) {
    return encodeByteChunks(storeRowStripes(store, 4));
  }
  const snapshot = store.snapshot();
  return {
    format: 'sparse-tiles-v1',
    tileSize: snapshot.tileSize,
    tiles: snapshot.tiles.map((tile) => ({
      column: tile.column,
      row: tile.row,
      data: encodeBytes(tile.pixels),
    })),
  };
}

function encodeCoverageStore(store: TiledCoverageStore): SerializedCoveragePayload {
  const fullBytes = store.width * store.height;
  if (!shouldEncodeSparse(store.allocatedByteLength, fullBytes)) {
    return encodeByteChunks(storeRowStripes(store, 1));
  }
  const snapshot = store.snapshot();
  return {
    format: 'sparse-tiles-v1',
    tileSize: snapshot.tileSize,
    defaultCoverage: snapshot.defaultCoverage,
    tiles: snapshot.tiles.map((tile) => ({
      column: tile.column,
      row: tile.row,
      data: encodeBytes(tile.coverage),
    })),
  };
}

function decodePixelPayload(
  payload: unknown,
  width: number,
  height: number,
  label: string,
): TiledPixelStore {
  if (typeof payload === 'string') {
    return TiledPixelStore.fromContiguous(width, height, decodeBytes(payload));
  }
  if (!isSparseTilesPayload(payload)) {
    throw new PersistenceError(`${label} must be base64 or sparse-tiles-v1`);
  }
  const tileSize = numberField(payload.tileSize, `${label} tile size`);
  return TiledPixelStore.fromSnapshot({
    schemaVersion: 1,
    width,
    height,
    tileSize,
    tiles: payload.tiles.map((tile, index) => {
      const entry = object(tile, `${label} tile ${String(index)}`);
      return {
        column: numberField(entry.column, `${label} tile column`),
        row: numberField(entry.row, `${label} tile row`),
        pixels: decodeBytes(stringField(entry.data, `${label} tile data`)),
      };
    }),
  });
}

function decodeCoveragePayload(
  payload: unknown,
  width: number,
  height: number,
  label: string,
): TiledCoverageStore {
  if (typeof payload === 'string') {
    return TiledCoverageStore.fromContiguous(width, height, decodeBytes(payload));
  }
  if (!isSparseTilesPayload(payload)) {
    throw new PersistenceError(`${label} must be base64 or sparse-tiles-v1`);
  }
  const tileSize = numberField(payload.tileSize, `${label} tile size`);
  const defaultCoverage =
    payload.defaultCoverage === undefined
      ? 0
      : numberField(payload.defaultCoverage, `${label} default coverage`);
  return TiledCoverageStore.fromSnapshot({
    schemaVersion: 2,
    width,
    height,
    tileSize,
    defaultCoverage,
    tiles: payload.tiles.map((tile, index) => {
      const entry = object(tile, `${label} tile ${String(index)}`);
      return {
        column: numberField(entry.column, `${label} tile column`),
        row: numberField(entry.row, `${label} tile row`),
        coverage: decodeBytes(stringField(entry.data, `${label} tile data`)),
      };
    }),
  });
}

interface PayloadEncoders {
  readonly pixel: (store: TiledPixelStore) => SerializedPixelPayload;
  readonly coverage: (store: TiledCoverageStore) => SerializedCoveragePayload;
}

const base64PayloadEncoders: PayloadEncoders = {
  pixel: encodePixelStore,
  coverage: encodeCoverageStore,
};

function serializeLayer(layer: Layer, encoders: PayloadEncoders): SerializedLayer {
  const base = {
    id: layer.id,
    name: layer.name,
    visible: layer.visible,
    opacity: layer.opacity,
    fillOpacity: layer.fillOpacity,
    blendMode: layer.blendMode,
    clipping: layer.clipping,
    locks: { ...layer.locks },
    transform: { ...layer.transform },
    effects: layer.effects.map((effect) => ({ ...effect, color: { ...effect.color } })),
  };
  const rasterMask: SerializedRasterMask | null =
    layer.rasterMask !== null
      ? {
          width: layer.rasterMask.width,
          height: layer.rasterMask.height,
          coverage: encoders.coverage(layer.rasterMask.coverageStore),
          enabled: layer.rasterMask.enabled,
          density: layer.rasterMask.density,
        }
      : null;
  const vectorMask: SerializedVectorMask | null =
    layer.vectorMask !== null
      ? {
          width: layer.vectorMask.width,
          height: layer.vectorMask.height,
          commands: layer.vectorMask.commands.map((command) => ({ ...command })),
          enabled: layer.vectorMask.enabled,
          density: layer.vectorMask.density,
          inverted: layer.vectorMask.inverted,
        }
      : null;
  if (layer.kind === 'pixel') {
    return {
      ...base,
      kind: 'pixel',
      width: layer.width,
      height: layer.height,
      pixels: encoders.pixel(layer.pixelStore),
      rasterMask,
      vectorMask,
    };
  }
  if (layer.kind === 'adjustment') {
    return {
      ...base,
      kind: 'adjustment',
      adjustment: { ...layer.adjustment },
      rasterMask,
      vectorMask,
    };
  }
  if (layer.kind === 'shape') {
    return {
      ...base,
      kind: 'shape',
      geometry: { ...layer.geometry },
      style: {
        fill: layer.style.fill === null ? null : { ...layer.style.fill },
        stroke: layer.style.stroke === null ? null : { ...layer.style.stroke },
        strokeWidth: layer.style.strokeWidth,
      },
      rasterMask,
      vectorMask,
    };
  }
  if (layer.kind === 'text') {
    return {
      ...base,
      kind: 'text',
      text: layer.text,
      width: layer.width,
      height: layer.height,
      style: { ...layer.style, color: { ...layer.style.color } },
      rasterMask,
      vectorMask,
    };
  }
  if (layer.kind === 'smart-object') {
    return {
      ...base,
      kind: 'smart-object',
      source: {
        id: layer.source.id,
        name: layer.source.name,
        canvas: { ...layer.source.canvas },
        layers: layer.source.layers.map((child) => serializeLayer(child, encoders)),
      },
      smartFilters: layer.smartFilters.map((filter) => ({ ...filter })),
      rasterMask,
      vectorMask,
    };
  }
  return {
    ...base,
    kind: 'group',
    children: layer.children.map((child) => serializeLayer(child, encoders)),
    compositing: layer.compositing,
    rasterMask,
    vectorMask,
  };
}

function toSerializedDocumentWithEncoders(
  document: ImageDocument,
  encoders: PayloadEncoders,
): SerializedImageDocument {
  validateDocument(document);
  return {
    schemaVersion: DOCUMENT_SCHEMA_VERSION,
    document: {
      id: document.id,
      name: document.name,
      canvas: { ...document.canvas },
      layers: document.layers.map((layer) => serializeLayer(layer, encoders)),
      channels: document.channels.map((channel) => ({
        kind: 'alpha',
        id: channel.id,
        name: channel.name,
        width: channel.width,
        height: channel.height,
        coverage: encoders.coverage(channel.coverageStore),
      })),
      selection:
        document.selection === null
          ? null
          : {
              width: document.selection.width,
              height: document.selection.height,
              coverage: encoders.coverage(document.selection.coverageStore),
            },
      preservation: document.preservation ?? null,
      guides: document.guides.map((guide) => ({
        id: guide.id,
        direction: guide.direction,
        position: guide.position,
      })),
      grid: document.grid ? { ...document.grid } : null,
      paths: document.paths.map((path) => ({
        id: path.id,
        name: path.name,
        open: path.open,
        commands: path.commands.map((command) => ({ ...command })),
      })),
    },
  };
}

export function toSerializedDocument(document: ImageDocument): SerializedImageDocument {
  return toSerializedDocumentWithEncoders(document, base64PayloadEncoders);
}

export function serializeDocument(document: ImageDocument): string {
  return JSON.stringify(toSerializedDocument(document));
}

function object(value: unknown, label: string): Record<string, unknown> {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    throw new PersistenceError(`${label} must be an object`);
  }
  return value as Record<string, unknown>;
}

function stringField(value: unknown, label: string): string {
  if (typeof value !== 'string') throw new PersistenceError(`${label} must be a string`);
  return value;
}

function numberField(value: unknown, label: string): number {
  if (typeof value !== 'number') throw new PersistenceError(`${label} must be a number`);
  return value;
}

function booleanField(value: unknown, label: string): boolean {
  if (typeof value !== 'boolean') throw new PersistenceError(`${label} must be a boolean`);
  return value;
}

function enumField<T extends string>(value: unknown, allowed: readonly T[], label: string): T {
  if (typeof value !== 'string' || !(allowed as readonly string[]).includes(value)) {
    throw new PersistenceError(`${label} is invalid`);
  }
  return value as T;
}

function deserializeRasterMask(
  value: unknown,
  schemaVersion: SupportedSchemaVersion = DOCUMENT_SCHEMA_VERSION,
): RasterMask {
  const mask = object(value, 'Raster mask');
  const width = numberField(mask.width, 'Raster mask width');
  const height = numberField(mask.height, 'Raster mask height');
  const options = {
    enabled: booleanField(mask.enabled, 'Raster mask enabled'),
    density: numberField(mask.density, 'Raster mask density'),
  };
  // Schema ≥20 may carry sparse-tiles-v1 coverage without full densify.
  if (schemaVersion >= 20 || isSparseTilesPayload(mask.coverage)) {
    return createRasterMaskFromCoverageStore(
      width,
      height,
      decodeCoveragePayload(mask.coverage, width, height, 'Raster mask coverage'),
      options,
    );
  }
  return createRasterMask(
    width,
    height,
    decodeBytes(stringField(mask.coverage, 'Raster mask coverage')),
    options,
  );
}

function deserializeVectorMask(value: unknown): VectorMask {
  const mask = object(value, 'Vector mask');
  return {
    width: numberField(mask.width, 'Vector mask width'),
    height: numberField(mask.height, 'Vector mask height'),
    commands: deserializePathCommands(mask.commands),
    enabled: booleanField(mask.enabled, 'Vector mask enabled'),
    density: numberField(mask.density, 'Vector mask density'),
    inverted: booleanField(mask.inverted, 'Vector mask inverted'),
  };
}

function deserializeAdjustment(value: unknown): Adjustment {
  const adjustment = object(value, 'Adjustment');
  const type = stringField(adjustment.type, 'Adjustment type');
  switch (type) {
    case 'brightness-contrast':
      return {
        type,
        brightness: numberField(adjustment.brightness, 'Adjustment brightness'),
        contrast: numberField(adjustment.contrast, 'Adjustment contrast'),
      };
    case 'invert':
      return { type };
    case 'levels':
      return {
        type,
        inputBlack: numberField(adjustment.inputBlack, 'Levels input black'),
        inputWhite: numberField(adjustment.inputWhite, 'Levels input white'),
        gamma: numberField(adjustment.gamma, 'Levels gamma'),
        outputBlack: numberField(adjustment.outputBlack, 'Levels output black'),
        outputWhite: numberField(adjustment.outputWhite, 'Levels output white'),
      };
    case 'hue-saturation':
      return {
        type,
        hue: numberField(adjustment.hue, 'Adjustment hue'),
        saturation: numberField(adjustment.saturation, 'Adjustment saturation'),
        lightness: numberField(adjustment.lightness, 'Adjustment lightness'),
      };
    case 'posterize':
      return {
        type,
        levels: numberField(adjustment.levels, 'Posterize levels'),
      };
    case 'threshold':
      return {
        type,
        level: numberField(adjustment.level, 'Threshold level'),
      };
    default:
      throw new PersistenceError(`Unsupported adjustment type: ${type}`);
  }
}

function deserializeColor(value: unknown, label: string) {
  const color = object(value, label);
  return {
    red: numberField(color.red, `${label} red`),
    green: numberField(color.green, `${label} green`),
    blue: numberField(color.blue, `${label} blue`),
    alpha: numberField(color.alpha, `${label} alpha`),
  };
}

function deserializeShapeStyle(value: unknown): ShapeStyle {
  const style = object(value, 'Shape style');
  return {
    fill: style.fill === null ? null : deserializeColor(style.fill, 'Shape fill'),
    stroke: style.stroke === null ? null : deserializeColor(style.stroke, 'Shape stroke'),
    strokeWidth: numberField(style.strokeWidth, 'Shape stroke width'),
  };
}

function deserializeTextStyle(value: unknown): TextStyle {
  const style = object(value, 'Text style');
  return {
    fontFamily: stringField(style.fontFamily, 'Text font family'),
    fontSize: numberField(style.fontSize, 'Text font size'),
    fontWeight: numberField(style.fontWeight, 'Text font weight'),
    italic: booleanField(style.italic, 'Text italic'),
    lineHeight: numberField(style.lineHeight, 'Text line height'),
    align: enumField(style.align, ['left', 'center', 'right'] as const, 'Text alignment'),
    color: deserializeColor(style.color, 'Text color'),
  };
}

function deserializeLayerEffects(value: unknown): readonly LayerEffect[] {
  if (!Array.isArray(value)) throw new PersistenceError('Layer effects must be an array');
  return value.map((candidate, index) => {
    const effect = object(candidate, `Layer effect ${String(index + 1)}`);
    const common = {
      enabled: booleanField(effect.enabled, 'Layer effect enabled'),
      opacity: numberField(effect.opacity, 'Layer effect opacity'),
      color: deserializeColor(effect.color, 'Layer effect color'),
    };
    const type = stringField(effect.type, 'Layer effect type');
    if (type === 'color-overlay') {
      return {
        ...common,
        type,
        blendMode: enumField(effect.blendMode, BLEND_MODES, 'Layer effect blend mode'),
      };
    }
    if (type === 'stroke') {
      return { ...common, type, size: numberField(effect.size, 'Layer effect stroke size') };
    }
    if (type === 'drop-shadow') {
      return {
        ...common,
        type,
        offsetX: numberField(effect.offsetX, 'Layer effect shadow offset x'),
        offsetY: numberField(effect.offsetY, 'Layer effect shadow offset y'),
        blur: numberField(effect.blur, 'Layer effect shadow blur'),
      };
    }
    throw new PersistenceError(`Unsupported layer effect type: ${type}`);
  });
}

function deserializeSmartFilters(value: unknown): readonly SmartFilter[] {
  if (!Array.isArray(value)) throw new PersistenceError('Smart filters must be an array');
  return value.map((candidate, index) => {
    const filter = object(candidate, `Smart filter ${String(index + 1)}`);
    const common = {
      id: stringField(filter.id, 'Smart filter id'),
      enabled: booleanField(filter.enabled, 'Smart filter enabled'),
      opacity: numberField(filter.opacity, 'Smart filter opacity'),
    };
    const type = stringField(filter.type, 'Smart filter type');
    if (type === 'invert' || type === 'grayscale') return { ...common, type };
    if (type === 'gaussian-blur')
      return { ...common, type, radius: numberField(filter.radius, 'Smart filter radius') };
    if (type === 'brightness-contrast')
      return {
        ...common,
        type,
        brightness: numberField(filter.brightness, 'Smart filter brightness'),
        contrast: numberField(filter.contrast, 'Smart filter contrast'),
      };
    throw new PersistenceError(`Unsupported smart filter type: ${type}`);
  });
}

type SupportedSchemaVersion =
  | 1
  | 2
  | 3
  | 4
  | 5
  | 6
  | 7
  | 8
  | 9
  | 10
  | 11
  | 12
  | 13
  | 14
  | 15
  | 16
  | 17
  | 18
  | 19
  | typeof DOCUMENT_SCHEMA_VERSION;

function deserializePathCommands(value: unknown): readonly PathCommand[] {
  if (!Array.isArray(value)) throw new PersistenceError('Path commands must be an array');
  return value.map((candidate, index) => {
    const command = object(candidate, `Path command ${String(index + 1)}`);
    const type = stringField(command.type, 'Path command type');
    if (type === 'move' || type === 'line') {
      return {
        type,
        x: numberField(command.x, 'Path command x'),
        y: numberField(command.y, 'Path command y'),
      };
    }
    if (type === 'cubic') {
      return {
        type,
        control1X: numberField(command.control1X, 'Path control 1 x'),
        control1Y: numberField(command.control1Y, 'Path control 1 y'),
        control2X: numberField(command.control2X, 'Path control 2 x'),
        control2Y: numberField(command.control2Y, 'Path control 2 y'),
        x: numberField(command.x, 'Path command x'),
        y: numberField(command.y, 'Path command y'),
      };
    }
    if (type === 'close') return { type };
    throw new PersistenceError(`Unsupported path command type: ${type}`);
  });
}

function deserializeLayer(
  value: unknown,
  schemaVersion: SupportedSchemaVersion,
  smartObjectDepth = 0,
): Layer {
  const layer = object(value, 'Layer');
  const common = {
    id: stringField(layer.id, 'Layer id'),
    name: stringField(layer.name, 'Layer name'),
    visible: booleanField(layer.visible, 'Layer visibility'),
    opacity: numberField(layer.opacity, 'Layer opacity'),
    fillOpacity: schemaVersion < 6 ? 1 : numberField(layer.fillOpacity, 'Layer fill opacity'),
    blendMode:
      schemaVersion < 6
        ? ('normal' as const)
        : enumField(layer.blendMode, BLEND_MODES, 'Layer blend mode'),
    clipping: schemaVersion < 6 ? false : booleanField(layer.clipping, 'Layer clipping'),
    locks:
      schemaVersion < 6
        ? { transparency: false, pixels: false, position: false, all: false }
        : (() => {
            const locks = object(layer.locks, 'Layer locks');
            return {
              transparency: booleanField(locks.transparency, 'Transparency lock'),
              pixels: booleanField(locks.pixels, 'Pixel lock'),
              position: booleanField(locks.position, 'Position lock'),
              all: booleanField(locks.all, 'All lock'),
            };
          })(),
    transform:
      schemaVersion === 1
        ? { a: 1, b: 0, c: 0, d: 1, e: 0, f: 0 }
        : (() => {
            const transform = object(layer.transform, 'Layer transform');
            return schemaVersion < 5
              ? {
                  a: 1,
                  b: 0,
                  c: 0,
                  d: 1,
                  e: numberField(transform.x, 'Layer transform x'),
                  f: numberField(transform.y, 'Layer transform y'),
                }
              : {
                  a: numberField(transform.a, 'Layer transform a'),
                  b: numberField(transform.b, 'Layer transform b'),
                  c: numberField(transform.c, 'Layer transform c'),
                  d: numberField(transform.d, 'Layer transform d'),
                  e: numberField(transform.e, 'Layer transform e'),
                  f: numberField(transform.f, 'Layer transform f'),
                };
          })(),
    effects: schemaVersion < 8 ? [] : deserializeLayerEffects(layer.effects),
  };
  if (layer.kind === 'pixel') {
    const width = numberField(layer.width, 'Pixel layer width');
    const height = numberField(layer.height, 'Pixel layer height');
    return createPixelLayer({
      ...common,
      width,
      height,
      pixelStore: decodePixelPayload(layer.pixels, width, height, 'Pixel buffer'),
      rasterMask:
        schemaVersion < 3 || layer.rasterMask === null || layer.rasterMask === undefined
          ? null
          : deserializeRasterMask(layer.rasterMask, schemaVersion),
      vectorMask:
        schemaVersion < 13 || layer.vectorMask === null || layer.vectorMask === undefined
          ? null
          : deserializeVectorMask(layer.vectorMask),
    });
  }
  if (layer.kind === 'group') {
    if (!Array.isArray(layer.children))
      throw new PersistenceError('Group children must be an array');
    return createGroupLayer({
      ...common,
      children: layer.children.map((child) =>
        deserializeLayer(child, schemaVersion, smartObjectDepth),
      ),
      compositing:
        schemaVersion < 6
          ? 'pass-through'
          : enumField(
              layer.compositing,
              ['pass-through', 'isolated'] as const,
              'Group compositing',
            ),
      rasterMask:
        schemaVersion < 16 || layer.rasterMask === null || layer.rasterMask === undefined
          ? null
          : deserializeRasterMask(layer.rasterMask),
      vectorMask:
        schemaVersion < 15 || layer.vectorMask === null || layer.vectorMask === undefined
          ? null
          : deserializeVectorMask(layer.vectorMask),
    });
  }
  if (layer.kind === 'adjustment') {
    if (schemaVersion < 6) throw new PersistenceError('Unsupported layer kind');
    return createAdjustmentLayer({
      ...common,
      adjustment: deserializeAdjustment(layer.adjustment),
      rasterMask:
        layer.rasterMask === null || layer.rasterMask === undefined
          ? null
          : deserializeRasterMask(layer.rasterMask),
      vectorMask:
        schemaVersion < 14 || layer.vectorMask === null || layer.vectorMask === undefined
          ? null
          : deserializeVectorMask(layer.vectorMask),
    });
  }
  if (layer.kind === 'shape') {
    if (schemaVersion < 7) throw new PersistenceError('Unsupported layer kind');
    const geometry = object(layer.geometry, 'Shape geometry');
    return createShapeLayer({
      ...common,
      geometry: {
        type: enumField(
          geometry.type,
          schemaVersion < 12 ? (['rectangle', 'ellipse'] as const) : SHAPE_KINDS,
          'Shape type',
        ),
        width: numberField(geometry.width, 'Shape width'),
        height: numberField(geometry.height, 'Shape height'),
        cornerRadius: numberField(geometry.cornerRadius, 'Shape corner radius'),
        commands: schemaVersion < 12 ? [] : deserializePathCommands(geometry.commands),
      },
      style: deserializeShapeStyle(layer.style),
      rasterMask:
        layer.rasterMask === null || layer.rasterMask === undefined
          ? null
          : deserializeRasterMask(layer.rasterMask),
      vectorMask:
        schemaVersion < 14 || layer.vectorMask === null || layer.vectorMask === undefined
          ? null
          : deserializeVectorMask(layer.vectorMask),
    });
  }
  if (layer.kind === 'text') {
    if (schemaVersion < 7) throw new PersistenceError('Unsupported layer kind');
    return createTextLayer({
      ...common,
      text: stringField(layer.text, 'Text content'),
      width: numberField(layer.width, 'Text width'),
      height: numberField(layer.height, 'Text height'),
      style: deserializeTextStyle(layer.style),
      rasterMask:
        layer.rasterMask === null || layer.rasterMask === undefined
          ? null
          : deserializeRasterMask(layer.rasterMask),
      vectorMask:
        schemaVersion < 14 || layer.vectorMask === null || layer.vectorMask === undefined
          ? null
          : deserializeVectorMask(layer.vectorMask),
    });
  }
  if (layer.kind === 'smart-object') {
    if (schemaVersion < 9) throw new PersistenceError('Unsupported layer kind');
    if (smartObjectDepth >= 16)
      throw new PersistenceError('Smart object nesting exceeds 16 levels');
    const source = object(layer.source, 'Smart object source');
    const canvas = object(source.canvas, 'Smart object canvas');
    if (!Array.isArray(source.layers))
      throw new PersistenceError('Smart object layers must be an array');
    return createSmartObjectLayer({
      ...common,
      source: {
        id: stringField(source.id, 'Smart object source id'),
        name: stringField(source.name, 'Smart object source name'),
        canvas: {
          width: numberField(canvas.width, 'Smart object width'),
          height: numberField(canvas.height, 'Smart object height'),
        },
        layers: source.layers.map((child) =>
          deserializeLayer(child, schemaVersion, smartObjectDepth + 1),
        ),
      },
      smartFilters: schemaVersion < 10 ? [] : deserializeSmartFilters(layer.smartFilters),
      rasterMask:
        layer.rasterMask === null || layer.rasterMask === undefined
          ? null
          : deserializeRasterMask(layer.rasterMask),
      vectorMask:
        schemaVersion < 14 || layer.vectorMask === null || layer.vectorMask === undefined
          ? null
          : deserializeVectorMask(layer.vectorMask),
    });
  }
  throw new PersistenceError('Unsupported layer kind');
}

export function fromSerializedDocument(value: unknown): ImageDocument {
  try {
    const envelope = object(value, 'Serialized document');
    if (
      envelope.schemaVersion !== 1 &&
      envelope.schemaVersion !== 2 &&
      envelope.schemaVersion !== 3 &&
      envelope.schemaVersion !== 4 &&
      envelope.schemaVersion !== 5 &&
      envelope.schemaVersion !== 6 &&
      envelope.schemaVersion !== 7 &&
      envelope.schemaVersion !== 8 &&
      envelope.schemaVersion !== 9 &&
      envelope.schemaVersion !== 10 &&
      envelope.schemaVersion !== 11 &&
      envelope.schemaVersion !== 12 &&
      envelope.schemaVersion !== 13 &&
      envelope.schemaVersion !== 14 &&
      envelope.schemaVersion !== 15 &&
      envelope.schemaVersion !== 16 &&
      envelope.schemaVersion !== 17 &&
      envelope.schemaVersion !== 18 &&
      envelope.schemaVersion !== 19 &&
      envelope.schemaVersion !== DOCUMENT_SCHEMA_VERSION
    ) {
      throw new PersistenceError(
        `Unsupported document schema version: ${String(envelope.schemaVersion)}`,
      );
    }
    const document = object(envelope.document, 'Document');
    const schemaVersion = envelope.schemaVersion as SupportedSchemaVersion;
    const canvas = object(document.canvas, 'Document canvas');
    if (!Array.isArray(document.layers))
      throw new PersistenceError('Document layers must be an array');
    if (schemaVersion >= 4 && !Array.isArray(document.channels))
      throw new PersistenceError('Document channels must be an array');
    const preservation =
      schemaVersion < 17 || document.preservation == null
        ? null
        : object(document.preservation, 'Document preservation');
    const guides =
      schemaVersion < 18 || document.guides == null
        ? []
        : (() => {
            if (!Array.isArray(document.guides)) {
              throw new PersistenceError('Document guides must be an array');
            }
            return (document.guides as unknown[]).map((value, index) => {
              const guide = object(value, `Guide ${String(index)}`);
              const direction = stringField(guide.direction, 'Guide direction');
              if (direction !== 'horizontal' && direction !== 'vertical') {
                throw new PersistenceError('Guide direction must be horizontal or vertical');
              }
              return createDocumentGuide({
                id: stringField(guide.id, 'Guide id'),
                direction,
                position: numberField(guide.position, 'Guide position'),
              });
            });
          })();
    const grid =
      schemaVersion < 18 || document.grid == null
        ? null
        : (() => {
            const gridValue = object(document.grid, 'Document grid');
            return {
              horizontal: numberField(gridValue.horizontal, 'Grid horizontal spacing'),
              vertical: numberField(gridValue.vertical, 'Grid vertical spacing'),
            };
          })();
    const paths =
      schemaVersion < 19 || document.paths == null
        ? []
        : (() => {
            if (!Array.isArray(document.paths)) {
              throw new PersistenceError('Document paths must be an array');
            }
            return (document.paths as unknown[]).map((value, index) => {
              const path = object(value, `Document path ${String(index)}`);
              return createDocumentPath({
                id: stringField(path.id, 'Document path id'),
                name: stringField(path.name, 'Document path name'),
                open: booleanField(path.open, 'Document path open'),
                commands: deserializePathCommands(path.commands),
              });
            });
          })();
    return createDocument({
      id: stringField(document.id, 'Document id'),
      name: stringField(document.name, 'Document name'),
      width: numberField(canvas.width, 'Canvas width'),
      height: numberField(canvas.height, 'Canvas height'),
      layers: document.layers.map((layer) => deserializeLayer(layer, schemaVersion)),
      channels:
        schemaVersion < 4
          ? []
          : (document.channels as unknown[]).map((value) => {
              const channel = object(value, 'Alpha channel');
              if (channel.kind !== 'alpha') {
                throw new PersistenceError('Unsupported channel kind');
              }
              const width = numberField(channel.width, 'Alpha channel width');
              const height = numberField(channel.height, 'Alpha channel height');
              return createAlphaChannel({
                id: stringField(channel.id, 'Alpha channel id'),
                name: stringField(channel.name, 'Alpha channel name'),
                width,
                height,
                coverageStore: decodeCoveragePayload(
                  channel.coverage,
                  width,
                  height,
                  'Alpha channel coverage',
                ),
              });
            }),
      selection:
        schemaVersion === 1 || document.selection === null || document.selection === undefined
          ? null
          : (() => {
              const selection = object(document.selection, 'Selection');
              const width = numberField(selection.width, 'Selection width');
              const height = numberField(selection.height, 'Selection height');
              // Contiguous (legacy) or sparse-tiles-v1 (schema ≥20) rehydrate to tiled ownership.
              return createSelectionMask({
                width,
                height,
                coverageStore: decodeCoveragePayload(
                  selection.coverage,
                  width,
                  height,
                  'Selection coverage',
                ),
              });
            })(),
      guides,
      grid,
      paths,
      preservation,
    });
  } catch (error) {
    if (error instanceof PersistenceError) throw error;
    throw new PersistenceError('Serialized document is invalid', { cause: error });
  }
}

export function deserializeDocument(serialized: string): ImageDocument {
  try {
    return fromSerializedDocument(JSON.parse(serialized) as unknown);
  } catch (error) {
    if (error instanceof PersistenceError) throw error;
    throw new PersistenceError('Serialized document is not valid JSON', { cause: error });
  }
}

interface CasPayloadCollection {
  readonly pixels: WeakMap<TiledPixelStore, SerializedCasTiles>;
  readonly coverage: WeakMap<TiledCoverageStore, SerializedCasTiles>;
  readonly resources: Map<string, Uint8Array>;
}

function bytesToHex(bytes: Uint8Array): string {
  return [...bytes].map((value) => value.toString(16).padStart(2, '0')).join('');
}

function hexToBytes(value: string): Uint8Array {
  if (!/^[0-9a-f]{64}$/u.test(value)) {
    throw new NativeDocumentError('The native document resource ID is invalid.');
  }
  const bytes = new Uint8Array(32);
  for (let index = 0; index < bytes.length; index += 1) {
    bytes[index] = Number.parseInt(value.slice(index * 2, index * 2 + 2), 16);
  }
  return bytes;
}

async function collectCasPayloads(document: ImageDocument): Promise<CasPayloadCollection> {
  const pixels = new WeakMap<TiledPixelStore, SerializedCasTiles>();
  const coverage = new WeakMap<TiledCoverageStore, SerializedCasTiles>();
  const resources = new Map<string, Uint8Array>();
  const resourceId = async (source: Uint8ClampedArray): Promise<string> => {
    const bytes = new Uint8Array(source.buffer, source.byteOffset, source.byteLength);
    const id = bytesToHex(await sha256(bytes));
    if (!resources.has(id)) resources.set(id, new Uint8Array(bytes));
    return id;
  };
  const registerPixels = async (store: TiledPixelStore): Promise<void> => {
    if (pixels.has(store)) return;
    const tiles: Array<{ column: number; row: number; resource: string }> = [];
    for (const tile of store.tiles()) {
      tiles.push({
        column: tile.column,
        row: tile.row,
        resource: await resourceId(tile.pixels),
      });
    }
    pixels.set(store, {
      format: 'cas-tiles-v1',
      tileSize: store.tileSize,
      tiles,
    });
  };
  const registerCoverage = async (store: TiledCoverageStore): Promise<void> => {
    if (coverage.has(store)) return;
    const tiles: Array<{ column: number; row: number; resource: string }> = [];
    for (const tile of store.tiles()) {
      tiles.push({
        column: tile.column,
        row: tile.row,
        resource: await resourceId(tile.coverage),
      });
    }
    coverage.set(store, {
      format: 'cas-tiles-v1',
      tileSize: store.tileSize,
      defaultCoverage: store.defaultCoverage,
      tiles,
    });
  };
  const visitLayer = async (layer: Layer): Promise<void> => {
    if (layer.rasterMask !== null) await registerCoverage(layer.rasterMask.coverageStore);
    if (layer.kind === 'pixel') await registerPixels(layer.pixelStore);
    else if (layer.kind === 'group') {
      for (const child of layer.children) await visitLayer(child);
    } else if (layer.kind === 'smart-object') {
      for (const child of layer.source.layers) await visitLayer(child);
    }
  };
  for (const layer of document.layers) await visitLayer(layer);
  for (const channel of document.channels) await registerCoverage(channel.coverageStore);
  if (document.selection !== null) await registerCoverage(document.selection.coverageStore);
  return { pixels, coverage, resources };
}

interface CasDocumentRecord {
  readonly manifest: string;
  readonly resourceIds: readonly string[];
  readonly resources: ReadonlyMap<string, Uint8Array>;
}

async function serializeCasDocument(document: ImageDocument): Promise<CasDocumentRecord> {
  const collection = await collectCasPayloads(document);
  const manifest = toSerializedDocumentWithEncoders(document, {
    pixel: (store) => {
      const payload = collection.pixels.get(store);
      if (payload === undefined) throw new NativeDocumentError('Pixel CAS payload is missing.');
      return payload;
    },
    coverage: (store) => {
      const payload = collection.coverage.get(store);
      if (payload === undefined) throw new NativeDocumentError('Coverage CAS payload is missing.');
      return payload;
    },
  });
  return {
    manifest: JSON.stringify(manifest),
    resourceIds: [...collection.resources.keys()].sort(),
    resources: collection.resources,
  };
}

async function encodeCasPayload(document: ImageDocument): Promise<Uint8Array> {
  const encoded = await serializeCasDocument(document);
  const manifestBytes = new TextEncoder().encode(encoded.manifest);
  const entries = encoded.resourceIds.map((id) => [id, encoded.resources.get(id)] as const);
  if (entries.some(([, bytes]) => bytes === undefined)) {
    throw new NativeDocumentError('Native document CAS resource is missing during encode.');
  }
  const byteLength =
    4 +
    manifestBytes.byteLength +
    4 +
    entries.reduce((total, [, bytes]) => total + 32 + 4 + (bytes?.byteLength ?? 0), 0);
  if (byteLength > NATIVE_DOCUMENT_MAX_BYTES - NATIVE_DOCUMENT_HEADER_BYTES) {
    throw new NativeDocumentError(
      `Native document files cannot exceed ${String(NATIVE_DOCUMENT_MAX_BYTES)} bytes.`,
    );
  }
  const payload = new Uint8Array(byteLength);
  const view = new DataView(payload.buffer);
  let offset = 0;
  view.setUint32(offset, manifestBytes.byteLength, false);
  offset += 4;
  payload.set(manifestBytes, offset);
  offset += manifestBytes.byteLength;
  view.setUint32(offset, entries.length, false);
  offset += 4;
  for (const [id, bytes] of entries) {
    if (bytes === undefined)
      throw new NativeDocumentError('Native document CAS resource is missing.');
    payload.set(hexToBytes(id), offset);
    offset += 32;
    view.setUint32(offset, bytes.byteLength, false);
    offset += 4;
    payload.set(bytes, offset);
    offset += bytes.byteLength;
  }
  return payload;
}

export function hasNativeDocumentSignature(buffer: ArrayBuffer): boolean {
  if (buffer.byteLength < NATIVE_DOCUMENT_MAGIC.length) return false;
  const bytes = new Uint8Array(buffer, 0, NATIVE_DOCUMENT_MAGIC.length);
  return NATIVE_DOCUMENT_MAGIC.every((value, index) => bytes[index] === value);
}

export async function encodeNativeDocument(document: ImageDocument): Promise<Uint8Array> {
  const payload = await encodeCasPayload(document);
  const totalBytes = NATIVE_DOCUMENT_HEADER_BYTES + payload.byteLength;
  if (totalBytes > NATIVE_DOCUMENT_MAX_BYTES) {
    throw new NativeDocumentError(
      `Native document files cannot exceed ${String(NATIVE_DOCUMENT_MAX_BYTES)} bytes.`,
    );
  }
  const checksum = await sha256(payload);
  const output = new Uint8Array(totalBytes);
  output.set(NATIVE_DOCUMENT_MAGIC, 0);
  const view = new DataView(output.buffer);
  let offset = NATIVE_DOCUMENT_MAGIC.length;
  view.setUint16(offset, NATIVE_DOCUMENT_FORMAT_VERSION, false);
  offset += 2;
  view.setUint32(offset, payload.byteLength, false);
  offset += 4;
  output.set(checksum, offset);
  offset += NATIVE_DOCUMENT_CHECKSUM_BYTES;
  output.set(payload, offset);
  return output;
}

function resolveCasTilePayload(
  value: unknown,
  resources: ReadonlyMap<string, Uint8Array>,
  label: string,
): unknown {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) return value;
  const payload = value as Record<string, unknown>;
  if (payload.format !== 'cas-tiles-v1') return value;
  if (!Array.isArray(payload.tiles)) {
    throw new NativeDocumentError(`${label} CAS tiles are invalid.`);
  }
  return {
    format: 'sparse-tiles-v1',
    tileSize: payload.tileSize,
    ...(payload.defaultCoverage === undefined ? {} : { defaultCoverage: payload.defaultCoverage }),
    tiles: payload.tiles.map((tile, index) => {
      const entry = object(tile, `${label} CAS tile ${String(index)}`);
      const id = stringField(entry.resource, `${label} CAS resource`);
      if (!/^[0-9a-f]{64}$/u.test(id)) {
        throw new NativeDocumentError(`${label} CAS resource ID is invalid.`);
      }
      const bytes = resources.get(id);
      if (bytes === undefined) {
        throw new NativeDocumentError(`${label} CAS resource is missing.`);
      }
      return {
        column: entry.column,
        row: entry.row,
        data: encodeBytes(bytes),
      };
    }),
  };
}

function resolveCasManifest(
  manifest: unknown,
  resources: ReadonlyMap<string, Uint8Array>,
): unknown {
  const envelope = object(manifest, 'Native CAS manifest');
  const document = object(envelope.document, 'Native CAS document');
  const resolveMask = (value: unknown, label: string): void => {
    if (value === null || value === undefined) return;
    const mask = object(value, label);
    mask.coverage = resolveCasTilePayload(mask.coverage, resources, `${label} coverage`);
  };
  const resolveLayer = (value: unknown): void => {
    const layer = object(value, 'Native CAS layer');
    resolveMask(layer.rasterMask, 'Native CAS raster mask');
    if (layer.kind === 'pixel') {
      layer.pixels = resolveCasTilePayload(layer.pixels, resources, 'Native CAS pixels');
    } else if (layer.kind === 'group') {
      if (!Array.isArray(layer.children)) {
        throw new NativeDocumentError('Native CAS group children are invalid.');
      }
      layer.children.forEach(resolveLayer);
    } else if (layer.kind === 'smart-object') {
      const source = object(layer.source, 'Native CAS smart-object source');
      if (!Array.isArray(source.layers)) {
        throw new NativeDocumentError('Native CAS smart-object layers are invalid.');
      }
      source.layers.forEach(resolveLayer);
    }
  };
  if (!Array.isArray(document.layers)) {
    throw new NativeDocumentError('Native CAS document layers are invalid.');
  }
  document.layers.forEach(resolveLayer);
  if (!Array.isArray(document.channels)) {
    throw new NativeDocumentError('Native CAS document channels are invalid.');
  }
  document.channels.forEach((value, index) => {
    const channel = object(value, `Native CAS channel ${String(index)}`);
    channel.coverage = resolveCasTilePayload(
      channel.coverage,
      resources,
      `Native CAS channel ${String(index)}`,
    );
  });
  if (document.selection !== null && document.selection !== undefined) {
    const selection = object(document.selection, 'Native CAS selection');
    selection.coverage = resolveCasTilePayload(
      selection.coverage,
      resources,
      'Native CAS selection',
    );
  }
  return envelope;
}

function deserializeCasDocument(
  manifest: string,
  resources: ReadonlyMap<string, Uint8Array>,
): ImageDocument {
  try {
    return fromSerializedDocument(resolveCasManifest(JSON.parse(manifest) as unknown, resources));
  } catch (error) {
    if (error instanceof NativeDocumentError) throw error;
    throw new NativeDocumentError('The native document CAS manifest is invalid.', {
      cause: error,
    });
  }
}

export interface NativeDocumentDecodeOptions {
  readonly signal?: AbortSignal;
  readonly onProgress?: (progress: number) => void;
}

function throwIfDecodeAborted(signal: AbortSignal | undefined): void {
  if (!signal?.aborted) return;
  throw signal.reason instanceof Error
    ? signal.reason
    : new DOMException('The operation was cancelled.', 'AbortError');
}

function reportDecodeProgress(options: NativeDocumentDecodeOptions, progress: number): void {
  options.onProgress?.(progress);
  throwIfDecodeAborted(options.signal);
}

async function decodeCasPayload(
  payload: Uint8Array,
  options: NativeDocumentDecodeOptions,
): Promise<ImageDocument> {
  const view = new DataView(payload.buffer, payload.byteOffset, payload.byteLength);
  let offset = 0;
  const readUint32 = (label: string): number => {
    if (offset + 4 > payload.byteLength) {
      throw new NativeDocumentError(`The native document ${label} is truncated.`);
    }
    const value = view.getUint32(offset, false);
    offset += 4;
    return value;
  };
  const manifestLength = readUint32('CAS manifest length');
  if (manifestLength > payload.byteLength - offset) {
    throw new NativeDocumentError('The native document CAS manifest is truncated.');
  }
  const manifestBytes = payload.subarray(offset, offset + manifestLength);
  offset += manifestLength;
  const resourceCount = readUint32('CAS resource count');
  if (resourceCount > Math.floor((payload.byteLength - offset) / 36)) {
    throw new NativeDocumentError('The native document CAS resource count is invalid.');
  }
  const resources = new Map<string, Uint8Array>();
  for (let index = 0; index < resourceCount; index += 1) {
    throwIfDecodeAborted(options.signal);
    if (offset + 36 > payload.byteLength) {
      throw new NativeDocumentError('The native document CAS resource header is truncated.');
    }
    const id = bytesToHex(payload.subarray(offset, offset + 32));
    offset += 32;
    const byteLength = readUint32('CAS resource length');
    if (byteLength > payload.byteLength - offset) {
      throw new NativeDocumentError('The native document CAS resource is truncated.');
    }
    if (resources.has(id)) {
      throw new NativeDocumentError('The native document CAS resource ID is duplicated.');
    }
    const bytes = new Uint8Array(payload.subarray(offset, offset + byteLength));
    offset += byteLength;
    if (bytesToHex(await sha256(bytes)) !== id) {
      throw new NativeDocumentError('The native document CAS resource hash does not match.');
    }
    resources.set(id, bytes);
    reportDecodeProgress(options, 0.1 + (0.8 * (index + 1)) / Math.max(1, resourceCount));
  }
  if (offset !== payload.byteLength) {
    throw new NativeDocumentError('The native document CAS payload has trailing bytes.');
  }
  const document = deserializeCasDocument(
    new TextDecoder('utf-8', { fatal: true }).decode(manifestBytes),
    resources,
  );
  reportDecodeProgress(options, 1);
  return document;
}

export async function decodeNativeDocument(
  input: ArrayBuffer | Uint8Array,
  options: NativeDocumentDecodeOptions = {},
): Promise<ImageDocument> {
  throwIfDecodeAborted(options.signal);
  reportDecodeProgress(options, 0);
  const bytes =
    input instanceof Uint8Array
      ? new Uint8Array(input.buffer, input.byteOffset, input.byteLength)
      : new Uint8Array(input);
  if (bytes.byteLength > NATIVE_DOCUMENT_MAX_BYTES) {
    throw new NativeDocumentError(
      `Native document files cannot exceed ${String(NATIVE_DOCUMENT_MAX_BYTES)} bytes.`,
    );
  }
  if (bytes.byteLength < NATIVE_DOCUMENT_HEADER_BYTES) {
    throw new NativeDocumentError('The native document header is truncated.');
  }
  const headerBuffer = bytes.buffer.slice(
    bytes.byteOffset,
    bytes.byteOffset + NATIVE_DOCUMENT_MAGIC.length,
  );
  if (!hasNativeDocumentSignature(headerBuffer)) {
    throw new NativeDocumentError('The file does not have a FreetoShop document signature.');
  }
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  let offset = NATIVE_DOCUMENT_MAGIC.length;
  const formatVersion = view.getUint16(offset, false);
  offset += 2;
  if (formatVersion !== 1 && formatVersion !== NATIVE_DOCUMENT_FORMAT_VERSION) {
    throw new NativeDocumentError(
      `Unsupported FreetoShop file format version: ${String(formatVersion)}.`,
    );
  }
  const payloadLength = view.getUint32(offset, false);
  offset += 4;
  const expectedLength = NATIVE_DOCUMENT_HEADER_BYTES + payloadLength;
  if (expectedLength !== bytes.byteLength) {
    throw new NativeDocumentError('The native document payload length is invalid.');
  }
  const expectedChecksum = bytes.subarray(offset, offset + NATIVE_DOCUMENT_CHECKSUM_BYTES);
  offset += NATIVE_DOCUMENT_CHECKSUM_BYTES;
  const payload = bytes.subarray(offset);
  const actualChecksum = await sha256(payload);
  throwIfDecodeAborted(options.signal);
  if (!constantTimeEqual(expectedChecksum, actualChecksum)) {
    throw new NativeDocumentError('The native document checksum does not match its contents.');
  }
  reportDecodeProgress(options, 0.1);
  if (formatVersion === NATIVE_DOCUMENT_FORMAT_VERSION) return decodeCasPayload(payload, options);
  try {
    const document = deserializeDocument(new TextDecoder('utf-8', { fatal: true }).decode(payload));
    reportDecodeProgress(options, 1);
    return document;
  } catch (error) {
    if (error instanceof NativeDocumentError) throw error;
    throw new NativeDocumentError('The native document payload is invalid.', { cause: error });
  }
}

async function sha256(bytes: Uint8Array): Promise<Uint8Array> {
  const source = new Uint8Array(bytes);
  return new Uint8Array(await globalThis.crypto.subtle.digest('SHA-256', source));
}

function constantTimeEqual(left: Uint8Array, right: Uint8Array): boolean {
  if (left.length !== right.length) return false;
  let difference = 0;
  for (let index = 0; index < left.length; index += 1) {
    difference |= (left[index] ?? 0) ^ (right[index] ?? 0);
  }
  return difference === 0;
}

export interface DocumentRepository {
  readonly lastSaveStats: IncrementalSaveStats | null;
  readonly lastLoadStats: DeferredLoadStats | null;
  save(document: ImageDocument): Promise<void>;
  prepareLoad(id: string): Promise<PreparedDocumentLoad | undefined>;
  load(id: string): Promise<ImageDocument | undefined>;
  delete(id: string): Promise<boolean>;
  list(): Promise<readonly string[]>;
}

export interface DocumentLoadMetadata {
  readonly id: string;
  readonly name: string;
  readonly width: number;
  readonly height: number;
  readonly topLevelLayerCount: number;
  readonly referencedResourceCount: number;
  readonly legacy: boolean;
}

export interface PreparedDocumentLoad {
  readonly metadata: DocumentLoadMetadata;
  hydrate(): Promise<ImageDocument>;
}

export interface DeferredLoadStats {
  readonly phase: 'metadata' | 'hydrated';
  readonly metadataBytes: number;
  readonly referencedResourceCount: number;
  readonly readResourceCount: number;
  readonly readResourceBytes: number;
}

export interface IncrementalSaveStats {
  readonly metadataBytes: number;
  readonly referencedResourceCount: number;
  readonly writtenResourceCount: number;
  readonly writtenResourceBytes: number;
  readonly reusedResourceCount: number;
  readonly removedResourceCount: number;
}

interface StoredCasDocument {
  readonly id: string;
  readonly manifest: string;
  readonly resourceIds: readonly string[];
}

interface StoredCasResource {
  readonly id: string;
  readonly bytes: Uint8Array;
  readonly documentIds: readonly string[];
}

function inspectStoredDocument(
  serialized: string,
  referencedResourceCount: number,
  legacy: boolean,
): DocumentLoadMetadata {
  try {
    const envelope = object(JSON.parse(serialized) as unknown, 'Stored document manifest');
    const document = object(envelope.document, 'Stored document');
    const canvas = object(document.canvas, 'Stored document canvas');
    if (!Array.isArray(document.layers)) {
      throw new PersistenceError('Stored document layers must be an array');
    }
    return {
      id: stringField(document.id, 'Stored document ID'),
      name: stringField(document.name, 'Stored document name'),
      width: numberField(canvas.width, 'Stored document width'),
      height: numberField(canvas.height, 'Stored document height'),
      topLevelLayerCount: document.layers.length,
      referencedResourceCount,
      legacy,
    };
  } catch (error) {
    if (error instanceof PersistenceError) throw error;
    throw new PersistenceError('Stored document manifest is invalid', { cause: error });
  }
}

function deferredStats(
  metadataBytes: number,
  referencedResourceCount: number,
  resources: readonly StoredCasResource[] = [],
  phase: DeferredLoadStats['phase'] = 'metadata',
): DeferredLoadStats {
  return {
    phase,
    metadataBytes,
    referencedResourceCount,
    readResourceCount: resources.length,
    readResourceBytes: resources.reduce((total, resource) => total + resource.bytes.byteLength, 0),
  };
}

async function hydrateCasRecord(
  record: StoredCasDocument,
  resources: readonly StoredCasResource[],
): Promise<ImageDocument> {
  const byId = new Map<string, Uint8Array>();
  for (const resource of resources) {
    if (resource.id !== bytesToHex(await sha256(resource.bytes))) {
      throw new PersistenceError('Stored document resource hash does not match');
    }
    byId.set(resource.id, new Uint8Array(resource.bytes));
  }
  if (record.resourceIds.some((id) => !byId.has(id))) {
    throw new PersistenceError('Stored document resource is missing');
  }
  try {
    return deserializeCasDocument(record.manifest, byId);
  } catch (error) {
    throw new PersistenceError('Stored document record is invalid', { cause: error });
  }
}

export class InMemoryDocumentRepository implements DocumentRepository {
  readonly #documents = new Map<string, StoredCasDocument>();
  readonly #resources = new Map<string, StoredCasResource>();
  #lastSaveStats: IncrementalSaveStats | null = null;
  #lastLoadStats: DeferredLoadStats | null = null;

  get lastSaveStats(): IncrementalSaveStats | null {
    return this.#lastSaveStats;
  }

  get lastLoadStats(): DeferredLoadStats | null {
    return this.#lastLoadStats;
  }

  async save(document: ImageDocument): Promise<void> {
    const encoded = await serializeCasDocument(document);
    const previous = this.#documents.get(document.id);
    const previousIds = new Set(previous?.resourceIds ?? []);
    const nextIds = new Set(encoded.resourceIds);
    let writtenResourceCount = 0;
    let writtenResourceBytes = 0;
    let removedResourceCount = 0;
    for (const id of encoded.resourceIds) {
      const existing = this.#resources.get(id);
      if (existing === undefined) {
        const bytes = encoded.resources.get(id);
        if (bytes === undefined) throw new PersistenceError('Encoded resource is missing');
        this.#resources.set(id, { id, bytes: new Uint8Array(bytes), documentIds: [document.id] });
        writtenResourceCount += 1;
        writtenResourceBytes += bytes.byteLength;
      } else if (!existing.documentIds.includes(document.id)) {
        this.#resources.set(id, {
          ...existing,
          documentIds: [...existing.documentIds, document.id].sort(),
        });
      }
    }
    for (const id of previousIds) {
      if (nextIds.has(id)) continue;
      const existing = this.#resources.get(id);
      if (existing === undefined) continue;
      const documentIds = existing.documentIds.filter((candidate) => candidate !== document.id);
      if (documentIds.length === 0) {
        this.#resources.delete(id);
        removedResourceCount += 1;
      } else {
        this.#resources.set(id, { ...existing, documentIds });
      }
    }
    this.#documents.set(document.id, {
      id: document.id,
      manifest: encoded.manifest,
      resourceIds: encoded.resourceIds,
    });
    this.#lastSaveStats = {
      metadataBytes: new TextEncoder().encode(encoded.manifest).byteLength,
      referencedResourceCount: encoded.resourceIds.length,
      writtenResourceCount,
      writtenResourceBytes,
      reusedResourceCount: encoded.resourceIds.length - writtenResourceCount,
      removedResourceCount,
    };
  }

  prepareLoad(id: string): Promise<PreparedDocumentLoad | undefined> {
    const record = this.#documents.get(id);
    if (record === undefined) return Promise.resolve(undefined);
    const metadataBytes = new TextEncoder().encode(record.manifest).byteLength;
    const metadata = inspectStoredDocument(record.manifest, record.resourceIds.length, false);
    if (metadata.id !== id) throw new PersistenceError('Stored document record is invalid');
    this.#lastLoadStats = deferredStats(metadataBytes, record.resourceIds.length);
    let hydration: Promise<ImageDocument> | undefined;
    return Promise.resolve({
      metadata,
      hydrate: () => {
        hydration ??= Promise.resolve().then(async () => {
          const resources = record.resourceIds.map((resourceId) => {
            const resource = this.#resources.get(resourceId);
            if (resource === undefined)
              throw new PersistenceError('Stored document resource is missing');
            return resource;
          });
          const document = await hydrateCasRecord(record, resources);
          this.#lastLoadStats = deferredStats(
            metadataBytes,
            record.resourceIds.length,
            resources,
            'hydrated',
          );
          return document;
        });
        return hydration;
      },
    });
  }

  async load(id: string): Promise<ImageDocument | undefined> {
    return (await this.prepareLoad(id))?.hydrate();
  }

  delete(id: string): Promise<boolean> {
    return Promise.resolve().then(() => {
      const record = this.#documents.get(id);
      if (record === undefined) return false;
      this.#documents.delete(id);
      for (const resourceId of record.resourceIds) {
        const resource = this.#resources.get(resourceId);
        if (resource === undefined) continue;
        const documentIds = resource.documentIds.filter((candidate) => candidate !== id);
        if (documentIds.length === 0) this.#resources.delete(resourceId);
        else this.#resources.set(resourceId, { ...resource, documentIds });
      }
      return true;
    });
  }

  list(): Promise<readonly string[]> {
    return Promise.resolve([...this.#documents.keys()].sort());
  }
}

export interface IndexedDbRepositoryOptions {
  readonly indexedDB?: IDBFactory;
  readonly databaseName?: string;
}

interface LegacyStoredDocument {
  readonly id: string;
  readonly serialized: string;
}

const STORE_NAME = 'documents';
const RESOURCE_STORE_NAME = 'resources';

export class IndexedDbDocumentRepository implements DocumentRepository {
  readonly #factory: IDBFactory;
  readonly #databaseName: string;
  #databasePromise: Promise<IDBDatabase> | undefined;
  #lastSaveStats: IncrementalSaveStats | null = null;
  #lastLoadStats: DeferredLoadStats | null = null;

  get lastSaveStats(): IncrementalSaveStats | null {
    return this.#lastSaveStats;
  }

  get lastLoadStats(): DeferredLoadStats | null {
    return this.#lastLoadStats;
  }

  constructor(options: IndexedDbRepositoryOptions = {}) {
    const factory = options.indexedDB ?? browserIndexedDB();
    if (factory === undefined) throw new PersistenceError('IndexedDB is not available');
    this.#factory = factory;
    this.#databaseName = options.databaseName ?? 'freetoshop';
  }

  #database(): Promise<IDBDatabase> {
    if (this.#databasePromise !== undefined) return this.#databasePromise;
    this.#databasePromise = new Promise((resolve, reject) => {
      const request = this.#factory.open(this.#databaseName, 2);
      request.onupgradeneeded = () => {
        if (!request.result.objectStoreNames.contains(STORE_NAME)) {
          request.result.createObjectStore(STORE_NAME, { keyPath: 'id' });
        }
        if (!request.result.objectStoreNames.contains(RESOURCE_STORE_NAME)) {
          request.result.createObjectStore(RESOURCE_STORE_NAME, { keyPath: 'id' });
        }
      };
      request.onsuccess = () => resolve(request.result);
      request.onerror = () =>
        reject(
          new PersistenceError('Could not open the document database', { cause: request.error }),
        );
      request.onblocked = () =>
        reject(new PersistenceError('Document database upgrade was blocked'));
    });
    return this.#databasePromise;
  }

  async #store(
    mode: IDBTransactionMode,
  ): Promise<{ store: IDBObjectStore; transaction: IDBTransaction }> {
    const transaction = (await this.#database()).transaction(STORE_NAME, mode);
    return { store: transaction.objectStore(STORE_NAME), transaction };
  }

  async save(document: ImageDocument): Promise<void> {
    const encoded = await serializeCasDocument(document);
    const transaction = (await this.#database()).transaction(
      [STORE_NAME, RESOURCE_STORE_NAME],
      'readwrite',
    );
    const documents = transaction.objectStore(STORE_NAME);
    const resources = transaction.objectStore(RESOURCE_STORE_NAME);
    const previousValue = await requestResult<unknown>(documents.get(document.id), 'read document');
    const previous =
      previousValue === undefined ||
      typeof (previousValue as { manifest?: unknown }).manifest !== 'string'
        ? undefined
        : (previousValue as StoredCasDocument);
    const previousIds = new Set(previous?.resourceIds ?? []);
    const nextIds = new Set(encoded.resourceIds);
    const resourceIds = [...new Set([...previousIds, ...nextIds])];
    const existing = await Promise.all(
      resourceIds.map((id) =>
        requestResult<StoredCasResource | undefined>(
          resources.get(id) as IDBRequest<StoredCasResource | undefined>,
          'read resource',
        ),
      ),
    );
    const byId = new Map(resourceIds.map((id, index) => [id, existing[index]] as const));
    let writtenResourceCount = 0;
    let writtenResourceBytes = 0;
    let removedResourceCount = 0;
    for (const id of encoded.resourceIds) {
      const current = byId.get(id);
      if (current === undefined) {
        const bytes = encoded.resources.get(id);
        if (bytes === undefined) throw new PersistenceError('Encoded resource is missing');
        resources.put({ id, bytes: new Uint8Array(bytes), documentIds: [document.id] });
        writtenResourceCount += 1;
        writtenResourceBytes += bytes.byteLength;
      } else if (!current.documentIds.includes(document.id)) {
        resources.put({
          ...current,
          documentIds: [...current.documentIds, document.id].sort(),
        });
      }
    }
    for (const id of previousIds) {
      if (nextIds.has(id)) continue;
      const current = byId.get(id);
      if (current === undefined) continue;
      const documentIds = current.documentIds.filter((candidate) => candidate !== document.id);
      if (documentIds.length === 0) {
        resources.delete(id);
        removedResourceCount += 1;
      } else {
        resources.put({ ...current, documentIds });
      }
    }
    documents.put({
      id: document.id,
      manifest: encoded.manifest,
      resourceIds: encoded.resourceIds,
    });
    await transactionDone(transaction, 'save document');
    this.#lastSaveStats = {
      metadataBytes: new TextEncoder().encode(encoded.manifest).byteLength,
      referencedResourceCount: encoded.resourceIds.length,
      writtenResourceCount,
      writtenResourceBytes,
      reusedResourceCount: encoded.resourceIds.length - writtenResourceCount,
      removedResourceCount,
    };
  }

  async prepareLoad(id: string): Promise<PreparedDocumentLoad | undefined> {
    const { store } = await this.#store('readonly');
    const stored: unknown = await requestResult(
      store.get(id) as IDBRequest<unknown>,
      'load document',
    );
    if (stored === undefined) return undefined;
    const record = object(stored, 'Stored document record');
    if (record.id !== id) {
      throw new PersistenceError('Stored document record is invalid');
    }
    if (typeof record.serialized === 'string') {
      const serialized = (record as unknown as LegacyStoredDocument).serialized;
      const metadataBytes = new TextEncoder().encode(serialized).byteLength;
      const metadata = inspectStoredDocument(serialized, 0, true);
      if (metadata.id !== id) throw new PersistenceError('Stored document record is invalid');
      this.#lastLoadStats = deferredStats(metadataBytes, 0);
      let hydration: Promise<ImageDocument> | undefined;
      return {
        metadata,
        hydrate: () => {
          hydration ??= Promise.resolve().then(() => {
            const document = deserializeDocument(serialized);
            this.#lastLoadStats = deferredStats(metadataBytes, 0, [], 'hydrated');
            return document;
          });
          return hydration;
        },
      };
    }
    if (typeof record.manifest !== 'string' || !Array.isArray(record.resourceIds)) {
      throw new PersistenceError('Stored document record is invalid');
    }
    const casRecord = record as unknown as StoredCasDocument;
    if (casRecord.resourceIds.some((resourceId) => typeof resourceId !== 'string')) {
      throw new PersistenceError('Stored document record is invalid');
    }
    const metadataBytes = new TextEncoder().encode(casRecord.manifest).byteLength;
    const metadata = inspectStoredDocument(casRecord.manifest, casRecord.resourceIds.length, false);
    if (metadata.id !== id) throw new PersistenceError('Stored document record is invalid');
    this.#lastLoadStats = deferredStats(metadataBytes, casRecord.resourceIds.length);
    let hydration: Promise<ImageDocument> | undefined;
    return {
      metadata,
      hydrate: () => {
        hydration ??= Promise.resolve().then(async () => {
          const transaction = (await this.#database()).transaction(RESOURCE_STORE_NAME, 'readonly');
          const resourceStore = transaction.objectStore(RESOURCE_STORE_NAME);
          const resources = await Promise.all(
            casRecord.resourceIds.map((resourceId) =>
              requestResult<StoredCasResource | undefined>(
                resourceStore.get(resourceId) as IDBRequest<StoredCasResource | undefined>,
                'load document resource',
              ).then((resource) => {
                if (resource === undefined)
                  throw new PersistenceError('Stored document resource is missing');
                return resource;
              }),
            ),
          );
          const document = await hydrateCasRecord(casRecord, resources);
          this.#lastLoadStats = deferredStats(
            metadataBytes,
            casRecord.resourceIds.length,
            resources,
            'hydrated',
          );
          return document;
        });
        return hydration;
      },
    };
  }

  async load(id: string): Promise<ImageDocument | undefined> {
    return (await this.prepareLoad(id))?.hydrate();
  }

  async delete(id: string): Promise<boolean> {
    const transaction = (await this.#database()).transaction(
      [STORE_NAME, RESOURCE_STORE_NAME],
      'readwrite',
    );
    const store = transaction.objectStore(STORE_NAME);
    const resources = transaction.objectStore(RESOURCE_STORE_NAME);
    const stored = await requestResult<unknown>(store.get(id), 'find document');
    const existed = stored !== undefined;
    if (stored !== undefined) {
      const value = object(stored, 'Stored document record');
      if (Array.isArray(value.resourceIds)) {
        const resourceIds = value.resourceIds.filter(
          (candidate): candidate is string => typeof candidate === 'string',
        );
        const records = await Promise.all(
          resourceIds.map((resourceId) =>
            requestResult<StoredCasResource | undefined>(
              resources.get(resourceId) as IDBRequest<StoredCasResource | undefined>,
              'read resource',
            ),
          ),
        );
        records.forEach((resource) => {
          if (resource === undefined) return;
          const documentIds = resource.documentIds.filter((candidate) => candidate !== id);
          if (documentIds.length === 0) resources.delete(resource.id);
          else resources.put({ ...resource, documentIds });
        });
      }
      store.delete(id);
    }
    await transactionDone(transaction, 'delete document');
    return existed;
  }

  async list(): Promise<readonly string[]> {
    const { store } = await this.#store('readonly');
    const keys = await requestResult<IDBValidKey[]>(store.getAllKeys(), 'list documents');
    return keys.filter((key): key is string => typeof key === 'string').sort();
  }
}

function requestResult<T>(request: IDBRequest<T>, operation: string): Promise<T> {
  return new Promise((resolve, reject) => {
    request.onsuccess = () => resolve(request.result);
    request.onerror = () =>
      reject(new PersistenceError(`Could not ${operation}`, { cause: request.error }));
  });
}

function transactionDone(transaction: IDBTransaction, operation: string): Promise<void> {
  return new Promise((resolve, reject) => {
    transaction.oncomplete = () => resolve();
    transaction.onerror = () =>
      reject(new PersistenceError(`Could not ${operation}`, { cause: transaction.error }));
    transaction.onabort = () =>
      reject(new PersistenceError(`Could not ${operation}`, { cause: transaction.error }));
  });
}

export function createDocumentRepository(
  options: IndexedDbRepositoryOptions = {},
): DocumentRepository {
  const factory = options.indexedDB ?? browserIndexedDB();
  return factory === undefined
    ? new InMemoryDocumentRepository()
    : new IndexedDbDocumentRepository({ ...options, indexedDB: factory });
}

function browserIndexedDB(): IDBFactory | undefined {
  return (globalThis as { indexedDB?: IDBFactory }).indexedDB;
}
