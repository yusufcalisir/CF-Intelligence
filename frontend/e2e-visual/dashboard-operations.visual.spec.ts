import { test, expect } from '@playwright/test';

test.describe('Visual Regression: Live Consortium Operations & FL Dashboard', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/operations', { waitUntil: 'domcontentloaded' });
    const heading = page.locator('h1').first();
    await heading.waitFor({ state: 'attached', timeout: 15000 });
    await heading.scrollIntoViewIfNeeded();
  });

  test('captures live operations dashboard and bank nodes visual baseline', async ({ page }) => {
    const heading = page.locator('h1').first();
    await expect(heading).toBeAttached();

    await expect(page).toHaveScreenshot('operations-dashboard-viewport.png', {
      fullPage: false,
    });
  });

  test('captures full operations page with model registry and incentive panels', async ({ page }) => {
    await expect(page.locator('text=Consortium Incentive Registry').first()).toBeAttached();

    await expect(page).toHaveScreenshot('operations-fullpage.png', {
      fullPage: true,
    });
  });
});
