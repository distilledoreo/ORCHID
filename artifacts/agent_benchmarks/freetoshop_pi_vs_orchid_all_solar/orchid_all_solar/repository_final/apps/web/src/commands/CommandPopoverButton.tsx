import { useRef, useState } from 'react';
import type { CommandRegistry } from './commandTypes.js';
import type { CommandLeafNode } from './commandMenuTree.js';
import { CommandMenuSurface } from './CommandMenuSurface.js';
import { Icon, type IconName } from '../ui/Icon.js';

export interface CommandPopoverButtonProps {
  readonly id: string;
  readonly label: string;
  readonly icon: IconName;
  readonly commandIds: readonly string[];
  readonly registry: CommandRegistry;
  readonly disabled?: boolean;
}

export function CommandPopoverButton({
  id,
  label,
  icon,
  commandIds,
  registry,
  disabled,
}: CommandPopoverButtonProps) {
  const [open, setOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const nodes: readonly CommandLeafNode[] = commandIds.flatMap((commandId, insertionIndex) => {
    const command = registry.get(commandId);
    return command
      ? [
          {
            kind: 'command' as const,
            command,
            order: command.order ?? Number.POSITIVE_INFINITY,
            insertionIndex,
          },
        ]
      : [];
  });
  return (
    <span className="command-popover-root">
      <button
        ref={triggerRef}
        id={id}
        className="icon-button"
        type="button"
        aria-label={label}
        title={label}
        aria-haspopup="menu"
        aria-expanded={open}
        disabled={disabled}
        onClick={() => setOpen((current) => !current)}
        onKeyDown={(event) => {
          if (event.key === 'Enter' || event.key === ' ' || event.key === 'ArrowDown') {
            event.preventDefault();
            setOpen(true);
          }
        }}
      >
        <Icon name={icon} />
      </button>
      {open && (
        <CommandMenuSurface
          nodes={nodes}
          registry={registry}
          ariaLabel={label}
          onCloseAll={() => {
            setOpen(false);
            triggerRef.current?.focus();
          }}
        />
      )}
    </span>
  );
}
