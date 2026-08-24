import { describe, expect, it, vi } from 'vitest';
import { createCommandRegistry } from './commandRegistry.js';
import { routeKeyboardCommand } from './keyboardCommandRouter.js';

function event(key: string, options: Partial<KeyboardEvent> = {}): KeyboardEvent {
  return {
    key,
    ctrlKey: false,
    metaKey: false,
    altKey: false,
    shiftKey: false,
    preventDefault: vi.fn(),
    ...options,
  } as unknown as KeyboardEvent;
}

describe('professional shortcut profile routing', () => {
  it('routes familiar shortcut metadata from the command registry', () => {
    const execute = vi.fn();
    const registry = createCommandRegistry([
      {
        id: 'layer.new',
        label: 'New Layer',
        menu: 'layer',
        shortcut: 'Ctrl+Shift+N',
        enabled: true,
        execute,
      },
    ]);
    const keyboard = event('n', { ctrlKey: true, shiftKey: true });
    expect(routeKeyboardCommand(keyboard, registry)).toBe(true);
    expect(execute).toHaveBeenCalledOnce();
    expect(keyboard.preventDefault).toHaveBeenCalledOnce();
  });

  it('does not match missing modifiers or disabled commands', () => {
    const execute = vi.fn();
    const registry = createCommandRegistry([
      {
        id: 'select.inverse',
        label: 'Inverse',
        menu: 'select',
        shortcut: 'Ctrl+Shift+I',
        enabled: false,
        execute,
      },
    ]);
    expect(routeKeyboardCommand(event('i', { ctrlKey: true }), registry)).toBe(false);
    expect(routeKeyboardCommand(event('i', { ctrlKey: true, shiftKey: true }), registry)).toBe(
      false,
    );
    expect(execute).not.toHaveBeenCalled();
  });
});
