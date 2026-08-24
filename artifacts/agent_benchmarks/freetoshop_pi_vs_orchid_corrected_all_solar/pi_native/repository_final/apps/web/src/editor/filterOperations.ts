import type {
  LayerTransform,
  PixelLayer,
  PixelPatch,
  SelectionMask,
} from '@freetoshop/core-document';
import {
  contrast,
  gaussianBlur,
  gaussianBlurPatches,
  grayscale,
  hueSaturation,
  invert,
  levels,
  posterize,
  pointwiseRgbFilterPatches,
  threshold,
  type FilterPatchObserver,
} from '@freetoshop/filter-engine';

export type FilterKind =
  | 'contrast'
  | 'levels'
  | 'hue-saturation'
  | 'invert'
  | 'grayscale'
  | 'threshold'
  | 'posterize'
  | 'gaussian-blur';

export interface FilterDraft {
  readonly kind: FilterKind;
  readonly contrast: number;
  readonly inputBlack: number;
  readonly gamma: number;
  readonly inputWhite: number;
  readonly outputBlack: number;
  readonly outputWhite: number;
  readonly hue: number;
  readonly saturation: number;
  readonly lightness: number;
  readonly threshold: number;
  readonly posterizeLevels: number;
  readonly blurRadius: number;
}

export const FILTER_LABELS: Readonly<Record<FilterKind, string>> = {
  contrast: 'Contrast',
  levels: 'Levels',
  'hue-saturation': 'Hue / Saturation',
  invert: 'Invert',
  grayscale: 'Grayscale',
  threshold: 'Threshold',
  posterize: 'Posterize',
  'gaussian-blur': 'Gaussian Blur',
};

export function createFilterPatch(
  layer: PixelLayer,
  selection: SelectionMask | null,
  worldTransform: LayerTransform,
  draft: FilterDraft,
) {
  switch (draft.kind) {
    case 'contrast':
      return contrast(layer, draft.contrast, selection, worldTransform);
    case 'levels':
      return levels(
        layer,
        {
          inputBlack: draft.inputBlack,
          gamma: draft.gamma,
          inputWhite: draft.inputWhite,
          outputBlack: draft.outputBlack,
          outputWhite: draft.outputWhite,
        },
        selection,
        worldTransform,
      );
    case 'hue-saturation':
      return hueSaturation(
        layer,
        { hue: draft.hue, saturation: draft.saturation, lightness: draft.lightness },
        selection,
        worldTransform,
      );
    case 'invert':
      return invert(layer, selection, worldTransform);
    case 'grayscale':
      return grayscale(layer, selection, worldTransform);
    case 'threshold':
      return threshold(layer, draft.threshold, selection, worldTransform);
    case 'posterize':
      return posterize(layer, draft.posterizeLevels, selection, worldTransform);
    case 'gaussian-blur':
      return gaussianBlur(layer, draft.blurRadius, selection, worldTransform);
  }
}

export function createFilterPatches(
  layer: PixelLayer,
  selection: SelectionMask | null,
  worldTransform: LayerTransform,
  draft: FilterDraft,
  observer?: FilterPatchObserver,
): readonly PixelPatch[] {
  if (draft.kind === 'gaussian-blur') {
    return gaussianBlurPatches(layer, draft.blurRadius, selection, worldTransform, observer);
  }
  const filter =
    draft.kind === 'contrast'
      ? ({ kind: 'contrast', amount: draft.contrast } as const)
      : draft.kind === 'levels'
        ? ({
            kind: 'levels',
            options: {
              inputBlack: draft.inputBlack,
              gamma: draft.gamma,
              inputWhite: draft.inputWhite,
              outputBlack: draft.outputBlack,
              outputWhite: draft.outputWhite,
            },
          } as const)
        : draft.kind === 'hue-saturation'
          ? ({
              kind: 'hue-saturation',
              options: {
                hue: draft.hue,
                saturation: draft.saturation,
                lightness: draft.lightness,
              },
            } as const)
          : draft.kind === 'invert'
            ? ({ kind: 'invert' } as const)
            : draft.kind === 'grayscale'
              ? ({ kind: 'grayscale' } as const)
              : draft.kind === 'threshold'
                ? ({ kind: 'threshold', level: draft.threshold } as const)
                : ({ kind: 'posterize', levels: draft.posterizeLevels } as const);
  return pointwiseRgbFilterPatches(layer, filter, selection, worldTransform, observer);
}
