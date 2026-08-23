import type { Tool } from '../editor/types.js';
import type { IconName } from '../ui/Icon.js';

export type ToolFamilyId =
  | 'move'
  | 'marquee'
  | 'lasso'
  | 'selection'
  | 'crop'
  | 'brush'
  | 'eraser'
  | 'clone-stamp'
  | 'pen'
  | 'hand';
export interface ToolDefinition {
  readonly tool: Tool;
  readonly label: string;
  readonly accessibleLabel: string;
  readonly icon: IconName;
  readonly needsDocument: boolean;
  readonly family: ToolFamilyId;
  readonly slot: number;
  readonly shortcut: string;
}
export const TOOL_REGISTRY: readonly ToolDefinition[] = [
  {
    tool: 'move',
    label: 'Move Tool',
    accessibleLabel: 'Move layer',
    icon: 'move',
    needsDocument: true,
    family: 'move',
    slot: 1,
    shortcut: 'V',
  },
  {
    tool: 'transform',
    label: 'Free Transform',
    accessibleLabel: 'Free transform',
    icon: 'transform',
    needsDocument: true,
    family: 'move',
    slot: 1,
    shortcut: 'Ctrl+T',
  },
  {
    tool: 'select',
    label: 'Rectangular Marquee Tool',
    accessibleLabel: 'Rectangular select',
    icon: 'select',
    needsDocument: true,
    family: 'marquee',
    slot: 2,
    shortcut: 'M',
  },
  {
    tool: 'ellipse',
    label: 'Elliptical Marquee Tool',
    accessibleLabel: 'Elliptical select',
    icon: 'ellipse',
    needsDocument: true,
    family: 'marquee',
    slot: 2,
    shortcut: 'M',
  },
  {
    tool: 'lasso',
    label: 'Lasso Tool',
    accessibleLabel: 'Freehand lasso',
    icon: 'lasso',
    needsDocument: true,
    family: 'lasso',
    slot: 3,
    shortcut: 'L',
  },
  {
    tool: 'wand',
    label: 'Magic Wand Tool',
    accessibleLabel: 'Magic Wand',
    icon: 'wand',
    needsDocument: true,
    family: 'selection',
    slot: 4,
    shortcut: 'W',
  },
  {
    tool: 'crop',
    label: 'Crop Tool',
    accessibleLabel: 'Crop document',
    icon: 'crop',
    needsDocument: true,
    family: 'crop',
    slot: 5,
    shortcut: 'C',
  },
  {
    tool: 'brush',
    label: 'Brush Tool',
    accessibleLabel: 'Brush',
    icon: 'brush',
    needsDocument: true,
    family: 'brush',
    slot: 8,
    shortcut: 'B',
  },
  {
    tool: 'eraser',
    label: 'Eraser Tool',
    accessibleLabel: 'Eraser',
    icon: 'eraser',
    needsDocument: true,
    family: 'eraser',
    slot: 11,
    shortcut: 'E',
  },
  {
    tool: 'clone-stamp',
    label: 'Clone Stamp Tool',
    accessibleLabel: 'Clone stamp',
    icon: 'clone',
    needsDocument: true,
    family: 'clone-stamp',
    slot: 8,
    shortcut: 'S',
  },
  {
    tool: 'pen',
    label: 'Pen Tool',
    accessibleLabel: 'Pen tool',
    icon: 'pen',
    needsDocument: true,
    family: 'pen',
    slot: 15,
    shortcut: 'P',
  },
  {
    tool: 'pan',
    label: 'Hand Tool',
    accessibleLabel: 'Pan canvas',
    icon: 'pan',
    needsDocument: false,
    family: 'hand',
    slot: 19,
    shortcut: 'H',
  },
];
export const TOOL_FAMILIES = [...new Set(TOOL_REGISTRY.map((tool) => tool.family))]
  .map((family) => ({
    family,
    slot: Math.min(
      ...TOOL_REGISTRY.filter((tool) => tool.family === family).map((tool) => tool.slot),
    ),
    tools: TOOL_REGISTRY.filter((tool) => tool.family === family),
  }))
  .sort((a, b) => a.slot - b.slot);
export function toolDefinition(tool: Tool): ToolDefinition {
  const definition = TOOL_REGISTRY.find((item) => item.tool === tool) ?? TOOL_REGISTRY[0];
  if (!definition) throw new Error('Tool registry must contain at least one tool.');
  return definition;
}

export function toolForShortcut(key: string, current: Tool, shift: boolean): Tool | null {
  const normalized = key.toUpperCase();
  const matches = TOOL_REGISTRY.filter((item) => item.shortcut === normalized);
  if (matches.length === 0) return null;
  if (!shift || matches.length === 1) return matches[0]?.tool ?? null;
  const index = matches.findIndex((item) => item.tool === current);
  return matches[(index + 1 + matches.length) % matches.length]?.tool ?? null;
}
