import { request } from '@playwright/test'

import {
  BASE, createDevice, createUser, expect, flash, mqttCredentials, postForm, q, randomMac, signIn, sql, test,
} from './support'

/* The devices page: register, rename and delete counters, the no-pepper state,
 * and that nobody can touch another user's devices. Provisioning reaching
 * Mosquitto is mqtt.spec.ts. */

test('register a device: credentials derived like the firmware, listed as not yet seen', async ({ open }) => {
  const user = createUser()
  const page = await open(await signIn(user.username))
  const mac = randomMac()
  const { id, password } = mqttCredentials(mac, user.pepper!)
  await page.goto('/devices')
  await page.getByLabel('MAC Address').fill(mac.toLowerCase())
  await page.getByLabel('Display Name (optional)').fill('Rooftop')
  await page.getByRole('button', { name: 'Register Device' }).click()
  await expect(flash(page)).toHaveText(`Device ${id} registered and provisioned.`)

  const row = page.getByRole('row').filter({ hasText: id })
  await expect(row.getByRole('cell')).toHaveText([id, 'Save', mac, 'offline', '—', 'Yes', 'Delete'])
  await expect(row.getByRole('textbox')).toHaveValue('Rooftop')
  expect(sql(`SELECT mac_address, mqtt_password FROM devices WHERE device_id = ${q(id)};`)).toBe(`${mac}\t${password}`)
})

test('a malformed MAC is refused, in the browser and by the server', async ({ open }) => {
  const user = createUser()
  const page = await open(await signIn(user.username))
  await page.goto('/devices')
  const mac = page.getByLabel('MAC Address')
  await mac.fill('5C:CF:7F:3A:91')
  await page.getByRole('button', { name: 'Register Device' }).click()
  expect(await mac.evaluate((el: HTMLInputElement) => el.validity.patternMismatch)).toBe(true)

  await mac.evaluate((el) => el.removeAttribute('pattern'))
  await page.getByRole('button', { name: 'Register Device' }).click()
  await expect(flash(page)).toHaveText('Invalid MAC address format. Use AA:BB:CC:DD:EE:FF.')
  expect(sql(`SELECT COUNT(*) FROM devices WHERE user_id = ${user.id};`)).toBe('0')
})

test('a MAC whose device ID is taken, by anyone, is refused', async ({ open }) => {
  const other = createUser()
  const taken = createDevice(other)
  const user = createUser()
  const page = await open(await signIn(user.username))
  await page.goto('/devices')
  await page.getByLabel('MAC Address').fill(taken.mac)
  await page.getByRole('button', { name: 'Register Device' }).click()
  await expect(flash(page)).toHaveText(`Device ${taken.id} already registered.`)
  await expect(page.getByText('No devices registered yet.')).toBeVisible()
})

test('without a pepper there is no form, only a link to the account page', async ({ open }) => {
  const user = createUser({ pepper: null })
  const state = await signIn(user.username)
  const page = await open(state)
  await page.goto('/devices')
  await expect(page.getByLabel('MAC Address')).toHaveCount(0)
  await expect(page.getByText('No devices registered yet.')).toBeVisible()
  await page.getByRole('link', { name: 'account settings' }).click()
  await expect(page.getByRole('heading', { name: 'Profile' })).toBeVisible()

  // The form's POST without a pepper is refused by the server too.
  const api = await request.newContext({ baseURL: BASE, storageState: state })
  const res = await postForm(api, '/devices', { action: 'add_device', mac_address: randomMac(), display_name: '' }, '/account')
  expect(await res.text()).toContain('Set a pepper in your account settings first.')
  await api.dispose()
  expect(sql(`SELECT COUNT(*) FROM devices WHERE user_id = ${user.id};`)).toBe('0')
})

test('rename a device, and clear its name back to the device ID', async ({ open }) => {
  const user = createUser()
  const dev = createDevice(user, { name: 'Kitchen' })
  const page = await open(await signIn(user.username))
  await page.goto('/devices')
  const name = page.getByRole('row').filter({ hasText: dev.id }).getByRole('textbox')
  await name.fill('Attic')
  await page.getByRole('row').filter({ hasText: dev.id }).getByRole('button', { name: 'Save' }).click()
  await expect(flash(page)).toHaveText('Device updated.')
  await expect(name).toHaveValue('Attic')

  await name.fill('')
  await page.getByRole('row').filter({ hasText: dev.id }).getByRole('button', { name: 'Save' }).click()
  await expect(flash(page)).toHaveText('Device updated.')
  await expect(name).toHaveValue('')
  await expect(name).toHaveAttribute('placeholder', dev.id)
  expect(sql(`SELECT display_name IS NULL FROM devices WHERE device_id = ${q(dev.id)};`)).toBe('1')
})

test('delete a device after confirming; cancelling keeps it', async ({ open }) => {
  const user = createUser()
  const page = await open(await signIn(user.username))
  await page.goto('/devices')
  const mac = randomMac()
  const { id } = mqttCredentials(mac, user.pepper!)
  await page.getByLabel('MAC Address').fill(mac)
  await page.getByRole('button', { name: 'Register Device' }).click()
  const row = page.getByRole('row').filter({ hasText: id })

  page.once('dialog', (d) => d.dismiss())
  await row.getByRole('button', { name: 'Delete' }).click()
  await expect(row).toBeVisible()

  let message = ''
  page.once('dialog', (d) => {
    message = d.message()
    return d.accept()
  })
  await row.getByRole('button', { name: 'Delete' }).click()
  await expect(flash(page)).toHaveText('Device deleted and unprovisioned.')
  expect(message).toBe('Delete this device? This will also remove it from Mosquitto.')
  await expect(row).toHaveCount(0)
  expect(sql(`SELECT COUNT(*) FROM devices WHERE device_id = ${q(id)};`)).toBe('0')
})

test("only your own devices are listed, and another user's cannot be renamed or deleted", async ({ open }) => {
  const owner = createUser()
  const theirs = createDevice(owner, { name: 'Not yours' })
  const dbId = sql(`SELECT id FROM devices WHERE device_id = ${q(theirs.id)};`)
  const user = createUser()
  const mine = createDevice(user, { name: 'Mine' })
  const state = await signIn(user.username)
  const page = await open(state)
  await page.goto('/devices')
  await expect(page.getByRole('row').filter({ hasText: mine.id })).toBeVisible()
  await expect(page.getByRole('row').filter({ hasText: theirs.id })).toHaveCount(0)

  const api = await request.newContext({ baseURL: BASE, storageState: state })
  await postForm(api, '/devices', { action: 'update_device', device_db_id: dbId, display_name: 'Hijacked' })
  await postForm(api, '/devices', { action: 'delete_device', device_db_id: dbId })
  await api.dispose()
  expect(sql(`SELECT display_name FROM devices WHERE device_id = ${q(theirs.id)};`)).toBe('Not yours')
})

test('a display name over 100 characters is refused, not a server error', async ({ open }) => {
  const user = createUser()
  const dev = createDevice(user, { name: 'Short' })
  const dbId = sql(`SELECT id FROM devices WHERE device_id = ${q(dev.id)};`)
  const state = await signIn(user.username)
  const page = await open(state)
  await page.goto('/devices')
  await expect(page.getByLabel('Display Name (optional)')).toHaveAttribute('maxlength', '100')
  await expect(page.getByRole('row').filter({ hasText: dev.id }).getByRole('textbox')).toHaveAttribute('maxlength', '100')

  // Past the browser's limit, as a script would post it.
  const api = await request.newContext({ baseURL: BASE, storageState: state })
  for (const form of [
    { action: 'add_device', mac_address: randomMac(), display_name: 'x'.repeat(101) },
    { action: 'update_device', device_db_id: dbId, display_name: 'x'.repeat(101) },
  ]) {
    const res = await postForm(api, '/devices', form)
    expect(res.status(), form.action).toBe(200)
    expect(await res.text()).toContain('Display name can be at most 100 characters.')
  }
  await api.dispose()
  expect(sql(`SELECT GROUP_CONCAT(display_name) FROM devices WHERE user_id = ${user.id};`)).toBe('Short')
})
