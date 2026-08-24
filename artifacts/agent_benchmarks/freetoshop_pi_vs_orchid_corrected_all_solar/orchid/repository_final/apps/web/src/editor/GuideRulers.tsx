import { useRef, type PointerEvent as ReactPointerEvent } from 'react';
import type { ViewportTransform } from '@freetoshop/renderer';
import type { GuideDirection } from '@freetoshop/core-document';

export interface GuideRulersProps {
  readonly enabled: boolean;
  readonly viewport: ViewportTransform;
  readonly documentWidth: number;
  readonly documentHeight: number;
  readonly onCreateGuide: (direction: GuideDirection, position: number) => void;
}

/**
 * Thin ruler strips along the top and left of the stage. Dragging out of a strip
 * creates a document guide at the release position (Photopea/Photoshop-like).
 */
export function GuideRulers({
  enabled,
  viewport,
  documentWidth,
  documentHeight,
  onCreateGuide,
}: GuideRulersProps) {
  const dragRef = useRef<{
    direction: GuideDirection;
    pointerId: number;
  } | null>(null);

  if (!enabled) return null;

  const finish = (event: ReactPointerEvent<HTMLDivElement>, direction: GuideDirection) => {
    const drag = dragRef.current;
    if (!drag || drag.pointerId !== event.pointerId || drag.direction !== direction) return;
    dragRef.current = null;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    const stage = event.currentTarget.parentElement;
    if (!stage) return;
    const bounds = stage.getBoundingClientRect();
    const docX = (event.clientX - bounds.left - viewport.panX) / viewport.zoom;
    const docY = (event.clientY - bounds.top - viewport.panY) / viewport.zoom;
    if (direction === 'vertical') {
      const position = Math.round(Math.min(documentWidth, Math.max(0, docX)));
      onCreateGuide('vertical', position);
    } else {
      const position = Math.round(Math.min(documentHeight, Math.max(0, docY)));
      onCreateGuide('horizontal', position);
    }
  };

  const begin = (event: ReactPointerEvent<HTMLDivElement>, direction: GuideDirection) => {
    event.preventDefault();
    event.stopPropagation();
    event.currentTarget.setPointerCapture(event.pointerId);
    dragRef.current = { direction, pointerId: event.pointerId };
  };

  return (
    <>
      <div
        className="guide-ruler guide-ruler-top"
        role="slider"
        aria-label="Drag to create a horizontal guide"
        aria-orientation="horizontal"
        aria-valuemin={0}
        aria-valuemax={documentHeight}
        aria-valuenow={0}
        data-guide-ruler="horizontal"
        onPointerDown={(event) => begin(event, 'horizontal')}
        onPointerUp={(event) => finish(event, 'horizontal')}
        onPointerCancel={() => {
          dragRef.current = null;
        }}
      />
      <div
        className="guide-ruler guide-ruler-left"
        role="slider"
        aria-label="Drag to create a vertical guide"
        aria-orientation="vertical"
        aria-valuemin={0}
        aria-valuemax={documentWidth}
        aria-valuenow={0}
        data-guide-ruler="vertical"
        onPointerDown={(event) => begin(event, 'vertical')}
        onPointerUp={(event) => finish(event, 'vertical')}
        onPointerCancel={() => {
          dragRef.current = null;
        }}
      />
    </>
  );
}
