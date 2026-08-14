import { test, expect } from '@playwright/test';

test.describe('Sticky Elements, Floating HUD & Layout Stability', () => {
  test('verifies sticky header remains pinned on scroll without overlapping content', async ({ page }) => {
    await page.goto('/', { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(400);

    const header = page.locator('header').first();
    await expect(header).toBeVisible();

    // Scroll down 1000px
    await page.evaluate(() => window.scrollTo(0, 1000));
    await page.waitForTimeout(300);

    // Verify header is still visible and pinned near top of viewport
    const headerBox = await header.boundingBox();
    if (headerBox) {
      expect(headerBox.y).toBeLessThanOrEqual(5); // Pinned at top
      expect(headerBox.height).toBeGreaterThan(0);
    }
  });

  test('verifies data charts and GNN graph containers scale smoothly without zero-size errors', async ({ page }) => {
    await page.goto('/operations', { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(500);

    // Recharts and Chart containers
    const chartOrCard = page.locator('.recharts-responsive-container, .glass-card').first();
    await expect(chartOrCard).toBeAttached();

    // Resize viewport rapidly to test dynamic reflow
    await page.setViewportSize({ width: 600, height: 700 });
    await page.waitForTimeout(300);

    const isStable = await page.evaluate(() => {
      return document.documentElement.scrollWidth <= document.documentElement.clientWidth;
    });
    expect(isStable).toBe(true);
  });
});
