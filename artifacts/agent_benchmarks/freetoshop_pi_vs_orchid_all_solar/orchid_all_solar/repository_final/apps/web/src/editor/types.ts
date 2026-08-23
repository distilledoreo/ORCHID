import type {
  CanvasAnchorPosition,
  ImageDocument,
  LayerTransform,
  ShapeGeometry,
} from '@freetoshop/core-document';
import type { CommandHistory } from '@freetoshop/command-history';
import type { BrushPoint, ResamplingMethod } from '@freetoshop/raster-engine';
import type { SelectionPoint } from '@freetoshop/selection-engine';
import type { FreeTransformSnapshot, TransformHandleId } from '@freetoshop/transform-engine';
import type { ViewportTransform } from '@freetoshop/renderer';

export type Toast = { id: number; message: string; tone: 'info' | 'error' };
export type Tool =
  | 'pan'
  | 'move'
  | 'transform'
  | 'crop'
  | 'select'
  | 'ellipse'
  | 'lasso'
  | 'wand'
  | 'pen'
  | 'brush'
  | 'eraser'
  | 'clone-stamp';
export type EditTarget = 'pixels' | 'mask';
export type Panel = import('../panels/panelRegistry.js').PanelId;
export type ChannelView = 'composite' | 'red' | 'green' | 'blue' | `alpha:${string}`;
export type TransformDraft = Record<keyof LayerTransform, string>;
export interface CanvasSizeDraft {
  readonly width: string;
  readonly height: string;
  readonly anchor: CanvasAnchorPosition;
}
export interface ImageSizeDraft {
  readonly width: string;
  readonly height: string;
  readonly method: ResamplingMethod;
  readonly constrain: boolean;
}
export interface SmartObjectEditSession {
  readonly parentHistory: CommandHistory<ImageDocument>;
  readonly layerId: string;
  readonly sourceId: string;
  readonly sourceName: string;
  readonly parentViewport: ViewportTransform;
}
export interface DocumentPoint {
  readonly x: number;
  readonly y: number;
}
export type PathHandleRole = 'anchor' | 'control1' | 'control2';
export interface DocumentRectangle extends DocumentPoint {
  readonly width: number;
  readonly height: number;
}
export type Gesture =
  | { kind: 'pan'; x: number; y: number; panX: number; panY: number }
  | {
      kind: 'move-guide';
      guideId: string;
      direction: 'horizontal' | 'vertical';
    }
  | {
      kind: 'move';
      start: DocumentPoint;
      origin: LayerTransform;
      parentInverse: LayerTransform;
      layerId: string;
    }
  | {
      kind: 'free-transform';
      layerId: string;
      handle: TransformHandleId;
      snapshot: FreeTransformSnapshot;
      start: DocumentPoint;
      changed: boolean;
    }
  | { kind: 'select' | 'ellipse'; start: DocumentPoint; current: DocumentPoint }
  | { kind: 'lasso'; points: SelectionPoint[] }
  | {
      kind: 'crop';
      start: DocumentPoint;
      current: DocumentPoint;
      original: DocumentRectangle | null;
    }
  | { kind: 'paint'; points: BrushPoint[]; layerId: string; target: EditTarget }
  | {
      kind: 'path-handle';
      layerId: string;
      commandIndex: number;
      role: PathHandleRole;
      geometry: ShapeGeometry;
      worldInverse: LayerTransform;
      changed: boolean;
    };
