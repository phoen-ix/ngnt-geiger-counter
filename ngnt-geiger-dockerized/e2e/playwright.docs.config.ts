import { defineConfig, devices } from '@playwright/test'

/* The docs tour (./docs): one screenshot per screen of a seeded demo stack,
 * and a render check of every page it visits. Run it through
 * ../scripts/docs-screenshots.sh, which starts the throwaway stack and passes
 * the env this needs (E2E_BASE_URL, DOCS_*_CONTAINER). Screenshots land in
 * ../../docs/screenshots unless DOCS_SCREENSHOT_DIR says otherwise. */
export default defineConfig({
  testDir: './docs',
  timeout: 120_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  // The seed is idempotent, but a retried render check would hide a flaky page.
  retries: 0,
  reporter: 'list',
  use: {
    ...devices['Desktop Chrome'],
    // 127.0.0.1, not localhost: the stack binds IPv4 only.
    baseURL: process.env.E2E_BASE_URL ?? 'http://127.0.0.1:1880',
    viewport: { width: 1280, height: 800 },
    deviceScaleFactor: 2,
    locale: 'en-GB',
    launchOptions: {
      args: ['--lang=en-GB'],
      env: { ...process.env, LANG: 'en_GB.UTF-8', LANGUAGE: 'en_GB:en', LC_ALL: 'en_GB.UTF-8' },
    },
    timezoneId: 'Europe/Vienna',
    contextOptions: { reducedMotion: 'reduce' },
    screenshot: 'only-on-failure',
    trace: 'off',
  },
})
