import { describe, expect, it, vi } from 'vitest';
import { createCommandRegistry, executeCommand } from './commandRegistry.js';

describe('command registry', () => {
  it('rejects duplicate identifiers', () => {
    const command = {
      id: 'file.save',
      label: 'Save',
      menu: 'file' as const,
      enabled: true,
      execute: vi.fn(),
    };
    expect(() => createCommandRegistry([command, command])).toThrow('Duplicate command id');
  });

  it('executes enabled commands and blocks disabled commands', () => {
    const save = vi.fn();
    const registry = createCommandRegistry([
      { id: 'file.save', label: 'Save', menu: 'file', enabled: true, execute: save },
      { id: 'edit.undo', label: 'Undo', menu: 'edit', enabled: false, execute: vi.fn() },
    ]);
    expect(executeCommand(registry, 'file.save')).toBe(true);
    expect(save).toHaveBeenCalledOnce();
    expect(executeCommand(registry, 'edit.undo')).toBe(false);
    expect(executeCommand(registry, 'missing')).toBe(false);
  });
});
