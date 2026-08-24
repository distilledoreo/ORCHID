import type { ReactNode } from 'react';

export interface PropertySectionProps {
  readonly title: string;
  readonly children: ReactNode;
  readonly defaultOpen?: boolean;
  readonly className?: string;
}

export function PropertySection({ title, children, defaultOpen, className }: PropertySectionProps) {
  return (
    <details className={`property-section ${className ?? ''}`} open={defaultOpen}>
      <summary>{title}</summary>
      <div className="property-section-body">{children}</div>
    </details>
  );
}
