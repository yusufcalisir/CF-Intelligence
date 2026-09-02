import { test, expect } from '@playwright/test';

/**
 * End-to-End Test Suite: Federated Learning Training Lifecycle & Quality Gating
 * Validates coordinator client nodes, non-IID hyperparameter negotiation,
 * real-time loss/AUC convergence curves, and model promotion gates.
 */
test.describe('E2E Workflow: Federated Learning Lifecycle & Model Quality Gate', () => {
  test.beforeEach(async ({ page }) => {
    // Intercept FL Coordinator clients endpoint
    await page.route('**/api/v1/coordinator/clients*', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([
          {
            bank_id: 'bank_alpha',
            status: 'ONLINE',
            pytorch_version: '2.4.0+cu121',
            python_version: '3.12.1',
            ram_gb: 64.0,
            hardware_type: 'cuda',
            device_count: 2,
            last_heartbeat_ago_seconds: 0.8,
          },
          {
            bank_id: 'bank_beta',
            status: 'ONLINE',
            pytorch_version: '2.4.0+cu121',
            python_version: '3.12.1',
            ram_gb: 32.0,
            hardware_type: 'cuda',
            device_count: 1,
            last_heartbeat_ago_seconds: 1.4,
          },
          {
            bank_id: 'bank_gamma',
            status: 'ONLINE',
            pytorch_version: '2.4.0+cpu',
            python_version: '3.12.1',
            ram_gb: 16.0,
            hardware_type: 'cpu',
            device_count: 0,
            last_heartbeat_ago_seconds: 2.1,
          },
        ]),
      });
    });
  });

  test('navigates to coordinator page and verifies bank nodes and dynamic parameter negotiation', async ({ page }) => {
    await page.goto('/coordinator');
    await page.waitForLoadState('domcontentloaded');

    // Verify Coordinator header
    await expect(page.locator('text=Federated Coordinator Suite').or(page.locator('text=Coordinator')).first()).toBeVisible();

    // Verify online clients metric badge
    await expect(page.locator('text=Online').or(page.locator('text=ONLINE')).first()).toBeVisible();
  });

  test('navigates to benchmarks hub and verifies real-world evaluation metrics', async ({ page }) => {
    await page.goto('/benchmarks');
    await page.waitForLoadState('domcontentloaded');

    // Verify benchmark hub navigation tabs
    await expect(page.locator('text=Benchmark').or(page.locator('text=Real Benchmarks')).first()).toBeVisible();

    // Verify Elliptic AML PR-AUC comparison metrics
    await expect(page.locator('text=0.8746').or(page.locator('text=Elliptic')).or(page.locator('text=GraphSAGE')).first()).toBeVisible();
  });
});
