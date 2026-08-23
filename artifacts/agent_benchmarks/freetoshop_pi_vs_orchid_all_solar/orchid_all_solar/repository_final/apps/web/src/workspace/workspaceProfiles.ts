import type { CommandMenu } from '../commands/commandTypes.js';
import type { PanelId } from '../panels/panelRegistry.js';

export type WorkspaceProfileId = 'professional-editing';
export type DockGroupId = 'color' | 'properties' | 'layers' | 'history';

export interface WorkspaceGroupProfile {
  readonly id: DockGroupId;
  readonly label: string;
  readonly panels: readonly PanelId[];
  readonly defaultPanel: PanelId;
  readonly defaultSize: number;
  readonly defaultCollapsed: boolean;
}

export interface WorkspaceProfile {
  readonly id: WorkspaceProfileId;
  readonly label: string;
  readonly menus: readonly CommandMenu[];
  readonly groups: readonly WorkspaceGroupProfile[];
  readonly quickAccessCommands: readonly string[];
}

export const PROFESSIONAL_EDITING_PROFILE: WorkspaceProfile = {
  id: 'professional-editing',
  label: 'Professional Editing',
  menus: ['file', 'edit', 'image', 'layer', 'type', 'select', 'filter', 'view', 'window', 'help'],
  groups: [
    {
      id: 'color',
      label: 'Color',
      panels: ['color', 'swatches'],
      defaultPanel: 'color',
      defaultSize: 112,
      defaultCollapsed: true,
    },
    {
      id: 'properties',
      label: 'Properties',
      panels: ['properties', 'adjustments'],
      defaultPanel: 'properties',
      defaultSize: 220,
      defaultCollapsed: false,
    },
    {
      id: 'layers',
      label: 'Layers',
      panels: ['layers', 'channels', 'paths'],
      defaultPanel: 'layers',
      defaultSize: 330,
      defaultCollapsed: false,
    },
    {
      id: 'history',
      label: 'History',
      panels: ['history', 'actions'],
      defaultPanel: 'history',
      defaultSize: 150,
      defaultCollapsed: false,
    },
  ],
  quickAccessCommands: ['file.new', 'file.save', 'file.export-png', 'edit.undo', 'edit.redo'],
};

export const WORKSPACE_PROFILES: readonly WorkspaceProfile[] = [PROFESSIONAL_EDITING_PROFILE];

export function workspaceProfile(id: WorkspaceProfileId): WorkspaceProfile {
  void id;
  return PROFESSIONAL_EDITING_PROFILE;
}
