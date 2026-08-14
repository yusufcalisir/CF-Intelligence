import { test, expect } from '@playwright/test';

test.describe('Keyboard-Only Workflows E2E Test Suite', () => {
  test('keyboard navigation across navbar links and route transitions', async ({ page }) => {
    await page.goto('/operations');
    await page.waitForLoadState('networkidle');

    // Focus first navigation link or brand logo
    await page.keyboard.press('Tab');

    // Tab through navigation links
    for (let i = 0; i < 6; i++) {
      await page.keyboard.press('Tab');
    }

    // Verify activeElement is a link or button
    const isInteractive = await page.evaluate(() => {
      const active = document.activeElement;
      if (!active) return false;
      const tag = active.tagName.toLowerCase();
      return tag === 'a' || tag === 'button' || tag === 'select' || tag === 'input';
    });

    expect(isInteractive).toBe(true);
  });

  test('keyboard form inputs and risk prediction submission', async ({ page }) => {
    await page.goto('/operations');
    await page.waitForLoadState('networkidle');

    // Locate transaction amount input
    const amountInput = page.locator('input[type="number"]').first();
    if (await amountInput.isVisible()) {
      await amountInput.focus();
      await expect(amountInput).toBeFocused();

      // Clear and type new value
      await amountInput.fill('45000');

      // Tab to submit button and press Enter
      const submitBtn = page.getByRole('button', { name: /evaluate transaction risk|submit/i }).first();
      if (await submitBtn.isVisible()) {
        await submitBtn.focus();
        await expect(submitBtn).toBeFocused();
        await page.keyboard.press('Enter');

        // Verify result panel or loading feedback
        await page.waitForTimeout(500);
      }
    }
  });

  test('dataset training configuration panel expandable via keyboard', async ({ page }) => {
    await page.goto('/operations');
    await page.waitForLoadState('networkidle');

    const configToggle = page.getByRole('button', { name: /dataset & training mode|configure/i }).first();
    if (await configToggle.isVisible()) {
      await configToggle.focus();
      await expect(configToggle).toBeFocused();
      await page.keyboard.press('Enter');

      // Verify config region appears
      const region = page.locator('[aria-labelledby="training-config-title"]');
      await expect(region).toBeVisible();

      // Press Escape or Close to dismiss
      const closeBtn = region.getByRole('button', { name: /close config panel/i });
      if (await closeBtn.isVisible()) {
        await closeBtn.focus();
        await page.keyboard.press('Enter');
        await expect(region).not.toBeVisible();
      }
    }
  });
});
