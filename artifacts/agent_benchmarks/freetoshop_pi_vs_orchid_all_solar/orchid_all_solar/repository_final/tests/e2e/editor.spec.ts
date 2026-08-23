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
    await new Promise<void>((resolve, reject) => {
      const request = indexedDB.deleteDatabase('freetoshop');
      request.onsuccess = () => resolve();
      request.onerror = () => reject(request.error);
      request.onblocked = () => resolve();
    });
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

  await page.getByRole('button', { name: 'Brush' }).click();
  await expect(page.getByLabel('Tool options')).toContainText('Brush');
  await expect(page.getByLabel('Tool options')).toContainText('Editing layer pixels');
  await expect(page.getByRole('toolbar', { name: 'Contextual document actions' })).toBeVisible();

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
  await expect(page.getByLabel('Tool options')).toContainText('Brush Tool');
  const brushSize = page.getByRole('slider', { name: 'Brush size' });
  const originalSize = Number(await brushSize.inputValue());
  await page.keyboard.press(']');
  await expect(brushSize).toHaveValue(String(originalSize + 2));

  await page.keyboard.press('h');
  const opacity = page.getByRole('spinbutton', { name: 'Layer opacity' });
  await opacity.focus();
  await opacity.press('b');
  await expect(page.getByLabel('Tool options')).toContainText('Hand Tool');

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
  await page.getByLabel('Text content').fill('FreetoShop');
  await page.getByLabel('Text font size').fill('72');
  await page.getByLabel('Text color').fill('#00ff00');
  await page.getByLabel('Text alignment').selectOption('center');
  await page.getByRole('checkbox', { name: 'Bold' }).check();
  expect(await exportedOpaquePixelCount(page)).toBeGreaterThan(500);

  await expect(page.getByText('Saved locally')).toBeVisible();
  await page.reload();
  await expect(page.getByText('Recovered your last local document.')).toBeVisible();
  await expect(page.getByLabel('Text content')).toHaveValue('FreetoShop');
  await expect(page.getByLabel('Text font size')).toHaveValue('72');
  await expect(page.getByLabel('Text alignment')).toHaveValue('center');
  await expect(page.getByRole('checkbox', { name: 'Bold' })).toBeChecked();
  expect(await exportedOpaquePixelCount(page)).toBeGreaterThan(500);
});

test('loads a local OpenType font and renders HarfBuzz-shaped text', async ({ page }) => {
  await page.getByRole('button', { name: 'Create document' }).click();
  await openMoreLayerActions(page);
  await page.getByRole('button', { name: 'Add text layer' }).click();
  await page.getByLabel('Load local font file').setInputFiles({
    name: 'DejaVuSans.ttf',
    mimeType: 'font/ttf',
    buffer: await readFile('node_modules/dejavu-fonts-ttf/ttf/DejaVuSans.ttf'),
  });
  await expect(
    page.getByText('DejaVuSans loaded locally with professional OpenType shaping.'),
  ).toBeVisible();
  await expect(page.getByLabel('Text font family')).toHaveValue('DejaVuSans');
  await expect(page.getByLabel('Text shaping status')).toHaveText(
    'Professional OpenType shaping active',
  );

  await page.getByLabel('Text content').fill('office مرحبا');
  await page.getByLabel('Text font size').fill('72');
  expect(await exportedOpaquePixelCount(page)).toBeGreaterThan(1_000);

  await expect(page.getByText('Saved locally')).toBeVisible();
  await page.reload();
  await expect(page.getByText('Recovered your last local document.')).toBeVisible();
  await expect(page.getByLabel('Text font family')).toHaveValue('DejaVuSans');
  await expect(page.getByLabel('Text shaping status')).toContainText('System-font fallback');
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
  await page.getByLabel('Add adjustment layer').selectOption('invert');
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
  await page.getByRole('menuitem', { name: 'Contrast…' }).click();
  const dialog = page.getByRole('dialog', { name: 'Contrast' });
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
  await expect(page.getByText('Editing layer mask', { exact: true })).toBeVisible();
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

  await page.getByLabel('Add adjustment layer').selectOption('invert');
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
