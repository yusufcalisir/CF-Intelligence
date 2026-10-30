import { defineConfig, devices } from '@playwright/test';

/**
 * Playwright configuration for Live Consortium Browser Acceptance Verification.
 *
 * Targets the live 10-service Docker consortium gateway (default: http://127.0.0.1:80).
 * Enforces zero request mocking and disables trace/screenshot/video persistence
 * to prevent accidental capture of bearer tokens or institutional credentials.
 */
export default defineConfig({
  testDir: './e2e-consortium',
  timeout: 60000,
  expect: {
    timeout: 15000,
  },
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: 0,
  workers: 1,
  reporter: 'list',
  use: {
    baseURL: process.env.CFI_GATEWAY_URL || 'http://127.0.0.1:80',
    trace: 'off',
    screenshot: 'off',
    video: 'off',
    viewport: { width: 1440, height: 900 },
  },
  projects: [
    {
      name: 'chromium-consortium-live',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
});

