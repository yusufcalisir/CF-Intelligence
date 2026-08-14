import { test, expect } from '@playwright/test';

test.describe('Responsive Quality: Zero Horizontal Overflow Across Breakpoints', () => {
  const routes = [
    { name: 'Landing Page', path: '/' },
    { name: 'Live Operations', path: '/operations' },
    { name: 'AML Alerts Feed', path: '/alerts' },
    { name: 'Case Management', path: '/cases' },
    { name: 'Enterprise Security', path: '/security' },
    { name: 'Privacy Defense & Attacks', path: '/privacy-defense' },
    { name: 'Institutional Bank Onboarding', path: '/onboarding' },
    { name: 'Kaggle Benchmark Hub', path: '/benchmarks' },
    { name: 'Observability & Drift', path: '/observability' },
    { name: 'AML Rules & Policies', path: '/policies' },
  ];

  for (const route of routes) {
    test(`verifies zero horizontal overflow on ${route.name} (${route.path})`, async ({ page }) => {
      await page.goto(route.path, { waitUntil: 'domcontentloaded' });
      await page.waitForTimeout(400);

      // Check root document scroll width vs client width
      const overflowMetrics = await page.evaluate(() => {
        const docEl = document.documentElement;
        const body = document.body;
        const clientWidth = docEl.clientWidth;
        const scrollWidth = Math.max(docEl.scrollWidth, body.scrollWidth);
        const windowWidth = window.innerWidth;

        // Check if any element bleeds outside the viewport width
        const elements = Array.from(document.querySelectorAll('*'));
        let maxRight = clientWidth;
        let offendingElementTag = '';
        let offendingElementClass = '';

        for (const el of elements) {
          const rect = el.getBoundingClientRect();
          // Filter out elements that are hidden or fixed/sticky full-width elements
          if (rect.width > 0 && rect.height > 0) {
            if (rect.right > clientWidth + 2) {
              // Ignore intentional horizontal scroll containers
              const style = window.getComputedStyle(el);
              const parentOverflow = window.getComputedStyle(el.parentElement || el).overflowX;
              if (parentOverflow !== 'auto' && parentOverflow !== 'scroll') {
                if (rect.right > maxRight) {
                  maxRight = rect.right;
                  offendingElementTag = el.tagName;
                  offendingElementClass = el.className?.toString?.() || '';
                }
              }
            }
          }
        }

        return {
          clientWidth,
          scrollWidth,
          windowWidth,
          hasHorizontalScrollbar: scrollWidth > clientWidth + 1,
          maxRight,
          offendingElementTag,
          offendingElementClass: offendingElementClass.slice(0, 100),
        };
      });

      expect(
        overflowMetrics.hasHorizontalScrollbar,
        `Detected horizontal scroll on ${route.name}: scrollWidth (${overflowMetrics.scrollWidth}px) exceeds clientWidth (${overflowMetrics.clientWidth}px). MaxRight: ${overflowMetrics.maxRight}px on <${overflowMetrics.offendingElementTag}>`
      ).toBe(false);
    });
  }

  test('maintains layout integrity during dynamic window resize and orientation change', async ({ page }) => {
    await page.goto('/', { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(300);

    // 1. Mobile Portrait (375x812)
    await page.setViewportSize({ width: 375, height: 812 });
    await page.waitForTimeout(200);
    let isOverflowing = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth);
    expect(isOverflowing).toBe(false);

    // 2. Mobile Landscape (812x375)
    await page.setViewportSize({ width: 812, height: 375 });
    await page.waitForTimeout(200);
    isOverflowing = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth);
    expect(isOverflowing).toBe(false);

    // 3. Tablet Portrait (768x1024)
    await page.setViewportSize({ width: 768, height: 1024 });
    await page.waitForTimeout(200);
    isOverflowing = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth);
    expect(isOverflowing).toBe(false);

    // 4. Large Desktop (1440x900)
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.waitForTimeout(200);
    isOverflowing = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth);
    expect(isOverflowing).toBe(false);
  });
});
