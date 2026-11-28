import type { Page } from '@playwright/test'

import { THE_ADMIN, expect, flash, given, logIn, nav, test, uid } from './lib'

/* Site-wide settings, from the admin's page to the screens of the people they
 * change (COVERAGE.c.md, U12-U15). They change what every other test sees, so
 * this file runs in the `global` project, one test at a time, and each test's
 * settings are put back afterwards. */

test.afterEach(() => given.settingsBack())

async function saveSettings(admin: Page, fields: Record<string, string>, registration?: 'Open' | 'Disabled') {
  await nav(admin).getByRole('link', { name: 'Admin' }).click()
  for (const [label, value] of Object.entries(fields)) await admin.getByLabel(label, { exact: true }).fill(value)
  if (registration) await admin.getByLabel('Registration').selectOption({ label: registration })
  await admin.getByRole('button', { name: 'Save Settings' }).click()
  await expect(flash(admin)).toHaveText('Settings saved.')
}

const card = (page: Page, label: string) => page.locator('.card').filter({ hasText: label })

test("U12 the display timezone the admin sets is the one a person's dates are shown in", async ({ browserOf }) => {
  const person = given.user()
  const counter = given.counter(person, { status: 'offline', lastSeenAt: '2026-07-01 08:30:00' })
  const page = await browserOf(person)
  const lastSeen = page.getByRole('row').filter({ hasText: counter.id }).getByRole('cell').nth(5)
  await nav(page).getByRole('link', { name: 'Devices' }).click()
  await expect(lastSeen).toHaveText('2026-07-01 10:30:00')

  const admin = await browserOf(THE_ADMIN)
  await saveSettings(admin, { 'Display Timezone': 'America/New_York' })
  await page.reload()
  await expect(lastSeen).toHaveText('2026-07-01 04:30:00')

  await saveSettings(admin, { 'Display Timezone': 'Europe/Vienna' })
  await page.reload()
  await expect(lastSeen).toHaveText('2026-07-01 10:30:00')
})

test("U13 the offline timeout and the default alert threshold the admin sets change a person's dashboard cards", async ({ browserOf }) => {
  const person = given.user()
  const counter = given.counter(person, { status: 'online', lastSeenMinutesAgo: 30, cpm: 10 }) // 0.0570 µSv/h
  given.publicCounter()
  const page = await browserOf(person)
  await page.getByLabel('Device:').selectOption({ label: counter.name })
  await expect(page).toHaveURL(new RegExp(`device=${counter.id}`))
  const device = card(page, 'Device').locator('.card-unit')
  const dose = card(page, 'Dose rate').locator('.card-value')
  await expect(device).toHaveText('offline')
  await expect(dose).toHaveText('0.0570')
  await expect(dose).not.toHaveClass(/orange/)

  const admin = await browserOf(THE_ADMIN)
  await saveSettings(admin, { 'Offline Timeout (minutes)': '60', 'Default Alert Threshold (μSv/h)': '0.05' })
  await page.reload()
  await expect(device).toHaveText('online')
  await expect(dose).toHaveClass(/orange/)

  await saveSettings(admin, { 'Offline Timeout (minutes)': '5', 'Default Alert Threshold (μSv/h)': '0.5' })
  await page.reload()
  await expect(device).toHaveText('offline')
  await expect(dose).toHaveText('0.0570')
  await expect(dose).not.toHaveClass(/orange/)
})

test('U14 registration the admin closes turns a guest away; opened again, the guest registers and signs in', async ({ browserOf }) => {
  const admin = await browserOf(THE_ADMIN)
  await saveSettings(admin, {}, 'Disabled')
  const guest = await browserOf()
  await guest.goto('/')
  await nav(guest).getByRole('link', { name: 'Register' }).click()
  await expect(guest).toHaveURL(/\/login$/)
  await expect(flash(guest)).toHaveText('Registration is currently disabled.')

  await saveSettings(admin, {}, 'Open')
  const newcomer = { username: `new_${uid()}`, password: `own-${uid()}` }
  await nav(guest).getByRole('link', { name: 'Register' }).click()
  await guest.getByLabel('Username').fill(newcomer.username)
  await guest.getByLabel('Password', { exact: true }).fill(newcomer.password)
  await guest.getByLabel('Confirm password').fill(newcomer.password)
  await guest.getByRole('button', { name: 'Register', exact: true }).click()
  await expect(flash(guest)).toHaveText('Account created. Please log in.')
  await logIn(guest, newcomer)
})

test('U15 with the session timeout the admin sets, a person who leaves the tab alone that long is signed out and told why', async ({ browserOf }) => {
  test.slow() // it waits out the shortest timeout there is, a minute
  const person = given.user()
  const page = await browserOf(person)
  // Not the dashboard: it reloads itself every 60 s, which keeps a session alive.
  await nav(page).getByRole('link', { name: 'Account' }).click()
  await expect(page.getByRole('heading', { name: 'Profile' })).toBeVisible()

  const admin = await browserOf(THE_ADMIN)
  await saveSettings(admin, { 'Session Timeout (minutes)': '1' })
  // Someone who signs in after the change, under the new timeout.
  const later = await browserOf(given.user())
  await nav(later).getByRole('link', { name: 'Account' }).click()
  await expect(later.getByRole('heading', { name: 'Profile' })).toBeVisible()
  // Both are away from the screen; time on the server has no event to wait for.
  await page.waitForTimeout(61_000)
  await nav(page).getByRole('link', { name: 'Devices' }).click()
  await expect(page).toHaveURL(/\/login$/)
  await expect(flash(page)).toHaveText('You were signed out after being idle')
  await nav(later).getByRole('link', { name: 'Devices' }).click()
  await expect(later).toHaveURL(/\/login$/)
  await expect(flash(later)).toHaveText('You were signed out after being idle')

  // The account is fine: signed in again, a session in use goes on.
  await logIn(page, person)
  await nav(page).getByRole('link', { name: 'Devices' }).click()
  await expect(page.getByRole('heading', { name: 'Your Devices' })).toBeVisible()
})
