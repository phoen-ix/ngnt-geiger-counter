import { request } from '@playwright/test'

import {
  BASE, createDevice, createUser, expect, flash, PASSWORD, postForm, q, signIn, sql, submitForm, test, uid,
} from './support'

/* The account page: profile (email, pepper, public) and the password change,
 * which signs out the account's other sessions. */

test('update the profile: email, pepper and public visibility', async ({ open }) => {
  const user = createUser({ pepper: null })
  const dev = createDevice(user, { name: `Shared ${uid()}` })
  const page = await open(await signIn(user.username))
  await page.goto('/account')
  await expect(page.getByLabel('Username')).toHaveValue(user.username)
  await expect(page.getByLabel('Username')).toBeDisabled()
  await page.getByLabel('Email').fill(`changed.${user.email}`)
  await page.getByLabel('MQTT Pepper').fill('c0ffee42')
  await page.getByLabel('Public profile').selectOption({ label: 'Public — anyone can see your devices' })
  await page.getByRole('button', { name: 'Save Profile' }).click()
  await expect(flash(page)).toHaveText('Profile updated.')
  await expect(page.getByLabel('Email')).toHaveValue(`changed.${user.email}`)
  await expect(page.getByLabel('MQTT Pepper')).toHaveValue('c0ffee42')
  await expect(page.getByLabel('Public profile')).toHaveValue('1')

  // Public: a guest now sees the counter; with a pepper, the device form shows.
  const guest = await open()
  await guest.goto('/')
  await expect(guest.getByLabel('Device:').locator('option', { hasText: dev.name! })).toHaveCount(1)
  await page.goto('/devices')
  await expect(page.getByLabel('MAC Address')).toBeVisible()

  // Cleared fields are stored as NULL; private again hides the counter.
  await page.goto('/account')
  await page.getByLabel('Email').fill('')
  await page.getByLabel('MQTT Pepper').fill('')
  await page.getByLabel('Public profile').selectOption('0')
  await page.getByRole('button', { name: 'Save Profile' }).click()
  await expect(flash(page)).toHaveText('Profile updated.')
  expect(sql(`SELECT email IS NULL, pepper IS NULL, public FROM users WHERE id = ${user.id};`)).toBe('1\t1\t0')
  await guest.reload()
  await expect(guest.locator('option', { hasText: dev.name! })).toHaveCount(0)
})

test('change the password: wrong current, too short and mismatched are refused', async ({ open }) => {
  const user = createUser()
  const page = await open(await signIn(user.username))
  for (const [current, next, next2, message] of [
    ['not-the-password', 'new-password-1', 'new-password-1', 'Current password is incorrect.'],
    [PASSWORD, '12345', '12345', 'New password must be at least 6 characters.'],
    [PASSWORD, 'new-password-1', 'new-password-2', 'New passwords do not match.'],
  ]) {
    await page.goto('/account')
    await page.getByLabel('Current password').fill(current)
    await page.getByLabel('New password', { exact: true }).fill(next)
    await page.getByLabel('Confirm new password').fill(next2)
    await page.getByRole('button', { name: 'Change Password' }).click()
    await expect(flash(page)).toHaveText(message)
  }
  expect(sql(`SELECT pw_version FROM users WHERE id = ${user.id};`)).toBe('0')
})

test('change the password: this session stays, the others are signed out', async ({ open }) => {
  const user = createUser()
  const elsewhere = await open(await signIn(user.username))
  const page = await open(await signIn(user.username))
  const newPassword = `new-${uid()}`
  await page.goto('/account')
  await page.getByLabel('Current password').fill(PASSWORD)
  await page.getByLabel('New password', { exact: true }).fill(newPassword)
  await page.getByLabel('Confirm new password').fill(newPassword)
  await page.getByRole('button', { name: 'Change Password' }).click()
  await expect(flash(page)).toHaveText('Password changed.')
  await expect(page.getByRole('heading', { name: 'Profile' })).toBeVisible()
  await page.goto('/devices')
  await expect(page.getByRole('heading', { name: 'Your Devices' })).toBeVisible()

  await elsewhere.goto('/devices')
  await expect(elsewhere).toHaveURL(/\/login$/)
  expect(sql(`SELECT pw_version FROM users WHERE username = ${q(user.username)};`)).toBe('1')
  await submitForm(elsewhere, '/login', { Username: user.username, Password: newPassword }, 'Login')
  await expect(flash(elsewhere)).toHaveText('Logged in.')
})

test('a non-ASCII or over-long pepper and an over-long email are refused', async ({ open }) => {
  const user = createUser()
  const state = await signIn(user.username)
  const page = await open(state)
  await page.goto('/account')
  await expect(page.getByLabel('MQTT Pepper')).toHaveAttribute('maxlength', '64')
  await expect(page.getByLabel('Email')).toHaveAttribute('maxlength', '255')
  // The credentials are derived from the pepper's ASCII bytes (firmware and server).
  await page.getByLabel('MQTT Pepper').fill('grüße')
  await page.getByRole('button', { name: 'Save Profile' }).click()
  await expect(flash(page)).toHaveText('The pepper can be at most 64 characters, ASCII letters, digits and symbols only.')

  const api = await request.newContext({ baseURL: BASE, storageState: state })
  for (const [field, message] of [
    [{ pepper: 'p'.repeat(65) }, 'The pepper can be at most 64 characters, ASCII letters, digits and symbols only.'],
    [{ email: `${'e'.repeat(250)}@geiger-e2e.example` }, 'Email can be at most 255 characters.'],
  ] as const) {
    const res = await postForm(api, '/account', { action: 'update_profile', email: user.email, pepper: user.pepper!, public: '0', ...field })
    expect(res.status()).toBe(200)
    expect(await res.text()).toContain(message)
  }
  await api.dispose()
  expect(sql(`SELECT email, pepper FROM users WHERE id = ${user.id};`)).toBe(`${user.email}\t${user.pepper}`)
})
