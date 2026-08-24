import { useState } from 'react';
import type { Layer, LayerTransform } from '@freetoshop/core-document';
import { Icon } from '../ui/Icon.js';

function rounded(value: number): number {
  return Math.round(value * 100) / 100;
}

export function PropertiesPanel({
  documentName,
  canvas,
  layer,
  layerSize,
  editTarget,
  contextOverride,
  onTransform,
}: {
  readonly documentName: string | null;
  readonly canvas: { readonly width: number; readonly height: number } | null;
  readonly layer: Layer | null | undefined;
  readonly layerSize: { readonly width: number; readonly height: number } | null;
  readonly editTarget: 'pixels' | 'mask';
  readonly contextOverride: string | null;
  readonly onTransform: (transform: LayerTransform) => void;
}) {
  const [constrained, setConstrained] = useState(true);
  const transform = layer?.transform;
  const scaleX = transform ? Math.hypot(transform.a, transform.b) : 1;
  const scaleY = transform ? Math.hypot(transform.c, transform.d) : 1;
  const width = (layerSize?.width ?? 0) * scaleX;
  const height = (layerSize?.height ?? 0) * scaleY;
  const rotation = transform ? (Math.atan2(transform.b, transform.a) * 180) / Math.PI : 0;
  const context =
    contextOverride ??
    (!documentName
      ? 'No Document'
      : !layer
        ? 'Document'
        : editTarget === 'mask'
          ? 'Raster Mask'
          : layer.kind === 'smart-object'
            ? 'Smart Object'
            : `${layer.kind.charAt(0).toUpperCase()}${layer.kind.slice(1)} Layer`);

  const updateWidth = (nextWidth: number) => {
    if (!transform || !layerSize || nextWidth <= 0) return;
    const nextScaleX = nextWidth / layerSize.width;
    const ratio = constrained && width > 0 ? nextWidth / width : 1;
    const radians = (rotation * Math.PI) / 180;
    onTransform({
      ...transform,
      a: Math.cos(radians) * nextScaleX,
      b: Math.sin(radians) * nextScaleX,
      c: constrained ? transform.c * ratio : transform.c,
      d: constrained ? transform.d * ratio : transform.d,
    });
  };
  const updateHeight = (nextHeight: number) => {
    if (!transform || !layerSize || nextHeight <= 0) return;
    const nextScaleY = nextHeight / layerSize.height;
    const ratio = constrained && height > 0 ? nextHeight / height : 1;
    const radians = (rotation * Math.PI) / 180;
    onTransform({
      ...transform,
      a: constrained ? transform.a * ratio : transform.a,
      b: constrained ? transform.b * ratio : transform.b,
      c: -Math.sin(radians) * nextScaleY,
      d: Math.cos(radians) * nextScaleY,
    });
  };
  const updateRotation = (degrees: number) => {
    if (!transform) return;
    const radians = (degrees * Math.PI) / 180;
    onTransform({
      ...transform,
      a: Math.cos(radians) * scaleX,
      b: Math.sin(radians) * scaleX,
      c: -Math.sin(radians) * scaleY,
      d: Math.cos(radians) * scaleY,
    });
  };

  return (
    <section className="panel-section compact-properties" aria-labelledby="properties-heading">
      <header className="properties-context-header">
        <div>
          <h2 id="properties-heading">Properties</h2>
          <h3 id="properties-context-heading">{context}</h3>
          <p>{layer?.name ?? documentName ?? 'Open or create a document'}</p>
        </div>
        {layer && <span className="properties-kind-badge">{layer.kind}</span>}
      </header>

      {layer && transform && layerSize ? (
        <fieldset className="properties-transform-grid">
          <legend>Transform</legend>
          <label>
            W
            <input
              aria-label="Property width"
              type="number"
              value={rounded(width)}
              onChange={(event) => updateWidth(Number(event.target.value))}
            />
          </label>
          <label>
            H
            <input
              aria-label="Property height"
              type="number"
              value={rounded(height)}
              onChange={(event) => updateHeight(Number(event.target.value))}
            />
          </label>
          <button
            className="property-constrain"
            aria-label="Constrain layer proportions"
            aria-pressed={constrained}
            title="Constrain proportions"
            onClick={() => setConstrained((value) => !value)}
          >
            <Icon name="link" />
          </button>
          <label>
            X
            <input
              aria-label="Property position X"
              type="number"
              value={rounded(transform.e)}
              onChange={(event) => onTransform({ ...transform, e: Number(event.target.value) })}
            />
          </label>
          <label>
            Y
            <input
              aria-label="Property position Y"
              type="number"
              value={rounded(transform.f)}
              onChange={(event) => onTransform({ ...transform, f: Number(event.target.value) })}
            />
          </label>
          <label className="property-rotation">
            Rotation
            <input
              aria-label="Property rotation"
              type="number"
              value={rounded(rotation)}
              onChange={(event) => updateRotation(Number(event.target.value))}
            />
          </label>
        </fieldset>
      ) : (
        <dl className="document-property-summary">
          <div>
            <dt>Document</dt>
            <dd>{documentName ?? 'No document'}</dd>
          </div>
          <div>
            <dt>Canvas</dt>
            <dd>{canvas ? `${String(canvas.width)} × ${String(canvas.height)} px` : '—'}</dd>
          </div>
        </dl>
      )}

      {layer && (
        <div className="properties-icon-row" aria-label="Transform quick actions">
          <button
            title="Flip horizontal"
            onClick={() =>
              onTransform({ ...layer.transform, a: -layer.transform.a, b: -layer.transform.b })
            }
          >
            Flip H
          </button>
          <button
            title="Flip vertical"
            onClick={() =>
              onTransform({ ...layer.transform, c: -layer.transform.c, d: -layer.transform.d })
            }
          >
            Flip V
          </button>
          <span>{editTarget === 'mask' ? 'Editing Layer Mask' : 'Editing Layer'}</span>
        </div>
      )}
    </section>
  );
}
