import { useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent } from 'react';
import type { CommandRegistry } from '../commands/commandTypes.js';

export function ContextMenu({
  x,
  y,
  commandIds,
  registry,
  onClose,
  returnFocus,
}: {
  readonly x: number;
  readonly y: number;
  readonly commandIds: readonly string[];
  readonly registry: CommandRegistry;
  readonly onClose: () => void;
  readonly returnFocus?: HTMLElement | null;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [position, setPosition] = useState({ x, y });
  useLayoutEffect(() => {
    const bounds = ref.current?.getBoundingClientRect();
    if (!bounds) return;
    const next = {
      x: Math.max(8, Math.min(x, window.innerWidth - bounds.width - 8)),
      y: Math.max(8, Math.min(y, window.innerHeight - bounds.height - 8)),
    };
    setPosition((current) => (current.x === next.x && current.y === next.y ? current : next));
  }, [x, y]);
  const closeAndRestore = () => {
    onClose();
    requestAnimationFrame(() => returnFocus?.focus());
  };
  useEffect(() => {
    ref.current?.querySelector<HTMLButtonElement>('button:not(:disabled)')?.focus();
    const close = () => onClose();
    window.addEventListener('pointerdown', close);
    return () => window.removeEventListener('pointerdown', close);
  }, [onClose]);
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const items = [
      ...event.currentTarget.querySelectorAll<HTMLButtonElement>('button:not(:disabled)'),
    ];
    const index = items.indexOf(document.activeElement as HTMLButtonElement);
    if (event.key === 'Escape') {
      event.preventDefault();
      closeAndRestore();
    } else if (event.key === 'Home' || event.key === 'End') {
      event.preventDefault();
      items[event.key === 'Home' ? 0 : items.length - 1]?.focus();
    } else if (event.key === 'Tab') {
      onClose();
    } else if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      items[(index + (event.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length]?.focus();
    }
  };
  return (
    <div
      ref={ref}
      className="context-menu"
      role="menu"
      aria-label="Context commands"
      style={{ left: position.x, top: position.y }}
      onKeyDown={onKeyDown}
      onPointerDown={(event) => event.stopPropagation()}
    >
      {commandIds.map((id) => {
        const command = registry.get(id);
        if (!command) return null;
        return (
          <button
            role="menuitem"
            disabled={!command.enabled}
            key={id}
            onClick={() => {
              command.execute();
              closeAndRestore();
            }}
          >
            <span>{command.label}</span>
            {command.shortcut && <kbd>{command.shortcut}</kbd>}
          </button>
        );
      })}
    </div>
  );
}
