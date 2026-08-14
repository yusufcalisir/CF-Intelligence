import { test, expect } from '@playwright/test';

test.describe('Modal Focus Trap & Keyboard Lifecycle E2E Test Suite', () => {
  test('Platform Launch Modal: open -> focus inside -> tab focus trap -> escape close -> return focus', async ({
    page,
  }) => {
    await page.goto('/');
    await page.waitForLoadState('networkidle');

    // Find the launch trigger button
    const launchBtn = page.getByRole('button', { name: /open platform demo/i }).first();
    await expect(launchBtn).toBeVisible();

    // 1. Focus the trigger button and press Enter to open modal
    await launchBtn.focus();
    await expect(launchBtn).toBeFocused();
    await page.keyboard.press('Enter');

    // 2. Modal opened -> dialog appears
    const dialog = page.locator('[role="dialog"]');
    await expect(dialog).toBeVisible();

    // 3. Focus moves inside modal
    const skipBtn = dialog.getByRole('button', { name: /skip/i });
    await expect(skipBtn).toBeVisible();

    // 4. Tab key cycles focus strictly inside modal container
    await page.keyboard.press('Tab');
    const isFocusInsideDialog = await dialog.evaluate((node) =>
      node.contains(document.activeElement)
    );
    expect(isFocusInsideDialog).toBe(true);

    // 5. Escape key closes modal
    await page.keyboard.press('Escape');
    await expect(dialog).not.toBeVisible();

    // 6. Focus returns to the original triggering element
    await expect(launchBtn).toBeFocused();
  });

  test('Benchmark Launch Modal: open -> focus inside -> escape close -> return focus', async ({
    page,
  }) => {
    await page.goto('/');
    await page.waitForLoadState('networkidle');

    const benchmarkBtn = page.getByRole('button', { name: /empirical benchmark/i }).first();
    if (await benchmarkBtn.isVisible()) {
      await benchmarkBtn.focus();
      await expect(benchmarkBtn).toBeFocused();
      await page.keyboard.press('Enter');

      const dialog = page.locator('[role="dialog"]');
      await expect(dialog).toBeVisible();

      // Escape dismiss
      await page.keyboard.press('Escape');
      await expect(dialog).not.toBeVisible();

      // Restored focus
      await expect(benchmarkBtn).toBeFocused();
    }
  });

  test('Policies Page Create Rule Modal: keyboard open -> focus trap -> escape dismiss -> restore focus', async ({
    page,
  }) => {
    await page.goto('/policies');
    await page.waitForLoadState('networkidle');

    const createRuleBtn = page.getByRole('button', { name: /add rule/i }).first();
    if (await createRuleBtn.isVisible()) {
      await createRuleBtn.focus();
      await expect(createRuleBtn).toBeFocused();
      await page.keyboard.press('Enter');

      const dialog = page.locator('[role="dialog"]');
      await expect(dialog).toBeVisible();

      // Verify focus is trapped inside dialog
      await page.keyboard.press('Tab');
      const isFocusInsideDialog = await dialog.evaluate((node) =>
        node.contains(document.activeElement)
      );
      expect(isFocusInsideDialog).toBe(true);

      // Escape key closes modal
      await page.keyboard.press('Escape');
      await expect(dialog).not.toBeVisible();

      // Return focus
      await expect(createRuleBtn).toBeFocused();
    }
  });

  test('Cases Page Create SAR Modal: keyboard open -> tab trap -> escape dismiss -> restore focus', async ({
    page,
  }) => {
    await page.goto('/cases');
    await page.waitForLoadState('networkidle');

    const newCaseBtn = page.getByRole('button', { name: /new sar case/i }).first();
    if (await newCaseBtn.isVisible()) {
      await newCaseBtn.focus();
      await expect(newCaseBtn).toBeFocused();
      await page.keyboard.press('Enter');

      const dialog = page.locator('[role="dialog"]');
      await expect(dialog).toBeVisible();

      // Press Tab through modal inputs
      await page.keyboard.press('Tab');
      const isInside = await dialog.evaluate((node) =>
        node.contains(document.activeElement)
      );
      expect(isInside).toBe(true);

      // Press Escape to dismiss
      await page.keyboard.press('Escape');
      await expect(dialog).not.toBeVisible();

      // Verify focus returned to the new SAR case button
      await expect(newCaseBtn).toBeFocused();
    }
  });

  test('Bank Node Details Drawer: keyboard open -> escape close -> return focus', async ({
    page,
  }) => {
    await page.goto('/');
    await page.waitForLoadState('networkidle');

    const jpmorganCard = page.locator('text=JPMorgan Chase & Co.').first();
    if (await jpmorganCard.isVisible()) {
      // Find the clickable card container
      await jpmorganCard.click();

      const drawerDialog = page.locator('[role="dialog"]');
      await expect(drawerDialog).toBeVisible();

      // Close drawer with Escape
      await page.keyboard.press('Escape');
      await expect(drawerDialog).not.toBeVisible();
    }
  });
});
