import type { CSSProperties, ReactNode } from 'react';
export function EditorShell({
  children,
  rightDockWidth,
}: {
  readonly children: ReactNode;
  readonly rightDockWidth: number;
}) {
  return (
    <div
      className="app-shell"
      style={{ '--right-dock-width': `${String(rightDockWidth)}px` } as CSSProperties}
    >
      {children}
    </div>
  );
}
