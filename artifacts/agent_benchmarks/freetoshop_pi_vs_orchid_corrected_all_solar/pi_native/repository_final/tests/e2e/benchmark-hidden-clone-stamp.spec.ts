import { expect, test } from '@playwright/test';
import { readFile } from 'node:fs/promises';

async function makePng(
  page: import('@playwright/test').Page,
  color: readonly [number, number, number, number],
) {
  const bytes = await page.evaluate(async (rgba) => {
    const canvas = document.createElement('canvas');
    canvas.width = 32;
    canvas.height = 32;
    const context = canvas.getContext('2d');
    if (!context) throw new Error('Canvas unavailable');
    const pixels = new Uint8ClampedArray(32 * 32 * 4);
    for (let offset = 0; offset < pixels.length; offset += 4) {
      pixels.set(rgba, offset);
    }
    context.putImageData(new ImageData(pixels, 32, 32), 0, 0);
    const blob = await new Promise<Blob>((resolve, reject) =>
      canvas.toBlob(
        (value) => (value ? resolve(value) : reject(new Error('PNG failed'))),
        'image/png',
      ),
    );
    return [...new Uint8Array(await blob.arrayBuffer())];
  }, color);
  return Buffer.from(bytes);
}

async function makeQuadrantPng(page: import('@playwright/test').Page) {
  const bytes = await page.evaluate(async () => {
    const canvas = document.createElement('canvas');
    canvas.width = 32;
    canvas.height = 32;
    const context = canvas.getContext('2d');
    if (!context) throw new Error('Canvas unavailable');
    for (const [color, x, y] of [
      ['rgb(200, 0, 0)', 0, 0],
      ['rgb(0, 180, 0)', 16, 0],
      ['rgb(0, 0, 220)', 0, 16],
      ['rgb(220, 180, 0)', 16, 16],
    ] as const) {
      context.fillStyle = color;
      context.fillRect(x, y, 16, 16);
    }
    const blob = await new Promise<Blob>((resolve, reject) =>
      canvas.toBlob(
        (value) => (value ? resolve(value) : reject(new Error('PNG failed'))),
        'image/png',
      ),
    );
    return [...new Uint8Array(await blob.arrayBuffer())];
  });
  return Buffer.from(bytes);
}

async function documentPoint(page: import('@playwright/test').Page, x: number, y: number) {
  const canvas = page.locator('canvas[aria-label$=" canvas"]');
  const box = await canvas.boundingBox();
  if (!box) throw new Error('Canvas bounds unavailable');
  const zoom = Math.min((box.width - 72) / 32, (box.height - 72) / 32);
  const panX = (box.width - 32 * zoom) / 2;
  const panY = (box.height - 32 * zoom) / 2;
  return { x: box.x + panX + (x + 0.5) * zoom, y: box.y + panY + (y + 0.5) * zoom };
}

async function documentCoordinate(page: import('@playwright/test').Page, x: number, y: number) {
  const canvas = page.locator('canvas[aria-label$=" canvas"]');
  const box = await canvas.boundingBox();
  if (!box) throw new Error('Canvas bounds unavailable');
  const zoom = Math.min((box.width - 72) / 32, (box.height - 72) / 32);
  const panX = (box.width - 32 * zoom) / 2;
  const panY = (box.height - 32 * zoom) / 2;
  return { x: box.x + panX + x * zoom, y: box.y + panY + y * zoom };
}

async function selectDockColumn(
  page: import('@playwright/test').Page,
  column: 'Layers' | 'Properties',
) {
  const tab = page
    .getByRole('tablist', { name: 'Panel columns' })
    .getByRole('tab', { name: column });
  if (await tab.isVisible()) await tab.click();
}

async function openHistoryPanel(page: import('@playwright/test').Page) {
  await selectDockColumn(page, 'Properties');
  await page.locator('#panel-tab-history').click();
}

async function openPropertiesPanel(page: import('@playwright/test').Page) {
  await selectDockColumn(page, 'Properties');
  await page.locator('#panel-tab-properties').click();
}

async function openMoreLayerActions(page: import('@playwright/test').Page) {
  const trigger = page.getByLabel('More layer actions');
  const open = await trigger.evaluate(
    (element) => (element.parentElement as HTMLDetailsElement | null)?.open ?? false,
  );
  if (!open) await trigger.click();
}

async function downloadedPngPath(page: import('@playwright/test').Page): Promise<string> {
  const exportButton = page.getByRole('button', { name: 'Export PNG' });
  await expect(exportButton).toBeEnabled();
  const downloadPromise = page.waitForEvent('download');
  await exportButton.click();
  const download = await downloadPromise;
  const path = await download.path();
  if (!path) throw new Error('Download path unavailable');
  // The download begins before exportPng's finally block clears the UI busy state.
  await expect(exportButton).toBeEnabled();
  return path;
}

async function downloadedNativeDocument(page: import('@playwright/test').Page) {
  const saveButton = page.getByRole('button', { name: 'Save document' });
  await expect(saveButton).toBeEnabled();
  const downloadPromise = page.waitForEvent('download');
  await saveButton.click();
  const download = await downloadPromise;
  const path = await download.path();
  if (!path) throw new Error('Native document download path unavailable');
  await expect(saveButton).toBeEnabled();
  return { buffer: await readFile(path), suggestedFilename: download.suggestedFilename() };
}

async function exportedPixel(page: import('@playwright/test').Page, x: number, y: number) {
  const path = await downloadedPngPath(page);
  const encoded = (await readFile(path)).toString('base64');
  return page.evaluate(
    async ({ source, pixelX, pixelY }) => {
      const binary = atob(source);
      const bytes = Uint8Array.from(binary, (character) => character.charCodeAt(0));
      const bitmap = await createImageBitmap(new Blob([bytes], { type: 'image/png' }));
      const canvas = document.createElement('canvas');
      canvas.width = bitmap.width;
      canvas.height = bitmap.height;
      const context = canvas.getContext('2d');
      if (!context) throw new Error('Offscreen canvas unavailable');
      context.drawImage(bitmap, 0, 0);
      return [...context.getImageData(pixelX, pixelY, 1, 1).data];
    },
    { source: encoded, pixelX: x, pixelY: y },
  );
}

async function exportedDimensions(page: import('@playwright/test').Page) {
  const path = await downloadedPngPath(page);
  const encoded = (await readFile(path)).toString('base64');
  return page.evaluate(async (source) => {
    const binary = atob(source);
    const bytes = Uint8Array.from(binary, (character) => character.charCodeAt(0));
    const bitmap = await createImageBitmap(new Blob([bytes], { type: 'image/png' }));
    return [bitmap.width, bitmap.height];
  }, encoded);
}

async function exportedOpaquePixelCount(page: import('@playwright/test').Page) {
  const path = await downloadedPngPath(page);
  const encoded = (await readFile(path)).toString('base64');
  return page.evaluate(async (source) => {
    const binary = atob(source);
    const bytes = Uint8Array.from(binary, (character) => character.charCodeAt(0));
    const bitmap = await createImageBitmap(new Blob([bytes], { type: 'image/png' }));
    const canvas = document.createElement('canvas');
    canvas.width = bitmap.width;
    canvas.height = bitmap.height;
    const context = canvas.getContext('2d');
    if (!context) throw new Error('Offscreen canvas unavailable');
    context.drawImage(bitmap, 0, 0);
    const pixels = context.getImageData(0, 0, bitmap.width, bitmap.height).data;
    let count = 0;
    for (let offset = 3; offset < pixels.length; offset += 4) {
      if ((pixels[offset] ?? 0) > 0) count += 1;
    }
    return count;
  }, encoded);
}

async function exportedTextColorCounts(page: import('@playwright/test').Page) {
  const path = await downloadedPngPath(page);
  const encoded = (await readFile(path)).toString('base64');
  return page.evaluate(async (source) => {
    const binary = atob(source);
    const bytes = Uint8Array.from(binary, (character) => character.charCodeAt(0));
    const bitmap = await createImageBitmap(new Blob([bytes], { type: 'image/png' }));
    const canvas = document.createElement('canvas');
    canvas.width = bitmap.width;
    canvas.height = bitmap.height;
    const context = canvas.getContext('2d');
    if (!context) throw new Error('Offscreen canvas unavailable');
    context.drawImage(bitmap, 0, 0);
    const pixels = context.getImageData(0, 0, bitmap.width, bitmap.height).data;
    let red = 0;
    let green = 0;
    for (let offset = 0; offset < pixels.length; offset += 4) {
      const alpha = pixels[offset + 3] ?? 0;
      if (alpha < 32) continue;
      if ((pixels[offset] ?? 0) > (pixels[offset + 1] ?? 0) * 1.5) red += 1;
      if ((pixels[offset + 1] ?? 0) > (pixels[offset] ?? 0) * 1.5) green += 1;
    }
    return { red, green };
  }, encoded);
}

function expectPixelNear(
  actual: readonly number[],
  expected: readonly [number, number, number, number],
  tolerance = 3,
) {
  expect(actual).toHaveLength(4);
  for (let channel = 0; channel < 4; channel += 1) {
    expect(Math.abs((actual[channel] ?? 0) - expected[channel])).toBeLessThanOrEqual(tolerance);
  }
}

async function visibleCanvasPixel(
  page: import('@playwright/test').Page,
  documentX: number,
  documentY: number,
) {
  const point = await documentPoint(page, documentX, documentY);
  return page.locator('canvas[aria-label$=" canvas"]').evaluate((canvas, clientPoint) => {
    const bounds = canvas.getBoundingClientRect();
    const x = Math.floor(((clientPoint.x - bounds.left) * canvas.width) / bounds.width);
    const y = Math.floor(((clientPoint.y - bounds.top) * canvas.height) / bounds.height);
    const context = canvas.getContext('2d');
    if (!context) throw new Error('Canvas unavailable');
    return [...context.getImageData(x, y, 1, 1).data];
  }, point);
}

async function expectSelectedLayerName(
  page: import('@playwright/test').Page,
  name: string | RegExp,
) {
  await expect(page.locator('.layer-row-button[aria-pressed="true"]').first()).toHaveAccessibleName(
    name,
  );
}

async function clickMenuCommand(
  page: import('@playwright/test').Page,
  menu: string,
  command: string,
) {
  await page.getByRole('menuitem', { name: menu, exact: true }).click();
  await page.getByRole('menuitem', { name: command }).click();
}

test.beforeEach(async ({ page }) => {
  // Match the professional dual-dock layout so History/Properties remain
  // simultaneously available without the narrow-column switcher.
  await page.setViewportSize({ width: 1672, height: 941 });
  await page.goto('/');
  await page.evaluate(async () => {
    localStorage.clear();
    await Promise.all(
      ['freetoshop', 'freetoshop-native-file-handles'].map(
        (databaseName) =>
          new Promise<void>((resolve, reject) => {
            const request = indexedDB.deleteDatabase(databaseName);
            request.onsuccess = () => resolve();
            request.onerror = () => reject(request.error);
            request.onblocked = () => resolve();
          }),
      ),
    );
  });
  await page.reload();
});

test('navigates and persists the professional workspace shell', async ({ page }) => {
  const fileMenu = page.getByRole('menuitem', { name: 'File' });
  await fileMenu.focus();
  await fileMenu.press('ArrowDown');
  await expect(page.getByRole('menu', { name: 'file commands' })).toBeVisible();
  await expect(page.getByRole('menuitem', { name: 'New' })).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(fileMenu).toBeFocused();

  await page.getByRole('button', { name: 'New', exact: true }).click();
  await expect(page.getByRole('tab', { name: /Untitled/ })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Close document' })).toBeVisible();

  await page.getByRole('menuitem', { name: 'Image' }).click();
  await page.getByRole('menuitem', { name: 'Image Size…' }).click();
  await expect(page.getByRole('dialog', { name: 'Image Size' })).toBeVisible();
  await page
    .getByRole('dialog', { name: 'Image Size' })
    .getByRole('button', { name: 'Cancel' })
    .click();

  await page.getByRole('button', { name: 'Brush', exact: true }).click();
  await expect(page.getByLabel('Tool options', { exact: true })).toContainText('Brush');
  await expect(page.getByLabel('Tool options', { exact: true })).toContainText(
    'Painting layer pixels',
  );
  await expect(page.getByRole('toolbar', { name: 'Contextual document actions' })).toHaveCount(0);

  await openPropertiesPanel(page);
  await expect(page.getByRole('heading', { name: 'Properties' })).toBeVisible();
  const splitter = page.getByRole('separator', { name: 'Resize primary dock' });
  await splitter.focus();
  await splitter.press('ArrowLeft');
  await expect(splitter).toHaveAttribute('aria-valuenow', '332');
  await page.getByRole('button', { name: 'Collapse panel dock' }).click();
  await expect(page.getByRole('button', { name: 'Expand panel dock' })).toBeVisible();

  await expect(page.getByText('Saved locally')).toBeVisible();
  await page.reload();
  await expect(page.getByText('Recovered your last local document.')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Expand panel dock' })).toBeVisible();
  const persisted = await page.evaluate(() =>
    JSON.parse(localStorage.getItem('freetoshop.workspace.v4') ?? '{}'),
  );
  expect(persisted).toMatchObject({
    version: 4,
    dockCollapsed: true,
    primaryDockWidth: 332,
  });
  expect(persisted.groups).toEqual(
    expect.arrayContaining([
      expect.objectContaining({ id: 'properties', activePanel: 'properties' }),
    ]),
  );
});

test('supports familiar professional-editor discovery and shortcut journeys', async ({ page }) => {
  await page.getByRole('button', { name: 'New', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Properties' })).toBeVisible();
  await expect(page.getByRole('heading', { name: /Layers · 1/ })).toBeVisible();

  await page.keyboard.press('ControlOrMeta+Shift+N');
  await expect(page.getByRole('heading', { name: /Layers · 2/ })).toBeVisible();
  await page.keyboard.press('ControlOrMeta+J');
  await expect(page.getByRole('heading', { name: /Layers · 3/ })).toBeVisible();

  await page.keyboard.press('b');
  await expect(page.getByLabel('Tool options', { exact: true })).toContainText('Brush');
  const brushSize = page.getByRole('slider', { name: 'Brush size' });
  const originalSize = Number(await brushSize.inputValue());
  await page.keyboard.press(']');
  await expect(brushSize).toHaveValue(String(originalSize + 2));

  await page.keyboard.press('h');
  const opacity = page.getByRole('spinbutton', { name: 'Layer opacity' });
  await opacity.focus();
  await opacity.press('b');
  await expect(page.getByLabel('Tool options', { exact: true })).toContainText('Hand');

  await page.getByRole('tab', { name: 'Channels' }).click();
  await expect(page.getByRole('heading', { name: /Channels/ })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Properties' })).toBeVisible();

  await page.getByRole('menuitem', { name: 'Window' }).click();
  await page.getByRole('menuitem', { name: 'Reset Professional Editing Workspace' }).click();
  await page
    .getByRole('dialog', { name: 'Reset Professional Editing Workspace' })
    .getByRole('button', { name: 'Reset Workspace' })
    .click();
  await expect(page.getByRole('tab', { name: 'Layers' })).toHaveAttribute('aria-selected', 'true');
});

test('imports a layered PSD in a worker and rejects a malformed PSD locally', async ({ page }) => {
  const input = page.getByLabel('Open image file');
  await input.setInputFiles({
    name: 'truncated.psd',
    mimeType: 'image/vnd.adobe.photoshop',
    buffer: await readFile('packages/file-codecs/test-fixtures/psd/truncated.psd'),
  });
  await expect(page.getByText('The PSD header is truncated.')).toBeVisible();

  await input.setInputFiles({
    name: 'pixel-layers-rgb-8.psd',
    mimeType: 'image/vnd.adobe.photoshop',
    buffer: await readFile('packages/file-codecs/test-fixtures/psd/pixel-layers-rgb-8.psd'),
  });
  await expect(
    page.getByText('pixel-layers-rgb-8.psd imported locally as editable layers'),
  ).toBeVisible();
  await expect(page.getByText('Layers · 2')).toBeVisible();
  await expect(page.getByText('Bottom blue', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Top red · multiply' })).toBeVisible();
  await expect(page.getByText('4 × 4 px')).toBeVisible();
  expect(await exportedDimensions(page)).toEqual([4, 4]);
  expect(await exportedPixel(page, 0, 0)).toEqual([0, 0, 180, 255]);
});

test('saves, reopens, and validates editable native documents', async ({ page }) => {
  const input = page.getByLabel('Open image file');
  await input.setInputFiles({
    name: 'native-source.png',
    mimeType: 'image/png',
    buffer: await makeQuadrantPng(page),
  });
  await openMoreLayerActions(page);
  await page.getByRole('button', { name: 'Add text layer' }).click();
  await page.getByLabel('Text content').fill('Still editable');
  await page.getByLabel('Text font size').fill('18');
  await expect(page.getByText('Layers · 2')).toBeVisible();

  const saved = await downloadedNativeDocument(page);
  expect(saved.suggestedFilename).toBe('native-source.freetoshop');
  expect(saved.buffer.subarray(0, 11).toString('utf8')).toBe('FREETOSHOP\0');
  expect(saved.buffer.readUInt16BE(11)).toBe(3);
  const manifestLength = saved.buffer.readUInt32BE(49);
  const resourceCountOffset = 53 + manifestLength;
  expect(saved.buffer.readUInt32BE(resourceCountOffset)).toBeGreaterThan(0);
  expect(saved.buffer[resourceCountOffset + 4 + 32]).toBe(1);

  await page.getByRole('button', { name: 'New', exact: true }).click();
  await expect(page.getByText('1024 × 768 px')).toBeVisible();
  await input.setInputFiles({
    name: saved.suggestedFilename,
    mimeType: 'application/x-freetoshop',
    buffer: saved.buffer,
  });
  await expect(
    page.getByText('native-source.freetoshop opened as an editable FreetoShop document.'),
  ).toBeVisible();
  await expect(page.getByText('Layers · 2')).toBeVisible();
  await expect(page.getByLabel('Text content')).toHaveValue('Still editable');
  await expect(page.getByLabel('Text font size')).toHaveValue('18');
  await expect(page.getByText('32 × 32 px')).toBeVisible();

  const shortcutDownload = page.waitForEvent('download');
  await page.keyboard.press('Control+s');
  expect((await shortcutDownload).suggestedFilename()).toBe('native-source.freetoshop');
  await expect(page.getByRole('button', { name: 'Save document' })).toBeEnabled();

  const corrupted = Buffer.from(saved.buffer);
  corrupted[corrupted.length - 1] = (corrupted[corrupted.length - 1] ?? 0) ^ 1;
  await input.setInputFiles({
    name: 'corrupted.freetoshop',
    mimeType: 'application/x-freetoshop',
    buffer: corrupted,
  });
  await expect(
    page.getByText('The native document checksum does not match its contents.'),
  ).toBeVisible();
  await expect(page.getByLabel('Text content')).toHaveValue('Still editable');
});

test('saves atomically to a chosen local file and reuses the handle for Ctrl+S', async ({
  page,
}) => {
  await page.addInitScript(() => {
    const probe = {
      pickerCalls: 0,
      openPickerCalls: 0,
      getFileCalls: 0,
      createWritableCalls: 0,
      closeCalls: 0,
      abortCalls: 0,
      writes: [] as Array<{ byteLength: number; magic: string; version: number }>,
      pickerOptions: null as unknown,
      openPickerOptions: null as unknown,
      storedBytes: null as Uint8Array | null,
    };
    Object.defineProperty(window, '__freetoshopNativeSaveProbe', {
      configurable: true,
      value: probe,
    });
    const nativeHandle = {
      kind: 'file',
      name: 'chosen-document.freetoshop',
      getFile: async () => {
        probe.getFileCalls += 1;
        return new File([probe.storedBytes ?? new Uint8Array()], nativeHandle.name, {
          type: 'application/x-freetoshop',
        });
      },
      createWritable: async (createOptions: unknown) => {
        probe.createWritableCalls += 1;
        if (JSON.stringify(createOptions) !== JSON.stringify({ keepExistingData: false })) {
          throw new Error('Expected complete replacement semantics.');
        }
        return {
          write: async (data: Uint8Array) => {
            const bytes = new Uint8Array(data);
            probe.storedBytes = new Uint8Array(bytes);
            probe.writes.push({
              byteLength: bytes.byteLength,
              magic: new TextDecoder().decode(bytes.subarray(0, 11)),
              version: new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength).getUint16(
                11,
                false,
              ),
            });
          },
          close: async () => {
            probe.closeCalls += 1;
          },
          abort: async () => {
            probe.abortCalls += 1;
          },
        };
      },
    };
    Object.defineProperty(window, 'showSaveFilePicker', {
      configurable: true,
      value: async (options: unknown) => {
        probe.pickerCalls += 1;
        probe.pickerOptions = options;
        return nativeHandle;
      },
    });
    Object.defineProperty(window, 'showOpenFilePicker', {
      configurable: true,
      value: async (options: unknown) => {
        probe.openPickerCalls += 1;
        probe.openPickerOptions = options;
        return [nativeHandle];
      },
    });
  });
  await page.reload();

  await page.getByRole('button', { name: 'Create document' }).click();
  await page.getByRole('menuitem', { name: 'File', exact: true }).click();
  await expect(page.getByRole('menuitem', { name: 'Save As to file…' })).toBeVisible();
  await page.keyboard.press('Escape');
  await page.keyboard.press('Control+Shift+s');
  await expect(page.getByText('Chosen-document.freetoshop saved to your device.')).toBeVisible();

  const readProbe = () =>
    page.evaluate(
      () =>
        (
          window as unknown as {
            __freetoshopNativeSaveProbe: {
              pickerCalls: number;
              openPickerCalls: number;
              getFileCalls: number;
              createWritableCalls: number;
              closeCalls: number;
              abortCalls: number;
              writes: Array<{ byteLength: number; magic: string; version: number }>;
              pickerOptions: unknown;
              openPickerOptions: unknown;
              storedBytes: Uint8Array | null;
            };
          }
        ).__freetoshopNativeSaveProbe,
    );
  await expect.poll(async () => (await readProbe()).closeCalls).toBe(1);
  let probe = await readProbe();
  expect(probe.pickerCalls).toBe(1);
  expect(probe.createWritableCalls).toBe(1);
  expect(probe.abortCalls).toBe(0);
  expect(probe.writes[0]).toMatchObject({
    magic: 'FREETOSHOP\0',
    version: 3,
  });
  expect(probe.pickerOptions).toMatchObject({
    id: 'freetoshop-native-document',
    suggestedName: 'Untitled-artwork.freetoshop',
  });

  await page.getByRole('button', { name: 'Add pixel layer' }).click();
  await page.keyboard.press('Control+s');
  await expect.poll(async () => (await readProbe()).closeCalls).toBe(2);
  probe = await readProbe();
  expect(probe.pickerCalls).toBe(1);
  expect(probe.createWritableCalls).toBe(2);
  expect(probe.writes).toHaveLength(2);
  expect(probe.abortCalls).toBe(0);

  await page.getByRole('button', { name: 'New', exact: true }).click();
  const fallbackDownload = page.waitForEvent('download');
  await page.keyboard.press('Control+s');
  await fallbackDownload;
  expect((await readProbe()).createWritableCalls).toBe(2);

  await page.keyboard.press('Control+o');
  await expect(
    page.getByText('Chosen-document.freetoshop opened as an editable FreetoShop document.'),
  ).toBeVisible();
  await expect(page.getByRole('button', { name: 'Layer 2', exact: true })).toBeVisible();
  probe = await readProbe();
  expect(probe.openPickerCalls).toBe(1);
  expect(probe.getFileCalls).toBe(1);
  expect(probe.openPickerOptions).toMatchObject({
    id: 'freetoshop-open-document',
    multiple: false,
  });

  await page.getByRole('button', { name: 'Add pixel layer' }).click();
  await page.keyboard.press('Control+s');
  await expect.poll(async () => (await readProbe()).closeCalls).toBe(3);
  probe = await readProbe();
  expect(probe.pickerCalls).toBe(1);
  expect(probe.openPickerCalls).toBe(1);
  expect(probe.createWritableCalls).toBe(3);
  expect(probe.abortCalls).toBe(0);
});

test('restores a native file handle after reload and saves back without another picker', async ({
  page,
  browserName,
}) => {
  test.skip(browserName !== 'chromium', 'Chromium provides cloneable File System Access handles.');
  await page.addInitScript(() => {
    const getHandle = async () => {
      const storage = navigator.storage as unknown as {
        getDirectory(): Promise<{
          getFileHandle(
            name: string,
            options: { create: true },
          ): Promise<{
            readonly name: string;
            getFile(): Promise<File>;
          }>;
        }>;
      };
      const directory = await storage.getDirectory();
      return directory.getFileHandle('cross-reload.freetoshop', { create: true });
    };
    Object.defineProperty(window, 'showSaveFilePicker', {
      configurable: true,
      value: async () => {
        localStorage.setItem(
          'freetoshop.e2e.savePickerCalls',
          String(Number(localStorage.getItem('freetoshop.e2e.savePickerCalls') ?? '0') + 1),
        );
        return getHandle();
      },
    });
    Object.defineProperty(window, 'showOpenFilePicker', {
      configurable: true,
      value: async () => [await getHandle()],
    });
  });
  await page.reload();

  await page.getByRole('button', { name: 'Create document' }).click();
  await page.keyboard.press('Control+Shift+s');
  await expect(page.getByText('Cross-reload.freetoshop saved to your device.')).toBeVisible();
  await expect(page.getByText('Saved locally')).toBeVisible();
  await expect
    .poll(() => page.evaluate(() => localStorage.getItem('freetoshop.e2e.savePickerCalls')))
    .toBe('1');

  await page.reload();
  await expect(page.getByText('Recovered your last local document.')).toBeVisible();
  await expect
    .poll(() =>
      page.evaluate(async () => {
        const documentId = localStorage.getItem('freetoshop.recentDocument');
        if (!documentId) return null;
        const database = await new Promise<IDBDatabase>((resolve, reject) => {
          const request = indexedDB.open('freetoshop-native-file-handles', 1);
          request.onsuccess = () => resolve(request.result);
          request.onerror = () => reject(request.error);
        });
        try {
          const stored = await new Promise<unknown>((resolve, reject) => {
            const request = database.transaction('handles').objectStore('handles').get(documentId);
            request.onsuccess = () => resolve(request.result);
            request.onerror = () => reject(request.error);
          });
          const handle = (stored as { handle?: unknown } | undefined)?.handle;
          return handle && typeof handle === 'object'
            ? {
                name: (handle as { name?: unknown }).name,
                getFile: typeof (handle as { getFile?: unknown }).getFile,
                createWritable: typeof (handle as { createWritable?: unknown }).createWritable,
              }
            : null;
        } finally {
          database.close();
        }
      }),
    )
    .toEqual({
      name: 'cross-reload.freetoshop',
      getFile: 'function',
      createWritable: 'function',
    });
  await page.getByRole('button', { name: 'Add pixel layer' }).click();
  await page.keyboard.press('Control+s');
  await expect(page.getByText('Cross-reload.freetoshop saved to your device.')).toBeVisible({
    timeout: 15_000,
  });
  expect(await page.evaluate(() => localStorage.getItem('freetoshop.e2e.savePickerCalls'))).toBe(
    '1',
  );

  await page.getByRole('button', { name: 'New', exact: true }).click();
  await page.keyboard.press('Control+o');
  await expect(
    page.getByText('Cross-reload.freetoshop opened as an editable FreetoShop document.'),
  ).toBeVisible();
  await expect(page.getByRole('button', { name: 'Layer 2', exact: true })).toBeVisible();
});

test('creates, edits, undoes, redoes, and restores a local document', async ({ page }) => {
  await expect(
    page.getByRole('heading', { name: 'Your canvas stays on your device.' }),
  ).toBeVisible();

  await page.getByRole('button', { name: 'Create document' }).click();
  await expect(page.getByLabel('Untitled artwork canvas')).toBeVisible();
  await expect(page.getByText('Layers · 1')).toBeVisible();

  await page.getByRole('button', { name: 'Add pixel layer' }).click();
  await expect(page.getByRole('button', { name: 'Layer 2', exact: true })).toBeVisible();
  await expect(page.getByText('Layers · 2')).toBeVisible();

  await page.getByRole('button', { name: 'Undo' }).click();
  await expect(page.getByRole('button', { name: 'Layer 2', exact: true })).toHaveCount(0);
  await page.getByRole('button', { name: 'Redo' }).click();
  await expect(page.getByRole('button', { name: 'Layer 2', exact: true })).toBeVisible();

  await expect(page.getByText('Saved locally')).toBeVisible();
  await page.reload();
  await expect(page.getByLabel('Untitled artwork canvas')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Layer 2', exact: true })).toBeVisible();
});

test('creates, edits, exports, undoes, and recovers editable shape layers', async ({ page }) => {
  test.setTimeout(60_000);
  await page.getByRole('button', { name: 'Create document' }).click();
  await openMoreLayerActions(page);
  await page.getByLabel('Add shape layer').selectOption('rectangle');
  await expect(page.getByText('Rectangle shape layer added.')).toBeVisible();
  await expectSelectedLayerName(page, /Rectangle.*rectangle/);
  await page.getByLabel('Shape fill color').fill('#ff0000');
  expect(await exportedPixel(page, 512, 384)).toEqual([255, 0, 0, 255]);

  await page
    .getByRole('region', { name: /Layer effects/ })
    .getByLabel('Add layer effect')
    .selectOption('color-overlay');
  await page.getByLabel('color-overlay color').fill('#0000ff');
  expect(await exportedPixel(page, 512, 384)).toEqual([0, 0, 255, 255]);
  await page.getByLabel('Enable color overlay').uncheck();
  expect(await exportedPixel(page, 512, 384)).toEqual([255, 0, 0, 255]);
  await page.getByRole('button', { name: 'Undo' }).click();
  expect(await exportedPixel(page, 512, 384)).toEqual([0, 0, 255, 255]);
  await page
    .getByRole('region', { name: /Layer effects/ })
    .getByLabel('Add layer effect')
    .selectOption('stroke');
  await page.getByLabel('stroke color').fill('#00ff00');
  await page.getByLabel('Stroke effect size').fill('6');
  expectPixelNear(await exportedPixel(page, 390, 384), [0, 255, 0, 255], 1);
  await page
    .getByRole('region', { name: /Layer effects/ })
    .getByLabel('Add layer effect')
    .selectOption('drop-shadow');
  await page.getByLabel('Drop shadow offsetX').fill('12');
  await page.getByLabel('Drop shadow offsetY').fill('12');
  await page.getByLabel('Drop shadow blur').fill('0');
  await page.getByLabel('drop-shadow opacity').fill('100');
  expect(await exportedPixel(page, 638, 470)).toEqual([0, 0, 0, 255]);
  await expectSelectedLayerName(page, /Rectangle.*fx 3/);

  await page.keyboard.press('ControlOrMeta+T');
  await expect(page.getByRole('group', { name: 'Transform controls for Rectangle' })).toBeVisible();
  await page.getByRole('button', { name: 'Rotate layer' }).focus();
  await page.getByRole('button', { name: 'Rotate layer' }).press('Shift+ArrowRight');
  await expect(page.getByText('Layer rotated 15 degrees.')).toBeVisible();
  await page.getByRole('button', { name: 'Undo' }).click();
  expect(await exportedPixel(page, 512, 384)).toEqual([0, 0, 255, 255]);

  await page.getByLabel('Shape type').selectOption('ellipse');
  await expectSelectedLayerName(page, /Rectangle.*ellipse/);
  await page.getByRole('button', { name: 'Undo' }).click();
  await expectSelectedLayerName(page, /Rectangle.*rectangle/);
  expect(await exportedPixel(page, 512, 384)).toEqual([0, 0, 255, 255]);

  await expect(page.getByText('Saved locally')).toBeVisible();
  await page.reload();
  await expect(page.getByText('Recovered your last local document.')).toBeVisible();
  await expectSelectedLayerName(page, /Rectangle.*rectangle.*fx 3/);
  expect(await exportedPixel(page, 512, 384)).toEqual([0, 0, 255, 255]);
});

test('draws, curves, exports, undoes, and recovers editable vector paths', async ({ page }) => {
  await page.getByLabel('Open image file').setInputFiles({
    name: 'path-base.png',
    mimeType: 'image/png',
    buffer: await makePng(page, [0, 0, 0, 0]),
  });
  await page.getByRole('button', { name: 'Pen tool' }).click();
  for (const [x, y] of [
    [5, 5],
    [27, 5],
    [16, 27],
  ] as const) {
    const point = await documentPoint(page, x, y);
    await page.mouse.click(point.x, point.y);
  }
  await expect(page.getByText('3 path anchors. Click to add')).toBeVisible();
  await page.getByRole('button', { name: 'Close path' }).click();
  await expect(page.getByText('Closed vector path added.')).toBeVisible();
  await expectSelectedLayerName(page, /Closed path.*path/);
  await expect(page.getByText('Path commands · 4')).toBeVisible();
  expectPixelNear(await exportedPixel(page, 16, 10), [53, 210, 154, 96]);

  await page.getByLabel('Shape fill color').fill('#ff0000');
  expect(await exportedPixel(page, 16, 10)).toEqual([255, 0, 0, 255]);
  await page.getByRole('button', { name: 'Convert line to curve' }).first().click();
  await expect(page.getByLabel('Path command 2 Control 1 Y')).toBeVisible();
  await page.getByRole('button', { name: 'Undo' }).click();
  await expect(page.getByLabel('Path command 2 Control 1 Y')).toHaveCount(0);
  await page.getByRole('button', { name: 'Redo' }).click();
  await page.getByLabel('Path command 2 Control 1 Y').fill('25');
  const controlHandle = page.getByRole('button', {
    name: 'Path command 2 control 1 handle',
  });
  await expect(controlHandle).toBeVisible();
  const handleBox = await controlHandle.boundingBox();
  if (!handleBox) throw new Error('Path control handle bounds unavailable');
  const handleTarget = await documentPoint(page, 12, 12);
  await page.mouse.move(handleBox.x + handleBox.width / 2, handleBox.y + handleBox.height / 2);
  await page.mouse.down();
  await page.mouse.move(handleTarget.x, handleTarget.y, { steps: 4 });
  await page.mouse.up();
  await expect(page.getByLabel('Path command 2 Control 1 Y')).toHaveValue('10.5');
  await page.getByRole('button', { name: 'Undo' }).click();
  await expect(page.getByLabel('Path command 2 Control 1 Y')).toHaveValue('25');
  await page.getByRole('button', { name: 'Redo' }).click();
  await expect(page.getByLabel('Path command 2 Control 1 Y')).toHaveValue('10.5');
  await expect(page.getByText('Saved locally')).toBeVisible();

  await page.reload();
  await expect(page.getByText('Recovered your last local document.')).toBeVisible();
  await expectSelectedLayerName(page, /Closed path.*path/);
  await expect(page.getByText('Path commands · 4')).toBeVisible();
  await expect(page.getByLabel('Path command 2 Control 1 Y')).toHaveValue('10.5');
  expect((await exportedPixel(page, 16, 10))[3]).toBeGreaterThan(0);
});

test('creates, styles, exports, and recovers editable text layers', async ({ page }) => {
  await page.getByRole('button', { name: 'Create document' }).click();
  await openMoreLayerActions(page);
  await page.getByRole('button', { name: 'Add text layer' }).click();
  await expect(page.getByText('Editable text layer added.')).toBeVisible();
  await expectSelectedLayerName(page, /Text.*editable text/);
  await page.getByLabel('Text content').fill('FreetoShop\nParagraph');
  await page.getByLabel('Text font size').fill('72');
  await page.getByLabel('Text color').fill('#00ff00');
  await page.getByLabel('Text alignment').selectOption('center');
  await page.getByRole('checkbox', { name: 'Bold', exact: true }).check();
  const content = page.getByLabel('Text content');
  await content.press('Control+Home');
  await page.keyboard.down('Shift');
  for (let index = 0; index < 4; index += 1) await page.keyboard.press('ArrowRight');
  await page.keyboard.up('Shift');
  await expect(page.getByLabel('Text character selection')).toContainText('0–4');
  await page.getByLabel('Range font family').selectOption('Georgia');
  await page.getByLabel('Range font size').fill('96');
  await page.getByLabel('Range color').fill('#ff0000');
  await page.getByLabel('Range bold').uncheck();
  await page.getByRole('button', { name: 'Apply character style' }).click();
  await expect(page.getByLabel('Text character style ranges')).toHaveText(
    '1 saved character style range',
  );
  await content.press('Control+End');
  await expect(page.getByLabel('Text paragraph selection')).toContainText('11–20');
  await page.getByLabel('Paragraph leading').fill('0.6');
  await page.getByLabel('Paragraph alignment').selectOption('right');
  await page.getByRole('button', { name: 'Apply paragraph style' }).click();
  await expect(page.getByLabel('Text paragraph style ranges')).toHaveText(
    '1 saved paragraph style range',
  );
  const colors = await exportedTextColorCounts(page);
  expect(colors.red).toBeGreaterThan(20);
  expect(colors.green).toBeGreaterThan(20);
  expect(await exportedOpaquePixelCount(page)).toBeGreaterThan(500);

  await expect(page.getByText('Saved locally')).toBeVisible();
  await page.reload();
  await expect(page.getByText('Recovered your last local document.')).toBeVisible();
  await expect(page.getByLabel('Text content')).toHaveValue('FreetoShop\nParagraph');
  await expect(page.getByLabel('Text font size')).toHaveValue('72');
  await expect(page.getByLabel('Text alignment')).toHaveValue('center');
  await expect(page.getByRole('checkbox', { name: 'Bold', exact: true })).toBeChecked();
  await expect(page.getByLabel('Text character style ranges')).toHaveText(
    '1 saved character style range',
  );
  await expect(page.getByLabel('Text paragraph style ranges')).toHaveText(
    '1 saved paragraph style range',
  );
  const recoveredColors = await exportedTextColorCounts(page);
  expect(recoveredColors.red).toBeGreaterThan(20);
  expect(recoveredColors.green).toBeGreaterThan(20);
  expect(await exportedOpaquePixelCount(page)).toBeGreaterThan(500);
});

test('loads a local OpenType font and renders HarfBuzz-shaped text', async ({ page }) => {
  test.setTimeout(60_000);
  await page.getByRole('button', { name: 'Create document' }).click();
  await openMoreLayerActions(page);
  await page.getByRole('button', { name: 'Add text layer' }).click();
  await page.getByLabel('Load local font file').setInputFiles({
    name: 'DejaVuSerif.ttf',
    mimeType: 'font/ttf',
    buffer: await readFile('node_modules/dejavu-fonts-ttf/ttf/DejaVuSerif.ttf'),
  });
  await expect(
    page.getByText(/DejaVu Serif .*weight 400.*professional OpenType shaping/u),
  ).toBeVisible();
  await page.getByLabel('Load local font file').setInputFiles({
    name: 'DejaVuSans.ttf',
    mimeType: 'font/ttf',
    buffer: await readFile('node_modules/dejavu-fonts-ttf/ttf/DejaVuSans.ttf'),
  });
  await expect(
    page.getByText(/DejaVu Sans .*weight 400.*professional OpenType shaping/u),
  ).toBeVisible();
  await expect(page.getByLabel('Text font family')).toHaveValue('DejaVu Sans');
  await expect(page.getByLabel('Text shaping status')).toHaveText(
    'Professional OpenType shaping active for every face',
  );
  await page.getByLabel('Text font family').selectOption('DejaVu Serif');
  await page.getByLabel('Text content').fill('A\u02ceB');
  await page.getByLabel('Text font size').fill('96');
  await page.evaluate(() => {
    const state = globalThis as typeof globalThis & {
      __freetoshopFillTextCalls?: number;
    };
    state.__freetoshopFillTextCalls = 0;
    const prototypes: Array<{ fillText: () => void }> = [];
    const canvasContext = document.createElement('canvas').getContext('2d');
    if (canvasContext)
      prototypes.push(Object.getPrototypeOf(canvasContext) as { fillText: () => void });
    if (typeof OffscreenCanvas !== 'undefined') {
      const offscreenContext = new OffscreenCanvas(1, 1).getContext('2d');
      if (offscreenContext)
        prototypes.push(Object.getPrototypeOf(offscreenContext) as { fillText: () => void });
    }
    for (const prototype of new Set(prototypes)) {
      prototype.fillText = () => {
        state.__freetoshopFillTextCalls = (state.__freetoshopFillTextCalls ?? 0) + 1;
      };
    }
  });
  expect(await exportedOpaquePixelCount(page)).toBeGreaterThan(100);
  expect(
    await page.evaluate(
      () =>
        (
          globalThis as typeof globalThis & {
            __freetoshopFillTextCalls?: number;
          }
        ).__freetoshopFillTextCalls ?? 0,
    ),
  ).toBe(0);

  await page.getByLabel('Text font family').selectOption('DejaVu Sans');
  await page.getByRole('checkbox', { name: 'Bold', exact: true }).check();
  await expect(page.getByLabel('Text shaping status')).toContainText('System-font fallback');
  await page.getByLabel('Load local font file').setInputFiles({
    name: 'DejaVuSans-Bold.ttf',
    mimeType: 'font/ttf',
    buffer: await readFile('node_modules/dejavu-fonts-ttf/ttf/DejaVuSans-Bold.ttf'),
  });
  await expect(
    page.getByText(/DejaVu Sans Bold loaded locally as weight 700.*professional OpenType shaping/u),
  ).toBeVisible();
  await expect(page.getByLabel('Text shaping status')).toHaveText(
    'Professional OpenType shaping active for every face',
  );
  await page.getByRole('checkbox', { name: 'Bold', exact: true }).uncheck();

  await page.getByLabel('Text content').fill('office مرحبا');
  await page.getByLabel('Text font size').fill('72');
  await page.getByLabel('Text color').fill('#00ff00');
  const content = page.getByLabel('Text content');
  await content.press('Control+Home');
  await page.keyboard.down('Shift');
  for (let index = 0; index < 6; index += 1) await page.keyboard.press('ArrowRight');
  await page.keyboard.up('Shift');
  await page.getByLabel('Range font family').selectOption('DejaVu Sans');
  await page.getByLabel('Range font size').fill('96');
  await page.getByLabel('Range tracking').fill('100');
  await page.getByLabel('Range color').fill('#ff0000');
  await page.getByLabel('Range bold').check();
  await page.getByRole('button', { name: 'Apply character style' }).click();
  await expect(page.getByLabel('Text character style ranges')).toHaveText(
    '1 saved character style range',
  );
  await expect(page.getByLabel('Text shaping status')).toHaveText(
    'Professional OpenType shaping active for every face',
  );
  const colors = await exportedTextColorCounts(page);
  expect(colors.red).toBeGreaterThan(100);
  expect(colors.green).toBeGreaterThan(100);
  expect(
    await page.evaluate(
      () =>
        (
          globalThis as typeof globalThis & {
            __freetoshopFillTextCalls?: number;
          }
        ).__freetoshopFillTextCalls ?? 0,
    ),
  ).toBe(0);
  expect(await exportedOpaquePixelCount(page)).toBeGreaterThan(1_000);

  await expect(page.getByText('Saved locally')).toBeVisible();
  await page.reload();
  await expect(page.getByText('Recovered your last local document.')).toBeVisible();
  await expect(page.getByLabel('Text font family')).toHaveValue('DejaVu Sans');
  await expect(page.getByLabel('Text shaping status')).toContainText('System-font fallback');
  await expect(page.getByLabel('Text character style ranges')).toHaveText(
    '1 saved character style range',
  );
});

test('converts, replaces, undoes, and recovers embedded smart objects', async ({ page }) => {
  test.setTimeout(60_000);
  await page.getByRole('button', { name: 'Create document' }).click();
  await openMoreLayerActions(page);
  await page.getByLabel('Add shape layer').selectOption('rectangle');
  await page.getByLabel('Shape fill color').fill('#ff0000');
  await openMoreLayerActions(page);
  await page.getByRole('button', { name: 'Convert to smart object' }).click();
  await expect(page.getByText('Rectangle converted to a smart object.')).toBeVisible();
  await expectSelectedLayerName(page, /Rectangle.*smart object/);
  await expect(page.getByText('240 × 160 px · 1 source layers')).toBeVisible();
  expect(await exportedPixel(page, 512, 384)).toEqual([255, 0, 0, 255]);

  await page.getByRole('button', { name: 'Undo' }).click();
  await expectSelectedLayerName(page, /Rectangle.*rectangle/);
  await page.getByRole('button', { name: 'Redo' }).click();
  await expectSelectedLayerName(page, /Rectangle.*smart object/);

  await page.getByLabel('Replace smart object contents file').setInputFiles({
    name: 'replacement.png',
    mimeType: 'image/png',
    buffer: await makePng(page, [0, 0, 255, 255]),
  });
  await expect(page.getByText('Rectangle contents replaced with replacement.png.')).toBeVisible();
  await expect(page.getByText('32 × 32 px · 1 source layers')).toBeVisible();
  expect(await exportedPixel(page, 400, 312)).toEqual([0, 0, 255, 255]);
  expect(await exportedPixel(page, 512, 384)).toEqual([0, 0, 0, 0]);

  await page.getByRole('button', { name: 'Undo' }).click();
  expect(await exportedPixel(page, 512, 384)).toEqual([255, 0, 0, 255]);
  await page.getByRole('button', { name: 'Redo' }).click();
  expect(await exportedPixel(page, 400, 312)).toEqual([0, 0, 255, 255]);

  await page.getByRole('button', { name: 'Edit contents' }).click();
  await expect(page.getByText('Editing: replacement.png contents')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Save smart object' })).toBeVisible();
  await openMoreLayerActions(page);
  await page.getByLabel('Add shape layer').selectOption('rectangle');
  await page.getByLabel('Shape fill color').fill('#ffff00');
  await page.getByRole('button', { name: 'Save smart object' }).click();
  await expect(page.getByText('Smart object contents saved to the parent document.')).toBeVisible();
  await expect(page.getByText('32 × 32 px · 2 source layers')).toBeVisible();
  expect(await exportedPixel(page, 400, 312)).toEqual([255, 255, 0, 255]);

  await page.getByRole('button', { name: 'Undo' }).click();
  expect(await exportedPixel(page, 400, 312)).toEqual([0, 0, 255, 255]);
  await page.getByRole('button', { name: 'Redo' }).click();
  expect(await exportedPixel(page, 400, 312)).toEqual([255, 255, 0, 255]);

  await page.getByLabel('Add smart filter').selectOption('invert');
  expect(await exportedPixel(page, 400, 312)).toEqual([0, 0, 255, 255]);
  await page.getByLabel('Enable smart invert').uncheck();
  expect(await exportedPixel(page, 400, 312)).toEqual([255, 255, 0, 255]);
  await page.getByRole('button', { name: 'Undo' }).click();
  expect(await exportedPixel(page, 400, 312)).toEqual([0, 0, 255, 255]);
  await page.getByLabel('Add smart filter').selectOption('gaussian-blur');
  await page.getByLabel('Smart Gaussian Blur radius').fill('2');
  await page.getByLabel('Add smart filter').selectOption('brightness-contrast');
  await page.getByLabel('Add smart filter').selectOption('grayscale');
  await expectSelectedLayerName(page, /smart object.*smart filters 4/);
  expect(await exportedPixel(page, 400, 312)).toEqual([18, 18, 18, 255]);
  await page.getByRole('button', { name: 'Undo' }).click();
  expect(await exportedPixel(page, 400, 312)).toEqual([0, 0, 255, 255]);
  await page.getByRole('button', { name: 'Redo' }).click();
  expect(await exportedPixel(page, 400, 312)).toEqual([18, 18, 18, 255]);

  await page.getByRole('button', { name: 'Edit contents' }).click();
  await page.getByLabel('Shape fill color').fill('#ff0000');
  await page.getByRole('button', { name: 'Cancel smart object edit' }).click();
  await expect(page.getByText('Smart object content edits cancelled.')).toBeVisible();
  expect(await exportedPixel(page, 400, 312)).toEqual([18, 18, 18, 255]);

  await expect(page.getByText('Saved locally')).toBeVisible();
  await page.reload();
  await expect(page.getByText('Recovered your last local document.')).toBeVisible();
  await expectSelectedLayerName(page, /smart object/);
  await expect(page.getByText('replacement.png contents')).toBeVisible();
  await expect(page.getByText('32 × 32 px · 2 source layers')).toBeVisible();
  await expectSelectedLayerName(page, /smart object.*smart filters 4/);
  expect(await exportedPixel(page, 400, 312)).toEqual([18, 18, 18, 255]);
});

test('multiselects, groups, duplicates, ungroups, and recovers layer trees', async ({ page }) => {
  await page.getByRole('button', { name: 'Create document' }).click();
  await page.getByRole('button', { name: 'Add pixel layer' }).click();
  await page.getByRole('button', { name: 'Add pixel layer' }).click();

  const layer2 = page.getByRole('button', { name: 'Layer 2', exact: true });
  const layer3 = page.getByRole('button', { name: 'Layer 3', exact: true });
  await layer2.click();
  await layer3.click({ modifiers: ['Control'] });
  await expect(layer2).toHaveAttribute('aria-pressed', 'true');
  await expect(layer3).toHaveAttribute('aria-pressed', 'true');

  await page.getByRole('button', { name: 'Group selected layers' }).click();
  await expect(page.getByText('Selected layers grouped.')).toBeVisible();
  const groupRow = page.getByRole('button', { name: /Group of 2.*2 layers/ });
  await expect(groupRow).toBeVisible();
  const groupBox = await groupRow.boundingBox();
  const childBox = await layer3.boundingBox();
  if (!groupBox || !childBox) throw new Error('Layer tree bounds unavailable');
  expect(groupBox.y).toBeLessThan(childBox.y);
  await expect(page.getByText('Layers · 4')).toBeVisible();

  await openMoreLayerActions(page);
  await page.getByRole('button', { name: 'Duplicate selected layer' }).click();
  await expect(page.getByText('Group of 2 duplicated.')).toBeVisible();
  await expect(page.getByRole('button', { name: /Group of 2 copy.*2 layers/ })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Layer 2', exact: true })).toHaveCount(2);
  await expect(page.getByText('Layers · 7')).toBeVisible();

  await openMoreLayerActions(page);
  await page.getByRole('button', { name: 'Ungroup selected layer' }).click();
  await expect(page.getByText('Layer group released.')).toBeVisible();
  await expect(page.getByRole('button', { name: /Group of 2 copy.*2 layers/ })).toHaveCount(0);
  await expect(page.getByText('Layers · 6')).toBeVisible();

  await openHistoryPanel(page);
  await page.getByRole('button', { name: 'History step Ungroup layers' }).click();
  await page.getByRole('tab', { name: 'Layers' }).click();
  await expect(page.getByRole('button', { name: /Group of 2 copy.*2 layers/ })).toBeVisible();
  await openHistoryPanel(page);
  await page.getByRole('button', { name: 'Future history step Ungroup layers' }).click();
  await page.getByRole('tab', { name: 'Layers' }).click();
  await expect(page.getByRole('button', { name: /Group of 2 copy.*2 layers/ })).toHaveCount(0);

  await expect(page.getByText('Saved locally')).toBeVisible();
  await page.reload();
  await expect(page.getByText('Recovered your last local document.')).toBeVisible();
  await expect(
    page.getByRole('button', { name: 'Group of 2 · 2 layers', exact: true }),
  ).toBeVisible();
  await expect(page.getByText('Layers · 6')).toBeVisible();
});

test('merges selected root layers and flattens visible pixels with undo and recovery', async ({
  page,
}) => {
  await page.getByLabel('Open image file').setInputFiles({
    name: 'merge.png',
    mimeType: 'image/png',
    buffer: await makePng(page, [40, 80, 120, 255]),
  });
  await page.getByRole('button', { name: 'New Adjustment Layer' }).click();
  await page.getByRole('menuitem', { name: 'Invert', exact: true }).click();
  await page.getByRole('button', { name: 'merge.png', exact: true }).click();
  await page.getByRole('button', { name: /Invert.*adjustment/ }).click({ modifiers: ['Control'] });
  await openMoreLayerActions(page);
  await page.getByRole('button', { name: 'Merge selected' }).click();
  await expect(page.getByText('Selected layers merged.')).toBeVisible();
  await expect(page.getByText('Layers · 1')).toBeVisible();
  expect(await exportedPixel(page, 8, 8)).toEqual([215, 175, 135, 255]);

  await page.getByRole('button', { name: 'Undo' }).click();
  await expect(page.getByText('Layers · 2')).toBeVisible();
  await expect(page.getByRole('button', { name: /Invert.*adjustment/ })).toBeVisible();
  await openMoreLayerActions(page);
  await page.getByRole('button', { name: 'Flatten image' }).click();
  await expect(page.getByText('Visible document content flattened.')).toBeVisible();
  await expect(page.getByText('Layers · 1')).toBeVisible();
  expect(await exportedPixel(page, 8, 8)).toEqual([215, 175, 135, 255]);

  await page.getByRole('button', { name: 'Undo' }).click();
  await expect(page.getByText('Layers · 2')).toBeVisible();
  await page.getByRole('button', { name: 'Redo' }).click();
  await expect(page.getByText('Layers · 1')).toBeVisible();
  await expect(page.getByText('Saved locally')).toBeVisible();
  await page.reload();
  await expect(page.getByText('Recovered your last local document.')).toBeVisible();
  await expect(page.getByText('Layers · 1')).toBeVisible();
  expect(await exportedPixel(page, 8, 8)).toEqual([215, 175, 135, 255]);
});

test('exports the flattened document as a PNG', async ({ page }) => {
  await page.getByRole('button', { name: 'Create document' }).click();
  const downloadPromise = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Export PNG' }).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toBe('Untitled-artwork.png');
  await expect(page.getByText('PNG export completed.')).toBeVisible();
});

test('paints and erases with one history entry per gesture', async ({ page }) => {
  await page.getByLabel('Open image file').setInputFiles({
    name: 'transparent.png',
    mimeType: 'image/png',
    buffer: await makePng(page, [0, 0, 0, 0]),
  });
  await page.getByRole('button', { name: 'Brush', exact: true }).click();
  await page.getByLabel('Brush size').fill('6');
  await page.getByLabel('Brush color').fill('#646464');
  const start = await documentPoint(page, 6, 8);
  const end = await documentPoint(page, 14, 8);
  await page.mouse.move(start.x, start.y);
  await page.mouse.down();
  await page.mouse.move(end.x, end.y, { steps: 5 });
  await page.mouse.up();
  await expect(page.locator('canvas')).toHaveAttribute('data-render-mode', 'dirty');
  await expect(page.locator('canvas')).toHaveAttribute('data-dirty-region-count', '1');
  expect(await exportedPixel(page, 10, 8)).toEqual([100, 100, 100, 255]);

  await page.getByRole('button', { name: 'Undo' }).click();
  expect(await exportedPixel(page, 10, 8)).toEqual([0, 0, 0, 0]);
  await expect(page.getByRole('button', { name: 'Undo' })).toBeDisabled();
  await page.getByRole('button', { name: 'Redo' }).click();

  await page.getByRole('button', { name: 'Eraser', exact: true }).click();
  const center = await documentPoint(page, 10, 8);
  await page.mouse.click(center.x, center.y);
  expect((await exportedPixel(page, 10, 8))[3]).toBe(0);
  await page.getByRole('button', { name: 'Undo' }).click();
  expect(await exportedPixel(page, 10, 8)).toEqual([100, 100, 100, 255]);
});

test('constrains brightness to a rectangular selection and moves the layer', async ({ page }) => {
  await page.getByLabel('Open image file').setInputFiles({
    name: 'gray.png',
    mimeType: 'image/png',
    buffer: await makePng(page, [100, 100, 100, 255]),
  });
  await page.getByRole('button', { name: 'Fit' }).click();
  await page.getByRole('button', { name: 'Rectangular select' }).click();
  const selectionStart = await documentPoint(page, 8, 8);
  const selectionEnd = await documentPoint(page, 24, 24);
  await page.mouse.move(selectionStart.x, selectionStart.y);
  await page.mouse.down();
  await page.mouse.move(selectionEnd.x, selectionEnd.y);
  await page.mouse.up();
  await expect(page.getByText('Selection: 8, 8 · 17 × 17')).toBeVisible();

  await page.getByLabel('Brightness').fill('40');
  await page.getByRole('button', { name: 'Apply brightness' }).click();
  expect(await exportedPixel(page, 12, 12)).toEqual([140, 140, 140, 255]);
  expect(await exportedPixel(page, 4, 4)).toEqual([100, 100, 100, 255]);

  await page.getByRole('button', { name: 'Move layer' }).click();
  const moveStart = await documentPoint(page, 12, 12);
  const moveEnd = await documentPoint(page, 16, 15);
  await page.mouse.move(moveStart.x, moveStart.y);
  await page.mouse.down();
  await page.mouse.move(moveEnd.x, moveEnd.y);
  await page.mouse.up();
  await expect(page.getByRole('status', { name: 'Layer position' })).toHaveText('4, 3');
  expect(await exportedPixel(page, 2, 2)).toEqual([0, 0, 0, 0]);
  expect(await exportedPixel(page, 16, 15)).toEqual([140, 140, 140, 255]);
});

test('applies full-layer brightness as one undoable tiled edit', async ({ page }) => {
  await page.getByLabel('Open image file').setInputFiles({
    name: 'full-brightness.png',
    mimeType: 'image/png',
    buffer: await makePng(page, [90, 110, 130, 200]),
  });
  await page.getByRole('slider', { name: 'Brightness', exact: true }).fill('35');
  await page.getByRole('button', { name: 'Apply brightness' }).click();
  expectPixelNear(await exportedPixel(page, 12, 12), [125, 145, 165, 200], 5);

  await page.getByRole('button', { name: 'Undo' }).click();
  expectPixelNear(await exportedPixel(page, 12, 12), [90, 110, 130, 200], 5);
  await page.getByRole('button', { name: 'Redo' }).click();
  expectPixelNear(await exportedPixel(page, 12, 12), [125, 145, 165, 200], 5);
});

test('creates and refines ellipse, lasso, and magic-wand selections', async ({ page }) => {
  await page.getByLabel('Open image file').setInputFiles({
    name: 'selection-tools.png',
    mimeType: 'image/png',
    buffer: await makeQuadrantPng(page),
  });
  await page.getByRole('button', { name: 'Fit' }).click();

  await page.keyboard.press('Shift+M');
  // Stay inside the target boundary pixels. Exact CSS-pixel boundaries round
  // differently between Chromium, Firefox, and WebKit at fractional zooms.
  const ellipseStart = await documentCoordinate(page, 8.25, 8.25);
  const ellipseEnd = await documentCoordinate(page, 23.75, 23.75);
  await page.mouse.move(ellipseStart.x, ellipseStart.y);
  await page.mouse.down();
  await page.mouse.move(ellipseEnd.x, ellipseEnd.y, { steps: 5 });
  await page.mouse.up();
  await expect(page.getByText('Selection: 8, 8 · 16 × 16')).toBeVisible();
  await page.getByLabel('Selection refinement radius').fill('2');
  await page.getByRole('button', { name: 'Expand', exact: true }).click();
  await expect(page.getByText('Selection: 6, 6 · 20 × 20')).toBeVisible();
  await openHistoryPanel(page);
  await page.getByRole('button', { name: 'History step Expand selection' }).click();
  await expect(page.getByText('Selection: 8, 8 · 16 × 16')).toBeVisible();

  await page.getByRole('button', { name: 'Freehand lasso' }).click();
  const lassoPoints = await Promise.all([
    documentCoordinate(page, 4, 4),
    documentCoordinate(page, 20, 4),
    documentCoordinate(page, 12, 20),
    documentCoordinate(page, 4, 4),
  ]);
  const first = lassoPoints[0];
  if (!first) throw new Error('Lasso start unavailable');
  await page.mouse.move(first.x, first.y);
  await page.mouse.down();
  for (const point of lassoPoints.slice(1)) await page.mouse.move(point.x, point.y, { steps: 5 });
  await page.mouse.up();
  await expect(page.getByText('Selection: 4, 4 · 16 × 16')).toBeVisible();

  await page.getByRole('button', { name: 'Magic Wand' }).click();
  await page.getByLabel('Magic Wand tolerance').fill('0');
  const red = await documentPoint(page, 4, 4);
  await page.mouse.click(red.x, red.y);
  await expect(page.getByText('Selection: 0, 0 · 16 × 16')).toBeVisible();
  await page.getByLabel('Selection mode').selectOption('add');
  const green = await documentPoint(page, 20, 4);
  await page.mouse.click(green.x, green.y);
  await expect(page.getByText('Selection: 0, 0 · 32 × 16')).toBeVisible();

  await page.getByLabel('Selection refinement radius').fill('2');
  await page.getByRole('button', { name: 'Feather' }).click();
  await expect(page.getByText('Selection: 0, 0 · 32 × 18')).toBeVisible();
  await page.getByRole('tab', { name: 'Channels' }).click();
  await page.getByRole('button', { name: 'Save selection as channel' }).click();
  await expect(page.getByRole('heading', { name: 'Channels · 1' })).toBeVisible();
});

test('samples point and averaged colors into foreground and background with Eyedropper', async ({
  page,
}) => {
  await page.getByLabel('Open image file').setInputFiles({
    name: 'eyedropper-quadrants.png',
    mimeType: 'image/png',
    buffer: await makeQuadrantPng(page),
  });
  await page.getByRole('button', { name: 'Fit' }).click();

  await page.keyboard.press('i');
  const canvas = page.locator('canvas[aria-label$=" canvas"]');
  await expect(canvas).toHaveAttribute('data-tool', 'eyedropper');
  await expect(page.getByLabel('Eyedropper sample size')).toHaveValue('1');
  await expect(page.getByLabel('Eyedropper sample source')).toHaveValue('all-layers');

  const red = await documentPoint(page, 8, 8);
  await page.mouse.click(red.x, red.y);
  await expect(page.locator('input[aria-label="Foreground color"]')).toHaveValue('#c80000');
  await expect(page.getByTestId('eyedropper-result')).toHaveText('#C80000 to foreground');

  const green = await documentPoint(page, 24, 8);
  await page.mouse.move(red.x, red.y);
  await page.mouse.down();
  await page.mouse.move(green.x, green.y, { steps: 4 });
  await page.mouse.up();
  await expect(page.locator('input[aria-label="Foreground color"]')).toHaveValue('#00b400');

  const blue = await documentPoint(page, 8, 24);
  await page.keyboard.down('Alt');
  await page.mouse.click(blue.x, blue.y);
  await page.keyboard.up('Alt');
  await expect(page.locator('input[aria-label="Background color"]')).toHaveValue('#0000dc');
  await expect(page.getByTestId('eyedropper-result')).toHaveText('#0000DC to background');

  await page.getByLabel('Eyedropper sample source').selectOption('current-layer');
  await page.getByLabel('Eyedropper sample size').selectOption('101');
  const center = await documentPoint(page, 16, 16);
  await page.mouse.click(center.x, center.y);
  await expect(page.locator('input[aria-label="Foreground color"]')).toHaveValue('#695a37');
  await expect(page.getByTestId('eyedropper-result')).toHaveText('#695A37 to foreground');

  await page.reload();
  await expect(canvas).toBeVisible();
  await page.keyboard.press('i');
  await expect(page.getByLabel('Eyedropper sample source')).toHaveValue('current-layer');
  await expect(page.getByLabel('Eyedropper sample size')).toHaveValue('101');
});

test('adds, moves, deletes, undoes, and restores persisted Color Sampler points in Info', async ({
  page,
}) => {
  await page.getByLabel('Open image file').setInputFiles({
    name: 'color-sampler-quadrants.png',
    mimeType: 'image/png',
    buffer: await makeQuadrantPng(page),
  });
  await page.getByRole('button', { name: 'Fit' }).click();

  await page.keyboard.press('i');
  await page.keyboard.press('Shift+I');
  const canvas = page.locator('canvas[aria-label$=" canvas"]');
  await expect(canvas).toHaveAttribute('data-tool', 'color-sampler');
  await expect(page.getByRole('heading', { name: 'Info' })).toBeVisible();
  await expect(page.getByLabel('Tool options', { exact: true })).toContainText('0 / 10 points');

  const red = await documentPoint(page, 8, 8);
  const green = await documentPoint(page, 24, 8);
  const blue = await documentPoint(page, 8, 24);
  await page.mouse.click(red.x, red.y);
  await page.mouse.click(green.x, green.y);

  await expect(canvas).toHaveAttribute('data-color-sampler-count', '2');
  await expect(page.getByTestId('color-sampler-reading-1')).toContainText('RGB200, 0, 0');
  await expect(page.getByTestId('color-sampler-reading-1')).toContainText('HSB0°, 100%, 78%');
  await expect(page.getByTestId('color-sampler-reading-1')).toContainText(
    'CMYK0%, 100%, 100%, 22%',
  );
  await expect(page.getByTestId('color-sampler-reading-1')).toContainText('Lab42, 67, 56');
  await expect(page.getByTestId('color-sampler-reading-2')).toContainText('RGB0, 180, 0');

  await page.mouse.move(red.x, red.y);
  await page.mouse.down();
  await page.mouse.move(blue.x, blue.y, { steps: 4 });
  await page.mouse.up();
  await expect(page.getByTestId('color-sampler-reading-1')).toContainText('RGB0, 0, 220');

  await page.getByRole('button', { name: 'Undo' }).click();
  await expect(page.getByTestId('color-sampler-reading-1')).toContainText('RGB200, 0, 0');

  await page.keyboard.down('Alt');
  await page.mouse.click(green.x, green.y);
  await page.keyboard.up('Alt');
  await expect(canvas).toHaveAttribute('data-color-sampler-count', '1');
  await expect(page.getByTestId('color-sampler-reading-2')).toHaveCount(0);

  await expect(page.getByText('Saved locally')).toBeVisible();
  await page.reload();
  await expect(page.getByText('Recovered your last local document.')).toBeVisible();
  await expect(canvas).toHaveAttribute('data-color-sampler-count', '1');
  await expect(page.getByTestId('color-sampler-reading-1')).toContainText('RGB200, 0, 0');

  await page.getByRole('button', { name: 'Clear all' }).click();
  await expect(canvas).toHaveAttribute('data-color-sampler-count', '0');
  await page.getByRole('button', { name: 'Undo' }).click();
  await expect(canvas).toHaveAttribute('data-color-sampler-count', '1');
});

test('shows exact rendered Histogram statistics for the image and active selection', async ({
  page,
}) => {
  await page.getByLabel('Open image file').setInputFiles({
    name: 'histogram-quadrants.png',
    mimeType: 'image/png',
    buffer: await makeQuadrantPng(page),
  });
  await page.getByRole('menuitem', { name: 'Window', exact: true }).click();
  await page.getByRole('menuitemcheckbox', { name: 'Histogram', exact: true }).click();

  await expect(page.getByRole('heading', { name: 'Histogram' })).toBeVisible();
  await expect(page.getByText('Exact rendered-pixel histogram.')).toBeVisible();
  await expect(page.getByTestId('histogram-pixel-count')).toHaveText('1,024');
  await page.getByLabel('Histogram channel').selectOption('red');
  await expect(page.locator('.histogram-statistics')).toContainText('0–220');
  await page.getByLabel('Histogram level').fill('200');
  await expect(page.getByTestId('histogram-level-count')).toHaveText('256');

  await clickMenuCommand(page, 'Select', 'Color Range…');
  await page.getByLabel('Color Range fuzziness').fill('0');
  const red = await documentPoint(page, 4, 4);
  await page.mouse.click(red.x, red.y);
  await page.getByRole('button', { name: 'OK' }).click();
  await page.getByLabel('Histogram scope').selectOption('selection');

  await expect(page.getByTestId('histogram-pixel-count')).toHaveText('256');
  await expect(page.locator('.histogram-statistics')).toContainText('200–200');
  await expect(page.getByTestId('histogram-level-count')).toHaveText('256');
  await page.getByLabel('Histogram source').selectOption('current-layer');
  await expect(page.getByTestId('histogram-pixel-count')).toHaveText('256');
});

test('selects by color range from sampled canvas color', async ({ page }) => {
  await page.getByLabel('Open image file').setInputFiles({
    name: 'color-range.png',
    mimeType: 'image/png',
    buffer: await makeQuadrantPng(page),
  });
  await page.getByRole('button', { name: 'Fit' }).click();
  await clickMenuCommand(page, 'Select', 'Color Range…');
  await page.getByLabel('Color Range fuzziness').fill('0');
  const red = await documentPoint(page, 4, 4);
  await page.mouse.click(red.x, red.y);
  await expect(page.getByText(/Sampled RGB 200, 0, 0/)).toBeVisible();
  await page.getByRole('button', { name: 'OK' }).click();
  await expect(page.getByText('Selection: 0, 0 · 16 × 16')).toBeVisible();
});

test('paints quick selection over similar colors with undo', async ({ page }) => {
  await page.getByLabel('Open image file').setInputFiles({
    name: 'quick-select.png',
    mimeType: 'image/png',
    buffer: await makeQuadrantPng(page),
  });
  await page.getByRole('button', { name: 'Fit' }).click();
  await page.keyboard.press('w');
  await page.keyboard.press('Shift+W');
  await expect(page.getByLabel('Tool options', { exact: true })).toContainText('Quick');
  const start = await documentPoint(page, 4, 4);
  const end = await documentPoint(page, 12, 12);
  await page.mouse.move(start.x, start.y);
  await page.mouse.down();
  await page.mouse.move(end.x, end.y, { steps: 8 });
  await page.mouse.up();
  await expect(page.getByText(/Selection: \d+, \d+ · \d+ × \d+/)).toBeVisible();
  await openHistoryPanel(page);
  await expect(page.getByRole('button', { name: /^History step Combine selection/ })).toBeVisible();
  await page.getByRole('button', { name: 'Undo' }).click();
  await expect(page.getByText('No selection')).toBeVisible();
});

test('selects an object by dragging a rectangle with tolerance and undo', async ({ page }) => {
  await page.getByLabel('Open image file').setInputFiles({
    name: 'object-select.png',
    mimeType: 'image/png',
    buffer: await makeQuadrantPng(page),
  });
  await page.getByRole('button', { name: 'Fit' }).click();
  await page.keyboard.press('w');
  await page.keyboard.press('Shift+W');
  await page.keyboard.press('Shift+W');
  await expect(page.getByLabel('Tool options', { exact: true })).toContainText('Object');
  await page.getByLabel('Object selection tolerance').fill('0');
  const start = await documentPoint(page, 0, 0);
  const end = await documentPoint(page, 16, 16);
  await page.mouse.move(start.x, start.y);
  await page.mouse.down();
  await page.mouse.move(end.x, end.y, { steps: 6 });
  await page.mouse.up();
  await expect(page.getByText('Selection: 0, 0 · 16 × 16')).toBeVisible();
  await openHistoryPanel(page);
  await expect(page.getByRole('button', { name: /^History step Combine selection/ })).toBeVisible();
  await page.getByRole('button', { name: 'Undo' }).click();
  await expect(page.getByText('No selection')).toBeVisible();
});

test('refines an inverted selection into a persistent complement mask', async ({ page }) => {
  await page.getByLabel('Open image file').setInputFiles({
    name: 'complement-mask.png',
    mimeType: 'image/png',
    buffer: await makePng(page, [200, 80, 20, 255]),
  });
  await page.getByRole('button', { name: 'Fit' }).click();
  await page.getByRole('button', { name: 'Rectangular select' }).click();
  const start = await documentPoint(page, 8, 8);
  const end = await documentPoint(page, 23, 23);
  await page.mouse.move(start.x, start.y);
  await page.mouse.down();
  await page.mouse.move(end.x, end.y);
  await page.mouse.up();
  await expect(page.getByText('Selection: 8, 8 · 16 × 16')).toBeVisible();

  await page.keyboard.press('Control+Shift+I');
  await expect(page.getByText('Selection: 0, 0 · 32 × 32')).toBeVisible();
  await page.getByRole('button', { name: 'Undo' }).click();
  await expect(page.getByText('Selection: 8, 8 · 16 × 16')).toBeVisible();
  await page.getByRole('button', { name: 'Redo' }).click();
  await expect(page.getByText('Selection: 0, 0 · 32 × 32')).toBeVisible();

  await page.getByLabel('Selection refinement radius').fill('1');
  await page.getByRole('button', { name: 'Feather' }).click();
  await expect(page.getByText('Feather selection applied.')).toBeVisible();
  await expect(page.getByText('Selection: 0, 0 · 32 × 32')).toBeVisible();

  await page.getByRole('button', { name: 'Add mask from selection' }).click();
  expect(await exportedPixel(page, 4, 4)).toEqual([200, 80, 20, 255]);
  expect(await exportedPixel(page, 12, 12)).toEqual([0, 0, 0, 0]);
  await expect(page.getByText('Saved locally')).toBeVisible();
  await page.reload();
  await expect(page.getByText('Recovered your last local document.')).toBeVisible();
  expect(await exportedPixel(page, 4, 4)).toEqual([200, 80, 20, 255]);
  expect(await exportedPixel(page, 12, 12)).toEqual([0, 0, 0, 0]);
});

test('reuses persistent worker tiles and uploads only a locally changed tile', async ({ page }) => {
  await expect.poll(async () => page.evaluate(() => Boolean(window.__freetoshopPerf))).toBe(true);
  const result = await page.evaluate(async () => {
    const api = window.__freetoshopPerf;
    if (!api) throw new Error('Performance probe unavailable');
    return api.runPersistentWorkerResourceProbe();
  });
  expect(result.firstTransferBytes).toBe(512 * 512 * 4 * 2);
  expect(result.metadataRevisionTransferBytes).toBe(0);
  expect(result.localizedEditTransferBytes).toBe(256 * 256 * 4);
  expect(result.exact).toBe(true);
  expect(result.reusedWithoutTileUpload).toBe(true);
  expect(result.transferredOnlyChangedTile).toBe(true);
});

test('reuses persistent document tiles across native export and resize tasks', async ({ page }) => {
  await expect.poll(async () => page.evaluate(() => Boolean(window.__freetoshopPerf))).toBe(true);
  const result = await page.evaluate(async () => {
    const api = window.__freetoshopPerf;
    if (!api) throw new Error('Performance probe unavailable');
    return api.runPersistentDocumentTaskProbe();
  });
  expect(result.firstNativeTransferBytes).toBe(512 * 512 * 4 * 2);
  expect(result.unchangedResizeTransferBytes).toBe(0);
  expect(result.resizedResultNativeTransferBytes).toBe(0);
  expect(result.localizedNativeTransferBytes).toBe(256 * 256 * 4);
  expect(result.streamedResizeResultTileCount).toBe(2);
  expect(result.hydratedResizeResultResourceCount).toBe(2);
  expect(result.streamedNativeImportResultTileCount).toBe(8);
  expect(result.hydratedNativeImportResultResourceCount).toBe(2);
  expect(result.importedResultNativeTransferBytes).toBe(0);
  expect(result.nativeImportProgressSamples.length).toBeGreaterThanOrEqual(15);
  expect(result.nativeImportProgressSamples[0]).toBe(0.05);
  expect(result.nativeImportProgressSamples).toContain(0.5);
  expect(result.nativeImportProgressSamples.at(-1)).toBe(1);
  expect(result.nativeImportProgressMonotonic).toBe(true);
  expect(result.resizeExact).toBe(true);
  expect(result.nativeExact).toBe(true);
  expect(result.nativeImportExact).toBe(true);
});

test('bounds a dense 2048-square streamed resize to one canonical output store', async ({
  page,
}) => {
  await expect.poll(async () => page.evaluate(() => Boolean(window.__freetoshopPerf))).toBe(true);
  const result = await page.evaluate(async () => {
    const api = window.__freetoshopPerf;
    if (!api) throw new Error('Performance probe unavailable');
    return api.runLargeStreamedResizeProbe();
  });
  expect(result.streamedTileCount).toBe(64);
  expect(result.hydratedResourceCount).toBe(1);
  expect(result.allocatedBytes).toBe(2048 * 2048 * 4);
  expect(result.peakAllocatedBytes).toBe(result.expectedBytes);
  expect(result.durationMs).toBeLessThan(15_000);
  expect(result.progressMonotonic).toBe(true);
  expect(result.exact).toBe(true);
});

test('increments IndexedDB autosave by content-addressed tile resources', async ({ page }) => {
  await expect.poll(async () => page.evaluate(() => Boolean(window.__freetoshopPerf))).toBe(true);
  const result = await page.evaluate(async () => {
    const api = window.__freetoshopPerf;
    if (!api) throw new Error('Performance probe unavailable');
    return api.runIncrementalRepositoryProbe();
  });
  expect(result.firstWrittenResourceCount).toBe(1);
  expect(result.metadataWrittenResourceCount).toBe(0);
  expect(result.editedWrittenResourceCount).toBe(1);
  expect(result.editedWrittenResourceBytes).toBe(256 * 256 * 4);
  expect(result.removedResourceCount).toBe(2);
  expect(result.deferredMetadataResourceReads).toBe(0);
  expect(result.hydratedResourceReads).toBe(2);
  expect(result.hydrationMemoized).toBe(true);
  expect(result.deferredMetadataExact).toBe(true);
  expect(result.exact).toBe(true);
  expect(result.legacyDeferredMetadataExact).toBe(true);
  expect(result.legacyExact).toBe(true);
});

test('uses a bounded custom region rasterizer without a full-frame fallback', async ({ page }) => {
  await expect.poll(async () => page.evaluate(() => Boolean(window.__freetoshopPerf))).toBe(true);
  const result = await page.evaluate(() => {
    const api = window.__freetoshopPerf;
    if (!api) throw new Error('Performance probe unavailable');
    return api.runCustomRegionRasterizerProbe();
  });
  expect(result.fullRasterizerCalls).toBe(0);
  expect(result.regionRasterizerCalls).toBe(1);
  expect(result.sourcePatchBytes).toBe(67 * 67 * 4);
  expect(result.outputBytes).toBe(64 * 64 * 4);
  expect(result.exact).toBe(true);
});

test('filters an 8192-square complement selection through bounded coverage reads', async ({
  page,
}) => {
  await expect.poll(async () => page.evaluate(() => Boolean(window.__freetoshopPerf))).toBe(true);
  const result = await page.evaluate(() => {
    const api = window.__freetoshopPerf;
    if (!api) throw new Error('Performance probe unavailable');
    return api.runComplementFilterProbe();
  });
  expect(result.pointwisePatchCount).toBe(1);
  expect(result.gaussianPatchCount).toBeGreaterThan(0);
  expect(result.coverageReadCount).toBeGreaterThan(0);
  expect(result.maxCoverageReadBytes).toBeLessThanOrEqual(258 * 258);
  expect(result.materialized).toBe(false);
  expect(result.bounded).toBe(true);
});

test('streams real worker filter tiles with monotonic progress and exact output', async ({
  page,
}) => {
  await expect.poll(async () => page.evaluate(() => Boolean(window.__freetoshopPerf))).toBe(true);
  const result = await page.evaluate(async () => {
    const api = window.__freetoshopPerf;
    if (!api) throw new Error('Performance probe unavailable');
    return api.runStreamingFilterWorkerProbe();
  });
  expect(result.patchCount).toBe(4);
  expect(result.streamedPatchCount).toBe(4);
  expect(result.progressSamples.length).toBeGreaterThanOrEqual(6);
  expect(result.progressSamples.at(0)).toBe(0.05);
  expect(result.progressSamples.at(-1)).toBe(1);
  expect(result.monotonicProgress).toBe(true);
  expect(result.exact).toBe(true);
});

test('exports recovered selection bounds as a soft-masked PNG', async ({ page }) => {
  await page.getByLabel('Open image file').setInputFiles({
    name: 'selection-export.png',
    mimeType: 'image/png',
    buffer: await makePng(page, [200, 80, 20, 255]),
  });
  await page.getByRole('button', { name: 'Fit' }).click();
  await page.getByRole('button', { name: 'Rectangular select' }).click();
  const start = await documentPoint(page, 8, 8);
  const end = await documentPoint(page, 23, 23);
  await page.mouse.move(start.x, start.y);
  await page.mouse.down();
  await page.mouse.move(end.x, end.y);
  await page.mouse.up();
  await expect(page.getByText('Selection: 8, 8 · 16 × 16')).toBeVisible();
  await expect(page.getByText('Saved locally')).toBeVisible();
  await page.reload();
  await expect(page.getByText('Recovered your last local document.')).toBeVisible();

  const downloadPromise = page.waitForEvent('download');
  await clickMenuCommand(page, 'File', 'Export Selection PNG');
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toBe('selection-export-selection.png');
  const path = await download.path();
  if (!path) throw new Error('Selection export download path unavailable');
  const encoded = (await readFile(path)).toString('base64');
  const decoded = await page.evaluate(async (source) => {
    const binary = atob(source);
    const bytes = Uint8Array.from(binary, (character) => character.charCodeAt(0));
    const bitmap = await createImageBitmap(new Blob([bytes], { type: 'image/png' }));
    const canvas = document.createElement('canvas');
    canvas.width = bitmap.width;
    canvas.height = bitmap.height;
    const context = canvas.getContext('2d');
    if (!context) throw new Error('Canvas unavailable');
    context.drawImage(bitmap, 0, 0);
    return {
      width: bitmap.width,
      height: bitmap.height,
      firstPixel: [...context.getImageData(0, 0, 1, 1).data],
    };
  }, encoded);
  expect(decoded).toEqual({ width: 16, height: 16, firstPixel: [200, 80, 20, 255] });
  await expect(page.getByText('Selection PNG export completed.')).toBeVisible();
});

test('applies selection-aware direct image adjustments with history and recovery', async ({
  page,
}) => {
  await page.getByLabel('Open image file').setInputFiles({
    name: 'filter-source.png',
    mimeType: 'image/png',
    buffer: await makePng(page, [100, 50, 25, 255]),
  });
  await page.getByRole('button', { name: 'Fit' }).click();
  await page.getByRole('button', { name: 'Rectangular select' }).click();
  const start = await documentPoint(page, 8, 8);
  const end = await documentPoint(page, 23, 23);
  await page.mouse.move(start.x, start.y);
  await page.mouse.down();
  await page.mouse.move(end.x, end.y);
  await page.mouse.up();

  await clickMenuCommand(page, 'Image', 'Adjustments');
  await page.getByRole('menuitem', { name: 'Brightness/Contrast…' }).click();
  const dialog = page.getByRole('dialog', { name: 'Brightness / Contrast' });
  await expect(dialog).toBeVisible();
  await dialog.getByRole('button', { name: 'Cancel' }).click();
  expect(await exportedPixel(page, 12, 12)).toEqual([100, 50, 25, 255]);

  await clickMenuCommand(page, 'Image', 'Adjustments');
  await page.getByRole('menuitem', { name: 'Invert Ctrl+I' }).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  expect(await exportedPixel(page, 12, 12)).toEqual([155, 205, 230, 255]);
  expect(await exportedPixel(page, 4, 4)).toEqual([100, 50, 25, 255]);

  await openHistoryPanel(page);
  await page.getByRole('button', { name: 'History step Apply Invert' }).click();
  expect(await exportedPixel(page, 12, 12)).toEqual([100, 50, 25, 255]);
  await page.getByRole('button', { name: 'Future history step Apply Invert' }).click();
  expect(await exportedPixel(page, 12, 12)).toEqual([155, 205, 230, 255]);

  await expect(page.getByText('Saved locally')).toBeVisible();
  await page.reload();
  await expect(page.getByText('Recovered your last local document.')).toBeVisible();
  expect(await exportedPixel(page, 12, 12)).toEqual([155, 205, 230, 255]);
  expect(await exportedPixel(page, 4, 4)).toEqual([100, 50, 25, 255]);
});

test('creates and persists a nondestructive raster mask with density controls', async ({
  page,
}) => {
  await page.getByLabel('Open image file').setInputFiles({
    name: 'mask-source.png',
    mimeType: 'image/png',
    buffer: await makePng(page, [200, 80, 20, 255]),
  });
  await page.getByRole('button', { name: 'Fit' }).click();
  await page.getByRole('button', { name: 'Rectangular select' }).click();
  const start = await documentPoint(page, 8, 8);
  const end = await documentPoint(page, 23, 23);
  await page.mouse.move(start.x, start.y);
  await page.mouse.down();
  await page.mouse.move(end.x, end.y);
  await page.mouse.up();
  await expect(page.getByText('Selection: 8, 8 · 16 × 16')).toBeVisible();

  await page.getByRole('button', { name: 'Add mask from selection' }).click();
  await expect(page.getByRole('checkbox', { name: 'Layer mask enabled' })).toBeChecked();
  await expect(page.getByRole('slider', { name: 'Mask density', exact: true })).toHaveValue('100');
  expect(await exportedPixel(page, 12, 12)).toEqual([200, 80, 20, 255]);
  expect(await exportedPixel(page, 4, 4)).toEqual([0, 0, 0, 0]);

  await page.getByRole('checkbox', { name: 'Layer mask enabled' }).uncheck();
  expect(await exportedPixel(page, 4, 4)).toEqual([200, 80, 20, 255]);
  await page.getByRole('button', { name: 'Undo' }).click();
  await expect(page.getByRole('checkbox', { name: 'Layer mask enabled' })).toBeChecked();
  expect(await exportedPixel(page, 4, 4)).toEqual([0, 0, 0, 0]);

  await page.getByRole('slider', { name: 'Mask density', exact: true }).fill('50');
  await page.getByRole('slider', { name: 'Mask density', exact: true }).blur();
  await expect(page.getByLabel('Mask density value')).toHaveText('50%');
  // PNG decoders may round unpremultiplied RGB channels differently when
  // alpha is fractional; the visual result and alpha remain equivalent.
  expectPixelNear(await exportedPixel(page, 4, 4), [200, 80, 20, 128]);

  await page.getByRole('button', { name: 'Invert layer mask' }).click();
  expect(await exportedPixel(page, 4, 4)).toEqual([200, 80, 20, 255]);
  expectPixelNear(await exportedPixel(page, 12, 12), [200, 80, 20, 128]);

  await page.waitForTimeout(450);
  await expect(page.getByText('Saved locally')).toBeVisible();
  await page.reload();
  await expect(page.getByText('Recovered your last local document.')).toBeVisible();
  await expect(page.getByRole('checkbox', { name: 'Layer mask enabled' })).toBeChecked();
  await expect(page.getByRole('slider', { name: 'Mask density', exact: true })).toHaveValue('50');
  expectPixelNear(await exportedPixel(page, 12, 12), [200, 80, 20, 128]);

  await page.getByRole('button', { name: 'Delete layer mask' }).click();
  await expect(page.getByRole('button', { name: 'Add reveal-all mask' })).toBeVisible();
  expect(await exportedPixel(page, 12, 12)).toEqual([200, 80, 20, 255]);
  await page.getByRole('button', { name: 'Undo' }).click();
  await expect(page.getByRole('checkbox', { name: 'Layer mask enabled' })).toBeChecked();
  expectPixelNear(await exportedPixel(page, 12, 12), [200, 80, 20, 128]);
});

test('creates, softens, inverts, recovers, and deletes an editable vector mask', async ({
  page,
}) => {
  await page.getByLabel('Open image file').setInputFiles({
    name: 'vector-mask.png',
    mimeType: 'image/png',
    buffer: await makePng(page, [40, 180, 120, 255]),
  });
  await page.getByRole('button', { name: 'Add rectangular vector mask' }).click();
  await expectSelectedLayerName(page, /vector mask/);
  await expect(page.getByRole('checkbox', { name: 'Vector mask enabled' })).toBeChecked();
  expect(await exportedPixel(page, 12, 12)).toEqual([40, 180, 120, 255]);
  expect(await exportedPixel(page, 4, 4)).toEqual([0, 0, 0, 0]);
  await page.getByLabel('Vector mask command 2 anchor X').fill('20');
  expect(await exportedPixel(page, 24, 12)).toEqual([0, 0, 0, 0]);
  await page.getByRole('button', { name: 'Undo' }).click();
  expect(await exportedPixel(page, 24, 12)).toEqual([40, 180, 120, 255]);
  await page.getByRole('button', { name: 'Redo' }).click();
  expect(await exportedPixel(page, 24, 12)).toEqual([0, 0, 0, 0]);

  await page.getByRole('slider', { name: 'Vector mask density' }).fill('50');
  await page.getByRole('slider', { name: 'Vector mask density' }).blur();
  await expect(page.getByLabel('Vector mask density value')).toHaveText('50%');
  await expect(page.getByLabel('Vector mask command 2 anchor X')).toHaveValue('20');
  expect((await exportedPixel(page, 4, 4))[3]).toBe(128);

  await page.getByRole('button', { name: 'Invert vector mask' }).click();
  expect(await exportedPixel(page, 4, 4)).toEqual([40, 180, 120, 255]);
  expect((await exportedPixel(page, 12, 12))[3]).toBe(128);
  await page.getByRole('checkbox', { name: 'Vector mask enabled' }).uncheck();
  expect(await exportedPixel(page, 12, 12)).toEqual([40, 180, 120, 255]);
  await page.getByRole('button', { name: 'Undo' }).click();
  await expect(page.getByRole('checkbox', { name: 'Vector mask enabled' })).toBeChecked();

  await page.waitForTimeout(450);
  await expect(page.getByText('Saved locally')).toBeVisible();
  await page.reload();
  await expect(page.getByText('Recovered your last local document.')).toBeVisible();
  await expect(page.getByLabel('Vector mask density value')).toHaveText('50%');
  await expect(page.getByRole('button', { name: 'Restore vector mask direction' })).toBeVisible();
  await expect(page.getByLabel('Vector mask command 2 anchor X')).toHaveValue('20');
  expect((await exportedPixel(page, 12, 12))[3]).toBe(128);

  await page.getByRole('button', { name: 'Delete vector mask' }).click();
  await expect(page.getByRole('button', { name: 'Add rectangular vector mask' })).toBeVisible();
  expect(await exportedPixel(page, 12, 12)).toEqual([40, 180, 120, 255]);
});

test('edits, exports, and recovers a vector mask on structured shape content', async ({ page }) => {
  await page.getByRole('button', { name: 'Create document' }).click();
  await openMoreLayerActions(page);
  await page.getByLabel('Add shape layer').selectOption('rectangle');
  await page.getByLabel('Shape fill color').fill('#ff0000');
  await page.getByRole('button', { name: 'Add rectangular vector mask' }).click();
  expect(await exportedPixel(page, 512, 384)).toEqual([255, 0, 0, 255]);
  expect(await exportedPixel(page, 410, 384)).toEqual([0, 0, 0, 0]);

  await page.getByLabel('Vector mask command 2 anchor X').fill('120');
  expect(await exportedPixel(page, 550, 360)).toEqual([0, 0, 0, 0]);
  await page.getByRole('button', { name: 'Undo' }).click();
  expect(await exportedPixel(page, 550, 360)).toEqual([255, 0, 0, 255]);
  await page.getByRole('button', { name: 'Redo' }).click();
  expect(await exportedPixel(page, 550, 360)).toEqual([0, 0, 0, 0]);

  await expect(page.getByText('Saved locally')).toBeVisible();
  await page.reload();
  await expect(page.getByText('Recovered your last local document.')).toBeVisible();
  await expect(page.getByLabel('Vector mask command 2 anchor X')).toHaveValue('120');
  expect(await exportedPixel(page, 512, 384)).toEqual([255, 0, 0, 255]);
});

test('masks, edits, and recovers the combined result of a layer group', async ({ page }) => {
  await page.getByRole('button', { name: 'Create document' }).click();
  await openMoreLayerActions(page);
  await page.getByLabel('Add shape layer').selectOption('rectangle');
  await page.getByLabel('Shape fill color').fill('#ff0000');
  const shape = page.getByRole('button', { name: /Rectangle.*rectangle/ });
  const background = page.getByRole('button', { name: 'Background', exact: true });
  await shape.click();
  await background.click({ modifiers: ['Control'] });
  await page.getByRole('button', { name: 'Group selected layers' }).click();
  await page.getByRole('button', { name: /Group of 2.*2 layers/ }).click();
  await page.getByRole('button', { name: 'Add rectangular vector mask' }).click();
  await expect(page.getByText('document-space vector coverage')).toBeVisible();
  await expectSelectedLayerName(page, /vector mask/);
  expect(await exportedPixel(page, 512, 384)).toEqual([255, 0, 0, 255]);
  expect(await exportedPixel(page, 100, 384)).toEqual([0, 0, 0, 0]);

  await page.getByLabel('Vector mask command 2 anchor X').fill('120');
  expect(await exportedPixel(page, 550, 360)).toEqual([0, 0, 0, 0]);
  await page.getByRole('button', { name: 'Undo' }).click();
  expect(await exportedPixel(page, 550, 360)).toEqual([255, 0, 0, 255]);
  await page.getByRole('button', { name: 'Redo' }).click();

  await expect(page.getByText('Saved locally')).toBeVisible();
  await page.reload();
  await expect(page.getByText('Recovered your last local document.')).toBeVisible();
  await page.getByRole('button', { name: /Group of 2.*2 layers.*vector mask/ }).click();
  await expect(page.getByLabel('Vector mask command 2 anchor X')).toHaveValue('120');
  expect(await exportedPixel(page, 550, 360)).toEqual([0, 0, 0, 0]);
});

test('paints and recovers a document-space raster mask on a layer group', async ({ page }) => {
  await page.getByRole('button', { name: 'Create document' }).click();
  await openMoreLayerActions(page);
  await page.getByLabel('Add shape layer').selectOption('rectangle');
  await page.getByLabel('Shape fill color').fill('#ff0000');
  const shape = page.getByRole('button', { name: /Rectangle.*rectangle/ });
  const background = page.getByRole('button', { name: 'Background', exact: true });
  await shape.click();
  await background.click({ modifiers: ['Control'] });
  await page.getByRole('button', { name: 'Group selected layers' }).click();
  await page.getByRole('button', { name: /Group of 2.*2 layers/ }).click();
  await page.getByRole('button', { name: 'Add reveal-all mask' }).click();
  await expect(page.getByRole('radio', { name: 'Layer pixels' })).toBeDisabled();
  await expect(page.getByRole('radio', { name: 'Layer mask' })).toBeChecked();
  await expect(page.getByRole('checkbox', { name: 'Layer mask enabled' })).toBeChecked();

  await page.getByRole('button', { name: 'Eraser', exact: true }).click();
  await page.getByLabel('Brush size').fill('40');
  const canvasBox = await page.locator('canvas[aria-label$=" canvas"]').boundingBox();
  if (!canvasBox) throw new Error('Canvas bounds unavailable');
  await page.mouse.click(canvasBox.x + canvasBox.width / 2, canvasBox.y + canvasBox.height / 2);
  expect((await exportedPixel(page, 512, 384))[3]).toBe(0);
  await page.getByRole('button', { name: 'Undo' }).click();
  expect(await exportedPixel(page, 512, 384)).toEqual([255, 0, 0, 255]);
  await page.getByRole('button', { name: 'Redo' }).click();

  await expect(page.getByText('Saved locally')).toBeVisible();
  await page.reload();
  await expect(page.getByText('Recovered your last local document.')).toBeVisible();
  expect((await exportedPixel(page, 512, 384))[3]).toBe(0);
});

test('paints and recovers a raster mask on editable shape content', async ({ page }) => {
  await page.getByRole('button', { name: 'Create document' }).click();
  await openMoreLayerActions(page);
  await page.getByLabel('Add shape layer').selectOption('rectangle');
  await page.getByLabel('Shape fill color').fill('#ff0000');
  await page.getByRole('button', { name: 'Add reveal-all mask' }).click();
  await expect(page.getByRole('radio', { name: 'Layer pixels' })).toBeDisabled();
  await expect(page.getByRole('radio', { name: 'Layer mask' })).toBeChecked();
  await expectSelectedLayerName(page, /mask/);

  await page.getByRole('button', { name: 'Eraser', exact: true }).click();
  await page.getByLabel('Brush size').fill('40');
  const canvasBox = await page.locator('canvas[aria-label$=" canvas"]').boundingBox();
  if (!canvasBox) throw new Error('Canvas bounds unavailable');
  await page.mouse.click(canvasBox.x + canvasBox.width / 2, canvasBox.y + canvasBox.height / 2);
  expect((await exportedPixel(page, 512, 384))[3]).toBe(0);
  await page.getByRole('button', { name: 'Undo' }).click();
  expect(await exportedPixel(page, 512, 384)).toEqual([255, 0, 0, 255]);
  await page.getByRole('button', { name: 'Redo' }).click();

  await expect(page.getByText('Saved locally')).toBeVisible();
  await page.reload();
  await expect(page.getByText('Recovered your last local document.')).toBeVisible();
  expect((await exportedPixel(page, 512, 384))[3]).toBe(0);
});

test('paints selection-aware mask coverage without changing layer pixels', async ({ page }) => {
  await page.getByLabel('Open image file').setInputFiles({
    name: 'mask-paint.png',
    mimeType: 'image/png',
    buffer: await makePng(page, [200, 80, 20, 255]),
  });
  await page.getByRole('button', { name: 'Add reveal-all mask' }).click();
  await expect(page.getByRole('radio', { name: 'Layer pixels' })).toBeChecked();
  await page.getByRole('button', { name: 'Fit' }).click();

  await page.getByRole('button', { name: 'Rectangular select' }).click();
  const selectionStart = await documentPoint(page, 8, 8);
  const selectionEnd = await documentPoint(page, 23, 23);
  await page.mouse.move(selectionStart.x, selectionStart.y);
  await page.mouse.down();
  await page.mouse.move(selectionEnd.x, selectionEnd.y);
  await page.mouse.up();
  await expect(page.getByText('Selection: 8, 8 · 16 × 16')).toBeVisible();

  await page.getByRole('radio', { name: 'Layer mask' }).check();
  await expect(page.getByLabel('Tool options', { exact: true })).toContainText(/layer mask/i);
  await page.getByRole('button', { name: 'Eraser', exact: true }).click();
  await page.getByLabel('Brush size').fill('6');
  const strokeStart = await documentPoint(page, 4, 12);
  const strokeEnd = await documentPoint(page, 16, 12);
  await page.mouse.move(strokeStart.x, strokeStart.y);
  await page.mouse.down();
  await page.mouse.move(strokeEnd.x, strokeEnd.y, { steps: 5 });
  await page.mouse.up();
  expect(await exportedPixel(page, 6, 12)).toEqual([200, 80, 20, 255]);
  expect((await exportedPixel(page, 12, 12))[3]).toBe(0);

  await page.getByRole('button', { name: 'Undo' }).click();
  expect(await exportedPixel(page, 12, 12)).toEqual([200, 80, 20, 255]);
  await page.getByRole('button', { name: 'Redo' }).click();
  expect((await exportedPixel(page, 12, 12))[3]).toBe(0);

  await page.getByRole('button', { name: 'Brush', exact: true }).click();
  const revealPoint = await documentPoint(page, 12, 12);
  await page.mouse.click(revealPoint.x, revealPoint.y);
  expect(await exportedPixel(page, 12, 12)).toEqual([200, 80, 20, 255]);

  await page.getByRole('radio', { name: 'Layer pixels' }).check();
  await page.getByLabel('Brush color').fill('#0064c8');
  const sourcePoint = await documentPoint(page, 12, 20);
  await page.mouse.click(sourcePoint.x, sourcePoint.y);
  expect(await exportedPixel(page, 12, 20)).toEqual([0, 100, 200, 255]);

  await page.getByRole('radio', { name: 'Layer mask' }).check();
  await page.getByRole('button', { name: 'Eraser', exact: true }).click();
  await page.mouse.click(sourcePoint.x, sourcePoint.y);
  expect((await exportedPixel(page, 12, 20))[3]).toBe(0);
  await page.getByRole('checkbox', { name: 'Layer mask enabled' }).uncheck();
  expect(await exportedPixel(page, 12, 20)).toEqual([0, 100, 200, 255]);
  await page.getByRole('checkbox', { name: 'Layer mask enabled' }).check();

  await page.waitForTimeout(450);
  await expect(page.getByText('Saved locally')).toBeVisible();
  await page.reload();
  await expect(page.getByText('Recovered your last local document.')).toBeVisible();
  await expect(page.getByRole('radio', { name: 'Layer pixels' })).toBeChecked();
  expect((await exportedPixel(page, 12, 20))[3]).toBe(0);
});

test('saves, previews, renames, restores, and navigates alpha channels', async ({ page }) => {
  await page.getByLabel('Open image file').setInputFiles({
    name: 'channel-source.png',
    mimeType: 'image/png',
    buffer: await makePng(page, [120, 60, 30, 255]),
  });
  await page.getByRole('button', { name: 'Fit' }).click();
  await page.getByRole('button', { name: 'Rectangular select' }).click();
  const start = await documentPoint(page, 8, 8);
  const end = await documentPoint(page, 23, 23);
  await page.mouse.move(start.x, start.y);
  await page.mouse.down();
  await page.mouse.move(end.x, end.y);
  await page.mouse.up();
  await expect(page.getByText('Selection: 8, 8 · 16 × 16')).toBeVisible();

  await page.getByRole('tab', { name: 'Channels' }).click();
  await page.getByRole('button', { name: 'Save selection as channel' }).click();
  await expect(page.getByRole('heading', { name: 'Channels · 1' })).toBeVisible();
  await expect(page.getByRole('radio', { name: 'Preview Alpha 1' })).toBeChecked();
  await expect.poll(() => visibleCanvasPixel(page, 4, 4)).toEqual([0, 0, 0, 255]);
  await expect.poll(() => visibleCanvasPixel(page, 12, 12)).toEqual([255, 255, 255, 255]);

  expect(await exportedPixel(page, 4, 4)).toEqual([120, 60, 30, 255]);
  expect(await exportedPixel(page, 12, 12)).toEqual([120, 60, 30, 255]);

  const name = page.getByLabel('Channel name for Alpha 1');
  await name.fill('Subject matte');
  await name.press('Enter');
  await expect(page.getByRole('radio', { name: 'Preview Subject matte' })).toBeChecked();

  await page.getByRole('radio', { name: 'Composite color' }).check();
  await page.getByRole('button', { name: 'Deselect' }).click();
  await expect(page.getByText('No selection')).toBeVisible();
  await page.getByRole('radio', { name: 'Preview Subject matte' }).check();
  await page.getByRole('button', { name: 'Load selected channel as selection' }).click();
  await expect(page.getByText('Selection: 8, 8 · 16 × 16')).toBeVisible();

  await page.getByRole('button', { name: 'Delete selected channel' }).click();
  await expect(page.getByRole('heading', { name: 'Channels · 0' })).toBeVisible();
  await openHistoryPanel(page);
  await page.getByRole('button', { name: 'History step Remove alpha channel' }).click();
  await page.getByRole('tab', { name: 'Channels' }).click();
  await expect(page.getByRole('radio', { name: 'Preview Subject matte' })).toBeVisible();
  await openHistoryPanel(page);
  await page.getByRole('button', { name: 'Future history step Remove alpha channel' }).click();
  await expect(page.getByText('Channels · 0')).toBeVisible();
  await page.getByRole('button', { name: 'History step Remove alpha channel' }).click();

  await expect(page.getByText('Saved locally')).toBeVisible();
  await page.reload();
  await expect(page.getByText('Recovered your last local document.')).toBeVisible();
  await page.getByRole('tab', { name: 'Channels' }).click();
  await expect(page.getByRole('radio', { name: 'Preview Subject matte' })).toBeVisible();
  await openHistoryPanel(page);
  await expect(page.getByText('0 undo steps, 0 redo steps')).toBeVisible();
  await expect(page.getByText('History begins at the current or recovered state.')).toBeVisible();
});

test('supports keyboard navigation between functional document panels', async ({ page }) => {
  await page.getByRole('button', { name: 'Create document' }).click();
  const layers = page.getByRole('tab', { name: 'Layers' });
  await layers.focus();
  await layers.press('ArrowRight');
  await expect(page.getByRole('tab', { name: 'Channels' })).toBeFocused();
  await expect(page.getByRole('heading', { name: 'Channels · 0' })).toBeVisible();
  await page.getByRole('tab', { name: 'Channels' }).press('End');
  await expect(page.getByRole('tab', { name: 'Paths' })).toBeFocused();
  await openHistoryPanel(page);
  await expect(page.getByRole('heading', { name: 'History' })).toBeVisible();
  await page.getByRole('tab', { name: 'Paths' }).press('Home');
  await expect(layers).toBeFocused();
  await expect(page.getByText('Layers · 1')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Add pixel layer' })).toBeVisible();
});

test('rotates and paints affine-transformed pixels with undo, export, and recovery', async ({
  page,
}) => {
  await page.getByLabel('Open image file').setInputFiles({
    name: 'quadrants.png',
    mimeType: 'image/png',
    buffer: await makeQuadrantPng(page),
  });
  await page.getByRole('button', { name: 'Fit' }).click();
  expect(await exportedPixel(page, 4, 4)).toEqual([200, 0, 0, 255]);
  expect(await exportedPixel(page, 4, 27)).toEqual([0, 0, 220, 255]);

  await page.getByRole('button', { name: 'Rotate 90° clockwise' }).click();
  await expect(page.getByLabel('Layer position X')).toHaveValue('32');
  await expect(page.getByLabel('Layer position Y')).toHaveValue('0');
  expect(await exportedPixel(page, 27, 4)).toEqual([200, 0, 0, 255]);
  expect(await exportedPixel(page, 4, 4)).toEqual([0, 0, 220, 255]);

  await page.getByRole('button', { name: 'Brush', exact: true }).click();
  await page.getByLabel('Brush size').fill('2');
  await page.getByLabel('Brush color').fill('#ffffff');
  const transformedRed = await documentPoint(page, 27, 4);
  await page.mouse.click(transformedRed.x, transformedRed.y);
  expect(await exportedPixel(page, 27, 4)).toEqual([255, 255, 255, 255]);

  await page.getByRole('button', { name: 'Undo' }).click();
  expect(await exportedPixel(page, 27, 4)).toEqual([200, 0, 0, 255]);
  await page.getByRole('button', { name: 'Undo' }).click();
  expect(await exportedPixel(page, 4, 4)).toEqual([200, 0, 0, 255]);
  await page.getByRole('button', { name: 'Redo' }).click();
  expect(await exportedPixel(page, 27, 4)).toEqual([200, 0, 0, 255]);
  await page.getByRole('button', { name: 'Redo' }).click();
  expect(await exportedPixel(page, 27, 4)).toEqual([255, 255, 255, 255]);

  await page.waitForTimeout(450);
  await expect(page.getByText('Saved locally')).toBeVisible();
  await page.reload();
  await expect(page.getByText('Recovered your last local document.')).toBeVisible();
  await expect(page.getByLabel('Layer position X')).toHaveValue('32');
  expect(await exportedPixel(page, 27, 4)).toEqual([255, 255, 255, 255]);
});

test('crops rotated documents while preserving selections, channels, history, and recovery', async ({
  page,
}) => {
  test.setTimeout(60_000);
  await page.getByLabel('Open image file').setInputFiles({
    name: 'crop-quadrants.png',
    mimeType: 'image/png',
    buffer: await makeQuadrantPng(page),
  });
  await page.getByRole('button', { name: 'Fit' }).click();

  await page.getByRole('button', { name: 'Rectangular select' }).click();
  const selectionStart = await documentPoint(page, 8, 8);
  const selectionEnd = await documentPoint(page, 23, 23);
  await page.mouse.move(selectionStart.x, selectionStart.y);
  await page.mouse.down();
  await page.mouse.move(selectionEnd.x, selectionEnd.y);
  await page.mouse.up();
  await page.getByRole('tab', { name: 'Channels' }).click();
  await page.getByRole('button', { name: 'Save selection as channel' }).click();
  await page.getByRole('radio', { name: 'Composite color' }).check();
  await page.getByRole('tab', { name: 'Layers' }).click();
  await page.getByRole('button', { name: 'Rotate 90° clockwise' }).click();

  await page.getByRole('button', { name: 'Crop document' }).click();
  const cropStart = await documentCoordinate(page, 4.25, 4.25);
  const cropEnd = await documentCoordinate(page, 19.75, 23.75);
  await page.mouse.move(cropStart.x, cropStart.y);
  await page.mouse.down();
  await page.mouse.move(cropEnd.x, cropEnd.y);
  await page.mouse.up();
  await expect(page.getByLabel('Crop x')).toHaveValue('4');
  await expect(page.getByLabel('Crop y')).toHaveValue('4');
  await expect(page.getByLabel('Crop width')).toHaveValue('16');
  await expect(page.getByLabel('Crop height')).toHaveValue('20');
  await page.keyboard.press('Escape');
  await expect(page.getByText('Crop cancelled.')).toBeVisible();
  await expect(page.getByText('32 × 32 px')).toBeVisible();

  await page.getByRole('button', { name: 'Crop document' }).click();
  await page.getByLabel('Crop x').fill('4');
  await page.getByLabel('Crop y').fill('4');
  await page.getByLabel('Crop width').fill('16');
  await page.getByLabel('Crop height').fill('20');
  await page.getByLabel('Crop height').press('Enter');
  await expect(page.getByText('Cropped to 16 × 20 pixels.')).toBeVisible();
  await expect(page.getByText('16 × 20 px')).toBeVisible();
  expect(await exportedDimensions(page)).toEqual([16, 20]);
  expect(await exportedPixel(page, 0, 0)).toEqual([0, 0, 220, 255]);
  expect(await exportedPixel(page, 15, 0)).toEqual([200, 0, 0, 255]);
  expect(await exportedPixel(page, 0, 19)).toEqual([220, 180, 0, 255]);
  expect(await exportedPixel(page, 15, 19)).toEqual([0, 180, 0, 255]);
  await expect(page.getByText('Selection: 4, 4 · 12 × 16')).toBeVisible();

  await openHistoryPanel(page);
  await page.getByRole('button', { name: 'History step Crop document' }).click();
  await expect(page.getByText('32 × 32 px')).toBeVisible();
  await page.getByRole('button', { name: 'Future history step Crop document' }).click();
  await expect(page.getByText('16 × 20 px')).toBeVisible();

  await page.getByRole('button', { name: 'Rectangular select' }).click();
  await page.getByRole('button', { name: 'Deselect' }).click();
  await page.getByRole('tab', { name: 'Channels' }).click();
  await page.getByRole('radio', { name: 'Preview Alpha 1' }).check();
  await page.getByRole('button', { name: 'Load selected channel as selection' }).click();
  await expect(page.getByText('Selection: 4, 4 · 12 × 16')).toBeVisible();

  await expect(page.getByText('Saved locally')).toBeVisible();
  await page.reload();
  await expect(page.getByText('Recovered your last local document.')).toBeVisible();
  await expect(page.getByText('16 × 20 px')).toBeVisible();
  expect(await exportedDimensions(page)).toEqual([16, 20]);
  expect(await exportedPixel(page, 0, 0)).toEqual([0, 0, 220, 255]);
  await page.getByRole('tab', { name: 'Channels' }).click();
  await expect(page.getByRole('radio', { name: 'Preview Alpha 1' })).toBeVisible();
});

test('resizes anchored canvases and reveals transformed layer content as undoable steps', async ({
  page,
}) => {
  await page.getByLabel('Open image file').setInputFiles({
    name: 'canvas-size.png',
    mimeType: 'image/png',
    buffer: await makeQuadrantPng(page),
  });
  await clickMenuCommand(page, 'Image', 'Canvas Size…');
  const canvasDialog = page.getByRole('dialog', { name: 'Canvas Size' });
  await expect(canvasDialog.getByRole('heading', { name: 'Canvas Size' })).toBeVisible();
  await expect(page.getByLabel('Canvas width')).toBeFocused();
  await page.getByLabel('Canvas width').fill('40');
  await page.keyboard.press('Escape');
  await expect(canvasDialog).toBeHidden();
  await expect(page.getByRole('menuitem', { name: 'Image', exact: true })).toBeFocused();
  await expect(page.getByText('32 × 32 px')).toBeVisible();

  await clickMenuCommand(page, 'Image', 'Canvas Size…');
  await page.getByLabel('Canvas width').fill('0');
  await expect(
    canvasDialog.getByText('Width and height must be positive whole numbers.'),
  ).toBeVisible();
  await expect(page.getByRole('button', { name: 'Resize canvas' })).toBeDisabled();
  await page.getByLabel('Canvas width').fill('40');
  await page.getByLabel('Canvas height').fill('36');
  await page.getByLabel('Anchor top left').check();
  await page.getByRole('button', { name: 'Resize canvas' }).click();
  await expect(page.getByText('40 × 36 px')).toBeVisible();
  expect(await exportedDimensions(page)).toEqual([40, 36]);
  expect(await exportedPixel(page, 4, 4)).toEqual([200, 0, 0, 255]);
  expect(await exportedPixel(page, 39, 35)).toEqual([0, 0, 0, 0]);

  await page.getByLabel('Layer position X').fill('50');
  await page.getByLabel('Layer position X').press('Enter');
  await page.getByLabel('Layer position Y').fill('-6');
  await page.getByLabel('Layer position Y').press('Enter');
  await clickMenuCommand(page, 'Image', 'Reveal All');
  await expect(page.getByText('82 × 42 px')).toBeVisible();
  expect(await exportedDimensions(page)).toEqual([82, 42]);
  expect(await exportedPixel(page, 50, 0)).toEqual([200, 0, 0, 255]);
  expect(await exportedPixel(page, 81, 31)).toEqual([220, 180, 0, 255]);

  await openHistoryPanel(page);
  await page.getByRole('button', { name: 'History step Reveal all' }).click();
  await expect(page.getByText('40 × 36 px')).toBeVisible();
  await page.getByRole('button', { name: 'Future history step Reveal all' }).click();
  await expect(page.getByText('82 × 42 px')).toBeVisible();
});

test('resamples rotated images, selections, and channels with history and recovery', async ({
  page,
}) => {
  test.setTimeout(60_000);
  await page.getByLabel('Open image file').setInputFiles({
    name: 'image-size.png',
    mimeType: 'image/png',
    buffer: await makeQuadrantPng(page),
  });
  await page.getByRole('button', { name: 'Fit' }).click();
  await page.getByRole('button', { name: 'Rectangular select' }).click();
  const selectionStart = await documentPoint(page, 8, 8);
  const selectionEnd = await documentPoint(page, 23, 23);
  await page.mouse.move(selectionStart.x, selectionStart.y);
  await page.mouse.down();
  await page.mouse.move(selectionEnd.x, selectionEnd.y);
  await page.mouse.up();
  await page.getByRole('tab', { name: 'Channels' }).click();
  await page.getByRole('button', { name: 'Save selection as channel' }).click();
  await page.getByRole('radio', { name: 'Composite color' }).check();
  await page.getByRole('tab', { name: 'Layers' }).click();
  await page.getByRole('button', { name: 'Rotate 90° clockwise' }).click();

  await clickMenuCommand(page, 'Image', 'Image Size…');
  const imageDialog = page.getByRole('dialog', { name: 'Image Size' });
  await expect(imageDialog.getByText('Current size: 32 × 32 pixels')).toBeVisible();
  await expect(page.getByLabel('Constrain proportions')).toBeChecked();
  await page.getByLabel('Image width').fill('16');
  await expect(page.getByLabel('Image height')).toHaveValue('16');
  await page.getByLabel('Resampling method').selectOption('nearest');
  await page.getByRole('button', { name: 'Resize image' }).click();
  await expect(page.getByText('16 × 16 px')).toBeVisible();
  expect(await exportedDimensions(page)).toEqual([16, 16]);
  expect(await exportedPixel(page, 2, 2)).toEqual([0, 0, 220, 255]);
  expect(await exportedPixel(page, 13, 2)).toEqual([200, 0, 0, 255]);
  expect(await exportedPixel(page, 2, 13)).toEqual([220, 180, 0, 255]);
  expect(await exportedPixel(page, 13, 13)).toEqual([0, 180, 0, 255]);
  await expect(page.getByText('Selection: 4, 4 · 8 × 8')).toBeVisible();

  await openHistoryPanel(page);
  await page.getByRole('button', { name: 'History step Resize image' }).click();
  await expect(page.getByText('32 × 32 px')).toBeVisible();
  await page.getByRole('button', { name: 'Future history step Resize image' }).click();
  await expect(page.getByText('16 × 16 px')).toBeVisible();

  await page.getByRole('button', { name: 'Rectangular select' }).click();
  await page.getByRole('button', { name: 'Deselect' }).click();
  await page.getByRole('tab', { name: 'Channels' }).click();
  await page.getByRole('radio', { name: 'Preview Alpha 1' }).check();
  await page.getByRole('button', { name: 'Load selected channel as selection' }).click();
  await expect(page.getByText('Selection: 4, 4 · 8 × 8')).toBeVisible();

  await expect(page.getByText('Saved locally')).toBeVisible();
  await page.reload();
  await expect(page.getByText('Recovered your last local document.')).toBeVisible();
  await expect(page.getByText('16 × 16 px')).toBeVisible();
  expect(await exportedPixel(page, 2, 2)).toEqual([0, 0, 220, 255]);
  await page.getByRole('tab', { name: 'Channels' }).click();
  await expect(page.getByRole('radio', { name: 'Preview Alpha 1' })).toBeVisible();
});

test('free transforms with accessible scale and rotation handles as single history steps', async ({
  page,
}) => {
  await page.getByLabel('Open image file').setInputFiles({
    name: 'free-transform.png',
    mimeType: 'image/png',
    buffer: await makeQuadrantPng(page),
  });
  await page.getByRole('button', { name: 'Fit' }).click();
  await page.keyboard.press('ControlOrMeta+T');
  await expect(
    page.getByRole('group', { name: 'Transform controls for free-transform.png' }),
  ).toBeVisible();

  const scale = page.getByRole('button', { name: 'Scale layer from bottom right' });
  const scaleBox = await scale.boundingBox();
  if (!scaleBox) throw new Error('Scale handle bounds unavailable');
  expect(scaleBox.width).toBeGreaterThanOrEqual(24);
  expect(scaleBox.height).toBeGreaterThanOrEqual(24);
  const scaledTarget = await documentCoordinate(page, 24, 24);
  await page.mouse.move(scaleBox.x + scaleBox.width / 2, scaleBox.y + scaleBox.height / 2);
  await page.mouse.down();
  await page.mouse.move(scaledTarget.x, scaledTarget.y, { steps: 4 });
  await page.mouse.up();
  expect(await exportedPixel(page, 6, 6)).toEqual([200, 0, 0, 255]);
  expect(await exportedPixel(page, 18, 6)).toEqual([0, 180, 0, 255]);
  expect(await exportedPixel(page, 6, 18)).toEqual([0, 0, 220, 255]);
  expect(await exportedPixel(page, 18, 18)).toEqual([220, 180, 0, 255]);
  expect(await exportedPixel(page, 26, 26)).toEqual([0, 0, 0, 0]);

  await page.getByRole('button', { name: 'Undo' }).click();
  expect(await exportedPixel(page, 26, 26)).toEqual([220, 180, 0, 255]);
  await expect(page.getByRole('button', { name: 'Undo' })).toBeDisabled();

  const rotate = page.getByRole('button', { name: 'Rotate layer' });
  const rotateBox = await rotate.boundingBox();
  if (!rotateBox) throw new Error('Rotation handle bounds unavailable');
  const center = await documentCoordinate(page, 16, 16);
  const rotateStart = {
    x: rotateBox.x + rotateBox.width / 2,
    y: rotateBox.y + rotateBox.height / 2,
  };
  const radius = center.y - rotateStart.y;
  await page.mouse.move(rotateStart.x, rotateStart.y);
  await page.mouse.down();
  await page.mouse.move(center.x + radius, center.y, { steps: 6 });
  await page.mouse.up();
  expect(await exportedPixel(page, 27, 4)).toEqual([200, 0, 0, 255]);
  expect(await exportedPixel(page, 4, 4)).toEqual([0, 0, 220, 255]);
  expect(await exportedPixel(page, 27, 27)).toEqual([0, 180, 0, 255]);
  expect(await exportedPixel(page, 4, 27)).toEqual([220, 180, 0, 255]);

  await page.getByRole('button', { name: 'Undo' }).click();
  expect(await exportedPixel(page, 4, 4)).toEqual([200, 0, 0, 255]);
  await page.getByRole('button', { name: 'Redo' }).click();
  expect(await exportedPixel(page, 4, 4)).toEqual([0, 0, 220, 255]);

  const cancelScale = page.getByRole('button', { name: 'Scale layer from bottom right' });
  const cancelBox = await cancelScale.boundingBox();
  if (!cancelBox) throw new Error('Cancel scale handle bounds unavailable');
  await page.mouse.move(cancelBox.x + cancelBox.width / 2, cancelBox.y + cancelBox.height / 2);
  await page.mouse.down();
  await page.mouse.move(cancelBox.x + 40, cancelBox.y + 40);
  await page.keyboard.press('Escape');
  await page.mouse.up();
  await expect(page.getByText('Free Transform cancelled.')).toBeVisible();
  expect(await exportedPixel(page, 4, 4)).toEqual([0, 0, 220, 255]);

  await page.getByRole('button', { name: 'Rotate layer' }).focus();
  await page.getByRole('button', { name: 'Rotate layer' }).press('Shift+ArrowLeft');
  await expect(page.getByText('Layer rotated -15 degrees.')).toBeVisible();
  await page.getByRole('button', { name: 'Undo' }).click();
  expect(await exportedPixel(page, 4, 4)).toEqual([0, 0, 220, 255]);

  await page.waitForTimeout(450);
  await expect(page.getByText('Saved locally')).toBeVisible();
  await page.reload();
  await expect(page.getByText('Recovered your last local document.')).toBeVisible();
  await page.keyboard.press('ControlOrMeta+T');
  await expect(page.getByRole('button', { name: 'Rotate layer' })).toBeVisible();
  expect(await exportedPixel(page, 4, 4)).toEqual([0, 0, 220, 255]);
});

test('paints with clone stamp from Alt-set source with undo, reopen, and aligned persistence', async ({
  page,
}) => {
  await page.getByLabel('Open image file').setInputFiles({
    name: 'clone-source.png',
    mimeType: 'image/png',
    buffer: await makeQuadrantPng(page),
  });
  await page.getByRole('button', { name: 'Fit' }).click();

  // Precondition: dest pixel differs from source.
  expect(await exportedPixel(page, 4, 20)).toEqual([0, 0, 220, 255]);

  await page.getByRole('button', { name: 'Clone stamp' }).click();
  await expect(page.getByLabel('Tool options', { exact: true })).toContainText('Clone Stamp');
  await expect(page.getByText('Alt/Option-click to set clone source')).toBeVisible();

  // Alt-click to set source at red quadrant center (8,8).
  const sourcePt = await documentPoint(page, 8, 8);
  await page.keyboard.down('Alt');
  await page.mouse.click(sourcePt.x, sourcePt.y);
  await page.keyboard.up('Alt');
  await expect(page.getByText(/Source set at/)).toBeVisible();

  // Paint at blue quadrant (4,20) - should clone red.
  const destPt = await documentPoint(page, 4, 20);
  await page.mouse.move(destPt.x, destPt.y);
  await page.mouse.down();
  await page.mouse.move(destPt.x + 6, destPt.y, { steps: 4 });
  await page.mouse.up();

  await expect.poll(() => exportedPixel(page, 4, 20)).not.toEqual([0, 0, 220, 255]);
  const cloned = await exportedPixel(page, 4, 20);
  // Red channel high indicates cloned from source.
  expect(cloned[0]).toBeGreaterThan(150);

  // Undo should revert.
  await page.getByRole('button', { name: 'Undo' }).click();
  expect(await exportedPixel(page, 4, 20)).toEqual([0, 0, 220, 255]);

  // Redo brings back clone.
  await page.getByRole('button', { name: 'Redo' }).click();
  await expect.poll(async () => (await exportedPixel(page, 4, 20))[0]).toBeGreaterThan(150);

  // One gesture = one undo step.
  await openHistoryPanel(page);
  await expect(page.getByRole('button', { name: /^History step Clone stamp/ })).toBeVisible();

  // Persistence check - reload and verify clone persists.
  await page.getByRole('tab', { name: 'Layers' }).click();
  await expect(page.getByText('Saved locally')).toBeVisible();
  await page.reload();
  await expect(page.getByText('Recovered your last local document.')).toBeVisible();
  await expect.poll(async () => (await exportedPixel(page, 4, 20))[0]).toBeGreaterThan(150);
});

test('paints with healing brush from Alt-set source with undo, reopen, and history', async ({
  page,
}) => {
  await page.getByLabel('Open image file').setInputFiles({
    name: 'heal-source.png',
    mimeType: 'image/png',
    buffer: await makeQuadrantPng(page),
  });
  await page.getByRole('button', { name: 'Fit' }).click();

  expect(await exportedPixel(page, 4, 20)).toEqual([0, 0, 220, 255]);

  await page.getByRole('button', { name: 'Healing brush' }).click();
  await expect(page.getByLabel('Tool options', { exact: true })).toContainText('Healing Brush');
  await expect(page.getByText(/Alt\/Option-click to set healing source/i)).toBeVisible();

  const sourcePt = await documentPoint(page, 8, 8);
  await page.keyboard.down('Alt');
  await page.mouse.click(sourcePt.x, sourcePt.y);
  await page.keyboard.up('Alt');
  await expect(page.getByText(/Source set at/)).toBeVisible();

  const destPt = await documentPoint(page, 4, 20);
  await page.mouse.move(destPt.x, destPt.y);
  await page.mouse.down();
  await page.mouse.move(destPt.x + 6, destPt.y, { steps: 4 });
  await page.mouse.up();

  await expect.poll(() => exportedPixel(page, 4, 20)).not.toEqual([0, 0, 220, 255]);
  const healed = await exportedPixel(page, 4, 20);
  // Stroke must change the blue field (tone-matched texture transfer).
  expect(healed).not.toEqual([0, 0, 220, 255]);

  await page.getByRole('button', { name: 'Undo' }).click();
  expect(await exportedPixel(page, 4, 20)).toEqual([0, 0, 220, 255]);
  await page.getByRole('button', { name: 'Redo' }).click();
  await expect.poll(async () => exportedPixel(page, 4, 20)).not.toEqual([0, 0, 220, 255]);

  await openHistoryPanel(page);
  await expect(page.getByRole('button', { name: /^History step Healing brush/ })).toBeVisible();

  await page.getByRole('tab', { name: 'Layers' }).click();
  await expect(page.getByText('Saved locally')).toBeVisible();
  await page.reload();
  await expect(page.getByText('Recovered your last local document.')).toBeVisible();
  await expect.poll(async () => exportedPixel(page, 4, 20)).not.toEqual([0, 0, 220, 255]);
});

test('dodges, burns, and smudges with undo, history, and persistence', async ({ page }) => {
  await page.getByLabel('Open image file').setInputFiles({
    name: 'retouch.png',
    mimeType: 'image/png',
    buffer: await makeQuadrantPng(page),
  });
  await page.getByRole('button', { name: 'Fit' }).click();

  const grayBefore = await exportedPixel(page, 4, 20);
  expect(grayBefore[2]).toBeGreaterThan(200);

  await page.getByRole('button', { name: 'Dodge' }).click();
  await expect(page.getByLabel('Tool options', { exact: true })).toContainText('Dodge');
  await expect(page.getByLabel('Dodge burn exposure')).toHaveValue('50');
  const dodgePt = await documentPoint(page, 4, 20);
  await page.mouse.move(dodgePt.x, dodgePt.y);
  await page.mouse.down();
  await page.mouse.move(dodgePt.x + 4, dodgePt.y, { steps: 3 });
  await page.mouse.up();
  await expect
    .poll(async () => (await exportedPixel(page, 4, 20))[2])
    .toBeGreaterThan(grayBefore[2] ?? 0);

  await page.getByRole('button', { name: 'Undo' }).click();
  expect(await exportedPixel(page, 4, 20)).toEqual(grayBefore);

  await page.keyboard.press('Shift+O');
  await expect(page.getByLabel('Tool options', { exact: true })).toContainText('Burn');
  const burnPt = await documentPoint(page, 4, 20);
  await page.mouse.move(burnPt.x, burnPt.y);
  await page.mouse.down();
  await page.mouse.move(burnPt.x + 4, burnPt.y, { steps: 3 });
  await page.mouse.up();
  await expect
    .poll(async () => (await exportedPixel(page, 4, 20))[2])
    .toBeLessThan(grayBefore[2] ?? 255);

  await page.getByRole('button', { name: 'Undo' }).click();
  await page.keyboard.press('r');
  await expect(page.getByLabel('Tool options', { exact: true })).toContainText('Smudge');
  const smudgeStart = await documentPoint(page, 12, 8);
  const smudgeEnd = await documentPoint(page, 18, 8);
  const greenBefore = await exportedPixel(page, 17, 8);
  expect(greenBefore[1]).toBeGreaterThan(150);
  await page.mouse.move(smudgeStart.x, smudgeStart.y);
  await page.mouse.down();
  await page.mouse.move(smudgeEnd.x, smudgeEnd.y, { steps: 6 });
  await page.mouse.up();
  await expect.poll(() => exportedPixel(page, 17, 8)).not.toEqual(greenBefore);

  await page.keyboard.press('o');
  await page.keyboard.press('Shift+O');
  await page.keyboard.press('Shift+O');
  await expect(page.getByLabel('Tool options', { exact: true })).toContainText('Sponge');
  await page.getByLabel('Sponge mode').selectOption('desaturate');
  const blueBefore = await exportedPixel(page, 4, 20);
  expect(blueBefore[2]).toBeGreaterThan(200);
  const spongePt = await documentPoint(page, 4, 20);
  await page.mouse.move(spongePt.x, spongePt.y);
  await page.mouse.down();
  await page.mouse.move(spongePt.x + 4, spongePt.y, { steps: 3 });
  await page.mouse.up();
  await expect
    .poll(async () => (await exportedPixel(page, 4, 20))[2])
    .toBeLessThan(blueBefore[2] ?? 255);

  await openHistoryPanel(page);
  await expect(page.getByRole('button', { name: /^History step Sponge/ })).toBeVisible();

  await page.getByRole('tab', { name: 'Layers' }).click();
  await expect(page.getByText('Saved locally')).toBeVisible();
  await page.reload();
  await expect(page.getByText('Recovered your last local document.')).toBeVisible();
  await expect.poll(() => exportedPixel(page, 17, 8)).not.toEqual(greenBefore);
});

test('paints with spot healing brush without source with undo and history', async ({ page }) => {
  const bytes = await page.evaluate(async () => {
    const canvas = document.createElement('canvas');
    canvas.width = 32;
    canvas.height = 32;
    const context = canvas.getContext('2d');
    if (!context) throw new Error('Canvas unavailable');
    context.fillStyle = 'rgb(0, 0, 200)';
    context.fillRect(0, 0, 32, 32);
    context.fillStyle = 'rgb(220, 0, 0)';
    context.fillRect(12, 12, 8, 8);
    const blob = await new Promise<Blob>((resolve, reject) =>
      canvas.toBlob(
        (value) => (value ? resolve(value) : reject(new Error('PNG failed'))),
        'image/png',
      ),
    );
    return [...new Uint8Array(await blob.arrayBuffer())];
  });
  await page.getByLabel('Open image file').setInputFiles({
    name: 'spot-heal.png',
    mimeType: 'image/png',
    buffer: Buffer.from(bytes),
  });
  await page.getByRole('button', { name: 'Fit' }).click();
  expect(await exportedPixel(page, 16, 16)).toEqual([220, 0, 0, 255]);

  await page.keyboard.press('j');
  await page.keyboard.press('Shift+J');
  await expect(page.getByLabel('Tool options', { exact: true })).toContainText(
    'Spot Healing Brush',
  );
  const pt = await documentPoint(page, 16, 16);
  await page.mouse.move(pt.x, pt.y);
  await page.mouse.down();
  await page.mouse.move(pt.x + 2, pt.y, { steps: 2 });
  await page.mouse.up();

  await expect.poll(async () => (await exportedPixel(page, 16, 16))[0]).toBeLessThan(220);
  await openHistoryPanel(page);
  await expect(
    page.getByRole('button', { name: /^History step Spot healing brush/ }),
  ).toBeVisible();
  await page.getByRole('button', { name: 'Undo' }).click();
  expect(await exportedPixel(page, 16, 16)).toEqual([220, 0, 0, 255]);
});

test('creates magnetic lasso selection along an edge', async ({ page }) => {
  await page.getByLabel('Open image file').setInputFiles({
    name: 'magnetic-lasso.png',
    mimeType: 'image/png',
    buffer: await makeQuadrantPng(page),
  });
  await page.getByRole('button', { name: 'Fit' }).click();
  await page.keyboard.press('l');
  await page.keyboard.press('Shift+L');
  await expect(page.getByLabel('Tool options', { exact: true })).toContainText('Magnetic Lasso');

  const path = await Promise.all([
    documentCoordinate(page, 4, 4),
    documentCoordinate(page, 20, 4),
    documentCoordinate(page, 12, 20),
    documentCoordinate(page, 4, 4),
  ]);
  const first = path[0];
  if (!first) throw new Error('Magnetic lasso start unavailable');
  await page.mouse.move(first.x, first.y);
  await page.mouse.down();
  for (const point of path.slice(1)) await page.mouse.move(point.x, point.y, { steps: 8 });
  await page.mouse.up();
  await expect(page.getByText(/Selection: \d+, \d+ · \d+ × \d+/)).toBeVisible();
  await openHistoryPanel(page);
  await expect(page.getByRole('button', { name: /^History step Combine selection/ })).toBeVisible();
});

test('paints with airbrush flow buildup with undo and history', async ({ page }) => {
  await page.getByLabel('Open image file').setInputFiles({
    name: 'airbrush.png',
    mimeType: 'image/png',
    buffer: await makePng(page, [0, 0, 200, 255]),
  });
  await page.getByRole('button', { name: 'Fit' }).click();
  await page.keyboard.press('b');
  await page.keyboard.press('Shift+B');
  await expect(page.getByLabel('Tool options', { exact: true })).toContainText('Airbrush');
  await page.getByLabel('Airbrush flow').fill('20');
  await page.getByLabel('Brush opacity').fill('100');
  const pt = await documentPoint(page, 16, 16);
  await page.mouse.move(pt.x, pt.y);
  await page.mouse.down();
  for (let step = 0; step < 12; step += 1) {
    await page.mouse.move(pt.x + step * 0.2, pt.y, { steps: 1 });
  }
  await page.mouse.up();
  await expect.poll(async () => (await exportedPixel(page, 16, 16))[0]).toBeGreaterThan(20);
  await openHistoryPanel(page);
  await expect(page.getByRole('button', { name: /^History step Airbrush/ })).toBeVisible();
  await page.getByRole('button', { name: 'Undo' }).click();
  expect(await exportedPixel(page, 16, 16)).toEqual([0, 0, 200, 255]);
});

test('heals with patch tool using destination drag and source offset with undo', async ({
  page,
}) => {
  const bytes = await page.evaluate(async () => {
    const canvas = document.createElement('canvas');
    canvas.width = 32;
    canvas.height = 32;
    const context = canvas.getContext('2d');
    if (!context) throw new Error('Canvas unavailable');
    context.fillStyle = 'rgb(0, 180, 0)';
    context.fillRect(0, 0, 32, 32);
    context.fillStyle = 'rgb(220, 0, 0)';
    context.fillRect(2, 10, 6, 6);
    const blob = await new Promise<Blob>((resolve, reject) =>
      canvas.toBlob(
        (value) => (value ? resolve(value) : reject(new Error('PNG failed'))),
        'image/png',
      ),
    );
    return [...new Uint8Array(await blob.arrayBuffer())];
  });
  await page.getByLabel('Open image file').setInputFiles({
    name: 'patch-tool.png',
    mimeType: 'image/png',
    buffer: Buffer.from(bytes),
  });
  await page.getByRole('button', { name: 'Fit' }).click();
  expect(await exportedPixel(page, 4, 12)).toEqual([220, 0, 0, 255]);

  await page.keyboard.press('j');
  await page.keyboard.press('Shift+J');
  await page.keyboard.press('Shift+J');
  await expect(page.getByLabel('Tool options', { exact: true })).toContainText('Patch');

  const destStart = await documentPoint(page, 2, 10);
  const destEnd = await documentPoint(page, 8, 16);
  await page.mouse.move(destStart.x, destStart.y);
  await page.mouse.down();
  await page.mouse.move(destEnd.x, destEnd.y);
  await page.mouse.up();

  const sourceStart = await documentPoint(page, 5, 13);
  const sourceEnd = await documentPoint(page, 21, 13);
  await page.mouse.move(sourceStart.x, sourceStart.y);
  await page.mouse.down();
  await page.mouse.move(sourceEnd.x, sourceEnd.y);
  await page.mouse.up();

  await expect.poll(async () => (await exportedPixel(page, 4, 12))[1]).toBeGreaterThan(80);
  await expect.poll(async () => (await exportedPixel(page, 4, 12))[0]).toBeLessThan(120);
  await openHistoryPanel(page);
  await expect(page.getByRole('button', { name: /^History step Patch tool/ })).toBeVisible();
  await page.getByRole('button', { name: 'Undo' }).click();
  expect(await exportedPixel(page, 4, 12)).toEqual([220, 0, 0, 255]);
});

test('edits text leading, tracking, and font with recovery', async ({ page }) => {
  await page.getByLabel('Open image file').setInputFiles({
    name: 'text.png',
    mimeType: 'image/png',
    buffer: await makePng(page, [30, 30, 30, 255]),
  });
  await page.getByRole('button', { name: 'Fit' }).click();
  await openMoreLayerActions(page);
  await page.getByRole('button', { name: 'Add text layer' }).click();
  await expect(page.getByLabel('Text content')).toBeVisible();
  await page.getByLabel('Text font size').fill('36');
  await page.getByLabel('Text leading').fill('1.5');
  await page.getByLabel('Text tracking').fill('50');
  await page.getByLabel('Text alignment').selectOption('center');
  await expect(page.getByText('Saved locally')).toBeVisible();
  await page.reload();
  await expect(page.getByText('Recovered your last local document.')).toBeVisible();
  await page
    .getByRole('button', { name: /Text|editable text/i })
    .first()
    .click();
  await expect(page.getByLabel('Text leading')).toHaveValue('1.5');
  await expect(page.getByLabel('Text tracking')).toHaveValue('50');
});

test('refines selection with Select and Mask overlay and layer-mask output', async ({ page }) => {
  await page.getByLabel('Open image file').setInputFiles({
    name: 'sam.png',
    mimeType: 'image/png',
    buffer: await makePng(page, [80, 80, 80, 255]),
  });
  await page.getByRole('button', { name: 'Fit' }).click();
  await page.getByRole('button', { name: 'Rectangular select' }).click();
  const start = await documentPoint(page, 2, 2);
  const end = await documentPoint(page, 20, 20);
  await page.mouse.move(start.x, start.y);
  await page.mouse.down();
  await page.mouse.move(end.x, end.y);
  await page.mouse.up();
  await page.getByRole('menuitem', { name: 'Select' }).click();
  await page.getByRole('menuitem', { name: 'Select and Mask…' }).click();
  await expect(page.getByRole('dialog', { name: 'Select and Mask' })).toBeVisible();
  // Overlay preview mode must be selectable (wires canvas overlay vs marching ants).
  await page.getByLabel('Select and Mask preview mode').selectOption('overlay');
  await expect(page.getByLabel('Select and Mask preview mode')).toHaveValue('overlay');
  await page.getByLabel('Select and Mask feather').fill('2');
  await page.getByLabel('Select and Mask contrast').fill('30');
  // Layer-mask output must commit selection+mask in one history step.
  await page.getByLabel('Select and Mask output').selectOption('layer-mask');
  await page.getByRole('button', { name: 'Apply', exact: true }).click();
  await expect(page.getByText(/Select and Mask applied/i)).toBeVisible();
  await openHistoryPanel(page);
  await expect(page.getByRole('button', { name: /^History step Select and Mask/ })).toBeVisible();
  // Single undo removes both refined selection and baked mask.
  await page.getByRole('button', { name: 'Undo' }).click();
  await expect(page.getByRole('button', { name: /^History step Select and Mask/ })).toHaveCount(0);
});

test('configures brush dynamics pressure spacing and jitter with persistence', async ({ page }) => {
  await page.getByLabel('Open image file').setInputFiles({
    name: 'brush-dyn.png',
    mimeType: 'image/png',
    buffer: await makePng(page, [40, 40, 40, 255]),
  });
  await page.getByRole('button', { name: 'Fit' }).click();
  await page.keyboard.press('b');
  await expect(page.getByLabel('Tool options', { exact: true })).toContainText('Brush');
  const dynamics = page.getByLabel('Brush dynamics', { exact: true });
  await expect(dynamics).toBeVisible();
  await dynamics.locator('summary').click();

  const sizePressure = page.getByLabel('Pressure controls size');
  const opacityPressure = page.getByLabel('Pressure controls opacity');
  const spacing = page.getByLabel('Brush spacing');
  const sizeJitter = page.getByLabel('Size jitter');
  const opacityJitter = page.getByLabel('Opacity jitter');

  await expect(sizePressure).toBeChecked();
  await sizePressure.uncheck();
  await opacityPressure.check();
  await spacing.fill('40');
  await sizeJitter.fill('15');
  await opacityJitter.fill('10');

  await expect(sizePressure).not.toBeChecked();
  await expect(opacityPressure).toBeChecked();
  await expect(spacing).toHaveValue('40');
  await expect(sizeJitter).toHaveValue('15');
  await expect(opacityJitter).toHaveValue('10');

  // Paint a short stroke so dynamics options are live on the brush path.
  const a = await documentPoint(page, 4, 4);
  const b = await documentPoint(page, 18, 18);
  await page.mouse.move(a.x, a.y);
  await page.mouse.down();
  await page.mouse.move(b.x, b.y);
  await page.mouse.up();
  await openHistoryPanel(page);
  await expect(page.getByRole('button', { name: /^History step Brush/ })).toBeVisible();

  // localStorage-backed tool settings survive reload (options rehydrate).
  await page.reload();
  await expect(page.getByText('Recovered your last local document.')).toBeVisible();
  await page.keyboard.press('b');
  await page.getByLabel('Brush dynamics', { exact: true }).locator('summary').click();
  await expect(page.getByLabel('Pressure controls size')).not.toBeChecked();
  await expect(page.getByLabel('Pressure controls opacity')).toBeChecked();
  await expect(page.getByLabel('Brush spacing')).toHaveValue('40');
  await expect(page.getByLabel('Size jitter')).toHaveValue('15');
  await expect(page.getByLabel('Opacity jitter')).toHaveValue('10');
});

test('applies perspective distort with undo and recovery', async ({ page }) => {
  await page.getByLabel('Open image file').setInputFiles({
    name: 'perspective.png',
    mimeType: 'image/png',
    buffer: await makePng(page, [40, 120, 200, 255]),
  });
  await page.getByRole('button', { name: 'Fit' }).click();
  const before = await exportedPixel(page, 8, 8);
  await page.getByRole('menuitem', { name: 'Edit' }).click();
  await page.getByRole('menuitem', { name: 'Perspective Distort…' }).click();
  await expect(page.getByRole('dialog', { name: 'Perspective Distort' })).toBeVisible();
  // Modeless panel must not inert the page (canvas handles stay reachable).
  await expect(page.getByRole('dialog', { name: 'Perspective Distort' })).toHaveAttribute(
    'data-modal',
    'false',
  );
  await page.getByLabel('Bottom-right X').fill('40');
  await page.getByLabel('Bottom-right Y').fill('40');
  await page.getByRole('button', { name: 'Apply perspective' }).click();
  await expect(page.getByText(/Perspective distort applied/i)).toBeVisible();
  await page.getByRole('button', { name: 'Undo' }).click();
  expect(await exportedPixel(page, 8, 8)).toEqual(before);
  await page.getByRole('button', { name: 'Redo' }).click();
  await expect(page.getByText('Saved locally')).toBeVisible();
  await page.reload();
  await expect(page.getByText('Recovered your last local document.')).toBeVisible();
});

test('drags perspective corner handle on canvas while panel is open', async ({ page }) => {
  await page.getByLabel('Open image file').setInputFiles({
    name: 'perspective-drag.png',
    mimeType: 'image/png',
    buffer: await makePng(page, [40, 120, 200, 255]),
  });
  await page.getByRole('button', { name: 'Fit' }).click();
  await page.getByRole('menuitem', { name: 'Edit' }).click();
  await page.getByRole('menuitem', { name: 'Perspective Distort…' }).click();
  const panel = page.getByRole('dialog', { name: 'Perspective Distort' });
  await expect(panel).toBeVisible();
  await expect(panel).toHaveAttribute('data-modal', 'false');

  // Identity SE corner sits at document (32, 32) on a 32² imported layer.
  await expect(page.getByLabel('Bottom-right X')).toHaveValue('32');
  await expect(page.getByLabel('Bottom-right Y')).toHaveValue('32');

  const se = await documentCoordinate(page, 32, 32);
  const target = await documentCoordinate(page, 20, 18);
  // Prove canvas is not inert under the modeless panel: real pointer drag on handle.
  await page.mouse.move(se.x, se.y);
  await page.mouse.down();
  await page.mouse.move(target.x, target.y, { steps: 8 });
  await page.mouse.up();

  // Corner numbers must update from the canvas drag path (not dialog fields).
  await expect
    .poll(async () => Number(await page.getByLabel('Bottom-right X').inputValue()))
    .toBeLessThan(30);
  await expect
    .poll(async () => Number(await page.getByLabel('Bottom-right Y').inputValue()))
    .toBeLessThan(30);

  await page.getByRole('button', { name: 'Apply perspective' }).click();
  await expect(page.getByText(/Perspective distort applied/i)).toBeVisible();
  await openHistoryPanel(page);
  await expect(
    page.getByRole('button', { name: /^History step Perspective distort/ }),
  ).toBeVisible();
});

test('aligns and links multi-selected layers with recovery', async ({ page }) => {
  await page.getByLabel('Open image file').setInputFiles({
    name: 'align.png',
    mimeType: 'image/png',
    buffer: await makePng(page, [200, 40, 40, 255]),
  });
  await page.getByRole('button', { name: 'Fit' }).click();
  await openMoreLayerActions(page);
  await page.getByRole('button', { name: 'Duplicate selected layer' }).click();
  await expect(page.getByText('Layers · 2')).toBeVisible();
  // Multi-select: original + copy (merge-test pattern).
  await page.getByRole('button', { name: 'align.png', exact: true }).click();
  await page.getByRole('button', { name: 'align.png copy', exact: true }).click({
    modifiers: ['Control'],
  });
  await page.getByRole('menuitem', { name: 'Layer' }).click();
  await page.getByRole('menuitem', { name: 'Align' }).click();
  await page.getByRole('menuitem', { name: 'Align Left Edges' }).click();
  await expect(page.getByText(/Layers aligned/i)).toBeVisible();
  await page.getByRole('menuitem', { name: 'Layer' }).click();
  await page.getByRole('menuitem', { name: 'Link Layers', exact: true }).click();
  await expect(page.getByText(/Layers linked/i)).toBeVisible();
  await expect(page.getByText('Saved locally')).toBeVisible();
  await page.reload();
  await expect(page.getByText('Recovered your last local document.')).toBeVisible();
  await expect(page.getByText('Layers · 2')).toBeVisible();
});

test('applies destructive Curves and persists Curves adjustment layer', async ({ page }) => {
  await page.getByLabel('Open image file').setInputFiles({
    name: 'curves.png',
    mimeType: 'image/png',
    buffer: await makePng(page, [128, 128, 128, 255]),
  });
  await page.getByRole('button', { name: 'Fit' }).click();
  expect(await exportedPixel(page, 4, 4)).toEqual([128, 128, 128, 255]);

  // Image → Adjustments → Curves…
  await page.getByRole('menuitem', { name: 'Image' }).click();
  await page.getByRole('menuitem', { name: 'Adjustments' }).click();
  await page.getByRole('menuitem', { name: 'Curves…' }).click();
  const curvesDialog = page.getByRole('dialog', { name: 'Curves' });
  await expect(curvesDialog).toBeVisible();
  const rgbGraph = curvesDialog.getByRole('application', { name: 'RGB Curves graph' });
  await expect(rgbGraph.locator('.curves-histogram')).toHaveCount(3);
  await curvesDialog.getByRole('button', { name: 'Targeted adjustment tool' }).click();
  await expect(curvesDialog).toHaveAttribute('data-modal', 'false');
  const targetedPoint = await documentPoint(page, 4, 4);
  await page.mouse.move(targetedPoint.x, targetedPoint.y);
  await page.mouse.down();
  await page.mouse.move(targetedPoint.x, targetedPoint.y - 30, { steps: 3 });
  await page.mouse.up();
  await expect(curvesDialog.getByText(/Input 128 · Output 158/)).toBeVisible();
  await expect(page.getByLabel('Curve point 2 output')).toHaveValue('158');
  await expect(curvesDialog.getByLabel('Curves preset')).toHaveValue('custom');
  const importedAcv = Buffer.from(
    '0004000400030000000000dc008000ff00ff00020000000000ff00ff00020000000000ff00ff00020000000000ff00ff',
    'hex',
  );
  await curvesDialog.getByLabel('Curves Load ACV preset').setInputFiles({
    name: 'lift-midtones.acv',
    mimeType: 'application/octet-stream',
    buffer: importedAcv,
  });
  await expect(curvesDialog.getByText('Loaded lift-midtones.acv.')).toBeVisible();
  await expect(curvesDialog.getByLabel('Curve point 2 output')).toHaveValue('220');
  const downloadPromise = page.waitForEvent('download');
  await curvesDialog.getByRole('button', { name: 'Save ACV' }).click();
  const acvDownload = await downloadPromise;
  expect(acvDownload.suggestedFilename()).toBe('curves.acv');
  const acvDownloadPath = await acvDownload.path();
  expect((await readFile(acvDownloadPath)).subarray(0, 4)).toEqual(Buffer.from([0, 4, 0, 4]));
  await page.getByRole('button', { name: 'Apply Curves' }).click();
  await expect.poll(async () => (await exportedPixel(page, 4, 4))[0]).toBeGreaterThan(128);

  await page.getByRole('button', { name: 'Undo' }).click();
  expect(await exportedPixel(page, 4, 4)).toEqual([128, 128, 128, 255]);

  await page.getByLabel('Open image file').setInputFiles({
    name: 'curves-channel.png',
    mimeType: 'image/png',
    buffer: await makePng(page, [100, 150, 200, 255]),
  });
  await page.getByRole('button', { name: 'Fit' }).click();
  await page.getByRole('menuitem', { name: 'Image' }).click();
  await page.getByRole('menuitem', { name: 'Adjustments' }).click();
  await page.getByRole('menuitem', { name: 'Curves…' }).click();
  await page.getByRole('button', { name: 'Reset curve' }).click();
  await page.getByLabel('Curves channel').selectOption('red');
  const redGraph = page.getByRole('application', { name: 'RED Curves graph' });
  await expect(redGraph.locator('.curves-histogram.histogram-red')).toHaveCount(1);
  await page.getByRole('button', { name: 'Add point' }).click();
  await page.getByLabel('Curve point 3 output').fill('220');
  await page.getByRole('button', { name: 'Apply Curves' }).click();
  await expect.poll(async () => (await exportedPixel(page, 4, 4))[0]).toBeGreaterThan(150);
  expect((await exportedPixel(page, 4, 4))[1]).toBe(150);

  // Nondestructive Curves adjustment layer via Layer menu.
  await page.getByRole('menuitem', { name: 'Layer' }).click();
  await page.getByRole('menuitem', { name: /New Adjustment Layer|Adjustment/i }).click();
  await page.getByRole('menuitem', { name: 'Curves…' }).click();
  await expect(page.getByText(/Curves adjustment layer added/i)).toBeVisible();
  await expect(page.getByRole('button', { name: 'Curves · adjustment' })).toBeVisible();
  const curvesProperties = page.getByRole('tabpanel', { name: 'Properties' });
  await curvesProperties.getByLabel('Adjustment Curves preset').selectOption('lighter');
  await expect.poll(async () => (await exportedPixel(page, 4, 4))[1]).toBeGreaterThan(150);
  await curvesProperties.getByLabel('Adjustment Curves preset').selectOption('default');
  await curvesProperties.getByLabel('Curves channel').selectOption('red');
  await expect(
    curvesProperties.getByRole('application', { name: 'RED adjustment Curves graph' }),
  ).toBeVisible();
  await curvesProperties.getByRole('button', { name: 'Add point' }).click();
  await curvesProperties.getByLabel('Adjustment curve point 3 output').fill('220');
  await expect.poll(async () => (await exportedPixel(page, 4, 4))[0]).toBeGreaterThan(160);
  expect((await exportedPixel(page, 4, 4))[1]).toBe(150);
  await expect(page.getByText('Saved locally')).toBeVisible();
  await page.reload();
  await expect(page.getByText('Recovered your last local document.')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Curves · adjustment' })).toBeVisible();
  const curvesPropertiesAfterReload = page.getByRole('tabpanel', { name: 'Properties' });
  await curvesPropertiesAfterReload.getByLabel('Curves channel').selectOption('green');
  await curvesPropertiesAfterReload
    .getByRole('button', { name: 'Targeted adjustment tool' })
    .click();
  const adjustmentTargetPoint = await documentPoint(page, 4, 4);
  await page.mouse.move(adjustmentTargetPoint.x, adjustmentTargetPoint.y);
  await page.mouse.down();
  await page.mouse.move(adjustmentTargetPoint.x, adjustmentTargetPoint.y + 60, { steps: 4 });
  await page.mouse.up();
  await expect(curvesPropertiesAfterReload.getByText(/Input 150 · Output 90/)).toBeVisible();
  await expect.poll(async () => (await exportedPixel(page, 4, 4))[1]).toBeLessThan(150);
  expect((await exportedPixel(page, 4, 4))[0]).toBeGreaterThan(160);
  await page.getByRole('button', { name: 'Undo' }).click();
  expect((await exportedPixel(page, 4, 4))[1]).toBe(150);
  await page.getByRole('button', { name: 'Redo' }).click();
  await expect.poll(async () => (await exportedPixel(page, 4, 4))[1]).toBeLessThan(150);
});

test('applies Photoshop-style Brightness/Contrast destructively and as a persistent adjustment layer', async ({
  page,
}) => {
  await page.getByLabel('Open image file').setInputFiles({
    name: 'brightness-contrast.png',
    mimeType: 'image/png',
    buffer: await makePng(page, [64, 64, 64, 255]),
  });
  await page.getByRole('button', { name: 'Fit' }).click();

  await page.getByRole('menuitem', { name: 'Image' }).click();
  await page.getByRole('menuitem', { name: 'Adjustments' }).click();
  await page.getByRole('menuitem', { name: 'Brightness/Contrast…' }).click();
  const dialog = page.getByRole('dialog', { name: 'Brightness / Contrast' });
  const brightness = dialog.getByRole('slider', { name: 'Brightness', exact: true });
  const contrast = dialog.getByRole('slider', { name: 'Contrast', exact: true });
  await expect(brightness).toHaveValue('0');
  await expect(brightness).toHaveAttribute('min', '-150');
  await expect(brightness).toHaveAttribute('max', '150');
  await expect(contrast).toHaveValue('0');
  await expect(contrast).toHaveAttribute('min', '-50');
  await expect(contrast).toHaveAttribute('max', '100');
  await expect(dialog.getByRole('checkbox', { name: 'Use Legacy' })).not.toBeChecked();
  await brightness.fill('75');
  await page.getByRole('button', { name: 'Apply Brightness / Contrast' }).click();
  expect(await exportedPixel(page, 4, 4)).toEqual([160, 160, 160, 255]);
  await page.getByRole('button', { name: 'Undo' }).click();
  expect(await exportedPixel(page, 4, 4)).toEqual([64, 64, 64, 255]);

  await page.getByRole('menuitem', { name: 'Layer' }).click();
  await page.getByRole('menuitem', { name: /New Adjustment Layer|Adjustment/i }).click();
  await page.getByRole('menuitem', { name: 'Brightness/Contrast…' }).click();
  await expect(page.getByText(/Brightness\/Contrast adjustment layer added/i)).toBeVisible();
  await expect(
    page.getByRole('button', { name: 'Brightness/Contrast · adjustment' }),
  ).toBeVisible();

  const properties = page.getByRole('tabpanel', { name: 'Properties' });
  await properties.getByRole('slider', { name: 'Brightness', exact: true }).fill('75');
  await expect.poll(async () => await exportedPixel(page, 4, 4)).toEqual([160, 160, 160, 255]);
  await properties.getByRole('checkbox', { name: 'Use Legacy' }).check();
  await expect.poll(async () => await exportedPixel(page, 4, 4)).toEqual([192, 192, 192, 255]);
  await properties.getByRole('slider', { name: 'Contrast', exact: true }).fill('-50');
  await expect.poll(async () => await exportedPixel(page, 4, 4)).toEqual([128, 128, 128, 255]);
  await properties.getByRole('slider', { name: 'Contrast', exact: true }).fill('0');

  await expect(page.getByText('Saved locally')).toBeVisible();
  await page.reload();
  await expect(page.getByText('Recovered your last local document.')).toBeVisible();
  await expect(
    page.getByRole('button', { name: 'Brightness/Contrast · adjustment' }),
  ).toBeVisible();
  await expect.poll(async () => await exportedPixel(page, 4, 4)).toEqual([192, 192, 192, 255]);
  await expect(
    page
      .getByRole('tabpanel', { name: 'Properties' })
      .getByRole('checkbox', { name: 'Use Legacy' }),
  ).toBeChecked();
});

test('applies Photoshop-style Exposure destructively and as a persistent adjustment layer', async ({
  page,
}) => {
  await page.getByLabel('Open image file').setInputFiles({
    name: 'exposure.png',
    mimeType: 'image/png',
    buffer: await makePng(page, [64, 64, 64, 255]),
  });
  await page.getByRole('button', { name: 'Fit' }).click();

  await page.getByRole('menuitem', { name: 'Image' }).click();
  await page.getByRole('menuitem', { name: 'Adjustments' }).click();
  await page.getByRole('menuitem', { name: 'Exposure…' }).click();
  const exposureDialog = page.getByRole('dialog', { name: 'Exposure' });
  const exposureInput = exposureDialog.getByRole('spinbutton', { name: 'Exposure', exact: true });
  await expect(exposureDialog).toBeVisible();
  await expect(exposureInput).toHaveValue('0');
  await expect(exposureDialog.getByLabel('Offset')).toHaveValue('0');
  await expect(exposureDialog.getByLabel('Gamma Correction')).toHaveValue('1');
  await exposureInput.fill('1');
  await page.getByRole('button', { name: 'Apply Exposure' }).click();
  expect(await exportedPixel(page, 4, 4)).toEqual([128, 128, 128, 255]);
  await page.getByRole('button', { name: 'Undo' }).click();
  expect(await exportedPixel(page, 4, 4)).toEqual([64, 64, 64, 255]);

  await page.getByRole('menuitem', { name: 'Layer' }).click();
  await page.getByRole('menuitem', { name: /New Adjustment Layer|Adjustment/i }).click();
  await page.getByRole('menuitem', { name: 'Exposure…' }).click();
  await expect(page.getByText(/Exposure adjustment layer added/i)).toBeVisible();
  await expect(page.getByRole('button', { name: 'Exposure · adjustment' })).toBeVisible();

  const properties = page.getByRole('tabpanel', { name: 'Properties' });
  await properties.getByRole('spinbutton', { name: 'Exposure', exact: true }).fill('1');
  await expect.poll(async () => await exportedPixel(page, 4, 4)).toEqual([128, 128, 128, 255]);
  await properties.getByRole('spinbutton', { name: 'Exposure', exact: true }).fill('0');
  await properties.getByLabel('Offset').fill('0.1');
  await expect.poll(async () => (await exportedPixel(page, 4, 4))[0]).toBe(90);
  await properties.getByLabel('Offset').fill('0');
  await properties.getByLabel('Gamma Correction').fill('2');
  await expect.poll(async () => (await exportedPixel(page, 4, 4))[0]).toBe(128);

  await expect(page.getByText('Saved locally')).toBeVisible();
  await page.reload();
  await expect(page.getByText('Recovered your last local document.')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Exposure · adjustment' })).toBeVisible();
  await expect.poll(async () => (await exportedPixel(page, 4, 4))[0]).toBe(128);
  const recoveredProperties = page.getByRole('tabpanel', { name: 'Properties' });
  await expect(recoveredProperties.getByLabel('Gamma Correction')).toHaveValue('2');
});

test('builds up airbrush opacity while the pointer stays stationary', async ({ page }) => {
  await page.getByLabel('Open image file').setInputFiles({
    name: 'airbrush-dwell.png',
    mimeType: 'image/png',
    buffer: await makePng(page, [0, 0, 200, 255]),
  });
  await page.getByRole('button', { name: 'Fit' }).click();
  await page.keyboard.press('b');
  await page.keyboard.press('Shift+B');
  await page.getByLabel('Airbrush flow').fill('30');
  await page.getByLabel('Brush opacity').fill('100');
  const pt = await documentPoint(page, 20, 20);
  await page.mouse.move(pt.x, pt.y);
  await page.mouse.down();
  await page.waitForTimeout(600);
  await page.mouse.up();
  await expect.poll(async () => (await exportedPixel(page, 20, 20))[1]).toBeGreaterThan(30);
});

test('edits, masks, persists, and exports nondestructive adjustment layers', async ({ page }) => {
  await page.getByLabel('Open image file').setInputFiles({
    name: 'adjustments.png',
    mimeType: 'image/png',
    buffer: await makePng(page, [40, 80, 120, 255]),
  });
  await page.getByRole('button', { name: 'Rectangular select' }).click();
  const start = await documentPoint(page, 0, 0);
  const end = await documentPoint(page, 15, 31);
  await page.mouse.move(start.x, start.y);
  await page.mouse.down();
  await page.mouse.move(end.x, end.y);
  await page.mouse.up();

  await page.getByRole('button', { name: 'New Adjustment Layer' }).click();
  await page.getByRole('menuitem', { name: 'Invert', exact: true }).click();
  await expect(page.getByText('Invert adjustment layer added.')).toBeVisible();
  await expectSelectedLayerName(page, /adjustment/);
  await page.getByRole('button', { name: 'Add mask from selection' }).click();
  expect(await exportedPixel(page, 4, 16)).toEqual([215, 175, 135, 255]);
  expect(await exportedPixel(page, 28, 16)).toEqual([40, 80, 120, 255]);

  await page.getByLabel('Adjustment mask density').fill('50');
  await page.getByLabel('Adjustment mask density').blur();
  expect(await exportedPixel(page, 4, 16)).toEqual([215, 175, 135, 255]);
  expect(await exportedPixel(page, 28, 16)).toEqual([128, 128, 128, 255]);

  await page.getByRole('button', { name: 'Undo' }).click();
  expect(await exportedPixel(page, 4, 16)).toEqual([215, 175, 135, 255]);
  expect(await exportedPixel(page, 28, 16)).toEqual([40, 80, 120, 255]);
  await page.getByRole('button', { name: 'Redo' }).click();
  expect(await exportedPixel(page, 28, 16)).toEqual([128, 128, 128, 255]);

  await page.getByRole('button', { name: 'adjustments.png', exact: true }).click();
  await page.getByLabel('Layer blend mode').selectOption('hue');
  await page.getByRole('checkbox', { name: 'Position', exact: true }).check();
  await page.getByLabel('Layer position X').fill('5');
  await page.getByLabel('Layer position X').press('Enter');
  await expect(page.getByText(/blocked by the layer lock/i)).toBeVisible();
  await expect(page.getByLabel('Layer position X')).toHaveValue('0');

  await expect(page.getByText('Saved locally')).toBeVisible();
  await page.reload();
  await expect(page.getByText('Recovered your last local document.')).toBeVisible();
  await expect(page.getByRole('button', { name: /^Invert.*adjustment$/ })).toBeVisible();
  await expect(page.getByRole('button', { name: /adjustments\.png.*hue/ })).toBeVisible();
  expect(await exportedPixel(page, 28, 16)).toEqual([128, 128, 128, 255]);
});

test('fills with paint bucket using tolerance, undo, and history', async ({ page }) => {
  const bytes = await page.evaluate(async () => {
    const canvas = document.createElement('canvas');
    canvas.width = 32;
    canvas.height = 32;
    const context = canvas.getContext('2d');
    if (!context) throw new Error('Canvas unavailable');
    context.fillStyle = 'rgb(0, 0, 200)';
    context.fillRect(0, 0, 32, 32);
    context.fillStyle = 'rgb(220, 0, 0)';
    context.fillRect(12, 12, 8, 8);
    const blob = await new Promise<Blob>((resolve, reject) =>
      canvas.toBlob(
        (value) => (value ? resolve(value) : reject(new Error('PNG failed'))),
        'image/png',
      ),
    );
    return [...new Uint8Array(await blob.arrayBuffer())];
  });
  await page.getByLabel('Open image file').setInputFiles({
    name: 'paint-bucket.png',
    mimeType: 'image/png',
    buffer: Buffer.from(bytes),
  });
  await page.getByRole('button', { name: 'Fit' }).click();
  expect(await exportedPixel(page, 4, 4)).toEqual([0, 0, 200, 255]);

  await page.getByRole('button', { name: 'Paint bucket' }).click();
  await expect(page.getByLabel('Tool options', { exact: true })).toContainText('Paint Bucket');
  await page.getByLabel('Brush color').fill('#00ff00');
  const pt = await documentPoint(page, 4, 4);
  await page.mouse.click(pt.x, pt.y);
  await expect(page.getByText('Paint bucket fill applied.')).toBeVisible();
  expect((await exportedPixel(page, 4, 4))[1]).toBeGreaterThan(200);
  expect((await exportedPixel(page, 16, 16))[0]).toBeGreaterThan(200);

  await openHistoryPanel(page);
  await expect(page.getByRole('button', { name: /^History step Paint bucket/ })).toBeVisible();
  await page.getByRole('button', { name: 'Undo' }).click();
  expect(await exportedPixel(page, 4, 4)).toEqual([0, 0, 200, 255]);
});

test('paints a linear gradient with foreground and background colors', async ({ page }) => {
  const bytes = await page.evaluate(async () => {
    const canvas = document.createElement('canvas');
    canvas.width = 32;
    canvas.height = 32;
    const context = canvas.getContext('2d');
    if (!context) throw new Error('Canvas unavailable');
    context.fillStyle = 'rgb(40, 40, 40)';
    context.fillRect(0, 0, 32, 32);
    const blob = await new Promise<Blob>((resolve, reject) =>
      canvas.toBlob(
        (value) => (value ? resolve(value) : reject(new Error('PNG failed'))),
        'image/png',
      ),
    );
    return [...new Uint8Array(await blob.arrayBuffer())];
  });
  await page.getByLabel('Open image file').setInputFiles({
    name: 'gradient-base.png',
    mimeType: 'image/png',
    buffer: Buffer.from(bytes),
  });
  await page.getByRole('button', { name: 'Fit' }).click();
  await page.keyboard.press('g');
  await page.keyboard.press('Shift+G');
  await expect(page.getByLabel('Tool options', { exact: true })).toContainText('Gradient');
  await page.locator('.rail-colors input[aria-label="Foreground color"]').fill('#ff0000');
  await page.locator('.background-swatch').fill('#0000ff');
  const start = await documentPoint(page, 4, 16);
  const end = await documentPoint(page, 28, 16);
  await page.mouse.move(start.x, start.y);
  await page.mouse.down();
  await page.mouse.move(end.x, end.y, { steps: 6 });
  await page.mouse.up();
  await expect(page.getByText('Linear gradient applied.')).toBeVisible();
  expect((await exportedPixel(page, 4, 16))[0]).toBeGreaterThan(200);
  expect((await exportedPixel(page, 28, 16))[2]).toBeGreaterThan(200);
  await openHistoryPanel(page);
  await expect(page.getByRole('button', { name: /^History step Gradient/ })).toBeVisible();
});

test('paints a radial gradient with undo history', async ({ page }) => {
  const bytes = await page.evaluate(async () => {
    const canvas = document.createElement('canvas');
    canvas.width = 32;
    canvas.height = 32;
    const context = canvas.getContext('2d');
    if (!context) throw new Error('Canvas unavailable');
    context.fillStyle = 'rgb(20, 20, 20)';
    context.fillRect(0, 0, 32, 32);
    const blob = await new Promise<Blob>((resolve, reject) =>
      canvas.toBlob(
        (value) => (value ? resolve(value) : reject(new Error('PNG failed'))),
        'image/png',
      ),
    );
    return [...new Uint8Array(await blob.arrayBuffer())];
  });
  await page.getByLabel('Open image file').setInputFiles({
    name: 'radial-base.png',
    mimeType: 'image/png',
    buffer: Buffer.from(bytes),
  });
  await page.getByRole('button', { name: 'Fit' }).click();
  await page.keyboard.press('g');
  await page.keyboard.press('Shift+G');
  await page.locator('#gradient-style').selectOption('radial');
  await page.locator('.rail-colors input[aria-label="Foreground color"]').fill('#ff0000');
  await page.locator('.background-swatch').fill('#0000ff');
  const start = await documentPoint(page, 16, 16);
  const end = await documentPoint(page, 28, 16);
  await page.mouse.move(start.x, start.y);
  await page.mouse.down();
  await page.mouse.move(end.x, end.y, { steps: 6 });
  await page.mouse.up();
  await expect(page.getByText('Radial gradient applied.')).toBeVisible();
  expect((await exportedPixel(page, 16, 16))[0]).toBeGreaterThan(200);
  expect((await exportedPixel(page, 28, 16))[2]).toBeGreaterThan(150);
  await openHistoryPanel(page);
  await expect(page.getByRole('button', { name: /^History step Radial Gradient/ })).toBeVisible();
  await page.getByRole('button', { name: 'Undo' }).click();
  expect((await exportedPixel(page, 16, 16))[0]).toBeLessThan(40);
});

test('applies destructive Vibrance and persists Vibrance adjustment layer', async ({ page }) => {
  const bytes = await page.evaluate(async () => {
    const canvas = document.createElement('canvas');
    canvas.width = 16;
    canvas.height = 16;
    const context = canvas.getContext('2d');
    if (!context) throw new Error('Canvas unavailable');
    // Muted warm tone so vibrance has headroom.
    context.fillStyle = 'rgb(160, 140, 130)';
    context.fillRect(0, 0, 16, 16);
    const blob = await new Promise<Blob>((resolve, reject) =>
      canvas.toBlob(
        (value) => (value ? resolve(value) : reject(new Error('PNG failed'))),
        'image/png',
      ),
    );
    return [...new Uint8Array(await blob.arrayBuffer())];
  });
  await page.getByLabel('Open image file').setInputFiles({
    name: 'vibrance-base.png',
    mimeType: 'image/png',
    buffer: Buffer.from(bytes),
  });
  await page.getByRole('button', { name: 'Fit' }).click();
  const before = await exportedPixel(page, 4, 4);
  expect(before[0]).toBe(160);

  await page.getByRole('menuitem', { name: 'Image' }).click();
  await page.getByRole('menuitem', { name: 'Adjustments' }).click();
  await page.getByRole('menuitem', { name: 'Vibrance…' }).click();
  const dialog = page.getByRole('dialog', { name: 'Vibrance' });
  await expect(dialog).toBeVisible();
  await dialog.getByRole('slider', { name: 'Vibrance amount' }).fill('80');
  await page.getByRole('button', { name: 'Apply Vibrance' }).click();
  await expect
    .poll(async () => {
      const pixel = await exportedPixel(page, 4, 4);
      return Math.abs(pixel[0]! - before[0]!) + Math.abs(pixel[1]! - before[1]!);
    })
    .toBeGreaterThan(0);

  await page.getByRole('button', { name: 'Undo' }).click();
  expect(await exportedPixel(page, 4, 4)).toEqual(before);

  await page.getByRole('menuitem', { name: 'Layer' }).click();
  await page.getByRole('menuitem', { name: /New Adjustment Layer|Adjustment/i }).click();
  await page.getByRole('menuitem', { name: 'Vibrance…' }).click();
  await expect(page.getByText(/Vibrance adjustment layer added/i)).toBeVisible();
  await expect(page.getByRole('button', { name: 'Vibrance · adjustment' })).toBeVisible();
  const properties = page.getByRole('tabpanel', { name: 'Properties' });
  await properties.getByRole('slider', { name: 'Vibrance amount' }).fill('90');
  await expect
    .poll(async () => {
      const pixel = await exportedPixel(page, 4, 4);
      return Math.abs(pixel[0]! - before[0]!) + Math.abs(pixel[1]! - before[1]!);
    })
    .toBeGreaterThan(0);
  await expect(page.getByText('Saved locally')).toBeVisible();
  await page.reload();
  await expect(page.getByText('Recovered your last local document.')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Vibrance · adjustment' })).toBeVisible();
});

test('applies destructive Color Balance and persists its tonal range controls', async ({
  page,
}) => {
  const bytes = await page.evaluate(async () => {
    const canvas = document.createElement('canvas');
    canvas.width = 16;
    canvas.height = 16;
    const context = canvas.getContext('2d');
    if (!context) throw new Error('Canvas unavailable');
    context.fillStyle = 'rgb(128, 128, 128)';
    context.fillRect(0, 0, 16, 16);
    const blob = await new Promise<Blob>((resolve, reject) =>
      canvas.toBlob(
        (value) => (value ? resolve(value) : reject(new Error('PNG failed'))),
        'image/png',
      ),
    );
    return [...new Uint8Array(await blob.arrayBuffer())];
  });
  await page.getByLabel('Open image file').setInputFiles({
    name: 'color-balance-base.png',
    mimeType: 'image/png',
    buffer: Buffer.from(bytes),
  });
  await page.getByRole('button', { name: 'Fit' }).click();

  await page.getByRole('menuitem', { name: 'Image' }).click();
  await page.getByRole('menuitem', { name: 'Adjustments' }).click();
  await page.getByRole('menuitem', { name: 'Color Balance…' }).click();
  const dialog = page.getByRole('dialog', { name: 'Color Balance' });
  await expect(dialog.getByRole('combobox', { name: 'Tone range' })).toHaveValue('midtones');
  await expect(dialog.getByRole('checkbox', { name: 'Preserve Luminosity' })).toBeChecked();
  await dialog.getByRole('checkbox', { name: 'Preserve Luminosity' }).uncheck();
  await dialog.getByRole('slider', { name: 'Cyan / Red' }).fill('20');
  await page.getByRole('button', { name: 'Apply Color Balance' }).click();
  expect(await exportedPixel(page, 4, 4)).toEqual([179, 128, 128, 255]);
  await page.getByRole('button', { name: 'Undo' }).click();
  expect(await exportedPixel(page, 4, 4)).toEqual([128, 128, 128, 255]);

  await page.getByRole('menuitem', { name: 'Layer' }).click();
  await page.getByRole('menuitem', { name: /New Adjustment Layer|Adjustment/i }).click();
  await page.getByRole('menuitem', { name: 'Color Balance…' }).click();
  await expect(page.getByText(/Color Balance adjustment layer added/i)).toBeVisible();
  const properties = page.getByRole('tabpanel', { name: 'Properties' });
  await properties.getByRole('checkbox', { name: 'Preserve Luminosity' }).uncheck();
  await properties.getByRole('slider', { name: 'Magenta / Green' }).fill('20');
  expect(await exportedPixel(page, 4, 4)).toEqual([128, 179, 128, 255]);
  await properties.getByRole('combobox', { name: 'Tone range' }).selectOption('highlights');
  await properties.getByRole('slider', { name: 'Yellow / Blue' }).fill('-17');
  await expect(page.getByText('Saved locally')).toBeVisible();
  await page.reload();
  await expect(page.getByText('Recovered your last local document.')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Color Balance · adjustment' })).toBeVisible();
  await expect(
    page.getByRole('tabpanel', { name: 'Properties' }).getByRole('checkbox', {
      name: 'Preserve Luminosity',
    }),
  ).not.toBeChecked();
});

test('applies destructive Channel Mixer and persists RGB and monochrome matrices', async ({
  page,
}) => {
  const bytes = await page.evaluate(async () => {
    const canvas = document.createElement('canvas');
    canvas.width = 16;
    canvas.height = 16;
    const context = canvas.getContext('2d');
    if (!context) throw new Error('Canvas unavailable');
    context.fillStyle = 'rgb(100, 50, 200)';
    context.fillRect(0, 0, 16, 16);
    const blob = await new Promise<Blob>((resolve, reject) =>
      canvas.toBlob(
        (value) => (value ? resolve(value) : reject(new Error('PNG failed'))),
        'image/png',
      ),
    );
    return [...new Uint8Array(await blob.arrayBuffer())];
  });
  await page.getByLabel('Open image file').setInputFiles({
    name: 'channel-mixer-base.png',
    mimeType: 'image/png',
    buffer: Buffer.from(bytes),
  });
  await page.getByRole('button', { name: 'Fit' }).click();

  await page.getByRole('menuitem', { name: 'Image' }).click();
  await page.getByRole('menuitem', { name: 'Adjustments' }).click();
  await page.getByRole('menuitem', { name: 'Channel Mixer…' }).click();
  const dialog = page.getByRole('dialog', { name: 'Channel Mixer' });
  await expect(dialog.getByRole('combobox', { name: 'Output channel' })).toHaveValue('red');
  await expect(dialog.getByRole('checkbox', { name: 'Monochrome' })).not.toBeChecked();
  await dialog.getByRole('slider', { name: 'Red source' }).fill('50');
  await dialog.getByRole('slider', { name: 'Green source' }).fill('50');
  await dialog.getByRole('slider', { name: 'Constant' }).fill('10');
  await expect(dialog.getByLabel('Channel total')).toContainText('100%');
  await dialog.getByRole('combobox', { name: 'Output channel' }).selectOption('green');
  await dialog.getByRole('slider', { name: 'Green source' }).fill('0');
  await dialog.getByRole('slider', { name: 'Blue source' }).fill('100');
  await page.getByRole('button', { name: 'Apply Channel Mixer' }).click();
  expect(await exportedPixel(page, 4, 4)).toEqual([101, 200, 200, 255]);
  await page.getByRole('button', { name: 'Undo' }).click();
  expect(await exportedPixel(page, 4, 4)).toEqual([100, 50, 200, 255]);

  await page.getByRole('menuitem', { name: 'Layer' }).click();
  await page.getByRole('menuitem', { name: /New Adjustment Layer|Adjustment/i }).click();
  await page.getByRole('menuitem', { name: 'Channel Mixer…' }).click();
  await expect(page.getByText(/Channel Mixer adjustment layer added/i)).toBeVisible();
  const properties = page.getByRole('tabpanel', { name: 'Properties' });
  await properties.getByRole('checkbox', { name: 'Monochrome' }).check();
  expect(await exportedPixel(page, 4, 4)).toEqual([100, 100, 100, 255]);
  await properties.getByRole('slider', { name: 'Red source' }).fill('60');
  await properties.getByRole('slider', { name: 'Green source' }).fill('30');
  await properties.getByRole('slider', { name: 'Blue source' }).fill('10');
  expect(await exportedPixel(page, 4, 4)).toEqual([95, 95, 95, 255]);
  await expect(page.getByText('Saved locally')).toBeVisible();
  await page.reload();
  await expect(page.getByText('Recovered your last local document.')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Channel Mixer · adjustment' })).toBeVisible();
  const recoveredProperties = page.getByRole('tabpanel', { name: 'Properties' });
  await expect(recoveredProperties.getByRole('checkbox', { name: 'Monochrome' })).toBeChecked();
  await expect(recoveredProperties.getByRole('slider', { name: 'Red source' })).toHaveValue('60');
});

test('applies, targets, tints, and persists Black and White adjustments', async ({ page }) => {
  const bytes = await page.evaluate(async () => {
    const canvas = document.createElement('canvas');
    canvas.width = 16;
    canvas.height = 16;
    const context = canvas.getContext('2d');
    if (!context) throw new Error('Canvas unavailable');
    context.fillStyle = 'rgb(255, 0, 0)';
    context.fillRect(0, 0, 16, 16);
    const blob = await new Promise<Blob>((resolve, reject) =>
      canvas.toBlob(
        (value) => (value ? resolve(value) : reject(new Error('PNG failed'))),
        'image/png',
      ),
    );
    return [...new Uint8Array(await blob.arrayBuffer())];
  });
  await page.getByLabel('Open image file').setInputFiles({
    name: 'black-white-base.png',
    mimeType: 'image/png',
    buffer: Buffer.from(bytes),
  });
  await page.getByRole('button', { name: 'Fit' }).click();

  await page.getByRole('menuitem', { name: 'Image' }).click();
  await page.getByRole('menuitem', { name: 'Adjustments' }).click();
  await page.getByRole('menuitem', { name: 'Black & White…' }).click();
  const dialog = page.getByRole('dialog', { name: 'Black & White' });
  await expect(dialog.getByRole('slider', { name: 'Reds' })).toHaveValue('40');
  await expect(dialog.getByRole('checkbox', { name: 'Tint' })).not.toBeChecked();
  await dialog.getByRole('button', { name: 'Targeted adjustment tool' }).click();
  const targetedPoint = await documentPoint(page, 4, 4);
  await page.mouse.move(targetedPoint.x, targetedPoint.y);
  await page.mouse.down();
  await page.mouse.move(targetedPoint.x + 20, targetedPoint.y, { steps: 3 });
  await page.mouse.up();
  await expect(dialog.getByRole('slider', { name: 'Reds' })).toHaveValue('60');
  await page.getByRole('button', { name: 'Apply Black & White' }).click();
  expect(await exportedPixel(page, 4, 4)).toEqual([153, 153, 153, 255]);
  await page.getByRole('button', { name: 'Undo' }).click();
  expect(await exportedPixel(page, 4, 4)).toEqual([255, 0, 0, 255]);

  await page.getByRole('menuitem', { name: 'Layer' }).click();
  await page.getByRole('menuitem', { name: /New Adjustment Layer|Adjustment/i }).click();
  await page.getByRole('menuitem', { name: 'Black & White…' }).click();
  await expect(page.getByText(/Black & White adjustment layer added/i)).toBeVisible();
  expect(await exportedPixel(page, 4, 4)).toEqual([102, 102, 102, 255]);
  const properties = page.getByRole('tabpanel', { name: 'Properties' });
  await properties.getByRole('button', { name: 'Targeted adjustment tool' }).click();
  await page.mouse.move(targetedPoint.x, targetedPoint.y);
  await page.mouse.down();
  await page.mouse.move(targetedPoint.x + 10, targetedPoint.y, { steps: 2 });
  await page.mouse.up();
  await expect(properties.getByRole('slider', { name: 'Reds' })).toHaveValue('50');
  await properties.getByRole('checkbox', { name: 'Tint' }).check();
  await properties.getByRole('slider', { name: 'Tint Hue' }).fill('0');
  await properties.getByRole('slider', { name: 'Tint Saturation' }).fill('100');
  const tinted = await exportedPixel(page, 4, 4);
  expect(tinted[0]).toBeGreaterThan(tinted[1]!);
  await expect(page.getByText('Saved locally')).toBeVisible();
  await page.reload();
  await expect(page.getByText('Recovered your last local document.')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Black & White · adjustment' })).toBeVisible();
  const recoveredProperties = page.getByRole('tabpanel', { name: 'Properties' });
  await expect(recoveredProperties.getByRole('checkbox', { name: 'Tint' })).toBeChecked();
  await expect(recoveredProperties.getByRole('slider', { name: 'Reds' })).toHaveValue('50');
  await expect(recoveredProperties.getByRole('slider', { name: 'Tint Saturation' })).toHaveValue(
    '100',
  );
});

test('applies preset and custom Photo Filters destructively and as a persistent adjustment', async ({
  page,
}) => {
  const bytes = await page.evaluate(async () => {
    const canvas = document.createElement('canvas');
    canvas.width = 16;
    canvas.height = 16;
    const context = canvas.getContext('2d');
    if (!context) throw new Error('Canvas unavailable');
    context.fillStyle = 'rgb(100, 100, 100)';
    context.fillRect(0, 0, 16, 16);
    const blob = await new Promise<Blob>((resolve, reject) =>
      canvas.toBlob(
        (value) => (value ? resolve(value) : reject(new Error('PNG failed'))),
        'image/png',
      ),
    );
    return [...new Uint8Array(await blob.arrayBuffer())];
  });
  await page.getByLabel('Open image file').setInputFiles({
    name: 'photo-filter-base.png',
    mimeType: 'image/png',
    buffer: Buffer.from(bytes),
  });
  await page.getByRole('button', { name: 'Fit' }).click();

  await page.getByRole('menuitem', { name: 'Image' }).click();
  await page.getByRole('menuitem', { name: 'Adjustments' }).click();
  await page.getByRole('menuitem', { name: 'Photo Filter…' }).click();
  const dialog = page.getByRole('dialog', { name: 'Photo Filter' });
  await expect(dialog.getByRole('radio', { name: 'Use preset filter' })).toBeChecked();
  await expect(dialog.getByRole('combobox', { name: 'Filter preset' })).toHaveValue('warming-85');
  await expect(dialog.getByRole('slider', { name: 'Density' })).toHaveValue('25');
  await expect(dialog.getByRole('checkbox', { name: 'Preserve Luminosity' })).toBeChecked();
  await dialog.getByRole('combobox', { name: 'Filter preset' }).selectOption('cooling-82');
  await dialog.getByRole('slider', { name: 'Density' }).fill('40');
  await dialog.getByRole('checkbox', { name: 'Preserve Luminosity' }).uncheck();
  await page.getByRole('button', { name: 'Apply Photo Filter' }).click();
  expect(await exportedPixel(page, 4, 4)).toEqual([60, 132, 162, 255]);
  await page.getByRole('button', { name: 'Undo' }).click();
  expect(await exportedPixel(page, 4, 4)).toEqual([100, 100, 100, 255]);

  await page.getByRole('menuitem', { name: 'Layer' }).click();
  await page.getByRole('menuitem', { name: /New Adjustment Layer|Adjustment/i }).click();
  await page.getByRole('menuitem', { name: 'Photo Filter…' }).click();
  await expect(page.getByText(/Photo Filter adjustment layer added/i)).toBeVisible();
  expect(await exportedPixel(page, 4, 4)).toEqual([122, 97, 63, 255]);
  const properties = page.getByRole('tabpanel', { name: 'Properties' });
  await properties.getByRole('radio', { name: 'Use custom color' }).check();
  await properties.getByLabel('Filter color').fill('#00ffff');
  await properties.getByRole('slider', { name: 'Density' }).fill('50');
  await properties.getByRole('checkbox', { name: 'Preserve Luminosity' }).uncheck();
  expect(await exportedPixel(page, 4, 4)).toEqual([50, 178, 178, 255]);
  await expect(page.getByText('Saved locally')).toBeVisible();
  await page.reload();
  await expect(page.getByText('Recovered your last local document.')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Photo Filter · adjustment' })).toBeVisible();
  const recoveredProperties = page.getByRole('tabpanel', { name: 'Properties' });
  await expect(recoveredProperties.getByRole('radio', { name: 'Use custom color' })).toBeChecked();
  await expect(recoveredProperties.getByLabel('Filter color')).toHaveValue('#00ffff');
  await expect(recoveredProperties.getByRole('slider', { name: 'Density' })).toHaveValue('50');
  await expect(
    recoveredProperties.getByRole('checkbox', { name: 'Preserve Luminosity' }),
  ).not.toBeChecked();
});

test('applies and recovers nine-range Selective Color adjustments', async ({ page }) => {
  const bytes = await page.evaluate(async () => {
    const canvas = document.createElement('canvas');
    canvas.width = 16;
    canvas.height = 16;
    const context = canvas.getContext('2d');
    if (!context) throw new Error('Canvas unavailable');
    context.fillStyle = 'rgb(255, 0, 0)';
    context.fillRect(0, 0, 16, 16);
    const blob = await new Promise<Blob>((resolve, reject) =>
      canvas.toBlob(
        (value) => (value ? resolve(value) : reject(new Error('PNG failed'))),
        'image/png',
      ),
    );
    return [...new Uint8Array(await blob.arrayBuffer())];
  });
  await page.getByLabel('Open image file').setInputFiles({
    name: 'selective-color-base.png',
    mimeType: 'image/png',
    buffer: Buffer.from(bytes),
  });
  await page.getByRole('button', { name: 'Fit' }).click();

  await page.getByRole('menuitem', { name: 'Image' }).click();
  await page.getByRole('menuitem', { name: 'Adjustments' }).click();
  await page.getByRole('menuitem', { name: 'Selective Color…' }).click();
  const dialog = page.getByRole('dialog', { name: 'Selective Color' });
  await expect(dialog.getByRole('combobox', { name: 'Colors' })).toHaveValue('reds');
  await expect(dialog.getByRole('radio', { name: 'Relative' })).toBeChecked();
  await dialog.getByRole('radio', { name: 'Absolute' }).check();
  await dialog.getByRole('slider', { name: 'Cyan' }).fill('20');
  await dialog.getByRole('combobox', { name: 'Colors' }).selectOption('neutrals');
  await dialog.getByRole('slider', { name: 'Magenta' }).fill('-40');
  await dialog.getByRole('combobox', { name: 'Colors' }).selectOption('reds');
  await expect(dialog.getByRole('slider', { name: 'Cyan' })).toHaveValue('20');
  await page.getByRole('button', { name: 'Apply Selective Color' }).click();
  expect(await exportedPixel(page, 4, 4)).toEqual([204, 0, 0, 255]);
  await page.getByRole('button', { name: 'Undo' }).click();
  expect(await exportedPixel(page, 4, 4)).toEqual([255, 0, 0, 255]);

  await page.getByRole('menuitem', { name: 'Layer' }).click();
  await page.getByRole('menuitem', { name: /New Adjustment Layer|Adjustment/i }).click();
  await page.getByRole('menuitem', { name: 'Selective Color…' }).click();
  await expect(page.getByText(/Selective Color adjustment layer added/i)).toBeVisible();
  const properties = page.getByRole('tabpanel', { name: 'Properties' });
  await properties.getByRole('radio', { name: 'Absolute' }).check();
  await properties.getByRole('slider', { name: 'Cyan' }).fill('20');
  expect(await exportedPixel(page, 4, 4)).toEqual([204, 0, 0, 255]);
  await properties.getByRole('combobox', { name: 'Colors' }).selectOption('blacks');
  await properties.getByRole('slider', { name: 'Black' }).fill('35');
  await properties.getByRole('combobox', { name: 'Colors' }).selectOption('reds');
  await expect(properties.getByRole('slider', { name: 'Cyan' })).toHaveValue('20');

  await expect(page.getByText('Saved locally')).toBeVisible();
  await page.reload();
  await expect(page.getByText('Recovered your last local document.')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Selective Color · adjustment' })).toBeVisible();
  const recovered = page.getByRole('tabpanel', { name: 'Properties' });
  await expect(recovered.getByRole('radio', { name: 'Absolute' })).toBeChecked();
  await expect(recovered.getByRole('combobox', { name: 'Colors' })).toHaveValue('reds');
  await expect(recovered.getByRole('slider', { name: 'Cyan' })).toHaveValue('20');
  await recovered.getByRole('combobox', { name: 'Colors' }).selectOption('blacks');
  await expect(recovered.getByRole('slider', { name: 'Black' })).toHaveValue('35');
});
