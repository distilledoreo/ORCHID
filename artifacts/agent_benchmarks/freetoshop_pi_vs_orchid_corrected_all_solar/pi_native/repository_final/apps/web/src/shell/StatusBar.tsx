import type { ReactNode } from 'react';

export function StatusBar({
  left,
  right,
}: {
  readonly left: ReactNode;
  readonly right: ReactNode;
}) {
  return (
    <footer className="statusbar">
      <div className="status-group">{left}</div>
      <div className="status-group">{right}</div>
    </footer>
  );
}
