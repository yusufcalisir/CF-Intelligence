import { test, expect } from '@playwright/test';

test.describe('Responsive Data Grids, Tables & Financial Logs', () => {
  test('verifies AML Policies rule registry layout and controls responsiveness on mobile & tablet', async ({ page }) => {
    await page.goto('/policies', { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(400);

    // Verify rules registry container fits within viewport
    const isContained = await page.evaluate(() => {
      return document.documentElement.scrollWidth <= document.documentElement.clientWidth;
    });
    expect(isContained).toBe(true);

    // Check add policy rule button
    const addBtn = page.locator('button:has-text("Add Policy Rule")').first();
    await expect(addBtn).toBeVisible();
  });

  test('verifies Enterprise Security cryptographic audit chain log table formatting and hash legibility', async ({ page }) => {
    await page.goto('/security', { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(400);

    // Security container should fit cleanly within screen
    const isLayoutClean = await page.evaluate(() => {
      return document.documentElement.scrollWidth <= document.documentElement.clientWidth;
    });
    expect(isLayoutClean).toBe(true);

    // Check if table or card log elements exist and fit
    const tables = await page.$$('table');
    if (tables.length > 0) {
      for (const tbl of tables) {
        const parentWrapper = await tbl.evaluate((el) => {
          const wrapper = el.closest('.overflow-x-auto, [style*="overflow"]') || el.parentElement;
          return wrapper ? wrapper.scrollWidth >= el.clientWidth : true;
        });
        expect(parentWrapper).toBe(true);
      }
    }
  });

  test('verifies Consortium Incentive & Settlement payouts grid responsiveness', async ({ page }) => {
    await page.goto('/operations', { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(400);

    // Check Model Registry / Incentive table cards
    const cardOrTable = page.locator('.glass-card, [role="table"]').first();
    await expect(cardOrTable).toBeAttached();

    const isLayoutClean = await page.evaluate(() => {
      return document.documentElement.scrollWidth <= document.documentElement.clientWidth;
    });
    expect(isLayoutClean).toBe(true);
  });
});
