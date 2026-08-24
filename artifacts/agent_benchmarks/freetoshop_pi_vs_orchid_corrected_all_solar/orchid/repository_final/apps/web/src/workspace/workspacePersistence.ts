import { PANEL_REGISTRY, type PanelId } from '../panels/panelRegistry.js';
import { PROFESSIONAL_EDITING_PROFILE, type DockGroupId } from './workspaceProfiles.js';
import { defaultWorkspaceState, type WorkspaceState } from './workspaceState.js';

export const WORKSPACE_STORAGE_KEY = 'freetoshop.workspace.v4';
const VERSION_THREE_KEY = 'freetoshop.workspace.v3';
const VERSION_TWO_KEY = 'freetoshop.workspace.v2';
const LEGACY_KEY = 'freetoshop.workspace.v1';
const panelIds = new Set(PANEL_REGISTRY.map((panel) => panel.id));

function bounded(value: unknown, fallback: number, min: number, max: number): number {
  const number = Number(value);
  return Number.isFinite(number) ? Math.max(min, Math.min(max, number)) : fallback;
}

export function loadWorkspaceState(
  storage: Pick<Storage, 'getItem'> = localStorage,
): WorkspaceState {
  const fallback = defaultWorkspaceState();
  try {
    const value = JSON.parse(
      storage.getItem(WORKSPACE_STORAGE_KEY) ?? 'null',
    ) as Partial<WorkspaceState> | null;
    if (value?.version === 4 && Array.isArray(value.groups)) {
      return {
        ...fallback,
        narrowDockColumn: value.narrowDockColumn === 'secondary' ? 'secondary' : 'primary',
        dockCollapsed: Boolean(value.dockCollapsed),
        primaryDockWidth: bounded(value.primaryDockWidth, fallback.primaryDockWidth, 280, 400),
        secondaryDockWidth: bounded(
          value.secondaryDockWidth,
          fallback.secondaryDockWidth,
          280,
          420,
        ),
        groups: PROFESSIONAL_EDITING_PROFILE.groups.map((profileGroup) => {
          const saved = value.groups?.find((group) => group.id === profileGroup.id);
          const activePanel =
            saved &&
            panelIds.has(saved.activePanel) &&
            profileGroup.panels.includes(saved.activePanel)
              ? saved.activePanel
              : profileGroup.defaultPanel;
          return {
            id: profileGroup.id,
            activePanel,
            size: bounded(saved?.size, profileGroup.defaultSize, 72, 640),
            collapsed: Boolean(saved?.collapsed),
            visible: saved?.visible !== false,
          };
        }),
      };
    }
    const versionThree = JSON.parse(storage.getItem(VERSION_THREE_KEY) ?? 'null') as {
      version?: number;
      dockCollapsed?: boolean;
      primaryDockWidth?: number;
      secondaryDockWidth?: number;
      groups?: WorkspaceState['groups'];
    } | null;
    if (versionThree?.version === 3 && Array.isArray(versionThree.groups)) {
      return {
        ...fallback,
        narrowDockColumn: 'primary',
        dockCollapsed: Boolean(versionThree.dockCollapsed),
        primaryDockWidth: bounded(
          versionThree.primaryDockWidth,
          fallback.primaryDockWidth,
          280,
          400,
        ),
        secondaryDockWidth: bounded(
          versionThree.secondaryDockWidth,
          fallback.secondaryDockWidth,
          280,
          420,
        ),
        groups: fallback.groups.map((group) => {
          const saved = versionThree.groups?.find((candidate) => candidate.id === group.id);
          return saved ? { ...group, ...saved, id: group.id } : group;
        }),
      };
    }
    const versionTwo = JSON.parse(storage.getItem(VERSION_TWO_KEY) ?? 'null') as {
      dockCollapsed?: boolean;
      dockWidth?: number;
      groups?: WorkspaceState['groups'];
    } | null;
    if (versionTwo) {
      return {
        ...fallback,
        dockCollapsed: Boolean(versionTwo.dockCollapsed),
        primaryDockWidth: bounded(versionTwo.dockWidth, fallback.primaryDockWidth, 280, 400),
        groups: fallback.groups.map((group) => {
          const saved = versionTwo.groups?.find((candidate) => candidate.id === group.id);
          return saved ? { ...group, ...saved, id: group.id } : group;
        }),
      };
    }
    const legacy = JSON.parse(storage.getItem(LEGACY_KEY) ?? 'null') as {
      activePanel?: PanelId;
      panelCollapsed?: boolean;
      panelWidth?: number;
    } | null;
    if (!legacy) return fallback;
    return {
      ...fallback,
      dockCollapsed: Boolean(legacy.panelCollapsed),
      primaryDockWidth: bounded(legacy.panelWidth, fallback.primaryDockWidth, 280, 400),
      groups: fallback.groups.map((group) => {
        const profileGroup = PROFESSIONAL_EDITING_PROFILE.groups.find(
          (candidate) => candidate.id === group.id,
        );
        return legacy.activePanel && profileGroup?.panels.includes(legacy.activePanel)
          ? { ...group, activePanel: legacy.activePanel }
          : group;
      }),
    };
  } catch {
    return fallback;
  }
}

export function saveWorkspaceState(
  state: WorkspaceState,
  storage: Pick<Storage, 'setItem'> = localStorage,
): void {
  storage.setItem(WORKSPACE_STORAGE_KEY, JSON.stringify(state));
}

export function updateDockGroup(
  state: WorkspaceState,
  id: DockGroupId,
  change: Partial<WorkspaceState['groups'][number]>,
): WorkspaceState {
  return {
    ...state,
    groups: state.groups.map((group) => (group.id === id ? { ...group, ...change, id } : group)),
  };
}

export function resetDockGroup(state: WorkspaceState, id: DockGroupId): WorkspaceState {
  const profile = PROFESSIONAL_EDITING_PROFILE.groups.find((group) => group.id === id);
  if (!profile) return state;
  return {
    ...state,
    groups: state.groups.map((group) =>
      group.id === id
        ? {
            id,
            activePanel: profile.defaultPanel,
            size: profile.defaultSize,
            collapsed: profile.defaultCollapsed,
            visible: true,
          }
        : group,
    ),
  };
}
