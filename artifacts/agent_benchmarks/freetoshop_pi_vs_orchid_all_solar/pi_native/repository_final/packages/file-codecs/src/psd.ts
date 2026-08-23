import {
  BLEND_MODES,
  createAdjustmentLayer,
  createAlphaChannel,
  createDocument,
  createDocumentGuide,
  createDocumentPath,
  createGroupLayer,
  createPixelLayer,
  createRasterMask,
  createSmartObjectLayer,
  createTextLayer,
  createVectorMask,
  type Adjustment,
  type AlphaChannel,
  type BlendMode,
  type DocumentGrid,
  type DocumentGuide,
  type DocumentPath,
  type ImageDocument,
  type Layer,
  type LayerEffect,
  type PathCommand,
  type RasterMask,
  type RgbaColor,
  type TextAlign,
  type VectorMask,
} from '@freetoshop/core-document';
import {
  initializeCanvas,
  readPsd,
  type AdjustmentLayer as PsdAdjustment,
  type BezierPath,
  type Color,
  type Layer as PsdLayer,
  type LayerEffectsInfo,
  type LayerMaskData,
  type LayerTextData,
  type LayerVectorMask,
  type LinkedFile,
  type Psd,
  type UnitsValue,
} from 'ag-psd';
import {
  hasPsdSignature,
  PSD_MAX_DECODED_BYTES,
  PSD_MAX_DIMENSION,
  PSD_MAX_PIXELS,
} from './signatures.js';
import { decodePsdAlphaPlanes } from './psdAlphaPlanes.js';
import { decodePsdPathResources } from './psdPathResources.js';

/** Max linked-file payload bytes stored as base64 in the preservation bag. */
const MAX_PRESERVED_LINKED_FILE_BYTES = 4 * 1024 * 1024;

/**
 * Optional FreetoShop test/extension fields attached to a synthetic `Psd` parse
 * result. Real `readPsd` output does not include these; inject them from unit
 * tests when exercising multi-channel coverage that ag-psd does not expose.
 */
export interface FreetoshopPsdExtensions {
  /**
   * Named alpha-channel coverage in document space (one byte per pixel).
   * When provided, these take priority over composite-imageData extraction.
   */
  readonly freetoshopAlphaChannels?: readonly {
    readonly name?: string;
    readonly coverage: ArrayLike<number>;
  }[];
  /**
   * Document-level work-path / saved-path descriptors for preservation until a
   * native document-path model is available.
   */
  readonly freetoshopDocumentPaths?: readonly {
    readonly name?: string;
    readonly open?: boolean;
    readonly knots?: readonly {
      readonly linked?: boolean;
      readonly points: readonly number[];
    }[];
  }[];
}

export interface PsdImportReport {
  readonly layerCount: number;
  readonly rasterizedStructuredLayers: number;
  readonly skippedLayers: number;
  /** Raster layer masks successfully converted into native coverage stores. */
  readonly importedRasterMasks: number;
  /** Vector layer masks successfully converted into native path commands. */
  readonly importedVectorMasks: number;
  /** Supported layer effects (drop-shadow / stroke / color-overlay) imported. */
  readonly importedEffects: number;
  /** Adjustment layers imported as native adjustment layers. */
  readonly importedAdjustmentLayers: number;
  /** Editable text layers imported from PSD text records. */
  readonly importedTextLayers: number;
  /** Smart objects imported from placedLayer + raster preview and/or linked PSD. */
  readonly importedSmartObjects: number;
  /** Smart objects whose source was built from a nested linked PSD payload. */
  readonly importedNestedSmartObjectSources: number;
  /** Linked-file records captured into document.preservation. */
  readonly preservedLinkedFiles: number;
  /** Saved alpha channels imported into document.channels. */
  readonly importedAlphaChannels: number;
  /** Alpha channels that received real coverage (not empty placeholders). */
  readonly importedAlphaChannelsWithCoverage: number;
  /** Document path descriptors captured into preservation (not yet native paths). */
  readonly preservedDocumentPaths: number;
  /** Ruler guides imported into document.guides. */
  readonly importedGuides: number;
  /** Whether a document grid was imported. */
  readonly importedGrid: boolean;
  /** Document-level paths imported into document.paths. */
  readonly importedDocumentPaths: number;
  readonly warnings: readonly string[];
}

export interface PsdImportResult {
  readonly document: ImageDocument;
  readonly report: PsdImportReport;
}

export type PsdImportPhase = 'parse' | 'layers' | 'resources' | 'complete';

export interface PsdImportProgress {
  readonly phase: PsdImportPhase;
  readonly completed: number;
  readonly total: number;
  /** Monotonic overall completion in the inclusive range 0..1. */
  readonly progress: number;
}

export interface PsdImportOptions {
  readonly documentId?: string;
  readonly name?: string;
  readonly parse?: (buffer: ArrayBuffer) => Psd;
  readonly signal?: AbortSignal;
  readonly onProgress?: (update: PsdImportProgress) => void;
  /**
   * Nesting depth for recursive linked-PSD smart-object imports (0 = top-level).
   * Caps runaway recursion when linked files embed further PSDs.
   */
  readonly nestedDepth?: number;
}

/** Maximum recursive depth for linked PSD → smart-object source imports. */
const MAX_NESTED_SMART_OBJECT_DEPTH = 2;

export function importPsdBuffer(
  buffer: ArrayBuffer,
  options: PsdImportOptions = {},
): PsdImportResult {
  reportPsdImportProgress(options, {
    phase: 'parse',
    completed: 0,
    total: 1,
    progress: 0,
  });
  const header = inspectPsdHeader(buffer);
  const psd = (options.parse ?? defaultParse)(buffer);
  reportPsdImportProgress(options, {
    phase: 'parse',
    completed: 1,
    total: 1,
    progress: 0.35,
  });
  if (psd.width !== header.width || psd.height !== header.height) {
    throw new Error('PSD dimensions changed while decoding.');
  }

  let nextLayerId = 0;
  let layerCount = 0;
  let rasterizedStructuredLayers = 0;
  let skippedLayers = 0;
  let importedRasterMasks = 0;
  let importedVectorMasks = 0;
  let importedEffects = 0;
  let importedAdjustmentLayers = 0;
  let importedTextLayers = 0;
  let importedSmartObjects = 0;
  let importedNestedSmartObjectSources = 0;
  const nestedDepth = options.nestedDepth ?? 0;
  const warnings = new Set<string>();
  const preservedLayerRecords: Array<Record<string, unknown>> = [];
  const makeLayerId = () => `psd-layer-${String(++nextLayerId)}`;
  const totalSourceLayers = countPsdLayers(psd.children ?? []);
  let convertedSourceLayers = 0;
  const completeSourceLayer = () => {
    convertedSourceLayers += 1;
    reportPsdImportProgress(options, {
      phase: 'layers',
      completed: convertedSourceLayers,
      total: Math.max(1, totalSourceLayers),
      progress: 0.35 + 0.4 * (convertedSourceLayers / Math.max(1, totalSourceLayers)),
    });
  };

  const convertLayers = (source: readonly PsdLayer[]): Layer[] => {
    const converted: Layer[] = [];
    // ag-psd exposes Photoshop's top-to-bottom order; FreetoShop composites bottom-to-top.
    for (const layer of [...source].reverse()) {
      throwIfPsdImportAborted(options.signal);
      const common = {
        id: makeLayerId(),
        name: layer.name?.trim() || `PSD Layer ${String(nextLayerId)}`,
        visible: !layer.hidden,
        opacity: clampUnit(layer.opacity ?? 1),
        fillOpacity: 1,
        blendMode: mapBlendMode(layer.blendMode, warnings),
        clipping: Boolean(layer.clipping),
        locks: { transparency: Boolean(layer.transparencyProtected) },
      } as const;

      if (layer.children) {
        const children = convertLayers(layer.children);
        converted.push(
          createGroupLayer({
            ...common,
            children,
            compositing: layer.blendMode === 'pass through' ? 'pass-through' : 'isolated',
          }),
        );
        layerCount += 1;
        completeSourceLayer();
        continue;
      }

      if (layer.adjustment) {
        const adjustment = convertPsdAdjustment(layer.adjustment, warnings);
        if (adjustment) {
          converted.push(
            createAdjustmentLayer({
              ...common,
              adjustment,
            }),
          );
          layerCount += 1;
          importedAdjustmentLayers += 1;
          completeSourceLayer();
          continue;
        }
        skippedLayers += 1;
        preservedLayerRecords.push({
          kind: 'adjustment',
          name: common.name,
          adjustmentType: 'type' in layer.adjustment ? String(layer.adjustment.type) : 'unknown',
        });
        warnings.add(
          'An unsupported PSD adjustment layer was preserved but not imported as editable.',
        );
        completeSourceLayer();
        continue;
      }

      if (layer.text) {
        const textLayer = convertPsdTextLayer(layer, common);
        if (textLayer) {
          converted.push(textLayer);
          layerCount += 1;
          importedTextLayers += 1;
          if (layer.imageData) {
            warnings.add(
              'PSD text layers were imported as editable text; raster previews were not used as pixel content.',
            );
          }
          completeSourceLayer();
          continue;
        }
      }

      if (layer.placedLayer) {
        const smart = convertPsdSmartObject(
          layer,
          common,
          psd.linkedFiles,
          nestedDepth,
          warnings,
          options.signal,
        );
        if (smart) {
          converted.push(smart.layer);
          layerCount += 1;
          importedSmartObjects += 1;
          if (smart.fromNestedPsd) importedNestedSmartObjectSources += 1;
          completeSourceLayer();
          continue;
        }
        skippedLayers += 1;
        preservedLayerRecords.push({
          kind: 'smart-object',
          name: common.name,
          placedId: layer.placedLayer.id,
          placedType: layer.placedLayer.type,
          left: layer.left ?? 0,
          top: layer.top ?? 0,
        });
        warnings.add(
          'A PSD smart object without a usable nested source or raster preview was preserved but not imported as editable.',
        );
        completeSourceLayer();
        continue;
      }

      const image = layer.imageData;
      if (!image || image.width < 1 || image.height < 1) {
        skippedLayers += 1;
        warnings.add('Layers without a raster preview were skipped.');
        preservedLayerRecords.push({
          kind: layer.vectorFill || layer.vectorStroke ? 'vector' : 'empty',
          name: common.name,
          left: layer.left ?? 0,
          top: layer.top ?? 0,
          hints: [
            layer.vectorFill ? 'vectorFill' : null,
            layer.vectorStroke ? 'vectorStroke' : null,
            layer.effects ? 'effects' : null,
            layer.mask ? 'mask' : null,
            layer.vectorMask ? 'vectorMask' : null,
          ].filter((value): value is string => value !== null),
        });
        completeSourceLayer();
        continue;
      }
      if (!(image.data instanceof Uint8Array || image.data instanceof Uint8ClampedArray)) {
        throw new Error('Only 8-bit PSD layer pixels are supported.');
      }
      if (layer.vectorFill || layer.vectorStroke) {
        rasterizedStructuredLayers += 1;
        warnings.add('Vector previews were imported as pixel layers.');
      }
      const layerLeft = layer.left ?? 0;
      const layerTop = layer.top ?? 0;
      let rasterMask: RasterMask | null = null;
      if (layer.mask) {
        const convertedMask = convertPsdRasterMask(
          layer.mask,
          image.width,
          image.height,
          layerLeft,
          layerTop,
          warnings,
        );
        if (convertedMask) {
          rasterMask = convertedMask;
          importedRasterMasks += 1;
        } else if (layer.mask.imageData || layer.mask.canvas) {
          warnings.add('A PSD layer mask could not be converted and was skipped.');
        } else {
          warnings.add('PSD layer masks without pixel data were skipped.');
        }
      }

      let vectorMask: VectorMask | null = null;
      if (layer.vectorMask) {
        const convertedVector = convertPsdVectorMask(
          layer.vectorMask,
          image.width,
          image.height,
          layerLeft,
          layerTop,
          warnings,
        );
        if (convertedVector) {
          vectorMask = convertedVector;
          importedVectorMasks += 1;
        } else {
          warnings.add('A PSD vector mask could not be converted and was skipped.');
        }
      }

      const effects = convertPsdEffects(layer.effects, warnings);
      importedEffects += effects.length;

      converted.push(
        createPixelLayer({
          ...common,
          width: image.width,
          height: image.height,
          pixels: image.data,
          transform: {
            a: 1,
            b: 0,
            c: 0,
            d: 1,
            e: layerLeft,
            f: layerTop,
          },
          rasterMask,
          vectorMask,
          effects,
        }),
      );
      layerCount += 1;
      completeSourceLayer();
    }
    return converted;
  };

  let layers = convertLayers(psd.children ?? []);
  if (layers.length === 0 && psd.imageData) {
    if (
      !(psd.imageData.data instanceof Uint8Array || psd.imageData.data instanceof Uint8ClampedArray)
    ) {
      throw new Error('Only 8-bit PSD composite pixels are supported.');
    }
    layers = [
      createPixelLayer({
        id: makeLayerId(),
        name: 'PSD composite',
        width: psd.imageData.width,
        height: psd.imageData.height,
        pixels: psd.imageData.data,
      }),
    ];
    layerCount = 1;
    warnings.add('The PSD had no importable layer previews; its composite preview was used.');
  }
  if (layers.length === 0) throw new Error('The PSD contains no decodable pixels.');

  if (totalSourceLayers === 0) {
    reportPsdImportProgress(options, {
      phase: 'layers',
      completed: 1,
      total: 1,
      progress: 0.75,
    });
  }

  const linkedFileRecords = preserveLinkedFiles(psd.linkedFiles, warnings);
  reportPsdResourceProgress(options, 1);
  const alphaImport = importAlphaChannels(psd, buffer, warnings);
  reportPsdResourceProgress(options, 2);
  const pathImport = importDocumentPaths(psd, buffer, warnings);
  reportPsdResourceProgress(options, 3);
  const resourcePreservation = preserveImageResources(psd);
  reportPsdResourceProgress(options, 4);
  const guideImport = importDocumentGuides(psd);
  reportPsdResourceProgress(options, 5);

  const preservation: Record<string, unknown> = {
    kind: 'psd-import-v1',
    header: {
      width: header.width,
      height: header.height,
    },
    skippedLayerRecords: preservedLayerRecords,
    linkedFiles: linkedFileRecords,
    alphaChannels: alphaImport.preservationRecords,
    documentPaths: pathImport.preservationRecords,
    pathSelectionState: pathImport.pathSelectionState,
    imageResources: resourcePreservation,
    rasterizedStructuredLayers,
    importedRasterMasks,
    importedVectorMasks,
    importedEffects,
    importedAdjustmentLayers,
    importedTextLayers,
    importedSmartObjects,
    importedNestedSmartObjectSources,
    importedAlphaChannels: alphaImport.channels.length,
    importedAlphaChannelsWithCoverage: alphaImport.withCoverage,
    preservedDocumentPaths: pathImport.preservationRecords.length,
    importedDocumentPaths: pathImport.paths.length,
    importedGuides: guideImport.guides.length,
    importedGrid: guideImport.grid !== null,
    warnings: [...warnings],
  };

  const result = {
    document: createDocument({
      id: options.documentId ?? makeId('psd-document'),
      name: options.name?.trim() || 'Imported PSD',
      width: psd.width,
      height: psd.height,
      layers,
      channels: alphaImport.channels,
      guides: guideImport.guides,
      grid: guideImport.grid,
      paths: pathImport.paths,
      preservation,
    }),
    report: {
      layerCount,
      rasterizedStructuredLayers,
      skippedLayers,
      importedRasterMasks,
      importedVectorMasks,
      importedEffects,
      importedAdjustmentLayers,
      importedTextLayers,
      importedSmartObjects,
      importedNestedSmartObjectSources,
      preservedLinkedFiles: linkedFileRecords.length,
      importedAlphaChannels: alphaImport.channels.length,
      importedAlphaChannelsWithCoverage: alphaImport.withCoverage,
      preservedDocumentPaths: pathImport.preservationRecords.length,
      importedDocumentPaths: pathImport.paths.length,
      importedGuides: guideImport.guides.length,
      importedGrid: guideImport.grid !== null,
      warnings: [...warnings],
    },
  };
  reportPsdImportProgress(options, {
    phase: 'complete',
    completed: 1,
    total: 1,
    progress: 1,
  });
  return result;
}

function reportPsdResourceProgress(options: PsdImportOptions, completed: number): void {
  reportPsdImportProgress(options, {
    phase: 'resources',
    completed,
    total: 5,
    progress: 0.75 + 0.04 * completed,
  });
}

function reportPsdImportProgress(options: PsdImportOptions, update: PsdImportProgress): void {
  throwIfPsdImportAborted(options.signal);
  options.onProgress?.(update);
  throwIfPsdImportAborted(options.signal);
}

function throwIfPsdImportAborted(signal: AbortSignal | undefined): void {
  if (!signal?.aborted) return;
  const error = new Error('PSD import was cancelled.');
  error.name = 'AbortError';
  throw error;
}

function countPsdLayers(layers: readonly PsdLayer[]): number {
  let count = 0;
  for (const layer of layers) {
    count += 1;
    if (layer.children) count += countPsdLayers(layer.children);
  }
  return count;
}

/**
 * Maps PSD gridAndGuidesInformation into first-class document guides/grid.
 * Guide locations are taken as document-pixel coordinates as exposed by ag-psd.
 */
function importDocumentGuides(psd: Psd): {
  readonly guides: DocumentGuide[];
  readonly grid: DocumentGrid | null;
} {
  const info = psd.imageResources?.gridAndGuidesInformation;
  if (!info) return { guides: [], grid: null };
  const guides: DocumentGuide[] = [];
  const sourceGuides = info.guides ?? [];
  for (let index = 0; index < sourceGuides.length; index += 1) {
    const guide = sourceGuides[index];
    if (!Number.isFinite(guide.location)) continue;
    guides.push(
      createDocumentGuide({
        id: `psd-guide-${String(index + 1)}`,
        direction: guide.direction,
        position: guide.location,
      }),
    );
  }
  let grid: DocumentGrid | null = null;
  if (
    info.grid &&
    Number.isFinite(info.grid.horizontal) &&
    Number.isFinite(info.grid.vertical) &&
    info.grid.horizontal > 0 &&
    info.grid.vertical > 0
  ) {
    grid = { horizontal: info.grid.horizontal, vertical: info.grid.vertical };
  }
  return { guides, grid };
}

/**
 * Imports saved PSD alpha channels into sparse document channels.
 *
 * Coverage sources (in priority order):
 * 1. `freetoshopAlphaChannels` injects on synthetic parse results (tests)
 * 2. Multi-plane composite decoder (`decodePsdAlphaPlanes`) for 8-bit RGB raw/RLE
 * 3. Single composite `imageData` alpha plane (ag-psd RGBA) for the first name
 * 4. Empty coverage placeholder (name preserved) when pixel data is unavailable
 */
function importAlphaChannels(
  psd: Psd & FreetoshopPsdExtensions,
  buffer: ArrayBuffer,
  warnings: Set<string>,
): {
  readonly channels: AlphaChannel[];
  readonly withCoverage: number;
  readonly preservationRecords: Array<Record<string, unknown>>;
} {
  const namesFromResources = psd.imageResources?.alphaChannelNames ?? [];
  const injected = psd.freetoshopAlphaChannels ?? [];
  const expectedPixels = psd.width * psd.height;

  // Multi-plane decode from file bytes (independent of ag-psd composite RGBA).
  const decodedPlanes = injected.length === 0 ? decodePsdAlphaPlanes(buffer) : null;
  const multiPlanes =
    decodedPlanes && decodedPlanes.width === psd.width && decodedPlanes.height === psd.height
      ? decodedPlanes.planes
      : null;

  // Build ordered names: prefer resource names; fall back to inject / plane count.
  const names: string[] = [];
  if (namesFromResources.length > 0) {
    for (const name of namesFromResources) {
      const trimmed = name.trim();
      if (trimmed) names.push(trimmed);
    }
  } else {
    for (let index = 0; index < injected.length; index += 1) {
      const trimmed = injected[index]?.name?.trim();
      names.push(trimmed || `Alpha ${String(index + 1)}`);
    }
  }

  // When multi-plane decoder recovered more planes than named resources, invent names.
  if (multiPlanes && multiPlanes.length > names.length && injected.length === 0) {
    for (let index = names.length; index < multiPlanes.length; index += 1) {
      names.push(`Alpha ${String(index + 1)}`);
    }
  }

  if (names.length === 0 && injected.length === 0 && !multiPlanes) {
    return { channels: [], withCoverage: 0, preservationRecords: [] };
  }

  // If resources listed names but inject has more, extend names from inject.
  if (injected.length > names.length) {
    for (let index = names.length; index < injected.length; index += 1) {
      const trimmed = injected[index]?.name?.trim();
      names.push(trimmed || `Alpha ${String(index + 1)}`);
    }
  }

  // Fallback single A plane from ag-psd composite when multi-plane path is empty.
  const compositeCoverage =
    injected.length === 0 && (!multiPlanes || multiPlanes.length === 0)
      ? extractCompositeAlphaCoverage(psd, warnings)
      : null;

  const channels: AlphaChannel[] = [];
  const preservationRecords: Array<Record<string, unknown>> = [];
  let withCoverage = 0;

  for (let index = 0; index < names.length; index += 1) {
    const name = names[index] ?? `Alpha ${String(index + 1)}`;
    const id = `psd-alpha-${String(index + 1)}`;
    let coverage: Uint8ClampedArray | null = null;
    let coverageSource: 'inject' | 'multi-plane' | 'composite-alpha' | 'empty' = 'empty';

    const inject = injected.at(index);
    if (inject !== undefined) {
      if (inject.coverage.length !== expectedPixels) {
        warnings.add(
          `Alpha channel "${name}" inject coverage length ${String(inject.coverage.length)} did not match document size ${String(expectedPixels)}; channel was imported empty.`,
        );
      } else {
        coverage = new Uint8ClampedArray(inject.coverage);
        coverageSource = 'inject';
      }
    } else if (multiPlanes && multiPlanes[index] && multiPlanes[index].length === expectedPixels) {
      coverage = multiPlanes[index];
      coverageSource = 'multi-plane';
    } else if (compositeCoverage !== null && index === 0) {
      coverage = compositeCoverage;
      coverageSource = 'composite-alpha';
    }

    if (coverage === null) {
      coverage = new Uint8ClampedArray(expectedPixels);
      if (coverageSource === 'empty') {
        warnings.add(
          `Alpha channel "${name}" was imported without pixel coverage; the name was preserved for editing.`,
        );
      }
    } else {
      withCoverage += 1;
    }

    channels.push(
      createAlphaChannel({
        id,
        name,
        width: psd.width,
        height: psd.height,
        coverage,
      }),
    );
    preservationRecords.push({
      id,
      name,
      index,
      coverageSource,
      hasCoverage: coverageSource !== 'empty',
      pixelCount: expectedPixels,
      multiPlaneCompression:
        coverageSource === 'multi-plane' ? decodedPlanes?.compression : undefined,
    });
  }

  if (names.length > 1 && injected.length === 0 && withCoverage < names.length) {
    warnings.add(
      `PSD listed ${String(names.length)} alpha channel names but only ${String(withCoverage)} plane(s) could be recovered. Unsupported compression or bit depth may apply.`,
    );
  }

  return { channels, withCoverage, preservationRecords };
}

/**
 * Extracts the composite imageData alpha plane as greyscale coverage when the
 * decoder reported at least four document channels. This is a best-effort path
 * for single saved alphas; multi-channel alphas need injects or a future decoder.
 */
function extractCompositeAlphaCoverage(psd: Psd, warnings: Set<string>): Uint8ClampedArray | null {
  const image = psd.imageData;
  if (!image || image.width !== psd.width || image.height !== psd.height) return null;
  const data = image.data;
  if (!(data instanceof Uint8Array || data instanceof Uint8ClampedArray)) return null;
  if (data.length !== image.width * image.height * 4) return null;
  if (!psd.channels || psd.channels < 4) {
    warnings.add(
      'Alpha channel names were present but the composite decoder did not expose an alpha plane; channels were imported empty.',
    );
    return null;
  }
  const coverage = new Uint8ClampedArray(image.width * image.height);
  for (let i = 0, p = 0; i < coverage.length; i += 1, p += 4) {
    coverage[i] = data[p + 3] ?? 0;
  }
  return coverage;
}

/**
 * Imports document-level paths from:
 * 1. FreetoShop injects (tests / synthetic parse results)
 * 2. PSD image resource path blocks (IDs 2000–2998) decoded from the file bytes
 * 3. pathSelectionState metadata when geometry is unavailable
 */
function importDocumentPaths(
  psd: Psd & FreetoshopPsdExtensions,
  buffer: ArrayBuffer,
  warnings: Set<string>,
): {
  readonly paths: DocumentPath[];
  readonly preservationRecords: Array<Record<string, unknown>>;
  readonly pathSelectionState: string[] | null;
} {
  const pathSelectionState = psd.imageResources?.pathSelectionState
    ? [...psd.imageResources.pathSelectionState]
    : null;
  const injected = psd.freetoshopDocumentPaths ?? [];
  const paths: DocumentPath[] = [];
  const preservationRecords: Array<Record<string, unknown>> = [];

  for (let index = 0; index < injected.length; index += 1) {
    const path = injected[index];
    const name = (path.name ?? '').trim() || `Path ${String(index + 1)}`;
    const open = path.open === true;
    const knots = path.knots ?? [];
    // Convert inject knots (ag-psd Bezier knot layout) into PathCommands in
    // document space (layer origin 0,0).
    const bezierLike = {
      open,
      knots: knots.map((knot) => ({
        linked: knot.linked !== false,
        points: [...knot.points],
      })),
    } as BezierPath;
    const commands = bezierPathToCommands(bezierLike, 0, 0);
    const id = `psd-path-${String(index + 1)}`;
    if (commands.length > 0) {
      paths.push(
        createDocumentPath({
          id,
          name,
          open,
          commands,
        }),
      );
    } else {
      warnings.add(
        `Document path "${name}" had no convertible knots and was preserved as metadata only.`,
      );
    }
    preservationRecords.push({
      index,
      id,
      name,
      open,
      source: 'inject',
      knotCount: knots.length,
      commandCount: commands.length,
      knots: knots.map((knot) => ({
        linked: knot.linked !== false,
        points: [...knot.points],
      })),
    });
  }

  // Decode path resources from the original PSD bytes when injects did not
  // already supply paths (avoid double-import of synthetic tests that inject).
  if (injected.length === 0) {
    try {
      const decoded = decodePsdPathResources(buffer, psd.width, psd.height);
      for (let index = 0; index < decoded.length; index += 1) {
        const resource = decoded[index];
        if (resource.commands.length === 0) continue;
        const id = `psd-path-res-${String(resource.resourceId)}`;
        paths.push(
          createDocumentPath({
            id,
            name: resource.name,
            open: resource.open,
            commands: resource.commands,
          }),
        );
        preservationRecords.push({
          index,
          id,
          name: resource.name,
          open: resource.open,
          source: 'resource',
          resourceId: resource.resourceId,
          knotCount: resource.knotCount,
          commandCount: resource.commands.length,
        });
      }
    } catch {
      warnings.add(
        'PSD path resources could not be decoded; path selection state may still be preserved.',
      );
    }
  }

  if (paths.length > 0) {
    warnings.add(
      'Document-level PSD paths were imported into native document.paths; pen/direct-selection editing UI is not complete.',
    );
  } else if (pathSelectionState && pathSelectionState.length > 0) {
    warnings.add(
      'PSD path selection state was preserved; document path geometry was not present in path resources.',
    );
  }
  return { paths, preservationRecords, pathSelectionState };
}

/**
 * Captures JSON-safe image-resource metadata used by professional workflows
 * (resolution, guides/grid, global light, XMP, pixel aspect). Does not claim
 * active color management or live guide editing — preservation only.
 */
function preserveImageResources(psd: Psd): Record<string, unknown> | null {
  const resources = psd.imageResources;
  if (!resources) return null;
  const bag: Record<string, unknown> = {};

  if (resources.resolutionInfo) {
    bag.resolutionInfo = { ...resources.resolutionInfo };
  }
  if (resources.gridAndGuidesInformation) {
    const grid = resources.gridAndGuidesInformation.grid;
    const guides = resources.gridAndGuidesInformation.guides;
    bag.gridAndGuides = {
      grid: grid ? { ...grid } : null,
      guides: guides
        ? guides.map((guide) => ({
            location: guide.location,
            direction: guide.direction,
          }))
        : [],
    };
  }
  if (typeof resources.globalAngle === 'number') {
    bag.globalAngle = resources.globalAngle;
  }
  if (typeof resources.globalAltitude === 'number') {
    bag.globalAltitude = resources.globalAltitude;
  }
  if (resources.pixelAspectRatio && typeof resources.pixelAspectRatio.aspect === 'number') {
    bag.pixelAspectRatio = resources.pixelAspectRatio.aspect;
  }
  if (typeof resources.xmpMetadata === 'string' && resources.xmpMetadata.length > 0) {
    // Cap XMP size to keep preservation bags bounded.
    const maxXmp = 256 * 1024;
    bag.xmpMetadata =
      resources.xmpMetadata.length <= maxXmp
        ? resources.xmpMetadata
        : resources.xmpMetadata.slice(0, maxXmp);
    bag.xmpTruncated = resources.xmpMetadata.length > maxXmp;
  }
  if (typeof resources.iccUntaggedProfile === 'boolean') {
    bag.iccUntaggedProfile = resources.iccUntaggedProfile;
  }
  if (resources.captionDigest) {
    bag.captionDigest = resources.captionDigest;
  }

  return Object.keys(bag).length > 0 ? bag : null;
}

/**
 * Captures PSD linkedFiles into JSON-serializable preservation records.
 * Small payloads are base64-encoded; large ones keep metadata only.
 */
function preserveLinkedFiles(
  files: readonly LinkedFile[] | undefined,
  warnings: Set<string>,
): Array<Record<string, unknown>> {
  if (!files || files.length === 0) return [];
  const records: Array<Record<string, unknown>> = [];
  for (const file of files) {
    const data = file.data;
    const byteLength = data?.byteLength ?? 0;
    let dataBase64: string | null = null;
    let dataOmitted = false;
    if (data && byteLength > 0) {
      if (byteLength <= MAX_PRESERVED_LINKED_FILE_BYTES) {
        dataBase64 = encodeBase64(data);
      } else {
        dataOmitted = true;
        warnings.add(
          `Linked file "${file.name}" exceeded the ${String(MAX_PRESERVED_LINKED_FILE_BYTES)}-byte preservation limit; metadata was kept without payload bytes.`,
        );
      }
    }
    records.push({
      id: file.id,
      name: file.name,
      type: file.type ?? null,
      creator: file.creator ?? null,
      time: file.time ?? null,
      childDocumentID: file.childDocumentID ?? null,
      assetModTime: file.assetModTime ?? null,
      dataByteLength: byteLength,
      dataBase64,
      dataOmitted,
      linkedFilePath: file.linkedFile
        ? {
            name: file.linkedFile.name,
            fullPath: file.linkedFile.fullPath,
            originalPath: file.linkedFile.originalPath,
            relativePath: file.linkedFile.relativePath,
            fileSize: file.linkedFile.fileSize,
          }
        : null,
    });
  }
  return records;
}

function encodeBase64(bytes: Uint8Array): string {
  const alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/';
  let result = '';
  const length = bytes.length;
  for (let index = 0; index < length; index += 3) {
    const remaining = length - index;
    const first = bytes.at(index) ?? 0;
    const second = remaining > 1 ? (bytes.at(index + 1) ?? 0) : 0;
    const third = remaining > 2 ? (bytes.at(index + 2) ?? 0) : 0;
    const packed = (first << 16) | (second << 8) | third;
    result += alphabet.charAt((packed >>> 18) & 63);
    result += alphabet.charAt((packed >>> 12) & 63);
    result += remaining > 1 ? alphabet.charAt((packed >>> 6) & 63) : '=';
    result += remaining > 2 ? alphabet.charAt(packed & 63) : '=';
  }
  return result;
}

/**
 * Imports a PSD placedLayer as an embedded smart object.
 *
 * Source priority:
 * 1. Linked-file PSD payload (recursive import up to MAX_NESTED_SMART_OBJECT_DEPTH)
 * 2. Raster preview imageData as a single pixel layer
 *
 * Linked-file byte payloads are also preserved in the document preservation bag.
 */
function convertPsdSmartObject(
  layer: PsdLayer,
  common: {
    readonly id: string;
    readonly name: string;
    readonly visible: boolean;
    readonly opacity: number;
    readonly fillOpacity: number;
    readonly blendMode: BlendMode;
    readonly clipping: boolean;
    readonly locks: { readonly transparency: boolean };
  },
  linkedFiles: readonly LinkedFile[] | undefined,
  nestedDepth: number,
  warnings: Set<string>,
  signal?: AbortSignal,
): { readonly layer: Layer; readonly fromNestedPsd: boolean } | null {
  const placed = layer.placedLayer;
  if (!placed) return null;

  const sourceId = placed.id.trim() || `so-source-${common.id}`;
  const layerLeft = layer.left ?? 0;
  const layerTop = layer.top ?? 0;

  // Transform from placed layer: 6-element affine when present.
  let transform = {
    a: 1,
    b: 0,
    c: 0,
    d: 1,
    e: layerLeft,
    f: layerTop,
  };
  if (Array.isArray(placed.transform) && placed.transform.length >= 6) {
    const a = placed.transform[0];
    const b = placed.transform[1];
    const c = placed.transform[2];
    const d = placed.transform[3];
    const e = placed.transform[4];
    const f = placed.transform[5];
    if (
      typeof a === 'number' &&
      typeof b === 'number' &&
      typeof c === 'number' &&
      typeof d === 'number' &&
      typeof e === 'number' &&
      typeof f === 'number' &&
      [a, b, c, d, e, f].every((value) => Number.isFinite(value))
    ) {
      transform = { a, b, c, d, e, f };
    }
  }

  // Prefer nested PSD source from linkedFiles when available and within depth budget.
  if (nestedDepth < MAX_NESTED_SMART_OBJECT_DEPTH && linkedFiles && linkedFiles.length > 0) {
    const linked = findLinkedFile(linkedFiles, placed.id);
    if (linked?.data && linked.data.byteLength > 0) {
      const nestedBuffer = uint8ToArrayBuffer(linked.data);
      if (hasPsdSignature(nestedBuffer)) {
        try {
          const nested = importPsdBuffer(nestedBuffer, {
            documentId: `nested-${sourceId}`,
            name: linked.name.trim() || `${common.name} source`,
            nestedDepth: nestedDepth + 1,
            signal,
          });
          return {
            layer: createSmartObjectLayer({
              ...common,
              source: {
                id: sourceId,
                name: linked.name.trim() || common.name,
                canvas: {
                  width: nested.document.canvas.width,
                  height: nested.document.canvas.height,
                },
                layers: nested.document.layers,
              },
              transform,
            }),
            fromNestedPsd: true,
          };
        } catch (error) {
          if (error instanceof Error && error.name === 'AbortError') throw error;
          const message = error instanceof Error ? error.message : 'unknown error';
          warnings.add(
            `Linked PSD for smart object "${common.name}" could not be nested-imported (${message}); falling back to raster preview when available.`,
          );
        }
      }
    }
  } else if (
    nestedDepth >= MAX_NESTED_SMART_OBJECT_DEPTH &&
    linkedFiles?.some((file) => file.id === placed.id && file.data)
  ) {
    warnings.add(
      `Nested smart-object PSD import depth limit (${String(MAX_NESTED_SMART_OBJECT_DEPTH)}) was reached for "${common.name}"; raster preview is used when available.`,
    );
  }

  // Fallback: single pixel layer from the placed layer's raster preview.
  const image = layer.imageData;
  if (!image || image.width < 1 || image.height < 1) return null;
  if (!(image.data instanceof Uint8Array || image.data instanceof Uint8ClampedArray)) {
    return null;
  }

  const sourceWidth = Math.max(1, Math.round(placed.width ?? image.width));
  const sourceHeight = Math.max(1, Math.round(placed.height ?? image.height));
  const width = image.width;
  const height = image.height;

  if (sourceWidth !== width || sourceHeight !== height) {
    warnings.add(
      'PSD smart-object source size differed from the raster preview; preview dimensions were used for the embedded source.',
    );
  }

  const embeddedPixels = createPixelLayer({
    id: `${sourceId}-pixels`,
    name: `${common.name} contents`,
    width,
    height,
    pixels: image.data,
  });

  return {
    layer: createSmartObjectLayer({
      ...common,
      source: {
        id: sourceId,
        name: common.name,
        canvas: { width, height },
        layers: [embeddedPixels],
      },
      transform,
    }),
    fromNestedPsd: false,
  };
}

function findLinkedFile(files: readonly LinkedFile[], placedId: string): LinkedFile | undefined {
  const trimmed = placedId.trim();
  if (!trimmed) return undefined;
  // ag-psd ids are often brace-wrapped UUIDs; match exact then case-insensitive.
  const exact = files.find((file) => file.id === trimmed);
  if (exact) return exact;
  const lower = trimmed.toLowerCase();
  return files.find((file) => file.id.toLowerCase() === lower);
}

function uint8ToArrayBuffer(bytes: Uint8Array): ArrayBuffer {
  if (
    bytes.byteOffset === 0 &&
    bytes.byteLength === bytes.buffer.byteLength &&
    bytes.buffer instanceof ArrayBuffer
  ) {
    return bytes.buffer;
  }
  const copy = new Uint8Array(bytes.byteLength);
  copy.set(bytes);
  return copy.buffer;
}

/**
 * Converts a PSD text record into an editable native text layer.
 * Preserves plain text and a best-effort style subset (font size/weight/color).
 */
function convertPsdTextLayer(
  layer: PsdLayer,
  common: {
    readonly id: string;
    readonly name: string;
    readonly visible: boolean;
    readonly opacity: number;
    readonly fillOpacity: number;
    readonly blendMode: BlendMode;
    readonly clipping: boolean;
    readonly locks: { readonly transparency: boolean };
  },
): Layer | null {
  const textData = layer.text;
  if (!textData || typeof textData.text !== 'string') return null;
  const content = textData.text.replace(/\r\n/g, '\n').replace(/\r/g, '\n');
  if (content.trim().length === 0) return null;

  const layerLeft = layer.left ?? 0;
  const layerTop = layer.top ?? 0;
  const layerRight = layer.right ?? layerLeft + 1;
  const layerBottom = layer.bottom ?? layerTop + 1;
  let width = Math.max(1, Math.round(layerRight - layerLeft));
  let height = Math.max(1, Math.round(layerBottom - layerTop));
  if (layer.imageData && layer.imageData.width >= 1 && layer.imageData.height >= 1) {
    width = Math.max(width, layer.imageData.width);
    height = Math.max(height, layer.imageData.height);
  }
  // Point text often has degenerate bounds; give a readable default box.
  if (width < 8) width = Math.max(8, Math.ceil(content.length * 12));
  if (height < 8) height = 48;

  const styleSource = textData.styleRuns?.[0]?.style ?? textData.style;
  const fontSize = Math.max(1, Math.round(styleSource?.fontSize ?? 24));
  const fontWeight = styleSource?.fauxBold ? 700 : 400;
  const italic = styleSource?.fauxItalic === true;
  const color = psdColorToRgba(styleSource?.fillColor);
  const align = mapPsdTextAlign(textData);

  return createTextLayer({
    ...common,
    text: content,
    width,
    height,
    style: {
      fontFamily: styleSource?.font?.name ?? 'Arial',
      fontSize,
      fontWeight,
      italic,
      lineHeight:
        styleSource?.leading && styleSource.leading > 0
          ? Math.max(0.5, styleSource.leading / fontSize)
          : 1.2,
      align,
      color,
    },
    transform: {
      a: 1,
      b: 0,
      c: 0,
      d: 1,
      e: layerLeft,
      f: layerTop,
    },
  });
}

function mapPsdTextAlign(textData: LayerTextData): TextAlign {
  const justification = textData.paragraphStyle?.justification;
  if (justification === 'center' || justification === 'justify-center') return 'center';
  if (
    justification === 'right' ||
    justification === 'justify-right' ||
    justification === 'justify-all'
  ) {
    return 'right';
  }
  return 'left';
}

/**
 * Maps ag-psd adjustment descriptors into native adjustment parameters.
 * Unsupported types return null (caller records preservation).
 */
function convertPsdAdjustment(adjustment: PsdAdjustment, warnings: Set<string>): Adjustment | null {
  const type = adjustment.type;
  if (type === 'invert') {
    return { type: 'invert' };
  }
  if (type === 'brightness/contrast') {
    return {
      type: 'brightness-contrast',
      brightness: clampRange(adjustment.brightness ?? 0, -255, 255),
      contrast: clampRange(adjustment.contrast ?? 0, -255, 255),
    };
  }
  if (type === 'levels') {
    const rgb = adjustment.rgb;
    return {
      type: 'levels',
      inputBlack: clampRange(rgb?.shadowInput ?? 0, 0, 255),
      inputWhite: clampRange(rgb?.highlightInput ?? 255, 0, 255),
      gamma: clampRange(rgb?.midtoneInput ?? 1, 0.1, 10),
      outputBlack: clampRange(rgb?.shadowOutput ?? 0, 0, 255),
      outputWhite: clampRange(rgb?.highlightOutput ?? 255, 0, 255),
    };
  }
  if (type === 'hue/saturation') {
    const master = adjustment.master;
    return {
      type: 'hue-saturation',
      hue: clampRange(master?.hue ?? 0, -180, 180),
      saturation: clampRange(master?.saturation ?? 0, -100, 100),
      lightness: clampRange(master?.lightness ?? 0, -100, 100),
    };
  }
  if (type === 'posterize') {
    return {
      type: 'posterize',
      levels: Math.round(clampRange(adjustment.levels ?? 4, 2, 256)),
    };
  }
  if (type === 'threshold') {
    return {
      type: 'threshold',
      level: Math.round(clampRange(adjustment.level ?? 128, 0, 255)),
    };
  }
  warnings.add(`Unsupported PSD adjustment type "${String(type)}" was not imported as editable.`);
  return null;
}

function clampRange(value: number, min: number, max: number): number {
  if (!Number.isFinite(value)) return min;
  return Math.max(min, Math.min(max, value));
}

/**
 * Maps the subset of PSD layer effects that FreetoShop models natively:
 * drop-shadow, stroke, and color-overlay (from solidFill). Other effects
 * produce warnings and are skipped until native models exist.
 */
function convertPsdEffects(
  effects: LayerEffectsInfo | undefined,
  warnings: Set<string>,
): LayerEffect[] {
  if (!effects) return [];
  const converted: LayerEffect[] = [];

  for (const shadow of effects.dropShadow ?? []) {
    if (shadow.present === false) continue;
    const color = psdColorToRgba(shadow.color);
    const angleDeg = shadow.angle ?? 120;
    const distance = unitsToPixels(shadow.distance, 5);
    const radians = (angleDeg * Math.PI) / 180;
    converted.push({
      type: 'drop-shadow',
      enabled: shadow.enabled !== false,
      opacity: clampUnit(shadow.opacity ?? 0.75),
      offsetX: Math.round(Math.cos(radians) * distance),
      offsetY: Math.round(Math.sin(radians) * distance),
      blur: Math.max(0, unitsToPixels(shadow.size, 5)),
      color,
    });
  }

  for (const stroke of effects.stroke ?? []) {
    if (stroke.present === false) continue;
    if (stroke.fillType && stroke.fillType !== 'color') {
      warnings.add('PSD gradient/pattern strokes were skipped; only solid color strokes import.');
      continue;
    }
    converted.push({
      type: 'stroke',
      enabled: stroke.enabled !== false,
      opacity: clampUnit(stroke.opacity ?? 1),
      size: Math.max(0, unitsToPixels(stroke.size, 1)),
      color: psdColorToRgba(stroke.color),
    });
  }

  for (const fill of effects.solidFill ?? []) {
    if (fill.present === false) continue;
    converted.push({
      type: 'color-overlay',
      enabled: fill.enabled !== false,
      opacity: clampUnit(fill.opacity ?? 1),
      blendMode: mapBlendMode(fill.blendMode, warnings),
      color: psdColorToRgba(fill.color),
    });
  }

  const unsupported: string[] = [];
  if (effects.outerGlow) unsupported.push('outer-glow');
  if (effects.innerGlow) unsupported.push('inner-glow');
  if (effects.innerShadow?.length) unsupported.push('inner-shadow');
  if (effects.bevel) unsupported.push('bevel');
  if (effects.satin) unsupported.push('satin');
  if (effects.gradientOverlay?.length) unsupported.push('gradient-overlay');
  if (effects.patternOverlay) unsupported.push('pattern-overlay');
  if (unsupported.length > 0) {
    warnings.add(`Unsupported PSD layer effects were skipped: ${unsupported.join(', ')}.`);
  }

  return converted;
}

function unitsToPixels(value: UnitsValue | undefined, fallback: number): number {
  if (!value || typeof value.value !== 'number' || !Number.isFinite(value.value)) return fallback;
  // ag-psd UnitsValue is typically already in document pixels for effect sizes.
  return Math.max(0, value.value);
}

function psdColorToRgba(color: Color | undefined): RgbaColor {
  if (!color) return { red: 0, green: 0, blue: 0, alpha: 255 };
  if ('r' in color && 'g' in color && 'b' in color) {
    const alpha = 'a' in color && typeof color.a === 'number' ? color.a : 1;
    return {
      red: clampByte(color.r <= 1 && color.r >= 0 ? color.r * 255 : color.r),
      green: clampByte(color.g <= 1 && color.g >= 0 ? color.g * 255 : color.g),
      blue: clampByte(color.b <= 1 && color.b >= 0 ? color.b * 255 : color.b),
      alpha: clampByte(alpha <= 1 ? alpha * 255 : alpha),
    };
  }
  if ('fr' in color && 'fg' in color && 'fb' in color) {
    return {
      red: clampByte(color.fr * 255),
      green: clampByte(color.fg * 255),
      blue: clampByte(color.fb * 255),
      alpha: 255,
    };
  }
  if ('k' in color && !('c' in color)) {
    const gray = clampByte((1 - color.k) * 255);
    return { red: gray, green: gray, blue: gray, alpha: 255 };
  }
  // CMYK/HSB/Lab: keep a conservative black so import never crashes.
  return { red: 0, green: 0, blue: 0, alpha: 255 };
}

function clampByte(value: number): number {
  if (!Number.isFinite(value)) return 0;
  return Math.max(0, Math.min(255, Math.round(value)));
}

/**
 * Converts ag-psd Bezier knot paths (document space) into layer-local PathCommands.
 * Knot points layout: [prevCtrlX, prevCtrlY, anchorX, anchorY, nextCtrlX, nextCtrlY].
 */
function convertPsdVectorMask(
  mask: LayerVectorMask,
  layerWidth: number,
  layerHeight: number,
  layerLeft: number,
  layerTop: number,
  warnings: Set<string>,
): VectorMask | null {
  if (mask.paths.length === 0) return null;
  const commands: PathCommand[] = [];
  let multipath = false;
  for (const path of mask.paths) {
    const pathCommands = bezierPathToCommands(path, layerLeft, layerTop);
    if (pathCommands.length === 0) continue;
    if (commands.length > 0) multipath = true;
    commands.push(...pathCommands);
  }
  if (commands.length === 0) return null;
  if (multipath) {
    warnings.add(
      'Multiple PSD vector subpaths were concatenated; boolean path operations are not preserved.',
    );
  }
  return createVectorMask(layerWidth, layerHeight, commands, {
    enabled: mask.disable !== true,
    inverted: mask.invert === true,
    density: 1,
  });
}

function bezierPathToCommands(
  path: BezierPath,
  layerLeft: number,
  layerTop: number,
): PathCommand[] {
  const knots = path.knots;
  if (knots.length === 0) return [];
  const commands: PathCommand[] = [];
  const local = (points: readonly number[], index: number): number =>
    (points[index] ?? 0) - (index % 2 === 0 ? layerLeft : layerTop);

  const first = knots[0].points;
  if (first.length < 6) return [];
  commands.push({ type: 'move', x: local(first, 2), y: local(first, 3) });

  for (let index = 1; index < knots.length; index += 1) {
    const prev = knots[index - 1].points;
    const current = knots[index].points;
    if (prev.length < 6 || current.length < 6) continue;
    commands.push({
      type: 'cubic',
      control1X: local(prev, 4),
      control1Y: local(prev, 5),
      control2X: local(current, 0),
      control2Y: local(current, 1),
      x: local(current, 2),
      y: local(current, 3),
    });
  }

  if (!path.open && knots.length > 1) {
    const last = knots[knots.length - 1].points;
    if (last.length >= 6) {
      commands.push({
        type: 'cubic',
        control1X: local(last, 4),
        control1Y: local(last, 5),
        control2X: local(first, 0),
        control2Y: local(first, 1),
        x: local(first, 2),
        y: local(first, 3),
      });
    }
    commands.push({ type: 'close' });
  } else if (!path.open) {
    commands.push({ type: 'close' });
  }
  return commands;
}

/**
 * Maps a PSD user/layer mask into a layer-local sparse raster mask.
 * Mask imageData may be greyscale (1 byte/pixel) or RGBA (alpha used as coverage).
 */
function convertPsdRasterMask(
  mask: LayerMaskData,
  layerWidth: number,
  layerHeight: number,
  layerLeft: number,
  layerTop: number,
  warnings: Set<string>,
): RasterMask | null {
  const image = mask.imageData;
  if (!image || image.width < 1 || image.height < 1) return null;
  if (!(image.data instanceof Uint8Array || image.data instanceof Uint8ClampedArray)) {
    return null;
  }

  const maskLeft = mask.left ?? 0;
  const maskTop = mask.top ?? 0;
  const relative = mask.positionRelativeToLayer === true;
  const defaultColor = Math.max(0, Math.min(255, Math.round(mask.defaultColor ?? 255)));
  const coverage = new Uint8ClampedArray(layerWidth * layerHeight);
  coverage.fill(defaultColor);

  const maskWidth = image.width;
  const maskHeight = image.height;
  const data = image.data;
  const isRgba = data.length === maskWidth * maskHeight * 4;
  const isGrey = data.length === maskWidth * maskHeight;
  if (!isRgba && !isGrey) {
    warnings.add('Unsupported PSD mask pixel layout was skipped.');
    return null;
  }

  for (let ly = 0; ly < layerHeight; ly += 1) {
    for (let lx = 0; lx < layerWidth; lx += 1) {
      // Document-space sample for non-relative masks; layer-local for relative.
      const docX = relative ? lx : layerLeft + lx;
      const docY = relative ? ly : layerTop + ly;
      const mx = docX - maskLeft;
      const my = docY - maskTop;
      if (mx < 0 || my < 0 || mx >= maskWidth || my >= maskHeight) continue;
      let value: number;
      if (isGrey) {
        value = data[my * maskWidth + mx] ?? defaultColor;
      } else {
        // Prefer alpha; fall back to red for opaque greyscale packed as RGB.
        const offset = (my * maskWidth + mx) * 4;
        const alpha = data[offset + 3] ?? 255;
        value = alpha === 255 ? (data[offset] ?? defaultColor) : alpha;
      }
      coverage[ly * layerWidth + lx] = value;
    }
  }

  const density =
    typeof mask.userMaskDensity === 'number'
      ? clampUnit(mask.userMaskDensity > 1 ? mask.userMaskDensity / 255 : mask.userMaskDensity)
      : 1;

  return createRasterMask(layerWidth, layerHeight, coverage, {
    enabled: mask.disabled !== true,
    density,
  });
}

function defaultParse(buffer: ArrayBuffer): Psd {
  initializeHeadlessImageData();
  return readPsd(buffer, {
    useImageData: true,
    skipThumbnail: true,
    // Load linked-file payloads when present so preservation can retain them
    // under the document memory ceiling.
    skipLinkedFilesData: false,
    totalMemoryLimit: PSD_MAX_DECODED_BYTES,
    throwForMissingFeatures: false,
    logMissingFeatures: false,
  });
}

let headlessImageDataInitialized = false;

function initializeHeadlessImageData(): void {
  if (typeof document !== 'undefined' || headlessImageDataInitialized) return;
  initializeCanvas(
    () => {
      throw new Error('PSD canvas decoding is unavailable in this worker.');
    },
    (width, height) =>
      ({ width, height, data: new Uint8ClampedArray(width * height * 4) }) as ImageData,
  );
  headlessImageDataInitialized = true;
}

function inspectPsdHeader(buffer: ArrayBuffer): {
  readonly width: number;
  readonly height: number;
} {
  if (!hasPsdSignature(buffer)) throw new TypeError('The file does not have a PSD signature.');
  if (buffer.byteLength < 26) throw new TypeError('The PSD header is truncated.');
  const view = new DataView(buffer);
  const version = view.getUint16(4, false);
  if (version === 2) throw new TypeError('PSB large documents are not supported yet.');
  if (version !== 1) throw new TypeError(`Unsupported PSD version: ${String(version)}.`);
  const height = view.getUint32(14, false);
  const width = view.getUint32(18, false);
  const depth = view.getUint16(22, false);
  const colorMode = view.getUint16(24, false);
  if (width < 1 || height < 1 || width > PSD_MAX_DIMENSION || height > PSD_MAX_DIMENSION) {
    throw new RangeError(
      `PSD dimensions must be between 1 and ${String(PSD_MAX_DIMENSION)} pixels.`,
    );
  }
  if (!Number.isSafeInteger(width * height) || width * height > PSD_MAX_PIXELS) {
    throw new RangeError(`PSD area cannot exceed ${String(PSD_MAX_PIXELS)} pixels.`);
  }
  if (width * height * 4 > PSD_MAX_DECODED_BYTES) {
    throw new RangeError('The PSD composite exceeds the decoded memory limit.');
  }
  if (depth !== 8)
    throw new TypeError(`Only 8-bit PSD files are supported; found ${String(depth)}-bit.`);
  if (colorMode !== 3)
    throw new TypeError('Only RGB PSD files are supported in this import phase.');
  return { width, height };
}

function mapBlendMode(value: PsdLayer['blendMode'], warnings: Set<string>): BlendMode {
  if (!value || value === 'pass through') return 'normal';
  const candidate =
    value.replaceAll(' ', '-') === 'linear-dodge' ? 'linear-dodge' : value.replaceAll(' ', '-');
  if ((BLEND_MODES as readonly string[]).includes(candidate)) return candidate as BlendMode;
  warnings.add(`Unsupported blend mode "${value}" was imported as Normal.`);
  return 'normal';
}

function clampUnit(value: number): number {
  return Number.isFinite(value) ? Math.max(0, Math.min(1, value)) : 1;
}

function makeId(prefix: string): string {
  return `${prefix}-${typeof crypto !== 'undefined' && 'randomUUID' in crypto ? crypto.randomUUID() : Date.now().toString(36)}`;
}
