import {
  ADMIN_STATE, PASSWORD, addReadings, createDevice, createUser, expect, flash, mailTo, mqttCredentials, mqttPublish,
  randomMac, resetLink, signIn, sql, submitForm, test, uid,
} from './support'

/* Every action a person repeats, done twice in a row on the same page, by
 * mouse and keyboard: nothing may be left over from the first time. Done twice
 * elsewhere: rename (devices), delete after cancelling (devices, admin), save
 * the profile (account), role and visibility there and back (admin), ranges and
 * the device filter (dashboard), settings and SMTP (global). */

async function register(page: import('@playwright/test').Page, mac: string, pepper: string) {
  await page.getByLabel('MAC Address').fill(mac)
  await page.getByRole('button', { name: 'Register Device' }).click()
  await expect(flash(page)).toHaveText(`Device ${mqttCredentials(mac, pepper).id} registered and provisioned.`)
}

test('register two counters in a row; both are listed and both log in', async ({ open }) => {
  const user = createUser()
  const page = await open(await signIn(user.username))
  await page.goto('/devices')
  const macs = [randomMac(), randomMac()]
  for (const mac of macs) await register(page, mac, user.pepper!)
  for (const mac of macs) {
    const { id, password } = mqttCredentials(mac, user.pepper!)
    await expect(page.getByRole('row').filter({ hasText: id })).toHaveCount(1)
    await expect.poll(() => mqttPublish(id, password, { id, status: 'connected' }).ok, { message: `${id} provisioned` }).toBe(true)
  }
})

test('delete a counter and register the same MAC again; it logs in again', async ({ open }) => {
  const user = createUser()
  const page = await open(await signIn(user.username))
  const mac = randomMac()
  const { id, password } = mqttCredentials(mac, user.pepper!)
  await page.goto('/devices')
  await register(page, mac, user.pepper!)
  await expect.poll(() => mqttPublish(id, password, { id, status: 'connected' }).ok).toBe(true)

  page.once('dialog', (d) => d.accept())
  await page.getByRole('row').filter({ hasText: id }).getByRole('button', { name: 'Delete' }).click()
  await expect(flash(page)).toHaveText('Device deleted and unprovisioned.')
  await expect.poll(() => mqttPublish(id, password, { id, status: 'connected' }).out, { message: 'removed from Mosquitto' }).toContain('not authorised')

  await register(page, mac, user.pepper!)
  await expect(page.getByRole('row').filter({ hasText: id })).toHaveCount(1)
  await expect.poll(() => mqttPublish(id, password, { id, status: 'connected' }).ok, { message: 'provisioned again' }).toBe(true)
})

test('a refused registration, corrected on the same page, registers', async ({ open }) => {
  const user = createUser()
  const taken = createDevice(createUser())
  const page = await open(await signIn(user.username))
  await page.goto('/devices')
  await page.getByLabel('MAC Address').fill(taken.mac)
  await page.getByLabel('Display Name (optional)').fill('Shed')
  await page.getByRole('button', { name: 'Register Device' }).click()
  await expect(flash(page)).toHaveText(`Device ${taken.id} already registered.`)

  const mac = randomMac()
  await page.getByLabel('MAC Address').fill(mac)
  await page.getByRole('button', { name: 'Register Device' }).click()
  const { id } = mqttCredentials(mac, user.pepper!)
  await expect(flash(page)).toHaveText(`Device ${id} registered and provisioned.`)
  await expect(page.getByRole('row').filter({ hasText: id }).getByRole('textbox')).toHaveValue('Shed')
  await expect(page.getByLabel('MAC Address')).toHaveValue('')
})

test('change the password twice in a row; only the last one signs in', async ({ open, page }) => {
  const user = createUser()
  const account = await open(await signIn(user.username))
  await account.goto('/account')
  const passwords = [`first-${uid()}`, `second-${uid()}`]
  let current = PASSWORD
  for (const next of passwords) {
    await account.getByLabel('Current password').fill(current)
    await account.getByLabel('New password', { exact: true }).fill(next)
    await account.getByLabel('Confirm new password').fill(next)
    await account.getByRole('button', { name: 'Change Password' }).click()
    await expect(flash(account)).toHaveText('Password changed.')
    await expect(account.getByLabel('Current password')).toHaveValue('')
    current = next
  }
  expect(sql(`SELECT pw_version FROM users WHERE id = ${user.id};`)).toBe('2')
  for (const [password, message] of [[PASSWORD, 'Invalid username or password.'], [passwords[0], 'Invalid username or password.'], [passwords[1], 'Logged in.']]) {
    await submitForm(page, '/login', { Username: user.username, Password: password }, 'Login')
    await expect(flash(page)).toHaveText(message)
  }
})

test('two reset mails: the newer link sets the password, the older one is dead after that', async ({ page }) => {
  const user = createUser()
  const links: string[] = []
  for (const count of [1, 2]) {
    await submitForm(page, '/forgot-password', { Username: user.username }, 'Send Reset Link')
    await expect(flash(page)).toHaveText('If that account exists and has an email, a reset link has been sent.')
    links.push(resetLink(await mailTo(user.email, count)))
  }
  expect(links[1]).not.toBe(links[0])

  const newPassword = `new-${uid()}`
  await page.goto(links[1])
  await page.getByLabel('New password', { exact: true }).fill(newPassword)
  await page.getByLabel('Confirm new password').fill(newPassword)
  await page.getByRole('button', { name: 'Reset Password' }).click()
  await expect(flash(page)).toHaveText('Password reset. Please log in.')

  await page.goto(links[0])
  await expect(page).toHaveURL(/\/login$/)
  await expect(flash(page)).toHaveText('Invalid or expired reset link.')
})

test('sign in, out and in again through the form', async ({ page }) => {
  const user = createUser()
  for (let round = 0; round < 2; round++) {
    await submitForm(page, '/login', { Username: user.username, Password: PASSWORD }, 'Login')
    await expect(flash(page)).toHaveText('Logged in.')
    await page.getByRole('navigation').getByRole('link', { name: 'Logout' }).click()
    await expect(flash(page)).toHaveText('Logged out.')
  }
})

test('the device filter after Back: it shows what the page shows, and picking again works', async ({ open }) => {
  const user = createUser()
  const a = createDevice(user, { name: `Alpha ${uid()}` })
  const b = createDevice(user, { name: `Beta ${uid()}` })
  addReadings(a.id, [{ minutesAgo: 2, cpm: 20 }])
  addReadings(b.id, [{ minutesAgo: 1, cpm: 40 }])
  const page = await open(await signIn(user.username))
  await page.goto('/?range=6h')
  await page.getByLabel('Device:').selectOption({ label: a.name! })
  await expect(page).toHaveURL(new RegExp(`device=${a.id}$`))
  await expect(page.locator('.card').filter({ hasText: 'CPM' }).locator('.card-value')).toHaveText('20')

  await page.goBack()
  await expect(page).toHaveURL(/\?range=6h$/)
  await expect(page.locator('header p')).toContainText('device:')
  await expect(page.getByLabel('Device:')).toHaveValue('all')
  await page.getByLabel('Device:').selectOption({ label: a.name! })
  await expect(page).toHaveURL(new RegExp(`device=${a.id}$`))
})

test('a double click or a tab opened before sets a role or visibility once, and says when nothing changed', async ({ open }) => {
  const user = createUser()
  const row = (page: import('@playwright/test').Page) =>
    page.getByRole('row').filter({ has: page.getByRole('cell', { name: user.username, exact: true }) })
  const [page, staleRole, stalePublic] = [await open(ADMIN_STATE), await open(ADMIN_STATE), await open(ADMIN_STATE)]
  for (const p of [page, staleRole, stalePublic]) await p.goto('/admin')

  await row(page).getByRole('button', { name: 'user' }).dblclick()
  await expect(row(page).getByRole('button', { name: 'admin' })).toBeVisible()
  await expect(flash(page).first()).toHaveText(new RegExp(`^(Role updated\\.|${user.username} is already admin\\.)$`))
  await row(page).getByRole('button', { name: 'No' }).dblclick()
  await expect(row(page).getByRole('button', { name: 'Yes' })).toBeVisible()
  await expect(flash(page).first()).toHaveText(new RegExp(`^(Visibility updated\\.|${user.username} is already public\\.)$`))
  expect(sql(`SELECT role, public FROM users WHERE id = ${user.id};`)).toBe('admin\t1')

  // Tabs opened before still offer the old values: asking again changes nothing, and says so.
  await row(staleRole).getByRole('button', { name: 'user' }).click()
  await expect(flash(staleRole)).toHaveText(`${user.username} is already admin.`)
  await expect(row(staleRole).getByRole('button', { name: 'admin' })).toBeVisible()
  await row(stalePublic).getByRole('button', { name: 'No' }).click()
  await expect(flash(stalePublic)).toHaveText(`${user.username} is already public.`)
  await expect(row(stalePublic).getByRole('button', { name: 'Yes' })).toBeVisible()
  expect(sql(`SELECT role, public FROM users WHERE id = ${user.id};`)).toBe('admin\t1')
})
