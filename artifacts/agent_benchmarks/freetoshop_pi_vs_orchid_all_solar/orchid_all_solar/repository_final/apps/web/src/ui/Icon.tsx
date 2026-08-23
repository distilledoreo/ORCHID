import type { SVGProps } from 'react';

export const ICON_NAMES = [
  'brand',
  'new',
  'open',
  'save',
  'export',
  'undo',
  'redo',
  'panels',
  'close',
  'chevron',
  'pan',
  'move',
  'transform',
  'crop',
  'select',
  'ellipse',
  'lasso',
  'wand',
  'pen',
  'brush',
  'eraser',
  'swap',
  'reset',
  'eye',
  'eyeOff',
  'lock',
  'mask',
  'vectorMask',
  'layer',
  'group',
  'ungroup',
  'text',
  'shape',
  'smartObject',
  'adjustment',
  'fx',
  'duplicate',
  'add',
  'trash',
  'link',
  'flipHorizontal',
  'flipVertical',
  'moveUp',
  'moveDown',
  'clipping',
  'more',
  'search',
  'filter',
  'panelMenu',
  'workspaceReset',
  'check',
  'clone',
] as const;

export type IconName = (typeof ICON_NAMES)[number];

const paths: Record<IconName, readonly string[]> = {
  brand: ['M4 3h12v4H8v3h7v4H8v7H4V3z'],
  new: ['M5 2h9l5 5v13H5V2z', 'M14 2v5h5', 'M12 10v7', 'M8.5 13.5h7'],
  open: ['M3 6h7l2 2h9l-3 11H4L3 6z', 'M4 6V4h7l2 2'],
  save: ['M4 3h14l2 2v16H4V3z', 'M8 3v6h8V3', 'M8 14h8v7H8z'],
  export: ['M12 3v12', 'M8 7l4-4 4 4', 'M5 12v8h14v-8'],
  undo: ['M9 7H4v-5', 'M4 7c4-4 12-4 15 2 2 5-1 10-6 11'],
  redo: ['M15 7h5v-5', 'M20 7c-4-4-12-4-15 2-2 5 1 10 6 11'],
  panels: ['M3 4h18v16H3z', 'M15 4v16', 'M15 10h6'],
  close: ['M6 6l12 12', 'M18 6L6 18'],
  chevron: ['M8 10l4 4 4-4'],
  pan: [
    'M8 11V6a2 2 0 014 0v4',
    'M12 10V5a2 2 0 014 0v6',
    'M16 11V8a2 2 0 014 0v5c0 5-3 8-8 8-3 0-5-2-7-5l-2-4a2 2 0 013-2l2 2',
  ],
  move: ['M12 2v20', 'M2 12h20', 'M12 2l-3 3', 'M12 2l3 3', 'M22 12l-3-3', 'M22 12l-3 3'],
  transform: ['M4 9V4h5', 'M15 4h5v5', 'M20 15v5h-5', 'M9 20H4v-5', 'M8 8h8v8H8z'],
  crop: ['M7 3v14a2 2 0 002 2h12', 'M3 7h14a2 2 0 012 2v12'],
  select: [
    'M4 4h5',
    'M4 4v5',
    'M20 4h-5',
    'M20 4v5',
    'M4 20h5',
    'M4 20v-5',
    'M20 20h-5',
    'M20 20v-5',
  ],
  ellipse: ['M3 12a9 7 0 1018 0 9 7 0 10-18 0z'],
  lasso: ['M20 11c0 5-4 8-9 8-5 0-8-3-8-7s4-7 9-7 8 2 8 6z', 'M11 19c0 3-4 4-5 1'],
  wand: ['M5 19L16 8', 'M14 3v3', 'M20 8h-3', 'M18 4l-2 2', 'M7 12l5 5'],
  pen: ['M4 20l3-7L17 3l4 4-10 10-7 3z', 'M14 6l4 4', 'M4 20l5-2-3-3-2 5z'],
  brush: ['M14 4l6 6-9 9c-2 2-6 1-7 1 0-1-1-5 1-7l9-9z', 'M12 6l6 6'],
  eraser: ['M4 14l9-10 7 7-8 9H8l-4-6z', 'M9 9l7 7'],
  swap: ['M7 7h10l-3-3', 'M17 17H7l3 3'],
  reset: ['M5 8V3l3 3', 'M5 6a8 8 0 111 12'],
  eye: ['M2 12s4-6 10-6 10 6 10 6-4 6-10 6S2 12 2 12z', 'M9 12a3 3 0 106 0 3 3 0 10-6 0z'],
  eyeOff: [
    'M3 3l18 18',
    'M10.6 6.2A10.6 10.6 0 0112 6c6 0 10 6 10 6a17.8 17.8 0 01-3.1 3.6',
    'M6.2 6.2C3.6 8 2 12 2 12s4 6 10 6c1 0 2-.2 2.8-.5',
    'M9.9 9.9a3 3 0 104.2 4.2',
  ],
  lock: ['M6 10h12v10H6z', 'M8 10V7a4 4 0 018 0v3'],
  mask: ['M3 5h18v14H3z', 'M8 12a4 4 0 108 0 4 4 0 10-8 0z'],
  vectorMask: ['M4 5h16v14H4z', 'M8 9l4-3 4 3-4 6-4-6z', 'M8 9h.01', 'M16 9h.01'],
  layer: ['M4 7l8-4 8 4-8 4-8-4z', 'M4 12l8 4 8-4', 'M4 17l8 4 8-4'],
  group: ['M3 6h7l2 2h9v11H3z'],
  ungroup: ['M3 6h7l2 2h5v10H3z', 'M14 10h7v8h-7', 'M17 7V4h4v4'],
  text: ['M5 4h14', 'M12 4v16', 'M8 20h8'],
  shape: ['M6 5h12v14H6z', 'M6 5h.01', 'M18 5h.01', 'M6 19h.01', 'M18 19h.01'],
  smartObject: ['M5 3h10l4 4v14H5z', 'M15 3v4h4', 'M12 10l3 2-3 5-3-5 3-2z'],
  adjustment: ['M4 12h16', 'M8 5v14', 'M16 5v14', 'M6 8h4', 'M14 16h4'],
  fx: ['M5 19L11 5h7', 'M8 12h8', 'M15 10l5 9', 'M20 10l-5 9'],
  duplicate: ['M8 8h12v12H8z', 'M4 4h12v4', 'M4 4v12h4'],
  add: ['M12 5v14', 'M5 12h14'],
  trash: ['M5 7h14', 'M9 7V4h6v3', 'M7 7l1 14h8l1-14'],
  link: ['M8 13l-2 2a3.5 3.5 0 105 5l3-3', 'M16 11l2-2a3.5 3.5 0 10-5-5l-3 3', 'M8 16l8-8'],
  flipHorizontal: ['M12 3v18', 'M9 7H4l3-3', 'M9 7l-3 3', 'M15 17h5l-3-3', 'M15 17l3 3'],
  flipVertical: ['M3 12h18', 'M7 9V4l-3 3', 'M7 9l3-3', 'M17 15v5l-3-3', 'M17 15l3 3'],
  moveUp: ['M12 19V5', 'M7 10l5-5 5 5'],
  moveDown: ['M12 5v14', 'M7 14l5 5 5-5'],
  clipping: ['M5 5v8h8', 'M13 13l6 6', 'M19 14v5h-5'],
  more: ['M5 12h.01', 'M12 12h.01', 'M19 12h.01'],
  search: ['M10.5 4a6.5 6.5 0 104.1 11.5L20 21', 'M10.5 4a6.5 6.5 0 000 13'],
  filter: ['M3 5h18l-7 8v6l-4 2v-8L3 5z'],
  panelMenu: ['M4 6h16', 'M4 12h16', 'M4 18h16'],
  workspaceReset: ['M20 11a8 8 0 10-2.3 5.7', 'M20 5v6h-6'],
  check: ['M5 12l4 4L19 6'],
  clone: ['M9 3L3 9l6 6', 'M15 3l6 6-6 6'],
};

export function Icon({ name, ...props }: SVGProps<SVGSVGElement> & { readonly name: IconName }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
      focusable="false"
      aria-hidden="true"
      {...props}
    >
      {paths[name].map((path) => (
        <path d={path} key={path} />
      ))}
    </svg>
  );
}
