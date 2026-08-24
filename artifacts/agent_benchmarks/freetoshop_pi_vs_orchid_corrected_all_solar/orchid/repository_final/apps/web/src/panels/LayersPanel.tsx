import type { MouseEvent } from 'react';
import { BLEND_MODES, type Layer } from '@freetoshop/core-document';
import { Icon } from '../ui/Icon.js';
import { LayerThumbnail, MaskThumbnail } from './LayerThumbnail.js';
import type { LayerFilterKind } from './layerFiltering.js';

export function LayersPanelHeader({
  layers,
  visibleLayers,
  searchQuery,
  filterKind,
  onSearchQueryChange,
  onFilterKindChange,
  onClearFilter,
  selectedLayer,
  onBlendMode,
  onOpacity,
  onFill,
}: {
  readonly layers: number;
  readonly visibleLayers: number;
  readonly searchQuery: string;
  readonly filterKind: LayerFilterKind;
  readonly onSearchQueryChange: (value: string) => void;
  readonly onFilterKindChange: (value: LayerFilterKind) => void;
  readonly onClearFilter: () => void;
  readonly selectedLayer: Layer | null | undefined;
  readonly onBlendMode: (value: Layer['blendMode']) => void;
  readonly onOpacity: (value: number) => void;
  readonly onFill: (value: number) => void;
}) {
  return (
    <section className="panel-section" aria-labelledby="layers-heading">
      <h2 id="layers-heading">
        Layers · {visibleLayers}/{layers}
      </h2>
      <div className="layer-filter-bar">
        <label className="layer-search-field">
          <Icon name="search" />
          <input
            aria-label="Search layers"
            type="search"
            value={searchQuery}
            placeholder="Search layers"
            onChange={(event) => onSearchQueryChange(event.target.value)}
          />
        </label>
        <label className="layer-filter-field">
          <Icon name="filter" />
          <select
            aria-label="Filter layers"
            value={filterKind}
            onChange={(event) => onFilterKindChange(event.target.value as LayerFilterKind)}
          >
            <option value="all">All</option>
            <option value="pixel">Pixel</option>
            <option value="text">Text</option>
            <option value="shape">Shape</option>
            <option value="adjustment">Adjustment</option>
            <option value="smart-object">Smart Object</option>
            <option value="group">Group</option>
            <option value="masked">Has Mask</option>
            <option value="effects">Has Effects</option>
          </select>
        </label>
        <button
          type="button"
          className="icon-button layer-filter-clear"
          aria-label="Clear layer search and filter"
          title="Clear layer search and filter"
          disabled={!searchQuery && filterKind === 'all'}
          onClick={onClearFilter}
        >
          <Icon name="close" />
        </button>
      </div>
      <div className="layers-top-controls">
        <label>
          Blend Mode
          <select
            aria-label="Layers panel blend mode"
            value={selectedLayer?.blendMode ?? 'normal'}
            disabled={!selectedLayer}
            onChange={(event) => onBlendMode(event.target.value as Layer['blendMode'])}
          >
            {BLEND_MODES.map((mode) => (
              <option value={mode} key={mode}>
                {mode}
              </option>
            ))}
          </select>
        </label>
        <label>
          Opacity
          <input
            aria-label="Layer opacity"
            type="number"
            min="0"
            max="100"
            value={Math.round((selectedLayer?.opacity ?? 1) * 100)}
            disabled={!selectedLayer}
            onChange={(event) => onOpacity(Number(event.target.value) / 100)}
          />
        </label>
        <label>
          Fill
          <input
            aria-label="Layer fill"
            type="number"
            min="0"
            max="100"
            value={Math.round((selectedLayer?.fillOpacity ?? 1) * 100)}
            disabled={!selectedLayer}
            onChange={(event) => onFill(Number(event.target.value) / 100)}
          />
        </label>
      </div>
    </section>
  );
}

export function LayerRow({
  layer,
  depth,
  selected,
  editTarget,
  onEditTargetChange,
  onSelect,
  onVisibility,
  onContextMenu,
}: {
  readonly layer: Layer;
  readonly depth: number;
  readonly selected: boolean;
  readonly editTarget: 'pixels' | 'mask' | null;
  readonly onEditTargetChange: (target: 'pixels' | 'mask') => void;
  readonly onSelect: (event: MouseEvent<HTMLButtonElement>) => void;
  readonly onVisibility: () => void;
  readonly onContextMenu: (event: MouseEvent<HTMLDivElement>) => void;
}) {
  return (
    <div
      className="layer-row"
      role="listitem"
      data-selected={selected}
      data-edit-target={selected ? (editTarget ?? undefined) : undefined}
      onContextMenu={onContextMenu}
    >
      <button
        className="icon-button"
        aria-label={`${layer.visible ? 'Hide' : 'Show'} ${layer.name}`}
        onClick={onVisibility}
      >
        <Icon name="eye" />
      </button>
      <span className="layer-preview-stack">
        <LayerThumbnail
          layer={layer}
          active={selected && editTarget === 'pixels'}
          onActivate={() => onEditTargetChange('pixels')}
        />
        {layer.rasterMask && (
          <MaskThumbnail
            mask={layer.rasterMask}
            layerName={layer.name}
            active={selected && editTarget === 'mask'}
            onActivate={() => onEditTargetChange('mask')}
          />
        )}
        {layer.vectorMask && (
          <span
            className="layer-vector-mask"
            title={layer.vectorMask.enabled ? 'Vector Mask' : 'Disabled Vector Mask'}
            aria-hidden="true"
          >
            <Icon name="vectorMask" />
          </span>
        )}
      </span>
      <button
        className="layer-row-button"
        aria-label={layerAccessibleName(layer)}
        aria-pressed={selected}
        style={{ paddingLeft: `${String(Math.max(0, depth) * 12)}px` }}
        onClick={onSelect}
      >
        {layer.kind === 'group' && (
          <span className="layer-disclosure" aria-hidden="true">
            <Icon name="chevron" />
          </span>
        )}
        <span className="layer-name">{layer.name}</span>
        <span className="layer-kind-icon" title={layer.kind} aria-hidden="true">
          {layer.kind === 'text' ? (
            <Icon name="text" />
          ) : layer.kind === 'adjustment' ? (
            <Icon name="adjustment" />
          ) : layer.kind === 'shape' ? (
            <Icon name="shape" />
          ) : layer.kind === 'smart-object' ? (
            <Icon name="smartObject" />
          ) : (
            ''
          )}
        </span>
        {layer.clipping && (
          <span className="layer-clipping" title="Clipping Mask" aria-hidden="true">
            <Icon name="clipping" />
          </span>
        )}
        {layer.effects.some((effect) => effect.enabled) && (
          <span
            className="layer-fx"
            title={`${String(layer.effects.length)} layer effects`}
            aria-hidden="true"
          >
            <Icon name="fx" />
          </span>
        )}
        {Object.values(layer.locks).some(Boolean) && (
          <span className="layer-lock" title="Locked" aria-hidden="true">
            <Icon name="lock" />
          </span>
        )}
        <span className="sr-only">
          {layer.kind}; {layer.rasterMask ? 'has layer mask; ' : ''}
          {layer.vectorMask ? 'has vector mask; ' : ''}
          {layer.clipping ? 'clipping mask; ' : ''}
          {Object.values(layer.locks).some(Boolean) ? 'locked' : ''}
        </span>
      </button>
    </div>
  );
}

function layerAccessibleName(layer: Layer): string {
  return `${layer.name}${layer.kind === 'group' ? ` · ${String(layer.children.length)} layers` : ''}${layer.rasterMask ? ` · ${layer.rasterMask.enabled ? 'mask' : 'mask disabled'}` : ''}${layer.vectorMask ? ` · ${layer.vectorMask.enabled ? 'vector mask' : 'vector mask disabled'}` : ''}${layer.kind === 'adjustment' ? ' · adjustment' : ''}${layer.kind === 'shape' ? ` · ${layer.geometry.type}` : ''}${layer.kind === 'text' ? ' · editable text' : ''}${layer.kind === 'smart-object' ? ` · smart object${layer.smartFilters.length > 0 ? ` · smart filters ${String(layer.smartFilters.length)}` : ''}` : ''}${layer.blendMode !== 'normal' ? ` · ${layer.blendMode}` : ''}${layer.clipping ? ' · clipped' : ''}${layer.effects.some((effect) => effect.enabled) ? ` · fx ${String(layer.effects.length)}` : ''}${Object.values(layer.locks).some(Boolean) ? ' · locked' : ''}`;
}
