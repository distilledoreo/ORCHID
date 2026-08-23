import { useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent } from 'react';
import type { CommandRegistry } from './commandTypes.js';
import type { CommandMenuNode } from './commandMenuTree.js';
import { executeCommand } from './commandRegistry.js';
import { Icon } from '../ui/Icon.js';

export interface CommandMenuSurfaceProps {
  readonly nodes: readonly CommandMenuNode[];
  readonly registry: CommandRegistry;
  readonly ariaLabel: string;
  readonly id?: string;
  readonly depth?: number;
  readonly onCloseAll: () => void;
  readonly onMoveRootMenu?: (direction: 1 | -1) => void;
  readonly parentTrigger?: HTMLButtonElement | null;
}

export function CommandMenuSurface({
  nodes,
  registry,
  ariaLabel,
  id,
  depth = 0,
  onCloseAll,
  onMoveRootMenu,
  parentTrigger,
}: CommandMenuSurfaceProps) {
  const [openSubmenu, setOpenSubmenu] = useState<string | null>(null);
  const [placement, setPlacement] = useState<'right' | 'left'>('right');
  const [top, setTop] = useState<number | null>(null);
  const [left, setLeft] = useState<number | null>(null);
  const surfaceRef = useRef<HTMLDivElement>(null);
  const firstOpen = useRef(true);
  const hoverTimer = useRef<number | null>(null);

  useEffect(
    () => () => {
      if (hoverTimer.current !== null) window.clearTimeout(hoverTimer.current);
    },
    [],
  );

  useLayoutEffect(() => {
    if (!firstOpen.current) return;
    firstOpen.current = false;
    requestAnimationFrame(() => focusEnabledItem(surfaceRef.current));
  }, []);

  useLayoutEffect(() => {
    if (depth === 0 || !surfaceRef.current) return;
    const parent = surfaceRef.current.parentElement
      ?.querySelector<HTMLButtonElement>(':scope > button')
      ?.getBoundingClientRect();
    const menu = surfaceRef.current.getBoundingClientRect();
    if (!parent) return;
    const nextPlacement = parent.right + menu.width <= window.innerWidth - 8 ? 'right' : 'left';
    const nextLeft = nextPlacement === 'right' ? parent.right - 3 : parent.left - menu.width + 3;
    const resolvedPlacement = nextLeft < 8 ? 'right' : nextPlacement;
    setPlacement(resolvedPlacement);
    setLeft(resolvedPlacement === 'right' ? parent.right - 3 : parent.left - menu.width + 3);
    setTop(Math.max(8, Math.min(parent.top - 4, window.innerHeight - menu.height - 8)));
  }, [depth, nodes.length]);

  const closeSubmenu = () => setOpenSubmenu(null);
  const openAndFocus = (id: string) => {
    setOpenSubmenu(id);
    requestAnimationFrame(() => {
      surfaceRef.current
        ?.querySelector<HTMLElement>(`[data-submenu-id="${CSS.escape(id)}"] [role="menu"]`)
        ?.querySelector<HTMLElement>('[role^="menuitem"]:not(:disabled)')
        ?.focus();
    });
  };
  const scheduleOpen = (id: string) => {
    if (hoverTimer.current !== null) window.clearTimeout(hoverTimer.current);
    hoverTimer.current = window.setTimeout(() => openAndFocus(id), 120);
  };

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (depth > 0) event.stopPropagation();
    const active = event.target as HTMLElement;
    const items = enabledItems(surfaceRef.current);
    const index = items.indexOf(active);
    if (event.key === 'Tab') {
      onCloseAll();
      return;
    }
    if (event.key === 'Escape') {
      event.preventDefault();
      onCloseAll();
      return;
    }
    if (depth > 0 && event.key === 'ArrowLeft') {
      event.preventDefault();
      setOpenSubmenu(null);
      parentTrigger?.focus();
      return;
    }
    if (
      (event.key === 'ArrowRight' || event.key === 'ArrowLeft') &&
      onMoveRootMenu &&
      !active.hasAttribute('aria-haspopup')
    ) {
      event.preventDefault();
      onMoveRootMenu(event.key === 'ArrowRight' ? 1 : -1);
      return;
    }
    if (
      event.key === 'ArrowDown' ||
      event.key === 'ArrowUp' ||
      event.key === 'Home' ||
      event.key === 'End'
    ) {
      event.preventDefault();
      if (items.length === 0) return;
      const next =
        event.key === 'Home'
          ? 0
          : event.key === 'End'
            ? items.length - 1
            : (index + (event.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length;
      items[next]?.focus();
      return;
    }
    if (
      active.hasAttribute('aria-haspopup') &&
      (event.key === 'ArrowRight' || event.key === 'Enter' || event.key === ' ')
    ) {
      event.preventDefault();
      const id = active.getAttribute('data-submenu-id');
      if (id) openAndFocus(id);
    }
  };

  return (
    <div
      ref={surfaceRef}
      id={id}
      className={depth === 0 ? 'command-menu' : 'command-submenu'}
      data-placement={placement}
      role="menu"
      aria-label={ariaLabel}
      onKeyDown={onKeyDown}
      style={
        depth > 0
          ? {
              top: top ?? -4,
              left: left ?? 0,
            }
          : undefined
      }
    >
      {nodes.map((node) => {
        if (node.kind === 'command') {
          const command = registry.get(node.command.id) ?? node.command;
          return (
            <button
              key={command.id}
              className="command-menu-item"
              type="button"
              role={command.checked === undefined ? 'menuitem' : 'menuitemcheckbox'}
              aria-checked={command.checked}
              data-separator={command.separatorBefore || undefined}
              disabled={!command.enabled}
              onClick={() => {
                if (executeCommand(registry, command.id)) onCloseAll();
              }}
            >
              <span className="menu-check" aria-hidden="true">
                {command.checked ? <Icon name="check" /> : null}
              </span>
              <span>{command.label}</span>
              {command.shortcut && <kbd>{command.shortcut}</kbd>}
            </button>
          );
        }
        const id = node.definition.id;
        return (
          <div
            key={id}
            className="command-submenu-root"
            data-separator={node.definition.separatorBefore || undefined}
            data-submenu-id={id}
            onMouseEnter={() => scheduleOpen(id)}
          >
            <button
              className="command-menu-item command-submenu-trigger"
              type="button"
              role="menuitem"
              aria-haspopup="menu"
              aria-expanded={openSubmenu === id}
              data-submenu-id={id}
              onClick={() => (openSubmenu === id ? closeSubmenu() : openAndFocus(id))}
            >
              <span aria-hidden="true" />
              <span>{node.definition.label}</span>
              <Icon name="chevron" />
            </button>
            {openSubmenu === id && (
              <CommandMenuSurface
                nodes={node.children}
                registry={registry}
                ariaLabel={node.definition.label}
                depth={depth + 1}
                parentTrigger={surfaceRef.current?.querySelector<HTMLButtonElement>(
                  `button[data-submenu-id="${CSS.escape(id)}"]`,
                )}
                onCloseAll={onCloseAll}
                onMoveRootMenu={onMoveRootMenu}
              />
            )}
          </div>
        );
      })}
    </div>
  );
}

function enabledItems(root: HTMLElement | null): HTMLElement[] {
  return root
    ? [
        ...root.querySelectorAll<HTMLElement>(
          ':scope > [role^="menuitem"]:not(:disabled), :scope > .command-submenu-root > [role^="menuitem"]:not(:disabled)',
        ),
      ]
    : [];
}

function focusEnabledItem(root: HTMLElement | null): void {
  enabledItems(root)[0]?.focus();
}
