import type { DocumentGuide } from '@freetoshop/core-document';

export interface GuideHit {
  readonly guide: DocumentGuide;
  readonly distance: number;
}

/**
 * Finds the nearest guide to a document-space point within threshold pixels.
 * Vertical guides compare on x; horizontal guides compare on y.
 */
export function hitTestGuide(
  point: { readonly x: number; readonly y: number },
  guides: readonly DocumentGuide[],
  threshold: number,
): GuideHit | null {
  if (!guides.length || threshold < 0) return null;
  let best: GuideHit | null = null;
  for (const guide of guides) {
    if (!Number.isFinite(guide.position)) continue;
    const distance =
      guide.direction === 'vertical'
        ? Math.abs(point.x - guide.position)
        : Math.abs(point.y - guide.position);
    if (distance <= threshold && (best === null || distance < best.distance)) {
      best = { guide, distance };
    }
  }
  return best;
}
