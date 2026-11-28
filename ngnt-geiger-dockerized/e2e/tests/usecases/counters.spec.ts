import type { Page } from '@playwright/test'

import { aCounter, expect, flash, given, inboxOf, nav, test, uid } from './lib'

/* A Geiger counter, from its own screen to its owner's pages and to a guest's
 * dashboard (COVERAGE.c.md, U3-U6). The owner types what the counter shows -
 * its MAC, the pepper it made up - into the web UI; the counter logs in with
 * what it derives from them. The devices page has no live update, so its
 * owner reloads it until the subscriber has stored what the counter sent.
 * The owner's mail is the one the subscriber sent through the admin's SMTP
 * settings (Mailpit, set up in first-run.spec.ts). */

const rowOf = (page: Page, id: string) => page.getByRole('row').filter({ hasText: id })
const card = (page: Page, label: string) => page.locator('.card').filter({ hasText: label })

async function register(owner: Page, counter: { mac: string; id: string }, name: string) {
  await nav(owner).getByRole('link', { name: 'Devices' }).click()
  await owner.getByLabel('MAC Address').fill(counter.mac)
  await owner.getByLabel('Display Name (optional)').fill(name)
  await owner.getByRole('button', { name: 'Register Device' }).click()
  await expect(flash(owner)).toHaveText(`Device ${counter.id} registered and provisioned.`)
}

/** Picks the counter in the dashboard's device filter, which loads its page. */
async function pick(page: Page, counter: { id: string; name: string }) {
  await page.goto('/')
  await page.getByLabel('Device:').selectOption({ label: counter.name })
  await expect(page).toHaveURL(new RegExp(`device=${counter.id}`))
}

test('U3 a counter set up with the pepper on its screen logs in, and its reading is on its owner\'s pages', async ({ browserOf }) => {
  const owner = given.user({ pepper: null })
  given.publicCounter() // the dashboard's device filter needs a second counter
  const counter = aCounter()
  const name = `Porch ${uid()}`
  const page = await browserOf(owner)

  await nav(page).getByRole('link', { name: 'Devices' }).click()
  await page.getByRole('link', { name: 'account settings' }).click()
  await page.getByLabel('MQTT Pepper').fill(counter.pepper)
  await page.getByRole('button', { name: 'Save Profile' }).click()
  await expect(flash(page)).toHaveText('Profile updated.')
  await register(page, counter, name)

  await counter.connects(31)
  const row = rowOf(page, counter.id)
  await expect.poll(async () => (await page.reload(), (await row.getByRole('cell').nth(4).textContent())?.trim())).toBe('online')
  await expect(row.getByRole('cell').nth(5)).not.toHaveText('—')

  await pick(page, { id: counter.id, name })
  await expect.poll(async () => (await page.reload(), (await card(page, 'CPM').locator('.card-value').textContent())?.trim())).toBe('31')
  await expect(card(page, 'Dose rate').locator('.card-value')).toHaveText('0.1767')
  await expect(card(page, 'Device').locator('.card-unit')).toHaveText('online')
})

test('U4 a counter its owner deletes is locked out, and let in again once registered again', async ({ browserOf }) => {
  const owner = given.user()
  const counter = aCounter(owner.pepper!)
  const page = await browserOf(owner)
  await register(page, counter, `Shed ${uid()}`)
  await counter.connects()

  page.once('dialog', (d) => d.accept())
  await rowOf(page, counter.id).getByRole('button', { name: 'Delete' }).click()
  await expect(flash(page)).toHaveText('Device deleted and unprovisioned.')
  await expect(rowOf(page, counter.id)).toHaveCount(0)
  await counter.isLockedOut()

  await register(page, counter, `Shed again ${uid()}`)
  await counter.connects()
  await expect.poll(async () => (await page.reload(), (await rowOf(page, counter.id).getByRole('cell').nth(4).textContent())?.trim())).toBe('online')
})

test('U5 a counter whose power goes is offline on its owner\'s page (the broker\'s last will), and online once back', async ({ browserOf }) => {
  const owner = given.user()
  const counter = aCounter(owner.pepper!)
  const page = await browserOf(owner)
  await register(page, counter, `Attic ${uid()}`)
  const status = rowOf(page, counter.id).getByRole('cell').nth(4)

  const on = await counter.switchOn()
  await expect.poll(async () => (await page.reload(), (await status.textContent())?.trim())).toBe('online')
  await on.powerCut()
  await expect.poll(async () => (await page.reload(), (await status.textContent())?.trim())).toBe('offline')

  await counter.connects()
  await expect.poll(async () => (await page.reload(), (await status.textContent())?.trim())).toBe('online')
})

test('U6 the alert threshold an owner sets on Devices turns a guest\'s dose-rate card orange and mails the owner once per crossing; cleared, the default applies', async ({ browserOf }) => {
  const owner = given.user({ public: true })
  const counter = aCounter(owner.pepper!)
  given.publicCounter()
  const name = `Cellar ${uid()}`
  const page = await browserOf(owner)
  await register(page, counter, name)
  const alert = rowOf(page, counter.id).getByLabel('Alert threshold')
  await alert.fill('0.1')
  await alert.press('Enter')
  await expect(flash(page)).toHaveText('Device updated.')

  const guest = await browserOf()
  await pick(guest, { id: counter.id, name })
  const dose = card(guest, 'Dose rate').locator('.card-value')
  /** The counter sends a reading of `cpm`; the guest reloads until its dose rate is on the card. */
  const reads = async (cpm: number, usvh: string) => {
    await counter.connects(cpm)
    await expect.poll(async () => (await guest.reload(), (await dose.textContent())?.trim())).toBe(usvh)
  }

  await reads(25, '0.1425')
  await expect(dose).toHaveClass(/orange/)
  const crossed = await inboxOf(owner.email)
  expect(crossed).toHaveLength(1)
  expect(crossed[0].text).toContain(`${name} measured 0.1425 µSv/h`)
  expect(crossed[0].text).toContain('alert threshold of 0.1 µSv/h')

  await reads(30, '0.1710') // still above: no second mail
  await expect(dose).toHaveClass(/orange/)
  expect(await inboxOf(owner.email)).toHaveLength(1)

  await reads(10, '0.0570') // back below
  await expect(dose).not.toHaveClass(/orange/)
  await reads(20, '0.1140') // and above again
  await expect(dose).toHaveClass(/orange/)
  const again = await inboxOf(owner.email, 2)
  expect(again).toHaveLength(2)
  expect(again[0].text).toContain(`${name} measured 0.1140 µSv/h`)

  await alert.fill('')
  await rowOf(page, counter.id).getByRole('button', { name: 'Save' }).click()
  await expect(flash(page)).toHaveText('Device updated.')
  await expect(alert).toHaveAttribute('placeholder', '0.5')
  await guest.reload()
  await expect(dose).toHaveText('0.1140')
  await expect(dose).not.toHaveClass(/orange/)
})
