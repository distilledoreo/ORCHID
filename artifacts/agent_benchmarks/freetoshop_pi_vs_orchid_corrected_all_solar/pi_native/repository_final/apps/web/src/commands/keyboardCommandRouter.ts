import { executeCommand } from './commandRegistry.js';
import type { CommandRegistry } from './commandTypes.js';

export function routeKeyboardCommand(event: KeyboardEvent, registry: CommandRegistry): boolean {
  const key = event.key.toLowerCase();
  const primary = event.ctrlKey || event.metaKey;
  const match = [...registry.values()].find((command) =>
    (command.shortcuts ?? shortcutFromLabel(command.shortcut)).some(
      (binding) =>
        binding.key === key &&
        Boolean(binding.primary) === primary &&
        Boolean(binding.alt) === event.altKey &&
        Boolean(binding.shift) === event.shiftKey,
    ),
  );
  if (!match || !executeCommand(registry, match.id)) return false;
  event.preventDefault();
  return true;
}

function shortcutFromLabel(label: string | undefined) {
  if (!label) return [];
  const parts = label.toLowerCase().split('+');
  const key = parts.at(-1);
  if (!key) return [];
  return [
    {
      key,
      primary: parts.includes('ctrl') || parts.includes('cmd'),
      alt: parts.includes('alt') || parts.includes('option'),
      shift: parts.includes('shift'),
    },
  ];
}
