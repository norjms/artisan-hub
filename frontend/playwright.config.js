// Screen checks for every route: run against a hub that is already running (BASE_URL, default
// http://127.0.0.1:8080) with the development sign-in (HUB_DEV_AUTH_BYPASS=true). Screenshots go to SHOTS_DIR.
import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: 'tests',
  timeout: 60_000,
  workers: 1,
  reporter: [['list']],
  use: {
    baseURL: process.env.BASE_URL || 'http://127.0.0.1:8080',
    launchOptions: process.env.PW_CHROMIUM ? { executablePath: process.env.PW_CHROMIUM } : {}
  },
  projects: [
    { name: '1920x1080', use: { viewport: { width: 1920, height: 1080 } } },
    { name: '1280x860', use: { viewport: { width: 1280, height: 860 } } }
  ]
});
