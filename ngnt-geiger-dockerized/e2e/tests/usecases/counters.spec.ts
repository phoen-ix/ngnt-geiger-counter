import type { Page } from '@playwright/test'

import { aCounter, expect, flash, given, nav, test, uid } from './lib'

/* A Geiger counter, from its own screen to its owner's pages and to a guest's
 * dashboard (COVERAGE.c.md, U3-U6). The owner types what the counter shows -
 * its MAC, the pepper it made up - into the web UI; the counter logs in with
 * what it derives from them. The devices page has no live update, so its
 * owner reloads it until the subscriber has stored what the counter sent. */

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

test('U6 the alert threshold an owner sets on Devices turns a guest\'s dose-rate card orange; cleared, the default applies', async ({ browserOf }) => {
  const owner = given.user({ public: true })
  const counter = given.counter(owner, { cpm: 20 }) // 0.1140 µSv/h, below the default 0.5
  given.publicCounter()
  const page = await browserOf(owner)
  const guest = await browserOf()
  const dose = card(guest, 'Dose rate').locator('.card-value')

  await pick(guest, counter)
  await expect(dose).toHaveText('0.1140')
  await expect(dose).not.toHaveClass(/orange/)

  await nav(page).getByRole('link', { name: 'Devices' }).click()
  const alert = rowOf(page, counter.id).getByLabel('Alert threshold')
  await alert.fill('0.1')
  await alert.press('Enter')
  await expect(flash(page)).toHaveText('Device updated.')
  await guest.reload()
  await expect(dose).toHaveText('0.1140')
  await expect(dose).toHaveClass(/orange/)

  await alert.fill('')
  await rowOf(page, counter.id).getByRole('button', { name: 'Save' }).click()
  await expect(flash(page)).toHaveText('Device updated.')
  await expect(alert).toHaveAttribute('placeholder', '0.5')
  await guest.reload()
  await expect(dose).toHaveText('0.1140')
  await expect(dose).not.toHaveClass(/orange/)
})
