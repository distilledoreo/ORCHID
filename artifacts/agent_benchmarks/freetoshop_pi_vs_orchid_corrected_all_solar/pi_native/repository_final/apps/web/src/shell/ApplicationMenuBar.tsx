import { useEffect, useRef, useState } from 'react';
import { COMMAND_SUBMENU_DEFINITIONS, MENU_DEFINITIONS } from '../commands/menuDefinitions.js';
import { buildCommandMenuTree } from '../commands/commandMenuTree.js';
import type { CommandMenu, CommandRegistry } from '../commands/commandTypes.js';
import { CommandMenuSurface } from '../commands/CommandMenuSurface.js';
import { Icon, type IconName } from '../ui/Icon.js';
import { PROFESSIONAL_EDITING_PROFILE } from '../workspace/workspaceProfiles.js';

const quickCommands: ReadonlyArray<{ readonly id: string; readonly icon: IconName }> = [
  { id: 'file.new', icon: 'new' },
  { id: 'file.save', icon: 'save' },
  { id: 'file.export-png', icon: 'export' },
  { id: 'edit.undo', icon: 'undo' },
  { id: 'edit.redo', icon: 'redo' },
];

export function ApplicationMenuBar({ registry }: { readonly registry: CommandRegistry }) {
  const [openMenu, setOpenMenu] = useState<CommandMenu | null>(null);
  const menuBarRef = useRef<HTMLElement>(null);

  useEffect(() => {
    if (!openMenu) return;
    const close = (event: PointerEvent) => {
      if (!menuBarRef.current?.contains(event.target as Node)) setOpenMenu(null);
    };
    window.addEventListener('pointerdown', close);
    return () => window.removeEventListener('pointerdown', close);
  }, [openMenu]);

  const focusMenu = (menu: CommandMenu, direction: 1 | -1 = 1) => {
    const menus = MENU_DEFINITIONS.filter((item) =>
      PROFESSIONAL_EDITING_PROFILE.menus.includes(item.id),
    );
    const index = menus.findIndex((item) => item.id === menu);
    const next = menus[(index + direction + menus.length) % menus.length];
    if (!next) return;
    setOpenMenu(next.id);
    requestAnimationFrame(() => document.getElementById(`menu-${next.id}`)?.focus());
  };

  return (
    <header className="application-menubar" ref={menuBarRef}>
      <div className="brand" aria-label="FreetoShop Studio">
        <span className="brand-mark">
          <Icon name="brand" />
        </span>
        <span className="brand-name">FreetoShop</span>
      </div>
      <nav className="menu-strip" aria-label="Application menus" role="menubar">
        {MENU_DEFINITIONS.filter((menu) =>
          PROFESSIONAL_EDITING_PROFILE.menus.includes(menu.id),
        ).map((menu) => {
          const nodes = buildCommandMenuTree(
            menu.id,
            [...registry.values()].filter((command) => command.menu === menu.id),
            COMMAND_SUBMENU_DEFINITIONS,
          );
          return (
            <div className="menu-root" key={menu.id}>
              <button
                id={`menu-${menu.id}`}
                className="menu-trigger"
                type="button"
                role="menuitem"
                aria-haspopup="menu"
                aria-expanded={openMenu === menu.id}
                onClick={() => setOpenMenu((current) => (current === menu.id ? null : menu.id))}
                onKeyDown={(event) => {
                  if (event.key === 'ArrowDown' || event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault();
                    setOpenMenu(menu.id);
                    requestAnimationFrame(() =>
                      document
                        .querySelector<HTMLElement>(`#menu-popup-${menu.id} [role^='menuitem']`)
                        ?.focus(),
                    );
                  } else if (event.key === 'ArrowRight') {
                    event.preventDefault();
                    focusMenu(menu.id, 1);
                  } else if (event.key === 'ArrowLeft') {
                    event.preventDefault();
                    focusMenu(menu.id, -1);
                  }
                }}
              >
                {menu.label}
              </button>
              {openMenu === menu.id && (
                <CommandMenuSurface
                  nodes={nodes}
                  registry={registry}
                  ariaLabel={`${menu.label} commands`}
                  id={`menu-popup-${menu.id}`}
                  onCloseAll={() => {
                    setOpenMenu(null);
                    document.getElementById(`menu-${menu.id}`)?.focus();
                  }}
                  onMoveRootMenu={(direction) => focusMenu(menu.id, direction)}
                />
              )}
            </div>
          );
        })}
      </nav>
      <div className="menubar-spacer" />
      <div className="command-quick-access" aria-label="Quick access commands">
        {quickCommands.map(({ id, icon }) => {
          const command = registry.get(id);
          if (!command) return null;
          return (
            <button
              className="icon-button"
              aria-label={command.label}
              data-command={
                id === 'image.size'
                  ? 'image-size'
                  : id === 'image.canvas-size'
                    ? 'canvas-size'
                    : id === 'filter.last'
                      ? 'filters'
                      : undefined
              }
              title={command.shortcut ? `${command.label} (${command.shortcut})` : command.label}
              disabled={!command.enabled}
              onClick={command.execute}
              key={id}
            >
              <Icon name={icon} />
            </button>
          );
        })}
      </div>
    </header>
  );
}
