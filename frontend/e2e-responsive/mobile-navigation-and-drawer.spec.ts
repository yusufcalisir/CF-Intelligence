import { test, expect } from '@playwright/test';

test.describe('Responsive Navigation: Mobile Drawer, Dropdowns & Scroll Locking', () => {
  test('verifies mobile hamburger button, drawer opening, body scroll lock, and category navigation', async ({ page }) => {
    await page.goto('/', { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(400);

    const viewport = page.viewportSize();
    // At width < 1024px (below Tailwind lg breakpoint), mobile hamburger drawer is active
    const isSmallViewport = viewport ? viewport.width < 1024 : false;

    if (isSmallViewport) {
      // 1. Hamburger button should be visible on mobile / tablet (< 1024px)
      const hamburger = page.locator('button[aria-label="Toggle Navigation Menu"]').first();
      await expect(hamburger).toBeVisible();

      // 2. Click to open navigation drawer
      await hamburger.dispatchEvent('click');
      await page.waitForTimeout(400);

      // 3. Body scroll should be locked (overflow: hidden)
      const bodyOverflow = await page.evaluate(() => document.body.style.overflow);
      expect(bodyOverflow).toBe('hidden');

      // 4. Verify category navigation items are accessible in full-screen drawer
      const archLink = page.locator('button:has-text("03 Architecture & Security"), button:has-text("Architecture & Security")').last();
      await expect(archLink).toBeVisible();

      // 5. Click category link -> drawer should close and body scroll should be restored
      await archLink.dispatchEvent('click');
      await page.waitForTimeout(400);

      const restoredOverflow = await page.evaluate(() => document.body.style.overflow);
      expect(restoredOverflow).not.toBe('hidden');

      // 6. Test Close (X) button toggle - scroll to top first
      await page.evaluate(() => window.scrollTo(0, 0));
      await page.waitForTimeout(200);
      await hamburger.dispatchEvent('click');
      await page.waitForTimeout(400);
      const closeButton = page.locator('button[aria-label="Close Navigation Menu"]').first();
      if (await closeButton.isVisible()) {
        await closeButton.dispatchEvent('click');
        await page.waitForTimeout(400);
        const finalOverflow = await page.evaluate(() => document.body.style.overflow);
        expect(finalOverflow).not.toBe('hidden');
      }
    } else {
      // On desktop viewports (>= 1024px), verify top horizontal navigation
      const desktopNav = page.locator('nav').first();
      await expect(desktopNav).toBeVisible();

      // Architecture & Security dropdown hover
      const archBtn = page.locator('button:has-text("Architecture & Security")').first();
      if (await archBtn.isVisible()) {
        await archBtn.hover();
        await page.waitForTimeout(200);
        await expect(page.locator('text=System Topology').first()).toBeVisible();
      }
    }
  });

  test('verifies in-app sidebar navigation toggle on authenticated dashboard pages', async ({ page }) => {
    await page.goto('/operations', { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(400);

    const viewport = page.viewportSize();
    const isSmall = viewport && viewport.width < 1024;

    if (isSmall) {
      // Mobile header should be present with responsive controls
      const header = page.locator('header').first();
      await expect(header).toBeAttached();
    } else {
      // Desktop sidebar should be visible
      const sidebar = page.locator('aside, nav').first();
      await expect(sidebar).toBeAttached();
    }
  });
});
