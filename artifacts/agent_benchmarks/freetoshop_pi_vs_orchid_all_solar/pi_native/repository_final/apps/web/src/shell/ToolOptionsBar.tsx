import type { SelectionCombineMode } from '@freetoshop/core-document';
import type { DocumentRectangle, EditTarget, Tool } from '../editor/types.js';
import { toolDefinition } from '../tools/toolRegistry.js';

type Refinement = 'feather' | 'expand' | 'contract' | 'smooth' | 'border';

export interface ToolOptionsBarProps {
  readonly tool: Tool;
  readonly status: string;
  readonly documentOpen: boolean;
  readonly selectionActive: boolean;
  readonly brushSize: number;
  readonly brushOpacity: number;
  readonly brushColor: string;
  readonly editTarget: EditTarget;
  readonly selectionMode: SelectionCombineMode;
  readonly selectionRadius: number;
  readonly wandTolerance: number;
  readonly wandContiguous: boolean;
  readonly cropRectangle: DocumentRectangle | null;
  readonly pathAnchorCount: number;
  readonly zoom: number;
  readonly busy: boolean;
  readonly operationProgress: number | null;
  readonly onFit: () => void;
  readonly onActualSize: () => void;
  readonly onBrushSizeChange: (value: number) => void;
  readonly onBrushOpacityChange: (value: number) => void;
  readonly onBrushColorChange: (value: string) => void;
  readonly onSelectionModeChange: (value: SelectionCombineMode) => void;
  readonly onDeselect: () => void;
  readonly onWandToleranceChange: (value: number) => void;
  readonly onWandContiguousChange: (value: boolean) => void;
  readonly onSelectionRadiusChange: (value: number) => void;
  readonly onRefineSelection: (operation: Refinement) => void;
  readonly onCropRectangleChange: (rectangle: DocumentRectangle) => void;
  readonly onApplyCrop: () => void;
  readonly onCancelCrop: () => void;
  readonly onFinishPath: (closed: boolean) => void;
  readonly onCancelPath: () => void;
  readonly onZoomChange: (zoom: number) => void;
  readonly onCancelOperation: () => void;
}

export function ToolOptionsBar(props: ToolOptionsBarProps) {
  const selectionTool =
    props.tool === 'select' ||
    props.tool === 'ellipse' ||
    props.tool === 'lasso' ||
    props.tool === 'wand';
  return (
    <div className="options-bar" aria-label="Tool options">
      <span className="options-label">{toolDefinition(props.tool).label}</span>
      <span id="edit-target-status" className="target-status" aria-live="polite">
        {props.status}
      </span>
      <div className="option-group">
        <button className="action-button" disabled={!props.documentOpen} onClick={props.onFit}>
          Fit
        </button>
        <button
          className="action-button"
          disabled={!props.documentOpen}
          onClick={props.onActualSize}
        >
          100%
        </button>
      </div>
      {(props.tool === 'brush' || props.tool === 'eraser') && (
        <>
          <div className="option-group">
            <label htmlFor="brush-size">Size</label>
            <input
              id="brush-size"
              aria-label="Brush size"
              type="range"
              min="1"
              max="200"
              value={props.brushSize}
              onChange={(event) => props.onBrushSizeChange(Number(event.target.value))}
            />
            <span>{props.brushSize}px</span>
          </div>
          <div className="option-group">
            <label htmlFor="brush-opacity">Opacity</label>
            <input
              id="brush-opacity"
              aria-label="Brush opacity"
              type="range"
              min="1"
              max="100"
              value={props.brushOpacity}
              onChange={(event) => props.onBrushOpacityChange(Number(event.target.value))}
            />
            <span>{props.brushOpacity}%</span>
          </div>
          {props.tool === 'brush' && props.editTarget === 'pixels' && (
            <label className="color-option">
              Color
              <input
                aria-label="Brush color"
                type="color"
                value={props.brushColor}
                onChange={(event) => props.onBrushColorChange(event.target.value)}
              />
            </label>
          )}
        </>
      )}
      {selectionTool && (
        <>
          <div className="option-group">
            <label htmlFor="selection-mode">Selection mode</label>
            <select
              id="selection-mode"
              aria-label="Selection mode"
              value={props.selectionMode}
              onChange={(event) =>
                props.onSelectionModeChange(event.target.value as SelectionCombineMode)
              }
            >
              <option value="replace">Replace</option>
              <option value="add">Add</option>
              <option value="subtract">Subtract</option>
              <option value="intersect">Intersect</option>
            </select>
            {props.selectionActive && (
              <button className="action-button" onClick={props.onDeselect}>
                Deselect
              </button>
            )}
          </div>
          {props.tool === 'wand' && (
            <div className="option-group">
              <label htmlFor="wand-tolerance">Tolerance</label>
              <input
                id="wand-tolerance"
                aria-label="Magic Wand tolerance"
                type="range"
                min="0"
                max="255"
                value={props.wandTolerance}
                onChange={(event) => props.onWandToleranceChange(Number(event.target.value))}
              />
              <span>{props.wandTolerance}</span>
              <label className="inline-check">
                <input
                  type="checkbox"
                  checked={props.wandContiguous}
                  onChange={(event) => props.onWandContiguousChange(event.target.checked)}
                />
                Contiguous
              </label>
            </div>
          )}
          {props.selectionActive && (
            <div className="option-group selection-refine" aria-label="Refine selection">
              <label htmlFor="selection-radius">Radius</label>
              <input
                id="selection-radius"
                aria-label="Selection refinement radius"
                type="number"
                min="0"
                max="100"
                step="1"
                value={props.selectionRadius}
                onChange={(event) =>
                  props.onSelectionRadiusChange(
                    Math.max(0, Math.min(100, Number(event.target.value))),
                  )
                }
              />
              {(['feather', 'expand', 'contract', 'smooth', 'border'] as const).map((operation) => (
                <button
                  className="action-button"
                  key={operation}
                  onClick={() => props.onRefineSelection(operation)}
                >
                  {operation[0]?.toUpperCase()}
                  {operation.slice(1)}
                </button>
              ))}
            </div>
          )}
        </>
      )}
      {props.tool === 'crop' && (
        <div className="option-group crop-options" aria-label="Crop options">
          {(['x', 'y', 'width', 'height'] as const).map((key) => (
            <label key={key}>
              {key === 'x' ? 'X' : key === 'y' ? 'Y' : key === 'width' ? 'W' : 'H'}
              <input
                type="number"
                step="1"
                min={key === 'width' || key === 'height' ? 1 : undefined}
                aria-label={`Crop ${key}`}
                value={props.cropRectangle?.[key] ?? ''}
                disabled={!props.cropRectangle}
                onChange={(event) => {
                  if (!props.cropRectangle) return;
                  const value = Math.round(Number(event.target.value));
                  if (!Number.isFinite(value)) return;
                  props.onCropRectangleChange({
                    ...props.cropRectangle,
                    [key]: key === 'width' || key === 'height' ? Math.max(1, value) : value,
                  });
                }}
                onKeyDown={(event) => {
                  if (event.key === 'Enter') {
                    event.preventDefault();
                    props.onApplyCrop();
                  } else if (event.key === 'Escape') {
                    event.preventDefault();
                    props.onCancelCrop();
                  }
                }}
              />
            </label>
          ))}
          <button
            className="action-button primary"
            disabled={!props.cropRectangle}
            onClick={props.onApplyCrop}
          >
            Apply crop
          </button>
          <button
            className="action-button"
            disabled={!props.cropRectangle}
            onClick={props.onCancelCrop}
          >
            Cancel crop
          </button>
        </div>
      )}
      {props.tool === 'transform' && (
        <span className="transform-instructions" role="status">
          Drag square handles to scale and the round handle to rotate. Hold Shift for uniform
          scaling; Escape cancels the active gesture.
        </span>
      )}
      {props.tool === 'pen' && (
        <div className="option-group" aria-label="Pen options">
          <span>{props.pathAnchorCount} anchors</span>
          <button
            className="action-button primary"
            disabled={props.pathAnchorCount < 2}
            onClick={() => props.onFinishPath(false)}
          >
            Finish open path
          </button>
          <button
            className="action-button"
            disabled={props.pathAnchorCount < 3}
            onClick={() => props.onFinishPath(true)}
          >
            Close path
          </button>
          <button
            className="action-button"
            disabled={props.pathAnchorCount === 0}
            onClick={props.onCancelPath}
          >
            Cancel path
          </button>
        </div>
      )}
      <div className="option-group">
        <label className="sr-only" htmlFor="zoom">
          Canvas zoom
        </label>
        <input
          id="zoom"
          className="zoom-input"
          type="range"
          min="5"
          max="400"
          value={Math.round(props.zoom * 100)}
          disabled={!props.documentOpen}
          onChange={(event) => props.onZoomChange(Number(event.target.value) / 100)}
        />
        <span>{Math.round(props.zoom * 100)}%</span>
      </div>
      {props.busy && (
        <span role="status">
          Working locally…
          {props.operationProgress === null
            ? ''
            : ` ${String(Math.round(props.operationProgress * 100))}%`}
        </span>
      )}
      {props.operationProgress !== null && (
        <button className="action-button" type="button" onClick={props.onCancelOperation}>
          Cancel operation
        </button>
      )}
    </div>
  );
}
