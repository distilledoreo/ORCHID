export type CommandMenu =
  | 'file'
  | 'edit'
  | 'image'
  | 'layer'
  | 'type'
  | 'select'
  | 'filter'
  | 'view'
  | 'window'
  | 'help';

export type CommandSubmenuId = string;

export interface EditorCommand {
  readonly id: string;
  readonly label: string;
  readonly menu: CommandMenu;
  readonly submenu?: CommandSubmenuId;
  readonly order?: number;
  readonly shortcut?: string;
  readonly shortcuts?: readonly ShortcutBinding[];
  readonly enabled: boolean;
  readonly checked?: boolean;
  readonly separatorBefore?: boolean;
  readonly execute: () => void;
}

export interface ShortcutBinding {
  readonly key: string;
  readonly primary?: boolean;
  readonly alt?: boolean;
  readonly shift?: boolean;
}

export type CommandRegistry = ReadonlyMap<string, EditorCommand>;
