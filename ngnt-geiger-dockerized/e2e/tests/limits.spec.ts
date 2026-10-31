import { request } from '@playwright/test'

import { BASE, expect, test, uid } from './support'

/* The rate limits on the sign-in forms (per IP and per gunicorn worker, so
 * up to twice the limit gets through). Runs last: afterwards every form of
 * the suite's IP is limited for the rest of the minute. 4 x the limit within
 * a few seconds must hit a 429. */

for (const [path, limit, form] of [
  ['/login', 10, { username: `nobody_${uid()}`, password: 'wrong-password' }],
  ['/register', 5, { username: 'ab', password: 'x', password2: 'x' }],
  ['/forgot-password', 5, { username: `nobody_${uid()}` }],
] as const) {
  test(`POST ${path} answers 429 after ${limit} a minute`, async () => {
    const api = await request.newContext({ baseURL: BASE })
    const token = /name="csrf_token" value="([^"]+)"/.exec(await (await api.get(path)).text())![1]
    const statuses: number[] = []
    for (let i = 0; i < 4 * limit && !statuses.includes(429); i++) {
      statuses.push((await api.post(path, { form: { csrf_token: token, ...form }, maxRedirects: 0 })).status())
    }
    // Earlier tests may have used part of this minute's allowance.
    expect(statuses).toContain(429)
    const res = await api.post(path, { form: { csrf_token: token, ...form }, maxRedirects: 0 })
    if (res.status() === 429) expect(await res.text()).toContain('Too Many Requests')
    await api.dispose()
  })
}

test('a form posted without its CSRF token is refused', async () => {
  const api = await request.newContext({ baseURL: BASE })
  const res = await api.post('/forgot-password', { form: { username: 'nobody' }, maxRedirects: 0 })
  expect(res.status()).toBe(400)
  expect(await res.text()).toContain('The CSRF token is missing.')
  await api.dispose()
})
