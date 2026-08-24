import type { ReactNode } from 'react';

export function ContextualTaskBar({
  label,
  children,
  onDismiss,
}: {
  readonly label: string;
  readonly children: ReactNode;
  readonly onDismiss: () => void;
}) {
  return (
    <div className="contextual-taskbar" role="toolbar" aria-label={label}>
      {children}
      <button
        className="context-dismiss"
        type="button"
        aria-label="Dismiss contextual actions"
        onClick={onDismiss}
      >
        ×
      </button>
    </div>
  );
}
