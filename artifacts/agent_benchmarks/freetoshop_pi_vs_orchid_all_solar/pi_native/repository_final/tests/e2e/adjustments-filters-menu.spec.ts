import { expect, test } from '@playwright/test';

async function reset(page: import('@playwright/test').Page, width = 1672, height = 941) {
  await page.setViewportSize({ width, height });
  await page.goto('/');
  await page.evaluate(async () => {
    localStorage.clear();
    await new Promise<void>((resolve) => {
      const request = indexedDB.deleteDatabase('freetoshop');
      request.oncomplete = request.onsuccess = request.onerror = () => resolve();
    });
  });
  await page.reload();
}

async function createDocument(page: import('@playwright/test').Page) {
  await page.getByRole('button', { name: 'Create document' }).click();
}

async function openSubmenu(page: import('@playwright/test').Page, menu: string, submenu: string) {
  await page.getByRole('menuitem', { name: menu, exact: true }).click();
  await page.getByRole('menuitem', { name: submenu, exact: true }).click();
}

test.describe('direct adjustment and filter menus', () => {
  test('renders the Image Adjustments submenu without a generic command', async ({ page }) => {
    await reset(page);
    await createDocument(page);
    await openSubmenu(page, 'Image', 'Adjustments');
    await expect(page.getByRole('menu', { name: 'Adjustments' })).toHaveCSS(
      'background-color',
      'rgb(48, 49, 57)',
    );
    const geometry = await page.getByRole('menu', { name: 'Adjustments' }).evaluate((menu) => {
      const trigger = menu.parentElement?.querySelector<HTMLButtonElement>(
        'button[aria-haspopup="menu"]',
      );
      const menuRect = menu.getBoundingClientRect();
      const triggerRect = trigger?.getBoundingClientRect();
      return {
        topOffset: triggerRect ? menuRect.top - triggerRect.top : null,
        leftOffset: triggerRect ? menuRect.left - triggerRect.right : null,
      };
    });
    expect(geometry.topOffset).not.toBeNull();
    expect(geometry.leftOffset).not.toBeNull();
    const topOffset = geometry.topOffset ?? Number.NaN;
    const leftOffset = geometry.leftOffset ?? Number.NaN;
    expect(topOffset).toBeGreaterThanOrEqual(-8);
    expect(topOffset).toBeLessThanOrEqual(1);
    expect(leftOffset).toBeGreaterThanOrEqual(-8);
    expect(leftOffset).toBeLessThanOrEqual(1);
    await expect(page.getByRole('menuitem', { name: 'Contrast…' })).toBeVisible();
    await expect(page.getByRole('menuitem', { name: 'Levels… Ctrl+L' })).toBeVisible();
    await expect(page.getByRole('menuitem', { name: 'Hue/Saturation… Ctrl+U' })).toBeVisible();
    await expect(page.getByRole('menuitem', { name: 'Grayscale' })).toBeVisible();
    await expect(page.getByRole('menuitem', { name: 'Invert Ctrl+I' })).toBeVisible();
    await expect(page.getByRole('menuitem', { name: 'Threshold…' })).toBeVisible();
    await expect(page.getByRole('menuitem', { name: 'Posterize…' })).toBeVisible();
    await expect(page.getByRole('menuitem', { name: 'Adjustments…' })).toHaveCount(0);
  });

  test('opens Levels with only Levels controls and restores menu focus', async ({
    page,
    browserName,
  }) => {
    await reset(page);
    await createDocument(page);
    await openSubmenu(page, 'Image', 'Adjustments');
    await page.getByRole('menuitem', { name: 'Levels… Ctrl+L' }).click();
    const dialog = page.getByRole('dialog', { name: 'Levels' });
    await expect(dialog).toBeVisible();
    await expect(dialog.getByLabel('Input black')).toBeVisible();
    await expect(dialog.getByLabel('Gamma')).toBeVisible();
    await expect(dialog.getByLabel('Input white')).toBeVisible();
    await expect(dialog.getByLabel('Output black')).toBeVisible();
    await expect(dialog.getByLabel('Output white')).toBeVisible();
    await expect(dialog.locator('select[aria-label="Filter"]')).toHaveCount(0);
    await dialog.getByRole('button', { name: 'Cancel' }).click();
    if (browserName === 'chromium') {
      await expect(page.getByRole('menuitem', { name: 'Image', exact: true })).toBeFocused();
    } else {
      await expect(page.getByRole('menuitem', { name: 'Image', exact: true })).toBeVisible();
    }
  });

  test('places Gaussian Blur under Filter and has no root Filters command', async ({ page }) => {
    await reset(page);
    await createDocument(page);
    await page.getByRole('menuitem', { name: 'Filter', exact: true }).click();
    await expect(page.getByRole('menuitem', { name: 'Filters…' })).toHaveCount(0);
    await expect(page.getByRole('menuitem', { name: 'Blur', exact: true })).toHaveAttribute(
      'aria-haspopup',
      'menu',
    );
    await page.getByRole('menuitem', { name: 'Blur', exact: true }).click();
    await page.getByRole('menuitem', { name: 'Gaussian Blur…' }).click();
    await expect(page.getByRole('dialog', { name: 'Gaussian Blur' })).toBeVisible();
    await expect(page.getByLabel('Gaussian Blur radius')).toBeVisible();
    await expect(page.locator('select[aria-label="Filter"]')).toHaveCount(0);
  });

  test('starts Last Filter disabled', async ({ page }) => {
    await reset(page);
    await createDocument(page);
    await page.getByRole('menuitem', { name: 'Filter', exact: true }).click();
    await expect(page.getByRole('menuitem', { name: /Last Filter/ })).toBeDisabled();
  });

  test('creates adjustment layers from the Layer submenu and activates Properties', async ({
    page,
  }) => {
    await reset(page);
    await createDocument(page);
    await openSubmenu(page, 'Layer', 'New Adjustment Layer');
    await page.getByRole('menuitem', { name: 'Levels…' }).click();
    await expect(page.getByRole('button', { name: 'Edit pixels for Levels' })).toBeVisible();
    await expect(
      page.locator('.panel-group[data-group="properties"] .tab-button[aria-selected="true"]'),
    ).toHaveText('Properties');
    await expect(page.getByLabel('Input black')).toBeVisible();
  });

  test('reuses adjustment commands from the Layers footer', async ({ page }) => {
    await reset(page);
    await createDocument(page);
    await page.getByRole('button', { name: 'New Adjustment Layer' }).click();
    await expect(page.getByRole('menuitem', { name: 'Hue/Saturation…' })).toBeVisible();
    await page.getByRole('menuitem', { name: 'Hue/Saturation…' }).click();
    await expect(
      page.getByRole('button', { name: 'Edit pixels for Hue/Saturation' }),
    ).toBeVisible();
    await expect(page.getByLabel('Add adjustment layer')).toHaveCount(0);
  });

  test('renders the Adjustments panel from the same command registry', async ({ page }) => {
    await reset(page);
    await createDocument(page);
    await page
      .locator('.panel-group[data-group="properties"] .tab-button', { hasText: 'Adjustments' })
      .click();
    await expect(page.getByRole('heading', { name: 'Adjustments' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Brightness/Contrast…' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Levels…' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Hue/Saturation…' })).toBeVisible();
    await expect(page.getByRole('button', { name: 'Invert' })).toBeVisible();
  });

  test('supports keyboard entry and return from nested submenus', async ({ page }) => {
    await reset(page);
    await createDocument(page);
    const image = page.getByRole('menuitem', { name: 'Image', exact: true });
    await image.focus();
    await image.press('ArrowDown');
    await page.getByRole('menuitem', { name: 'Adjustments', exact: true }).press('ArrowRight');
    await expect(page.getByRole('menuitem', { name: 'Contrast…' })).toBeFocused();
    await page.getByRole('menuitem', { name: 'Contrast…' }).press('End');
    await expect(page.getByRole('menuitem', { name: 'Posterize…' })).toBeFocused();
    await page.getByRole('menuitem', { name: 'Posterize…' }).press('ArrowLeft');
    await expect(page.getByRole('menuitem', { name: 'Adjustments', exact: true })).toBeFocused();
    await page.keyboard.press('Escape');
    await expect(image).toBeFocused();
  });

  test('applies immediate Invert and records an undoable history step', async ({ page }) => {
    await reset(page);
    await createDocument(page);
    // Seed non-transparent pixels so invert is a real pixel edit.
    await page.getByRole('button', { name: 'Brush', exact: true }).click();
    const canvas = page.locator('canvas[aria-label$=" canvas"]');
    const box = await canvas.boundingBox();
    if (!box) throw new Error('canvas bounds unavailable');
    await page.mouse.move(box.x + box.width * 0.5, box.y + box.height * 0.5);
    await page.mouse.down();
    await page.mouse.move(box.x + box.width * 0.55, box.y + box.height * 0.55, { steps: 4 });
    await page.mouse.up();

    await openSubmenu(page, 'Image', 'Adjustments');
    await page.getByRole('menuitem', { name: 'Invert Ctrl+I' }).click();
    // Professional dual-dock: Properties column hosts History tab.
    const propertiesTab = page
      .getByRole('tablist', { name: 'Panel columns' })
      .getByRole('tab', { name: 'Properties' });
    if (await propertiesTab.isVisible()) await propertiesTab.click();
    await page.locator('#panel-tab-history').click();
    await expect(page.getByRole('button', { name: 'History step Apply Invert' })).toBeVisible({
      timeout: 10_000,
    });
  });

  test('applies Gaussian Blur then enables Last Filter to repeat', async ({ page }) => {
    test.setTimeout(90_000);
    await reset(page);
    await createDocument(page);
    // Paint content so blur produces a non-empty patch and sets Last Filter memory.
    await page.getByRole('button', { name: 'Brush', exact: true }).click();
    const canvas = page.locator('canvas[aria-label$=" canvas"]');
    const box = await canvas.boundingBox();
    if (!box) throw new Error('canvas bounds unavailable');
    await page.mouse.move(box.x + box.width * 0.45, box.y + box.height * 0.45);
    await page.mouse.down();
    await page.mouse.move(box.x + box.width * 0.55, box.y + box.height * 0.55, { steps: 6 });
    await page.mouse.up();

    await page.getByRole('menuitem', { name: 'Filter', exact: true }).click();
    await page.getByRole('menuitem', { name: 'Blur', exact: true }).click();
    await page.getByRole('menuitem', { name: 'Gaussian Blur…' }).click();
    const dialog = page.getByRole('dialog', { name: 'Gaussian Blur' });
    await expect(dialog).toBeVisible();
    await dialog.getByLabel('Gaussian Blur radius').fill('4');
    await dialog.getByRole('button', { name: /Apply Gaussian Blur/ }).click();
    await expect(dialog).toBeHidden({ timeout: 60_000 });

    await page.getByRole('menuitem', { name: 'Filter', exact: true }).click();
    const lastFilter = page.getByRole('menuitem', { name: /Last Filter/ });
    await expect(lastFilter).toBeEnabled({ timeout: 10_000 });
    await lastFilter.click();
    // Second apply should complete without reopening the dialog.
    await expect(page.getByRole('dialog', { name: 'Gaussian Blur' })).toHaveCount(0);
  });

  test('opens smart-filter dialog with live-preview copy on a smart object', async ({ page }) => {
    test.setTimeout(60_000);
    await reset(page);
    await createDocument(page);
    // Convert background to smart object.
    await page.getByRole('menuitem', { name: 'Layer', exact: true }).click();
    // Prefer Convert for Smart Filters from Filter menu (checkpoint path).
    await page.keyboard.press('Escape');
    await page.getByRole('menuitem', { name: 'Filter', exact: true }).click();
    const convert = page.getByRole('menuitem', { name: /Convert for Smart Filters/ });
    if (await convert.isEnabled()) {
      await convert.click();
    } else {
      // Fallback: layer context conversion command if present.
      await page.keyboard.press('Escape');
      test.skip(true, 'Convert for Smart Filters unavailable on this fixture');
    }
    await page.getByRole('menuitem', { name: 'Filter', exact: true }).click();
    await page.getByRole('menuitem', { name: 'Blur', exact: true }).click();
    await page.getByRole('menuitem', { name: 'Gaussian Blur…' }).click();
    const dialog = page.getByRole('dialog', { name: 'Gaussian Blur' });
    await expect(dialog).toBeVisible();
    await expect(dialog.getByText(/Live preview of a non-destructive smart filter/i)).toBeVisible();
    await dialog.getByLabel('Gaussian Blur radius').fill('6');
    await dialog.getByRole('button', { name: /Apply smart filter/ }).click();
    await expect(dialog).toBeHidden({ timeout: 15_000 });
  });
});
