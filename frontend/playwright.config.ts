import { defineConfig } from '@playwright/test';

/**
 * Playwright Visual Regression Testing (VRT) Configuration
 * Uses Vite preview server for deterministic, high-performance visual snapshot rendering.
 */
export default defineConfig({
  testDir: './e2e-visual',
  snapshotDir: './e2e-visual/snapshots',
  snapshotPathTemplate: '{snapshotDir}/{testFileDir}/{testFileName}-snapshots/{arg}-{projectName}{ext}',
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: 0,
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
    {
      name: 'desktop-standard',
      use: {
        browserName: 'chromium',
        viewport: { width: 1280, height: 800 },
        deviceScaleFactor: 1,
      },
    },
    {
      name: 'desktop-wide',
      use: {
        browserName: 'chromium',
        viewport: { width: 1920, height: 1080 },
        deviceScaleFactor: 1,
      },
    },
    {
      name: 'tablet',
      use: {
        browserName: 'chromium',
        viewport: { width: 768, height: 1024 },
        deviceScaleFactor: 1,
      },
    },
    {
      name: 'mobile',
      use: {
        browserName: 'chromium',
        viewport: { width: 375, height: 812 },
        deviceScaleFactor: 1,
        isMobile: true,
        hasTouch: true,
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
