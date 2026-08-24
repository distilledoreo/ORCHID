import { useCallback, useEffect, useRef, useState, type KeyboardEvent } from 'react';
import type { Tool } from '../editor/types.js';
import { Icon } from '../ui/Icon.js';
import { TOOL_FAMILIES, type ToolDefinition } from '../tools/toolRegistry.js';

export interface ToolRailProps {
  readonly documentOpen: boolean;
  readonly transformAvailable: boolean;
  readonly tool: Tool;
  readonly onSelectTool: (tool: Tool) => void;
  readonly onActivateCrop: () => void;
  readonly onTogglePanels: () => void;
  readonly foregroundColor: string;
  readonly backgroundColor: string;
  readonly onForegroundColorChange: (color: string) => void;
  readonly onBackgroundColorChange: (color: string) => void;
}

export function ToolRail(props: ToolRailProps) {
  const [openFamily, setOpenFamily] = useState<string | null>(null);
  const railRef = useRef<HTMLElement>(null);
  const triggerRefs = useRef(new Map<string, HTMLButtonElement>());
  const closeFamily = useCallback(
    (returnFocus: boolean) => {
      const family = openFamily;
      setOpenFamily(null);
      if (returnFocus && family)
        requestAnimationFrame(() => triggerRefs.current.get(family)?.focus());
    },
    [openFamily],
  );
  const openFamilyMenu = (family: string) => setOpenFamily(family);
  const focusFlyoutItem = (family: string, index: number) => {
    requestAnimationFrame(() => {
      const items = railRef.current?.querySelectorAll<HTMLButtonElement>(
        `[data-family="${family}"] [role="menuitem"]`,
      );
      items?.[index]?.focus();
    });
  };
  const handleFlyoutKeyDown = (event: KeyboardEvent<HTMLDivElement>, family: string) => {
    const items = [
      ...event.currentTarget.querySelectorAll<HTMLButtonElement>('button:not(:disabled)'),
    ];
    const index = items.indexOf(document.activeElement as HTMLButtonElement);
    const delta = event.key === 'ArrowDown' || event.key === 'ArrowRight' ? 1 : -1;
    if (
      event.key === 'ArrowDown' ||
      event.key === 'ArrowRight' ||
      event.key === 'ArrowUp' ||
      event.key === 'ArrowLeft'
    ) {
      event.preventDefault();
      focusFlyoutItem(family, (index + delta + items.length) % items.length);
    } else if (event.key === 'Home' || event.key === 'End') {
      event.preventDefault();
      focusFlyoutItem(family, event.key === 'Home' ? 0 : items.length - 1);
    } else if (event.key === 'Escape') {
      event.preventDefault();
      closeFamily(true);
    } else if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      items[index]?.click();
      closeFamily(true);
    } else if (event.key === 'Tab') closeFamily(false);
  };
  useEffect(() => {
    if (openFamily === null) return;
    const onPointerDown = (event: PointerEvent) => {
      if (!railRef.current?.contains(event.target as Node)) closeFamily(false);
    };
    const onEscape = (event: globalThis.KeyboardEvent) => {
      if (event.key === 'Escape') closeFamily(true);
    };
    window.addEventListener('pointerdown', onPointerDown);
    window.addEventListener('keydown', onEscape);
    return () => {
      window.removeEventListener('pointerdown', onPointerDown);
      window.removeEventListener('keydown', onEscape);
    };
  }, [closeFamily, openFamily]);
  return (
    <aside ref={railRef} className="tool-rail" aria-label="Canvas tools">
      {TOOL_FAMILIES.map(({ family, tools }) => {
        const active = tools.find((item) => item.tool === props.tool) ?? tools[0];
        if (!active) return null;
        return (
          <div className="tool-family" data-family={family} key={family}>
            <ToolButton
              item={active}
              {...props}
              onOpenFamily={
                tools.length > 1
                  ? () =>
                      openFamily === family
                        ? closeFamily(false)
                        : (openFamilyMenu(family),
                          focusFlyoutItem(
                            family,
                            Math.max(
                              0,
                              tools.findIndex((item) => item.tool === props.tool),
                            ),
                          ))
                  : undefined
              }
              buttonRef={(element) => {
                if (element) triggerRefs.current.set(family, element);
                else triggerRefs.current.delete(family);
              }}
              hasPopup={tools.length > 1}
              expanded={openFamily === family}
              onKeyDown={(event) => {
                if (tools.length > 1 && ['ArrowRight', 'ArrowDown', 'F4'].includes(event.key)) {
                  event.preventDefault();
                  openFamilyMenu(family);
                  focusFlyoutItem(
                    family,
                    Math.max(
                      0,
                      tools.findIndex((item) => item.tool === props.tool),
                    ),
                  );
                }
              }}
            />
            {tools.length > 1 && <span className="tool-family-indicator" aria-hidden="true" />}
            {openFamily === family && (
              <div
                className="tool-flyout"
                role="menu"
                aria-label={`${family} tools`}
                onKeyDown={(event) => handleFlyoutKeyDown(event, family)}
              >
                {tools.map((item) => (
                  <ToolButton
                    key={item.tool}
                    item={item}
                    {...props}
                    menuItem
                    onOpenFamily={() => closeFamily(false)}
                  />
                ))}
              </div>
            )}
          </div>
        );
      })}
      <div className="rail-spacer" />
      <div className="rail-colors" title="Foreground and background colors">
        <input
          aria-label="Foreground color"
          type="color"
          value={props.foregroundColor}
          onChange={(event) => props.onForegroundColorChange(event.target.value)}
        />
        <input
          className="background-swatch"
          aria-label="Background color"
          type="color"
          value={props.backgroundColor}
          onChange={(event) => props.onBackgroundColorChange(event.target.value)}
        />
      </div>
      <button
        className="rail-color-command rail-default-colors"
        title="Default colors (D)"
        aria-label="Default foreground and background colors"
        onClick={() => {
          props.onForegroundColorChange('#000000');
          props.onBackgroundColorChange('#ffffff');
        }}
      >
        D
      </button>
      <button
        className="rail-color-command rail-swap-colors"
        title="Swap colors (X)"
        aria-label="Swap foreground and background colors"
        onClick={() => {
          props.onForegroundColorChange(props.backgroundColor);
          props.onBackgroundColorChange(props.foregroundColor);
        }}
      >
        <Icon name="swap" />
      </button>
      <button className="tool-button" aria-label="Toggle panels" onClick={props.onTogglePanels}>
        <Icon name="panels" />
      </button>
    </aside>
  );
}

function ToolButton({
  item,
  documentOpen,
  transformAvailable,
  tool,
  onSelectTool,
  onActivateCrop,
  onOpenFamily,
  buttonRef,
  onKeyDown,
  expanded,
  hasPopup,
  menuItem = false,
}: ToolRailProps & {
  readonly item: ToolDefinition;
  readonly onOpenFamily?: () => void;
  readonly buttonRef?: (element: HTMLButtonElement | null) => void;
  readonly onKeyDown?: (event: KeyboardEvent<HTMLButtonElement>) => void;
  readonly expanded?: boolean;
  readonly hasPopup?: boolean;
  readonly menuItem?: boolean;
}) {
  const disabled =
    item.tool === 'transform' ? !transformAvailable : item.needsDocument && !documentOpen;
  const activate = () => {
    if (item.tool === 'crop') onActivateCrop();
    else onSelectTool(item.tool);
    onOpenFamily?.();
  };
  return (
    <button
      className="tool-button"
      role={menuItem ? 'menuitem' : undefined}
      ref={buttonRef}
      onKeyDown={onKeyDown}
      aria-haspopup={hasPopup ? 'menu' : undefined}
      aria-expanded={hasPopup ? expanded : undefined}
      aria-label={item.accessibleLabel}
      title={`${item.label} (${item.shortcut})`}
      disabled={disabled}
      aria-pressed={!menuItem ? tool === item.tool : undefined}
      onClick={activate}
      onContextMenu={(event) => {
        if (onOpenFamily) {
          event.preventDefault();
          onOpenFamily();
        }
      }}
    >
      <Icon name={item.icon} />
      <span className="tool-label">{item.label.replace(' Tool', '')}</span>
    </button>
  );
}
