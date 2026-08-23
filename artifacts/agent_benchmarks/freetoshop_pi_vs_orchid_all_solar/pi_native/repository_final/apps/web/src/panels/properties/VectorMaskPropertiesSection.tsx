import type { Layer, PathCommand } from '@freetoshop/core-document';
import { PropertySection } from './PropertySection.js';

export interface VectorMaskPropertiesSectionProps {
  readonly layer: Layer;
  readonly densityDraft: number;
  readonly onEnabled: (enabled: boolean) => void;
  readonly onDensityChange: (value: number) => void;
  readonly onDensityCommit: (value: number) => void;
  readonly onInvert: () => void;
  readonly onDelete: () => void;
  readonly onCreate: () => void;
  readonly onUpdateCommand: (index: number, command: PathCommand) => void;
}

export function VectorMaskPropertiesSection({
  layer,
  densityDraft,
  onEnabled,
  onDensityChange,
  onDensityCommit,
  onInvert,
  onDelete,
  onCreate,
  onUpdateCommand,
}: VectorMaskPropertiesSectionProps) {
  if (!layer.vectorMask)
    return (
      <PropertySection title="Vector mask" defaultOpen>
        <button className="action-button" onClick={onCreate}>
          Add rectangular vector mask
        </button>
      </PropertySection>
    );
  return (
    <PropertySection title="Vector mask" defaultOpen>
      <div className="mask-fields">
        <p className="field-help">
          {layer.vectorMask.commands.length} editable path commands ·{' '}
          {layer.kind === 'group' || layer.kind === 'adjustment'
            ? 'document-space vector coverage'
            : 'local vector coverage'}
        </p>
        <div className="path-editor">
          {layer.vectorMask.commands.map((command, index) => (
            <fieldset className="smart-filter-card" key={`${command.type}-${String(index)}`}>
              <legend>
                {command.type === 'close' ? 'Close path' : `${command.type} ${String(index + 1)}`}
              </legend>
              {command.type !== 'close' && (
                <>
                  <label className="property-field">
                    Anchor X
                    <input
                      aria-label={`Vector mask command ${String(index + 1)} anchor X`}
                      type="number"
                      value={command.x}
                      onChange={(event) =>
                        onUpdateCommand(index, { ...command, x: Number(event.target.value) })
                      }
                    />
                  </label>
                  <label className="property-field">
                    Anchor Y
                    <input
                      aria-label={`Vector mask command ${String(index + 1)} anchor Y`}
                      type="number"
                      value={command.y}
                      onChange={(event) =>
                        onUpdateCommand(index, { ...command, y: Number(event.target.value) })
                      }
                    />
                  </label>
                </>
              )}
              {command.type === 'cubic' &&
                (['control1X', 'control1Y', 'control2X', 'control2Y'] as const).map((field) => (
                  <label className="property-field" key={field}>
                    {field}
                    <input
                      type="number"
                      value={command[field]}
                      onChange={(event) =>
                        onUpdateCommand(index, { ...command, [field]: Number(event.target.value) })
                      }
                    />
                  </label>
                ))}
            </fieldset>
          ))}
        </div>
        <label className="toggle-field">
          <input
            type="checkbox"
            checked={layer.vectorMask.enabled}
            onChange={(event) => onEnabled(event.target.checked)}
          />
          Vector mask enabled
        </label>
        <label className="property-field property-wide">
          Vector mask density
          <input
            aria-label="Vector mask density"
            aria-valuetext={`${String(densityDraft)} percent`}
            type="range"
            min="0"
            max="100"
            value={densityDraft}
            onChange={(event) => onDensityChange(Number(event.target.value))}
            onPointerUp={(event) => onDensityCommit(Number(event.currentTarget.value))}
            onKeyUp={(event) => onDensityCommit(Number(event.currentTarget.value))}
            onBlur={(event) => onDensityCommit(Number(event.currentTarget.value))}
          />
          <output aria-label="Vector mask density value">{densityDraft}%</output>
        </label>
        <div className="mask-actions">
          <button className="action-button" onClick={onInvert}>
            {layer.vectorMask.inverted ? 'Restore vector mask direction' : 'Invert vector mask'}
          </button>
          <button className="action-button danger" onClick={onDelete}>
            Delete vector mask
          </button>
        </div>
      </div>
    </PropertySection>
  );
}
