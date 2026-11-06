import { ADMIN_STATE, addReadings, createDevice, createUser, expect, q, signIn, sql, test, uid } from './support'

/* The dashboard: who sees which counters, the device filter, the time ranges,
 * the cards (latest reading, alert colour, online state, count), the chart,
 * the empty state and the auto-refresh. Readings are SQL rows relative to now;
 * other tests' public counters may be listed too, so each test filters to
 * its own. */

const card = (page: import('@playwright/test').Page, label: string) => page.locator('.card').filter({ hasText: label })
const options = (page: import('@playwright/test').Page) => page.getByLabel('Device:').locator('option')
const rows = (page: import('@playwright/test').Page) => page.getByRole('row').filter({ has: page.getByRole('cell') })
const tab = (page: import('@playwright/test').Page, label: string) => page.getByRole('link', { name: label, exact: true })

test('visibility: guests see public counters, users also their own, admins all', async ({ open }) => {
  const tag = uid()
  const pub = createUser({ public: true })
  const priv = createUser()
  const viewer = createUser()
  const devPub = createDevice(pub, { name: `Public ${tag}` })
  const devPriv = createDevice(priv, { name: `Private ${tag}` })
  const devOwn = createDevice(viewer, { name: `Own ${tag}` })
  createDevice(createUser({ public: true })) // the device filter needs two visible counters, even for the guest
  for (const d of [devPub, devPriv, devOwn]) addReadings(d.id, [{ minutesAgo: 5, cpm: 15 }])

  const sees = async (state: Parameters<typeof open>[0]) => {
    const page = await open(state)
    await page.goto('/')
    return (await options(page).allTextContents()).filter((t) => t.endsWith(tag)).sort()
  }
  expect(await sees(undefined)).toEqual([`Public ${tag}`])
  expect(await sees(await signIn(viewer.username))).toEqual([`Own ${tag}`, `Public ${tag}`])
  expect(await sees(ADMIN_STATE)).toEqual([`Own ${tag}`, `Private ${tag}`, `Public ${tag}`])

  // A private counter's ID in the URL falls back to all devices.
  const guest = await open()
  await guest.goto(`/?device=${devPriv.id}`)
  await expect(guest.getByLabel('Device:')).toHaveValue('all')
  await expect(guest.getByRole('cell', { name: `Private ${tag}` })).toHaveCount(0)
})

test('the device filter shows one counter, keeps the range and the header drops the device name', async ({ open }) => {
  const user = createUser()
  const a = createDevice(user, { name: `Alpha ${uid()}`, status: 'online', lastSeenMinutesAgo: 1 })
  const b = createDevice(user, { name: `Beta ${uid()}` })
  addReadings(a.id, [{ minutesAgo: 3, cpm: 20 }, { minutesAgo: 2, cpm: 21 }])
  addReadings(b.id, [{ minutesAgo: 1, cpm: 40 }])
  const page = await open(await signIn(user.username))
  await page.goto('/?range=6h')
  await page.getByLabel('Device:').selectOption({ label: a.name! })
  await expect(page).toHaveURL(new RegExp(`\\?range=6h&device=${a.id}$`))
  await expect(page.getByRole('heading', { name: 'Last 6 hours', exact: true })).toBeVisible()
  await expect(page.locator('header p')).toContainText('Last reading:')
  await expect(page.locator('header p')).not.toContainText('device:')
  await expect(rows(page)).toHaveCount(2)
  await expect(rows(page).getByRole('cell', { name: a.name!, exact: true })).toHaveCount(2)
  await expect(card(page, 'Device').locator('.card-value')).toHaveText(a.name!)
  await expect(card(page, 'Device').locator('.card-unit')).toHaveText('online')
  await expect(card(page, 'CPM').locator('.card-value')).toHaveText('21')

  await page.getByLabel('Device:').selectOption('all')
  await expect(page).toHaveURL(/\?range=6h&device=all$/)
  await expect(page.locator('header p')).toContainText('device:')
})

test('the time ranges: each lists the readings inside it', async ({ open }) => {
  const user = createUser()
  const dev = createDevice(user)
  addReadings(dev.id, [
    { minutesAgo: 30, cpm: 11 },
    { minutesAgo: 3 * 60, cpm: 12 },
    { minutesAgo: 12 * 60, cpm: 13 },
    { minutesAgo: 3 * 24 * 60, cpm: 14 },
    { minutesAgo: 9 * 24 * 60, cpm: 15 },
  ])
  const page = await open(await signIn(user.username))
  await page.goto(`/?device=${dev.id}`)
  await expect(tab(page, 'Last 24 hours')).toHaveClass('active')
  for (const [label, count] of [['Last hour', 1], ['Last 6 hours', 2], ['Last 24 hours', 3], ['Last 7 days', 4]] as const) {
    await tab(page, label).click()
    await expect(tab(page, label)).toHaveClass('active')
    await expect(page.getByRole('heading', { name: label, exact: true })).toBeVisible()
    await expect(page.getByRole('heading', { name: `Recent measurements — ${label.toLowerCase()}` })).toBeVisible()
    await expect(rows(page)).toHaveCount(count)
    expect(new URL(page.url()).searchParams.get('device')).toBe(dev.id)
    const points = await page.evaluate(() => (window as any).Chart.getChart('chart').data.datasets[0].data.length)
    expect(points).toBe(count)
  }
  // An unknown range is the default one.
  await page.goto(`/?range=1y&device=${dev.id}`)
  await expect(tab(page, 'Last 24 hours')).toHaveClass('active')
  // The page reloads itself every minute with the same range and device.
  await page.goto(`/?range=6h&device=${dev.id}`)
  await expect(page.locator('meta[http-equiv="refresh"]')).toHaveAttribute('content', `60;url=?range=6h&device=${dev.id}`)
})

test('cards: dose rate turns orange above the alert threshold; 100+ readings', async ({ open }) => {
  const user = createUser()
  const calm = createDevice(user)
  const hot = createDevice(user, { alert: 0.05 })
  addReadings(calm.id, [{ minutesAgo: 1, cpm: 20 }])
  addReadings(hot.id, Array.from({ length: 120 }, (_, i) => ({ minutesAgo: i + 1, cpm: 20 })))
  const page = await open(await signIn(user.username))

  await page.goto(`/?device=${calm.id}`)
  await expect(card(page, 'Dose rate').locator('.card-value')).toHaveText('0.1140')
  await expect(card(page, 'Dose rate').locator('.card-value')).not.toHaveClass(/orange/)
  await expect(card(page, 'Readings stored').locator('.card-value')).toHaveText('1')

  await page.goto(`/?device=${hot.id}`)
  await expect(card(page, 'Dose rate').locator('.card-value')).toHaveClass(/orange/)
  await expect(card(page, 'Readings stored').locator('.card-value')).toHaveText('100+')
  await expect(rows(page)).toHaveCount(100)
})

test('online means status online and seen within the offline timeout', async ({ open }) => {
  const user = createUser()
  const devs = {
    online: createDevice(user, { status: 'online', lastSeenMinutesAgo: 2 }),
    stale: createDevice(user, { status: 'online', lastSeenMinutesAgo: 30 }),
    offline: createDevice(user, { status: 'offline', lastSeenMinutesAgo: 1 }),
  }
  for (const d of Object.values(devs)) addReadings(d.id, [{ minutesAgo: 2, cpm: 10 }])
  const page = await open(await signIn(user.username))
  for (const [state, dev] of Object.entries(devs)) {
    await page.goto(`/?device=${dev.id}`)
    await expect(card(page, 'Device').locator('.card-unit')).toHaveText(state === 'online' ? 'online' : 'offline')
    await expect(card(page, 'Device').locator('.card-value')).toHaveClass(state === 'online' ? /green/ : /orange/)
  }
})

test('timestamps are shown in the display timezone', async ({ open }) => {
  const user = createUser()
  const dev = createDevice(user)
  const at = new Date(Math.floor(Date.now() / 1000) * 1000 - 10 * 60_000)
  const utc = at.toISOString().slice(0, 19).replace('T', ' ')
  sql(`INSERT INTO measurements (device_id, measured_at, cpm, usvh) VALUES (${q(dev.id)}, ${q(utc)}, 17, 0.0969);`)
  const vienna = at.toLocaleString('sv-SE', { timeZone: 'Europe/Vienna' })
  const page = await open(await signIn(user.username))
  await page.goto(`/?device=${dev.id}`)
  await expect(page.locator('header p strong')).toHaveText(vienna)
  await expect(rows(page).getByRole('cell').first()).toHaveText(vienna)
})

test('a counter without readings: empty cards, chart and table', async ({ open }) => {
  const user = createUser()
  const dev = createDevice(user, { name: `Silent ${uid()}` })
  createDevice(user)
  const page = await open(await signIn(user.username))
  await page.goto(`/?device=${dev.id}`)
  await expect(page.locator('header p')).toContainText('Waiting for first measurement')
  await expect(card(page, 'CPM').locator('.card-value')).toHaveText('—')
  await expect(card(page, 'Dose rate').locator('.card-value')).toHaveText('—')
  await expect(card(page, 'Readings stored')).toHaveCount(0)
  await expect(page.getByText('No data available for the last 24 hours.')).toBeVisible()
  await expect(page.getByText('No measurements in the last 24 hours.')).toBeVisible()
  await expect(page.locator('canvas#chart')).toHaveCount(0)
})
