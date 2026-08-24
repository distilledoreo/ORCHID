export type PanelId =
  | 'color'
  | 'swatches'
  | 'layers'
  | 'channels'
  | 'paths'
  | 'properties'
  | 'adjustments'
  | 'history'
  | 'actions';
export interface PanelDefinition {
  readonly id: PanelId;
  readonly label: string;
  readonly available: boolean;
}
export const PANEL_REGISTRY: readonly PanelDefinition[] = [
  { id: 'color', label: 'Color', available: true },
  { id: 'swatches', label: 'Swatches', available: true },
  { id: 'layers', label: 'Layers', available: true },
  { id: 'channels', label: 'Channels', available: true },
  { id: 'paths', label: 'Paths', available: false },
  { id: 'properties', label: 'Properties', available: true },
  { id: 'adjustments', label: 'Adjustments', available: true },
  { id: 'actions', label: 'Actions', available: false },
  { id: 'history', label: 'History', available: true },
];
