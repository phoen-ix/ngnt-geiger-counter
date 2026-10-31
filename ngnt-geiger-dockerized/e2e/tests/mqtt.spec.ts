import { createUser, expect, flash, mqttCredentials, mqttPublish, q, randomMac, signIn, sql, test } from './support'

/* The whole pipeline a Geiger counter goes through: registered in the web UI,
 * Flask writes devices.conf and the reload flag, Mosquitto's entrypoint picks
 * them up (polls every 5 s), the counter publishes over TLS with its derived
 * credentials, the subscriber stores readings and status, the dashboard and
 * the devices page show them; deleting the device locks it out again. */

const utcNow = () => new Date().toISOString().slice(0, 19).replace('T', ' ')

test('register, connect, measure, go offline, delete', async ({ open }) => {
  const user = createUser()
  const page = await open(await signIn(user.username))
  const mac = randomMac()
  const { id, password } = mqttCredentials(mac, user.pepper!)
  await page.goto('/devices')
  await page.getByLabel('MAC Address').fill(mac)
  await page.getByLabel('Display Name (optional)').fill('Porch')
  await page.getByRole('button', { name: 'Register Device' }).click()
  await expect(flash(page)).toHaveText(`Device ${id} registered and provisioned.`)

  // Wrong password: refused. Right one: accepted once Mosquitto reloaded.
  expect(mqttPublish(id, 'wrong-password', { id, status: 'connected' }).out).toContain('not authorised')
  await expect.poll(() => mqttPublish(id, password, { id, status: 'connected' }).ok, { message: 'provisioned in Mosquitto' }).toBe(true)
  const row = page.getByRole('row').filter({ hasText: id })
  await expect.poll(async () => (await page.reload(), (await row.getByRole('cell').nth(3).textContent())?.trim())).toBe('online')
  await expect(row.getByRole('cell').nth(4)).not.toHaveText('—')

  // Reconnected: "connected" and the pending reading within the same second.
  expect(mqttPublish(id, password, [{ id, status: 'connected' }, { id, ts: utcNow(), cpm: 31, usvh: 0.1767 }]).ok).toBe(true)
  await page.goto(`/?device=${id}`)
  await expect.poll(async () => (await page.reload(), page.getByRole('row').filter({ hasText: 'Porch' }).count())).toBe(1)
  await expect(page.getByRole('row').filter({ hasText: 'Porch' }).getByRole('cell')).toHaveText([/^\d{4}-/, '31', '0.1767', 'Porch'])
  await expect(page.locator('.card').filter({ hasText: 'CPM' }).locator('.card-value')).toHaveText('31')
  await expect(page.locator('.card').filter({ hasText: 'Device' }).locator('.card-unit')).toHaveText('online')

  // The broker's last will for the counter.
  expect(mqttPublish(id, password, { id, status: 'offline' }).ok).toBe(true)
  await page.goto('/devices')
  await expect.poll(async () => (await page.reload(), (await row.getByRole('cell').nth(3).textContent())?.trim())).toBe('offline')

  page.once('dialog', (d) => d.accept())
  await row.getByRole('button', { name: 'Delete' }).click()
  await expect(flash(page)).toHaveText('Device deleted and unprovisioned.')
  await expect.poll(() => mqttPublish(id, password, { id, status: 'connected' }).out, { message: 'removed from Mosquitto' }).toContain('not authorised')
})

test("a counter cannot report readings or status for another user's device", async ({ open }) => {
  const victim = createUser()
  const attacker = createUser()
  const page = await open(await signIn(attacker.username))
  const victimMac = randomMac()
  const victimDev = mqttCredentials(victimMac, victim.pepper!)
  sql(
    `INSERT INTO devices (user_id, device_id, mac_address, mqtt_password, status, provisioned) ` +
      `VALUES (${victim.id}, ${q(victimDev.id)}, ${q(victimMac)}, ${q(victimDev.password)}, 'offline', TRUE);`,
  )
  const mac = randomMac()
  const own = mqttCredentials(mac, attacker.pepper!)
  await page.goto('/devices')
  await page.getByLabel('MAC Address').fill(mac)
  await page.getByRole('button', { name: 'Register Device' }).click()
  await expect(flash(page)).toHaveText(`Device ${own.id} registered and provisioned.`)
  await expect.poll(() => mqttPublish(own.id, own.password, { id: own.id, status: 'connected' }).ok).toBe(true)

  // On its own topic (the ACL allows only that), but naming the victim's device.
  expect(mqttPublish(own.id, own.password, { id: victimDev.id, status: 'connected' }).ok).toBe(true)
  expect(mqttPublish(own.id, own.password, { id: victimDev.id, ts: utcNow(), cpm: 9999, usvh: 56.9943 }).ok).toBe(true)
  // A reading of its own afterwards: once that is stored, the forged ones were handled.
  expect(mqttPublish(own.id, own.password, { id: own.id, ts: utcNow(), cpm: 12, usvh: 0.0684 }).ok).toBe(true)
  await expect.poll(() => sql(`SELECT COUNT(*) FROM measurements WHERE device_id = ${q(own.id)};`)).toBe('1')

  expect(sql(`SELECT COUNT(*) FROM measurements WHERE device_id = ${q(victimDev.id)};`)).toBe('0')
  expect(sql(`SELECT status, last_seen IS NULL FROM devices WHERE device_id = ${q(victimDev.id)};`)).toBe('offline\t1')
})

test('malformed messages are skipped and the next reading is stored', async ({ open }) => {
  const user = createUser()
  const page = await open(await signIn(user.username))
  const mac = randomMac()
  const dev = mqttCredentials(mac, user.pepper!)
  await page.goto('/devices')
  await page.getByLabel('MAC Address').fill(mac)
  await page.getByRole('button', { name: 'Register Device' }).click()
  await expect(flash(page)).toHaveText(`Device ${dev.id} registered and provisioned.`)
  await expect.poll(() => mqttPublish(dev.id, dev.password, { id: dev.id, status: 'connected' }).ok).toBe(true)

  for (const bad of ['not json', '[1, 2]', JSON.stringify({ id: dev.id, cpm: 5 }), JSON.stringify({ id: dev.id, ts: utcNow(), usvh: 0.1 })]) {
    expect(mqttPublish(dev.id, dev.password, bad).ok).toBe(true)
  }
  expect(mqttPublish(dev.id, dev.password, { id: dev.id, ts: utcNow(), cpm: 14, usvh: 0.0798 }).ok).toBe(true)
  await expect.poll(() => sql(`SELECT GROUP_CONCAT(cpm) FROM measurements WHERE device_id = ${q(dev.id)};`)).toBe('14')
})
