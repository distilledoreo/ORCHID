import { expect, test, type Page } from '@playwright/test';
import path from 'node:path';

const checkpointDirectory = path.join(
  process.cwd(),
  'docs',
  'ui-checkpoints',
  'visual-parity',
  process.env.UI_VISUAL_CHECKPOINT ?? 'final',
);

async function reset(page: Page) {
  await page.goto('/');
  await page.evaluate(() => localStorage.clear());
  await page.reload();
}

async function createLayeredFixture(page: Page) {
  await page.getByRole('button', { name: 'Create document' }).click();
  const switchNarrowColumn = async (column: 'Layers' | 'Properties') => {
    const switcher = page.locator('.narrow-dock-switcher button', { hasText: column });
    if ((await switcher.count()) && (await switcher.isVisible())) await switcher.click();
  };
  const colors = ['#203d77', '#c24956', '#df9a32', '#17b897', '#744fc6'];
  const paintStroke = async (offset: number, color: string) => {
    await page.keyboard.press('b');
    await page.getByLabel('Brush color').fill(color);
    const canvas = await page.locator('canvas[aria-label$=" canvas"]').boundingBox();
    if (!canvas) throw new Error('Canvas bounds unavailable');
    await page.mouse.move(canvas.x + 170 + offset, canvas.y + 180 + offset / 2);
    await page.mouse.down();
    await page.mouse.move(canvas.x + 420 + offset, canvas.y + 330 + offset / 3, { steps: 12 });
    await page.mouse.up();
  };
  await paintStroke(0, colors[0]);
  for (let index = 0; index < 4; index += 1) {
    await page.keyboard.press('ControlOrMeta+Shift+N');
    await paintStroke(index * 35 + 20, colors[index + 1] ?? colors[0]);
  }
  await page.getByRole('button', { name: 'Add Layer Mask' }).click();
  await switchNarrowColumn('Properties');
  await page
    .getByRole('button', { name: 'Add rectangular vector mask' })
    .evaluate((button: HTMLButtonElement) => button.click());
  await switchNarrowColumn('Layers');
  await page.getByRole('button', { name: 'Layer 3', exact: true }).click();
  await page
    .getByRole('button', { name: 'Layer 2', exact: true })
    .click({ modifiers: ['ControlOrMeta'] });
  await page.getByRole('button', { name: 'Group selected layers' }).click();
  const layerActions = page.getByLabel('Layer actions');
  await layerActions.getByRole('button', { name: 'New Adjustment Layer' }).click();
  await page.getByRole('menuitem', { name: 'Hue/Saturation…' }).click();
  await switchNarrowColumn('Layers');
  await layerActions.getByLabel('More layer actions').click();
  await layerActions.getByLabel('Add shape layer').selectOption('rectangle');
  await layerActions.getByLabel('More layer actions').click();
  await layerActions.getByRole('button', { name: 'Add text layer' }).click();
  await page.getByRole('button', { name: 'Layer 4', exact: true }).click();
  await page.getByRole('button', { name: 'Make smart object' }).click();
  await layerActions.getByLabel('Add layer effect').selectOption('drop-shadow');
  await expect(page.locator('.layer-row')).toHaveCount(9);
}

async function geometry(page: Page) {
  return page.evaluate(() => {
    const rect = (selector: string) => {
      const value = document.querySelector(selector)?.getBoundingClientRect();
      return value
        ? {
            x: Math.round(value.x),
            y: Math.round(value.y),
            width: Math.round(value.width),
            height: Math.round(value.height),
          }
        : null;
    };
    return {
      viewport: { width: innerWidth, height: innerHeight, deviceScaleFactor: devicePixelRatio },
      menu: rect('.application-menubar'),
      options: rect('.options-bar'),
      tabs: rect('.document-tab-strip'),
      toolRail: rect('.tool-rail'),
      canvasViewport: rect('.canvas-stage'),
      primaryDock: rect('.side-panel'),
      propertiesGroup: rect('.panel-group:has(#panel-properties)'),
      layersGroup: rect('.panel-group:has(#panel-layers)'),
      historyGroup: rect('.panel-group:has(#panel-history)'),
      status: rect('.statusbar'),
      bodyScroll: {
        horizontal: document.documentElement.scrollWidth > document.documentElement.clientWidth,
        vertical: document.documentElement.scrollHeight > document.documentElement.clientHeight,
      },
    };
  });
}

async function assertDesktopGeometry(page: Page, width: number, height: number) {
  await page.setViewportSize({ width, height });
  await page.getByRole('button', { name: 'Fit' }).click();
  const shell = await page.evaluate(() => {
    const box = (selector: string) => {
      const value = document.querySelector(selector)?.getBoundingClientRect();
      if (!value) throw new Error(`Missing layout region: ${selector}`);
      return { x: value.x, y: value.y, width: value.width, height: value.height };
    };
    const optionalBox = (selector: string) => {
      const value = document.querySelector(selector)?.getBoundingClientRect();
      return value ? { x: value.x, y: value.y, width: value.width, height: value.height } : null;
    };
    const documentVisualBox = () => {
      const canvas = document.querySelector<HTMLCanvasElement>('canvas[aria-label$=" canvas"]');
      if (!canvas) throw new Error('Missing document canvas');
      const bounds = canvas.getBoundingClientRect();
      return {
        x: bounds.x + Number(canvas.dataset.documentX),
        y: bounds.y + Number(canvas.dataset.documentY),
        width: Number(canvas.dataset.documentWidth),
        height: Number(canvas.dataset.documentHeight),
      };
    };
    const intersects = (row: DOMRect, viewport: DOMRect) =>
      row.bottom > viewport.top && row.top < viewport.bottom;
    const list = document.querySelector('.panel-group[data-group="layers"] .layer-list');
    if (!list) throw new Error('Missing Layers list');
    const listBox = list.getBoundingClientRect();
    const visibleRows = [...list.querySelectorAll('.layer-row')].filter((row) =>
      intersects(row.getBoundingClientRect(), listBox),
    ).length;
    const panelOverflow = [...document.querySelectorAll('.panel-group')].some(
      (panel) => panel.scrollWidth > panel.clientWidth + 1,
    );
    return {
      menu: box('.application-menubar'),
      options: box('.options-bar'),
      tabs: box('.document-tab-strip'),
      status: box('.statusbar'),
      rail: box('.tool-rail'),
      stage: box('.canvas-stage'),
      document: documentVisualBox(),
      layers: box('.panel-group[data-group="layers"]'),
      properties: box('.panel-group[data-group="properties"]'),
      list: box('.panel-group[data-group="layers"] .layer-list'),
      layerFooter: box('.panel-group[data-group="layers"] .layer-actions'),
      compactProperties: box('.compact-properties'),
      taskbar: optionalBox('.contextual-taskbar'),
      visibleRows,
      thumbnailImages: document.querySelectorAll('.layer-thumbnail img, .layer-mask-thumbnail img')
        .length,
      panelOverflow,
      bodyOverflow:
        document.documentElement.scrollWidth > document.documentElement.clientWidth + 1 ||
        document.documentElement.scrollHeight > document.documentElement.clientHeight + 1,
      colorVisible: Boolean(document.querySelector('.panel-group[data-group="color"]')),
    };
  });

  expect(shell.menu.height).toBeGreaterThanOrEqual(36);
  expect(shell.menu.height).toBeLessThanOrEqual(40);
  expect(shell.options.height).toBeGreaterThanOrEqual(40);
  expect(shell.options.height).toBeLessThanOrEqual(46);
  expect(shell.tabs.height).toBeGreaterThanOrEqual(34);
  expect(shell.tabs.height).toBeLessThanOrEqual(38);
  expect(shell.status.height).toBeGreaterThanOrEqual(28);
  expect(shell.status.height).toBeLessThanOrEqual(32);
  expect(shell.status.x).toBeCloseTo(shell.stage.x, 0);
  expect(shell.status.width).toBeCloseTo(shell.stage.width, 0);
  expect(shell.rail.width).toBeGreaterThanOrEqual(96);
  expect(shell.document.x).toBeGreaterThanOrEqual(shell.stage.x);
  expect(shell.document.y).toBeGreaterThanOrEqual(shell.stage.y);
  expect(shell.document.x + shell.document.width).toBeLessThanOrEqual(
    shell.stage.x + shell.stage.width,
  );
  expect(shell.document.y + shell.document.height).toBeLessThanOrEqual(
    shell.stage.y + shell.stage.height,
  );
  expect(
    Math.abs(shell.document.x + shell.document.width / 2 - (shell.stage.x + shell.stage.width / 2)),
  ).toBeLessThanOrEqual(48);
  expect(
    Math.abs(
      shell.document.y + shell.document.height / 2 - (shell.stage.y + shell.stage.height / 2),
    ),
  ).toBeLessThanOrEqual(48);
  expect(shell.panelOverflow).toBe(false);
  expect(shell.bodyOverflow).toBe(false);
  expect(shell.colorVisible).toBe(false);
  expect(shell.thumbnailImages).toBeGreaterThanOrEqual(4);
  expect(shell.compactProperties.y).toBeGreaterThanOrEqual(shell.properties.y);
  expect(shell.list.y + shell.list.height).toBeLessThanOrEqual(shell.layerFooter.y + 1);
  expect(shell.layerFooter.height).toBeLessThanOrEqual(48);
  if (shell.taskbar) {
    const horizontalOverlap =
      shell.taskbar.x < shell.document.x + shell.document.width &&
      shell.taskbar.x + shell.taskbar.width > shell.document.x;
    const verticalOverlap =
      shell.taskbar.y < shell.document.y + shell.document.height &&
      shell.taskbar.y + shell.taskbar.height > shell.document.y;
    expect(horizontalOverlap && verticalOverlap).toBe(false);
  }

  if (width >= 1600) {
    expect(shell.layers.width).toBeGreaterThanOrEqual(280);
    expect(shell.properties.width).toBeGreaterThanOrEqual(280);
    expect(shell.layers.x + shell.layers.width).toBeLessThanOrEqual(shell.properties.x + 1);
    expect(shell.list.height).toBeGreaterThanOrEqual(360);
    expect(shell.visibleRows).toBeGreaterThanOrEqual(8);
  }
}

test('captures the rendered workspace visual checkpoint', async ({ page, browserName }) => {
  test.skip(browserName !== 'chromium', 'Visual evidence is captured once with Chromium.');
  test.setTimeout(90_000);
  await reset(page);
  await page.setViewportSize({ width: 1672, height: 941 });
  await page.getByRole('button', { name: 'Create document' }).click();
  await page.screenshot({
    path: path.join(checkpointDirectory, '1672x941-new.png'),
    animations: 'disabled',
  });
  await expect(page).toHaveScreenshot('visual-parity-1672x941-new.png', {
    animations: 'disabled',
    maxDiffPixelRatio: 0.005,
  });

  await reset(page);
  await createLayeredFixture(page);
  const captureMenu = async (menu: string, submenu: string, name: string) => {
    await page.getByRole('menuitem', { name: menu, exact: true }).click();
    await page.getByRole('menuitem', { name: submenu, exact: true }).click();
    await page.screenshot({
      path: path.join(checkpointDirectory, `1672x941-${name}.png`),
      animations: 'disabled',
    });
    await expect(page).toHaveScreenshot(`visual-parity-1672x941-${name}.png`, {
      animations: 'disabled',
      maxDiffPixelRatio: 0.005,
    });
    await page.keyboard.press('Escape');
  };
  await captureMenu('Image', 'Adjustments', 'menu-image-adjustments');
  await captureMenu('Filter', 'Blur', 'menu-filter-blur');
  await captureMenu('Layer', 'New Adjustment Layer', 'menu-layer-adjustment');
  for (const [width, height] of [
    [2048, 1046],
    [1672, 941],
    [1440, 900],
    [1366, 768],
  ] as const) {
    await page.setViewportSize({ width, height });
    await page.getByRole('button', { name: 'Fit' }).click();
    await page.screenshot({
      path: path.join(checkpointDirectory, `${String(width)}x${String(height)}-layered.png`),
      animations: 'disabled',
    });
    await expect(page).toHaveScreenshot(
      `visual-parity-${String(width)}x${String(height)}-layered.png`,
      { animations: 'disabled', maxDiffPixelRatio: 0.005 },
    );
    console.log(`VISUAL_BASELINE ${String(width)}x${String(height)}`, await geometry(page));
  }

  await page.setViewportSize({ width: 1672, height: 941 });
  await page.keyboard.press('m');
  const canvas = await page.locator('canvas[aria-label$=" canvas"]').boundingBox();
  if (!canvas) throw new Error('Canvas bounds unavailable');
  await page.mouse.move(canvas.x + canvas.width * 0.35, canvas.y + canvas.height * 0.35);
  await page.mouse.down();
  await page.mouse.move(canvas.x + canvas.width * 0.62, canvas.y + canvas.height * 0.63);
  await page.mouse.up();
  await expect(page.getByRole('heading', { name: 'Active Selection' })).toBeVisible();
  await page.screenshot({
    path: path.join(checkpointDirectory, '1672x941-selection.png'),
    animations: 'disabled',
  });
  await expect(page).toHaveScreenshot('visual-parity-1672x941-selection.png', {
    animations: 'disabled',
    maxDiffPixelRatio: 0.005,
  });

  await page.keyboard.press('ControlOrMeta+t');
  await expect(page.getByRole('heading', { name: 'Active Transform' })).toBeVisible();
  await page.screenshot({
    path: path.join(checkpointDirectory, '1672x941-transform.png'),
    animations: 'disabled',
  });
  await expect(page).toHaveScreenshot('visual-parity-1672x941-transform.png', {
    animations: 'disabled',
    maxDiffPixelRatio: 0.005,
  });
});

test('presents contextual Properties states for selection, transform, and crop', async ({
  page,
}) => {
  test.setTimeout(90_000);
  await reset(page);
  await page.setViewportSize({ width: 1672, height: 941 });
  await page.getByRole('button', { name: 'Create document' }).click();
  const canvas = await page.locator('canvas[aria-label$=" canvas"]').boundingBox();
  if (!canvas) throw new Error('Canvas bounds unavailable');
  await page.keyboard.press('m');
  await page.mouse.move(canvas.x + 120, canvas.y + 120);
  await page.mouse.down();
  await page.mouse.move(canvas.x + 320, canvas.y + 280);
  await page.mouse.up();
  await expect(page.getByRole('heading', { name: 'Active Selection' })).toBeVisible();
  await page.keyboard.press('ControlOrMeta+t');
  await expect(page.getByRole('heading', { name: 'Active Transform' })).toBeVisible();
  await page.keyboard.press('Escape');
  await page.keyboard.press('ControlOrMeta+d');
  await page.keyboard.press('c');
  await page.mouse.move(canvas.x + 90, canvas.y + 90);
  await page.mouse.down();
  await page.mouse.move(canvas.x + 400, canvas.y + 340);
  await page.mouse.up();
  await expect(page.getByRole('heading', { name: 'Active Crop' })).toBeVisible();
});

test('enforces the P0 wide and standard desktop workspace geometry', async ({
  page,
  browserName,
}) => {
  test.skip(browserName !== 'chromium', 'Geometry is engine-independent and asserted in Chromium.');
  test.setTimeout(90_000);
  await reset(page);
  await createLayeredFixture(page);
  await assertDesktopGeometry(page, 1672, 941);
  await assertDesktopGeometry(page, 1440, 900);
  await page.setViewportSize({ width: 1366, height: 768 });
  await expect(page.locator('.tool-rail')).toHaveCSS('width', '46px');
});
