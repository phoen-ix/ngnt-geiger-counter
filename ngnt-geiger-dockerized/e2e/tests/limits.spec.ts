import { request } from '@playwright/test'

import { BASE, PASSWORD, createUser, expect, flash, submitForm, test, uid } from './support'

/* The rate limits on the sign-in forms, per gunicorn worker (so up to twice the
 * limit gets through): failed sign-ins per address and account, reset requests
 * per address and account, registrations per address. 4 x the limit within a
 * few seconds must hit a 429. Runs last: afterwards the suite's address cannot
 * register for the rest of the minute. */

/** Posts `form` to `path` 4 x `limit` times in a row from one address; the answers. */
async function burst(path: string, limit: number, form: Record<string, string>) {
  const api = await request.newContext({ baseURL: BASE })
  const token = /name="csrf_token" value="([^"]+)"/.exec(await (await api.get(path)).text())![1]
  const answers: { status: number; text: string }[] = []
  for (let i = 0; i < 4 * limit; i++) {
    const res = await api.post(path, { form: { csrf_token: token, ...form }, maxRedirects: 0 })
    answers.push({ status: res.status(), text: await res.text() })
  }
  await api.dispose()
  return answers
}

for (const [path, limit, form] of [
  ['/login', 10, { username: `nobody_${uid()}`, password: 'wrong-password' }],
  ['/register', 5, { username: 'ab', password: 'x', password2: 'x' }],
  ['/forgot-password', 5, { username: `nobody_${uid()}` }],
] as const) {
  test(`POST ${path} answers 429 after ${limit} a minute`, async () => {
    const refused = (await burst(path, limit, form)).filter((a) => a.status === 429)
    expect(refused.length).toBeGreaterThan(0)
    expect(refused[0].text).toContain('Too Many Requests')
  })
}

test('an account at its sign-in or reset limit locks out nobody else at the same address', async ({ page }) => {
  await burst('/login', 10, { username: `nobody_${uid()}`, password: 'wrong-password' })
  await burst('/forgot-password', 5, { username: `nobody_${uid()}` })
  const user = createUser()
  await submitForm(page, '/login', { Username: user.username, Password: PASSWORD }, 'Login')
  await expect(flash(page)).toHaveText('Logged in.')
  await submitForm(page, '/forgot-password', { Username: user.username }, 'Send Reset Link')
  await expect(flash(page)).toHaveText('If that account exists and has an email, a reset link has been sent.')
})

test('a form posted without its CSRF token is refused', async () => {
  const api = await request.newContext({ baseURL: BASE })
  const res = await api.post('/forgot-password', { form: { username: 'nobody' }, maxRedirects: 0 })
  expect(res.status()).toBe(400)
  expect(await res.text()).toContain('The CSRF token is missing.')
  await api.dispose()
})
