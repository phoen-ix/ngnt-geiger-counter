import type { Page } from '@playwright/test'

import { THE_ADMIN, aCounter, expect, flash, given, logIn, nav, test, tryToLogIn, uid } from './lib'

/* Accounts, from the admin's or the owner's hand to the person it changes
 * (COVERAGE.c.md, U7-U11): each person in their own browser, logged in
 * through the form, reading the outcome on their own screen. */

const rowOf = (page: Page, username: string) =>
  page.getByRole('row').filter({ has: page.getByRole('cell', { name: username, exact: true }) })

async function adminPage(browserOf: (who: typeof THE_ADMIN) => Promise<Page>) {
  const admin = await browserOf(THE_ADMIN)
  await nav(admin).getByRole('link', { name: 'Admin' }).click()
  await expect(admin.getByRole('heading', { name: 'Users' })).toBeVisible()
  return admin
}

/** The guest's dashboard: the device filter lists every counter they may see. */
const guestSees = (guest: Page, name: string) => guest.getByLabel('Device:').getByRole('option', { name })

test('U7 a user the admin deletes is signed out, cannot sign in, and their counter is locked out', async ({ browserOf }) => {
  const person = given.user()
  const counter = aCounter(person.pepper!)
  const page = await browserOf(person)
  await nav(page).getByRole('link', { name: 'Devices' }).click()
  await page.getByLabel('MAC Address').fill(counter.mac)
  await page.getByRole('button', { name: 'Register Device' }).click()
  await expect(flash(page)).toHaveText(`Device ${counter.id} registered and provisioned.`)
  await counter.connects()

  const admin = await adminPage(browserOf)
  let asked = ''
  admin.once('dialog', (d) => ((asked = d.message()), d.accept()))
  await rowOf(admin, person.username).getByRole('button', { name: 'Delete' }).click()
  await expect(flash(admin)).toHaveText('User deleted.')
  expect(asked).toBe(`Delete user ${person.username}? All their devices will be unprovisioned.`)
  await expect(rowOf(admin, person.username)).toHaveCount(0)

  await nav(page).getByRole('link', { name: 'Account' }).click()
  await expect(page).toHaveURL(/\/login$/)
  await tryToLogIn(page, person)
  await expect(flash(page)).toHaveText('Invalid username or password.')
  await counter.isLockedOut()
})

test('U8 a role the admin gives is the admin page the person gets, and taken back, the page is gone', async ({ browserOf }) => {
  const person = given.user()
  const page = await browserOf(person)
  await expect(nav(page).getByRole('link', { name: 'Admin' })).toHaveCount(0)

  const admin = await adminPage(browserOf)
  await rowOf(admin, person.username).getByRole('button', { name: 'user' }).click()
  await expect(flash(admin)).toHaveText('Role updated.')
  await expect(rowOf(admin, person.username).getByRole('button', { name: 'admin' })).toBeVisible()

  await nav(page).getByRole('link', { name: 'Devices' }).click()
  await nav(page).getByRole('link', { name: 'Admin' }).click()
  await expect(page.getByRole('heading', { name: 'Global Settings' })).toBeVisible()
  await expect(rowOf(page, person.username).getByRole('button', { name: 'admin' })).toBeDisabled()

  await rowOf(admin, person.username).getByRole('button', { name: 'admin' }).click()
  await expect(flash(admin)).toHaveText('Role updated.')
  // The link on the page they still have open leads back to the dashboard.
  await nav(page).getByRole('link', { name: 'Admin' }).click()
  await expect(page).toHaveURL(/\/$/)
  await expect(nav(page).getByRole('link', { name: 'Devices' })).toBeVisible()
  await expect(nav(page).getByRole('link', { name: 'Admin' })).toHaveCount(0)
})

test("U9 a person the admin makes public: a guest sees their counter and the person's account says so; private again, both go", async ({ browserOf }) => {
  const person = given.user()
  const counter = given.counter(person)
  given.publicCounter()
  given.publicCounter() // the device filter shows with two counters, the person's or not
  const guest = await browserOf()
  await guest.goto('/')
  await expect(guest.getByLabel('Device:')).toBeVisible()
  await expect(guestSees(guest, counter.name)).toHaveCount(0)
  const page = await browserOf(person)
  await nav(page).getByRole('link', { name: 'Account' }).click()
  await expect(page.getByLabel('Public profile')).toHaveValue('0')

  const admin = await adminPage(browserOf)
  await rowOf(admin, person.username).getByRole('button', { name: 'No' }).click()
  await expect(flash(admin)).toHaveText('Visibility updated.')
  await guest.reload()
  await expect(guestSees(guest, counter.name)).toHaveCount(1)
  await page.reload()
  await expect(page.getByLabel('Public profile')).toHaveValue('1')

  await rowOf(admin, person.username).getByRole('button', { name: 'Yes' }).click()
  await expect(flash(admin)).toHaveText('Visibility updated.')
  await guest.reload()
  await expect(guest.getByLabel('Device:')).toBeVisible()
  await expect(guestSees(guest, counter.name)).toHaveCount(0)
  await page.reload()
  await expect(page.getByLabel('Public profile')).toHaveValue('0')
})

test('U10 a person who makes their profile public is seen by a guest, and private again, is not', async ({ browserOf }) => {
  const person = given.user()
  const counter = given.counter(person)
  given.publicCounter()
  given.publicCounter()
  const page = await browserOf(person)
  const guest = await browserOf()
  const setPublic = async (option: string) => {
    await nav(page).getByRole('link', { name: 'Account' }).click()
    await page.getByLabel('Public profile').selectOption({ label: option })
    await page.getByRole('button', { name: 'Save Profile' }).click()
    await expect(flash(page)).toHaveText('Profile updated.')
  }

  await setPublic('Public — anyone can see your devices')
  await guest.goto('/')
  await expect(guestSees(guest, counter.name)).toHaveCount(1)

  await setPublic('Private — only you see your devices')
  await guest.reload()
  await expect(guest.getByLabel('Device:')).toBeVisible()
  await expect(guestSees(guest, counter.name)).toHaveCount(0)
})

test('U11 a password changed on one device signs the other out; there only the new one signs in', async ({ browserOf }) => {
  const person = given.user()
  const laptop = await browserOf(person)
  const phone = await browserOf(person)
  const changed = { ...person, password: `changed-${uid()}` }

  await nav(laptop).getByRole('link', { name: 'Account' }).click()
  await laptop.getByLabel('Current password').fill(person.password)
  await laptop.getByLabel('New password', { exact: true }).fill(changed.password)
  await laptop.getByLabel('Confirm new password').fill(changed.password)
  await laptop.getByRole('button', { name: 'Change Password' }).click()
  await expect(flash(laptop)).toHaveText('Password changed.')

  await nav(phone).getByRole('link', { name: 'Devices' }).click()
  await expect(phone).toHaveURL(/\/login$/)
  await tryToLogIn(phone, person)
  await expect(flash(phone)).toHaveText('Invalid username or password.')
  await logIn(phone, changed)

  await nav(laptop).getByRole('link', { name: 'Devices' }).click()
  await expect(laptop.getByRole('heading', { name: 'Your Devices' })).toBeVisible()
})
