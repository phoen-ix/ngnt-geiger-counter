import { randomBytes } from 'node:crypto'

import { request } from '@playwright/test'

import { BASE, PASSWORD, createUser, expect, flash, mailTo, postForm, q, sql, submitForm, test, uid } from './support'

/* Register, sign in and out, forgot and reset password (mail through Mailpit). */

const nav = (page: import('@playwright/test').Page) => page.getByRole('navigation')

test.describe('register', () => {
  test('a new account can sign in', async ({ page }) => {
    const username = `reg_${uid()}`
    await submitForm(page, '/register', {
      Username: username,
      'Email (optional)': `${username}@geiger-e2e.example`,
      Password: PASSWORD,
      'Confirm password': PASSWORD,
    }, 'Register')
    await expect(page).toHaveURL(/\/login$/)
    await expect(flash(page)).toHaveText('Account created. Please log in.')

    await submitForm(page, '/login', { Username: username, Password: PASSWORD }, 'Login')
    await expect(page).toHaveURL(/\/$/)
    await expect(flash(page)).toHaveText('Logged in.')
    await expect(nav(page).getByRole('link', { name: 'Logout' })).toBeVisible()
    await expect(nav(page).getByRole('link', { name: 'Admin' })).toHaveCount(0)
    expect(sql(`SELECT role, email FROM users WHERE username = ${q(username)};`)).toBe(
      `user\t${username}@geiger-e2e.example`,
    )
  })

  test('invalid input is refused with its reason and the form keeps the username', async ({ page }) => {
    const taken = createUser().username
    const cases: [Record<string, string>, string][] = [
      [{ Username: 'ab' }, 'Username must be 3-50 characters.'],
      [{ Username: 'no spaces!' }, 'Username may only contain letters, digits, underscores, and hyphens.'],
      [{ Password: '12345', 'Confirm password': '12345' }, 'Password must be at least 6 characters.'],
      [{ 'Confirm password': 'something-else' }, 'Passwords do not match.'],
      [{ Username: taken }, 'Username already taken.'],
    ]
    for (const [override, message] of cases) {
      const fields = { Username: `reg_${uid()}`, Password: PASSWORD, 'Confirm password': PASSWORD, ...override }
      await submitForm(page, '/register', fields, 'Register')
      await expect(page).toHaveURL(/\/register$/)
      await expect(flash(page)).toHaveText(message)
      await expect(page.getByLabel('Username')).toHaveValue(fields.Username)
    }
  })

  test('an email over 255 characters is refused, not a server error', async ({ page }) => {
    await page.goto('/register')
    await expect(page.getByLabel('Email (optional)')).toHaveAttribute('maxlength', '255')
    const username = `reg_${uid()}`
    const api = await request.newContext({ baseURL: BASE })
    let res: import('@playwright/test').APIResponse | undefined
    const form = { username, email: `${'e'.repeat(250)}@geiger-e2e.example`, password: PASSWORD, password2: PASSWORD }
    await expect
      .poll(async () => (res = await postForm(api, '/register', form)).status(), { intervals: [5_000], timeout: 75_000 })
      .not.toBe(429)
    expect(res!.status()).toBe(200)
    expect(await res!.text()).toContain('Email can be at most 255 characters.')
    await api.dispose()
    expect(sql(`SELECT COUNT(*) FROM users WHERE username = ${q(username)};`)).toBe('0')
  })

  test('empty required fields are stopped in the browser', async ({ page }) => {
    await page.goto('/register')
    await page.getByRole('button', { name: 'Register', exact: true }).click()
    await expect(page).toHaveURL(/\/register$/)
    expect(await page.getByLabel('Username').evaluate((el: HTMLInputElement) => el.validity.valueMissing)).toBe(true)
    await expect(flash(page)).toHaveCount(0)
  })
})

test.describe('sign in and out', () => {
  test('a wrong password or an unknown user gets the same message', async ({ page }) => {
    const user = createUser()
    for (const [username, password] of [[user.username, 'not-the-password'], [`nobody_${uid()}`, PASSWORD]]) {
      await submitForm(page, '/login', { Username: username, Password: password }, 'Login')
      await expect(page).toHaveURL(/\/login$/)
      await expect(flash(page)).toHaveText('Invalid username or password.')
    }
  })

  test('sign in, then sign out', async ({ page }) => {
    const user = createUser()
    await page.goto('/')
    await nav(page).getByRole('link', { name: 'Login' }).click()
    await submitForm(page, '/login', { Username: user.username, Password: PASSWORD }, 'Login')
    await expect(page.getByRole('navigation').getByRole('link', { name: 'Devices' })).toBeVisible()
    await nav(page).getByRole('link', { name: 'Logout' }).click()
    await expect(flash(page)).toHaveText('Logged out.')
    await expect(nav(page).getByRole('link', { name: 'Login' })).toBeVisible()
    await page.goto('/devices')
    await expect(page).toHaveURL(/\/login$/)
  })

  test('the login page links to forgot-password and register', async ({ page }) => {
    await page.goto('/login')
    await page.getByRole('link', { name: 'Forgot password?' }).click()
    await expect(page.getByRole('heading', { name: 'Forgot Password' })).toBeVisible()
    await page.getByRole('link', { name: 'Back to login' }).click()
    await page.locator('.auth-form').getByRole('link', { name: 'Register' }).click()
    await expect(page.getByRole('heading', { name: 'Create Account' })).toBeVisible()
    await page.locator('.auth-form').getByRole('link', { name: 'Login' }).click()
    await expect(page.getByRole('heading', { name: 'Login' })).toBeVisible()
  })
})

test.describe('password reset', () => {
  test('mail link sets a new password once; the old one stops working', async ({ page }) => {
    const user = createUser()
    const newPassword = `new-${uid()}`
    await submitForm(page, '/forgot-password', { Username: user.username }, 'Send Reset Link')
    await expect(page).toHaveURL(/\/login$/)
    await expect(flash(page)).toHaveText('If that account exists and has an email, a reset link has been sent.')

    const text = await mailTo(user.email)
    expect(text).toContain('You requested a password reset for your NGNT Geiger Counter account.')
    const link = /(http\S+\/reset-password\/[0-9a-f]{64})/.exec(text)?.[1]
    expect(link, text).toBeTruthy()

    await page.goto(link!)
    await expect(page.getByRole('heading', { name: 'Set New Password' })).toBeVisible()
    await page.getByLabel('New password', { exact: true }).fill(newPassword)
    await page.getByLabel('Confirm new password').fill(newPassword)
    await page.getByRole('button', { name: 'Reset Password' }).click()
    await expect(flash(page)).toHaveText('Password reset. Please log in.')

    await submitForm(page, '/login', { Username: user.username, Password: PASSWORD }, 'Login')
    await expect(flash(page)).toHaveText('Invalid username or password.')
    await submitForm(page, '/login', { Username: user.username, Password: newPassword }, 'Login')
    await expect(flash(page)).toHaveText('Logged in.')

    await page.goto(link!)
    await expect(page).toHaveURL(/\/login$/)
    await expect(flash(page)).toHaveText('Invalid or expired reset link.')
  })

  test('no mail for an unknown user or one without an email, same message', async ({ page }) => {
    const noEmail = createUser()
    sql(`UPDATE users SET email = NULL WHERE id = ${noEmail.id};`)
    for (const username of [`nobody_${uid()}`, noEmail.username]) {
      await submitForm(page, '/forgot-password', { Username: username }, 'Send Reset Link')
      await expect(flash(page)).toHaveText('If that account exists and has an email, a reset link has been sent.')
    }
    expect(sql(`SELECT COUNT(*) FROM password_resets WHERE user_id = ${noEmail.id};`)).toBe('0')
  })

  test('an unknown or expired link goes back to the login page', async ({ page }) => {
    const user = createUser()
    const expired = randomBytes(32).toString('hex')
    sql(`INSERT INTO password_resets (user_id, token, expires_at) VALUES (${user.id}, ${q(expired)}, UTC_TIMESTAMP() - INTERVAL 1 MINUTE);`)
    for (const token of [randomBytes(32).toString('hex'), expired]) {
      await page.goto(`/reset-password/${token}`)
      await expect(page).toHaveURL(/\/login$/)
      await expect(flash(page)).toHaveText('Invalid or expired reset link.')
    }
  })

  test('a short or mismatched new password is refused', async ({ page }) => {
    const user = createUser()
    const token = randomBytes(32).toString('hex')
    sql(`INSERT INTO password_resets (user_id, token, expires_at) VALUES (${user.id}, ${q(token)}, UTC_TIMESTAMP() + INTERVAL 1 HOUR);`)
    for (const [pw, pw2, message] of [
      ['12345', '12345', 'Password must be at least 6 characters.'],
      ['long-enough', 'different-one', 'Passwords do not match.'],
    ]) {
      await page.goto(`/reset-password/${token}`)
      await page.getByLabel('New password', { exact: true }).fill(pw)
      await page.getByLabel('Confirm new password').fill(pw2)
      await page.getByRole('button', { name: 'Reset Password' }).click()
      await expect(flash(page)).toHaveText(message)
      await expect(page.getByRole('heading', { name: 'Set New Password' })).toBeVisible()
    }
    expect(sql(`SELECT used FROM password_resets WHERE token = ${q(token)};`)).toBe('0')
  })
})
