import { randomBytes } from 'node:crypto'

import { ADMIN_STATE, USER_STATE, createUser, expect, q, sql, test } from './support'

/* Breadth: every route as every role lands on the right page with its heading,
 * without a page error or a 5xx (the support fixture fails the test on those),
 * and the nav shows what that role may open. */

const token = randomBytes(32).toString('hex')
test.beforeAll(() => {
  const user = createUser()
  sql(`INSERT INTO password_resets (user_id, token, expires_at) VALUES (${user.id}, ${q(token)}, UTC_TIMESTAMP() + INTERVAL 1 HOUR);`)
})

type Row = { path: string; lands: string | RegExp; heading: string }
const signedOut = (path: string): Row => ({ path, lands: '/login', heading: 'Login' })

const ROUTES: Record<'guest' | 'user' | 'admin', Row[]> = {
  guest: [
    { path: '/', lands: '/', heading: 'Last 24 hours' },
    { path: '/login', lands: '/login', heading: 'Login' },
    { path: '/register', lands: '/register', heading: 'Create Account' },
    { path: '/forgot-password', lands: '/forgot-password', heading: 'Forgot Password' },
    { path: `/reset-password/${token}`, lands: `/reset-password/${token}`, heading: 'Set New Password' },
    signedOut('/devices'),
    signedOut('/account'),
    signedOut('/admin'),
    { path: '/logout', lands: '/', heading: 'Last 24 hours' },
  ],
  user: [
    { path: '/', lands: '/', heading: 'Last 24 hours' },
    { path: '/login', lands: '/login', heading: 'Login' },
    { path: '/register', lands: '/register', heading: 'Create Account' },
    { path: '/forgot-password', lands: '/forgot-password', heading: 'Forgot Password' },
    { path: `/reset-password/${token}`, lands: `/reset-password/${token}`, heading: 'Set New Password' },
    { path: '/devices', lands: '/devices', heading: 'Your Devices' },
    { path: '/account', lands: '/account', heading: 'Profile' },
    { path: '/admin', lands: '/', heading: 'Last 24 hours' },
    { path: '/logout', lands: '/', heading: 'Last 24 hours' },
  ],
  admin: [
    { path: '/', lands: '/', heading: 'Last 24 hours' },
    { path: '/login', lands: '/login', heading: 'Login' },
    { path: '/register', lands: '/register', heading: 'Create Account' },
    { path: '/forgot-password', lands: '/forgot-password', heading: 'Forgot Password' },
    { path: `/reset-password/${token}`, lands: `/reset-password/${token}`, heading: 'Set New Password' },
    { path: '/devices', lands: '/devices', heading: 'Your Devices' },
    { path: '/account', lands: '/account', heading: 'Profile' },
    { path: '/admin', lands: '/admin', heading: 'Global Settings' },
    { path: '/logout', lands: '/', heading: 'Last 24 hours' },
  ],
}

const NAV = {
  guest: ['Dashboard', 'Login', 'Register'],
  user: ['Dashboard', 'Devices', 'Account', 'Logout'],
  admin: ['Dashboard', 'Devices', 'Account', 'Admin', 'Logout'],
}
const STATE = { guest: undefined, user: USER_STATE, admin: ADMIN_STATE }

for (const [role, rows] of Object.entries(ROUTES) as [keyof typeof ROUTES, Row[]][]) {
  test.describe(`as ${role}`, () => {
    for (const row of rows) {
      test(`GET ${row.path.replace(token, '<token>')}`, async ({ open }) => {
        const page = await open(STATE[role])
        const res = await page.goto(row.path)
        expect(res?.status()).toBe(200)
        expect(new URL(page.url()).pathname).toBe(row.lands)
        await expect(page.getByRole('heading', { name: row.heading, exact: true })).toBeVisible()
        await expect(page.getByRole('heading', { level: 1 })).toContainText('NGNT Geiger Counter')
      })
    }

    test('nav links', async ({ open }) => {
      const page = await open(STATE[role])
      await page.goto('/')
      await expect(page.getByRole('navigation').getByRole('link')).toHaveText(NAV[role])
    })
  })
}

test('an unknown path is a 404', async ({ page }) => {
  const res = await page.goto('/no-such-page')
  expect(res?.status()).toBe(404)
  await expect(page.getByRole('heading', { name: 'Not Found' })).toBeVisible()
})

test('the stylesheet is served', async ({ request }) => {
  const res = await request.get('/static/style.css')
  expect(res.status()).toBe(200)
  expect(res.headers()['content-type']).toContain('text/css')
})
