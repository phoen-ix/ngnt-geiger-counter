import { THE_ADMIN, SITE_URL, adminPasswordFile, expect, flash, given, inboxOf, linkIn, logIn, nav, test, tryToLogIn, uid } from './lib'

/* A fresh stack, from the server's hand to the admin's and on to the first
 * person who forgets a password (COVERAGE.c.md, U1-U2). Its own project, before
 * setup.ts: every other spec signs in as THE_ADMIN with the password U1 sets,
 * so this needs the fresh stack scripts/e2e.sh starts. */

test.describe.configure({ mode: 'serial' })

const FROM = 'geiger@geiger-e2e.example'

test('U1 the first admin signs in with the password from data/ and sets their own; the file goes, the old one is refused', async ({ browserOf }) => {
  const file = adminPasswordFile()
  expect(file, 'data/admin_initial_password.txt').not.toBeNull()
  const first = { username: /^Username: (.+)$/m.exec(file!)![1], password: /^Password: (.+)$/m.exec(file!)![1] }

  const admin = await browserOf(first)
  await expect(nav(admin).getByRole('link', { name: 'Admin' })).toBeVisible()
  await nav(admin).getByRole('link', { name: 'Account' }).click()
  await admin.getByLabel('Current password').fill(first.password)
  await admin.getByLabel('New password', { exact: true }).fill(THE_ADMIN.password)
  await admin.getByLabel('Confirm new password').fill(THE_ADMIN.password)
  await admin.getByRole('button', { name: 'Change Password' }).click()
  await expect(flash(admin)).toHaveText('Password changed.')
  expect(adminPasswordFile(), 'the file with the first password').toBeNull()

  await nav(admin).getByRole('link', { name: 'Logout' }).click()
  await expect(flash(admin)).toHaveText('Logged out.')
  await tryToLogIn(admin, first)
  await expect(flash(admin)).toHaveText('Invalid username or password.')
  await logIn(admin, THE_ADMIN)
})

test('U2 the mail server and Base URL the admin types in carry a reset mail; the person sets a password with its link', async ({ browserOf }) => {
  const person = given.user()
  const page = await browserOf()
  const askForReset = async () => {
    await page.goto('/login')
    await page.getByRole('link', { name: 'Forgot password?' }).click()
    await page.getByLabel('Username').fill(person.username)
    await page.getByRole('button', { name: 'Send Reset Link' }).click()
    await expect(flash(page)).toHaveText('If that account exists and has an email, a reset link has been sent.')
  }
  // Before the admin set the mail up, nothing goes out (counted below).
  await askForReset()

  const admin = await browserOf(THE_ADMIN)
  await nav(admin).getByRole('link', { name: 'Admin' }).click()
  await admin.getByLabel('Base URL').fill(SITE_URL)
  await admin.getByRole('button', { name: 'Save Settings' }).click()
  await expect(flash(admin)).toHaveText('Settings saved.')
  await admin.getByLabel('SMTP Host').fill('mailpit')
  await admin.getByLabel('SMTP Port').fill('1025')
  await admin.getByLabel('From Address').fill(FROM)
  await admin.getByLabel('Use STARTTLS').selectOption({ label: 'No' })
  await admin.getByRole('button', { name: 'Save SMTP' }).click()
  await expect(flash(admin)).toHaveText('SMTP settings saved.')

  await askForReset()
  const mails = await inboxOf(person.email)
  expect(mails).toHaveLength(1)
  expect(mails[0].from).toBe(FROM)
  const link = linkIn(mails[0].text)
  expect(link.startsWith(`${SITE_URL}/reset-password/`), link).toBe(true)

  const chosen = { username: person.username, password: `chosen-${uid()}` }
  await page.goto(link)
  await page.getByLabel('New password', { exact: true }).fill(chosen.password)
  await page.getByLabel('Confirm new password').fill(chosen.password)
  await page.getByRole('button', { name: 'Reset Password' }).click()
  await expect(flash(page)).toHaveText('Password reset. Please log in.')
  await logIn(page, chosen)

  // The link once more: spent.
  await page.goto(link)
  await expect(flash(page)).toHaveText('Invalid or expired reset link.')
})
