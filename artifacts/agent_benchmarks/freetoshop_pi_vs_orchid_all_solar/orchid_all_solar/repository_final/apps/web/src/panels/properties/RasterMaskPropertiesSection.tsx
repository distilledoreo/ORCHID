import type { Layer } from '@freetoshop/core-document';
import { PropertySection } from './PropertySection.js';

export interface RasterMaskPropertiesSectionProps {
  readonly layer: Layer;
  readonly editTarget: 'pixels' | 'mask';
  readonly densityDraft: number;
  readonly selectionAvailable: boolean;
  readonly onEditTarget: (target: 'pixels' | 'mask') => void;
  readonly onEnabled: (enabled: boolean) => void;
  readonly onDensityPreview: (value: number) => void;
  readonly onDensityCommit: (value: number) => void;
  readonly onInvert: () => void;
  readonly onDelete: () => void;
  readonly onCreateRevealAll: () => void;
  readonly onCreateHideAll: () => void;
  readonly onCreateFromSelection: () => void;
}

export function RasterMaskPropertiesSection({
  layer,
  editTarget,
  densityDraft,
  selectionAvailable,
  onEditTarget,
  onEnabled,
  onDensityPreview,
  onDensityCommit,
  onInvert,
  onDelete,
  onCreateRevealAll,
  onCreateHideAll,
  onCreateFromSelection,
}: RasterMaskPropertiesSectionProps) {
  if (!layer.rasterMask) {
    return (
      <PropertySection title="Raster mask" defaultOpen>
        <div className="mask-actions">
          <button className="action-button" onClick={onCreateRevealAll}>
            Add reveal-all mask
          </button>
          <button className="action-button" onClick={onCreateHideAll}>
            Add hide-all mask
          </button>
          <button
            className="action-button"
            disabled={!selectionAvailable}
            onClick={onCreateFromSelection}
          >
            Add mask from selection
          </button>
        </div>
      </PropertySection>
    );
  }
  return (
    <PropertySection title="Raster mask" defaultOpen>
      <div className="mask-fields">
        <fieldset className="target-picker">
          <legend>Paint target</legend>
          <label>
            <input
              type="radio"
              name={`paint-target-${layer.id}`}
              value="pixels"
              disabled={layer.kind !== 'pixel'}
              checked={editTarget === 'pixels'}
              onChange={() => onEditTarget('pixels')}
            />
            Layer pixels
          </label>
          <label>
            <input
              type="radio"
              name={`paint-target-${layer.id}`}
              value="mask"
              checked={editTarget === 'mask'}
              onChange={() => onEditTarget('mask')}
            />
            Layer mask
          </label>
        </fieldset>
        <label className="toggle-field">
          <input
            type="checkbox"
            checked={layer.rasterMask.enabled}
            onChange={(event) => onEnabled(event.target.checked)}
          />
          Layer mask enabled
        </label>
        <label className="property-field property-wide">
          Mask density
          <input
            aria-label="Mask density"
            aria-valuetext={`${String(densityDraft)} percent`}
            type="range"
            min="0"
            max="100"
            value={densityDraft}
            onChange={(event) => onDensityPreview(Number(event.target.value))}
            onPointerUp={(event) => onDensityCommit(Number(event.currentTarget.value))}
            onKeyUp={(event) => onDensityCommit(Number(event.currentTarget.value))}
            onBlur={(event) => onDensityCommit(Number(event.currentTarget.value))}
          />
          <output aria-label="Mask density value">{densityDraft}%</output>
        </label>
        <div className="mask-actions">
          <button className="action-button" onClick={onInvert}>
            Invert layer mask
          </button>
          <button className="action-button danger" onClick={onDelete}>
            Delete layer mask
          </button>
        </div>
      </div>
    </PropertySection>
  );
}
