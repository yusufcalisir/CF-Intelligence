import { test, expect } from '@playwright/test';

test.describe('Visual Regression: Enterprise Security & Theme Contrast', () => {
  test('captures security controls suite with dark mode theme styling', async ({ page }) => {
    await page.goto('/security', { waitUntil: 'domcontentloaded' });
    await page.locator('text=Enterprise Security').first().waitFor({ state: 'visible', timeout: 10000 });

    await expect(page.locator('text=Enterprise Security').first()).toBeVisible();

    await expect(page).toHaveScreenshot('security-suite-viewport.png', {
      fullPage: false,
    });
  });

  test('captures privacy defense and byzantine catalog visual alignment', async ({ page }) => {
    await page.goto('/privacy-defense', { waitUntil: 'domcontentloaded' });
    await page.locator('text=Privacy Defense').first().waitFor({ state: 'visible', timeout: 10000 });

    await expect(page.locator('text=Privacy Defense').first()).toBeVisible();

    await expect(page).toHaveScreenshot('privacy-defense-viewport.png', {
      fullPage: false,
    });
  });
});
