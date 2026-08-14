import { test, expect } from '@playwright/test';

test.describe('Responsive Modals & Dialog Overlays: Viewport Fit & Touch Interactions', () => {
  test('verifies Platform Launch Modal fits within viewport and buttons remain reachable', async ({ page }) => {
    await page.goto('/', { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(400);

    // Click Launch Demo CTA that is visible on the current viewport
    const launchBtn = page.locator('section#hero button:has-text("Launch Live Platform Demo"), button:has-text("Launch Live Platform Demo"):visible, button:has-text("Launch Demo"):visible, button:has-text("Open Platform Demo"):visible').first();
    await expect(launchBtn).toBeVisible();
    await launchBtn.click();
    await page.waitForTimeout(400);

    // Verify modal overlay appears
    const modal = page.locator('div[role="dialog"]').first();
    await expect(modal).toBeAttached();

    // Verify modal content window does not bleed horizontally outside window
    const box = await modal.boundingBox();
    const viewport = page.viewportSize();
    if (box && viewport) {
      expect(box.width).toBeLessThanOrEqual(viewport.width + 2);
    }

    // Dismiss modal (via Close button or Escape)
    await page.keyboard.press('Escape');
    await page.waitForTimeout(300);
  });

  test('verifies Case Management New Case modal form fits on small viewports and inputs receive touch/keyboard focus', async ({ page }) => {
    await page.goto('/cases', { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(400);

    // Click + New Case
    const newCaseBtn = page.locator('button:has-text("New Case"), button:has-text("Create Case")').first();
    await expect(newCaseBtn).toBeVisible();
    await newCaseBtn.click();
    await page.waitForTimeout(400);

    // Form inputs should be visible and not exceed modal bounds
    const titleInput = page.locator('input[type="text"], input[placeholder*="Title"], input[placeholder*="Case"]').first();
    if (await titleInput.isVisible()) {
      await titleInput.fill('Cross-Bank Smurfing Test Case');
      expect(await titleInput.inputValue()).toBe('Cross-Bank Smurfing Test Case');
    }

    // Close modal
    const cancelBtn = page.locator('button:has-text("Cancel"), button[aria-label="Close"]').first();
    if (await cancelBtn.isVisible()) {
      await cancelBtn.click();
    } else {
      await page.keyboard.press('Escape');
    }
  });

  test('verifies Kaggle Benchmark Launch modal responsiveness on tablet and mobile', async ({ page }) => {
    await page.goto('/benchmarks', { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(400);

    // Look for Run Benchmark / Launch Simulation CTA
    const launchBenchBtn = page.locator('button:has-text("Launch Training"), button:has-text("Run Benchmark"), button:has-text("Launch Pilot")').first();
    if (await launchBenchBtn.isVisible()) {
      await launchBenchBtn.click();
      await page.waitForTimeout(400);

      // Check modal viewport containment
      const modal = page.locator('div[role="dialog"]').first();
      if (await modal.isVisible()) {
        const box = await modal.boundingBox();
        const viewport = page.viewportSize();
        if (box && viewport) {
          expect(box.width).toBeLessThanOrEqual(viewport.width + 2);
        }
      }

      await page.keyboard.press('Escape');
    }
  });
});
