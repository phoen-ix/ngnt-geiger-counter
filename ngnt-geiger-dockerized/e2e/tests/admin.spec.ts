import { request } from '@playwright/test'

import {
  ADMIN_STATE, BASE, USER_STATE, createDevice, createUser, expect, flash, mqttPublish, postForm, q, randomMac, signIn, sql, test,
} from './support'

/* The admin page's user list: role and visibility toggles, deleting a user,
 * the admin's own row; and that only admins get in. Site-wide settings are
 * global.spec.ts. */

const rowOf = (page: import('@playwright/test').Page, username: string) =>
  page.getByRole('row').filter({ has: page.getByRole('cell', { name: username, exact: true }) })

test('the user list shows email, role, visibility, device count and creation date', async ({ open }) => {
  const user = createUser({ public: true })
  createDevice(user)
  createDevice(user)
  sql(`UPDATE users SET created_at = '2026-07-01 08:30:00' WHERE id = ${user.id};`)
  const page = await open(ADMIN_STATE)
  await page.goto('/admin')
  await expect(rowOf(page, user.username).getByRole('cell')).toHaveText([
    user.username, user.email, 'user', 'Yes', '2', '2026-07-01 10:30:00', 'Delete',
  ])
  // The admin's own row: role locked, no delete.
  const own = rowOf(page, 'admin')
  await expect(own.getByRole('button', { name: 'admin' })).toBeDisabled()
  await expect(own.getByRole('button', { name: 'Delete' })).toHaveCount(0)
})

test('promote a user to admin and back; the role applies without signing in again', async ({ open }) => {
  const user = createUser()
  const target = await open(await signIn(user.username))
  await target.goto('/')
  await expect(target.getByRole('link', { name: 'Admin' })).toHaveCount(0)

  const page = await open(ADMIN_STATE)
  await page.goto('/admin')
  await rowOf(page, user.username).getByRole('button', { name: 'user' }).click()
  await expect(flash(page)).toHaveText('Role updated.')
  await expect(rowOf(page, user.username).getByRole('button', { name: 'admin' })).toBeEnabled()

  await target.reload()
  await target.getByRole('link', { name: 'Admin' }).click()
  await expect(target.getByRole('heading', { name: 'Global Settings' })).toBeVisible()

  await rowOf(page, user.username).getByRole('button', { name: 'admin' }).click()
  await expect(flash(page)).toHaveText('Role updated.')
  await target.goto('/admin')
  await expect(target).toHaveURL(/\/$/)
})

test('toggle a user public and private', async ({ open }) => {
  const user = createUser()
  const dev = createDevice(user)
  const page = await open(ADMIN_STATE)
  const guest = await open()
  await page.goto('/admin')
  await rowOf(page, user.username).getByRole('button', { name: 'No' }).click()
  await expect(flash(page)).toHaveText('Visibility updated.')
  await guest.goto('/')
  await expect(guest.locator('option', { hasText: dev.name! })).toHaveCount(1)

  await rowOf(page, user.username).getByRole('button', { name: 'Yes' }).click()
  await expect(flash(page)).toHaveText('Visibility updated.')
  await guest.reload()
  await expect(guest.locator('option', { hasText: dev.name! })).toHaveCount(0)
})

test('delete a user after confirming: devices unprovisioned, their session ends', async ({ open }) => {
  const user = createUser()
  const target = await open(await signIn(user.username))
  await target.goto('/devices')
  await target.getByLabel('MAC Address').fill(randomMac())
  await target.getByRole('button', { name: 'Register Device' }).click()
  await expect(flash(target)).toContainText('registered and provisioned.')
  const [deviceId, password] = sql(`SELECT device_id, mqtt_password FROM devices WHERE user_id = ${user.id};`).split('\t')
  await expect.poll(() => mqttPublish(deviceId, password, { id: deviceId, status: 'connected' }).ok).toBe(true)

  const page = await open(ADMIN_STATE)
  await page.goto('/admin')
  page.once('dialog', (d) => d.dismiss())
  await rowOf(page, user.username).getByRole('button', { name: 'Delete' }).click()
  await expect(rowOf(page, user.username)).toBeVisible()

  let message = ''
  page.once('dialog', (d) => {
    message = d.message()
    return d.accept()
  })
  await rowOf(page, user.username).getByRole('button', { name: 'Delete' }).click()
  await expect(flash(page)).toHaveText('User deleted.')
  expect(message).toBe(`Delete user ${user.username}? All their devices will be unprovisioned.`)
  await expect(rowOf(page, user.username)).toHaveCount(0)
  expect(sql(`SELECT COUNT(*) FROM devices WHERE device_id = ${q(deviceId)};`)).toBe('0')
  await expect.poll(() => mqttPublish(deviceId, password, { id: deviceId, status: 'connected' }).out).toContain('not authorised')

  await target.goto('/devices')
  await expect(target).toHaveURL(/\/login$/)
})

test('a user cannot open or post to the admin page', async ({ open }) => {
  const page = await open(USER_STATE)
  await page.goto('/admin')
  await expect(page).toHaveURL(/\/$/)

  const victim = createUser()
  const api = await request.newContext({ baseURL: BASE, storageState: USER_STATE })
  const token = /name="csrf_token" value="([^"]+)"/.exec(await (await api.get('/account')).text())![1]
  for (const action of ['toggle_role', 'delete_user']) {
    const res = await api.post('/admin', { form: { csrf_token: token, action, target_user_id: String(victim.id) }, maxRedirects: 0 })
    expect(res.status()).toBe(302)
    expect(res.headers().location).toBe('/')
  }
  await api.dispose()
  expect(sql(`SELECT role FROM users WHERE id = ${victim.id};`)).toBe('user')
})

test('a malformed user id is refused', async () => {
  const api = await request.newContext({ baseURL: BASE, storageState: ADMIN_STATE })
  const res = await postForm(api, '/admin', { action: 'toggle_public', target_user_id: 'abc' })
  expect(await res.text()).toContain('Invalid request.')
  await api.dispose()
})
