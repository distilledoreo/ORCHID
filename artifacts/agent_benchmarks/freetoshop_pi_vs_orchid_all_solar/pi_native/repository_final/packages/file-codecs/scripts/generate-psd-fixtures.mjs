import { mkdirSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { writePsdUint8Array } from 'ag-psd';

const output = fileURLToPath(new URL('../test-fixtures/psd/', import.meta.url));
mkdirSync(output, { recursive: true });

function solid(width, height, color) {
  const data = new Uint8ClampedArray(width * height * 4);
  for (let offset = 0; offset < data.length; offset += 4) data.set(color, offset);
  return { width, height, data };
}

function write(name, psd) {
  writeFileSync(`${output}${name}`, writePsdUint8Array(psd, { generateThumbnail: false }));
}

write('flattened-rgb-8.psd', {
  width: 3,
  height: 2,
  imageData: solid(3, 2, [12, 34, 56, 255]),
});

const layeredComposite = solid(4, 4, [0, 0, 180, 255]);
for (let y = 1; y < 3; y += 1) {
  for (let x = 1; x < 3; x += 1) {
    layeredComposite.data.set([220, 20, 10, 255], (y * 4 + x) * 4);
  }
}
write('pixel-layers-rgb-8.psd', {
  width: 4,
  height: 4,
  imageData: layeredComposite,
  children: [
    {
      name: 'Top red',
      left: 1,
      top: 1,
      right: 3,
      bottom: 3,
      opacity: 0.8,
      blendMode: 'multiply',
      imageData: solid(2, 2, [220, 20, 10, 255]),
    },
    {
      name: 'Bottom blue',
      left: 0,
      top: 0,
      right: 4,
      bottom: 4,
      imageData: solid(4, 4, [0, 0, 180, 255]),
    },
  ],
});

write('nested-group-rgb-8.psd', {
  width: 4,
  height: 4,
  imageData: solid(4, 4, [0, 140, 40, 255]),
  children: [
    {
      name: 'Foreground group',
      opened: true,
      blendMode: 'pass through',
      children: [
        {
          name: 'Green square',
          left: 1,
          top: 1,
          right: 3,
          bottom: 3,
          imageData: solid(2, 2, [0, 200, 60, 255]),
        },
      ],
    },
    {
      name: 'Background',
      left: 0,
      top: 0,
      right: 4,
      bottom: 4,
      imageData: solid(4, 4, [20, 20, 20, 255]),
    },
  ],
});

// Layered RGB with user mask, drop-shadow + stroke effects, and guides.
// Exercises the real import path without unit-test parse injects.
const maskPixels = solid(4, 4, [255, 255, 255, 255]);
for (let y = 0; y < 4; y += 1) {
  for (let x = 0; x < 4; x += 1) {
    const value = (x + y) % 2 === 0 ? 255 : 64;
    maskPixels.data.set([value, value, value, 255], (y * 4 + x) * 4);
  }
}
write('mask-effects-guides-rgb-8.psd', {
  width: 8,
  height: 8,
  bitsPerChannel: 8,
  colorMode: 3,
  imageData: solid(8, 8, [30, 30, 40, 255]),
  children: [
    {
      name: 'Masked red',
      left: 2,
      top: 2,
      right: 6,
      bottom: 6,
      opacity: 0.9,
      blendMode: 'normal',
      imageData: solid(4, 4, [220, 30, 20, 255]),
      mask: {
        top: 2,
        left: 2,
        bottom: 6,
        right: 6,
        defaultColor: 0,
        disabled: false,
        imageData: maskPixels,
      },
      effects: {
        dropShadow: [
          {
            present: true,
            showInDialog: true,
            enabled: true,
            size: { value: 4, units: 'Pixels' },
            distance: { value: 2, units: 'Pixels' },
            opacity: 0.6,
            angle: 120,
            color: { r: 0, g: 0, b: 0 },
          },
        ],
        stroke: [
          {
            present: true,
            showInDialog: true,
            enabled: true,
            size: { value: 1, units: 'Pixels' },
            opacity: 1,
            color: { r: 255, g: 255, b: 0 },
          },
        ],
      },
    },
    {
      name: 'Background',
      left: 0,
      top: 0,
      right: 8,
      bottom: 8,
      imageData: solid(8, 8, [20, 40, 80, 255]),
    },
  ],
  imageResources: {
    gridAndGuidesInformation: {
      grid: { horizontal: 8, vertical: 8 },
      guides: [
        { location: 4, direction: 'vertical' },
        { location: 4, direction: 'horizontal' },
      ],
    },
  },
});

// Embedded smart object with linked nested PSD payload (no parse injects).
{
  const soId = '20953ddb-9391-11ec-b4f1-c15674f50bc4';
  const nested = writePsdUint8Array(
    {
      width: 4,
      height: 4,
      imageData: solid(4, 4, [10, 20, 30, 255]),
      children: [
        {
          name: 'Inner pixel',
          left: 0,
          top: 0,
          right: 4,
          bottom: 4,
          imageData: solid(4, 4, [200, 50, 50, 255]),
        },
      ],
    },
    { generateThumbnail: false },
  );
  write('smart-object-linked-rgb-8.psd', {
    width: 8,
    height: 8,
    bitsPerChannel: 8,
    colorMode: 3,
    imageData: solid(8, 8, [40, 40, 40, 255]),
    linkedFiles: [
      {
        id: soId,
        name: 'inner.psd',
        type: '8BPB',
        data: nested,
      },
    ],
    children: [
      {
        name: 'Smart Object',
        left: 1,
        top: 1,
        right: 5,
        bottom: 5,
        placedLayer: {
          id: soId,
          type: 'raster',
          transform: [1, 0, 0, 1, 1, 1],
          width: 4,
          height: 4,
        },
        imageData: solid(4, 4, [180, 60, 60, 255]),
      },
      {
        name: 'Background',
        left: 0,
        top: 0,
        right: 8,
        bottom: 8,
        imageData: solid(8, 8, [30, 30, 50, 255]),
      },
    ],
  });
}

// Text layer + background for real binary text import (no parse injects).
write('text-layer-rgb-8.psd', {
  width: 16,
  height: 8,
  bitsPerChannel: 8,
  colorMode: 3,
  imageData: solid(16, 8, [240, 240, 240, 255]),
  children: [
    {
      name: 'Title',
      left: 1,
      top: 1,
      right: 12,
      bottom: 6,
      text: {
        text: 'Hello',
        transform: [1, 0, 0, 1, 1, 5],
        style: {
          font: { name: 'Arial' },
          fontSize: 12,
          fillColor: { r: 0, g: 0, b: 0 },
        },
      },
      imageData: solid(11, 5, [0, 0, 0, 0]),
    },
    {
      name: 'Background',
      left: 0,
      top: 0,
      right: 16,
      bottom: 8,
      imageData: solid(16, 8, [200, 210, 220, 255]),
    },
  ],
});

writeFileSync(`${output}truncated.psd`, new Uint8Array([0x38, 0x42, 0x50, 0x53]));
const psbHeader = new ArrayBuffer(26);
const psb = new DataView(psbHeader);
for (const [index, value] of [0x38, 0x42, 0x50, 0x53].entries()) psb.setUint8(index, value);
psb.setUint16(4, 2, false);
psb.setUint16(12, 4, false);
psb.setUint32(14, 1, false);
psb.setUint32(18, 1, false);
psb.setUint16(22, 8, false);
psb.setUint16(24, 3, false);
writeFileSync(`${output}psb-header.psb`, new Uint8Array(psbHeader));
