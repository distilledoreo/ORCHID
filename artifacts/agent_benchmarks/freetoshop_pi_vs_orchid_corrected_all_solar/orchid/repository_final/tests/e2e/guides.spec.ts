import { expect, test } from '@playwright/test';

test.describe('document guides', () => {
  test.beforeEach(async ({ page }) => {
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

  test('creates vertical and horizontal guides from the View menu', async ({ page }) => {
    await page.getByRole('button', { name: 'New', exact: true }).click();
    await expect(page.getByRole('tab', { name: /Untitled/ })).toBeVisible();

    const canvas = page.locator('canvas[aria-label$=" canvas"]');
    await expect(canvas).toBeVisible();
    await expect.poll(async () => canvas.getAttribute('data-guide-count')).toBe('0');

    await page.getByRole('menuitem', { name: 'View', exact: true }).click();
    await page.getByRole('menuitem', { name: 'New Vertical Guide' }).click();
    await expect.poll(async () => canvas.getAttribute('data-guide-count')).toBe('1');

    await page.getByRole('menuitem', { name: 'View', exact: true }).click();
    await page.getByRole('menuitem', { name: 'New Horizontal Guide' }).click();
    await expect.poll(async () => canvas.getAttribute('data-guide-count')).toBe('2');

    await page.getByRole('menuitem', { name: 'View', exact: true }).click();
    await page.getByRole('menuitem', { name: 'Clear Guides' }).click();
    await expect.poll(async () => canvas.getAttribute('data-guide-count')).toBe('0');
  });

  test('drag from ruler strips creates a guide', async ({ page }) => {
    await page.getByRole('button', { name: 'New', exact: true }).click();
    await expect(page.getByRole('tab', { name: /Untitled/ })).toBeVisible();

    const canvas = page.locator('canvas[aria-label$=" canvas"]');
    await expect(canvas).toBeVisible();
    const stage = page.locator('.canvas-stage');
    const topRuler = page.locator('[data-guide-ruler="horizontal"]');
    await expect(topRuler).toBeVisible();

    const box = await stage.boundingBox();
    if (!box) throw new Error('stage bounds unavailable');
    // Drag from top ruler into the document area to place a horizontal guide.
    await topRuler.hover();
    await page.mouse.down();
    await page.mouse.move(box.x + box.width * 0.5, box.y + box.height * 0.4, { steps: 6 });
    await page.mouse.up();

    await expect
      .poll(async () => Number((await canvas.getAttribute('data-guide-count')) ?? '0'))
      .toBeGreaterThan(0);
  });

  test('double-click on a guide removes it', async ({ page }) => {
    await page.getByRole('button', { name: 'New', exact: true }).click();
    await expect(page.getByRole('tab', { name: /Untitled/ })).toBeVisible();
    const canvas = page.locator('canvas[aria-label$=" canvas"]');
    await page.getByRole('menuitem', { name: 'View', exact: true }).click();
    await page.getByRole('menuitem', { name: 'New Vertical Guide' }).click();
    await expect.poll(async () => canvas.getAttribute('data-guide-count')).toBe('1');

    // Dispatch a pointerdown with detail=2 at the vertical guide (canvas center).
    const removed = await canvas.evaluate((element) => {
      const zoom = Number(element.getAttribute('data-document-zoom') ?? '1');
      const panX = Number(element.getAttribute('data-document-x') ?? '0');
      const panY = Number(element.getAttribute('data-document-y') ?? '0');
      const docW = Number(element.getAttribute('data-document-width') ?? '0') / zoom;
      const localX = panX + (docW / 2) * zoom;
      const localY = panY + 40 * zoom;
      const rect = element.getBoundingClientRect();
      element.dispatchEvent(
        new PointerEvent('pointerdown', {
          bubbles: true,
          cancelable: true,
          clientX: rect.left + localX,
          clientY: rect.top + localY,
          pointerId: 1,
          pointerType: 'mouse',
          button: 0,
          buttons: 1,
          detail: 2,
        }),
      );
      return element.getAttribute('data-guide-count');
    });
    // Attribute updates after React paint; poll for removal.
    void removed;
    await expect
      .poll(async () => Number((await canvas.getAttribute('data-guide-count')) ?? '0'))
      .toBe(0);
  });
});
