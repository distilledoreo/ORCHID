import { Icon } from '../ui/Icon.js';
import type { MouseEventHandler } from 'react';

export function DocumentTabStrip({
  name,
  width,
  height,
  saving,
  specialContext,
  onClose,
  onContextMenu,
}: {
  readonly name: string | null;
  readonly width?: number;
  readonly height?: number;
  readonly saving: boolean;
  readonly specialContext?: string;
  readonly onClose: () => void;
  readonly onContextMenu?: MouseEventHandler<HTMLDivElement>;
}) {
  return (
    <div className="document-tab-strip" role="tablist" aria-label="Open documents">
      {name ? (
        <div
          className="document-tab"
          role="tab"
          aria-selected="true"
          tabIndex={0}
          onContextMenu={onContextMenu}
        >
          <span
            className="document-dirty"
            data-saving={saving}
            aria-label={saving ? 'Saving' : 'Saved locally'}
          />
          <span className="document-tab-name">
            {specialContext ? `${specialContext}: ${name}` : name}
          </span>
          {width !== undefined && height !== undefined && (
            <span className="document-tab-meta">
              {width} × {height}
            </span>
          )}
          <button
            className="document-close"
            aria-label="Close document"
            title="Close local document"
            onClick={onClose}
          >
            <Icon name="close" />
          </button>
        </div>
      ) : (
        <span className="document-tab-empty">No document open</span>
      )}
      <div className="document-tabs-future" aria-hidden="true" />
    </div>
  );
}
