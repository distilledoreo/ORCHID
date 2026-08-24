import {
  Children,
  isValidElement,
  useRef,
  type KeyboardEvent,
  type ReactElement,
  type ReactNode,
} from 'react';
import { Icon } from '../ui/Icon.js';
import type { DockGroupId } from '../workspace/workspaceProfiles.js';
import type { DockGroupState } from '../workspace/workspaceState.js';
import { PANEL_REGISTRY, type PanelId } from './panelRegistry.js';

export function PanelContent({ children }: { readonly id: PanelId; readonly children: ReactNode }) {
  return children;
}

export function PanelDock({
  collapsed,
  primaryWidth,
  secondaryWidth,
  groups,
  children,
  onChangeGroup,
  onToggleCollapsed,
  onResizePrimary,
  onResizeSecondary,
  narrowColumn,
  onNarrowColumnChange,
  onOpenGroupMenu,
}: {
  readonly collapsed: boolean;
  readonly primaryWidth: number;
  readonly secondaryWidth: number;
  readonly groups: readonly DockGroupState[];
  readonly children: ReactNode;
  readonly onChangeGroup: (id: DockGroupId, change: Partial<DockGroupState>) => void;
  readonly onToggleCollapsed: () => void;
  readonly onResizePrimary: (width: number) => void;
  readonly onResizeSecondary: (width: number) => void;
  readonly narrowColumn: 'primary' | 'secondary';
  readonly onNarrowColumnChange: (column: 'primary' | 'secondary') => void;
  readonly onOpenGroupMenu: (event: React.MouseEvent<HTMLElement>, id: DockGroupId) => void;
}) {
  const drag = useRef<{ x: number; width: number } | null>(null);
  const groupDrag = useRef<{ id: DockGroupId; y: number; size: number } | null>(null);
  const columnDrag = useRef<{ x: number; width: number } | null>(null);
  const instances = Children.toArray(children).filter(
    (child): child is ReactElement<{ id: PanelId; children: ReactNode }> =>
      isValidElement(child) && child.type === PanelContent,
  );
  const contentsFor = (id: PanelId) => {
    const contents = instances
      .filter((instance) => instance.props.id === id)
      .map((instance) => instance.props.children);
    if (id !== 'properties') return contents;

    const compactSummary = contents.at(-1);
    const detailedControls = contents.slice(0, -1);
    return (
      <div className="properties-panel-stack">
        {compactSummary}
        <div className="properties-detail-scroll">{detailedControls}</div>
      </div>
    );
  };
  const renderGroup = (group: DockGroupState, index: number): ReactNode => (
    <div
      className="panel-group"
      data-group={group.id}
      data-collapsed={group.collapsed}
      key={group.id}
      style={{ flexBasis: group.collapsed ? undefined : group.size }}
    >
      {index > 0 && !group.collapsed && (
        <div
          className="panel-group-splitter"
          role="separator"
          aria-label={`Resize ${group.id} panel group`}
          aria-orientation="horizontal"
          tabIndex={0}
          onPointerDown={(event) => {
            groupDrag.current = { id: group.id, y: event.clientY, size: group.size };
            event.currentTarget.setPointerCapture(event.pointerId);
          }}
          onPointerMove={(event) => {
            if (groupDrag.current?.id === group.id)
              onChangeGroup(group.id, {
                size: Math.max(
                  72,
                  Math.min(640, groupDrag.current.size + groupDrag.current.y - event.clientY),
                ),
              });
          }}
          onPointerUp={(event) => {
            groupDrag.current = null;
            event.currentTarget.releasePointerCapture(event.pointerId);
          }}
          onKeyDown={(event) => {
            if (event.key === 'ArrowUp')
              onChangeGroup(group.id, { size: Math.min(640, group.size + 12) });
            else if (event.key === 'ArrowDown')
              onChangeGroup(group.id, { size: Math.max(72, group.size - 12) });
            else return;
            event.preventDefault();
          }}
        />
      )}
      <PanelGroupHeader
        group={group}
        onChange={(change) => onChangeGroup(group.id, change)}
        onOpenMenu={(event) => onOpenGroupMenu(event, group.id)}
      />
      {!group.collapsed && (
        <div
          className="panel-content"
          role="tabpanel"
          id={`panel-${group.activePanel}`}
          aria-labelledby={`panel-tab-${group.activePanel}`}
        >
          {contentsFor(group.activePanel)}
        </div>
      )}
    </div>
  );

  return (
    <aside
      className="side-panel stacked-panel-dock"
      data-collapsed={collapsed}
      data-narrow-column={narrowColumn}
      aria-label="Document panels"
      style={
        collapsed
          ? undefined
          : {
              width: primaryWidth + secondaryWidth,
              gridTemplateColumns: `${String(primaryWidth)}px ${String(secondaryWidth)}px`,
            }
      }
    >
      {!collapsed && (
        <div
          className="panel-splitter"
          role="separator"
          aria-label="Resize primary dock"
          aria-orientation="vertical"
          aria-valuemin={280}
          aria-valuemax={400}
          aria-valuenow={primaryWidth}
          tabIndex={0}
          onPointerDown={(event) => {
            drag.current = { x: event.clientX, width: primaryWidth };
            event.currentTarget.setPointerCapture(event.pointerId);
          }}
          onPointerMove={(event) => {
            if (drag.current)
              onResizePrimary(
                Math.max(280, Math.min(400, drag.current.width + drag.current.x - event.clientX)),
              );
          }}
          onPointerUp={(event) => {
            drag.current = null;
            event.currentTarget.releasePointerCapture(event.pointerId);
          }}
          onKeyDown={(event) => {
            if (event.key === 'ArrowLeft') onResizePrimary(Math.min(400, primaryWidth + 12));
            else if (event.key === 'ArrowRight') onResizePrimary(Math.max(280, primaryWidth - 12));
            else return;
            event.preventDefault();
          }}
        />
      )}
      {!collapsed && (
        <div
          className="dock-column-splitter"
          style={{ left: primaryWidth - 4 }}
          role="separator"
          aria-label="Resize secondary dock"
          aria-orientation="vertical"
          aria-valuemin={280}
          aria-valuemax={420}
          aria-valuenow={secondaryWidth}
          tabIndex={0}
          onPointerDown={(event) => {
            columnDrag.current = { x: event.clientX, width: secondaryWidth };
            event.currentTarget.setPointerCapture(event.pointerId);
          }}
          onPointerMove={(event) => {
            if (columnDrag.current) {
              onResizeSecondary(
                Math.max(
                  280,
                  Math.min(420, columnDrag.current.width + event.clientX - columnDrag.current.x),
                ),
              );
            }
          }}
          onPointerUp={(event) => {
            columnDrag.current = null;
            event.currentTarget.releasePointerCapture(event.pointerId);
          }}
          onKeyDown={(event) => {
            if (event.key === 'ArrowLeft') onResizeSecondary(Math.max(280, secondaryWidth - 12));
            else if (event.key === 'ArrowRight')
              onResizeSecondary(Math.min(420, secondaryWidth + 12));
            else return;
            event.preventDefault();
          }}
        />
      )}
      <button
        className="panel-dock-toggle"
        aria-label={collapsed ? 'Expand panel dock' : 'Collapse panel dock'}
        onClick={onToggleCollapsed}
      >
        <Icon name={collapsed ? 'panels' : 'chevron'} />
      </button>
      {!collapsed && (
        <>
          <div className="narrow-dock-switcher" role="tablist" aria-label="Panel columns">
            <button
              type="button"
              role="tab"
              aria-selected={narrowColumn === 'primary'}
              onClick={() => onNarrowColumnChange('primary')}
            >
              Layers
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={narrowColumn === 'secondary'}
              onClick={() => onNarrowColumnChange('secondary')}
            >
              Properties
            </button>
          </div>
          <div
            className="dock-column dock-column-primary"
            data-active={narrowColumn === 'primary'}
            style={{ gridColumn: 1, gridRow: '1 / 3' }}
          >
            {groups
              .filter((group) => group.visible && group.id === 'layers')
              .map((group) => renderGroup(group, 0))}
          </div>
          <div
            className="dock-column dock-column-secondary"
            data-active={narrowColumn === 'secondary'}
            style={{ gridColumn: 2, gridRow: '1 / 3' }}
          >
            {groups
              .filter((group) => group.visible && group.id !== 'layers')
              .map((group, index) => renderGroup(group, index))}
          </div>
        </>
      )}
    </aside>
  );
}

function PanelGroupHeader({
  group,
  onChange,
  onOpenMenu,
}: {
  readonly group: DockGroupState;
  readonly onChange: (change: Partial<DockGroupState>) => void;
  readonly onOpenMenu: (event: React.MouseEvent<HTMLElement>) => void;
}) {
  const profilePanels: Record<DockGroupId, readonly PanelId[]> = {
    color: ['color', 'swatches'],
    properties: ['properties', 'adjustments'],
    layers: ['layers', 'channels', 'paths'],
    history: ['history', 'actions'],
  };
  const available = PANEL_REGISTRY.filter((panel) => profilePanels[group.id].includes(panel.id));
  const select = (id: PanelId) => {
    onChange({ activePanel: id, collapsed: false });
    requestAnimationFrame(() => document.getElementById(`panel-tab-${id}`)?.focus());
  };
  const onKeyDown = (event: KeyboardEvent<HTMLButtonElement>, panel: PanelId) => {
    const index = available.findIndex((item) => item.id === panel);
    let next: PanelId | undefined;
    if (event.key === 'ArrowRight') next = available[(index + 1) % available.length]?.id;
    if (event.key === 'ArrowLeft')
      next = available[(index - 1 + available.length) % available.length]?.id;
    if (event.key === 'Home') next = available[0]?.id;
    if (event.key === 'End') next = available.at(-1)?.id;
    if (next) {
      event.preventDefault();
      select(next);
    }
  };
  return (
    <div className="panel-dock-header" onContextMenu={onOpenMenu}>
      <div className="panel-tabs" role="tablist" aria-label={`${group.id} panels`}>
        {available.map((panel) => (
          <button
            id={`panel-tab-${panel.id}`}
            className="tab-button"
            role="tab"
            aria-controls={`panel-${panel.id}`}
            aria-selected={group.activePanel === panel.id}
            tabIndex={group.activePanel === panel.id ? 0 : -1}
            onClick={() => select(panel.id)}
            onKeyDown={(event) => onKeyDown(event, panel.id)}
            key={panel.id}
          >
            {panel.label}
          </button>
        ))}
      </div>
      <button
        className="panel-collapse"
        aria-label={
          group.collapsed ? `Expand ${group.id} panel group` : `Collapse ${group.id} panel group`
        }
        onClick={() => onChange({ collapsed: !group.collapsed })}
      >
        <Icon name="chevron" />
      </button>
      <button
        type="button"
        className="panel-menu-button"
        aria-label={`Open ${group.id} panel menu`}
        title={`Open ${group.id} panel menu`}
        onClick={onOpenMenu}
      >
        <Icon name="panelMenu" />
      </button>
    </div>
  );
}
