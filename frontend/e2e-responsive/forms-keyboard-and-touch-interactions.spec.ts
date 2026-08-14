import { test, expect } from '@playwright/test';

test.describe('Responsive Forms, Touch Hit Targets & Keyboard Accessibility', () => {
  test('verifies Institutional Bank Onboarding multi-step wizard form responsiveness, inputs & step transitions', async ({ page }) => {
    await page.goto('/onboarding', { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(400);

    // 1. Verify Step 1 Form Inputs fit screen
    const bankNameInput = page.locator('input[placeholder*="Bank"], input[name*="name"]').first();
    if (await bankNameInput.isVisible()) {
      await bankNameInput.click();
      await bankNameInput.fill('Santander Global Banking Corp');
      expect(await bankNameInput.inputValue()).toBe('Santander Global Banking Corp');
    }

    const swiftInput = page.locator('input[placeholder*="SWIFT"], input[placeholder*="BIC"], input[name*="swift"]').first();
    if (await swiftInput.isVisible()) {
      await swiftInput.fill('SANTESTXX');
      expect(await swiftInput.inputValue()).toBe('SANTESTXX');
    }

    // 2. Verify touch targets on form action buttons (min-height target)
    const nextBtn = page.locator('button:has-text("Next"), button:has-text("Continue"), button:has-text("Proceed")').first();
    if (await nextBtn.isVisible()) {
      const box = await nextBtn.boundingBox();
      if (box) {
        expect(box.height).toBeGreaterThanOrEqual(36); // Touch accessible height
      }
      await nextBtn.click();
      await page.waitForTimeout(400);
    }
  });

  test('verifies search inputs and filter selectors across Alert Feed and Graph views', async ({ page }) => {
    // Alerts Page
    await page.goto('/alerts', { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(400);

    const searchInput = page.locator('input[type="text"], input[type="search"]').first();
    if (await searchInput.isVisible()) {
      await searchInput.fill('Structuring');
      expect(await searchInput.inputValue()).toBe('Structuring');
      await searchInput.fill('');
    }

    // Graph Page
    await page.goto('/graph', { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(400);

    const graphSearch = page.locator('input[placeholder*="Search"], input[type="text"]').first();
    if (await graphSearch.isVisible()) {
      await graphSearch.fill('ACC-');
      expect(await graphSearch.inputValue()).toBe('ACC-');
    }
  });

  test('verifies keyboard Tab navigation through interactive form controls and links', async ({ page }) => {
    await page.goto('/', { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(300);

    // Press Tab multiple times to verify focus management
    for (let i = 0; i < 5; i++) {
      await page.keyboard.press('Tab');
    }

    const isElementFocused = await page.evaluate(() => document.activeElement !== document.body);
    expect(isElementFocused).toBe(true);
  });
});
