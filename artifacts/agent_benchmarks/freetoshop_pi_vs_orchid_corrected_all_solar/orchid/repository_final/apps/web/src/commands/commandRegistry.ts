import type { CommandRegistry, EditorCommand } from './commandTypes.js';

export function createCommandRegistry(commands: readonly EditorCommand[]): CommandRegistry {
  const registry = new Map<string, EditorCommand>();
  for (const command of commands) {
    if (registry.has(command.id)) throw new Error(`Duplicate command id: ${command.id}`);
    registry.set(command.id, command);
  }
  return registry;
}

export function executeCommand(registry: CommandRegistry, id: string): boolean {
  const command = registry.get(id);
  if (!command?.enabled) return false;
  command.execute();
  return true;
}
