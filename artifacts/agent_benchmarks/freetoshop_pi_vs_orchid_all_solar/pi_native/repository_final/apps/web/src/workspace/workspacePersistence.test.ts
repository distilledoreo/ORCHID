import { describe, expect, it } from 'vitest';
import { defaultWorkspaceState } from './workspaceState.js';
import {
  loadWorkspaceState,
  resetDockGroup,
  saveWorkspaceState,
  WORKSPACE_STORAGE_KEY,
} from './workspacePersistence.js';

describe('workspace persistence', () => {
  it('round trips versioned stacked dock state', () => {
    const values = new Map<string, string>();
    const storage = {
      getItem: (key: string) => values.get(key) ?? null,
      setItem: (key: string, value: string) => values.set(key, value),
    };
    const state = {
      ...defaultWorkspaceState(),
      dockCollapsed: true,
      primaryDockWidth: 388,
      secondaryDockWidth: 404,
      narrowDockColumn: 'secondary' as const,
      groups: defaultWorkspaceState().groups.map((group) =>
        group.id === 'layers' ? { ...group, activePanel: 'channels' as const, size: 388 } : group,
      ),
    };
    saveWorkspaceState(state, storage);
    expect(values.has(WORKSPACE_STORAGE_KEY)).toBe(true);
    expect(loadWorkspaceState(storage)).toEqual(state);
  });

  it('migrates the legacy single-panel workspace into its owning group', () => {
    const storage = {
      getItem: (key: string) =>
        key === 'freetoshop.workspace.v1'
          ? JSON.stringify({ activePanel: 'properties', panelCollapsed: true, panelWidth: 412 })
          : null,
    };
    const state = loadWorkspaceState(storage);
    expect(state.version).toBe(4);
    expect(state.dockCollapsed).toBe(true);
    expect(state.primaryDockWidth).toBe(400);
    expect(state.groups.find((group) => group.id === 'properties')?.activePanel).toBe('properties');
  });

  it('migrates the version two single-width dock without losing panel state', () => {
    const storage = {
      getItem: (key: string) =>
        key === 'freetoshop.workspace.v2'
          ? JSON.stringify({
              dockCollapsed: false,
              dockWidth: 376,
              groups: defaultWorkspaceState().groups.map((group) =>
                group.id === 'layers' ? { ...group, activePanel: 'channels' } : group,
              ),
            })
          : null,
    };
    const state = loadWorkspaceState(storage);
    expect(state.version).toBe(4);
    expect(state.primaryDockWidth).toBe(376);
    expect(state.secondaryDockWidth).toBe(defaultWorkspaceState().secondaryDockWidth);
    expect(state.groups.find((group) => group.id === 'layers')?.activePanel).toBe('channels');
  });

  it('migrates version three and defaults the narrow column', () => {
    const legacy = { ...defaultWorkspaceState(), version: 3, narrowDockColumn: undefined };
    const state = loadWorkspaceState({
      getItem: (key) => (key === 'freetoshop.workspace.v3' ? JSON.stringify(legacy) : null),
    });
    expect(state.version).toBe(4);
    expect(state.narrowDockColumn).toBe('primary');
  });

  it('normalizes invalid narrow columns and resets only one group', () => {
    const state = loadWorkspaceState({
      getItem: (key) =>
        key === WORKSPACE_STORAGE_KEY
          ? JSON.stringify({ ...defaultWorkspaceState(), narrowDockColumn: 'invalid' })
          : null,
    });
    expect(state.narrowDockColumn).toBe('primary');
    const changed = {
      ...state,
      groups: state.groups.map((group) =>
        group.id === 'layers' ? { ...group, collapsed: true, size: 500 } : group,
      ),
    };
    const reset = resetDockGroup(changed, 'layers');
    expect(reset.groups.find((group) => group.id === 'layers')).toMatchObject({
      collapsed: false,
      visible: true,
    });
    expect(reset.groups.find((group) => group.id === 'history')).toEqual(
      changed.groups.find((group) => group.id === 'history'),
    );
  });

  it('falls back for malformed data and clamps dock dimensions', () => {
    expect(loadWorkspaceState({ getItem: () => '{broken' })).toEqual(defaultWorkspaceState());
    const state = loadWorkspaceState({
      getItem: (key: string) =>
        key === WORKSPACE_STORAGE_KEY
          ? JSON.stringify({ ...defaultWorkspaceState(), primaryDockWidth: 9999 })
          : null,
    });
    expect(state.primaryDockWidth).toBe(400);
  });
});
