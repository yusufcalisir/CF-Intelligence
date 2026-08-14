import { test, expect } from '@playwright/test';

test.describe('Visual Regression: SaaS Landing Page & Navigation', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/', { waitUntil: 'domcontentloaded' });
    await page.locator('h1').first().waitFor({ state: 'visible', timeout: 10000 });
  });

  test('captures landing hero and main viewport visual baseline', async ({ page }) => {
    await expect(page.locator('h1').first()).toBeVisible();
    await expect(page.locator('text=Collaborative Cross-Bank').first()).toBeVisible();

    await expect(page).toHaveScreenshot('landing-hero-viewport.png', {
      fullPage: false,
    });
  });

  test('captures full landing page layout across all sections', async ({ page }) => {
    await expect(page.locator('#hero')).toBeAttached();
    await expect(page.locator('#problem-solution')).toBeAttached();
    await expect(page.locator('#architecture')).toBeAttached();
    await expect(page.locator('#benchmarks')).toBeAttached();
    await expect(page.locator('#contact')).toBeAttached();

    await expect(page).toHaveScreenshot('landing-fullpage.png', {
      fullPage: true,
    });
  });

  test('captures mobile navigation drawer state on small viewports', async ({ page, isMobile }) => {
    if (!isMobile) return;

    const hamburger = page.locator('button[aria-label="Toggle Navigation Menu"]').first();
    if (await hamburger.isVisible()) {
      await hamburger.click();
      await page.waitForTimeout(400);
      await expect(page).toHaveScreenshot('landing-mobile-drawer-open.png');
    }
  });
});
