import type { PanelId } from '../panels/panelRegistry.js';
import {
  PROFESSIONAL_EDITING_PROFILE,
  type DockGroupId,
  type WorkspaceProfileId,
} from './workspaceProfiles.js';

export interface DockGroupState {
  readonly id: DockGroupId;
  readonly activePanel: PanelId;
  readonly size: number;
  readonly collapsed: boolean;
  readonly visible: boolean;
}

export interface WorkspaceState {
  readonly version: 4;
  readonly narrowDockColumn: NarrowDockColumn;
  readonly profileId: WorkspaceProfileId;
  readonly dockCollapsed: boolean;
  readonly primaryDockWidth: number;
  readonly secondaryDockWidth: number;
  readonly groups: readonly DockGroupState[];
}

export type NarrowDockColumn = 'primary' | 'secondary';

export function defaultWorkspaceState(): WorkspaceState {
  return {
    version: 4,
    narrowDockColumn: 'primary',
    profileId: PROFESSIONAL_EDITING_PROFILE.id,
    dockCollapsed: false,
    primaryDockWidth: 320,
    secondaryDockWidth: 316,
    groups: PROFESSIONAL_EDITING_PROFILE.groups.map((group) => ({
      id: group.id,
      activePanel: group.defaultPanel,
      size: group.defaultSize,
      collapsed: group.defaultCollapsed,
      visible: group.id !== 'color',
    })),
  };
}

export const DEFAULT_WORKSPACE_STATE = defaultWorkspaceState();
