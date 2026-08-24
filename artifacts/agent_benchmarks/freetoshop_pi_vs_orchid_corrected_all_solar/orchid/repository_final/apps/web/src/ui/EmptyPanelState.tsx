export function EmptyPanelState({
  title,
  description,
}: {
  readonly title: string;
  readonly description: string;
}) {
  return (
    <div className="empty-panel-state">
      <strong>{title}</strong>
      <p>{description}</p>
    </div>
  );
}
