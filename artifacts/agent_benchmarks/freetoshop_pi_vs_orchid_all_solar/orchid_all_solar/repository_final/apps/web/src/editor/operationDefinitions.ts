import type { Adjustment, SmartFilter } from '@freetoshop/core-document';
import type { FilterDraft, FilterKind } from './filterOperations.js';

export type ImageAdjustmentKind =
  | 'contrast'
  | 'levels'
  | 'hue-saturation'
  | 'grayscale'
  | 'invert'
  | 'threshold'
  | 'posterize';
export type ParameterizedImageAdjustmentKind =
  | 'contrast'
  | 'levels'
  | 'hue-saturation'
  | 'threshold'
  | 'posterize';
export type ImmediateImageAdjustmentKind = 'grayscale' | 'invert';
export type FilterMenuKind = 'gaussian-blur';
export type SmartFilterMenuKind = 'gaussian-blur';

export interface ImageAdjustmentDefinition {
  readonly id: string;
  readonly kind: ImageAdjustmentKind;
  readonly label: string;
  readonly order: number;
  readonly parameterized: boolean;
  readonly shortcut?: string;
  readonly separatorBefore?: boolean;
}
export interface FilterMenuDefinition {
  readonly id: string;
  readonly kind: FilterMenuKind;
  readonly label: string;
  readonly submenu: 'filter.blur';
  readonly order: number;
}
export interface AdjustmentLayerDefinition {
  readonly id: string;
  readonly type: Adjustment['type'];
  readonly label: string;
  readonly order: number;
}

export const IMAGE_ADJUSTMENT_DEFINITIONS = [
  {
    id: 'image.adjustments.contrast',
    kind: 'contrast',
    label: 'Contrast…',
    order: 10,
    parameterized: true,
  },
  {
    id: 'image.adjustments.levels',
    kind: 'levels',
    label: 'Levels…',
    order: 20,
    parameterized: true,
    shortcut: 'Ctrl+L',
  },
  {
    id: 'image.adjustments.hue-saturation',
    kind: 'hue-saturation',
    label: 'Hue/Saturation…',
    order: 30,
    parameterized: true,
    shortcut: 'Ctrl+U',
  },
  {
    id: 'image.adjustments.grayscale',
    kind: 'grayscale',
    label: 'Grayscale',
    order: 40,
    parameterized: false,
    separatorBefore: true,
  },
  {
    id: 'image.adjustments.invert',
    kind: 'invert',
    label: 'Invert',
    order: 50,
    parameterized: false,
    shortcut: 'Ctrl+I',
  },
  {
    id: 'image.adjustments.threshold',
    kind: 'threshold',
    label: 'Threshold…',
    order: 60,
    parameterized: true,
    separatorBefore: true,
  },
  {
    id: 'image.adjustments.posterize',
    kind: 'posterize',
    label: 'Posterize…',
    order: 70,
    parameterized: true,
  },
] as const satisfies readonly ImageAdjustmentDefinition[];

export const FILTER_MENU_DEFINITIONS = [
  {
    id: 'filter.blur.gaussian',
    kind: 'gaussian-blur',
    label: 'Gaussian Blur…',
    submenu: 'filter.blur',
    order: 10,
  },
] as const satisfies readonly FilterMenuDefinition[];

export const ADJUSTMENT_LAYER_DEFINITIONS = [
  {
    id: 'layer.new-adjustment.brightness-contrast',
    type: 'brightness-contrast',
    label: 'Brightness/Contrast…',
    order: 10,
  },
  { id: 'layer.new-adjustment.levels', type: 'levels', label: 'Levels…', order: 20 },
  {
    id: 'layer.new-adjustment.hue-saturation',
    type: 'hue-saturation',
    label: 'Hue/Saturation…',
    order: 30,
  },
  { id: 'layer.new-adjustment.invert', type: 'invert', label: 'Invert', order: 40 },
  { id: 'layer.new-adjustment.posterize', type: 'posterize', label: 'Posterize…', order: 50 },
  { id: 'layer.new-adjustment.threshold', type: 'threshold', label: 'Threshold…', order: 60 },
] as const satisfies readonly AdjustmentLayerDefinition[];

export const IMAGE_ADJUSTMENT_COMMAND_IDS = IMAGE_ADJUSTMENT_DEFINITIONS.map(({ id }) => id);
export const FILTER_OPERATION_COMMAND_IDS = FILTER_MENU_DEFINITIONS.map(({ id }) => id);
export const ADJUSTMENT_LAYER_COMMAND_IDS = ADJUSTMENT_LAYER_DEFINITIONS.map(({ id }) => id);

export function createAdjustmentPreset(type: Adjustment['type']): Adjustment {
  switch (type) {
    case 'brightness-contrast':
      return { type, brightness: 0, contrast: 0 };
    case 'levels':
      return { type, inputBlack: 0, inputWhite: 255, gamma: 1, outputBlack: 0, outputWhite: 255 };
    case 'hue-saturation':
      return { type, hue: 0, saturation: 0, lightness: 0 };
    case 'invert':
      return { type };
    case 'posterize':
      return { type, levels: 4 };
    case 'threshold':
      return { type, level: 128 };
  }
}

export function adjustmentLayerLabel(type: Adjustment['type']): string {
  return type === 'brightness-contrast'
    ? 'Brightness/Contrast'
    : type === 'hue-saturation'
      ? 'Hue/Saturation'
      : `${type.slice(0, 1).toUpperCase()}${type.slice(1)}`;
}

export function createSmartFilterFromDraft(
  kind: SmartFilterMenuKind,
  draft: FilterDraft,
  id: string,
): SmartFilter {
  return { id, type: 'gaussian-blur', enabled: true, opacity: 1, radius: draft.blurRadius };
}

export function isImmediateImageAdjustment(
  kind: ImageAdjustmentKind,
): kind is ImmediateImageAdjustmentKind {
  return kind === 'grayscale' || kind === 'invert';
}
export function isParameterizedImageAdjustment(
  kind: ImageAdjustmentKind,
): kind is ParameterizedImageAdjustmentKind {
  return !isImmediateImageAdjustment(kind);
}
export function supportsSmartFilter(kind: FilterKind): kind is SmartFilterMenuKind {
  return kind === 'gaussian-blur';
}
