import { defineConfig, devices } from '@playwright/test';

/**
 * Playwright Multi-Device, Cross-Browser & Responsive Test Configuration
 * Tests across Mobile (iOS WebKit, Android Chromium), Tablet (iPad WebKit, Android Tablet),
 * Laptop (Firefox, WebKit, Chromium), and Desktop (1440px, 1920px).
 */
export default defineConfig({
  testDir: './',
  testMatch: ['e2e-responsive/**/*.spec.ts', 'e2e-visual/**/*.spec.ts'],
  snapshotDir: './e2e-visual/snapshots',
  snapshotPathTemplate: '{snapshotDir}/{testFileDir}/{testFileName}-snapshots/{arg}-{projectName}{ext}',
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: 2,
  reporter: [['list']],
  expect: {
    toHaveScreenshot: {
      maxDiffPixelRatio: 0.05,
      threshold: 0.2,
      animations: 'disabled',
    },
  },
  use: {
    baseURL: 'http://127.0.0.1:5173',
    trace: 'on-first-retry',
    colorScheme: 'dark',
    actionTimeout: 15000,
    navigationTimeout: 30000,
  },
  projects: [
    // ── 1. MOBILE VIEWPORTS & ENGINES ──
    {
      name: 'mobile-iphone-webkit',
      testMatch: /.*\.spec\.ts/,
      use: {
        ...devices['iPhone 13'],
        browserName: 'webkit',
      },
    },
    {
      name: 'mobile-android-chromium',
      testMatch: /.*\.spec\.ts/,
      use: {
        ...devices['Pixel 7'],
        browserName: 'chromium',
      },
    },
    {
      name: 'mobile-narrow-320',
      testMatch: /e2e-responsive\/.*\.spec\.ts/,
      use: {
        browserName: 'chromium',
        viewport: { width: 320, height: 568 },
        deviceScaleFactor: 2,
        isMobile: true,
        hasTouch: true,
      },
    },

    // ── 2. TABLET BREAKPOINTS (768px & 1024px) ──
    {
      name: 'tablet-ipad-768-webkit',
      testMatch: /.*\.spec\.ts/,
      use: {
        ...devices['iPad (gen 7)'],
        browserName: 'webkit',
      },
    },
    {
      name: 'tablet-landscape-1024-chromium',
      testMatch: /.*\.spec\.ts/,
      use: {
        ...devices['iPad (gen 7) landscape'],
        browserName: 'chromium',
      },
    },

    // ── 3. LAPTOP & DESKTOP BREAKPOINTS (1280px, 1440px, 1920px) ──
    {
      name: 'laptop-1280-firefox',
      testMatch: /.*\.spec\.ts/,
      use: {
        browserName: 'firefox',
        viewport: { width: 1280, height: 800 },
      },
    },
    {
      name: 'desktop-1440-chromium',
      testMatch: /.*\.spec\.ts/,
      use: {
        browserName: 'chromium',
        viewport: { width: 1440, height: 900 },
      },
    },
    {
      name: 'ultrawide-1920-chromium',
      testMatch: /.*\.spec\.ts/,
      use: {
        browserName: 'chromium',
        viewport: { width: 1920, height: 1080 },
      },
    },
  ],
  webServer: {
    command: 'npm run preview -- --host 127.0.0.1 --port 5173',
    url: 'http://127.0.0.1:5173',
    reuseExistingServer: true,
    timeout: 60000,
  },
});
