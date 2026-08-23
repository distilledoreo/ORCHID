import { expect, test, type Page } from '@playwright/test';

async function reset(page: Page, width = 1672, height = 941) {
  await page.setViewportSize({ width, height });
  await page.goto('/');
  await page.evaluate(() => localStorage.clear());
  await page.reload();
}

async function createLayers(page: Page, count = 3) {
  await page.getByRole('button', { name: 'Create document' }).click();
  for (let index = 1; index < count; index += 1) {
    await page.keyboard.press('ControlOrMeta+Shift+N');
  }
}

async function selectNarrowColumn(page: Page, label: 'Layers' | 'Properties') {
  const button = page.locator('.narrow-dock-switcher button', { hasText: label });
  if (await button.count()) await button.click();
}

test.describe('UI/P1 polish', () => {
  test('targets layer pixels and raster masks independently', async ({ page }) => {
    await reset(page);
    await createLayers(page, 2);
    await page.getByLabel('Add Layer Mask').click();
    await expect(page.getByRole('button', { name: 'Edit pixels for Layer 2' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    await expect(page.getByRole('button', { name: 'Edit layer mask for Layer 2' })).toHaveAttribute(
      'aria-pressed',
      'false',
    );
    await page.getByRole('button', { name: 'Edit layer mask for Layer 2' }).click();
    await expect(page.getByRole('button', { name: 'Edit pixels for Layer 2' })).toHaveAttribute(
      'aria-pressed',
      'false',
    );
    await expect(page.getByRole('button', { name: 'Edit layer mask for Layer 2' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    await expect(page.getByLabel('Tool options')).toContainText('Editing layer mask');
    await expect(page.locator('.layer-row[data-selected="true"] .layer-row-button')).toHaveCount(1);
  });

  test('searches layers and preserves matching groups', async ({ page }) => {
    await reset(page);
    await createLayers(page, 3);
    await page.getByRole('button', { name: 'Layer 3', exact: true }).click();
    await page
      .getByRole('button', { name: 'Layer 2', exact: true })
      .click({ modifiers: ['ControlOrMeta'] });
    await page.getByRole('button', { name: 'Group selected layers' }).click();
    await page.getByRole('searchbox', { name: 'Search layers' }).fill('Layer 2');
    await expect(page.locator('.layer-row')).toHaveCount(2);
    await expect(page.locator('.layer-row').filter({ hasText: 'Layer 2' })).toBeVisible();
    await page.getByRole('button', { name: 'Clear layer search and filter' }).click();
    await expect(page.locator('.layer-row')).toHaveCount(4);
  });

  test('filters layers by kind and mask', async ({ page }) => {
    await reset(page);
    await createLayers(page, 2);
    await page.getByLabel('More layer actions').click();
    await page.getByLabel('Add text layer').click();
    await page.getByLabel('Filter layers').selectOption('text');
    await expect(page.locator('.layer-row')).toHaveCount(1);
    await page.getByLabel('Filter layers').selectOption('all');
    await page.getByRole('button', { name: 'Layer 2', exact: true }).click();
    await page.getByLabel('Add Layer Mask').click();
    await page.getByLabel('Filter layers').selectOption('masked');
    await expect(page.locator('.layer-row').filter({ hasText: 'Layer 2' })).toBeVisible();
  });

  test('opens and selects tool flyouts from the keyboard', async ({ page }) => {
    await reset(page);
    await createLayers(page, 1);
    const trigger = page.getByRole('button', { name: 'Rectangular select' });
    await trigger.focus();
    await trigger.press('ArrowRight');
    await expect(page.getByRole('menu', { name: 'marquee tools' })).toBeVisible();
    const flyout = page.getByRole('menu', { name: 'marquee tools' });
    const firstItem = flyout.getByRole('menuitem').first();
    const secondItem = flyout.getByRole('menuitem').nth(1);
    await firstItem.focus();
    await firstItem.press('ArrowDown');
    await expect(secondItem).toBeFocused();
    await secondItem.press('Enter');
    await expect(page.getByRole('button', { name: 'Elliptical select' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    const activeTrigger = page.getByRole('button', { name: 'Elliptical select' });
    await activeTrigger.focus();
    await activeTrigger.press('ArrowRight');
    await page.getByRole('menu', { name: 'marquee tools' }).press('Escape');
    await expect(activeTrigger).toBeFocused();
  });

  test('supports panel-group menus and reset commands', async ({ page }) => {
    await reset(page);
    await createLayers(page, 1);
    const menuButton = page.getByRole('button', { name: 'Open layers panel menu' });
    await menuButton.click();
    await page.getByRole('menuitem', { name: 'Collapse Panel Group' }).click();
    await expect(page.locator('.panel-group[data-group="layers"]')).toHaveAttribute(
      'data-collapsed',
      'true',
    );
    await menuButton.click();
    await page.getByRole('menuitem', { name: 'Reset Panel Group' }).click();
    await expect(page.locator('.panel-group[data-group="layers"]')).toHaveAttribute(
      'data-collapsed',
      'false',
    );
  });

  test('confirms workspace reset and preserves Cancel behavior', async ({ page }) => {
    await reset(page);
    await createLayers(page, 1);
    await page.getByRole('menuitem', { name: 'Window' }).click();
    await page.getByRole('menuitem', { name: 'Reset Professional Editing Workspace' }).click();
    const dialog = page.getByRole('dialog', { name: 'Reset Professional Editing Workspace' });
    await expect(dialog).toBeVisible();
    await dialog.getByRole('button', { name: 'Cancel' }).click();
    await expect(dialog).toBeHidden();
    await page.getByRole('menuitem', { name: 'Window' }).click();
    await page.getByRole('menuitem', { name: 'Reset Professional Editing Workspace' }).click();
    await dialog.getByRole('button', { name: 'Reset Workspace' }).click();
    await expect(dialog).toBeHidden();
  });

  test('persists the narrow dock column', async ({ page }) => {
    await reset(page, 1440, 900);
    await createLayers(page, 1);
    await expect(page.getByRole('tab', { name: 'Properties' })).toBeVisible();
    await selectNarrowColumn(page, 'Properties');
    await expect(page.locator('.dock-column-secondary[data-active="true"]')).toBeVisible();
    await expect(page.locator('.dock-column-primary[data-active="false"]')).toHaveCSS(
      'pointer-events',
      'none',
    );
    await page.reload();
    await expect(page.locator('.side-panel[data-narrow-column="secondary"]')).toBeVisible();
    await selectNarrowColumn(page, 'Layers');
    await expect(page.locator('.dock-column-primary[data-active="true"]')).toBeVisible();
  });

  test('removes meaningful motion when reduced motion is requested', async ({ page }) => {
    await reset(page, 1440, 900);
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await createLayers(page, 1);
    for (const selector of ['.side-panel', '.dock-column', '.panel-collapse']) {
      const duration = await page
        .locator(selector)
        .first()
        .evaluate((element) => getComputedStyle(element).transitionDuration);
      expect(Number.parseFloat(duration)).toBeLessThanOrEqual(0.01);
    }
  });

  test('keeps selected controls visible in forced colors', async ({ page }) => {
    await reset(page);
    await createLayers(page, 2);
    await page.emulateMedia({ forcedColors: 'active' });
    await page.getByRole('button', { name: 'Edit pixels for Layer 2' }).click();
    const outline = await page
      .getByRole('button', { name: 'Edit pixels for Layer 2' })
      .evaluate((element) => getComputedStyle(element).outlineWidth);
    expect(Number.parseFloat(outline)).toBeGreaterThanOrEqual(2);
  });
});
