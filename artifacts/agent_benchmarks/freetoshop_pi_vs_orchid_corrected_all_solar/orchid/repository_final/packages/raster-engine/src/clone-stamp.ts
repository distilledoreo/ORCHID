/**
 * Clone stamp operations for FreetoShop.
 *
 * Implements Photoshop-style clone stamp tool that samples from a source layer
 * and paints onto a destination layer, supporting aligned/unaligned modes,
 * transformed layers, and selection clipping.
 */

import {
  createPixelLayer,
  findLayer,
  findLayerWorldTransform,
  type PixelLayer,
  type PixelPatch,
  type SelectionMask,
  type LayerTransform,
  type BrushPoint,
} from '@freetoshop/core-document';
import { TiledPixelStore } from '@freetoshop/pixel-storage';
import { paintStroke, type BrushOptions } from './index.js';

export interface CloneSource {
  /** The layer ID to clone from. */
  readonly sourceLayerId: string;
  /** Document-space position of the clone source origin. */
  readonly sourcePoint: { x: number; y: number };
  /** World transform of the source layer at the time the source was set. */
  readonly sourceLayerWorldTransform: LayerTransform;
  /** When true, the offset between source and destination is preserved across the stroke. */
  readonly aligned: boolean;
  /** Optional fixed offset from source to destination in document pixels. */
  readonly offset: { x: number; y: number } | null;
}

export interface CloneStampOptions {
  readonly size: number;
  readonly hardness: number;
  readonly opacity: number;
  readonly source: CloneSource;
}

interface StrokeContext {
  destinationLayer: PixelLayer;
  sourceLayer: PixelLayer;
  options: CloneStampOptions;
  selection: SelectionMask | null;
  destinationWorldTransform: LayerTransform;
  sourceWorldTransform: LayerTransform;
}

/**
 * Computes the source document point for a given destination point.
 *
 * In aligned mode, the offset between source and destination is preserved.
 * In unaligned mode, the source always samples from the original source point.
 */
function computeSourcePoint(
  context: StrokeContext,
  destinationPoint: { x: number; y: number },
): { x: number; y: number } {
  const { source } = context.options;
  const { sourcePoint, aligned, offset, sourceLayerWorldTransform } = source;

  if (!aligned) {
    // Unaligned: always sample from the original source point
    let point = { ...sourcePoint };
    if (offset !== null) {
      point = { x: point.x + offset.x, y: point.y + offset.y };
    }
    return point;
  }

  // Aligned: compute destination document point, then apply offset
  const destWorldPoint = findLayerWorldTransform(
    // We need a document to compute world transform, but we're working with layers directly
    // Use the destination world transform to convert local to document
    {} as any, // TODO: proper document
    context.destinationLayer.id,
  );

  // For now, just use the destination point directly
  let sourceDocPoint = { ...destinationPoint };

  if (offset !== null) {
    sourceDocPoint = { x: sourceDocPoint.x + offset.x, y: sourceDocPoint.y + offset.y };
  } else {
    // No offset: source = destination (cloning same location)
    // This is handled by the source layer sampling below
  }

  return sourceDocPoint;
}

/**
 * Paints a clone stamp stroke, sampling pixels from a source layer and writing them
 * to a destination layer. Returns the smallest layer-local patch containing changed pixels.
 *
 * In aligned mode, the offset between the source position and each destination point
 * is preserved throughout the stroke. In unaligned mode, each dab samples from the
 * same source position.
 */
export function paintCloneStamp(
  destinationLayer: PixelLayer,
  sourceLayer: PixelLayer,
  points: readonly BrushPoint[],
  options: CloneStampOptions,
  selection?: SelectionMask | null,
  destinationWorldTransform?: LayerTransform,
  sourceWorldTransform?: LayerTransform,
): PixelPatch {
  const effectiveDestinationTransform = destinationWorldTransform ?? destinationLayer.transform;
  const effectiveSourceTransform = sourceWorldTransform ?? sourceLayer.transform;

  // Validate inputs
  if (!destinationLayer || !sourceLayer) {
    throw new RangeError('Both destination and source layers are required');
  }

  if (points.length === 0) {
    throw new RangeError('Stroke must contain at least one point');
  }

  const { size, hardness, opacity, source } = options;

  if (size <= 0) {
    throw new RangeError('Brush size must be greater than 0');
  }

  if (hardness < 0 || hardness > 1) {
    throw new RangeError('Hardness must be between 0 and 1');
  }

  if (opacity < 0 || opacity > 1) {
    throw new RangeError('Opacity must be between 0 and 1');
  }

  if (!source.sourceLayerId) {
    throw new RangeError('Clone source layer ID is required');
  }

  // Create brush options for the clone stamp
  // Clone stamp uses source-over mode with the sampled color
  const brushOptions: BrushOptions = {
    mode: 'source-over',
    size,
    hardness,
    opacity,
    color: [0, 0, 0, 255], // Color will be sampled per-dab
  };

  // For clone stamp, we need to sample the source color at each dab point
  // and then paint that color onto the destination
  // We'll use the existing paintStroke infrastructure but with dynamic color sampling

  const radius = size / 2;
  const destinationInverse = invertTransform(effectiveDestinationTransform);
  const sourceInverse = invertTransform(effectiveSourceTransform);

  // Build dabs in destination document space (same as paintStroke)
  const dabs: Array<{ x: number; y: number; pressure: number }> = [];
  const first = points[0];
  dabs.push({ x: first.x, y: first.y, pressure: first.pressure ?? 1 });

  const spacing = Math.max(0.25, radius * 0.5);
  for (let i = 1; i < points.length; i++) {
    const start = points[i - 1];
    const end = points[i];
    const distance = Math.hypot(end.x - start.x, end.y - start.y);
    const steps = Math.max(1, Math.ceil(distance / spacing));
    for (let step = 1; step <= steps; step++) {
      const progress = step / steps;
      dabs.push({
        x: start.x + (end.x - start.x) * progress,
        y: start.y + (end.y - start.y) * progress,
        pressure: (start.pressure ?? 1) + ((end.pressure ?? 1) - (start.pressure ?? 1)) * progress,
      });
    }
  }

  // Compute stroke bounds in destination layer local space
  let strokeBounds: { left: number; top: number; right: number; bottom: number } | null = null;

  for (const dab of dabs) {
    // Convert destination document point to layer-local coordinates
    const localDest = applyInverseTransform(effectiveDestinationTransform, dab.x, dab.y);

    // Compute source document point based on aligned/unaligned mode
    let sourceDocPoint: { x: number; y: number };

    if (!source.aligned) {
      // Unaligned: always sample from original source point + offset
      sourceDocPoint = { ...source.sourcePoint };
      if (source.offset !== null) {
        sourceDocPoint = {
          x: sourceDocPoint.x + source.offset.x,
          y: sourceDocPoint.y + source.offset.y,
        };
      }
    } else {
      // Aligned: source = destination + offset (or destination if no offset)
      sourceDocPoint = { ...dab };
      if (source.offset !== null) {
        sourceDocPoint = {
          x: sourceDocPoint.x + source.offset.x,
          y: sourceDocPoint.y + source.offset.y,
        };
      }
      // If no offset, source = destination (cloning from same location)
    }

    // Convert source document point to source layer-local coordinates
    const localSrc = applyInverseTransform(effectiveSourceTransform, sourceDocPoint.x, sourceDocPoint.y);

    // Accumulate stroke bounds (destination layer local space)
    if (!strokeBounds) {
      strokeBounds = {
        left: Math.floor(localDest.x - radius),
        top: Math.floor(localDest.y - radius),
        right: Math.ceil(localDest.x + radius),
        bottom: Math.ceil(localDest.y + radius),
      };
    } else {
      strokeBounds.left = Math.min(strokeBounds.left, Math.floor(localDest.x - radius));
      strokeBounds.top = Math.min(strokeBounds.top, Math.floor(localDest.y - radius));
      strokeBounds.right = Math.max(strokeBounds.right, Math.ceil(localDest.x + radius));
      strokeBounds.bottom = Math.max(strokeBounds.bottom, Math.ceil(localDest.y + radius));
    }
  }

  if (!strokeBounds) {
    return emptyPatch();
  }

  // Clip to layer bounds
  strokeBounds.left = Math.max(0, strokeBounds.left);
  strokeBounds.top = Math.max(0, strokeBounds.top);
  strokeBounds.right = Math.min(destinationLayer.width - 1, strokeBounds.right);
  strokeBounds.bottom = Math.min(destinationLayer.height - 1, strokeBounds.bottom);

  if (strokeBounds.left > strokeBounds.right || strokeBounds.top > strokeBounds.bottom) {
    return emptyPatch();
  }

  // Read destination region
  const regionWidth = strokeBounds.right - strokeBounds.left + 1;
  const regionHeight = strokeBounds.bottom - strokeBounds.top + 1;
  const pixels = destinationLayer.pixelStore.read({
    x: strokeBounds.left,
    y: strokeBounds.top,
    width: regionWidth,
    height: regionHeight,
  });

  // Process each dab
  let changedBounds: { left: number; top: number; right: number; bottom: number } | null = null;

  for (const dab of dabs) {
    // Compute destination document point (already in doc space)
    const destDocPoint = { x: dab.x, y: dab.y };

    // Compute source document point
    let sourceDocPoint: { x: number; y: number };

    if (!source.aligned) {
      // Unaligned: always use original source point + offset
      sourceDocPoint = { ...source.sourcePoint };
      if (source.offset !== null) {
        sourceDocPoint = {
          x: sourceDocPoint.x + source.offset.x,
          y: sourceDocPoint.y + source.offset.y,
        };
      }
    } else {
      // Aligned: destination + offset (or destination if no offset)
      sourceDocPoint = { ...destDocPoint };
      if (source.offset !== null) {
        sourceDocPoint = {
          x: sourceDocPoint.x + source.offset.x,
          y: sourceDocPoint.y + source.offset.y,
        };
      }
    }

    // Convert to layer-local coordinates
    const localDest = applyInverseTransform(effectiveDestinationTransform, destDocPoint.x, destDocPoint.y);
    const localSrc = applyInverseTransform(effectiveSourceTransform, sourceDocPoint.x, sourceDocPoint.y);

    // Check source bounds
    if (localSrc.x < 0 || localSrc.y < 0 || localSrc.x >= sourceLayer.width || localSrc.y >= sourceLayer.height) {
      continue;
    }

    // Sample source color at this dab's source location
    const sourcePixels = sourceLayer.pixelStore.read({
      x: Math.round(localSrc.x),
      y: Math.round(localSrc.y),
      width: 1,
      height: 1,
    });
    const sampledColor: [number, number, number, number] = [
      sourcePixels[0],
      sourcePixels[1],
      sourcePixels[2],
      sourcePixels[3],
    ];

    // Paint this dab using the sampled color
    const patch = paintSingleDab(
      destinationLayer,
      pixels,
      strokeBounds,
      regionWidth,
      regionHeight,
      localDest,
      radius,
      hardness,
      opacity,
      dab.pressure,
      sampledColor,
      selection,
      effectiveDestinationTransform,
    );

    if (patch.changedBounds) {
      changedBounds = unionBounds(changedBounds, patch.changedBounds);
    }
  }

  if (!changedBounds) {
    return emptyPatch();
  }

  // Extract the changed region from the working pixels
  const patchLeft = Math.max(strokeBounds.left, changedBounds.left);
  const patchTop = Math.max(strokeBounds.top, changedBounds.top);
  const patchRight = Math.min(strokeBounds.left + regionWidth - 1, changedBounds.right);
  const patchBottom = Math.min(strokeBounds.top + regionHeight - 1, changedBounds.bottom);

  const patchWidth = patchRight - patchLeft + 1;
  const patchHeight = patchBottom - patchTop + 1;
  const patchPixels = new Uint8ClampedArray(patchWidth * patchHeight * 4);

  for (let y = patchTop; y <= patchBottom; y++) {
    for (let x = patchLeft; x <= patchRight; x++) {
      const srcOffset = ((y - strokeBounds.top) * regionWidth + (x - strokeBounds.left)) * 4;
      const dstOffset = ((y - patchTop) * patchWidth + (x - patchLeft)) * 4;
      patchPixels[dstOffset] = pixels[srcOffset];
      patchPixels[dstOffset + 1] = pixels[srcOffset + 1];
      patchPixels[dstOffset + 2] = pixels[srcOffset + 2];
      patchPixels[dstOffset + 3] = pixels[srcOffset + 3];
    }
  }

  return {
    x: patchLeft,
    y: patchTop,
    width: patchWidth,
    height: patchHeight,
    pixels: patchPixels,
  };
}

interface DabResult {
  changedBounds: { left: number; top: number; right: number; bottom: number } | null;
}

function paintSingleDab(
  destinationLayer: PixelLayer,
  pixels: Uint8ClampedArray,
  strokeBounds: { left: number; top: number; right: number; bottom: number },
  regionWidth: number,
  regionHeight: number,
  localDest: { x: number; y: number },
  radius: number,
  hardness: number,
  opacity: number,
  pressure: number,
  color: [number, number, number, number],
  selection: SelectionMask | null,
  worldTransform: LayerTransform,
): DabResult {
  const changedBounds: { left: number; top: number; right: number; bottom: number } | null = null;

  // Iterate over pixels in the dab's influence region
  const startX = Math.max(strokeBounds.left, Math.floor(localDest.x - radius));
  const startY = Math.max(strokeBounds.top, Math.floor(localDest.y - radius));
  const endX = Math.min(strokeBounds.left + regionWidth - 1, Math.ceil(localDest.x + radius));
  const endY = Math.min(strokeBounds.top + regionHeight - 1, Math.ceil(localDest.y + radius));

  for (let y = startY; y <= endY; y++) {
    for (let x = startX; x <= endX; x++) {
      // Convert to document space
      const docPoint = applyTransform(worldTransform, x, y);

      // Calculate distance from dab center in document space
      const dx = docPoint.x - localDest.x;
      const dy = docPoint.y - localDest.y;
      const distance = Math.sqrt(dx * dx + dy * dy);

      if (distance > radius) continue;

      // Calculate falloff based on hardness
      let falloff = 1;
      if (hardness < 1) {
        const hardRadius = radius * hardness;
        if (distance > hardRadius) {
          falloff = 1 - (distance - hardRadius) / (radius - hardRadius);
        }
      }

      // Check selection
      let selected = 1;
      if (selection) {
        const selX = Math.round(docPoint.x);
        const selY = Math.round(docPoint.y);
        if (selX >= 0 && selX < selection.width && selY >= 0 && selY < selection.height) {
          const coverage = selection.coverageStore.read({
            x: selX,
            y: selY,
            width: 1,
            height: 1,
          })[0];
          selected = coverage / 255;
        } else {
          selected = 0;
        }
      }

      const strength = falloff * opacity * pressure * selected;
      if (strength <= 0) continue;

      // Apply source-over compositing
      const pixelOffset = ((y - strokeBounds.top) * regionWidth + (x - strokeBounds.left)) * 4;
      const srcAlpha = (color[3] / 255) * strength;
      const dstAlpha = pixels[pixelOffset + 3] / 255;
      const outAlpha = srcAlpha + dstAlpha * (1 - srcAlpha);

      if (outAlpha === 0) continue;

      const srcR = color[0];
      const srcG = color[1];
      const srcB = color[2];
      const dstR = pixels[pixelOffset];
      const dstG = pixels[pixelOffset + 1];
      const dstB = pixels[pixelOffset + 2];

      pixels[pixelOffset] = Math.round((srcR * srcAlpha + dstR * dstAlpha * (1 - srcAlpha)) / outAlpha);
      pixels[pixelOffset + 1] = Math.round((srcG * srcAlpha + dstG * dstAlpha * (1 - srcAlpha)) / outAlpha);
      pixels[pixelOffset + 2] = Math.round((srcB * srcAlpha + dstB * dstAlpha * (1 - srcAlpha)) / outAlpha);
      pixels[pixelOffset + 3] = Math.round(outAlpha * 255);
    }
  }

  return { changedBounds: null };
}

function applyTransform(transform: LayerTransform, x: number, y: number): { x: number; y: number } {
  return {
    x: transform.a * x + transform.c * y + transform.e,
    y: transform.b * x + transform.d * y + transform.f,
  };
}

function applyInverseTransform(transform: LayerTransform, x: number, y: number): { x: number; y: number } {
  const det = transform.a * transform.d - transform.b * transform.c;
  if (det === 0) {
    throw new RangeError('Transform is not invertible');
  }
  return {
    x: (transform.d * (x - transform.e) - transform.c * (y - transform.f)) / det,
    y: (-transform.b * (x - transform.e) + transform.a * (y - transform.f)) / det,
  };
}

function unionBounds(
  a: { left: number; top: number; right: number; bottom: number } | null,
  b: { left: number; top: number; right: number; bottom: number } | null,
): { left: number; top: number; right: number; bottom: number } | null {
  if (!a) return b;
  if (!b) return a;
  return {
    left: Math.min(a.left, b.left),
    top: Math.min(a.top, b.top),
    right: Math.max(a.right, b.right),
    bottom: Math.max(a.bottom, b.bottom),
  };
}

function emptyPatch(): PixelPatch {
  return {
    x: 0,
    y: 0,
    width: 0,
    height: 0,
    pixels: new Uint8ClampedArray(0),
  };
}

/**
 * Command that applies a clone stamp stroke as an atomic undoable operation.
 */
export function paintCloneStampCommand(
  destinationLayerId: string,
  sourceLayerId: string,
  points: readonly BrushPoint[],
  options: CloneStampOptions,
  selection?: SelectionMask | null,
): PixelPatch {
  // This will be used by the App to execute clone stamp as a command
  // For now, just call paintCloneStamp directly
  // The command infrastructure will wrap this
  const destinationLayer = findLayer({} as any, destinationLayerId);
  const sourceLayer = findLayer({} as any, sourceLayerId);

  if (!destinationLayer || !sourceLayer) {
    throw new RangeError('Layer not found');
  }

  return paintCloneStamp(
    destinationLayer,
    sourceLayer,
    points,
    options,
    selection,
  );
}
