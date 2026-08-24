import {
  addLayer,
  addDocumentGuide,
  clearDocumentGuides,
  moveDocumentGuide,
  removeDocumentGuide,
  addAdjustmentLayerRasterMaskFromSelection,
  addLayerRasterMaskFromSelection,
  addAlphaChannelFromSelection,
  combineSelection,
  convertLayerToSmartObject,
  combineRectangularSelection,
  cropDocument,
  createDocument,
  createAdjustmentLayer,
  createPixelLayer,
  createShapeLayer,
  createTextLayer,
  createRasterMask,
  createVectorMask,
  duplicateLayer,
  findLayer,
  findAlphaChannel,
  findLayerWorldTransform,
  groupLayers,
  invertLayerTransform,
  invertLayerMask,
  invertLayerVectorMask,
  invertSelection,
  identityLayerTransform,
  multiplyLayerTransforms,
  patchPixels,
  patchLayerRasterMask,
  loadAlphaChannelSelection,
  removeLayer,
  removeLayers,
  replaceRootLayers,
  replacePixelStore,
  replaceSmartObjectSource,
  removeAlphaChannel,
  resizeCanvas,
  revealAll,
  renameAlphaChannel,
  renameLayer,
  reorderLayer,
  setLayerOpacity,
  setLayerBlendMode,
  setLayerClipping,
  setLayerFillOpacity,
  setLayerEffects,
  setLayerLock,
  setAdjustmentLayerParameters,
  setLayerMaskDensity,
  setLayerMaskEnabled,
  setLayerRasterMask,
  setLayerVectorMask,
  setLayerVectorMaskDensity,
  setLayerVectorMaskEnabled,
  setLayerTranslation,
  setLayerTransform,
  setLayerVisibility,
  setShapeGeometry,
  setShapeStyle,
  setSmartObjectFilters,
  setSelection,
  selectAll,
  setTextContent,
  setTextStyle,
  transformPoint,
  ungroupLayer,
  type ImageDocument,
  BLEND_MODES,
  type Adjustment,
  type Layer,
  type LayerEffect,
  type LayerTransform,
  type PathCommand,
  type PixelLayer,
  type SmartFilter,
  type SelectionCombineMode,
  type SelectionMask,
} from '@freetoshop/core-document';
import { CommandHistory } from '@freetoshop/command-history';
import type { PixelRegion } from '@freetoshop/pixel-storage';
import { exportImage, importImageDocument } from '@freetoshop/file-codecs';
import { hasPsdSignature, PSD_MIME_TYPE } from '@freetoshop/file-codecs/signatures';
import {
  createDocumentRepository,
  hasNativeDocumentSignature,
  NATIVE_DOCUMENT_EXTENSION,
  NATIVE_DOCUMENT_MAX_BYTES,
  NATIVE_DOCUMENT_MIME_TYPE,
} from '@freetoshop/persistence';
import {
  adjustBrightnessPixelStore,
  paintMaskStroke,
  paintStroke,
  type ResamplingMethod,
} from '@freetoshop/raster-engine';
import {
  clampZoom,
  CompositeTileCache,
  compositeDocumentRegion,
  compositeDocumentPixels,
  renderDocument,
  requiresDeterministicComposite,
  selectionOutlineSegments,
  supportsDocumentRegionComposite,
  type ChannelPreview,
  type ViewportTransform,
} from '@freetoshop/renderer';
import {
  compositeMipLevelForZoom,
  planAdvancedCompositeRender,
  shouldSeedCompositeWorkerFrame,
  shouldChainFullCompositeRefine,
} from './editor/compositeRenderPlan.js';
import { CompositeWorkerScheduler } from './editor/compositeWorkerScheduler.js';
import {
  createFreeTransformSnapshot,
  getDocumentHandleGeometry,
  scaleTransformFromHandle,
  type TransformHandleId,
} from '@freetoshop/transform-engine';
import {
  borderSelection,
  contractSelection,
  ellipticalSelection,
  expandSelection,
  featherSelection,
  magicWandSelectionFromSource,
  polygonalSelection,
  smoothSelection,
  type SelectionPoint,
} from '@freetoshop/selection-engine';
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type MouseEvent as ReactMouseEvent,
  type PointerEvent as ReactPointerEvent,
} from 'react';
import { textEngine } from '@freetoshop/text-engine';
import { ModalDialog } from './components/ModalDialog.js';
import { PixelOperationDialog } from './components/PixelOperationDialog.js';
import { ToolRail } from './components/ToolRail.js';
import { toolForShortcut } from './tools/toolRegistry.js';
import { createCommandRegistry } from './commands/commandRegistry.js';
import { routeKeyboardCommand } from './commands/keyboardCommandRouter.js';
import { ApplicationMenuBar } from './shell/ApplicationMenuBar.js';
import { ContextualTaskBar } from './shell/ContextualTaskBar.js';
import { DocumentTabStrip } from './shell/DocumentTabStrip.js';
import { StatusBar } from './shell/StatusBar.js';
import { ToolOptionsBar } from './shell/ToolOptionsBar.js';
import { EditorShell } from './shell/EditorShell.js';
import { WorkspaceLayout } from './shell/WorkspaceLayout.js';
import { CanvasWorkspace } from './workspace/CanvasWorkspace.js';
import { EmptyPanelState } from './ui/EmptyPanelState.js';
import { ContextMenu } from './ui/ContextMenu.js';
import { Icon } from './ui/Icon.js';
import { PanelContent, PanelDock } from './panels/PanelDock.js';
import { LayerRow, LayersPanelHeader } from './panels/LayersPanel.js';
import {
  collectSmartSnapEdges,
  snapDocumentPoint,
  snapThresholdInDocumentPixels,
  type SmartSnapEdge,
} from './editor/guideSnapping.js';
import { GuideRulers } from './editor/GuideRulers.js';
import { hitTestGuide } from './editor/guideHitTest.js';
import { PropertiesPanel } from './panels/PropertiesPanel.js';
import { LayerPropertiesSections } from './panels/properties/LayerPropertiesSections.js';
import { AdjustmentsPanel } from './panels/AdjustmentsPanel.js';
import { resolveLiveCompositeCacheMaxBytes } from './perf/liveCompositeCacheConfig.js';
import { CommandPopoverButton } from './commands/CommandPopoverButton.js';
import {
  loadWorkspaceState,
  resetDockGroup,
  saveWorkspaceState,
} from './workspace/workspacePersistence.js';
import { defaultWorkspaceState } from './workspace/workspaceState.js';
import { updateDockGroup } from './workspace/workspacePersistence.js';
import type { DockGroupId } from './workspace/workspaceProfiles.js';
import { visibleLayerIdsForFilter, type LayerFilterKind } from './panels/layerFiltering.js';
import {
  FILTER_LABELS,
  createFilterPatch as filterPatch,
  type FilterDraft,
  type FilterKind,
} from './editor/filterOperations.js';
import {
  ADJUSTMENT_LAYER_COMMAND_IDS,
  ADJUSTMENT_LAYER_DEFINITIONS,
  FILTER_MENU_DEFINITIONS,
  IMAGE_ADJUSTMENT_DEFINITIONS,
  adjustmentLayerLabel,
  createAdjustmentPreset,
  createSmartFilterFromDraft,
  isImmediateImageAdjustment,
  supportsSmartFilter,
} from './editor/operationDefinitions.js';
import { previewDocumentWithSmartFilterDraft } from './editor/smartFilterPreview.js';
import type {
  CanvasSizeDraft,
  ChannelView,
  DocumentPoint,
  EditTarget,
  Gesture,
  ImageSizeDraft,
  PathHandleRole,
  SmartObjectEditSession,
  Toast,
  Tool,
  TransformDraft,
} from './editor/types.js';
import {
  MAX_DOCUMENT_DIMENSION,
  colorToHex,
  createBlankDocument,
  displayLayers,
  flattenLayers,
  freeTransformAtPoint,
  hexColor,
  hexToColor,
  layerDepth,
  makeId,
  movePathHandle,
  movedLayerPosition,
  parseSizeDraft,
  parseTransformDraft,
  pathCommandAnchor,
  renderableLayerDimensions,
  sameTransform,
  selectionBounds,
  selectionRectangle,
  transformAroundLayerCenter,
  transformDraft,
} from './editor/model.js';
import { selectionExportRaster } from './editor/selectionExport.js';
import { createSparsePixelSourceResolver } from './editor/pixelSources.js';
import {
  documentRegionForLocalPatch,
  type RenderInvalidation,
} from './editor/renderInvalidation.js';
import { isAbortError, PersistentImageWorkerClient } from './workers/client.js';

const RECENT_DOCUMENT_KEY = 'freetoshop.recentDocument';
const TRANSFORM_HANDLE_LABELS: Record<Exclude<TransformHandleId, 'center'>, string> = {
  nw: 'Scale layer from top left',
  n: 'Scale layer from top',
  ne: 'Scale layer from top right',
  e: 'Scale layer from right',
  se: 'Scale layer from bottom right',
  s: 'Scale layer from bottom',
  sw: 'Scale layer from bottom left',
  w: 'Scale layer from left',
  rotate: 'Rotate layer',
};
const repository = createDocumentRepository();

function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  anchor.style.display = 'none';
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  // WebKit may not consume a large object URL until after the click task has
  // returned. Keep it alive briefly so the download cannot race revocation.
  setTimeout(() => URL.revokeObjectURL(url), 30_000);
}

function documentPixels(document: ImageDocument): Uint8ClampedArray {
  const canvas = window.document.createElement('canvas');
  canvas.width = document.canvas.width;
  canvas.height = document.canvas.height;
  const context = canvas.getContext('2d', { willReadFrequently: true });
  if (!context) throw new Error('Canvas 2D is unavailable in this browser.');
  renderDocument(context, document, { checkerboard: false });
  return context.getImageData(0, 0, canvas.width, canvas.height).data;
}

function readDocumentPixelRegion(
  pixels: Uint8ClampedArray,
  documentWidth: number,
  region: PixelRegion,
): Uint8ClampedArray {
  const output = new Uint8ClampedArray(region.width * region.height * 4);
  for (let row = 0; row < region.height; row += 1) {
    const sourceStart = ((region.y + row) * documentWidth + region.x) * 4;
    output.set(
      pixels.subarray(sourceStart, sourceStart + region.width * 4),
      row * region.width * 4,
    );
  }
  return output;
}

interface GuideSnapContext {
  readonly document: ImageDocument | null;
  readonly showGuides: boolean;
  readonly showGrid: boolean;
  readonly showSmartGuides: boolean;
  /** Layers currently being moved/edited — excluded from smart-edge candidates. */
  readonly excludeLayerIds?: ReadonlySet<string>;
  /** Mutable sink for temporary smart-guide overlay strokes. */
  readonly activeSmartEdgesSink?: { current: readonly SmartSnapEdge[] };
  readonly enabled?: boolean;
}

function applyGuideSnap(
  point: DocumentPoint,
  viewport: ViewportTransform,
  snap: GuideSnapContext | undefined,
): DocumentPoint {
  if (!snap?.enabled || !snap.document) {
    if (snap?.activeSmartEdgesSink) snap.activeSmartEdgesSink.current = [];
    return point;
  }
  const smartEdges = snap.showSmartGuides
    ? collectSmartSnapEdges(snap.document, { excludeLayerIds: snap.excludeLayerIds })
    : undefined;
  const result = snapDocumentPoint(point, {
    guides: snap.showGuides ? snap.document.guides : [],
    grid: snap.showGrid ? snap.document.grid : null,
    smartEdges,
    threshold: snapThresholdInDocumentPixels(8, viewport.zoom),
    snapToGuides: snap.showGuides,
    snapToGrid: snap.showGrid,
    snapToSmartGuides: snap.showSmartGuides,
  });
  if (snap.activeSmartEdgesSink) {
    snap.activeSmartEdgesSink.current = result.activeSmartEdges;
  }
  return { x: result.x, y: result.y };
}

function canvasPoint(
  event: ReactPointerEvent<HTMLCanvasElement>,
  viewport: ViewportTransform,
  snap?: GuideSnapContext,
) {
  const bounds = event.currentTarget.getBoundingClientRect();
  const point = {
    x: (event.clientX - bounds.left - viewport.panX) / viewport.zoom,
    y: (event.clientY - bounds.top - viewport.panY) / viewport.zoom,
  };
  return applyGuideSnap(point, viewport, snap);
}

function stageDocumentPoint(
  clientX: number,
  clientY: number,
  stage: HTMLElement,
  viewport: ViewportTransform,
  snap?: GuideSnapContext,
): DocumentPoint {
  const bounds = stage.getBoundingClientRect();
  const point = {
    x: (clientX - bounds.left - viewport.panX) / viewport.zoom,
    y: (clientY - bounds.top - viewport.panY) / viewport.zoom,
  };
  return applyGuideSnap(point, viewport, snap);
}

export function App() {
  const sparsePixelSource = useMemo(() => createSparsePixelSourceResolver(), []);
  const [document, setDocument] = useState<ImageDocument | null>(null);
  const [selectedLayerId, setSelectedLayerId] = useState<string | null>(null);
  const [selectedLayerIds, setSelectedLayerIds] = useState<readonly string[]>([]);
  const [layerSearchQuery, setLayerSearchQuery] = useState('');
  const [layerFilterKind, setLayerFilterKind] = useState<LayerFilterKind>('all');
  const [viewport, setViewport] = useState<ViewportTransform>({ zoom: 1, panX: 0, panY: 0 });
  /** View → Extras: show document guides / grid overlay on the canvas. */
  const [showGuides, setShowGuides] = useState(true);
  const [showGrid, setShowGrid] = useState(true);
  /** View → Smart Guides: snap to other layer edges/centers and canvas midlines. */
  const [showSmartGuides, setShowSmartGuides] = useState(true);
  /** Active smart-guide overlay lines (updated by pointer snap; drawn next frame). */
  const [activeSmartGuideLines, setActiveSmartGuideLines] = useState<readonly SmartSnapEdge[]>([]);
  const activeSmartEdgesSinkRef = useRef<{ current: readonly SmartSnapEdge[] }>({ current: [] });
  const flushSmartGuideOverlay = useCallback(() => {
    const edges = activeSmartEdgesSinkRef.current.current;
    setActiveSmartGuideLines((previous) => {
      if (
        previous.length === edges.length &&
        previous.every((edge, index) => {
          const next = edges.at(index);
          return (
            next !== undefined &&
            edge.axis === next.axis &&
            edge.position === next.position &&
            edge.source === next.source
          );
        })
      ) {
        return previous;
      }
      return edges;
    });
  }, []);
  const clearSmartGuideOverlay = useCallback(() => {
    activeSmartEdgesSinkRef.current.current = [];
    setActiveSmartGuideLines((previous) => (previous.length === 0 ? previous : []));
  }, []);
  const guideSnap = useMemo(() => {
    const exclude = new Set<string>(selectedLayerIds);
    if (selectedLayerId) exclude.add(selectedLayerId);
    return {
      document,
      showGuides,
      showGrid,
      showSmartGuides,
      excludeLayerIds: exclude,
      activeSmartEdgesSink: activeSmartEdgesSinkRef.current,
      enabled: true,
    } satisfies GuideSnapContext;
  }, [document, showGuides, showGrid, showSmartGuides, selectedLayerId, selectedLayerIds]);
  const [workspaceState, setWorkspaceState] = useState(loadWorkspaceState);
  const { dockCollapsed: panelCollapsed, primaryDockWidth, secondaryDockWidth } = workspaceState;
  const setPanelCollapsed = useCallback((value: boolean | ((current: boolean) => boolean)) => {
    setWorkspaceState((current) => ({
      ...current,
      dockCollapsed: typeof value === 'function' ? value(current.dockCollapsed) : value,
    }));
  }, []);
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [contextMenu, setContextMenu] = useState<{
    x: number;
    y: number;
    commands: readonly string[];
    returnFocus?: HTMLElement | null;
  } | null>(null);
  const [busy, setBusy] = useState(false);
  const [operationProgress, setOperationProgress] = useState<number | null>(null);
  const [saveState, setSaveState] = useState<'idle' | 'saving' | 'saved' | 'error'>('idle');
  const [revision, setRevision] = useState(0);
  const [tool, setTool] = useState<Tool>('pan');
  const [brushSize, setBrushSize] = useState(32);
  const [brushOpacity, setBrushOpacity] = useState(100);
  const [brushColor, setBrushColor] = useState('#17b897');
  const [backgroundColor, setBackgroundColor] = useState('#ffffff');
  const [brightness, setBrightness] = useState(0);
  const [previewDocument, setPreviewDocument] = useState<ImageDocument | null>(null);
  const [selectionPreview, setSelectionPreview] = useState<ReturnType<
    typeof selectionRectangle
  > | null>(null);
  const [selectionMaskPreview, setSelectionMaskPreview] = useState<SelectionMask | null>(null);
  const [lassoPreview, setLassoPreview] = useState<readonly SelectionPoint[]>([]);
  const [pathDraft, setPathDraft] = useState<readonly DocumentPoint[]>([]);
  const [selectionMode, setSelectionMode] = useState<SelectionCombineMode>('replace');
  const [selectionRadius, setSelectionRadius] = useState(2);
  const [wandTolerance, setWandTolerance] = useState(32);
  const [wandContiguous, setWandContiguous] = useState(true);
  const [cropRectangle, setCropRectangle] = useState<ReturnType<typeof selectionRectangle> | null>(
    null,
  );
  const [sizeDialog, setSizeDialog] = useState<'canvas' | 'image' | null>(null);
  const [activePixelOperation, setActivePixelOperation] = useState<{
    readonly kind: FilterKind;
    readonly mode: 'destructive' | 'smart';
  } | null>(null);
  const [lastFilter, setLastFilter] = useState<{
    readonly kind: 'gaussian-blur';
    readonly draft: FilterDraft;
  } | null>(null);
  const [canvasSizeDraft, setCanvasSizeDraft] = useState<CanvasSizeDraft>({
    width: '',
    height: '',
    anchor: 'center',
  });
  const [imageSizeDraft, setImageSizeDraft] = useState<ImageSizeDraft>({
    width: '',
    height: '',
    method: 'bilinear',
    constrain: true,
  });
  const [filterDraft, setFilterDraft] = useState<FilterDraft>({
    kind: 'contrast',
    contrast: 20,
    inputBlack: 0,
    gamma: 1,
    inputWhite: 255,
    outputBlack: 0,
    outputWhite: 255,
    hue: 0,
    saturation: 20,
    lightness: 0,
    threshold: 128,
    posterizeLevels: 4,
    blurRadius: 2,
  });
  const [filterError, setFilterError] = useState<string | null>(null);
  const [maskDensityDraft, setMaskDensityDraft] = useState(100);
  const [vectorMaskDensityDraft, setVectorMaskDensityDraft] = useState(100);
  const [editTarget, setEditTarget] = useState<EditTarget>('pixels');
  const [workspaceResetOpen, setWorkspaceResetOpen] = useState(false);
  const [channelView, setChannelView] = useState<ChannelView>('composite');
  const [selectedChannelId, setSelectedChannelId] = useState<string | null>(null);
  const [channelNameDraft, setChannelNameDraft] = useState('');
  const [layerTransformDraft, setLayerTransformDraft] = useState<TransformDraft>(() =>
    transformDraft(identityLayerTransform()),
  );
  const [transformGestureStatus, setTransformGestureStatus] = useState('');
  const [smartObjectEditSession, setSmartObjectEditSession] =
    useState<SmartObjectEditSession | null>(null);
  const [loadedFontFamilies, setLoadedFontFamilies] = useState<readonly string[]>([]);
  const [taskBarDismissed, setTaskBarDismissed] = useState(false);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const stageRef = useRef<HTMLDivElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const fontFileRef = useRef<HTMLInputElement>(null);
  const smartObjectFileRef = useRef<HTMLInputElement>(null);
  const sizeDialogTriggerRef = useRef<HTMLButtonElement | null>(null);
  const historyRef = useRef<CommandHistory<ImageDocument> | null>(null);
  const gestureRef = useRef<Gesture | null>(null);
  const toastId = useRef(0);
  const operationAbortRef = useRef<AbortController | null>(null);
  const maskDensityEditingRef = useRef(false);
  const renderInvalidationRef = useRef<RenderInvalidation<ImageDocument> | null>(null);
  const compositeTileCacheRef = useRef(
    new CompositeTileCache({
      tileSize: 64,
      maxBytes: resolveLiveCompositeCacheMaxBytes(window.__freetoshopPerfCacheMaxBytes),
    }),
  );
  const compositeTileCacheDocumentIdRef = useRef<string | null>(null);
  const compositeWorkerSchedulerRef = useRef<CompositeWorkerScheduler | null>(null);
  const compositeWorkerScheduledDocRef = useRef<ImageDocument | null>(null);
  const compositeWorkerSeededGenerationRef = useRef(-1);
  const documentWorkerClientRef = useRef<PersistentImageWorkerClient | null>(null);
  const [compositeWorkerEpoch, setCompositeWorkerEpoch] = useState(0);
  const saveGenerationRef = useRef(0);

  const getCompositeWorkerScheduler = useCallback((): CompositeWorkerScheduler => {
    if (!compositeWorkerSchedulerRef.current) {
      compositeWorkerSchedulerRef.current = new CompositeWorkerScheduler({
        onFrame: () => setCompositeWorkerEpoch((value) => value + 1),
      });
    }
    return compositeWorkerSchedulerRef.current;
  }, []);

  const getDocumentWorkerClient = useCallback((): PersistentImageWorkerClient => {
    documentWorkerClientRef.current ??= new PersistentImageWorkerClient();
    return documentWorkerClientRef.current;
  }, []);

  useEffect(
    () => () => {
      operationAbortRef.current?.abort();
      documentWorkerClientRef.current?.dispose();
      documentWorkerClientRef.current = null;
    },
    [],
  );
  useEffect(
    () => () => {
      compositeWorkerSchedulerRef.current?.clear();
      compositeWorkerSchedulerRef.current = null;
      compositeWorkerScheduledDocRef.current = null;
    },
    [],
  );
  useEffect(() => saveWorkspaceState(workspaceState), [workspaceState]);
  useEffect(
    () => setTaskBarDismissed(false),
    [tool, selectedLayerId, document?.selection, smartObjectEditSession],
  );

  const layers = useMemo(() => (document ? flattenLayers(document.layers) : []), [document]);
  const visibleLayerIds = useMemo(
    () => visibleLayerIdsForFilter(document?.layers ?? [], layerSearchQuery, layerFilterKind),
    [document?.layers, layerFilterKind, layerSearchQuery],
  );
  const visibleDisplayLayers = useMemo(
    () => displayLayers(document?.layers ?? []).filter((layer) => visibleLayerIds.has(layer.id)),
    [document?.layers, visibleLayerIds],
  );
  const selectedLayer =
    document && selectedLayerId ? findLayer(document, selectedLayerId) : undefined;
  const selectedMask = selectedLayer?.rasterMask ?? null;
  const selectedVectorMask = selectedLayer?.vectorMask ?? null;
  const selectedChannel =
    document && selectedChannelId ? findAlphaChannel(document, selectedChannelId) : undefined;
  const renderedDocument = previewDocument ?? document;
  const history = historyRef.current;
  const parsedCanvasSize = useMemo(
    () => parseSizeDraft(canvasSizeDraft.width, canvasSizeDraft.height),
    [canvasSizeDraft.height, canvasSizeDraft.width],
  );
  const parsedImageSize = useMemo(
    () => parseSizeDraft(imageSizeDraft.width, imageSizeDraft.height),
    [imageSizeDraft.height, imageSizeDraft.width],
  );
  const channelPreview = useMemo<ChannelPreview>(() => {
    if (!renderedDocument || channelView === 'composite') return { mode: 'composite' };
    if (channelView === 'red' || channelView === 'green' || channelView === 'blue') {
      return { mode: channelView };
    }
    const channel = findAlphaChannel(renderedDocument, channelView.slice('alpha:'.length));
    return channel
      ? {
          mode: 'alpha',
          coverage: channel.coverageStore,
        }
      : { mode: 'composite' };
  }, [channelView, renderedDocument]);
  const transformOverlay = useMemo(() => {
    if (
      tool !== 'transform' ||
      channelView !== 'composite' ||
      !renderedDocument ||
      !selectedLayerId
    )
      return null;
    const layer = findLayer(renderedDocument, selectedLayerId);
    if (!layer) return null;
    const dimensions = renderableLayerDimensions(layer);
    if (!dimensions) return null;
    const world = findLayerWorldTransform(renderedDocument, layer.id) ?? layer.transform;
    const parent = multiplyLayerTransforms(world, invertLayerTransform(layer.transform));
    const snapshot = createFreeTransformSnapshot(
      { ...dimensions, transform: layer.transform },
      parent,
    );
    const geometry = getDocumentHandleGeometry(snapshot, {
      rotationOffset: 32 / viewport.zoom,
    }).map((handle) => ({
      ...handle,
      screenX: viewport.panX + handle.x * viewport.zoom,
      screenY: viewport.panY + handle.y * viewport.zoom,
    }));
    return { layer, dimensions, snapshot, geometry };
  }, [channelView, renderedDocument, selectedLayerId, tool, viewport]);
  const pathOverlay = useMemo(() => {
    if (tool !== 'pen' || channelView !== 'composite' || !renderedDocument || !selectedLayerId)
      return null;
    const layer = findLayer(renderedDocument, selectedLayerId);
    if (layer?.kind !== 'shape' || layer.geometry.type !== 'path') return null;
    const world = findLayerWorldTransform(renderedDocument, layer.id) ?? layer.transform;
    const toHandle = (
      commandIndex: number,
      role: PathHandleRole,
      localX: number,
      localY: number,
    ) => {
      const point = transformPoint(world, localX, localY);
      return {
        commandIndex,
        role,
        documentX: point.x,
        documentY: point.y,
        screenX: viewport.panX + point.x * viewport.zoom,
        screenY: viewport.panY + point.y * viewport.zoom,
      };
    };
    const handles: Array<ReturnType<typeof toHandle>> = [];
    const segments: Array<{ from: ReturnType<typeof toHandle>; to: ReturnType<typeof toHandle> }> =
      [];
    let previousAnchor: ReturnType<typeof toHandle> | null = null;
    layer.geometry.commands.forEach((command, commandIndex) => {
      if (command.type === 'close') return;
      const anchor = toHandle(commandIndex, 'anchor', command.x, command.y);
      handles.push(anchor);
      if (command.type === 'cubic') {
        const control1 = toHandle(commandIndex, 'control1', command.control1X, command.control1Y);
        const control2 = toHandle(commandIndex, 'control2', command.control2X, command.control2Y);
        handles.push(control1, control2);
        if (previousAnchor) segments.push({ from: previousAnchor, to: control1 });
        segments.push({ from: control2, to: anchor });
      }
      previousAnchor = anchor;
    });
    return { layer, worldInverse: invertLayerTransform(world), handles, segments };
  }, [channelView, renderedDocument, selectedLayerId, tool, viewport]);

  useEffect(() => {
    if (!maskDensityEditingRef.current) {
      setMaskDensityDraft(Math.round((selectedMask?.density ?? 1) * 100));
    }
  }, [selectedLayerId, selectedMask?.density]);

  useEffect(() => {
    if (selectedMask === null) setEditTarget('pixels');
    else if (selectedLayer && selectedLayer.kind !== 'pixel' && selectedLayer.kind !== 'adjustment')
      setEditTarget('mask');
  }, [selectedLayer, selectedLayerId, selectedMask]);

  useEffect(() => {
    setVectorMaskDensityDraft(Math.round((selectedVectorMask?.density ?? 1) * 100));
  }, [selectedLayerId, selectedVectorMask?.density]);

  useEffect(() => {
    setChannelNameDraft(selectedChannel?.name ?? '');
  }, [selectedChannel?.id, selectedChannel?.name]);

  useEffect(() => {
    setLayerTransformDraft(transformDraft(selectedLayer?.transform ?? identityLayerTransform()));
  }, [selectedLayer?.id, selectedLayer?.transform]);

  useEffect(() => {
    if (!activePixelOperation) {
      setFilterError(null);
      setPreviewDocument(null);
      return;
    }
    if (!document || !selectedLayer) {
      setFilterError('Select a layer to preview this operation.');
      setPreviewDocument(null);
      return;
    }
    // Live smart-filter preview on smart objects (non-destructive draft stack).
    if (activePixelOperation.mode === 'smart') {
      if (selectedLayer.kind !== 'smart-object') {
        setFilterError('Select a smart object to preview a smart filter.');
        setPreviewDocument(null);
        return;
      }
      try {
        setPreviewDocument(
          previewDocumentWithSmartFilterDraft(document, selectedLayer.id, filterDraft),
        );
        setFilterError(null);
      } catch (error) {
        setPreviewDocument(null);
        setFilterError(error instanceof Error ? error.message : 'Smart filter preview failed.');
      }
      return;
    }
    if (selectedLayer.kind !== 'pixel') {
      setFilterError('Select a pixel layer to apply a filter.');
      setPreviewDocument(null);
      return;
    }
    try {
      const worldTransform =
        findLayerWorldTransform(document, selectedLayer.id) ?? selectedLayer.transform;
      const patch = filterPatch(selectedLayer, document.selection, worldTransform, filterDraft);
      setPreviewDocument(
        patch.width > 0
          ? patchPixels(selectedLayer.id, patch, FILTER_LABELS[filterDraft.kind]).execute(document)
          : document,
      );
      setFilterError(null);
    } catch (error) {
      setPreviewDocument(null);
      setFilterError(error instanceof Error ? error.message : 'Filter preview failed.');
    }
  }, [activePixelOperation, document, filterDraft, selectedLayer]);

  useEffect(() => {
    if (!document) return;
    if (selectedChannelId && !findAlphaChannel(document, selectedChannelId)) {
      setSelectedChannelId(null);
    }
    if (channelView.startsWith('alpha:') && !findAlphaChannel(document, channelView.slice(6))) {
      setChannelView('composite');
    }
  }, [channelView, document, selectedChannelId]);

  useEffect(() => {
    if (!document) return;
    const valid = selectedLayerIds.filter((id) => findLayer(document, id) !== undefined);
    if (valid.length !== selectedLayerIds.length) setSelectedLayerIds(valid);
    if (selectedLayerId && findLayer(document, selectedLayerId) === undefined) {
      setSelectedLayerId(valid.at(-1) ?? null);
    }
  }, [document, selectedLayerId, selectedLayerIds]);

  const notify = useCallback((message: string, tone: Toast['tone'] = 'info') => {
    const id = ++toastId.current;
    setToasts((current) => [...current, { id, message, tone }]);
    window.setTimeout(() => setToasts((current) => current.filter((item) => item.id !== id)), 3200);
  }, []);

  const replaceDocument = useCallback((next: ImageDocument) => {
    historyRef.current = new CommandHistory(next, { capacity: 100 });
    setDocument(next);
    const selected = flattenLayers(next.layers).at(-1)?.id ?? null;
    setSelectedLayerId(selected);
    setSelectedLayerIds(selected ? [selected] : []);
    setSelectedChannelId(null);
    setChannelView('composite');
    setCropRectangle(null);
    setSelectionPreview(null);
    setSelectionMaskPreview(null);
    setLassoPreview([]);
    setSizeDialog(null);
    localStorage.setItem(RECENT_DOCUMENT_KEY, next.id);
    setRevision((value) => value + 1);
  }, []);

  const runCommand = useCallback(
    (
      command: Parameters<CommandHistory<ImageDocument>['execute']>[0],
      dirtyRegions?: readonly PixelRegion[],
    ) => {
      const currentHistory = historyRef.current;
      if (!currentHistory) return undefined;
      try {
        const next = currentHistory.execute(command);
        if (dirtyRegions && dirtyRegions.length > 0) {
          renderInvalidationRef.current = { document: next, regions: dirtyRegions };
        }
        setDocument(next);
        setRevision((value) => value + 1);
        return next;
      } catch (error) {
        notify(
          error instanceof Error ? error.message : 'The operation could not be completed.',
          'error',
        );
        return undefined;
      }
    },
    [notify],
  );

  const fitTarget = useCallback((target: ImageDocument) => {
    const stage = stageRef.current;
    if (!stage) return;
    const margin = 72;
    const zoom = clampZoom(
      Math.min(
        (stage.clientWidth - margin) / target.canvas.width,
        (stage.clientHeight - margin) / target.canvas.height,
      ),
    );
    setViewport({
      zoom,
      panX: (stage.clientWidth - target.canvas.width * zoom) / 2,
      panY: (stage.clientHeight - target.canvas.height * zoom) / 2,
    });
  }, []);

  const fitDocument = useCallback(() => {
    if (document) fitTarget(document);
  }, [document, fitTarget]);

  useEffect(() => {
    const recentId = localStorage.getItem(RECENT_DOCUMENT_KEY);
    if (!recentId) return;
    let active = true;
    void repository
      .prepareLoad(recentId)
      .then(async (prepared) => {
        if (!active || !prepared) return undefined;
        await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
        return prepared.hydrate();
      })
      .then((restored) => {
        if (!active || !restored) return;
        historyRef.current = new CommandHistory(restored, { capacity: 100 });
        setDocument(restored);
        const selected = flattenLayers(restored.layers).at(-1)?.id ?? null;
        setSelectedLayerId(selected);
        setSelectedLayerIds(selected ? [selected] : []);
        setSelectedChannelId(null);
        setChannelView('composite');
        setCropRectangle(null);
        setSelectionPreview(null);
        setSelectionMaskPreview(null);
        setLassoPreview([]);
        setSizeDialog(null);
        setSaveState('saved');
        requestAnimationFrame(() => fitTarget(restored));
        notify('Recovered your last local document.');
      })
      .catch((error: unknown) => {
        localStorage.removeItem(RECENT_DOCUMENT_KEY);
        notify(error instanceof Error ? error.message : 'Local recovery failed.', 'error');
      });
    return () => {
      active = false;
    };
  }, [fitTarget, notify]);

  useLayoutEffect(() => {
    if (!document || smartObjectEditSession) return;
    const saveGeneration = ++saveGenerationRef.current;
    setSaveState('saving');
    const timer = window.setTimeout(() => {
      void repository
        .save(document)
        .then(() => {
          if (saveGenerationRef.current !== saveGeneration) return;
          localStorage.setItem(RECENT_DOCUMENT_KEY, document.id);
          setSaveState('saved');
        })
        .catch((error: unknown) => {
          if (saveGenerationRef.current !== saveGeneration) return;
          setSaveState('error');
          notify(error instanceof Error ? error.message : 'Local autosave failed.', 'error');
        });
    }, 350);
    return () => window.clearTimeout(timer);
  }, [document, notify, revision, smartObjectEditSession]);

  const createNew = useCallback(() => {
    const next = createBlankDocument();
    replaceDocument(next);
    requestAnimationFrame(() => fitTarget(next));
    notify('New local document created.');
  }, [fitTarget, notify, replaceDocument]);

  const openFile = useCallback(
    async (file: File) => {
      if (operationAbortRef.current) return;
      setBusy(true);
      let controller: AbortController | null = null;
      let importKind: 'native' | 'psd' | 'raster' = 'raster';
      try {
        const header = await file.slice(0, 26).arrayBuffer();
        const isPsd = hasPsdSignature(header);
        const isNative = hasNativeDocumentSignature(header);
        const claimsPsd = file.type === PSD_MIME_TYPE || /\.psd$/i.test(file.name);
        const claimsNative =
          file.type === NATIVE_DOCUMENT_MIME_TYPE ||
          file.name.toLowerCase().endsWith(NATIVE_DOCUMENT_EXTENSION);
        if (claimsPsd && !isPsd) {
          throw new TypeError('The selected file does not have a PSD signature.');
        }
        if (claimsNative && !isNative) {
          throw new TypeError('The selected file does not have a FreetoShop document signature.');
        }
        if (isNative && file.size > NATIVE_DOCUMENT_MAX_BYTES) {
          throw new RangeError(
            `Native document files cannot exceed ${String(NATIVE_DOCUMENT_MAX_BYTES)} bytes.`,
          );
        }

        let next: ImageDocument;
        let psdWarningCount = 0;
        if (isPsd || isNative) {
          importKind = isNative ? 'native' : 'psd';
          const startingHistory = historyRef.current;
          const startingDocument = startingHistory?.state;
          controller = new AbortController();
          operationAbortRef.current = controller;
          setOperationProgress(0);
          const buffer = await file.arrayBuffer();
          const result = await getDocumentWorkerClient().run(
            isNative
              ? { kind: 'native-import', buffer }
              : { kind: 'psd-import', buffer, name: file.name },
            { signal: controller.signal, onProgress: setOperationProgress },
          );
          if (result.kind === 'native-import' && isNative) next = result.document;
          else if (result.kind === 'psd-import' && isPsd) {
            next = result.document;
            psdWarningCount = result.report.warnings.length;
          } else throw new Error('The document worker returned an invalid import result.');
          if (
            historyRef.current !== startingHistory ||
            historyRef.current?.state !== startingDocument
          ) {
            throw new Error('The document changed while the file was opening. Please try again.');
          }
        } else {
          next = await importImageDocument(file);
        }
        replaceDocument(next);
        requestAnimationFrame(() => fitTarget(next));
        notify(
          isNative
            ? `${file.name} opened as an editable FreetoShop document.`
            : isPsd
              ? `${file.name} imported locally as editable layers${psdWarningCount ? ` with ${String(psdWarningCount)} compatibility warning${psdWarningCount === 1 ? '' : 's'}` : ''}. PSD export is not available yet.`
              : `${file.name} opened locally.`,
        );
      } catch (error) {
        if (isAbortError(error))
          notify(`${importKind === 'native' ? 'Native document' : 'PSD'} import cancelled.`);
        else
          notify(
            error instanceof Error ? error.message : 'The image could not be opened.',
            'error',
          );
      } finally {
        if (operationAbortRef.current === controller) operationAbortRef.current = null;
        setOperationProgress(null);
        setBusy(false);
        if (fileRef.current) fileRef.current.value = '';
      }
    },
    [fitTarget, getDocumentWorkerClient, notify, replaceDocument],
  );

  const loadFontFile = useCallback(
    async (file: File) => {
      setBusy(true);
      try {
        const family = file.name.replace(/\.(?:otf|ttf|ttc)$/iu, '').trim() || 'Local font';
        await textEngine.registerFont({ family, data: await file.arrayBuffer() });
        setLoadedFontFamilies(textEngine.families);
        if (selectedLayer?.kind === 'text') {
          runCommand(
            setTextStyle(selectedLayer.id, { ...selectedLayer.style, fontFamily: family }),
          );
        } else {
          setRevision((value) => value + 1);
        }
        notify(`${family} loaded locally with professional OpenType shaping.`);
      } catch (error) {
        notify(error instanceof Error ? error.message : 'The font could not be loaded.', 'error');
      } finally {
        setBusy(false);
        if (fontFileRef.current) fontFileRef.current.value = '';
      }
    },
    [notify, runCommand, selectedLayer],
  );

  const convertSelectedToSmartObject = useCallback(() => {
    if (
      !selectedLayer ||
      selectedLayer.kind === 'group' ||
      selectedLayer.kind === 'adjustment' ||
      selectedLayer.kind === 'smart-object'
    )
      return;
    runCommand(
      convertLayerToSmartObject(selectedLayer.id, makeId('smart-source'), makeId('smart-content')),
    );
    notify(`${selectedLayer.name} converted to a smart object.`);
  }, [notify, runCommand, selectedLayer]);

  const replaceSmartObjectFile = useCallback(
    async (file: File) => {
      if (selectedLayer?.kind !== 'smart-object') return;
      setBusy(true);
      try {
        const imported = await importImageDocument(file);
        runCommand(
          replaceSmartObjectSource(selectedLayer.id, {
            id: makeId('smart-source'),
            name: `${file.name} contents`,
            canvas: { ...imported.canvas },
            layers: imported.layers,
          }),
        );
        notify(`${selectedLayer.name} contents replaced with ${file.name}.`);
      } catch (error) {
        notify(
          error instanceof Error ? error.message : 'Smart object replacement failed.',
          'error',
        );
      } finally {
        setBusy(false);
        if (smartObjectFileRef.current) smartObjectFileRef.current.value = '';
      }
    },
    [notify, runCommand, selectedLayer],
  );

  const editSmartObjectContents = useCallback(() => {
    if (selectedLayer?.kind !== 'smart-object' || !historyRef.current || smartObjectEditSession)
      return;
    const parentHistory = historyRef.current;
    const sourceDocument = createDocument({
      id: selectedLayer.source.id,
      name: selectedLayer.source.name,
      width: selectedLayer.source.canvas.width,
      height: selectedLayer.source.canvas.height,
      layers: selectedLayer.source.layers,
    });
    setSmartObjectEditSession({
      parentHistory,
      layerId: selectedLayer.id,
      sourceId: selectedLayer.source.id,
      sourceName: selectedLayer.source.name,
      parentViewport: viewport,
    });
    historyRef.current = new CommandHistory(sourceDocument, { capacity: 100 });
    setDocument(sourceDocument);
    const selected = flattenLayers(sourceDocument.layers).at(-1)?.id ?? null;
    setSelectedLayerId(selected);
    setSelectedLayerIds(selected ? [selected] : []);
    setSelectedChannelId(null);
    setChannelView('composite');
    setRevision((value) => value + 1);
    requestAnimationFrame(() => fitTarget(sourceDocument));
    notify(`Editing ${selectedLayer.name} contents.`);
  }, [fitTarget, notify, selectedLayer, smartObjectEditSession, viewport]);

  const saveSmartObjectContents = useCallback(() => {
    if (!document || !smartObjectEditSession) return;
    try {
      const next = smartObjectEditSession.parentHistory.execute(
        replaceSmartObjectSource(smartObjectEditSession.layerId, {
          id: smartObjectEditSession.sourceId,
          name: smartObjectEditSession.sourceName,
          canvas: { ...document.canvas },
          layers: document.layers,
        }),
      );
      historyRef.current = smartObjectEditSession.parentHistory;
      setSmartObjectEditSession(null);
      setDocument(next);
      setSelectedLayerId(smartObjectEditSession.layerId);
      setSelectedLayerIds([smartObjectEditSession.layerId]);
      setViewport(smartObjectEditSession.parentViewport);
      setRevision((value) => value + 1);
      notify('Smart object contents saved to the parent document.');
    } catch (error) {
      notify(
        error instanceof Error ? error.message : 'Smart object contents could not be saved.',
        'error',
      );
    }
  }, [document, notify, smartObjectEditSession]);

  const cancelSmartObjectContents = useCallback(() => {
    if (!smartObjectEditSession) return;
    const parent = smartObjectEditSession.parentHistory.state;
    historyRef.current = smartObjectEditSession.parentHistory;
    setSmartObjectEditSession(null);
    setDocument(parent);
    setSelectedLayerId(smartObjectEditSession.layerId);
    setSelectedLayerIds([smartObjectEditSession.layerId]);
    setViewport(smartObjectEditSession.parentViewport);
    setRevision((value) => value + 1);
    notify('Smart object content edits cancelled.');
  }, [notify, smartObjectEditSession]);

  const exportPng = useCallback(async () => {
    if (!document) return;
    setBusy(true);
    try {
      const blob = await exportImage({
        width: document.canvas.width,
        height: document.canvas.height,
        pixels: documentPixels(document),
      });
      const name =
        document.name.replace(/\.[^.]+$/, '').replace(/[^a-z0-9-_]+/gi, '-') || 'artwork';
      downloadBlob(blob, `${name}.png`);
      notify('PNG export completed.');
    } catch (error) {
      notify(error instanceof Error ? error.message : 'Export failed.', 'error');
    } finally {
      setBusy(false);
    }
  }, [document, notify]);

  const exportSelectionPng = useCallback(async () => {
    if (!document) return;
    setBusy(true);
    try {
      const raster = selectionExportRaster(document, sparsePixelSource);
      if (!raster) throw new Error('Create a nonempty selection before exporting it.');
      const blob = await exportImage(raster);
      const name =
        document.name.replace(/\.[^.]+$/, '').replace(/[^a-z0-9-_]+/gi, '-') || 'artwork';
      downloadBlob(blob, `${name}-selection.png`);
      notify('Selection PNG export completed.');
    } catch (error) {
      notify(error instanceof Error ? error.message : 'Selection export failed.', 'error');
    } finally {
      setBusy(false);
    }
  }, [document, notify, sparsePixelSource]);

  const saveNativeDocument = useCallback(async () => {
    if (!document || busy || operationAbortRef.current) return;
    const controller = new AbortController();
    operationAbortRef.current = controller;
    setBusy(true);
    setOperationProgress(0);
    try {
      const result = await getDocumentWorkerClient().run(
        { kind: 'native-export', document },
        { signal: controller.signal, onProgress: setOperationProgress },
      );
      if (result.kind !== 'native-export') {
        throw new Error('The native document worker returned an invalid export result.');
      }
      const name =
        document.name
          .replace(/\.[^.]+$/, '')
          .replace(/[^a-z0-9-_]+/gi, '-')
          .replace(/^-+|-+$/g, '') || 'artwork';
      downloadBlob(
        new Blob([result.buffer], { type: NATIVE_DOCUMENT_MIME_TYPE }),
        `${name}${NATIVE_DOCUMENT_EXTENSION}`,
      );
      notify('Editable FreetoShop document saved.');
    } catch (error) {
      if (isAbortError(error)) notify('Native document save cancelled.');
      else
        notify(
          error instanceof Error ? error.message : 'The native document could not be saved.',
          'error',
        );
    } finally {
      if (operationAbortRef.current === controller) operationAbortRef.current = null;
      setOperationProgress(null);
      setBusy(false);
    }
  }, [busy, document, getDocumentWorkerClient, notify]);

  const undo = useCallback(() => {
    if (!historyRef.current?.canUndo) return;
    setDocument(historyRef.current.undo());
    setRevision((value) => value + 1);
  }, []);

  const redo = useCallback(() => {
    if (!historyRef.current?.canRedo) return;
    setDocument(historyRef.current.redo());
    setRevision((value) => value + 1);
  }, []);

  const activateCrop = useCallback(() => {
    setTool('crop');
    setChannelView('composite');
    if (document) {
      setCropRectangle({
        x: 0,
        y: 0,
        width: document.canvas.width,
        height: document.canvas.height,
      });
    }
  }, [document]);

  const cancelCrop = useCallback(() => {
    gestureRef.current = null;
    setCropRectangle(null);
    setTool('move');
    setTransformGestureStatus('Crop cancelled.');
  }, []);

  const applyCrop = useCallback(() => {
    if (!document || !cropRectangle) return;
    try {
      const command = cropDocument(cropRectangle);
      const next = runCommand(command);
      if (!next) return;
      setCropRectangle(null);
      setTool('move');
      setTransformGestureStatus('Crop applied.');
      window.requestAnimationFrame(() => fitTarget(next));
      notify(`Cropped to ${String(cropRectangle.width)} × ${String(cropRectangle.height)} pixels.`);
    } catch (error) {
      notify(error instanceof Error ? error.message : 'The crop could not be applied.', 'error');
    }
  }, [cropRectangle, document, fitTarget, notify, runCommand]);

  const closeSizeDialog = useCallback(() => {
    const trigger = sizeDialogTriggerRef.current;
    setSizeDialog(null);
    setActivePixelOperation(null);
    window.requestAnimationFrame(() => {
      window.requestAnimationFrame(() => {
        window.setTimeout(() => {
          const target =
            trigger && window.document.contains(trigger)
              ? trigger
              : window.document.getElementById('menu-image');
          target?.focus();
        }, 100);
      });
    });
  }, []);

  const openCanvasSize = useCallback(
    (trigger?: HTMLButtonElement) => {
      if (!document) return;
      sizeDialogTriggerRef.current =
        trigger ?? window.document.querySelector<HTMLButtonElement>('[data-command="canvas-size"]');
      setCanvasSizeDraft({
        width: String(document.canvas.width),
        height: String(document.canvas.height),
        anchor: 'center',
      });
      setSizeDialog('canvas');
    },
    [document],
  );

  const openImageSize = useCallback(
    (trigger?: HTMLButtonElement) => {
      if (!document) return;
      sizeDialogTriggerRef.current =
        trigger ?? window.document.querySelector<HTMLButtonElement>('[data-command="image-size"]');
      setImageSizeDraft({
        width: String(document.canvas.width),
        height: String(document.canvas.height),
        method: 'bilinear',
        constrain: true,
      });
      setSizeDialog('image');
    },
    [document],
  );

  const openContextualFilterOperation = useCallback(
    (kind: FilterKind, mode: 'destructive' | 'smart', trigger?: HTMLButtonElement) => {
      if (
        !document ||
        (mode === 'destructive' && selectedLayer?.kind !== 'pixel') ||
        (mode === 'smart' && selectedLayer?.kind !== 'smart-object')
      )
        return;
      sizeDialogTriggerRef.current =
        trigger ?? window.document.querySelector<HTMLButtonElement>('#menu-filter');
      setChannelView('composite');
      setFilterError(null);
      setFilterDraft((draft) => ({ ...draft, kind }));
      setActivePixelOperation({ kind, mode });
    },
    [document, selectedLayer],
  );

  const applyCanvasSize = useCallback(() => {
    if (!document || parsedCanvasSize.error) return;
    if (
      parsedCanvasSize.width === document.canvas.width &&
      parsedCanvasSize.height === document.canvas.height
    ) {
      closeSizeDialog();
      notify('Canvas size is unchanged.');
      return;
    }
    const next = runCommand(
      resizeCanvas(parsedCanvasSize.width, parsedCanvasSize.height, canvasSizeDraft.anchor),
    );
    if (!next) return;
    closeSizeDialog();
    window.requestAnimationFrame(() => fitTarget(next));
    notify(
      `Canvas resized to ${String(parsedCanvasSize.width)} × ${String(parsedCanvasSize.height)} pixels.`,
    );
  }, [
    canvasSizeDraft.anchor,
    closeSizeDialog,
    document,
    fitTarget,
    notify,
    parsedCanvasSize,
    runCommand,
  ]);

  const applyImageSize = useCallback(async () => {
    if (!document || parsedImageSize.error || operationAbortRef.current) return;
    if (
      parsedImageSize.width === document.canvas.width &&
      parsedImageSize.height === document.canvas.height
    ) {
      closeSizeDialog();
      notify('Image size is unchanged.');
      return;
    }
    const controller = new AbortController();
    operationAbortRef.current = controller;
    setBusy(true);
    setOperationProgress(0);
    try {
      const result = await getDocumentWorkerClient().run(
        {
          kind: 'resize',
          document,
          width: parsedImageSize.width,
          height: parsedImageSize.height,
          method: imageSizeDraft.method,
        },
        { signal: controller.signal, onProgress: setOperationProgress },
      );
      if (result.kind !== 'resize')
        throw new Error('The resize worker returned an invalid result.');
      const next = runCommand({
        label: 'Resize image',
        execute(current) {
          if (current !== document) {
            throw new Error('The document changed while the image was resizing. Please try again.');
          }
          return result.document;
        },
      });
      if (!next) return;
      closeSizeDialog();
      window.requestAnimationFrame(() => fitTarget(next));
      notify(
        `Image resized to ${String(parsedImageSize.width)} × ${String(parsedImageSize.height)} pixels.`,
      );
    } catch (error) {
      if (isAbortError(error)) notify('Image resize cancelled.');
      else notify(error instanceof Error ? error.message : 'Image resize failed.', 'error');
    } finally {
      if (operationAbortRef.current === controller) operationAbortRef.current = null;
      setOperationProgress(null);
      setBusy(false);
    }
  }, [
    closeSizeDialog,
    document,
    fitTarget,
    getDocumentWorkerClient,
    imageSizeDraft.method,
    notify,
    parsedImageSize,
    runCommand,
  ]);

  const applyFilter = useCallback(
    async (requestedDraft: FilterDraft = filterDraft) => {
      if (!document || !selectedLayer || filterError || operationAbortRef.current) return;
      if (selectedLayer.kind === 'smart-object' && requestedDraft.kind === 'gaussian-blur') {
        const smartFilter = createSmartFilterFromDraft(
          'gaussian-blur',
          requestedDraft,
          makeId('smart-filter'),
        );
        runCommand(
          setSmartObjectFilters(selectedLayer.id, [...selectedLayer.smartFilters, smartFilter]),
        );
        setLastFilter({ kind: 'gaussian-blur', draft: requestedDraft });
        closeSizeDialog();
        notify('Gaussian Blur added as a smart filter.');
        return;
      }
      if (selectedLayer.kind !== 'pixel') return;
      const controller = new AbortController();
      operationAbortRef.current = controller;
      setBusy(true);
      setOperationProgress(0);
      try {
        const worldTransform =
          findLayerWorldTransform(document, selectedLayer.id) ?? selectedLayer.transform;
        const result = await getDocumentWorkerClient().run(
          {
            kind: 'filter',
            layer: selectedLayer,
            selection: document.selection,
            worldTransform,
            draft: requestedDraft,
          },
          { signal: controller.signal, onProgress: setOperationProgress },
        );
        if (result.kind !== 'filter')
          throw new Error('The filter worker returned an invalid result.');
        if (result.patches.length === 0) {
          closeSizeDialog();
          notify(`${FILTER_LABELS[requestedDraft.kind]} made no pixel changes.`);
          return;
        }
        let pixelStore = selectedLayer.pixelStore;
        for (const patch of result.patches) {
          pixelStore = pixelStore.write({
            ...patch,
            pixels: new Uint8ClampedArray(patch.pixels),
          }).store;
        }
        const command = replacePixelStore(
          selectedLayer.id,
          pixelStore,
          `Apply ${FILTER_LABELS[requestedDraft.kind]}`,
        );
        const next = runCommand({
          label: command.label,
          execute(current) {
            if (current !== document) {
              throw new Error(
                'The document changed while the filter was running. Please try again.',
              );
            }
            return command.execute(current);
          },
        });
        if (!next) return;
        closeSizeDialog();
        if (requestedDraft.kind === 'gaussian-blur')
          setLastFilter({ kind: 'gaussian-blur', draft: requestedDraft });
        notify(`${FILTER_LABELS[requestedDraft.kind]} applied destructively.`);
      } catch (error) {
        if (isAbortError(error)) notify('Filter cancelled.');
        else notify(error instanceof Error ? error.message : 'Filter failed.', 'error');
      } finally {
        if (operationAbortRef.current === controller) operationAbortRef.current = null;
        setOperationProgress(null);
        setBusy(false);
      }
    },
    [
      closeSizeDialog,
      document,
      filterDraft,
      filterError,
      getDocumentWorkerClient,
      notify,
      runCommand,
      selectedLayer,
    ],
  );

  const cancelLongOperation = useCallback(() => operationAbortRef.current?.abort(), []);

  const revealEntireDocument = useCallback(() => {
    if (!document) return;
    const next = runCommand(revealAll());
    if (!next) return;
    if (next === document) {
      notify('All visible layer content is already inside the canvas.');
      return;
    }
    window.requestAnimationFrame(() => fitTarget(next));
    notify(
      `Canvas expanded to ${String(next.canvas.width)} × ${String(next.canvas.height)} pixels.`,
    );
  }, [document, fitTarget, notify, runCommand]);

  const applySelectionRefinement = useCallback(
    (operation: 'feather' | 'expand' | 'contract' | 'smooth' | 'border') => {
      if (!document?.selection) return;
      try {
        const labels = {
          feather: 'Feather selection',
          expand: 'Expand selection',
          contract: 'Contract selection',
          smooth: 'Smooth selection',
          border: 'Border selection',
        } as const;
        const nextSelection =
          operation === 'feather'
            ? featherSelection(document.selection, selectionRadius)
            : operation === 'expand'
              ? expandSelection(document.selection, selectionRadius)
              : operation === 'contract'
                ? contractSelection(document.selection, selectionRadius)
                : operation === 'smooth'
                  ? smoothSelection(document.selection, selectionRadius)
                  : borderSelection(document.selection, Math.max(1, selectionRadius));
        runCommand(setSelection(nextSelection, labels[operation]));
        notify(`${labels[operation]} applied.`);
      } catch (error) {
        notify(error instanceof Error ? error.message : 'Selection refinement failed.', 'error');
      }
    },
    [document, notify, runCommand, selectionRadius],
  );

  const selectWithMagicWand = useCallback(
    (point: DocumentPoint) => {
      if (!document) return;
      const seedX = Math.floor(point.x);
      const seedY = Math.floor(point.y);
      if (
        seedX < 0 ||
        seedY < 0 ||
        seedX >= document.canvas.width ||
        seedY >= document.canvas.height
      ) {
        return;
      }
      try {
        const regionComposite = supportsDocumentRegionComposite(document);
        let fallbackPixels: Uint8ClampedArray | undefined;
        const selection = magicWandSelectionFromSource({
          source: {
            width: document.canvas.width,
            height: document.canvas.height,
            read: (region) => {
              if (regionComposite) {
                return compositeDocumentRegion(document, region, {
                  pixelSource: sparsePixelSource,
                });
              }
              fallbackPixels ??= documentPixels(document);
              return readDocumentPixelRegion(fallbackPixels, document.canvas.width, region);
            },
          },
          seedX,
          seedY,
          tolerance: wandTolerance,
          contiguous: wandContiguous,
          sampleAlpha: true,
        });
        runCommand(combineSelection(selection, selectionMode));
        notify(`Magic Wand selected at ${String(seedX)}, ${String(seedY)}.`);
      } catch (error) {
        notify(error instanceof Error ? error.message : 'Magic Wand failed.', 'error');
      }
    },
    [document, notify, runCommand, selectionMode, sparsePixelSource, wandContiguous, wandTolerance],
  );

  const saveSelectionAsChannel = useCallback(() => {
    if (!document?.selection) return;
    const id = makeId('channel');
    runCommand(addAlphaChannelFromSelection(id, `Alpha ${String(document.channels.length + 1)}`));
    setSelectedChannelId(id);
    setChannelView(`alpha:${id}`);
    notify('Selection saved as an alpha channel.');
  }, [document, notify, runCommand]);

  const loadSelectedChannel = useCallback(() => {
    if (!selectedChannel) return;
    runCommand(loadAlphaChannelSelection(selectedChannel.id));
    notify(`${selectedChannel.name} loaded as the selection.`);
  }, [notify, runCommand, selectedChannel]);

  const deleteSelectedChannel = useCallback(() => {
    if (!selectedChannel) return;
    const id = selectedChannel.id;
    runCommand(removeAlphaChannel(id));
    setSelectedChannelId(null);
    if (channelView === `alpha:${id}`) setChannelView('composite');
    notify('Alpha channel deleted.');
  }, [channelView, notify, runCommand, selectedChannel]);

  const commitChannelName = useCallback(() => {
    if (!selectedChannel) return;
    const name = channelNameDraft.trim();
    if (!name || name === selectedChannel.name) {
      setChannelNameDraft(selectedChannel.name);
      return;
    }
    runCommand(renameAlphaChannel(selectedChannel.id, name));
  }, [channelNameDraft, runCommand, selectedChannel]);

  const commitLayerTransform = useCallback(() => {
    if (!selectedLayer) return;
    const next = parseTransformDraft(layerTransformDraft);
    if (!next) {
      setLayerTransformDraft(transformDraft(selectedLayer.transform));
      notify('Transform values must form a finite, invertible matrix.', 'error');
      return;
    }
    if (
      (Object.keys(next) as Array<keyof LayerTransform>).every(
        (key) => next[key] === selectedLayer.transform[key],
      )
    )
      return;
    const result = runCommand(setLayerTransform(selectedLayer.id, next));
    if (!result) setLayerTransformDraft(transformDraft(selectedLayer.transform));
  }, [layerTransformDraft, notify, runCommand, selectedLayer]);

  const applyCenteredTransform = useCallback(
    (operation: LayerTransform, message: string) => {
      if (!selectedLayer || !renderableLayerDimensions(selectedLayer)) return;
      runCommand(
        setLayerTransform(selectedLayer.id, transformAroundLayerCenter(selectedLayer, operation)),
      );
      notify(message);
    },
    [notify, runCommand, selectedLayer],
  );

  const beginFreeTransform = useCallback(
    (event: ReactPointerEvent<HTMLButtonElement>, handle: Exclude<TransformHandleId, 'center'>) => {
      if (!transformOverlay) return;
      const stage = stageRef.current;
      if (!stage) return;
      event.preventDefault();
      event.stopPropagation();
      event.currentTarget.setPointerCapture(event.pointerId);
      gestureRef.current = {
        kind: 'free-transform',
        layerId: transformOverlay.layer.id,
        handle,
        snapshot: transformOverlay.snapshot,
        start: stageDocumentPoint(event.clientX, event.clientY, stage, viewport, guideSnap),
        changed: false,
      };
      setTransformGestureStatus(handle === 'rotate' ? 'Rotating layer…' : 'Scaling layer…');
    },
    [guideSnap, transformOverlay, viewport],
  );

  const previewFreeTransform = useCallback(
    (event: ReactPointerEvent<HTMLElement>) => {
      const gesture = gestureRef.current;
      const stage = stageRef.current;
      if (gesture?.kind !== 'free-transform' || !stage || !document) return;
      try {
        const point = stageDocumentPoint(event.clientX, event.clientY, stage, viewport, guideSnap);
        const next = freeTransformAtPoint(gesture, point, event.shiftKey);
        gesture.changed = !sameTransform(next, gesture.snapshot.localTransform);
        setPreviewDocument(setLayerTransform(gesture.layerId, next).execute(document));
      } catch {
        setTransformGestureStatus(
          'Move farther from the fixed handle to keep the transform valid.',
        );
      }
    },
    [document, guideSnap, viewport],
  );

  const finishFreeTransform = useCallback(
    (event: ReactPointerEvent<HTMLElement>) => {
      if (event.currentTarget.hasPointerCapture(event.pointerId)) {
        event.currentTarget.releasePointerCapture(event.pointerId);
      }
      const gesture = gestureRef.current;
      gestureRef.current = null;
      const stage = stageRef.current;
      if (gesture?.kind !== 'free-transform' || !stage) return;
      try {
        const point = stageDocumentPoint(event.clientX, event.clientY, stage, viewport, guideSnap);
        const next = freeTransformAtPoint(gesture, point, event.shiftKey);
        if (!sameTransform(next, gesture.snapshot.localTransform)) {
          runCommand(setLayerTransform(gesture.layerId, next));
          setTransformGestureStatus('Free Transform committed.');
        } else {
          setTransformGestureStatus('Free Transform unchanged.');
        }
      } catch {
        setTransformGestureStatus('Free Transform cancelled because the result was invalid.');
      } finally {
        setPreviewDocument(null);
      }
    },
    [guideSnap, runCommand, viewport],
  );

  const cancelFreeTransform = useCallback(() => {
    gestureRef.current = null;
    setPreviewDocument(null);
    setTransformGestureStatus('Free Transform cancelled.');
  }, []);

  const beginPathHandle = useCallback(
    (event: ReactPointerEvent<HTMLButtonElement>, commandIndex: number, role: PathHandleRole) => {
      if (!pathOverlay) return;
      event.preventDefault();
      event.stopPropagation();
      event.currentTarget.setPointerCapture(event.pointerId);
      gestureRef.current = {
        kind: 'path-handle',
        layerId: pathOverlay.layer.id,
        commandIndex,
        role,
        geometry: pathOverlay.layer.geometry,
        worldInverse: pathOverlay.worldInverse,
        changed: false,
      };
      setTransformGestureStatus(`Moving path ${role === 'anchor' ? 'anchor' : 'control handle'}…`);
    },
    [pathOverlay],
  );

  const pathGeometryAtPointer = useCallback(
    (gesture: Extract<Gesture, { kind: 'path-handle' }>, clientX: number, clientY: number) => {
      const stage = stageRef.current;
      if (!stage) return gesture.geometry;
      const documentPoint = stageDocumentPoint(clientX, clientY, stage, viewport, guideSnap);
      const local = transformPoint(gesture.worldInverse, documentPoint.x, documentPoint.y);
      return movePathHandle(gesture.geometry, gesture.commandIndex, gesture.role, {
        x: Math.round(local.x * 10) / 10,
        y: Math.round(local.y * 10) / 10,
      });
    },
    [guideSnap, viewport],
  );

  const previewPathHandle = useCallback(
    (event: ReactPointerEvent<HTMLButtonElement>) => {
      const gesture = gestureRef.current;
      if (gesture?.kind !== 'path-handle' || !document) return;
      const geometry = pathGeometryAtPointer(gesture, event.clientX, event.clientY);
      gesture.changed = geometry !== gesture.geometry;
      setPreviewDocument(setShapeGeometry(gesture.layerId, geometry).execute(document));
    },
    [document, pathGeometryAtPointer],
  );

  const finishPathHandle = useCallback(
    (event: ReactPointerEvent<HTMLButtonElement>) => {
      if (event.currentTarget.hasPointerCapture(event.pointerId)) {
        event.currentTarget.releasePointerCapture(event.pointerId);
      }
      const gesture = gestureRef.current;
      gestureRef.current = null;
      if (gesture?.kind !== 'path-handle') return;
      const geometry = pathGeometryAtPointer(gesture, event.clientX, event.clientY);
      if (gesture.changed) {
        runCommand(setShapeGeometry(gesture.layerId, geometry));
        setTransformGestureStatus('Path handle moved.');
      } else {
        setTransformGestureStatus('Path handle unchanged.');
      }
      setPreviewDocument(null);
    },
    [pathGeometryAtPointer, runCommand],
  );

  const cancelPathHandle = useCallback(() => {
    if (gestureRef.current?.kind !== 'path-handle') return;
    gestureRef.current = null;
    setPreviewDocument(null);
    setTransformGestureStatus('Path handle move cancelled.');
  }, []);

  const handlePathHandleKeyDown = useCallback(
    (event: ReactKeyboardEvent<HTMLButtonElement>, commandIndex: number, role: PathHandleRole) => {
      if (!pathOverlay) return;
      if (!['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(event.key)) return;
      event.preventDefault();
      const handle = pathOverlay.handles.find(
        (candidate) => candidate.commandIndex === commandIndex && candidate.role === role,
      );
      if (!handle) return;
      const step = (event.shiftKey ? 10 : 1) / viewport.zoom;
      const documentPoint = {
        x:
          handle.documentX +
          (event.key === 'ArrowLeft' ? -step : event.key === 'ArrowRight' ? step : 0),
        y:
          handle.documentY +
          (event.key === 'ArrowUp' ? -step : event.key === 'ArrowDown' ? step : 0),
      };
      const local = transformPoint(pathOverlay.worldInverse, documentPoint.x, documentPoint.y);
      runCommand(
        setShapeGeometry(
          pathOverlay.layer.id,
          movePathHandle(pathOverlay.layer.geometry, commandIndex, role, local),
        ),
      );
      setTransformGestureStatus('Path handle nudged.');
    },
    [pathOverlay, runCommand, viewport.zoom],
  );

  const handleTransformKeyDown = useCallback(
    (
      event: ReactKeyboardEvent<HTMLButtonElement>,
      handle: Exclude<TransformHandleId, 'center'>,
    ) => {
      if (!transformOverlay) return;
      if (event.key === 'Escape') {
        event.preventDefault();
        cancelFreeTransform();
        return;
      }
      if (!['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(event.key)) return;
      event.preventDefault();
      const step = event.shiftKey ? 10 : 1;
      if (handle === 'rotate') {
        const degrees =
          (event.key === 'ArrowLeft' || event.key === 'ArrowUp' ? -step : step) *
          (event.shiftKey ? 1.5 : 1);
        const radians = (degrees * Math.PI) / 180;
        runCommand(
          setLayerTransform(
            transformOverlay.layer.id,
            transformAroundLayerCenter(transformOverlay.layer, {
              a: Math.cos(radians),
              b: Math.sin(radians),
              c: -Math.sin(radians),
              d: Math.cos(radians),
              e: 0,
              f: 0,
            }),
          ),
        );
        setTransformGestureStatus(`Layer rotated ${String(degrees)} degrees.`);
        return;
      }
      const geometry = transformOverlay.geometry.find((candidate) => candidate.id === handle);
      if (!geometry) return;
      const deltaX = event.key === 'ArrowLeft' ? -step : event.key === 'ArrowRight' ? step : 0;
      const deltaY = event.key === 'ArrowUp' ? -step : event.key === 'ArrowDown' ? step : 0;
      try {
        const next = scaleTransformFromHandle(
          transformOverlay.snapshot,
          handle,
          {
            x: geometry.x + deltaX / viewport.zoom,
            y: geometry.y + deltaY / viewport.zoom,
          },
          { uniform: event.shiftKey },
        );
        runCommand(setLayerTransform(transformOverlay.layer.id, next));
        setTransformGestureStatus('Layer scaled from the keyboard.');
      } catch {
        setTransformGestureStatus('That keyboard scale would make the transform invalid.');
      }
    },
    [cancelFreeTransform, runCommand, transformOverlay, viewport.zoom],
  );

  const addBlankLayer = useCallback(() => {
    if (!document) return;
    const layer = createPixelLayer({
      id: makeId('layer'),
      name: `Layer ${String(layers.length + 1)}`,
      width: document.canvas.width,
      height: document.canvas.height,
    });
    runCommand(addLayer(layer));
    setSelectedLayerId(layer.id);
    setSelectedLayerIds([layer.id]);
  }, [document, layers.length, runCommand]);

  const addShape = useCallback(
    (type: 'rectangle' | 'ellipse') => {
      if (!document) return;
      const width = Math.min(240, document.canvas.width);
      const height = Math.min(160, document.canvas.height);
      const layer = createShapeLayer({
        id: makeId('shape'),
        name: type === 'rectangle' ? 'Rectangle' : 'Ellipse',
        geometry: { type, width, height, cornerRadius: 0 },
        transform: {
          ...identityLayerTransform(),
          e: Math.round((document.canvas.width - width) / 2),
          f: Math.round((document.canvas.height - height) / 2),
        },
      });
      runCommand(addLayer(layer));
      setSelectedLayerId(layer.id);
      setSelectedLayerIds([layer.id]);
      notify(`${layer.name} shape layer added.`);
    },
    [document, notify, runCommand],
  );

  const finishPath = useCallback(
    (closed: boolean) => {
      if (!document || pathDraft.length < 2) {
        notify('Add at least two path anchors before finishing.', 'error');
        return;
      }
      if (closed && pathDraft.length < 3) {
        notify('A closed path needs at least three anchors.', 'error');
        return;
      }
      const margin = 3;
      const originX = Math.floor(Math.min(...pathDraft.map((point) => point.x)) - margin);
      const originY = Math.floor(Math.min(...pathDraft.map((point) => point.y)) - margin);
      const farX = Math.ceil(Math.max(...pathDraft.map((point) => point.x)) + margin);
      const farY = Math.ceil(Math.max(...pathDraft.map((point) => point.y)) + margin);
      const commands: PathCommand[] = pathDraft.map((point, index) => ({
        type: index === 0 ? 'move' : 'line',
        x: point.x - originX,
        y: point.y - originY,
      }));
      if (closed) commands.push({ type: 'close' });
      const layer = createShapeLayer({
        id: makeId('path'),
        name: closed ? 'Closed path' : 'Open path',
        geometry: {
          type: 'path',
          width: Math.max(1, farX - originX),
          height: Math.max(1, farY - originY),
          cornerRadius: 0,
          commands,
        },
        style: {
          fill: closed ? { red: 52, green: 211, blue: 153, alpha: 96 } : null,
          stroke: { red: 23, green: 184, blue: 151, alpha: 255 },
          strokeWidth: 4,
        },
        transform: { ...identityLayerTransform(), e: originX, f: originY },
      });
      runCommand(addLayer(layer));
      setSelectedLayerId(layer.id);
      setSelectedLayerIds([layer.id]);
      setPathDraft([]);
      notify(`${closed ? 'Closed' : 'Open'} vector path added.`);
    },
    [document, notify, pathDraft, runCommand],
  );

  const addText = useCallback(() => {
    if (!document) return;
    const width = Math.min(400, document.canvas.width);
    const height = Math.min(160, document.canvas.height);
    const layer = createTextLayer({
      id: makeId('text'),
      name: 'Text',
      text: 'Your text',
      width,
      height,
      transform: {
        ...identityLayerTransform(),
        e: Math.round((document.canvas.width - width) / 2),
        f: Math.round((document.canvas.height - height) / 2),
      },
    });
    runCommand(addLayer(layer));
    setSelectedLayerId(layer.id);
    setSelectedLayerIds([layer.id]);
    notify('Editable text layer added.');
  }, [document, notify, runCommand]);

  const addAdjustment = useCallback(
    (type: Adjustment['type']) => {
      if (!document) return;
      const layer = createAdjustmentLayer({
        id: makeId('adjustment'),
        name: adjustmentLayerLabel(type),
        adjustment: createAdjustmentPreset(type),
      });
      runCommand(addLayer(layer));
      setSelectedLayerId(layer.id);
      setSelectedLayerIds([layer.id]);
      setEditTarget('pixels');
      setWorkspaceState((current) => ({
        ...current,
        narrowDockColumn: window.innerWidth < 1600 ? 'secondary' : current.narrowDockColumn,
        groups: current.groups.map((group) =>
          group.id === 'properties'
            ? { ...group, visible: true, collapsed: false, activePanel: 'properties' }
            : group,
        ),
      }));
      requestAnimationFrame(() => window.document.getElementById('panel-tab-properties')?.focus());
      notify(`${adjustmentLayerLabel(type)} adjustment layer added.`);
    },
    [document, notify, runCommand],
  );

  const updateAdjustment = useCallback(
    (adjustment: Adjustment) => {
      if (selectedLayer?.kind !== 'adjustment') return;
      runCommand(setAdjustmentLayerParameters(selectedLayer.id, adjustment));
    },
    [runCommand, selectedLayer],
  );

  const addAdjustmentMask = useCallback(() => {
    if (selectedLayer?.kind !== 'adjustment') return;
    runCommand(addAdjustmentLayerRasterMaskFromSelection(selectedLayer.id));
    notify(
      document?.selection
        ? 'Adjustment mask added from selection.'
        : 'Reveal-all adjustment mask added.',
    );
  }, [document?.selection, notify, runCommand, selectedLayer]);

  const addLayerEffect = useCallback(
    (type: LayerEffect['type']) => {
      if (!selectedLayer) return;
      const defaults: Record<LayerEffect['type'], LayerEffect> = {
        'color-overlay': {
          type: 'color-overlay',
          enabled: true,
          opacity: 1,
          blendMode: 'normal',
          color: { red: 52, green: 211, blue: 153, alpha: 255 },
        },
        stroke: {
          type: 'stroke',
          enabled: true,
          opacity: 1,
          size: 4,
          color: { red: 255, green: 255, blue: 255, alpha: 255 },
        },
        'drop-shadow': {
          type: 'drop-shadow',
          enabled: true,
          opacity: 0.6,
          offsetX: 8,
          offsetY: 8,
          blur: 6,
          color: { red: 0, green: 0, blue: 0, alpha: 255 },
        },
      };
      runCommand(setLayerEffects(selectedLayer.id, [...selectedLayer.effects, defaults[type]]));
      notify(`${type.split('-').join(' ')} effect added.`);
    },
    [notify, runCommand, selectedLayer],
  );

  const updateLayerEffect = useCallback(
    (index: number, effect: LayerEffect) => {
      if (!selectedLayer) return;
      runCommand(
        setLayerEffects(
          selectedLayer.id,
          selectedLayer.effects.map((current, candidate) =>
            candidate === index ? effect : current,
          ),
        ),
      );
    },
    [runCommand, selectedLayer],
  );

  const removeLayerEffect = useCallback(
    (index: number) => {
      if (!selectedLayer) return;
      runCommand(
        setLayerEffects(
          selectedLayer.id,
          selectedLayer.effects.filter((_, candidate) => candidate !== index),
        ),
      );
    },
    [runCommand, selectedLayer],
  );

  const addSmartFilter = useCallback(
    (type: SmartFilter['type']) => {
      if (selectedLayer?.kind !== 'smart-object') return;
      const common = { id: makeId('smart-filter'), enabled: true, opacity: 1 };
      const defaults: Record<SmartFilter['type'], SmartFilter> = {
        invert: { ...common, type: 'invert' },
        grayscale: { ...common, type: 'grayscale' },
        'gaussian-blur': { ...common, type: 'gaussian-blur', radius: 4 },
        'brightness-contrast': {
          ...common,
          type: 'brightness-contrast',
          brightness: 0,
          contrast: 20,
        },
      };
      runCommand(
        setSmartObjectFilters(selectedLayer.id, [...selectedLayer.smartFilters, defaults[type]]),
      );
      notify(`${type.split('-').join(' ')} smart filter added.`);
    },
    [notify, runCommand, selectedLayer],
  );

  const updateSmartFilter = useCallback(
    (index: number, filter: SmartFilter) => {
      if (selectedLayer?.kind !== 'smart-object') return;
      runCommand(
        setSmartObjectFilters(
          selectedLayer.id,
          selectedLayer.smartFilters.map((current, candidate) =>
            candidate === index ? filter : current,
          ),
        ),
      );
    },
    [runCommand, selectedLayer],
  );

  const removeSmartFilter = useCallback(
    (index: number) => {
      if (selectedLayer?.kind !== 'smart-object') return;
      runCommand(
        setSmartObjectFilters(
          selectedLayer.id,
          selectedLayer.smartFilters.filter((_, candidate) => candidate !== index),
        ),
      );
    },
    [runCommand, selectedLayer],
  );

  const selectLayer = useCallback((event: ReactMouseEvent<HTMLButtonElement>, id: string) => {
    if (!event.ctrlKey && !event.metaKey) {
      setSelectedLayerId(id);
      setSelectedLayerIds([id]);
      return;
    }
    setSelectedLayerIds((current) => {
      const selected = current.includes(id);
      const next = selected ? current.filter((candidate) => candidate !== id) : [...current, id];
      setSelectedLayerId(selected ? (next.at(-1) ?? null) : id);
      return next;
    });
  }, []);

  const selectLayerEditTarget = useCallback((layerId: string, target: EditTarget) => {
    setSelectedLayerId(layerId);
    setSelectedLayerIds([layerId]);
    setEditTarget(target);
  }, []);

  const openPanelGroupMenu = useCallback(
    (event: ReactMouseEvent<HTMLElement>, groupId: DockGroupId) => {
      event.preventDefault();
      event.stopPropagation();
      setContextMenu({
        x: event.clientX,
        y: event.clientY,
        returnFocus: event.currentTarget,
        commands: [
          `window.panel.${groupId}.toggle-collapse`,
          `window.panel.${groupId}.reset`,
          `window.panel.${groupId}`,
          'window.reset-workspace',
        ],
      });
    },
    [],
  );

  const confirmWorkspaceReset = useCallback(() => {
    setWorkspaceState(defaultWorkspaceState());
    setWorkspaceResetOpen(false);
    notify('Professional Editing workspace reset.');
  }, [notify]);

  const duplicateSelectedLayer = useCallback(() => {
    if (!selectedLayer) return;
    const id = makeId('layer-copy');
    const next = runCommand(duplicateLayer(selectedLayer.id, id));
    if (!next) return;
    setSelectedLayerId(id);
    setSelectedLayerIds([id]);
    notify(`${selectedLayer.name} duplicated.`);
  }, [notify, runCommand, selectedLayer]);

  const groupSelectedLayers = useCallback(() => {
    if (selectedLayerIds.length === 0) return;
    const id = makeId('group');
    const next = runCommand(
      groupLayers(selectedLayerIds, {
        id,
        name:
          selectedLayerIds.length === 1
            ? 'Layer group'
            : `Group of ${String(selectedLayerIds.length)}`,
      }),
    );
    if (!next) return;
    setSelectedLayerId(id);
    setSelectedLayerIds([id]);
    notify('Selected layers grouped.');
  }, [notify, runCommand, selectedLayerIds]);

  const ungroupSelectedLayer = useCallback(() => {
    if (selectedLayer?.kind !== 'group') return;
    const childIds = selectedLayer.children.map((child) => child.id);
    const next = runCommand(ungroupLayer(selectedLayer.id));
    if (!next) return;
    setSelectedLayerIds(childIds);
    setSelectedLayerId(childIds.at(-1) ?? null);
    notify('Layer group released.');
  }, [notify, runCommand, selectedLayer]);

  const mergeSelectedLayers = useCallback(() => {
    if (!document || selectedLayerIds.length < 2) return;
    const selected = new Set(selectedLayerIds);
    const rootLayers = document.layers.filter((layer) => selected.has(layer.id));
    if (rootLayers.length !== selected.size) {
      notify('Merge currently requires layers that share the document root.', 'error');
      return;
    }
    const pixels = compositeDocumentPixels(
      createDocument({
        id: makeId('merge-preview'),
        name: 'Merge preview',
        width: document.canvas.width,
        height: document.canvas.height,
        layers: rootLayers,
      }),
    );
    const merged = createPixelLayer({
      id: makeId('merged'),
      name: `Merged ${String(rootLayers.length)} layers`,
      width: document.canvas.width,
      height: document.canvas.height,
      pixels,
    });
    const next = runCommand(replaceRootLayers(selectedLayerIds, merged));
    if (!next) return;
    setSelectedLayerId(merged.id);
    setSelectedLayerIds([merged.id]);
    notify('Selected layers merged.');
  }, [document, notify, runCommand, selectedLayerIds]);

  const flattenImage = useCallback(() => {
    if (!document || document.layers.length === 0) return;
    const flattened = createPixelLayer({
      id: makeId('flattened'),
      name: 'Flattened image',
      width: document.canvas.width,
      height: document.canvas.height,
      pixels: compositeDocumentPixels(document),
    });
    const next = runCommand(
      replaceRootLayers(
        document.layers.map((layer) => layer.id),
        flattened,
        'Flatten image',
      ),
    );
    if (!next) return;
    setSelectedLayerId(flattened.id);
    setSelectedLayerIds([flattened.id]);
    notify('Visible document content flattened.');
  }, [document, notify, runCommand]);

  const deleteSelectedLayer = useCallback(() => {
    if (selectedLayerIds.length === 0 || !document) return;
    runCommand(
      selectedLayerIds.length === 1
        ? removeLayer(selectedLayerIds[0] as string)
        : removeLayers(selectedLayerIds),
    );
    const selected = new Set(selectedLayerIds);
    const remaining = layers.filter((layer) => !selected.has(layer.id));
    const next = remaining.at(-1)?.id ?? null;
    setSelectedLayerId(next);
    setSelectedLayerIds(next ? [next] : []);
  }, [document, layers, runCommand, selectedLayerIds]);

  const forgetDocument = useCallback(() => {
    if (
      !document ||
      !window.confirm(
        'Remove this document from local recovery storage? Export it first if needed.',
      )
    )
      return;
    void repository
      .delete(document.id)
      .then(() => {
        localStorage.removeItem(RECENT_DOCUMENT_KEY);
        historyRef.current = null;
        setDocument(null);
        setSelectedLayerId(null);
        setSelectedLayerIds([]);
        setSelectedChannelId(null);
        setChannelView('composite');
        setCropRectangle(null);
        setSelectionPreview(null);
        setSelectionMaskPreview(null);
        setLassoPreview([]);
        setSizeDialog(null);
        setSaveState('idle');
        notify('Local recovery copy removed.');
      })
      .catch((error: unknown) =>
        notify(error instanceof Error ? error.message : 'Could not clear local data.', 'error'),
      );
  }, [document, notify]);

  const moveSelectedLayer = useCallback(
    (direction: -1 | 1) => {
      if (!selectedLayerId || !document) return;
      const fromIndex = document.layers.findIndex((layer) => layer.id === selectedLayerId);
      const toIndex = fromIndex + direction;
      if (fromIndex < 0 || toIndex < 0 || toIndex >= document.layers.length) return;
      runCommand(reorderLayer(null, fromIndex, toIndex));
    },
    [document, runCommand, selectedLayerId],
  );

  const applyBrightness = useCallback(() => {
    if (!document || selectedLayer?.kind !== 'pixel' || brightness === 0) return;
    try {
      const pixelStore = adjustBrightnessPixelStore(
        selectedLayer,
        brightness,
        document.selection,
        findLayerWorldTransform(document, selectedLayer.id) ?? selectedLayer.transform,
      );
      if (pixelStore === selectedLayer.pixelStore) return;
      runCommand(replacePixelStore(selectedLayer.id, pixelStore, 'Apply Brightness'));
      setBrightness(0);
      notify('Brightness applied to the selected pixels.');
    } catch (error) {
      notify(error instanceof Error ? error.message : 'Brightness could not be applied.', 'error');
    }
  }, [brightness, document, notify, runCommand, selectedLayer]);

  const addLayerMaskFromSelection = useCallback(() => {
    if (!selectedLayer || selectedLayer.kind === 'adjustment') return;
    runCommand(addLayerRasterMaskFromSelection(selectedLayer.id));
    notify('Layer mask added from selection.');
  }, [notify, runCommand, selectedLayer]);

  const addRevealAllMask = useCallback(() => {
    if (!selectedLayer || selectedLayer.kind === 'adjustment') return;
    const dimensions =
      selectedLayer.kind === 'group' ? document?.canvas : renderableLayerDimensions(selectedLayer);
    if (!dimensions) return;
    runCommand(
      setLayerRasterMask(selectedLayer.id, createRasterMask(dimensions.width, dimensions.height)),
    );
    notify('Reveal-all layer mask added.');
  }, [document?.canvas, notify, runCommand, selectedLayer]);

  const addHideAllMask = useCallback(() => {
    if (!selectedLayer || selectedLayer.kind === 'adjustment') return;
    const dimensions =
      selectedLayer.kind === 'group' ? document?.canvas : renderableLayerDimensions(selectedLayer);
    if (!dimensions) return;
    runCommand(
      setLayerRasterMask(
        selectedLayer.id,
        createRasterMask(
          dimensions.width,
          dimensions.height,
          new Uint8Array(dimensions.width * dimensions.height),
        ),
      ),
    );
    notify('Hide-all layer mask added.');
  }, [document?.canvas, notify, runCommand, selectedLayer]);

  const addRectangularVectorMask = useCallback(() => {
    if (!document || !selectedLayer) return;
    const dimensions =
      selectedLayer.kind === 'adjustment' || selectedLayer.kind === 'group'
        ? document.canvas
        : renderableLayerDimensions(selectedLayer);
    if (!dimensions) return;
    const left = dimensions.width * 0.2;
    const top = dimensions.height * 0.2;
    const right = dimensions.width * 0.8;
    const bottom = dimensions.height * 0.8;
    runCommand(
      setLayerVectorMask(
        selectedLayer.id,
        createVectorMask(dimensions.width, dimensions.height, [
          { type: 'move', x: left, y: top },
          { type: 'line', x: right, y: top },
          { type: 'line', x: right, y: bottom },
          { type: 'line', x: left, y: bottom },
          { type: 'close' },
        ]),
      ),
    );
    notify('Editable rectangular vector mask added.');
  }, [document, notify, runCommand, selectedLayer]);

  const commitVectorMaskDensity = useCallback(
    (value: number) => {
      setVectorMaskDensityDraft(value);
      if (
        !selectedLayer ||
        selectedLayer.vectorMask === null ||
        Math.round(selectedLayer.vectorMask.density * 100) === value
      ) {
        return;
      }
      runCommand(setLayerVectorMaskDensity(selectedLayer.id, value / 100));
    },
    [runCommand, selectedLayer],
  );

  const updateVectorMaskCommand = useCallback(
    (index: number, command: PathCommand) => {
      if (!selectedLayer || selectedLayer.vectorMask === null) return;
      const commands = selectedLayer.vectorMask.commands.map((candidate, commandIndex) =>
        commandIndex === index ? command : candidate,
      );
      runCommand(
        setLayerVectorMask(selectedLayer.id, {
          ...selectedLayer.vectorMask,
          commands,
        }),
      );
    },
    [runCommand, selectedLayer],
  );

  const previewMaskDensity = useCallback(
    (value: number) => {
      setMaskDensityDraft(value);
      if (!document || !selectedLayer?.rasterMask) return;
      if (maskDensityEditingRef.current) {
        setPreviewDocument(setLayerMaskDensity(selectedLayer.id, value / 100).execute(document));
      } else {
        setPreviewDocument(null);
        runCommand(setLayerMaskDensity(selectedLayer.id, value / 100));
      }
    },
    [document, runCommand, selectedLayer],
  );

  const commitMaskDensity = useCallback(
    (value: number) => {
      if (!maskDensityEditingRef.current) return;
      maskDensityEditingRef.current = false;
      setPreviewDocument(null);
      setMaskDensityDraft(value);
      if (
        !selectedLayer ||
        selectedLayer.rasterMask === null ||
        Math.round(selectedLayer.rasterMask.density * 100) === value
      )
        return;
      runCommand(setLayerMaskDensity(selectedLayer.id, value / 100));
    },
    [runCommand, selectedLayer],
  );

  const paintCommand = useCallback(
    (base: ImageDocument, layer: Layer, gesture: Extract<Gesture, { kind: 'paint' }>) => {
      const worldTransform =
        layer.kind === 'group'
          ? identityLayerTransform()
          : (findLayerWorldTransform(base, layer.id) ?? layer.transform);
      if (gesture.target === 'mask') {
        if (layer.kind === 'adjustment') return null;
        if (layer.rasterMask === null) return null;
        const patch = paintMaskStroke(
          layer.rasterMask,
          worldTransform,
          gesture.points,
          {
            size: brushSize,
            hardness: 0.85,
            opacity: brushOpacity / 100,
            mode: tool === 'eraser' ? 'conceal' : 'reveal',
          },
          base.selection,
        );
        const dirtyRegion = documentRegionForLocalPatch(patch, worldTransform, base.canvas);
        return patch.width > 0 && dirtyRegion
          ? { command: patchLayerRasterMask(layer.id, patch), dirtyRegion }
          : null;
      }
      if (layer.kind !== 'pixel') return null;
      const patch = paintStroke(
        layer,
        gesture.points,
        {
          size: brushSize,
          hardness: 0.85,
          opacity: brushOpacity / 100,
          color: hexColor(brushColor),
          mode: tool === 'eraser' ? 'erase' : 'source-over',
        },
        base.selection,
        worldTransform,
      );
      const dirtyRegion = documentRegionForLocalPatch(patch, worldTransform, base.canvas);
      return patch.width > 0 && dirtyRegion
        ? { command: patchPixels(layer.id, patch), dirtyRegion }
        : null;
    },
    [brushColor, brushOpacity, brushSize, tool],
  );

  useEffect(() => {
    if (tool !== 'pen' && pathDraft.length > 0) setPathDraft([]);
  }, [pathDraft.length, tool]);

  const commandRegistry = createCommandRegistry([
    {
      id: 'file.new',
      label: 'New',
      menu: 'file',
      shortcut: 'Ctrl+N',
      enabled: !smartObjectEditSession,
      execute: createNew,
    },
    {
      id: 'file.open',
      label: 'Open',
      menu: 'file',
      shortcut: 'Ctrl+O',
      enabled: !smartObjectEditSession,
      execute: () => fileRef.current?.click(),
    },
    {
      id: 'file.save',
      label: 'Save document',
      menu: 'file',
      shortcut: 'Ctrl+S',
      enabled: Boolean(document) && !busy && !smartObjectEditSession,
      execute: () => void saveNativeDocument(),
    },
    {
      id: 'file.export-png',
      label: 'Export PNG',
      menu: 'file',
      enabled: Boolean(document) && !busy,
      separatorBefore: true,
      execute: () => void exportPng(),
    },
    {
      id: 'file.export-selection-png',
      label: 'Export Selection PNG',
      menu: 'file',
      enabled: Boolean(document && selectionBounds(document)) && !busy,
      execute: () => void exportSelectionPng(),
    },
    {
      id: 'file.forget',
      label: 'Forget local',
      menu: 'file',
      enabled: Boolean(document) && !smartObjectEditSession,
      separatorBefore: true,
      execute: forgetDocument,
    },
    {
      id: 'edit.undo',
      label: 'Undo',
      menu: 'edit',
      shortcut: 'Ctrl+Z',
      enabled: Boolean(history?.canUndo),
      execute: undo,
    },
    {
      id: 'edit.redo',
      label: 'Redo',
      menu: 'edit',
      shortcut: 'Ctrl+Shift+Z',
      enabled: Boolean(history?.canRedo),
      execute: redo,
    },
    {
      id: 'edit.transform',
      label: 'Free Transform',
      menu: 'edit',
      shortcut: 'Ctrl+T',
      enabled: Boolean(selectedLayer && renderableLayerDimensions(selectedLayer)),
      execute: () => setTool('transform'),
    },
    ...IMAGE_ADJUSTMENT_DEFINITIONS.map((definition) => ({
      id: definition.id,
      label: definition.label,
      menu: 'image' as const,
      submenu: 'image.adjustments',
      order: definition.order,
      shortcut: 'shortcut' in definition ? definition.shortcut : undefined,
      separatorBefore: 'separatorBefore' in definition ? definition.separatorBefore : undefined,
      enabled: selectedLayer?.kind === 'pixel' && !busy,
      execute: () =>
        !isImmediateImageAdjustment(definition.kind)
          ? openContextualFilterOperation(
              definition.kind,
              'destructive',
              window.document.querySelector<HTMLButtonElement>('#menu-image') ?? undefined,
            )
          : void applyFilter({ ...filterDraft, kind: definition.kind }),
    })),
    {
      id: 'image.size',
      label: 'Image Size…',
      menu: 'image',
      shortcut: 'Ctrl+Alt+I',
      enabled: Boolean(document) && !busy,
      execute: () => openImageSize(),
    },
    {
      id: 'image.canvas-size',
      label: 'Canvas Size…',
      menu: 'image',
      shortcut: 'Ctrl+Alt+C',
      enabled: Boolean(document) && !busy,
      execute: () => openCanvasSize(),
    },
    {
      id: 'image.reveal-all',
      label: 'Reveal All',
      menu: 'image',
      enabled: Boolean(document) && !busy,
      execute: revealEntireDocument,
    },
    {
      id: 'layer.new',
      label: 'New Layer',
      menu: 'layer',
      order: 10,
      shortcut: 'Ctrl+Shift+N',
      enabled: Boolean(document),
      execute: addBlankLayer,
    },
    {
      id: 'layer.duplicate',
      label: 'Duplicate Layer',
      menu: 'layer',
      order: 200,
      shortcut: 'Ctrl+J',
      enabled: Boolean(selectedLayer),
      execute: duplicateSelectedLayer,
    },
    {
      id: 'layer.group',
      label: 'Group Layers',
      menu: 'layer',
      order: 210,
      enabled: selectedLayerIds.length > 0,
      execute: groupSelectedLayers,
    },
    {
      id: 'layer.ungroup',
      label: 'Ungroup Layers',
      menu: 'layer',
      order: 220,
      enabled: selectedLayer?.kind === 'group',
      execute: ungroupSelectedLayer,
    },
    {
      id: 'layer.mask',
      label: 'Layer Mask / Reveal Selection',
      menu: 'layer',
      enabled: Boolean(selectedLayer && document?.selection),
      execute: addLayerMaskFromSelection,
    },
    ...ADJUSTMENT_LAYER_DEFINITIONS.map((definition) => ({
      id: definition.id,
      label: definition.label,
      menu: 'layer' as const,
      submenu: 'layer.new-adjustment',
      order: definition.order,
      enabled: Boolean(document) && !busy,
      execute: () => addAdjustment(definition.type),
    })),
    {
      id: 'layer.delete',
      label: 'Delete Layer',
      menu: 'layer',
      enabled: selectedLayerIds.length > 0,
      execute: deleteSelectedLayer,
    },
    {
      id: 'layer.flatten',
      label: 'Flatten Image',
      menu: 'layer',
      enabled: Boolean(document?.layers.length),
      execute: flattenImage,
    },
    {
      id: 'type.new',
      label: 'New Text Layer',
      menu: 'type',
      shortcut: 'T',
      enabled: Boolean(document),
      execute: addText,
    },
    {
      id: 'select.all',
      label: 'All',
      menu: 'select',
      shortcut: 'Ctrl+A',
      enabled: Boolean(document),
      execute: () => runCommand(selectAll()),
    },
    {
      id: 'select.deselect',
      label: 'Deselect',
      menu: 'select',
      shortcut: 'Ctrl+D',
      enabled: Boolean(document?.selection),
      execute: () => runCommand(setSelection(null)),
    },
    {
      id: 'select.inverse',
      label: 'Inverse',
      menu: 'select',
      shortcut: 'Ctrl+Shift+I',
      enabled: Boolean(document?.selection),
      execute: () => runCommand(invertSelection()),
    },
    {
      id: 'filter.last',
      label: lastFilter ? 'Last Filter: Gaussian Blur' : 'Last Filter',
      menu: 'filter',
      order: 10,
      shortcut: 'Ctrl+F',
      enabled:
        Boolean(lastFilter) &&
        !busy &&
        (selectedLayer?.kind === 'pixel' || selectedLayer?.kind === 'smart-object'),
      execute: () => lastFilter && void applyFilter(lastFilter.draft),
    },
    {
      id: 'filter.convert-smart',
      label: 'Convert for Smart Filters',
      menu: 'filter',
      order: 20,
      separatorBefore: true,
      enabled:
        !busy && Boolean(selectedLayer && ['pixel', 'text', 'shape'].includes(selectedLayer.kind)),
      execute: convertSelectedToSmartObject,
    },
    ...FILTER_MENU_DEFINITIONS.map((definition) => ({
      id: definition.id,
      label: definition.label,
      menu: 'filter' as const,
      submenu: definition.submenu,
      order: definition.order,
      enabled:
        !busy &&
        supportsSmartFilter(definition.kind) &&
        (selectedLayer?.kind === 'pixel' || selectedLayer?.kind === 'smart-object'),
      execute: () =>
        openContextualFilterOperation(
          definition.kind,
          selectedLayer?.kind === 'smart-object' ? 'smart' : 'destructive',
        ),
    })),
    {
      id: 'view.fit',
      label: 'Fit on Screen',
      menu: 'view',
      shortcut: 'Ctrl+0',
      enabled: Boolean(document),
      execute: fitDocument,
    },
    {
      id: 'view.actual-size',
      label: '100%',
      menu: 'view',
      enabled: Boolean(document),
      execute: () => setViewport((view) => ({ ...view, zoom: 1 })),
    },
    {
      id: 'view.show-guides',
      label: 'Show Guides',
      menu: 'view',
      order: 40,
      separatorBefore: true,
      enabled: Boolean(document),
      checked: showGuides,
      execute: () => setShowGuides((value) => !value),
    },
    {
      id: 'view.show-grid',
      label: 'Show Grid',
      menu: 'view',
      order: 41,
      enabled: Boolean(document),
      checked: showGrid,
      execute: () => setShowGrid((value) => !value),
    },
    {
      id: 'view.smart-guides',
      label: 'Smart Guides',
      menu: 'view',
      order: 42,
      enabled: Boolean(document),
      checked: showSmartGuides,
      execute: () => setShowSmartGuides((value) => !value),
    },
    {
      id: 'view.new-guide-vertical',
      label: 'New Vertical Guide',
      menu: 'view',
      order: 43,
      separatorBefore: true,
      enabled: Boolean(document),
      execute: () => {
        if (!document) return;
        const position = Math.round(document.canvas.width / 2);
        runCommand(
          addDocumentGuide({
            id: makeId('guide'),
            direction: 'vertical',
            position,
          }),
        );
      },
    },
    {
      id: 'view.new-guide-horizontal',
      label: 'New Horizontal Guide',
      menu: 'view',
      order: 44,
      enabled: Boolean(document),
      execute: () => {
        if (!document) return;
        const position = Math.round(document.canvas.height / 2);
        runCommand(
          addDocumentGuide({
            id: makeId('guide'),
            direction: 'horizontal',
            position,
          }),
        );
      },
    },
    {
      id: 'view.clear-guides',
      label: 'Clear Guides',
      menu: 'view',
      order: 45,
      enabled: Boolean(document && document.guides.length > 0),
      execute: () => {
        if (!document?.guides.length) return;
        runCommand(clearDocumentGuides());
      },
    },
    {
      id: 'window.panels',
      label: panelCollapsed ? 'Show Panels' : 'Hide Panels',
      menu: 'window',
      enabled: true,
      checked: !panelCollapsed,
      execute: () => setPanelCollapsed((value) => !value),
    },
    ...workspaceState.groups
      .map((group) => ({
        id: `window.panel.${group.id}`,
        label: `${group.id.charAt(0).toUpperCase()}${group.id.slice(1)} Panels`,
        menu: 'window' as const,
        enabled: true,
        checked: group.visible,
        execute: () =>
          setWorkspaceState((current) =>
            updateDockGroup(current, group.id, { visible: !group.visible }),
          ),
      }))
      .flatMap((command) => {
        const group = workspaceState.groups.find(
          (candidate) => command.id === `window.panel.${candidate.id}`,
        );
        if (!group) return [command];
        return [
          command,
          {
            id: `window.panel.${group.id}.toggle-collapse`,
            label: group.collapsed ? 'Expand Panel Group' : 'Collapse Panel Group',
            menu: 'window' as const,
            enabled: group.visible,
            execute: () =>
              setWorkspaceState((current) =>
                updateDockGroup(current, group.id, { collapsed: !group.collapsed }),
              ),
          },
          {
            id: `window.panel.${group.id}.reset`,
            label: 'Reset Panel Group',
            menu: 'window' as const,
            enabled: true,
            execute: () => setWorkspaceState((current) => resetDockGroup(current, group.id)),
          },
        ];
      }),
    {
      id: 'window.reset-workspace',
      label: 'Reset Professional Editing Workspace',
      menu: 'window',
      enabled: true,
      separatorBefore: true,
      execute: () => setWorkspaceResetOpen(true),
    },
  ]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (target?.matches('input, textarea, select')) return;
      const mod = event.ctrlKey || event.metaKey;
      if (event.key === 'Escape' && gestureRef.current?.kind === 'path-handle') {
        event.preventDefault();
        cancelPathHandle();
      } else if (tool === 'pen' && event.key === 'Escape' && pathDraft.length > 0) {
        event.preventDefault();
        setPathDraft([]);
        notify('Path draft cancelled.');
      } else if (tool === 'pen' && event.key === 'Enter' && pathDraft.length >= 2) {
        event.preventDefault();
        finishPath(false);
      } else if (tool === 'crop' && event.key === 'Escape') {
        event.preventDefault();
        cancelCrop();
      } else if (tool === 'crop' && event.key === 'Enter' && cropRectangle) {
        event.preventDefault();
        applyCrop();
      } else if (event.key === 'Escape' && gestureRef.current?.kind === 'free-transform') {
        event.preventDefault();
        gestureRef.current = null;
        setPreviewDocument(null);
        setTransformGestureStatus('Free Transform cancelled.');
      } else if (routeKeyboardCommand(event, commandRegistry)) {
        return;
      } else if (!mod && event.key.toLowerCase() === 'd') {
        setBrushColor('#000000');
        setBackgroundColor('#ffffff');
      } else if (!mod && event.key.toLowerCase() === 'x') {
        setBrushColor(backgroundColor);
        setBackgroundColor(brushColor);
      } else if (!mod && (event.key === '[' || event.key === ']')) {
        setBrushSize((size) => Math.max(1, Math.min(512, size + (event.key === '[' ? -2 : 2))));
      } else if (!mod && toolForShortcut(event.key, tool, event.shiftKey)) {
        const next = toolForShortcut(event.key, tool, event.shiftKey);
        if (next === 'crop') activateCrop();
        else if (next) setTool(next);
      } else if (event.key === 'Escape' && document?.selection) {
        event.preventDefault();
        runCommand(setSelection(null));
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [
    activateCrop,
    applyCrop,
    backgroundColor,
    brushColor,
    cancelCrop,
    cancelPathHandle,
    commandRegistry,
    cropRectangle,
    document?.selection,
    finishPath,
    notify,
    pathDraft.length,
    runCommand,
    tool,
  ]);

  useEffect(() => {
    const canvas = canvasRef.current;
    const stage = stageRef.current;
    if (!canvas || !stage) return;
    const pendingInvalidation = renderInvalidationRef.current;
    const invalidationMatches = pendingInvalidation?.document === renderedDocument;
    if (invalidationMatches) renderInvalidationRef.current = null;
    const initialDirtyRegions = invalidationMatches ? pendingInvalidation.regions : undefined;
    const draw = (requestedDirtyRegions?: readonly PixelRegion[]) => {
      const ratio = window.devicePixelRatio || 1;
      const width = Math.max(1, Math.floor(stage.clientWidth * ratio));
      const height = Math.max(1, Math.floor(stage.clientHeight * ratio));
      let resized = false;
      if (canvas.width !== width || canvas.height !== height) {
        canvas.width = width;
        canvas.height = height;
        resized = true;
      }
      const context = canvas.getContext('2d');
      if (!context) return;
      const dirtyRegions = resized ? undefined : requestedDirtyRegions;
      canvas.dataset.renderMode = dirtyRegions ? 'dirty' : 'full';
      canvas.dataset.dirtyRegionCount = String(dirtyRegions?.length ?? 0);
      canvas.dataset.renderSequence =
        `${canvas.dataset.renderSequence ?? ''}${dirtyRegions ? 'D' : 'F'}`.slice(-24);
      canvas.dataset.renderReason = resized
        ? 'resize'
        : dirtyRegions
          ? 'invalidation'
          : pendingInvalidation
            ? 'pending-mismatch'
            : 'full-invalidation';
      if (dirtyRegions) {
        context.save();
        context.setTransform(1, 0, 0, 1, 0, 0);
        context.beginPath();
        for (const region of dirtyRegions) {
          const left = Math.floor((viewport.panX + region.x * viewport.zoom) * ratio);
          const top = Math.floor((viewport.panY + region.y * viewport.zoom) * ratio);
          const right = Math.ceil(
            (viewport.panX + (region.x + region.width) * viewport.zoom) * ratio,
          );
          const bottom = Math.ceil(
            (viewport.panY + (region.y + region.height) * viewport.zoom) * ratio,
          );
          context.rect(left, top, right - left, bottom - top);
        }
        context.clip();
      }
      context.fillStyle = '#080c0b';
      context.fillRect(0, 0, width, height);
      if (!renderedDocument) {
        if (dirtyRegions) context.restore();
        return;
      }
      context.save();
      context.shadowColor = 'rgba(0, 0, 0, 0.45)';
      context.shadowBlur = 24 * ratio;
      context.shadowOffsetY = 8 * ratio;
      context.fillStyle = '#151b1a';
      context.fillRect(
        viewport.panX * ratio - 1,
        viewport.panY * ratio - 1,
        renderedDocument.canvas.width * viewport.zoom * ratio + 2,
        renderedDocument.canvas.height * viewport.zoom * ratio + 2,
      );
      context.restore();
      if (compositeTileCacheDocumentIdRef.current !== renderedDocument.id) {
        compositeTileCacheRef.current.clear();
        compositeTileCacheDocumentIdRef.current = renderedDocument.id;
        getCompositeWorkerScheduler().clear();
        compositeWorkerScheduledDocRef.current = null;
        compositeWorkerSeededGenerationRef.current = -1;
      }
      let precomposedPixels: Uint8ClampedArray | undefined;
      let warmAssembleOnly = false;
      let seededFromWorker = false;
      let skippedOversizeWorkerSeed = false;
      const advancedComposite = requiresDeterministicComposite(renderedDocument.layers);
      const compositeScheduler = getCompositeWorkerScheduler();
      const compositeMipLevel = compositeMipLevelForZoom(viewport.zoom * ratio);
      if (advancedComposite && channelPreview.mode === 'composite') {
        const viewportRegion = CompositeTileCache.viewportDocumentRegion(
          {
            zoom: viewport.zoom * ratio,
            panX: viewport.panX * ratio,
            panY: viewport.panY * ratio,
          },
          width,
          height,
          renderedDocument.canvas,
        );
        const warmth = compositeTileCacheRef.current.probeWarmth(renderedDocument, {
          viewportRegion,
          progressiveViewportOnly: true,
          mipLevel: compositeMipLevel,
        });
        const workerFrame = compositeScheduler.frameFor(
          renderedDocument,
          compositeScheduler.generation,
          compositeMipLevel,
        );
        const plan = planAdvancedCompositeRender({
          document: renderedDocument,
          fullyWarm: warmth.fullyWarm,
          workerFrame,
          scheduledDocument: compositeWorkerScheduledDocRef.current,
          seededGeneration: compositeWorkerSeededGenerationRef.current,
          workerInflight: compositeScheduler.inflight,
        });
        const seedFrame = plan.seedFrame;
        if (seedFrame) {
          // Populate dependency-keyed tiles from the worker frame so later paints
          // use warm main-thread assemble. Progressive frames only seed the
          // viewport they filled (transparent off-screen holes must not cache).
          if (shouldSeedCompositeWorkerFrame(seedFrame, compositeTileCacheRef.current.maxBytes)) {
            if (seedFrame.pixelRegion) {
              compositeTileCacheRef.current.seedRegion(
                renderedDocument,
                seedFrame.pixels,
                seedFrame.pixelRegion,
                seedFrame.mipLevel,
              );
            } else {
              compositeTileCacheRef.current.composite(renderedDocument, {
                referenceComposite: () => seedFrame.pixels,
                mipReferenceComposite: () => seedFrame.pixels,
                progressiveViewportOnly: seedFrame.progressiveViewportOnly === true,
                viewportRegion: seedFrame.viewportRegion ?? viewportRegion,
                mipLevel: seedFrame.mipLevel,
              });
            }
            seededFromWorker = true;
          } else {
            // A full frame larger than the cache can never become fully warm.
            // Display it directly and acknowledge the generation instead of
            // creating an endless seed/evict/reschedule loop.
            precomposedPixels = seedFrame.pixels;
            skippedOversizeWorkerSeed = true;
          }
          compositeWorkerSeededGenerationRef.current = seedFrame.generation;
        }
        precomposedPixels ??= plan.precomposedPixels;
        warmAssembleOnly = plan.warmAssembleOnly;
        if (plan.cancelInflight) {
          compositeScheduler.cancel();
          // Allow a later cold region (pan after cancel) to schedule again.
          compositeWorkerScheduledDocRef.current = null;
        }
        if (plan.clearScheduledDocument) {
          compositeWorkerScheduledDocRef.current = null;
        }
        if (plan.scheduleWorker) {
          compositeWorkerScheduledDocRef.current = renderedDocument;
          // Prefer progressive viewport cold fills on large documents so the
          // worker pays only for on-screen tiles first.
          compositeScheduler.schedule(renderedDocument, {
            viewportRegion,
            progressiveViewportOnly: true,
            mipLevel: compositeMipLevel,
            chainFullRefine: shouldChainFullCompositeRefine(
              renderedDocument.canvas,
              compositeTileCacheRef.current.maxBytes,
              compositeMipLevel,
            ),
          });
        }
      }
      renderDocument(context, renderedDocument, {
        clear: false,
        preview: channelPreview,
        viewport: {
          zoom: viewport.zoom * ratio,
          panX: viewport.panX * ratio,
          panY: viewport.panY * ratio,
        },
        checkerboard: {
          size: 10,
          light: '#eef2f1',
          dark: '#c8cfcd',
        },
        pixelSource: sparsePixelSource,
        dirtyRegions,
        compositeTileCache: compositeTileCacheRef.current,
        progressiveComposite: true,
        compositeMipLevel,
        precomposedPixels,
        warmAssembleOnly,
        guidesOverlay: {
          showGuides,
          showGrid,
          smartGuideLines: showSmartGuides ? activeSmartGuideLines : [],
        },
      });
      const cacheStats = compositeTileCacheRef.current.stats;
      canvas.dataset.compositeCacheWarm = cacheStats.lastComposeFullyWarm ? '1' : '0';
      canvas.dataset.compositeCacheHits = String(cacheStats.hits);
      canvas.dataset.compositeCacheMisses = String(cacheStats.misses);
      canvas.dataset.compositeCacheAllocatedBytes = String(cacheStats.allocatedBytes);
      canvas.dataset.compositeCacheTileCount = String(cacheStats.tileCount);
      canvas.dataset.compositeCacheUniqueSpatialTiles = String(cacheStats.uniqueSpatialTileCount);
      canvas.dataset.compositeCacheEvictions = String(cacheStats.evictions);
      canvas.dataset.compositeCacheMaxBytes = String(compositeTileCacheRef.current.maxBytes);
      canvas.dataset.compositeCacheViewportTiles = String(
        cacheStats.lastComposePrioritizedViewportTiles,
      );
      canvas.dataset.compositeWorkerInflight = compositeScheduler.inflight ? '1' : '0';
      canvas.dataset.compositeWorkerFrame = precomposedPixels || seededFromWorker ? '1' : '0';
      canvas.dataset.compositeWorkerSeeded = seededFromWorker ? '1' : '0';
      canvas.dataset.compositeWorkerOversizeSeedSkipped = skippedOversizeWorkerSeed ? '1' : '0';
      canvas.dataset.compositeWorkerWarmOnly = warmAssembleOnly ? '1' : '0';
      canvas.dataset.compositeWorkerGeneration = String(compositeScheduler.generation);
      canvas.dataset.compositeWorkerInputBytes = String(
        compositeScheduler.lastInputTransferByteLength,
      );
      canvas.dataset.compositeMipLevel = String(compositeMipLevel);
      canvas.dataset.compositeWorkerFrameWidth = String(compositeScheduler.latestFrame?.width ?? 0);
      canvas.dataset.compositeWorkerFrameHeight = String(
        compositeScheduler.latestFrame?.height ?? 0,
      );
      canvas.dataset.compositeDocumentWidth = String(renderedDocument.canvas.width);
      canvas.dataset.compositeDocumentHeight = String(renderedDocument.canvas.height);
      canvas.dataset.showGuides = showGuides ? '1' : '0';
      canvas.dataset.showGrid = showGrid ? '1' : '0';
      canvas.dataset.showSmartGuides = showSmartGuides ? '1' : '0';
      canvas.dataset.smartGuideLineCount = String(
        showSmartGuides ? activeSmartGuideLines.length : 0,
      );
      canvas.dataset.guideCount = String(renderedDocument.guides.length);
      canvas.dataset.hasGrid = renderedDocument.grid ? '1' : '0';
      const showSelection = channelView === 'composite' && tool !== 'crop';
      if (
        showSelection &&
        (selectionPreview || selectionMaskPreview || renderedDocument.selection)
      ) {
        context.save();
        context.setTransform(
          viewport.zoom * ratio,
          0,
          0,
          viewport.zoom * ratio,
          viewport.panX * ratio,
          viewport.panY * ratio,
        );
        context.setLineDash([4 / viewport.zoom, 4 / viewport.zoom]);
        context.lineWidth = 1 / viewport.zoom;
        context.strokeStyle = '#ffffff';
        context.beginPath();
        if (selectionPreview) {
          context.rect(
            selectionPreview.x,
            selectionPreview.y,
            selectionPreview.width,
            selectionPreview.height,
          );
        } else {
          const mask = selectionMaskPreview ?? renderedDocument.selection;
          for (const segment of mask ? selectionOutlineSegments(mask) : []) {
            context.moveTo(segment.x1, segment.y1);
            context.lineTo(segment.x2, segment.y2);
          }
        }
        context.stroke();
        context.lineDashOffset = 4 / viewport.zoom;
        context.strokeStyle = '#101716';
        context.stroke();
        context.restore();
      }
      if (showSelection && lassoPreview.length > 0) {
        context.save();
        context.setTransform(
          viewport.zoom * ratio,
          0,
          0,
          viewport.zoom * ratio,
          viewport.panX * ratio,
          viewport.panY * ratio,
        );
        context.beginPath();
        const first = lassoPreview[0];
        if (first) context.moveTo(first.x, first.y);
        for (const point of lassoPreview.slice(1)) context.lineTo(point.x, point.y);
        context.lineWidth = 1 / viewport.zoom;
        context.strokeStyle = '#ffffff';
        context.setLineDash([4 / viewport.zoom, 4 / viewport.zoom]);
        context.stroke();
        context.restore();
      }
      if (tool === 'pen' && pathDraft.length > 0) {
        context.save();
        context.setTransform(
          viewport.zoom * ratio,
          0,
          0,
          viewport.zoom * ratio,
          viewport.panX * ratio,
          viewport.panY * ratio,
        );
        context.beginPath();
        const first = pathDraft[0];
        if (first) context.moveTo(first.x, first.y);
        for (const point of pathDraft.slice(1)) context.lineTo(point.x, point.y);
        context.lineWidth = 2 / viewport.zoom;
        context.strokeStyle = '#17b897';
        context.stroke();
        const anchorSize = 7 / viewport.zoom;
        context.fillStyle = '#e7fffa';
        context.strokeStyle = '#07110f';
        context.lineWidth = 1 / viewport.zoom;
        for (const point of pathDraft) {
          context.fillRect(
            point.x - anchorSize / 2,
            point.y - anchorSize / 2,
            anchorSize,
            anchorSize,
          );
          context.strokeRect(
            point.x - anchorSize / 2,
            point.y - anchorSize / 2,
            anchorSize,
            anchorSize,
          );
        }
        context.restore();
      }
      if (tool === 'crop' && cropRectangle) {
        context.save();
        context.setTransform(
          viewport.zoom * ratio,
          0,
          0,
          viewport.zoom * ratio,
          viewport.panX * ratio,
          viewport.panY * ratio,
        );
        context.beginPath();
        context.rect(0, 0, renderedDocument.canvas.width, renderedDocument.canvas.height);
        context.rect(cropRectangle.x, cropRectangle.y, cropRectangle.width, cropRectangle.height);
        context.fillStyle = 'rgba(2, 7, 6, 0.62)';
        context.fill('evenodd');
        context.lineWidth = 1.5 / viewport.zoom;
        context.strokeStyle = '#6ee7d2';
        context.setLineDash([8 / viewport.zoom, 4 / viewport.zoom]);
        context.strokeRect(
          cropRectangle.x,
          cropRectangle.y,
          cropRectangle.width,
          cropRectangle.height,
        );
        context.restore();
      }
      if (dirtyRegions) context.restore();
    };
    draw(initialDirtyRegions);
    const observer = new ResizeObserver(() => {
      const ratio = window.devicePixelRatio || 1;
      const width = Math.max(1, Math.floor(stage.clientWidth * ratio));
      const height = Math.max(1, Math.floor(stage.clientHeight * ratio));
      if (canvas.width === width && canvas.height === height) return;
      draw();
    });
    observer.observe(stage);
    return () => observer.disconnect();
  }, [
    channelPreview,
    channelView,
    compositeWorkerEpoch,
    cropRectangle,
    getCompositeWorkerScheduler,
    lassoPreview,
    pathDraft,
    renderedDocument,
    showGrid,
    showGuides,
    showSmartGuides,
    activeSmartGuideLines,
    sparsePixelSource,
    revision,
    selectionPreview,
    selectionMaskPreview,
    tool,
    viewport,
  ]);

  return (
    <EditorShell rightDockWidth={panelCollapsed ? 0 : primaryDockWidth + secondaryDockWidth}>
      <ApplicationMenuBar registry={commandRegistry} />

      <ToolOptionsBar
        tool={tool}
        status={
          tool === 'crop'
            ? cropRectangle
              ? `Crop ${String(cropRectangle.width)} × ${String(cropRectangle.height)} at ${String(cropRectangle.x)}, ${String(cropRectangle.y)}. Enter applies; Escape cancels.`
              : 'Drag on the canvas to define a crop.'
            : tool === 'transform'
              ? selectedLayer && renderableLayerDimensions(selectedLayer)
                ? 'Free Transform active for the selected renderable layer.'
                : 'Select a renderable layer to use Free Transform.'
              : tool === 'pen'
                ? `${String(pathDraft.length)} path anchors. Click to add; Enter finishes open; Escape cancels.`
                : editTarget === 'mask'
                  ? selectedMask?.enabled
                    ? 'Editing layer mask'
                    : 'Editing layer mask (disabled); enable it to preview the result.'
                  : 'Editing layer pixels'
        }
        documentOpen={document !== null}
        selectionActive={Boolean(document?.selection)}
        brushSize={brushSize}
        brushOpacity={brushOpacity}
        brushColor={brushColor}
        editTarget={editTarget}
        selectionMode={selectionMode}
        selectionRadius={selectionRadius}
        wandTolerance={wandTolerance}
        wandContiguous={wandContiguous}
        cropRectangle={cropRectangle}
        pathAnchorCount={pathDraft.length}
        zoom={viewport.zoom}
        busy={busy}
        operationProgress={operationProgress}
        onFit={fitDocument}
        onActualSize={() => setViewport((view) => ({ ...view, zoom: 1 }))}
        onBrushSizeChange={setBrushSize}
        onBrushOpacityChange={setBrushOpacity}
        onBrushColorChange={setBrushColor}
        onSelectionModeChange={setSelectionMode}
        onDeselect={() => runCommand(setSelection(null))}
        onWandToleranceChange={setWandTolerance}
        onWandContiguousChange={setWandContiguous}
        onSelectionRadiusChange={setSelectionRadius}
        onRefineSelection={applySelectionRefinement}
        onCropRectangleChange={setCropRectangle}
        onApplyCrop={applyCrop}
        onCancelCrop={cancelCrop}
        onFinishPath={finishPath}
        onCancelPath={() => setPathDraft([])}
        onZoomChange={(zoom) => setViewport((view) => ({ ...view, zoom }))}
        onCancelOperation={cancelLongOperation}
      />
      <DocumentTabStrip
        name={document?.name ?? null}
        width={document?.canvas.width}
        height={document?.canvas.height}
        saving={saveState === 'saving'}
        specialContext={smartObjectEditSession ? 'Editing' : undefined}
        onClose={forgetDocument}
        onContextMenu={(event) => {
          event.preventDefault();
          setContextMenu({
            x: event.clientX,
            y: event.clientY,
            commands: ['file.save', 'file.forget'],
          });
        }}
      />

      <WorkspaceLayout>
        <ToolRail
          documentOpen={document !== null}
          transformAvailable={Boolean(
            selectedLayer && renderableLayerDimensions(selectedLayer) !== null,
          )}
          tool={tool}
          onSelectTool={setTool}
          onActivateCrop={activateCrop}
          onTogglePanels={() => setPanelCollapsed((value) => !value)}
          foregroundColor={brushColor}
          backgroundColor={backgroundColor}
          onForegroundColorChange={setBrushColor}
          onBackgroundColorChange={setBackgroundColor}
        />

        <CanvasWorkspace ref={stageRef}>
          {document ? (
            <GuideRulers
              enabled={showGuides}
              viewport={viewport}
              documentWidth={document.canvas.width}
              documentHeight={document.canvas.height}
              onCreateGuide={(direction, position) => {
                runCommand(
                  addDocumentGuide({
                    id: makeId('guide'),
                    direction,
                    position,
                  }),
                );
              }}
            />
          ) : null}
          <canvas
            ref={canvasRef}
            aria-label={document ? `${document.name} canvas` : 'No document open'}
            aria-describedby="edit-target-status"
            data-tool={tool}
            data-document-x={viewport.panX}
            data-document-y={viewport.panY}
            data-document-width={(document?.canvas.width ?? 0) * viewport.zoom}
            data-document-height={(document?.canvas.height ?? 0) * viewport.zoom}
            data-document-zoom={viewport.zoom}
            tabIndex={0}
            onContextMenu={(event) => {
              event.preventDefault();
              setContextMenu({
                x: event.clientX,
                y: event.clientY,
                commands: ['select.deselect', 'edit.transform', 'view.fit', 'view.actual-size'],
              });
            }}
            onPointerDown={(event) => {
              if (channelView !== 'composite' && tool !== 'pan') {
                notify('Channel preview is read-only. Select Composite color to edit.', 'error');
                return;
              }
              event.currentTarget.setPointerCapture(event.pointerId);
              const point = canvasPoint(event, viewport, guideSnap);
              flushSmartGuideOverlay();
              // Prefer guide drag when Show Guides is on and pointer is near a guide.
              if (
                document &&
                showGuides &&
                document.guides.length > 0 &&
                (tool === 'pan' || tool === 'move')
              ) {
                const hit = hitTestGuide(
                  point,
                  document.guides,
                  snapThresholdInDocumentPixels(6, viewport.zoom),
                );
                if (hit) {
                  if (event.detail >= 2) {
                    runCommand(removeDocumentGuide(hit.guide.id));
                    return;
                  }
                  gestureRef.current = {
                    kind: 'move-guide',
                    guideId: hit.guide.id,
                    direction: hit.guide.direction,
                  };
                  return;
                }
              }
              if (tool === 'pan') {
                gestureRef.current = {
                  kind: 'pan',
                  x: event.clientX,
                  y: event.clientY,
                  panX: viewport.panX,
                  panY: viewport.panY,
                };
              } else if (tool === 'select' && document) {
                gestureRef.current = { kind: 'select', start: point, current: point };
                setSelectionPreview(selectionRectangle(point, point));
              } else if (tool === 'ellipse' && document) {
                gestureRef.current = { kind: 'ellipse', start: point, current: point };
                setSelectionMaskPreview(
                  ellipticalSelection(
                    document.canvas.width,
                    document.canvas.height,
                    selectionRectangle(point, point),
                  ),
                );
              } else if (tool === 'lasso' && document) {
                const points = [point];
                gestureRef.current = { kind: 'lasso', points };
                setLassoPreview(points);
              } else if (tool === 'wand' && document) {
                selectWithMagicWand(point);
                event.currentTarget.releasePointerCapture(event.pointerId);
              } else if (tool === 'pen' && document) {
                setPathDraft((current) => [
                  ...current,
                  {
                    x: Math.max(0, Math.min(document.canvas.width, point.x)),
                    y: Math.max(0, Math.min(document.canvas.height, point.y)),
                  },
                ]);
                event.currentTarget.releasePointerCapture(event.pointerId);
              } else if (tool === 'crop' && document) {
                gestureRef.current = {
                  kind: 'crop',
                  start: point,
                  current: point,
                  original: cropRectangle,
                };
                setCropRectangle(selectionRectangle(point, point));
              } else if (tool === 'transform' && transformOverlay && document) {
                const local = transformPoint(
                  invertLayerTransform(transformOverlay.snapshot.worldTransform),
                  point.x,
                  point.y,
                );
                if (
                  local.x >= 0 &&
                  local.y >= 0 &&
                  local.x <= transformOverlay.dimensions.width &&
                  local.y <= transformOverlay.dimensions.height
                ) {
                  gestureRef.current = {
                    kind: 'free-transform',
                    layerId: transformOverlay.layer.id,
                    handle: 'center',
                    snapshot: transformOverlay.snapshot,
                    start: point,
                    changed: false,
                  };
                  setTransformGestureStatus('Moving transformed layer…');
                } else {
                  event.currentTarget.releasePointerCapture(event.pointerId);
                  notify('Drag inside the transformed layer or use one of its handles.');
                }
              } else if (tool === 'move' && selectedLayer && document) {
                const worldTransform =
                  findLayerWorldTransform(document, selectedLayer.id) ?? selectedLayer.transform;
                const parentTransform = multiplyLayerTransforms(
                  worldTransform,
                  invertLayerTransform(selectedLayer.transform),
                );
                gestureRef.current = {
                  kind: 'move',
                  start: point,
                  origin: selectedLayer.transform,
                  parentInverse: invertLayerTransform(parentTransform),
                  layerId: selectedLayer.id,
                };
              } else if (
                (tool === 'brush' || tool === 'eraser') &&
                selectedLayer &&
                (selectedLayer.kind === 'pixel' ||
                  (editTarget === 'mask' && selectedLayer.kind !== 'adjustment'))
              ) {
                if (editTarget === 'mask' && selectedLayer.rasterMask === null) {
                  notify('Add a raster mask before painting the mask.', 'error');
                  return;
                }
                gestureRef.current = {
                  kind: 'paint',
                  layerId: selectedLayer.id,
                  target: editTarget,
                  points: [
                    { ...point, pressure: event.pointerType === 'pen' ? event.pressure || 1 : 1 },
                  ],
                };
              } else {
                notify('Select a pixel layer or an editable layer mask before painting.', 'error');
              }
            }}
            onPointerMove={(event) => {
              const gesture = gestureRef.current;
              if (!gesture) return;
              if (gesture.kind === 'free-transform') {
                previewFreeTransform(event);
                return;
              }
              const point = canvasPoint(event, viewport, guideSnap);
              flushSmartGuideOverlay();
              if (gesture.kind === 'move-guide' && document) {
                const position =
                  gesture.direction === 'vertical'
                    ? Math.round(Math.min(document.canvas.width, Math.max(0, point.x)))
                    : Math.round(Math.min(document.canvas.height, Math.max(0, point.y)));
                setPreviewDocument(moveDocumentGuide(gesture.guideId, position).execute(document));
              } else if (gesture.kind === 'pan') {
                setViewport((view) => ({
                  ...view,
                  panX: gesture.panX + event.clientX - gesture.x,
                  panY: gesture.panY + event.clientY - gesture.y,
                }));
              } else if (gesture.kind === 'select') {
                gesture.current = point;
                setSelectionPreview(selectionRectangle(gesture.start, point));
              } else if (gesture.kind === 'ellipse' && document) {
                gesture.current = point;
                setSelectionMaskPreview(
                  ellipticalSelection(
                    document.canvas.width,
                    document.canvas.height,
                    selectionRectangle(gesture.start, point),
                  ),
                );
              } else if (gesture.kind === 'lasso') {
                const previous = gesture.points.at(-1);
                if (!previous || Math.hypot(point.x - previous.x, point.y - previous.y) >= 0.5) {
                  gesture.points.push(point);
                  setLassoPreview([...gesture.points]);
                }
              } else if (gesture.kind === 'crop') {
                gesture.current = point;
                setCropRectangle(selectionRectangle(gesture.start, point));
              } else if (gesture.kind === 'move' && document) {
                const { x, y } = movedLayerPosition(gesture, point);
                setPreviewDocument(setLayerTranslation(gesture.layerId, x, y).execute(document));
              } else if (gesture.kind === 'paint' && document) {
                const layer = findLayer(document, gesture.layerId);
                if (!layer || layer.kind === 'adjustment') return;
                gesture.points.push({
                  ...point,
                  pressure: event.pointerType === 'pen' ? event.pressure || 1 : 1,
                });
                const operation = paintCommand(document, layer, gesture);
                if (operation) {
                  const next = operation.command.execute(document);
                  renderInvalidationRef.current = {
                    document: next,
                    regions: [operation.dirtyRegion],
                  };
                  setPreviewDocument(next);
                }
              }
            }}
            onPointerUp={(event) => {
              if (gestureRef.current?.kind === 'free-transform') {
                finishFreeTransform(event);
                return;
              }
              if (event.currentTarget.hasPointerCapture(event.pointerId))
                event.currentTarget.releasePointerCapture(event.pointerId);
              const gesture = gestureRef.current;
              gestureRef.current = null;
              clearSmartGuideOverlay();
              if (!gesture || !document) return;
              const point = canvasPoint(event, viewport, guideSnap);
              if (gesture.kind === 'move-guide') {
                const position =
                  gesture.direction === 'vertical'
                    ? Math.round(Math.min(document.canvas.width, Math.max(0, point.x)))
                    : Math.round(Math.min(document.canvas.height, Math.max(0, point.y)));
                runCommand(moveDocumentGuide(gesture.guideId, position));
                setPreviewDocument(null);
              } else if (gesture.kind === 'select') {
                const rectangle = selectionRectangle(gesture.start, point);
                runCommand(combineRectangularSelection(rectangle, selectionMode));
              } else if (gesture.kind === 'ellipse') {
                const selection = ellipticalSelection(
                  document.canvas.width,
                  document.canvas.height,
                  selectionRectangle(gesture.start, point),
                );
                runCommand(combineSelection(selection, selectionMode));
              } else if (gesture.kind === 'lasso') {
                gesture.points.push(point);
                if (gesture.points.length >= 3) {
                  runCommand(
                    combineSelection(
                      polygonalSelection(
                        document.canvas.width,
                        document.canvas.height,
                        gesture.points,
                      ),
                      selectionMode,
                    ),
                  );
                }
              } else if (gesture.kind === 'crop') {
                setCropRectangle(selectionRectangle(gesture.start, point));
              } else if (gesture.kind === 'move') {
                const { x, y } = movedLayerPosition(gesture, point);
                runCommand(setLayerTranslation(gesture.layerId, x, y));
              } else if (gesture.kind === 'paint') {
                const layer = findLayer(document, gesture.layerId);
                if (layer && layer.kind !== 'adjustment') {
                  gesture.points.push({
                    ...point,
                    pressure: event.pointerType === 'pen' ? event.pressure || 1 : 1,
                  });
                  const operation = paintCommand(document, layer, gesture);
                  if (operation) runCommand(operation.command, [operation.dirtyRegion]);
                }
              }
              setPreviewDocument(null);
              setSelectionPreview(null);
              setSelectionMaskPreview(null);
              setLassoPreview([]);
            }}
            onPointerCancel={() => {
              if (gestureRef.current?.kind === 'free-transform') cancelFreeTransform();
              else {
                if (gestureRef.current?.kind === 'crop') {
                  setCropRectangle(gestureRef.current.original);
                }
                gestureRef.current = null;
                setPreviewDocument(null);
                setSelectionPreview(null);
                setSelectionMaskPreview(null);
                setLassoPreview([]);
              }
            }}
            onWheel={(event) => {
              if (!document) return;
              event.preventDefault();
              setViewport((view) => ({
                ...view,
                zoom: clampZoom(view.zoom * (event.deltaY < 0 ? 1.1 : 0.9)),
              }));
            }}
          />
          {transformOverlay && (
            <div
              className="transform-overlay"
              role="group"
              aria-label={`Transform controls for ${transformOverlay.layer.name}`}
              aria-describedby="transform-gesture-status"
            >
              <svg className="transform-outline" aria-hidden="true">
                <polygon
                  points={(['nw', 'ne', 'se', 'sw'] as const)
                    .map((id) => {
                      const point = transformOverlay.geometry.find((handle) => handle.id === id);
                      return point ? `${String(point.screenX)},${String(point.screenY)}` : '';
                    })
                    .join(' ')}
                />
                {(() => {
                  const top = transformOverlay.geometry.find((handle) => handle.id === 'n');
                  const rotate = transformOverlay.geometry.find((handle) => handle.id === 'rotate');
                  return top && rotate ? (
                    <line
                      x1={top.screenX}
                      y1={top.screenY}
                      x2={rotate.screenX}
                      y2={rotate.screenY}
                    />
                  ) : null;
                })()}
              </svg>
              {transformOverlay.geometry
                .filter(
                  (
                    handle,
                  ): handle is typeof handle & {
                    id: Exclude<TransformHandleId, 'center'>;
                  } => handle.id !== 'center',
                )
                .map((handle) => (
                  <button
                    type="button"
                    className="transform-handle"
                    data-kind={handle.kind}
                    key={handle.id}
                    aria-label={TRANSFORM_HANDLE_LABELS[handle.id]}
                    style={{ left: handle.screenX, top: handle.screenY }}
                    onPointerDown={(event) => beginFreeTransform(event, handle.id)}
                    onPointerMove={previewFreeTransform}
                    onPointerUp={finishFreeTransform}
                    onPointerCancel={cancelFreeTransform}
                    onKeyDown={(event) => handleTransformKeyDown(event, handle.id)}
                  />
                ))}
            </div>
          )}
          {pathOverlay && (
            <div
              className="path-overlay"
              role="group"
              aria-label={`Path controls for ${pathOverlay.layer.name}`}
              aria-describedby="transform-gesture-status"
            >
              <svg className="path-control-lines" aria-hidden="true">
                {pathOverlay.segments.map((segment, index) => (
                  <line
                    key={index}
                    x1={segment.from.screenX}
                    y1={segment.from.screenY}
                    x2={segment.to.screenX}
                    y2={segment.to.screenY}
                  />
                ))}
              </svg>
              {pathOverlay.handles.map((handle) => (
                <button
                  type="button"
                  className="path-handle"
                  data-role={handle.role}
                  key={`${String(handle.commandIndex)}-${handle.role}`}
                  aria-label={`Path command ${String(handle.commandIndex + 1)} ${
                    handle.role === 'anchor'
                      ? 'anchor'
                      : handle.role === 'control1'
                        ? 'control 1'
                        : 'control 2'
                  } handle`}
                  style={{ left: handle.screenX, top: handle.screenY }}
                  onPointerDown={(event) =>
                    beginPathHandle(event, handle.commandIndex, handle.role)
                  }
                  onPointerMove={previewPathHandle}
                  onPointerUp={finishPathHandle}
                  onPointerCancel={cancelPathHandle}
                  onKeyDown={(event) =>
                    handlePathHandleKeyDown(event, handle.commandIndex, handle.role)
                  }
                />
              ))}
            </div>
          )}
          <span
            id="transform-gesture-status"
            className={transformGestureStatus ? 'transform-gesture-status' : 'sr-only'}
            role="status"
            aria-live="polite"
          >
            {transformGestureStatus || 'Free Transform ready.'}
          </span>
          {!document && (
            <div className="empty-state">
              <div className="empty-card">
                <div className="empty-emblem" aria-hidden="true">
                  <Icon name="brand" />
                </div>
                <h1>Your canvas stays on your device.</h1>
                <p>
                  Create a transparent document or open a PNG, JPEG, WebP, or other
                  browser-supported image. Nothing is uploaded.
                </p>
                <div className="empty-actions">
                  <button className="action-button primary" onClick={createNew}>
                    Create document
                  </button>
                  <button className="action-button" onClick={() => fileRef.current?.click()}>
                    Open image
                  </button>
                </div>
              </div>
            </div>
          )}
          {document &&
            !taskBarDismissed &&
            (tool === 'crop' || document.selection || smartObjectEditSession || selectedLayer) && (
              <ContextualTaskBar
                label="Contextual document actions"
                onDismiss={() => setTaskBarDismissed(true)}
              >
                {tool === 'crop' && (
                  <>
                    <button
                      className="action-button primary"
                      disabled={!cropRectangle}
                      onClick={applyCrop}
                    >
                      Apply crop
                    </button>
                    <button
                      className="action-button"
                      disabled={!cropRectangle}
                      onClick={cancelCrop}
                    >
                      Cancel crop
                    </button>
                  </>
                )}
                {document.selection && (
                  <>
                    <button
                      className="action-button"
                      onClick={() => runCommand(setSelection(null))}
                    >
                      Clear selection
                    </button>
                    {selectedLayer && (
                      <button className="action-button" onClick={addLayerMaskFromSelection}>
                        Add mask
                      </button>
                    )}
                  </>
                )}
                {smartObjectEditSession && (
                  <>
                    <button className="action-button primary" onClick={saveSmartObjectContents}>
                      Save smart object
                    </button>
                    <button className="action-button" onClick={cancelSmartObjectContents}>
                      Cancel smart object edit
                    </button>
                  </>
                )}
                {!smartObjectEditSession && selectedLayer?.kind === 'smart-object' && (
                  <button className="action-button" onClick={editSmartObjectContents}>
                    Edit smart-object contents
                  </button>
                )}
                {!smartObjectEditSession &&
                  selectedLayer &&
                  selectedLayer.kind !== 'smart-object' && (
                    <button className="action-button" onClick={convertSelectedToSmartObject}>
                      Make smart object
                    </button>
                  )}
              </ContextualTaskBar>
            )}
        </CanvasWorkspace>

        <PanelDock
          collapsed={panelCollapsed}
          primaryWidth={primaryDockWidth}
          secondaryWidth={secondaryDockWidth}
          groups={workspaceState.groups}
          onChangeGroup={(id, change) =>
            setWorkspaceState((current) => updateDockGroup(current, id, change))
          }
          onToggleCollapsed={() => setPanelCollapsed((value) => !value)}
          onResizePrimary={(width) =>
            setWorkspaceState((current) => ({ ...current, primaryDockWidth: width }))
          }
          onResizeSecondary={(width) =>
            setWorkspaceState((current) => ({ ...current, secondaryDockWidth: width }))
          }
          narrowColumn={workspaceState.narrowDockColumn}
          onNarrowColumnChange={(column) =>
            setWorkspaceState((current) => ({ ...current, narrowDockColumn: column }))
          }
          onOpenGroupMenu={openPanelGroupMenu}
        >
          <PanelContent id="properties">
            <>
              {selectedLayer && (
                <LayerPropertiesSections
                  {...{
                    addAdjustmentMask,
                    addLayerEffect,
                    addLayerMaskFromSelection,
                    addHideAllMask,
                    addRectangularVectorMask,
                    addRevealAllMask,
                    addSmartFilter,
                    applyBrightness,
                    applyCenteredTransform,
                    busy,
                    brightness,
                    colorToHex,
                    commitLayerTransform,
                    commitMaskDensity,
                    commitVectorMaskDensity,
                    document,
                    editSmartObjectContents,
                    editTarget,
                    fontFileRef,
                    hexToColor,
                    identityLayerTransform,
                    invertLayerMask,
                    invertLayerVectorMask,
                    layerTransformDraft,
                    loadedFontFamilies,
                    maskDensityDraft,
                    maskDensityEditingRef,
                    notify,
                    pathCommandAnchor,
                    previewMaskDensity,
                    removeLayerEffect,
                    removeSmartFilter,
                    renameLayer,
                    runCommand,
                    selectedLayer,
                    setBrightness,
                    setEditTarget,
                    setLayerBlendMode,
                    setLayerClipping,
                    setLayerFillOpacity,
                    setLayerLock,
                    setLayerMaskEnabled,
                    setLayerOpacity,
                    setLayerRasterMask,
                    setLayerTransform,
                    setLayerTransformDraft,
                    setLayerVectorMask,
                    setLayerVectorMaskEnabled,
                    setShapeGeometry,
                    setShapeStyle,
                    setTextContent,
                    setTextStyle,
                    setVectorMaskDensityDraft,
                    smartObjectFileRef,
                    textEngine,
                    updateAdjustment,
                    updateLayerEffect,
                    updateSmartFilter,
                    updateVectorMaskCommand,
                    vectorMaskDensityDraft,
                    BLEND_MODES,
                  }}
                />
              )}
            </>
          </PanelContent>
          <PanelContent id="layers">
            <>
              <LayersPanelHeader
                layers={layers.length}
                visibleLayers={visibleDisplayLayers.length}
                searchQuery={layerSearchQuery}
                filterKind={layerFilterKind}
                onSearchQueryChange={setLayerSearchQuery}
                onFilterKindChange={setLayerFilterKind}
                onClearFilter={() => {
                  setLayerSearchQuery('');
                  setLayerFilterKind('all');
                }}
                selectedLayer={selectedLayer}
                onBlendMode={(value) =>
                  selectedLayer && runCommand(setLayerBlendMode(selectedLayer.id, value))
                }
                onOpacity={(value) =>
                  selectedLayer && runCommand(setLayerOpacity(selectedLayer.id, value))
                }
                onFill={(value) =>
                  selectedLayer && runCommand(setLayerFillOpacity(selectedLayer.id, value))
                }
              />
              <div className="layer-list" role="list" aria-label="Document layers">
                {visibleDisplayLayers.map((layer) => (
                  <LayerRow
                    key={layer.id}
                    layer={layer}
                    depth={layerDepth(document?.layers ?? [], layer.id)}
                    selected={selectedLayerIds.includes(layer.id)}
                    editTarget={selectedLayerIds.includes(layer.id) ? editTarget : null}
                    onEditTargetChange={(target) => selectLayerEditTarget(layer.id, target)}
                    onContextMenu={(event) => {
                      event.preventDefault();
                      setSelectedLayerId(layer.id);
                      setSelectedLayerIds([layer.id]);
                      setContextMenu({
                        x: event.clientX,
                        y: event.clientY,
                        commands: [
                          'layer.duplicate',
                          'layer.group',
                          'layer.ungroup',
                          'layer.mask',
                          'layer.delete',
                          'layer.flatten',
                        ],
                      });
                    }}
                    onSelect={(event) => selectLayer(event, layer.id)}
                    onVisibility={() => runCommand(setLayerVisibility(layer.id, !layer.visible))}
                  />
                ))}
                {visibleDisplayLayers.length === 0 && (
                  <p className="layer-filter-empty" role="status">
                    No layers match the current search and filter.
                  </p>
                )}
              </div>
            </>
          </PanelContent>
          <PanelContent id="channels">
            <section className="panel-section channel-panel" aria-labelledby="channels-heading">
              <h2 id="channels-heading">Channels · {document?.channels.length ?? 0}</h2>
              <fieldset className="channel-list">
                <legend>Channel preview</legend>
                {(['composite', 'red', 'green', 'blue'] as const).map((view) => {
                  const label =
                    view === 'composite'
                      ? 'Composite color'
                      : `${view.charAt(0).toUpperCase()}${view.slice(1)}`;
                  return (
                    <label className="channel-row" key={view} data-selected={channelView === view}>
                      <input
                        type="radio"
                        name="channel-preview"
                        checked={channelView === view}
                        disabled={!document}
                        onChange={() => {
                          setChannelView(view);
                          setSelectedChannelId(null);
                        }}
                      />
                      <span className={`channel-swatch channel-${view}`} aria-hidden="true" />
                      <span>{label}</span>
                    </label>
                  );
                })}
                {document?.channels.map((channel) => (
                  <label
                    className="channel-row"
                    key={channel.id}
                    data-selected={selectedChannelId === channel.id}
                  >
                    <input
                      type="radio"
                      name="channel-preview"
                      aria-label={`Preview ${channel.name}`}
                      checked={channelView === `alpha:${channel.id}`}
                      onChange={() => {
                        setSelectedChannelId(channel.id);
                        setChannelView(`alpha:${channel.id}`);
                      }}
                    />
                    <span className="channel-swatch channel-alpha" aria-hidden="true" />
                    <span>{channel.name}</span>
                  </label>
                ))}
              </fieldset>

              {selectedChannel && (
                <label className="property-field channel-name-field">
                  Channel name for {selectedChannel.name}
                  <input
                    value={channelNameDraft}
                    onChange={(event) => setChannelNameDraft(event.target.value)}
                    onBlur={commitChannelName}
                    onKeyDown={(event) => {
                      if (event.key === 'Enter') event.currentTarget.blur();
                      if (event.key === 'Escape') {
                        event.preventDefault();
                        setChannelNameDraft(selectedChannel.name);
                      }
                    }}
                  />
                </label>
              )}

              <div className="channel-actions">
                <button
                  className="action-button"
                  disabled={!document?.selection}
                  aria-describedby={!document?.selection ? 'channel-selection-help' : undefined}
                  onClick={saveSelectionAsChannel}
                >
                  Save selection as channel
                </button>
                <button
                  className="action-button"
                  disabled={!selectedChannel}
                  onClick={loadSelectedChannel}
                >
                  Load selected channel as selection
                </button>
                <button
                  className="action-button danger"
                  disabled={!selectedChannel}
                  onClick={deleteSelectedChannel}
                >
                  Delete selected channel
                </button>
                {!document?.selection && (
                  <span id="channel-selection-help" className="field-help">
                    Create a selection before saving an alpha channel.
                  </span>
                )}
              </div>
            </section>
          </PanelContent>
          <PanelContent id="history">
            <section className="panel-section history-panel" aria-labelledby="history-heading">
              <h2 id="history-heading">History</h2>
              <p className="history-summary" aria-live="polite">
                {history?.undoCount ?? 0} undo steps, {history?.redoCount ?? 0} redo steps
              </p>
              <ol className="history-list">
                <li>
                  <button
                    className="history-step"
                    aria-label={
                      history?.canUndo
                        ? `History step ${history.nextUndoLabel ?? 'previous change'}`
                        : 'No earlier state'
                    }
                    disabled={!history?.canUndo}
                    onClick={undo}
                  >
                    {history?.canUndo
                      ? `Undo ${history.nextUndoLabel ?? 'previous change'}`
                      : 'No earlier state'}
                  </button>
                </li>
                <li className="history-current" aria-current="step">
                  Current document state
                </li>
                <li>
                  <button
                    className="history-step"
                    aria-label={
                      history?.canRedo
                        ? `Future history step ${history.nextRedoLabel ?? 'next change'}`
                        : 'No later state'
                    }
                    disabled={!history?.canRedo}
                    onClick={redo}
                  >
                    {history?.canRedo
                      ? `Redo ${history.nextRedoLabel ?? 'next change'}`
                      : 'No later state'}
                  </button>
                </li>
              </ol>
              {!history?.canUndo && !history?.canRedo && (
                <p className="field-help">History begins at the current or recovered state.</p>
              )}
            </section>
          </PanelContent>
          <PanelContent id="properties">
            <PropertiesPanel
              documentName={document?.name ?? null}
              canvas={document?.canvas ?? null}
              layer={selectedLayer}
              layerSize={selectedLayer ? renderableLayerDimensions(selectedLayer) : null}
              editTarget={editTarget}
              contextOverride={
                tool === 'transform'
                  ? 'Active Transform'
                  : tool === 'crop' && cropRectangle
                    ? 'Active Crop'
                    : pathDraft.length > 0
                      ? 'Active Path'
                      : document && selectionBounds(document)
                        ? 'Active Selection'
                        : null
              }
              onTransform={(transform) =>
                selectedLayer && runCommand(setLayerTransform(selectedLayer.id, transform))
              }
            />
          </PanelContent>
          <PanelContent id="paths">
            <EmptyPanelState
              title="Paths"
              description="Path editing remains available on the canvas. A persistent path library is not implemented yet."
            />
          </PanelContent>
          <PanelContent id="adjustments">
            <AdjustmentsPanel registry={commandRegistry} />
          </PanelContent>
          <PanelContent id="actions">
            <EmptyPanelState
              title="Actions"
              description="Action recording and playback are part of the later automation milestone."
            />
          </PanelContent>
          <PanelContent id="layers">
            <div className="layer-actions" aria-label="Layer actions">
              <div className="layer-primary-actions">
                <span className="layer-icon-select" title="Add Layer Style">
                  <Icon name="fx" />
                  <select
                    aria-label="Add layer effect"
                    value=""
                    disabled={!selectedLayer}
                    onChange={(event) => {
                      if (event.target.value)
                        addLayerEffect(event.target.value as LayerEffect['type']);
                    }}
                  >
                    <option value="">Add Layer Style</option>
                    <option value="color-overlay">Color Overlay</option>
                    <option value="stroke">Stroke</option>
                    <option value="drop-shadow">Drop Shadow</option>
                  </select>
                </span>
                <button
                  className="icon-button"
                  aria-label="Add Layer Mask"
                  title="Add Layer Mask"
                  disabled={!selectedLayer || selectedLayer.rasterMask !== null}
                  onClick={addRevealAllMask}
                >
                  <Icon name="mask" />
                </button>
                <CommandPopoverButton
                  id="layers-adjustment-menu-button"
                  label="New Adjustment Layer"
                  icon="adjustment"
                  commandIds={ADJUSTMENT_LAYER_COMMAND_IDS}
                  registry={commandRegistry}
                  disabled={!document || busy}
                />
                <button
                  className="icon-button"
                  aria-label="Group selected layers"
                  title="New Group"
                  disabled={selectedLayerIds.length === 0}
                  onClick={groupSelectedLayers}
                >
                  <Icon name="group" />
                </button>
                <button
                  className="icon-button"
                  aria-label="Add pixel layer"
                  title="New Layer"
                  disabled={!document}
                  onClick={addBlankLayer}
                >
                  <Icon name="add" />
                </button>
                <button
                  className="icon-button danger"
                  aria-label="Delete selected layer"
                  title="Delete Layer"
                  disabled={!selectedLayer}
                  onClick={deleteSelectedLayer}
                >
                  <Icon name="trash" />
                </button>
                <details className="layer-more-actions">
                  <summary aria-label="More layer actions" title="More layer actions">
                    <Icon name="more" />
                  </summary>
                  <div
                    className="layer-more-actions-menu"
                    onClickCapture={(event) => {
                      if ((event.target as HTMLElement).closest('button'))
                        event.currentTarget.closest('details')?.removeAttribute('open');
                    }}
                    onChangeCapture={(event) =>
                      event.currentTarget.closest('details')?.removeAttribute('open')
                    }
                  >
                    <select
                      className="adjustment-layer-select"
                      aria-label="Add shape layer"
                      value=""
                      disabled={!document}
                      onChange={(event) => {
                        if (event.target.value)
                          addShape(event.target.value as 'rectangle' | 'ellipse');
                      }}
                    >
                      <option value="">Add shape…</option>
                      <option value="rectangle">Rectangle</option>
                      <option value="ellipse">Ellipse</option>
                    </select>
                    <button
                      className="icon-button"
                      aria-label="Add text layer"
                      title="Add Text Layer"
                      disabled={!document}
                      onClick={addText}
                    >
                      <Icon name="text" />
                    </button>
                    <button
                      className="icon-button"
                      aria-label="Duplicate selected layer"
                      title="Duplicate Layer"
                      disabled={!selectedLayer}
                      onClick={duplicateSelectedLayer}
                    >
                      <Icon name="duplicate" />
                    </button>
                    <button
                      className="icon-button"
                      aria-label="Ungroup selected layer"
                      disabled={selectedLayer?.kind !== 'group'}
                      onClick={ungroupSelectedLayer}
                    >
                      <Icon name="ungroup" />
                    </button>
                    <button
                      className="icon-button"
                      aria-label="Move selected layer up"
                      disabled={
                        !selectedLayer ||
                        selectedLayerIds.length !== 1 ||
                        document?.layers.at(-1)?.id === selectedLayerId
                      }
                      onClick={() => moveSelectedLayer(1)}
                    >
                      <Icon name="moveUp" />
                    </button>
                    <button
                      className="icon-button"
                      aria-label="Move selected layer down"
                      disabled={
                        !selectedLayer ||
                        selectedLayerIds.length !== 1 ||
                        document?.layers[0]?.id === selectedLayerId
                      }
                      onClick={() => moveSelectedLayer(-1)}
                    >
                      <Icon name="moveDown" />
                    </button>
                    <button
                      className="action-button layer-merge-action"
                      disabled={
                        !selectedLayer ||
                        selectedLayer.kind === 'group' ||
                        selectedLayer.kind === 'adjustment' ||
                        selectedLayer.kind === 'smart-object'
                      }
                      onClick={convertSelectedToSmartObject}
                    >
                      Convert to smart object
                    </button>
                    <button
                      className="action-button layer-merge-action"
                      disabled={selectedLayerIds.length < 2}
                      onClick={mergeSelectedLayers}
                    >
                      Merge selected
                    </button>
                    <button
                      className="action-button layer-merge-action"
                      disabled={!document?.layers.length}
                      onClick={flattenImage}
                    >
                      Flatten image
                    </button>
                  </div>
                </details>
              </div>
            </div>
          </PanelContent>
          <PanelContent id="color">
            <section className="panel-section color-panel" aria-labelledby="color-heading">
              <h2 id="color-heading">Color</h2>
              <label className="property-field">
                Foreground{' '}
                <input
                  type="color"
                  value={brushColor}
                  onChange={(event) => setBrushColor(event.target.value)}
                />
              </label>
            </section>
          </PanelContent>
          <PanelContent id="swatches">
            <section className="panel-section" aria-labelledby="swatches-heading">
              <h2 id="swatches-heading">Swatches</h2>
              <p className="field-help">Document swatch libraries are not implemented yet.</p>
            </section>
          </PanelContent>
        </PanelDock>
      </WorkspaceLayout>

      <StatusBar
        left={
          <>
            <span>
              {document
                ? `${String(document.canvas.width)} × ${String(document.canvas.height)} px`
                : 'Ready'}
            </span>
            {document && (
              <span>
                {layers.filter((layer): layer is PixelLayer => layer.kind === 'pixel').length} pixel
                layers
              </span>
            )}
            <span>
              {document && selectionBounds(document)
                ? (() => {
                    const bounds = selectionBounds(document);
                    return bounds
                      ? `Selection: ${String(bounds.x)}, ${String(bounds.y)} · ${String(bounds.width)} × ${String(bounds.height)}`
                      : 'No selection';
                  })()
                : 'No selection'}
            </span>
            {document && <span>RGB / 8-bit</span>}
            <span>{editTarget === 'mask' ? 'Mask target' : 'Pixel target'}</span>
          </>
        }
        right={
          <>
            <span>
              {saveState === 'saving'
                ? 'Saving locally…'
                : saveState === 'error'
                  ? 'Autosave error'
                  : saveState === 'saved'
                    ? 'Saved locally'
                    : 'Local-only'}
            </span>
            <span>{Math.round(viewport.zoom * 100)}%</span>
            {import.meta.env.DEV && document && (
              <span className="debug-status">
                rev {revision} · {canvasRef.current?.dataset.renderMode ?? 'idle'}
              </span>
            )}
          </>
        }
      />

      <ModalDialog open={sizeDialog === 'canvas'} title="Canvas Size" onCancel={closeSizeDialog}>
        <form
          className="size-form"
          onSubmit={(event) => {
            event.preventDefault();
            applyCanvasSize();
          }}
        >
          <p className="dialog-summary">
            Current size: {document?.canvas.width ?? 0} × {document?.canvas.height ?? 0} pixels
          </p>
          <div className="size-fields">
            <label>
              Width
              <input
                autoFocus
                aria-label="Canvas width"
                aria-describedby={parsedCanvasSize.error ? 'canvas-size-error' : undefined}
                type="number"
                min="1"
                max={MAX_DOCUMENT_DIMENSION}
                step="1"
                value={canvasSizeDraft.width}
                onChange={(event) =>
                  setCanvasSizeDraft((draft) => ({ ...draft, width: event.target.value }))
                }
              />
            </label>
            <label>
              Height
              <input
                aria-label="Canvas height"
                aria-describedby={parsedCanvasSize.error ? 'canvas-size-error' : undefined}
                type="number"
                min="1"
                max={MAX_DOCUMENT_DIMENSION}
                step="1"
                value={canvasSizeDraft.height}
                onChange={(event) =>
                  setCanvasSizeDraft((draft) => ({ ...draft, height: event.target.value }))
                }
              />
            </label>
          </div>
          <fieldset className="anchor-grid">
            <legend>Anchor</legend>
            {(
              [
                ['top-left', 'Top left'],
                ['top-center', 'Top'],
                ['top-right', 'Top right'],
                ['center-left', 'Left'],
                ['center', 'Center'],
                ['center-right', 'Right'],
                ['bottom-left', 'Bottom left'],
                ['bottom-center', 'Bottom'],
                ['bottom-right', 'Bottom right'],
              ] as const
            ).map(([anchor, glyph]) => (
              <label key={anchor} title={anchor.replace('-', ' ')}>
                <input
                  type="radio"
                  name="canvas-anchor"
                  aria-label={`Anchor ${anchor.replace('-', ' ')}`}
                  value={anchor}
                  checked={canvasSizeDraft.anchor === anchor}
                  onChange={() => setCanvasSizeDraft((draft) => ({ ...draft, anchor }))}
                />
                <span aria-hidden="true">{glyph}</span>
              </label>
            ))}
          </fieldset>
          {parsedCanvasSize.error && (
            <p className="dialog-error" id="canvas-size-error" role="alert">
              {parsedCanvasSize.error}
            </p>
          )}
          <div className="dialog-actions">
            <button className="action-button" type="button" onClick={closeSizeDialog}>
              Cancel
            </button>
            <button
              className="action-button primary"
              type="submit"
              disabled={Boolean(parsedCanvasSize.error) || busy}
            >
              Resize canvas
            </button>
          </div>
        </form>
      </ModalDialog>

      <ModalDialog open={sizeDialog === 'image'} title="Image Size" onCancel={closeSizeDialog}>
        <form
          className="size-form"
          onSubmit={(event) => {
            event.preventDefault();
            void applyImageSize();
          }}
        >
          <p className="dialog-summary">
            Current size: {document?.canvas.width ?? 0} × {document?.canvas.height ?? 0} pixels
          </p>
          <div className="size-fields">
            <label>
              Width
              <input
                autoFocus
                aria-label="Image width"
                aria-describedby={parsedImageSize.error ? 'image-size-error' : undefined}
                type="number"
                min="1"
                max={MAX_DOCUMENT_DIMENSION}
                step="1"
                value={imageSizeDraft.width}
                onChange={(event) => {
                  const width = event.target.value;
                  setImageSizeDraft((draft) => {
                    const numericWidth = Number(width);
                    const constrainedHeight =
                      draft.constrain && document && Number.isSafeInteger(numericWidth)
                        ? String(
                            Math.max(
                              1,
                              Math.round(
                                (numericWidth * document.canvas.height) / document.canvas.width,
                              ),
                            ),
                          )
                        : draft.height;
                    return { ...draft, width, height: constrainedHeight };
                  });
                }}
              />
            </label>
            <label>
              Height
              <input
                aria-label="Image height"
                aria-describedby={parsedImageSize.error ? 'image-size-error' : undefined}
                type="number"
                min="1"
                max={MAX_DOCUMENT_DIMENSION}
                step="1"
                value={imageSizeDraft.height}
                onChange={(event) => {
                  const height = event.target.value;
                  setImageSizeDraft((draft) => {
                    const numericHeight = Number(height);
                    const constrainedWidth =
                      draft.constrain && document && Number.isSafeInteger(numericHeight)
                        ? String(
                            Math.max(
                              1,
                              Math.round(
                                (numericHeight * document.canvas.width) / document.canvas.height,
                              ),
                            ),
                          )
                        : draft.width;
                    return { ...draft, width: constrainedWidth, height };
                  });
                }}
              />
            </label>
          </div>
          <label className="dialog-checkbox">
            <input
              type="checkbox"
              checked={imageSizeDraft.constrain}
              onChange={(event) =>
                setImageSizeDraft((draft) => ({ ...draft, constrain: event.target.checked }))
              }
            />
            Constrain proportions
          </label>
          <label>
            Resampling method
            <select
              aria-label="Resampling method"
              value={imageSizeDraft.method}
              onChange={(event) =>
                setImageSizeDraft((draft) => ({
                  ...draft,
                  method: event.target.value as ResamplingMethod,
                }))
              }
            >
              <option value="nearest">Nearest neighbor</option>
              <option value="bilinear">Bilinear</option>
            </select>
          </label>
          <p className="dialog-summary">
            New size: {parsedImageSize.error ? '—' : parsedImageSize.width} ×{' '}
            {parsedImageSize.error ? '—' : parsedImageSize.height} pixels
          </p>
          {parsedImageSize.error && (
            <p className="dialog-error" id="image-size-error" role="alert">
              {parsedImageSize.error}
            </p>
          )}
          <div className="dialog-actions">
            <button className="action-button" type="button" onClick={closeSizeDialog}>
              Cancel
            </button>
            <button
              className="action-button primary"
              type="submit"
              disabled={Boolean(parsedImageSize.error) || busy}
            >
              Resize image
            </button>
          </div>
        </form>
      </ModalDialog>

      <PixelOperationDialog
        open={activePixelOperation !== null}
        mode={activePixelOperation?.mode ?? 'destructive'}
        draft={filterDraft}
        selectedLayerName={selectedLayer?.name ?? 'Selected layer'}
        selectionActive={Boolean(document?.selection)}
        error={filterError}
        busy={busy}
        onDraftChange={setFilterDraft}
        onCancel={closeSizeDialog}
        onApply={() => void applyFilter()}
      />

      <ModalDialog
        open={workspaceResetOpen}
        title="Reset Professional Editing Workspace"
        onCancel={() => setWorkspaceResetOpen(false)}
      >
        <p className="dialog-summary">
          This resets dock widths, panel visibility, active panel tabs, collapsed groups, and the
          narrow-layout panel column. It does not change the open document or editing history.
        </p>
        <div className="dialog-actions">
          <button
            type="button"
            className="action-button"
            onClick={() => setWorkspaceResetOpen(false)}
          >
            Cancel
          </button>
          <button type="button" className="action-button danger" onClick={confirmWorkspaceReset}>
            Reset Workspace
          </button>
        </div>
      </ModalDialog>

      <input
        ref={fileRef}
        className="sr-only"
        type="file"
        accept=".freetoshop,.psd,application/x-freetoshop,image/vnd.adobe.photoshop,image/png,image/jpeg,image/webp,image/gif,image/avif"
        aria-label="Open image file"
        onChange={(event) => {
          const file = event.target.files?.[0];
          if (file) void openFile(file);
        }}
      />
      <input
        ref={smartObjectFileRef}
        className="sr-only"
        type="file"
        accept="image/png,image/jpeg,image/webp,image/gif,image/avif"
        aria-label="Replace smart object contents file"
        onChange={(event) => {
          const file = event.target.files?.[0];
          if (file) void replaceSmartObjectFile(file);
        }}
      />
      <input
        ref={fontFileRef}
        className="sr-only"
        type="file"
        accept=".otf,.ttf,.ttc,font/otf,font/ttf,application/font-sfnt"
        aria-label="Load local font file"
        onChange={(event) => {
          const file = event.target.files?.[0];
          if (file) void loadFontFile(file);
        }}
      />
      <div className="toast-region" aria-live="polite" aria-atomic="false">
        {toasts.map((toast) => (
          <div className="toast" data-tone={toast.tone} key={toast.id}>
            {toast.message}
          </div>
        ))}
      </div>
      {contextMenu && (
        <ContextMenu
          x={contextMenu.x}
          y={contextMenu.y}
          commandIds={contextMenu.commands}
          registry={commandRegistry}
          returnFocus={contextMenu.returnFocus}
          onClose={() => setContextMenu(null)}
        />
      )}
    </EditorShell>
  );
}
