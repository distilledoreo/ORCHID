import { BLEND_MODES, type Layer } from '@freetoshop/core-document';
import type { TransformDraft } from '../../editor/types.js';
import { PropertySection } from './PropertySection.js';

export interface LayerBasicsSectionProps {
  readonly layer: Layer;
  readonly brightness: number;
  readonly transformDraft: TransformDraft;
  readonly onRename: (name: string) => void;
  readonly onOpacity: (value: number) => void;
  readonly onBlendMode: (value: Layer['blendMode']) => void;
  readonly onFill: (value: number) => void;
  readonly onClipping: (value: boolean) => void;
  readonly onLock: (lock: keyof Layer['locks'], value: boolean) => void;
  readonly onTransformDraftChange: (key: 'e' | 'f', value: string) => void;
  readonly onTransformCommit: () => void;
  readonly onTransformCancel: () => void;
  readonly onBrightnessChange: (value: number) => void;
  readonly onApplyBrightness: () => void;
}

export function LayerBasicsSection({
  layer,
  brightness,
  transformDraft,
  onRename,
  onOpacity,
  onBlendMode,
  onFill,
  onClipping,
  onLock,
  onTransformDraftChange,
  onTransformCommit,
  onTransformCancel,
  onBrightnessChange,
  onApplyBrightness,
}: LayerBasicsSectionProps) {
  return (
    <PropertySection title="Layer" defaultOpen>
      <div className="property-grid">
        <label className="property-field">
          Name
          <input
            value={layer.name}
            onChange={(event) => event.target.value.trim() && onRename(event.target.value)}
          />
        </label>
        <label className="property-field">
          Opacity
          <input
            className="opacity-input"
            type="range"
            min="0"
            max="100"
            value={Math.round(layer.opacity * 100)}
            onChange={(event) => onOpacity(Number(event.target.value) / 100)}
          />
        </label>
        <label className="property-field">
          Blend mode
          <select
            aria-label="Layer blend mode"
            value={layer.blendMode}
            onChange={(event) => onBlendMode(event.target.value as Layer['blendMode'])}
          >
            {BLEND_MODES.map((mode) => (
              <option key={mode} value={mode}>
                {mode
                  .split('-')
                  .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
                  .join(' ')}
              </option>
            ))}
          </select>
        </label>
        <label className="property-field">
          Fill opacity
          <input
            aria-label="Layer fill opacity"
            type="range"
            min="0"
            max="100"
            value={Math.round(layer.fillOpacity * 100)}
            onChange={(event) => onFill(Number(event.target.value) / 100)}
          />
        </label>
        <label className="toggle-field property-wide">
          <input
            type="checkbox"
            checked={layer.clipping}
            onChange={(event) => onClipping(event.target.checked)}
          />
          Clip to layer below
        </label>
        <fieldset className="layer-locks property-wide">
          <legend>Layer locks</legend>
          {(['transparency', 'pixels', 'position', 'all'] as const).map((lock) => (
            <label key={lock}>
              <input
                type="checkbox"
                checked={layer.locks[lock]}
                onChange={(event) => onLock(lock, event.target.checked)}
              />
              {lock.charAt(0).toUpperCase() + lock.slice(1)}
            </label>
          ))}
        </fieldset>
        {(['e', 'f'] as const).map((key) => (
          <label className="property-field" key={key}>
            Position {key === 'e' ? 'X' : 'Y'}
            <input
              type="number"
              step="0.1"
              aria-label={`Layer position ${key === 'e' ? 'X' : 'Y'}`}
              value={transformDraft[key]}
              onChange={(event) => onTransformDraftChange(key, event.target.value)}
              onBlur={onTransformCommit}
              onKeyDown={(event) => {
                if (event.key === 'Enter') event.currentTarget.blur();
                if (event.key === 'Escape') {
                  event.preventDefault();
                  onTransformCancel();
                }
              }}
            />
          </label>
        ))}
        <output className="sr-only" aria-label="Layer position">
          {layer.transform.e}, {layer.transform.f}
        </output>
        {layer.kind === 'pixel' && (
          <label className="property-field property-wide">
            Brightness
            <input
              aria-label="Brightness"
              type="range"
              min="-255"
              max="255"
              value={brightness}
              onChange={(event) => onBrightnessChange(Number(event.target.value))}
            />
            <span>{brightness > 0 ? `+${String(brightness)}` : brightness}</span>
            <button
              className="action-button"
              disabled={brightness === 0}
              onClick={onApplyBrightness}
            >
              Apply brightness
            </button>
          </label>
        )}
      </div>
    </PropertySection>
  );
}
