import { useEffect, useState } from 'react';
import type { Layer, RasterMask } from '@freetoshop/core-document';
import type { PixelSource } from '@freetoshop/pixel-storage';
import { Icon } from '../ui/Icon.js';
import { sampleMaskThumbnail, samplePixelThumbnail } from './layerThumbnailSampling.js';

const THUMBNAIL_SIZE = 32;
const pixelCache = new WeakMap<object, string>();
const maskCache = new WeakMap<object, string>();

function pixelSourceFor(layer: Layer): PixelSource | null {
  if (layer.kind === 'pixel') return layer.pixelStore;
  if (layer.kind !== 'smart-object') return null;
  const queue = [...layer.source.layers];
  while (queue.length > 0) {
    const candidate = queue.shift();
    if (!candidate) break;
    if (candidate.kind === 'pixel') return candidate.pixelStore;
    if (candidate.kind === 'group') queue.unshift(...candidate.children);
  }
  return null;
}

function imageUrl(pixels: Uint8ClampedArray, size: number): string {
  const canvas = globalThis.document.createElement('canvas');
  canvas.width = size;
  canvas.height = size;
  canvas.getContext('2d')?.putImageData(new ImageData(pixels, size, size), 0, 0);
  return canvas.toDataURL('image/png');
}

function useCachedPreview<T extends object>(
  source: T | null,
  cache: WeakMap<object, string>,
  sample: (source: T) => Uint8ClampedArray,
): string | null {
  const [url, setUrl] = useState(() => (source ? (cache.get(source) ?? null) : null));
  useEffect(() => {
    if (!source) {
      setUrl(null);
      return;
    }
    const cached = cache.get(source);
    if (cached) {
      setUrl(cached);
      return;
    }
    let cancelled = false;
    const timeout = window.setTimeout(() => {
      const next = imageUrl(sample(source), THUMBNAIL_SIZE);
      cache.set(source, next);
      if (!cancelled) setUrl(next);
    }, 0);
    return () => {
      cancelled = true;
      window.clearTimeout(timeout);
    };
  }, [cache, sample, source]);
  return url;
}

export function LayerThumbnail({
  layer,
  active,
  onActivate,
}: {
  readonly layer: Layer;
  readonly active: boolean;
  readonly onActivate: () => void;
}) {
  const source = pixelSourceFor(layer);
  const url = useCachedPreview(source, pixelCache, samplePixelThumbnail);
  return (
    <button
      type="button"
      className="layer-thumbnail"
      aria-label={`Edit pixels for ${layer.name}`}
      aria-pressed={active}
      title={`Edit pixels for ${layer.name}`}
      onClick={(event) => {
        event.stopPropagation();
        onActivate();
      }}
    >
      {url ? (
        <img src={url} alt="" />
      ) : (
        <Icon name={layer.kind === 'group' ? 'group' : layer.kind === 'text' ? 'text' : 'layer'} />
      )}
    </button>
  );
}

export function MaskThumbnail({
  mask,
  layerName,
  active,
  onActivate,
}: {
  readonly mask: RasterMask;
  readonly layerName: string;
  readonly active: boolean;
  readonly onActivate: () => void;
}) {
  const url = useCachedPreview(mask.coverageStore, maskCache, sampleMaskThumbnail);
  return (
    <button
      type="button"
      className="layer-mask-thumbnail"
      aria-label={`Edit layer mask for ${layerName}`}
      aria-pressed={active}
      title={
        mask.enabled
          ? `Edit Layer Mask for ${layerName}`
          : `Edit Disabled Layer Mask for ${layerName}`
      }
      onClick={(event) => {
        event.stopPropagation();
        onActivate();
      }}
    >
      {url ? <img src={url} alt="" /> : <Icon name="mask" />}
    </button>
  );
}
