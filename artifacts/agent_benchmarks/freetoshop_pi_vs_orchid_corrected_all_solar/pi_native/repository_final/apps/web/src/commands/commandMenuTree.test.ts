import { describe, expect, it } from 'vitest';
import type { EditorCommand } from './commandTypes.js';
import { buildCommandMenuTree } from './commandMenuTree.js';
import type { CommandSubmenuDefinition } from './menuDefinitions.js';

const command = (id: string, extra: Partial<EditorCommand> = {}): EditorCommand => ({
  id,
  label: id,
  menu: 'image',
  enabled: true,
  execute: () => undefined,
  ...extra,
});
const submenu = (
  id: string,
  extra: Partial<CommandSubmenuDefinition> = {},
): CommandSubmenuDefinition => ({
  id,
  menu: 'image',
  label: id,
  order: 10,
  ...extra,
});

describe('buildCommandMenuTree', () => {
  it('keeps flat commands and attaches commands to submenus', () => {
    const tree = buildCommandMenuTree(
      'image',
      [command('root'), command('child', { submenu: 'one' })],
      [submenu('one')],
    );
    expect(
      tree.map((node) => (node.kind === 'command' ? node.command.id : node.definition.id)),
    ).toEqual(['one', 'root']);
    expect(tree[0]).toMatchObject({
      kind: 'submenu',
      children: [{ kind: 'command', command: { id: 'child' } }],
    });
  });

  it('supports nesting, omits empty submenus, and sorts stable ties', () => {
    const definitions = [
      submenu('parent', { order: 20 }),
      submenu('empty', { order: 1 }),
      submenu('child', { parentId: 'parent', order: 2 }),
    ];
    const tree = buildCommandMenuTree(
      'image',
      [command('b', { submenu: 'child', order: 4 }), command('a', { submenu: 'child', order: 4 })],
      definitions,
    );
    expect(tree).toHaveLength(1);
    expect(tree[0]).toMatchObject({
      definition: { id: 'parent' },
      children: [
        {
          definition: { id: 'child' },
          children: [{ command: { id: 'b' } }, { command: { id: 'a' } }],
        },
      ],
    });
  });

  it.each([
    ['unknown command submenu', [command('bad', { submenu: 'missing' })], [submenu('known')]],
    [
      'unknown parent',
      [command('ok', { submenu: 'child' })],
      [submenu('child', { parentId: 'missing' })],
    ],
    ['cycle', [], [submenu('a', { parentId: 'b' }), submenu('b', { parentId: 'a' })]],
  ])('%s throws', (_label, commands, definitions) => {
    expect(() => buildCommandMenuTree('image', commands, definitions)).toThrow();
  });

  it('does not mutate source arrays', () => {
    const commands = [command('a')];
    const definitions = [submenu('one')];
    buildCommandMenuTree('image', commands, definitions);
    expect(commands).toHaveLength(1);
    expect(commands[0]?.id).toBe('a');
    expect(definitions).toHaveLength(1);
    expect(definitions[0]?.id).toBe('one');
  });
});
