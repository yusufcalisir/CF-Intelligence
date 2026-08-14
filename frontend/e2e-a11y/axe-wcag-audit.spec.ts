import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

const ROUTES = [
  { name: 'Landing Page', path: '/' },
  { name: 'Live Operations', path: '/operations' },
  { name: 'Graph Visualizer', path: '/graph' },
  { name: 'Federated Learning Lab', path: '/fl-lab' },
  { name: 'Counterfactual Workbench', path: '/workbench' },
  { name: 'Observability & Metrics', path: '/observability' },
  { name: 'Security & Enclave Audits', path: '/security' },
  { name: 'Case Management', path: '/cases' },
  { name: 'Policy Rules Engine', path: '/policies' },
  { name: 'Benchmark Empirical Lab', path: '/benchmarks' },
];

test.describe('Automated Axe-Core WCAG 2.1 AA Accessibility Audits', () => {
  for (const route of ROUTES) {
    test(`audit ${route.name} (${route.path}) for WCAG 2.1 AA violations`, async ({ page }) => {
      // Navigate to route
      await page.goto(route.path);
      await page.waitForLoadState('networkidle');

      // Run Axe analysis targeting WCAG 2.1 Level A & AA
      const accessibilityScanResults = await new AxeBuilder({ page })
        .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
        // Exclude third-party WebGL canvases where 3D shaders render without DOM nodes
        .exclude('canvas')
        .analyze();

      // Filter critical and serious accessibility violations
      const severeViolations = accessibilityScanResults.violations.filter(
        (v) => v.impact === 'critical' || v.impact === 'serious'
      );

      if (severeViolations.length > 0) {
        console.error(
          `Accessibility violations on ${route.path}:`,
          JSON.stringify(severeViolations, null, 2)
        );
      }

      expect(severeViolations).toEqual([]);
    });
  }

  test('audit PlatformLaunchModal active dialog state for WCAG compliance', async ({ page }) => {
    await page.goto('/');
    await page.waitForLoadState('networkidle');

    // Trigger platform demo launch modal
    const demoBtn = page.getByRole('button', { name: /open platform demo/i }).first();
    if (await demoBtn.isVisible()) {
      await demoBtn.click();
      await page.waitForSelector('[role="dialog"]');

      const accessibilityScanResults = await new AxeBuilder({ page })
        .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
        .include('[role="dialog"]')
        .analyze();

      const severeViolations = accessibilityScanResults.violations.filter(
        (v) => v.impact === 'critical' || v.impact === 'serious'
      );

      expect(severeViolations).toEqual([]);
    }
  });

  test('audit BenchmarkLaunchModal active dialog state for WCAG compliance', async ({ page }) => {
    await page.goto('/');
    await page.waitForLoadState('networkidle');

    const benchmarkBtn = page.getByRole('button', { name: /empirical benchmark/i }).first();
    if (await benchmarkBtn.isVisible()) {
      await benchmarkBtn.click();
      await page.waitForSelector('[role="dialog"]');

      const accessibilityScanResults = await new AxeBuilder({ page })
        .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
        .include('[role="dialog"]')
        .analyze();

      const severeViolations = accessibilityScanResults.violations.filter(
        (v) => v.impact === 'critical' || v.impact === 'serious'
      );

      expect(severeViolations).toEqual([]);
    }
  });
});
