import type { CommandMenu, EditorCommand } from './commandTypes.js';
import type { CommandSubmenuDefinition } from './menuDefinitions.js';

export interface CommandLeafNode {
  readonly kind: 'command';
  readonly command: EditorCommand;
  readonly order: number;
  readonly insertionIndex: number;
}

export interface CommandSubmenuNode {
  readonly kind: 'submenu';
  readonly definition: CommandSubmenuDefinition;
  readonly children: readonly CommandMenuNode[];
  readonly order: number;
  readonly insertionIndex: number;
}

export type CommandMenuNode = CommandLeafNode | CommandSubmenuNode;

export function buildCommandMenuTree(
  menu: CommandMenu,
  commands: readonly EditorCommand[],
  submenuDefinitions: readonly CommandSubmenuDefinition[],
): readonly CommandMenuNode[] {
  const allDefinitions = new Map(
    submenuDefinitions.map((definition) => [definition.id, definition]),
  );
  const definitions = submenuDefinitions.filter((definition) => definition.menu === menu);
  const byId = new Map<string, CommandSubmenuDefinition>();
  definitions.forEach((definition) => {
    if (byId.has(definition.id)) throw new Error(`Duplicate submenu id: ${definition.id}`);
    byId.set(definition.id, definition);
  });
  const commandIndexes = new Map<string, number>();
  commands.forEach((command, index) => commandIndexes.set(command.id, index));
  for (const definition of definitions) {
    if (!definition.parentId) continue;
    const parent = byId.get(definition.parentId);
    const anyParent = allDefinitions.get(definition.parentId);
    if (!parent && anyParent && anyParent.menu !== definition.menu)
      throw new Error(`Submenu parent belongs to another menu: ${definition.parentId}`);
    if (!parent) throw new Error(`Unknown submenu parent: ${definition.parentId}`);
  }
  for (const command of commands) {
    if (command.submenu && !byId.has(command.submenu))
      throw new Error(`Unknown submenu referenced by command ${command.id}: ${command.submenu}`);
  }

  const visiting = new Set<string>();
  const built = new Map<string, CommandSubmenuNode>();
  const childrenFor = (id: string): CommandMenuNode[] => {
    if (visiting.has(id)) throw new Error(`Submenu cycle detected at: ${id}`);
    visiting.add(id);
    const definition = byId.get(id);
    if (!definition) throw new Error(`Unknown submenu: ${id}`);
    const children: CommandMenuNode[] = [];
    definitions.forEach((child, index) => {
      if (child.parentId === id) {
        const node = makeSubmenu(child.id, index);
        if (node.children.length > 0) children.push(node);
      }
    });
    commands.forEach((command, index) => {
      if (command.submenu === id) {
        children.push({
          kind: 'command',
          command,
          order: command.order ?? Number.POSITIVE_INFINITY,
          insertionIndex: commandIndexes.get(command.id) ?? index,
        });
      }
    });
    visiting.delete(id);
    return sortNodes(children);
  };
  const makeSubmenu = (id: string, insertionIndex: number): CommandSubmenuNode => {
    const existing = built.get(id);
    if (existing) return existing;
    const definition = byId.get(id);
    if (!definition) throw new Error(`Unknown submenu: ${id}`);
    const node: CommandSubmenuNode = {
      kind: 'submenu',
      definition,
      children: childrenFor(id),
      order: definition.order,
      insertionIndex,
    };
    built.set(id, node);
    return node;
  };
  const root: CommandMenuNode[] = [];
  definitions.forEach((definition, index) => {
    if (!definition.parentId) {
      const node = makeSubmenu(definition.id, index);
      if (node.children.length > 0) root.push(node);
    }
  });
  definitions.forEach((definition, index) => {
    if (!built.has(definition.id)) makeSubmenu(definition.id, index);
  });
  commands.forEach((command, index) => {
    if (!command.submenu) {
      root.push({
        kind: 'command',
        command,
        order: command.order ?? Number.POSITIVE_INFINITY,
        insertionIndex: commandIndexes.get(command.id) ?? index,
      });
    }
  });
  return sortNodes(root);
}

function sortNodes(nodes: readonly CommandMenuNode[]): CommandMenuNode[] {
  return [...nodes].sort(
    (left, right) => left.order - right.order || left.insertionIndex - right.insertionIndex,
  );
}
