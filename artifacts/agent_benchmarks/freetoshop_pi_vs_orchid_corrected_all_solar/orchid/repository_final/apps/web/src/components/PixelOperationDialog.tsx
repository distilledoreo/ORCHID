import type { Dispatch, SetStateAction } from 'react';
import { ModalDialog } from './ModalDialog.js';
import { FILTER_LABELS, type FilterDraft } from '../editor/filterOperations.js';

export interface PixelOperationDialogProps {
  readonly open: boolean;
  readonly mode: 'destructive' | 'smart';
  readonly draft: FilterDraft;
  readonly selectedLayerName: string;
  readonly selectionActive: boolean;
  readonly error: string | null;
  readonly busy: boolean;
  readonly onDraftChange: Dispatch<SetStateAction<FilterDraft>>;
  readonly onCancel: () => void;
  readonly onApply: () => void;
}

export function PixelOperationDialog({
  open,
  mode,
  draft,
  selectedLayerName,
  selectionActive,
  error,
  busy,
  onDraftChange,
  onCancel,
  onApply,
}: PixelOperationDialogProps) {
  const set = <K extends keyof FilterDraft>(key: K, value: FilterDraft[K]) =>
    onDraftChange((current) => ({ ...current, [key]: value }));
  return (
    <ModalDialog open={open} title={FILTER_LABELS[draft.kind]} onCancel={onCancel}>
      <form
        className="size-form filter-form"
        onSubmit={(event) => {
          event.preventDefault();
          onApply();
        }}
      >
        <p className="dialog-summary">
          {mode === 'smart'
            ? `Live preview of a non-destructive smart filter on ${selectedLayerName}. Apply commits one undo step.`
            : `Live preview on ${selectedLayerName}. Applying changes pixels destructively and creates one undo step.`}
          {selectionActive ? ' Inside the current selection.' : ''}
        </p>
        {draft.kind === 'contrast' && (
          <label>
            Contrast: {draft.contrast}
            <input
              aria-label="Contrast amount"
              type="range"
              min="-100"
              max="100"
              value={draft.contrast}
              onChange={(event) => set('contrast', Number(event.target.value))}
            />
          </label>
        )}
        {draft.kind === 'levels' && (
          <div className="filter-grid" aria-label="Levels settings">
            {(
              [
                ['inputBlack', 'Input black', 0, 254, 1],
                ['gamma', 'Gamma', 0.01, 9.99, 0.01],
                ['inputWhite', 'Input white', 1, 255, 1],
                ['outputBlack', 'Output black', 0, 255, 1],
                ['outputWhite', 'Output white', 0, 255, 1],
              ] as const
            ).map(([key, label, min, max, step]) => (
              <label key={key}>
                {label}
                <input
                  aria-label={label}
                  type="number"
                  min={min}
                  max={max}
                  step={step}
                  value={draft[key]}
                  onChange={(event) => set(key, Number(event.target.value))}
                />
              </label>
            ))}
          </div>
        )}
        {draft.kind === 'hue-saturation' && (
          <div className="filter-grid" aria-label="Hue and saturation settings">
            {(
              [
                ['hue', 'Hue', -180, 180],
                ['saturation', 'Saturation', -100, 100],
                ['lightness', 'Lightness', -100, 100],
              ] as const
            ).map(([key, label, min, max]) => (
              <label key={key}>
                {label}: {draft[key]}
                <input
                  aria-label={label}
                  type="range"
                  min={min}
                  max={max}
                  value={draft[key]}
                  onChange={(event) => set(key, Number(event.target.value))}
                />
              </label>
            ))}
          </div>
        )}
        {draft.kind === 'threshold' && (
          <label>
            Threshold: {draft.threshold}
            <input
              aria-label="Threshold level"
              type="range"
              min="0"
              max="255"
              value={draft.threshold}
              onChange={(event) => set('threshold', Number(event.target.value))}
            />
          </label>
        )}
        {draft.kind === 'posterize' && (
          <label>
            Levels: {draft.posterizeLevels}
            <input
              aria-label="Posterize levels"
              type="range"
              min="2"
              max="256"
              value={draft.posterizeLevels}
              onChange={(event) => set('posterizeLevels', Number(event.target.value))}
            />
          </label>
        )}
        {draft.kind === 'gaussian-blur' && (
          <label>
            Radius: {draft.blurRadius}
            <input
              aria-label="Gaussian Blur radius"
              type="range"
              min="0"
              max="100"
              value={draft.blurRadius}
              onChange={(event) => set('blurRadius', Number(event.target.value))}
            />
          </label>
        )}
        {error && (
          <p className="dialog-error" role="alert">
            {error}
          </p>
        )}
        <div className="dialog-actions">
          <button className="action-button" type="button" onClick={onCancel}>
            Cancel
          </button>
          <button className="action-button primary" type="submit" disabled={Boolean(error) || busy}>
            {mode === 'smart' ? 'Apply smart filter' : `Apply ${FILTER_LABELS[draft.kind]}`}
          </button>
        </div>
      </form>
    </ModalDialog>
  );
}
