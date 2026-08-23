/* eslint-disable @typescript-eslint/no-explicit-any, @typescript-eslint/no-unsafe-assignment, @typescript-eslint/no-unsafe-call, @typescript-eslint/no-unsafe-member-access, @typescript-eslint/no-unsafe-return */
import {
  BLEND_MODES,
  type Adjustment,
  type Layer,
  type LayerEffect,
  type PathCommand,
  type SmartFilter,
} from '@freetoshop/core-document';
import { pathCommandAnchor, transformDraft } from '../../editor/model.js';
import type { TransformDraft } from '../../editor/types.js';
import { LayerBasicsSection } from './LayerBasicsSection.js';
import { RasterMaskPropertiesSection } from './RasterMaskPropertiesSection.js';
import { VectorMaskPropertiesSection } from './VectorMaskPropertiesSection.js';

export interface LayerPropertiesSectionsProps {
  readonly selectedLayer: Layer | null | undefined;
  readonly [key: string]: unknown;
}

export function LayerPropertiesSections(props: LayerPropertiesSectionsProps) {
  // Presentation-only extraction: command/history behavior remains owned by App.
  const { selectedLayer } = props;
  const {
    addAdjustmentMask,
    addHideAllMask,
    addLayerEffect,
    addLayerMaskFromSelection,
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
    previewMaskDensity,
    removeLayerEffect,
    removeSmartFilter,
    renameLayer,
    runCommand,
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
  } = props as unknown as Record<string, any>;
  const setLayerTransformDraft = props.setLayerTransformDraft as (
    value: TransformDraft | ((current: TransformDraft) => TransformDraft),
  ) => void;
  if (!selectedLayer) return null;
  return (
    <section className="panel-section" aria-labelledby="properties-detail-heading">
      <h2 id="properties-detail-heading">Selected layer</h2>
      <LayerBasicsSection
        layer={selectedLayer}
        brightness={brightness}
        transformDraft={layerTransformDraft}
        onRename={(name) => runCommand(renameLayer(selectedLayer.id, name))}
        onOpacity={(value) => runCommand(setLayerOpacity(selectedLayer.id, value))}
        onBlendMode={(value) => runCommand(setLayerBlendMode(selectedLayer.id, value))}
        onFill={(value) => runCommand(setLayerFillOpacity(selectedLayer.id, value))}
        onClipping={(value) => runCommand(setLayerClipping(selectedLayer.id, value))}
        onLock={(lock, value) => runCommand(setLayerLock(selectedLayer.id, lock, value))}
        onTransformDraftChange={(key, value) =>
          setLayerTransformDraft((current) => ({ ...current, [key]: value }))
        }
        onTransformCommit={commitLayerTransform}
        onTransformCancel={() => setLayerTransformDraft(transformDraft(selectedLayer.transform))}
        onBrightnessChange={setBrightness}
        onApplyBrightness={applyBrightness}
      />
      <div className="property-grid">
        {selectedLayer.kind === 'shape' && (
          <div className="adjustment-controls property-wide">
            <h3>Shape</h3>
            <label className="property-field">
              Shape type
              <select
                aria-label="Shape type"
                value={selectedLayer.geometry.type}
                onChange={(event) => {
                  const type = event.target.value as 'rectangle' | 'ellipse' | 'path';
                  const { width, height } = selectedLayer.geometry;
                  runCommand(
                    setShapeGeometry(selectedLayer.id, {
                      ...selectedLayer.geometry,
                      type,
                      cornerRadius:
                        type === 'rectangle'
                          ? Math.min(
                              selectedLayer.geometry.cornerRadius,
                              Math.min(width, height) / 2,
                            )
                          : 0,
                      commands:
                        type === 'path'
                          ? [
                              { type: 'move', x: width / 2, y: 0 },
                              { type: 'line', x: width, y: height },
                              { type: 'line', x: 0, y: height },
                              { type: 'close' },
                            ]
                          : [],
                    }),
                  );
                }}
              >
                <option value="rectangle">Rectangle</option>
                <option value="ellipse">Ellipse</option>
                <option value="path">Path</option>
              </select>
            </label>
            {(['width', 'height'] as const).map((field) => (
              <label className="property-field" key={field}>
                {field === 'width' ? 'Width' : 'Height'}
                <input
                  aria-label={`Shape ${field}`}
                  type="number"
                  min="1"
                  step="1"
                  value={selectedLayer.geometry[field]}
                  onChange={(event) => {
                    const value = Number(event.target.value);
                    if (Number.isInteger(value) && value > 0)
                      runCommand(
                        setShapeGeometry(selectedLayer.id, {
                          ...selectedLayer.geometry,
                          [field]: value,
                        }),
                      );
                  }}
                />
              </label>
            ))}
            {selectedLayer.geometry.type === 'rectangle' && (
              <label className="property-field">
                Corner radius
                <input
                  aria-label="Shape corner radius"
                  type="number"
                  min="0"
                  step="1"
                  value={selectedLayer.geometry.cornerRadius}
                  onChange={(event) => {
                    const value = Number(event.target.value);
                    if (Number.isFinite(value) && value >= 0)
                      runCommand(
                        setShapeGeometry(selectedLayer.id, {
                          ...selectedLayer.geometry,
                          cornerRadius: value,
                        }),
                      );
                  }}
                />
              </label>
            )}
            {selectedLayer.geometry.type === 'path' && (
              <div className="path-editor property-wide">
                <h3>Path commands · {selectedLayer.geometry.commands.length}</h3>
                {selectedLayer.geometry.commands.map((command, index) => {
                  const previous = selectedLayer.geometry.commands
                    .slice(0, index)
                    .reverse()
                    .map(pathCommandAnchor)
                    .find((point) => point !== null);
                  return (
                    <fieldset className="smart-filter-card" key={index}>
                      <legend>
                        {command.type === 'move'
                          ? 'Move'
                          : command.type === 'line'
                            ? `Line ${String(index)}`
                            : command.type === 'cubic'
                              ? `Curve ${String(index)}`
                              : 'Close path'}
                      </legend>
                      {command.type !== 'close' && (
                        <>
                          {(['x', 'y'] as const).map((field) => (
                            <label className="property-field" key={field}>
                              Anchor {field.toUpperCase()}
                              <input
                                aria-label={`Path command ${String(index + 1)} anchor ${field.toUpperCase()}`}
                                type="number"
                                step="0.1"
                                value={command[field]}
                                onChange={(event) => {
                                  const value = Number(event.target.value);
                                  if (!Number.isFinite(value)) return;
                                  const commands = selectedLayer.geometry.commands.map(
                                    (candidate, commandIndex) =>
                                      commandIndex === index
                                        ? { ...candidate, [field]: value }
                                        : candidate,
                                  );
                                  runCommand(
                                    setShapeGeometry(selectedLayer.id, {
                                      ...selectedLayer.geometry,
                                      commands,
                                    }),
                                  );
                                }}
                              />
                            </label>
                          ))}
                        </>
                      )}
                      {command.type === 'cubic' && (
                        <>
                          {(
                            [
                              ['control1X', 'Control 1 X'],
                              ['control1Y', 'Control 1 Y'],
                              ['control2X', 'Control 2 X'],
                              ['control2Y', 'Control 2 Y'],
                            ] as const
                          ).map(([field, label]) => (
                            <label className="property-field" key={field}>
                              {label}
                              <input
                                aria-label={`Path command ${String(index + 1)} ${label}`}
                                type="number"
                                step="0.1"
                                value={command[field]}
                                onChange={(event) => {
                                  const value = Number(event.target.value);
                                  if (!Number.isFinite(value)) return;
                                  const commands = selectedLayer.geometry.commands.map(
                                    (candidate, commandIndex) =>
                                      commandIndex === index
                                        ? { ...candidate, [field]: value }
                                        : candidate,
                                  );
                                  runCommand(
                                    setShapeGeometry(selectedLayer.id, {
                                      ...selectedLayer.geometry,
                                      commands,
                                    }),
                                  );
                                }}
                              />
                            </label>
                          ))}
                        </>
                      )}
                      {(command.type === 'line' || command.type === 'cubic') && (
                        <button
                          className="action-button"
                          onClick={() => {
                            const replacement: PathCommand =
                              command.type === 'cubic'
                                ? { type: 'line', x: command.x, y: command.y }
                                : {
                                    type: 'cubic',
                                    control1X:
                                      (previous?.x ?? command.x) +
                                      (command.x - (previous?.x ?? command.x)) / 3,
                                    control1Y:
                                      (previous?.y ?? command.y) +
                                      (command.y - (previous?.y ?? command.y)) / 3,
                                    control2X:
                                      (previous?.x ?? command.x) +
                                      ((command.x - (previous?.x ?? command.x)) * 2) / 3,
                                    control2Y:
                                      (previous?.y ?? command.y) +
                                      ((command.y - (previous?.y ?? command.y)) * 2) / 3,
                                    x: command.x,
                                    y: command.y,
                                  };
                            runCommand(
                              setShapeGeometry(selectedLayer.id, {
                                ...selectedLayer.geometry,
                                commands: selectedLayer.geometry.commands.map(
                                  (candidate, commandIndex) =>
                                    commandIndex === index ? replacement : candidate,
                                ),
                              }),
                            );
                          }}
                        >
                          {command.type === 'cubic'
                            ? 'Convert curve to line'
                            : 'Convert line to curve'}
                        </button>
                      )}
                    </fieldset>
                  );
                })}
                <div className="path-actions">
                  <button
                    className="action-button"
                    onClick={() => {
                      const commands = [...selectedLayer.geometry.commands];
                      if (commands.at(-1)?.type === 'close') commands.pop();
                      else commands.push({ type: 'close' });
                      runCommand(
                        setShapeGeometry(selectedLayer.id, {
                          ...selectedLayer.geometry,
                          commands,
                        }),
                      );
                    }}
                  >
                    {selectedLayer.geometry.commands.at(-1)?.type === 'close'
                      ? 'Open path'
                      : 'Close path'}
                  </button>
                  <button
                    className="action-button danger"
                    disabled={
                      selectedLayer.geometry.commands.filter((command) => command.type !== 'close')
                        .length <= 2
                    }
                    onClick={() => {
                      const wasClosed = selectedLayer.geometry.commands.at(-1)?.type === 'close';
                      const commands: PathCommand[] = selectedLayer.geometry.commands.filter(
                        (command) => command.type !== 'close',
                      );
                      commands.pop();
                      if (wasClosed && commands.length >= 3) commands.push({ type: 'close' });
                      runCommand(
                        setShapeGeometry(selectedLayer.id, {
                          ...selectedLayer.geometry,
                          commands,
                        }),
                      );
                    }}
                  >
                    Remove last anchor
                  </button>
                </div>
              </div>
            )}
            <label className="property-field">
              Fill color
              <input
                aria-label="Shape fill color"
                type="color"
                value={colorToHex(
                  selectedLayer.style.fill ?? {
                    red: 0,
                    green: 0,
                    blue: 0,
                    alpha: 255,
                  },
                )}
                onChange={(event) =>
                  runCommand(
                    setShapeStyle(selectedLayer.id, {
                      ...selectedLayer.style,
                      fill: hexToColor(event.target.value),
                    }),
                  )
                }
              />
            </label>
            <label className="property-field">
              Stroke width
              <input
                aria-label="Shape stroke width"
                type="number"
                min="0"
                step="1"
                value={selectedLayer.style.strokeWidth}
                onChange={(event) => {
                  const value = Number(event.target.value);
                  if (Number.isFinite(value) && value >= 0)
                    runCommand(
                      setShapeStyle(selectedLayer.id, {
                        ...selectedLayer.style,
                        strokeWidth: value,
                        stroke:
                          value > 0
                            ? (selectedLayer.style.stroke ?? {
                                red: 255,
                                green: 255,
                                blue: 255,
                                alpha: 255,
                              })
                            : selectedLayer.style.stroke,
                      }),
                    );
                }}
              />
            </label>
            {selectedLayer.style.strokeWidth > 0 && selectedLayer.style.stroke && (
              <label className="property-field">
                Stroke color
                <input
                  aria-label="Shape stroke color"
                  type="color"
                  value={colorToHex(selectedLayer.style.stroke)}
                  onChange={(event) =>
                    runCommand(
                      setShapeStyle(selectedLayer.id, {
                        ...selectedLayer.style,
                        stroke: hexToColor(event.target.value),
                      }),
                    )
                  }
                />
              </label>
            )}
          </div>
        )}
        {selectedLayer.kind === 'text' && (
          <div className="adjustment-controls property-wide">
            <h3>Text</h3>
            <label className="property-field property-wide">
              Content
              <textarea
                aria-label="Text content"
                rows={3}
                value={selectedLayer.text}
                onChange={(event) =>
                  runCommand(setTextContent(selectedLayer.id, event.target.value))
                }
              />
            </label>
            <label className="property-field">
              Font family
              <select
                aria-label="Text font family"
                value={selectedLayer.style.fontFamily}
                onChange={(event) =>
                  runCommand(
                    setTextStyle(selectedLayer.id, {
                      ...selectedLayer.style,
                      fontFamily: event.target.value,
                    }),
                  )
                }
              >
                {[
                  ...new Set([
                    'Arial',
                    'Georgia',
                    'Courier New',
                    'Verdana',
                    'Times New Roman',
                    ...loadedFontFamilies,
                    selectedLayer.style.fontFamily,
                  ]),
                ].map((font) => (
                  <option key={font} value={font}>
                    {font}
                  </option>
                ))}
              </select>
            </label>
            <div className="property-field property-wide">
              <button
                className="action-button"
                type="button"
                disabled={busy}
                onClick={() => fontFileRef.current?.click()}
              >
                Load local font…
              </button>
              <span role="status" aria-label="Text shaping status">
                {textEngine.hasFont(selectedLayer.style.fontFamily)
                  ? 'Professional OpenType shaping active'
                  : 'System-font fallback · load the font file for portable shaping'}
              </span>
            </div>
            <label className="property-field">
              Font size
              <input
                aria-label="Text font size"
                type="number"
                min="1"
                step="1"
                value={selectedLayer.style.fontSize}
                onChange={(event) => {
                  const value = Number(event.target.value);
                  if (Number.isFinite(value) && value > 0)
                    runCommand(
                      setTextStyle(selectedLayer.id, {
                        ...selectedLayer.style,
                        fontSize: value,
                      }),
                    );
                }}
              />
            </label>
            <label className="property-field">
              Alignment
              <select
                aria-label="Text alignment"
                value={selectedLayer.style.align}
                onChange={(event) =>
                  runCommand(
                    setTextStyle(selectedLayer.id, {
                      ...selectedLayer.style,
                      align: event.target.value as 'left' | 'center' | 'right',
                    }),
                  )
                }
              >
                <option value="left">Left</option>
                <option value="center">Center</option>
                <option value="right">Right</option>
              </select>
            </label>
            <label className="property-field">
              Text color
              <input
                aria-label="Text color"
                type="color"
                value={colorToHex(selectedLayer.style.color)}
                onChange={(event) =>
                  runCommand(
                    setTextStyle(selectedLayer.id, {
                      ...selectedLayer.style,
                      color: hexToColor(event.target.value),
                    }),
                  )
                }
              />
            </label>
            <label className="toggle-field">
              <input
                type="checkbox"
                checked={selectedLayer.style.italic}
                onChange={(event) =>
                  runCommand(
                    setTextStyle(selectedLayer.id, {
                      ...selectedLayer.style,
                      italic: event.target.checked,
                    }),
                  )
                }
              />
              Italic
            </label>
            <label className="toggle-field">
              <input
                type="checkbox"
                checked={selectedLayer.style.fontWeight >= 700}
                onChange={(event) =>
                  runCommand(
                    setTextStyle(selectedLayer.id, {
                      ...selectedLayer.style,
                      fontWeight: event.target.checked ? 700 : 400,
                    }),
                  )
                }
              />
              Bold
            </label>
          </div>
        )}
        {selectedLayer.kind === 'adjustment' && (
          <div className="adjustment-controls property-wide">
            <h3>{selectedLayer.name}</h3>
            {selectedLayer.adjustment.type === 'brightness-contrast' && (
              <>
                {(['brightness', 'contrast'] as const).map((field) => (
                  <label className="property-field" key={field}>
                    {field === 'brightness' ? 'Brightness' : 'Contrast'}
                    <input
                      type="range"
                      min="-255"
                      max="255"
                      value={
                        (
                          selectedLayer.adjustment as Extract<
                            Adjustment,
                            { type: 'brightness-contrast' }
                          >
                        )[field]
                      }
                      onChange={(event) =>
                        updateAdjustment({
                          ...(selectedLayer.adjustment as Extract<
                            Adjustment,
                            { type: 'brightness-contrast' }
                          >),
                          [field]: Number(event.target.value),
                        })
                      }
                    />
                  </label>
                ))}
              </>
            )}
            {selectedLayer.adjustment.type === 'levels' && (
              <>
                {(
                  [
                    ['inputBlack', 'Input black', 0, 254, 1],
                    ['inputWhite', 'Input white', 1, 255, 1],
                    ['gamma', 'Gamma', 0.01, 9.99, 0.01],
                    ['outputBlack', 'Output black', 0, 255, 1],
                    ['outputWhite', 'Output white', 0, 255, 1],
                  ] as const
                ).map(([field, label, min, max, step]) => (
                  <label className="property-field" key={field}>
                    {label}
                    <input
                      type="number"
                      min={min}
                      max={max}
                      step={step}
                      value={
                        (selectedLayer.adjustment as Extract<Adjustment, { type: 'levels' }>)[field]
                      }
                      onChange={(event) => {
                        const value = Number(event.target.value);
                        const next = {
                          ...(selectedLayer.adjustment as Extract<Adjustment, { type: 'levels' }>),
                          [field]: value,
                        };
                        if (
                          next.inputBlack < next.inputWhite &&
                          next.outputBlack <= next.outputWhite
                        ) {
                          updateAdjustment(next);
                        }
                      }}
                    />
                  </label>
                ))}
              </>
            )}
            {selectedLayer.adjustment.type === 'hue-saturation' && (
              <>
                {(
                  [
                    ['hue', 'Hue', -180, 180],
                    ['saturation', 'Saturation', -100, 100],
                    ['lightness', 'Lightness', -100, 100],
                  ] as const
                ).map(([field, label, min, max]) => (
                  <label className="property-field" key={field}>
                    {label}
                    <input
                      type="range"
                      min={min}
                      max={max}
                      value={
                        (
                          selectedLayer.adjustment as Extract<
                            Adjustment,
                            { type: 'hue-saturation' }
                          >
                        )[field]
                      }
                      onChange={(event) =>
                        updateAdjustment({
                          ...(selectedLayer.adjustment as Extract<
                            Adjustment,
                            { type: 'hue-saturation' }
                          >),
                          [field]: Number(event.target.value),
                        })
                      }
                    />
                  </label>
                ))}
              </>
            )}
            {selectedLayer.adjustment.type === 'invert' && (
              <p className="field-help">Invert has no adjustable parameters.</p>
            )}
          </div>
        )}
        {selectedLayer.kind === 'smart-object' && (
          <div className="adjustment-controls property-wide">
            <h3>Smart object</h3>
            <p className="field-help">Embedded source: {selectedLayer.source.name}</p>
            <p className="field-help">
              {selectedLayer.source.canvas.width} × {selectedLayer.source.canvas.height} px ·{' '}
              {selectedLayer.source.layers.length} source layers
            </p>
            <button className="action-button" onClick={editSmartObjectContents}>
              Edit contents
            </button>
            <button
              className="action-button"
              disabled={busy}
              onClick={() => smartObjectFileRef.current?.click()}
            >
              Replace contents…
            </button>
            <h3>Smart filters · {selectedLayer.smartFilters.length}</h3>
            <label className="property-field property-wide">
              Add filter
              <select
                aria-label="Add smart filter"
                value=""
                onChange={(event) => {
                  if (event.target.value) addSmartFilter(event.target.value as SmartFilter['type']);
                }}
              >
                <option value="">Choose a smart filter…</option>
                <option value="brightness-contrast">Brightness / Contrast</option>
                <option value="invert">Invert</option>
                <option value="grayscale">Grayscale</option>
                <option value="gaussian-blur">Gaussian Blur</option>
              </select>
            </label>
            {selectedLayer.smartFilters.map((filter, index) => (
              <fieldset className="smart-filter-card property-wide" key={filter.id}>
                <legend>
                  {filter.type
                    .split('-')
                    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
                    .join(' ')}
                </legend>
                <label className="toggle-field">
                  <input
                    type="checkbox"
                    aria-label={`Enable smart ${filter.type.split('-').join(' ')}`}
                    checked={filter.enabled}
                    onChange={(event) =>
                      updateSmartFilter(index, {
                        ...filter,
                        enabled: event.target.checked,
                      })
                    }
                  />
                  Enabled
                </label>
                <label className="property-field">
                  Opacity
                  <input
                    aria-label={`Smart ${filter.type} opacity`}
                    type="range"
                    min="0"
                    max="100"
                    value={Math.round(filter.opacity * 100)}
                    onChange={(event) =>
                      updateSmartFilter(index, {
                        ...filter,
                        opacity: Number(event.target.value) / 100,
                      })
                    }
                  />
                </label>
                {filter.type === 'gaussian-blur' && (
                  <label className="property-field">
                    Radius
                    <input
                      aria-label="Smart Gaussian Blur radius"
                      type="number"
                      min="0"
                      max="100"
                      value={filter.radius}
                      onChange={(event) =>
                        updateSmartFilter(index, {
                          ...filter,
                          radius: Math.max(
                            0,
                            Math.min(100, Math.round(Number(event.target.value))),
                          ),
                        })
                      }
                    />
                  </label>
                )}
                {filter.type === 'brightness-contrast' && (
                  <>
                    {(['brightness', 'contrast'] as const).map((field) => (
                      <label className="property-field" key={field}>
                        {field === 'brightness' ? 'Brightness' : 'Contrast'}
                        <input
                          aria-label={`Smart filter ${field}`}
                          type="range"
                          min="-255"
                          max="255"
                          value={filter[field]}
                          onChange={(event) =>
                            updateSmartFilter(index, {
                              ...filter,
                              [field]: Number(event.target.value),
                            })
                          }
                        />
                      </label>
                    ))}
                  </>
                )}
                <button className="action-button danger" onClick={() => removeSmartFilter(index)}>
                  Remove smart filter
                </button>
              </fieldset>
            ))}
          </div>
        )}
        <details className="transform-details property-wide">
          <summary>Affine transform matrix</summary>
          <div className="transform-matrix-grid">
            {(['a', 'b', 'c', 'd'] as const).map((key) => (
              <label className="property-field" key={key}>
                Matrix {key.toUpperCase()}
                <input
                  type="number"
                  step="0.01"
                  value={layerTransformDraft[key]}
                  onChange={(event) =>
                    setLayerTransformDraft((current) => ({
                      ...current,
                      [key]: event.target.value,
                    }))
                  }
                  onBlur={commitLayerTransform}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter') event.currentTarget.blur();
                    if (event.key === 'Escape') {
                      event.preventDefault();
                      setLayerTransformDraft(transformDraft(selectedLayer.transform));
                    }
                  }}
                />
              </label>
            ))}
          </div>
        </details>
        {selectedLayer.kind === 'pixel' && (
          <div className="transform-actions property-wide" aria-label="Transform actions">
            <button
              className="action-button"
              onClick={() =>
                applyCenteredTransform(
                  { a: 0, b: 1, c: -1, d: 0, e: 0, f: 0 },
                  'Layer rotated 90 degrees clockwise.',
                )
              }
            >
              Rotate 90° clockwise
            </button>
            <button
              className="action-button"
              onClick={() =>
                applyCenteredTransform(
                  { a: 0, b: -1, c: 1, d: 0, e: 0, f: 0 },
                  'Layer rotated 90 degrees counterclockwise.',
                )
              }
            >
              Rotate 90° counterclockwise
            </button>
            <button
              className="action-button"
              onClick={() =>
                applyCenteredTransform(
                  { a: -1, b: 0, c: 0, d: 1, e: 0, f: 0 },
                  'Layer flipped horizontally.',
                )
              }
            >
              Flip horizontal
            </button>
            <button
              className="action-button"
              onClick={() =>
                applyCenteredTransform(
                  { a: 1, b: 0, c: 0, d: -1, e: 0, f: 0 },
                  'Layer flipped vertically.',
                )
              }
            >
              Flip vertical
            </button>
            <button
              className="action-button"
              onClick={() => {
                runCommand(setLayerTransform(selectedLayer.id, identityLayerTransform()));
                notify('Layer transform reset.');
              }}
            >
              Reset transform
            </button>
          </div>
        )}
      </div>
      {(selectedLayer.kind === 'pixel' ||
        selectedLayer.kind === 'shape' ||
        selectedLayer.kind === 'text' ||
        selectedLayer.kind === 'smart-object' ||
        selectedLayer.kind === 'group') && (
        <section className="mask-controls" aria-labelledby="layer-effects-heading">
          <h3 id="layer-effects-heading">Layer effects · {selectedLayer.effects.length}</h3>
          <select
            aria-label="Add layer effect"
            value=""
            onChange={(event) => {
              if (event.target.value) addLayerEffect(event.target.value as LayerEffect['type']);
            }}
          >
            <option value="">Add effect…</option>
            <option value="color-overlay">Color overlay</option>
            <option value="stroke">Stroke</option>
            <option value="drop-shadow">Drop shadow</option>
          </select>
          {selectedLayer.effects.map((effect, index) => (
            <fieldset className="layer-locks" key={`${effect.type}-${String(index)}`}>
              <legend>
                {effect.type
                  .split('-')
                  .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
                  .join(' ')}
              </legend>
              <label className="toggle-field">
                <input
                  type="checkbox"
                  aria-label={`Enable ${effect.type.split('-').join(' ')}`}
                  checked={effect.enabled}
                  onChange={(event) =>
                    updateLayerEffect(index, {
                      ...effect,
                      enabled: event.target.checked,
                    })
                  }
                />
                Enabled
              </label>
              <label className="property-field">
                Opacity
                <input
                  aria-label={`${effect.type} opacity`}
                  type="range"
                  min="0"
                  max="100"
                  value={Math.round(effect.opacity * 100)}
                  onChange={(event) =>
                    updateLayerEffect(index, {
                      ...effect,
                      opacity: Number(event.target.value) / 100,
                    })
                  }
                />
              </label>
              <label className="property-field">
                Color
                <input
                  aria-label={`${effect.type} color`}
                  type="color"
                  value={colorToHex(effect.color)}
                  onChange={(event) =>
                    updateLayerEffect(index, {
                      ...effect,
                      color: hexToColor(event.target.value, effect.color.alpha),
                    })
                  }
                />
              </label>
              {effect.type === 'color-overlay' && (
                <label className="property-field">
                  Blend mode
                  <select
                    aria-label="Color overlay blend mode"
                    value={effect.blendMode}
                    onChange={(event) =>
                      updateLayerEffect(index, {
                        ...effect,
                        blendMode: event.target.value as (typeof BLEND_MODES)[number],
                      })
                    }
                  >
                    {BLEND_MODES.map((mode) => (
                      <option key={mode} value={mode}>
                        {mode}
                      </option>
                    ))}
                  </select>
                </label>
              )}
              {effect.type === 'stroke' && (
                <label className="property-field">
                  Size
                  <input
                    aria-label="Stroke effect size"
                    type="number"
                    min="0"
                    max="100"
                    value={effect.size}
                    onChange={(event) =>
                      updateLayerEffect(index, {
                        ...effect,
                        size: Math.max(0, Number(event.target.value)),
                      })
                    }
                  />
                </label>
              )}
              {effect.type === 'drop-shadow' && (
                <>
                  {(['offsetX', 'offsetY', 'blur'] as const).map((field) => (
                    <label className="property-field" key={field}>
                      {field === 'offsetX' ? 'Offset X' : field === 'offsetY' ? 'Offset Y' : 'Blur'}
                      <input
                        aria-label={`Drop shadow ${field}`}
                        type="number"
                        min={field === 'blur' ? 0 : -1000}
                        max="1000"
                        value={effect[field]}
                        onChange={(event) =>
                          updateLayerEffect(index, {
                            ...effect,
                            [field]: Number(event.target.value),
                          })
                        }
                      />
                    </label>
                  ))}
                </>
              )}
              <button className="action-button danger" onClick={() => removeLayerEffect(index)}>
                Remove effect
              </button>
            </fieldset>
          ))}
        </section>
      )}
      {(selectedLayer.kind === 'pixel' ||
        selectedLayer.kind === 'shape' ||
        selectedLayer.kind === 'text' ||
        selectedLayer.kind === 'smart-object' ||
        selectedLayer.kind === 'group') && (
        <RasterMaskPropertiesSection
          layer={selectedLayer}
          editTarget={editTarget}
          densityDraft={maskDensityDraft}
          selectionAvailable={Boolean(document?.selection)}
          onEditTarget={setEditTarget}
          onEnabled={(enabled) => runCommand(setLayerMaskEnabled(selectedLayer.id, enabled))}
          onDensityPreview={previewMaskDensity}
          onDensityCommit={commitMaskDensity}
          onInvert={() => {
            runCommand(invertLayerMask(selectedLayer.id));
            notify('Layer mask inverted.');
          }}
          onDelete={() => {
            runCommand(setLayerRasterMask(selectedLayer.id, null));
            notify('Layer mask deleted.');
          }}
          onCreateRevealAll={addRevealAllMask}
          onCreateHideAll={addHideAllMask}
          onCreateFromSelection={addLayerMaskFromSelection}
        />
      )}
      <VectorMaskPropertiesSection
        layer={selectedLayer}
        densityDraft={vectorMaskDensityDraft}
        onEnabled={(enabled) => runCommand(setLayerVectorMaskEnabled(selectedLayer.id, enabled))}
        onDensityChange={setVectorMaskDensityDraft}
        onDensityCommit={commitVectorMaskDensity}
        onInvert={() => {
          runCommand(invertLayerVectorMask(selectedLayer.id));
          notify('Vector mask inverted.');
        }}
        onDelete={() => {
          runCommand(setLayerVectorMask(selectedLayer.id, null));
          notify('Vector mask deleted.');
        }}
        onCreate={addRectangularVectorMask}
        onUpdateCommand={updateVectorMaskCommand}
      />
      {selectedLayer.kind === 'adjustment' && (
        <section className="mask-controls" aria-labelledby="adjustment-mask-heading">
          <h3 id="adjustment-mask-heading">Adjustment mask</h3>
          {selectedLayer.rasterMask === null ? (
            <button className="action-button" onClick={addAdjustmentMask}>
              {document?.selection ? 'Add mask from selection' : 'Add reveal-all mask'}
            </button>
          ) : (
            <div className="mask-fields">
              <label className="toggle-field">
                <input
                  type="checkbox"
                  checked={selectedLayer.rasterMask.enabled}
                  onChange={(event) =>
                    runCommand(setLayerMaskEnabled(selectedLayer.id, event.target.checked))
                  }
                />
                Adjustment mask enabled
              </label>
              <label className="property-field property-wide">
                Mask density
                <input
                  aria-label="Adjustment mask density"
                  type="range"
                  min="0"
                  max="100"
                  value={maskDensityDraft}
                  onPointerDown={() => {
                    maskDensityEditingRef.current = true;
                  }}
                  onChange={(event) => previewMaskDensity(Number(event.target.value))}
                  onPointerUp={(event) => commitMaskDensity(Number(event.currentTarget.value))}
                  onKeyUp={(event) => commitMaskDensity(Number(event.currentTarget.value))}
                  onBlur={(event) => commitMaskDensity(Number(event.currentTarget.value))}
                />
                <output>{maskDensityDraft}%</output>
              </label>
              <div className="mask-actions">
                <button
                  className="action-button"
                  onClick={() => runCommand(invertLayerMask(selectedLayer.id))}
                >
                  Invert mask
                </button>
                <button
                  className="action-button danger"
                  onClick={() => runCommand(setLayerRasterMask(selectedLayer.id, null))}
                >
                  Delete mask
                </button>
              </div>
            </div>
          )}
        </section>
      )}
    </section>
  );
}
