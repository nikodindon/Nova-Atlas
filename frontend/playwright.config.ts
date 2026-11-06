import { defineConfig } from '@playwright/test';
export default defineConfig({
  testDir: 'tests/browser',
  fullyParallel: false,
  workers: 1,
  timeout: 30000,
  use: {
    baseURL: 'http://127.0.0.1:4173',
    headless: true,
    launchOptions: {
      executablePath: process.env.CHROMIUM_PATH,
      args: ['--disable-background-networking', '--disable-dev-shm-usage'],
    },
  },
  webServer: {
    command: 'node tests/start.mjs',
    url: 'http://127.0.0.1:4173/healthz',
    timeout: 30000,
    reuseExistingServer: false,
  },
  projects: [
    { name: 'desktop', use: { viewport: { width: 1440, height: 1000 } } },
    {
      name: 'mobile',
      use: { viewport: { width: 390, height: 844 }, isMobile: true, deviceScaleFactor: 1 },
    },
  ],
});
