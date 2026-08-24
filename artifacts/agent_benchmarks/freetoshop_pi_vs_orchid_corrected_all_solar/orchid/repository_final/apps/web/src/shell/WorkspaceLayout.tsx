import type { ReactNode } from 'react';
export function WorkspaceLayout({ children }: { readonly children: ReactNode }) {
  return <main className="workspace">{children}</main>;
}
