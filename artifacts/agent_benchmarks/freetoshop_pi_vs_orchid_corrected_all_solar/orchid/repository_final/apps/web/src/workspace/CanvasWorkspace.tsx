import { forwardRef, type ReactNode } from 'react';
export const CanvasWorkspace = forwardRef<HTMLElement, { readonly children: ReactNode }>(
  function CanvasWorkspace({ children }, ref) {
    return (
      <section className="canvas-stage" ref={ref} aria-label="Document canvas">
        {children}
      </section>
    );
  },
);
