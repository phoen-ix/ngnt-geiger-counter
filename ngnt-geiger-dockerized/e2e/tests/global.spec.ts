import { ADMIN_STATE, BASE, addReadings, createDevice, createUser, expect, flash, signIn, sql, test } from './support'

/* Site-wide settings on the admin page. Each test changes a setting every
 * other test depends on and puts it back, so this file runs alone, after the
 * main specs, one test at a time. */

test.describe.configure({ mode: 'serial' })

const DEFAULTS: Record<string, string> = {
  'Base URL': BASE,
  'Display Timezone': 'Europe/Vienna',
  'Offline Timeout (minutes)': '5',
  'Default Alert Threshold (μSv/h)': '0.5',
  'Session Timeout (minutes)': '1440',
}

async function saveSettings(page: import('@playwright/test').Page, fields: Record<string, string>, registration = 'Open') {
  await page.goto('/admin')
  for (const [label, value] of Object.entries(fields)) await page.getByLabel(label, { exact: true }).fill(value)
  await page.getByLabel('Registration').selectOption({ label: registration })
  await page.getByRole('button', { name: 'Save Settings' }).click()
}

test.afterEach(async ({ open }) => {
  const page = await open(ADMIN_STATE)
  await saveSettings(page, DEFAULTS)
  await expect(flash(page)).toHaveText('Settings saved.')
})

test('settings are saved and shown again; the CPM factor is read-only', async ({ open }) => {
  const page = await open(ADMIN_STATE)
  await saveSettings(page, { 'Base URL': 'https://geiger.e2e.example', 'Offline Timeout (minutes)': '7' })
  await expect(flash(page)).toHaveText('Settings saved.')
  await expect(page.getByLabel('Base URL')).toHaveValue('https://geiger.e2e.example')
  await expect(page.getByLabel('Offline Timeout (minutes)')).toHaveValue('7')
  await expect(page.getByLabel('Default CPM Factor')).toBeDisabled()
  await expect(page.getByLabel('Default CPM Factor')).toHaveValue('0.0057')
})

test('registration disabled: the register page sends people to the login page', async ({ open }) => {
  const admin = await open(ADMIN_STATE)
  await saveSettings(admin, {}, 'Disabled')
  await expect(flash(admin)).toHaveText('Settings saved.')
  await expect(admin.getByLabel('Registration')).toHaveValue('0')
  const guest = await open()
  await guest.goto('/register')
  await expect(guest).toHaveURL(/\/login$/)
  await expect(flash(guest)).toHaveText('Registration is currently disabled.')
})

test('the display timezone applies to dashboard and devices dates', async ({ open }) => {
  const user = createUser()
  const dev = createDevice(user, { status: 'online', lastSeenMinutesAgo: 0 })
  sql(`UPDATE devices SET last_seen = '2026-07-01 08:30:00' WHERE device_id = '${dev.id}';`)
  sql(`INSERT INTO measurements (device_id, measured_at, cpm, usvh) VALUES ('${dev.id}', UTC_TIMESTAMP() - INTERVAL 5 MINUTE, 10, 0.057);`)
  const admin = await open(ADMIN_STATE)
  await saveSettings(admin, { 'Display Timezone': 'America/New_York' })
  await expect(flash(admin)).toHaveText('Settings saved.')
  const page = await open(await signIn(user.username))
  await page.goto('/devices')
  await expect(page.getByRole('row').filter({ hasText: dev.id }).getByRole('cell').nth(4)).toHaveText('2026-07-01 04:30:00')
  await page.goto(`/?device=${dev.id}`)
  const utc = sql(`SELECT measured_at FROM measurements WHERE device_id = '${dev.id}';`)
  const ny = new Date(`${utc.replace(' ', 'T')}Z`).toLocaleString('sv-SE', { timeZone: 'America/New_York' })
  await expect(page.locator('tbody tr td').first()).toHaveText(ny)
})

test('the offline timeout and the default alert threshold change the dashboard cards', async ({ open }) => {
  const user = createUser()
  const dev = createDevice(user, { status: 'online', lastSeenMinutesAgo: 30 })
  addReadings(dev.id, [{ minutesAgo: 1, cpm: 10 }])
  const page = await open(await signIn(user.username))
  await page.goto(`/?device=${dev.id}`)
  const deviceCard = page.locator('.card').filter({ hasText: 'Device' }).locator('.card-unit')
  const dose = page.locator('.card').filter({ hasText: 'Dose rate' }).locator('.card-value')
  await expect(deviceCard).toHaveText('offline')
  await expect(dose).not.toHaveClass(/orange/)

  const admin = await open(ADMIN_STATE)
  await saveSettings(admin, { 'Offline Timeout (minutes)': '60', 'Default Alert Threshold (μSv/h)': '0.05' })
  await expect(flash(admin)).toHaveText('Settings saved.')
  await page.reload()
  await expect(deviceCard).toHaveText('online')
  await expect(dose).toHaveClass(/orange/)
})

test('the session timeout sets how long a sign-in lasts', async ({ open }) => {
  const admin = await open(ADMIN_STATE)
  // Not below the age of the saved admin session (setup.ts): Flask rejects
  // cookies older than the lifetime, and the other worker keeps the lowered
  // value until a signed-in request reaches it.
  await saveSettings(admin, { 'Session Timeout (minutes)': '60' })
  await expect(flash(admin)).toHaveText('Settings saved.')
  const page = await open(await signIn(createUser().username))
  const res = await page.goto('/devices')
  await expect(page.getByRole('heading', { name: 'Your Devices' })).toBeVisible()
  // What the server sets, not the browser's jar.
  const setCookie = (await res!.allHeaders())['set-cookie'] ?? ''
  const left = Date.parse(/Expires=([^;]+)/i.exec(setCookie)?.[1] ?? '') / 1000 - Date.now() / 1000
  expect(left).toBeGreaterThan(59 * 60)
  expect(left).toBeLessThanOrEqual(60 * 60 + 5)
})

test('invalid settings are refused, nothing is saved and nobody is locked out', async ({ open }) => {
  const admin = await open(ADMIN_STATE)
  for (const [label, value, message] of [
    ['Session Timeout (minutes)', '', 'Session Timeout (minutes) must be a whole number of at least 1.'],
    ['Offline Timeout (minutes)', '', 'Offline Timeout (minutes) must be a whole number of at least 1.'],
    ['Default Alert Threshold (μSv/h)', 'high', 'Default Alert Threshold (μSv/h) must be a number of at least 0.'],
    ['Display Timezone', 'Mars/Olympus_Mons', 'Display Timezone must be a timezone name such as Europe/Vienna.'],
    ['Display Timezone', '', 'Display Timezone must be a timezone name such as Europe/Vienna.'],
    ['Base URL', `https://geiger.e2e.example/${'x'.repeat(250)}`, 'Base URL can be at most 255 characters.'],
  ]) {
    await saveSettings(admin, { [label]: value })
    await expect(flash(admin)).toHaveText(message)
    await admin.goto('/admin')
    await expect(admin.getByLabel(label, { exact: true })).toHaveValue(DEFAULTS[label])
  }
  // Still signed in, and the dashboard still renders for everyone.
  await expect(admin.getByRole('heading', { name: 'Global Settings' })).toBeVisible()
  const guest = await open()
  const res = await guest.goto('/')
  expect(res?.status()).toBe(200)
})

test('SMTP settings are saved and shown again, the password masked', async ({ open }) => {
  const page = await open(ADMIN_STATE)
  await page.goto('/admin')
  const before = Object.fromEntries(
    await Promise.all(['SMTP Host', 'SMTP Port', 'From Address'].map(async (l) => [l, await page.getByLabel(l).inputValue()])),
  )
  await page.getByLabel('SMTP User').fill('mailer')
  await page.getByLabel('SMTP Password').fill('e2e-smtp-secret')
  await page.getByRole('button', { name: 'Save SMTP' }).click()
  await expect(flash(page)).toHaveText('SMTP settings saved.')
  await expect(page.getByLabel('SMTP User')).toHaveValue('mailer')
  await expect(page.getByLabel('SMTP Password')).toHaveAttribute('type', 'password')
  for (const [l, v] of Object.entries(before)) await expect(page.getByLabel(l)).toHaveValue(v)
  await expect(page.getByLabel('Use STARTTLS')).toHaveValue('0')

  // An empty port would break every reset mail (int('') in the mailer).
  await page.getByLabel('SMTP Port').fill('')
  await page.getByRole('button', { name: 'Save SMTP' }).click()
  await expect(flash(page)).toHaveText('SMTP Port must be a port number (1-65535).')
  await expect(page.getByLabel('SMTP Port')).toHaveValue(before['SMTP Port'])

  // Back to Mailpit without login (setup.ts).
  await page.getByLabel('SMTP User').fill('')
  await page.getByLabel('SMTP Password').fill('')
  await page.getByRole('button', { name: 'Save SMTP' }).click()
  await expect(flash(page)).toHaveText('SMTP settings saved.')
})
