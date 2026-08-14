import { test, expect } from '@playwright/test';

test.describe('Visual Regression: AML Alerts Intelligence & Case Ledger', () => {
  test('captures AML alerts page stream and filter toolbar visual baseline', async ({ page }) => {
    await page.goto('/alerts', { waitUntil: 'domcontentloaded' });
    await page.locator('text=Alert Intelligence').first().waitFor({ state: 'visible', timeout: 10000 });

    await expect(page.locator('text=Alert Intelligence').first()).toBeVisible();

    await expect(page).toHaveScreenshot('alerts-feed-viewport.png', {
      fullPage: false,
    });
  });

  test('captures AML case management overview layout', async ({ page }) => {
    await page.goto('/cases', { waitUntil: 'domcontentloaded' });
    await page.locator('text=Case Management').first().waitFor({ state: 'visible', timeout: 10000 });

    await expect(page.locator('text=Case Management').first()).toBeVisible();

    await expect(page).toHaveScreenshot('cases-management-viewport.png', {
      fullPage: false,
    });
  });
});
