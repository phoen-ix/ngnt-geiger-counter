import { defineConfig, devices } from '@playwright/test'

/* The e2e suite (./tests): every route as every role, and journeys per feature
 * against the whole stack (Flask, MariaDB, Mosquitto, the subscriber, Mailpit).
 * Run it through ../scripts/e2e.sh, which starts the stack and passes the env
 * this needs (E2E_BASE_URL, E2E_MAIL_URL). COVERAGE.md maps every feature to a
 * test.
 *
 * Projects, in order: `setup` (admin password, SMTP to Mailpit, shared
 * sessions) -> `main` (independent specs, each with its own users and devices)
 * -> `global` (specs that change site-wide settings, one at a time) ->
 * `limits` (runs into the rate limits; last, as every browser shares one IP). */
const ci = Boolean(process.env.CI)

export default defineConfig({
  testDir: './tests',
  timeout: 90_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 2,
  retries: ci ? 1 : 0,
  forbidOnly: ci,
  reporter: [['list'], ['html', { open: 'never', outputFolder: `${process.env.E2E_ARTIFACTS ?? '.'}/playwright-report` }]],
  outputDir: `${process.env.E2E_ARTIFACTS ?? '.'}/test-results`,
  use: {
    ...devices['Desktop Chrome'],
    // 127.0.0.1, not localhost: the stack binds IPv4 only.
    baseURL: process.env.E2E_BASE_URL ?? 'http://127.0.0.1:1880',
    viewport: { width: 1280, height: 800 },
    locale: 'en-GB',
    timezoneId: 'Europe/Vienna',
    actionTimeout: 15_000,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [
    { name: 'setup', testMatch: 'setup.ts' },
    { name: 'main', testMatch: /(sweep|auth|devices|mqtt|dashboard|account|admin)\.spec\.ts/, dependencies: ['setup'] },
    { name: 'global', testMatch: 'global.spec.ts', dependencies: ['main'] },
    { name: 'limits', testMatch: 'limits.spec.ts', dependencies: ['global'] },
  ],
})
