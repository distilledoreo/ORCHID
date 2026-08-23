import type { CommandMenu, CommandSubmenuId } from './commandTypes.js';

export interface CommandSubmenuDefinition {
  readonly id: CommandSubmenuId;
  readonly menu: CommandMenu;
  readonly parentId?: CommandSubmenuId;
  readonly label: string;
  readonly order: number;
  readonly separatorBefore?: boolean;
}

export interface MenuDefinition {
  readonly id: CommandMenu;
  readonly label: string;
}

export const MENU_DEFINITIONS: readonly MenuDefinition[] = [
  { id: 'file', label: 'File' },
  { id: 'edit', label: 'Edit' },
  { id: 'image', label: 'Image' },
  { id: 'layer', label: 'Layer' },
  { id: 'type', label: 'Type' },
  { id: 'select', label: 'Select' },
  { id: 'filter', label: 'Filter' },
  { id: 'view', label: 'View' },
  { id: 'window', label: 'Window' },
  { id: 'help', label: 'Help' },
];

export const COMMAND_SUBMENU_DEFINITIONS: readonly CommandSubmenuDefinition[] = [
  { id: 'image.adjustments', menu: 'image', label: 'Adjustments', order: 100 },
  { id: 'filter.blur', menu: 'filter', label: 'Blur', order: 100, separatorBefore: true },
  {
    id: 'layer.new-adjustment',
    menu: 'layer',
    label: 'New Adjustment Layer',
    order: 120,
    separatorBefore: true,
  },
];
