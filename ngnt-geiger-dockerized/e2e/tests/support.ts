import { execFileSync, spawn } from 'node:child_process'
import { once } from 'node:events'
import { createHmac, pbkdf2Sync, randomBytes } from 'node:crypto'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import {
  test as base,
  expect,
  request,
  type APIRequestContext,
  type APIResponse,
  type BrowserContext,
  type Page,
} from '@playwright/test'

/* Shared helpers of the e2e suite. Every test owns its data: users and devices
 * get a unique suffix (uid()). Users are inserted with SQL (the register form
 * allows 5 sign-ups a minute and has its own journey in auth.spec.ts) and sign
 * in through the real login form; measurements and device status are SQL
 * (the subscriber writes those) except in mqtt.spec.ts, which goes through
 * Mosquitto and the subscriber. */

export const BASE = process.env.E2E_BASE_URL ?? 'http://127.0.0.1:1880'
export const MAIL = process.env.E2E_MAIL_URL ?? 'http://127.0.0.1:1885'
const PROJECT = process.env.COMPOSE_PROJECT_NAME ?? 'ngnt-geiger-counter_e2e'
export const CONTAINER = {
  db: `${PROJECT}-ngnt-geiger-mariadb`,
  flask: `${PROJECT}-ngnt-geiger-flask`,
  mosquitto: `${PROJECT}-ngnt-geiger-mosquitto`,
}
export const AUTH = fileURLToPath(new URL('../.auth/', import.meta.url))
export const ADMIN_STATE = path.join(AUTH, 'admin.json')
export const USER_STATE = path.join(AUTH, 'user.json')

/** Every account's password (the bootstrap admin's after setup.ts). */
export const PASSWORD = 'geiger-e2e-Pw7x'
// werkzeug's pbkdf2 format, few rounds: check_password_hash reads them from the hash.
const SALT = 'e2esalt'
const HASH = `pbkdf2:sha256:1000$${SALT}$${pbkdf2Sync(PASSWORD, SALT, 1000, 32, 'sha256').toString('hex')}`

export const uid = () => `${Date.now().toString(36)}${randomBytes(3).toString('hex')}`

/** A MAC no other test uses; its last 6 hex digits make the device_id. */
export const randomMac = () =>
  ['5C', 'CF', '7F', ...[...randomBytes(3)].map((b) => b.toString(16).padStart(2, '0').toUpperCase())].join(':')

/** helpers.derive_mqtt_credentials() / the firmware, re-implemented. */
export function mqttCredentials(mac: string, pepper: string) {
  const hex = mac.replace(/[:-]/g, '').toLowerCase()
  return { id: `geiger_${hex.slice(-6)}`, password: createHmac('sha256', pepper).update(hex).digest('hex').slice(0, 16) }
}

export const q = (s: string | null | undefined) => (s == null ? 'NULL' : `'${s.replace(/\\/g, '\\\\').replace(/'/g, "''")}'`)

/** Runs SQL as root in the stack's database; returns the tab-separated rows. */
export function sql(statements: string): string {
  return execFileSync(
    'docker',
    ['exec', '-i', CONTAINER.db, 'sh', '-c', 'mariadb -N -B -uroot -p"$MARIADB_ROOT_PASSWORD" "$MARIADB_DATABASE"'],
    { input: statements },
  )
    .toString()
    .trim()
}

export type User = { id: number; username: string; email: string; pepper: string | null }

export function createUser(opts: { pepper?: string | null; public?: boolean; role?: 'admin' | 'user' } = {}): User {
  const username = `u_${uid()}`
  const email = `${username}@geiger-e2e.example`
  const pepper = opts.pepper === undefined ? randomBytes(4).toString('hex') : opts.pepper
  const id = Number(
    sql(
      `INSERT INTO users (username, email, password_hash, role, pepper, public) VALUES ` +
        `(${q(username)}, ${q(email)}, ${q(HASH)}, ${q(opts.role ?? 'user')}, ${q(pepper)}, ${opts.public ? 1 : 0});\n` +
        `SELECT LAST_INSERT_ID();`,
    ),
  )
  return { id, username, email, pepper }
}

/** A device row as Flask's add_device writes it, without provisioning Mosquitto. */
export function createDevice(
  user: User,
  opts: { name?: string | null; status?: 'online' | 'offline'; lastSeenMinutesAgo?: number; alert?: number } = {},
) {
  const mac = randomMac()
  const { id } = mqttCredentials(mac, user.pepper ?? 'none')
  const lastSeen = opts.lastSeenMinutesAgo == null ? 'NULL' : `UTC_TIMESTAMP() - INTERVAL ${opts.lastSeenMinutesAgo} MINUTE`
  sql(
    `INSERT INTO devices (user_id, device_id, mac_address, display_name, status, last_seen, alert_threshold, provisioned) ` +
      `VALUES (${user.id}, ${q(id)}, ${q(mac)}, ${q(opts.name === undefined ? `Counter ${id.slice(-6)}` : opts.name)}, ` +
      `${q(opts.status ?? 'offline')}, ${lastSeen}, ${opts.alert ?? 'NULL'}, TRUE);`,
  )
  return { id, mac, name: opts.name === undefined ? `Counter ${id.slice(-6)}` : opts.name }
}

/** One reading per entry, `minutesAgo` before now (UTC, as the firmware sends it). */
export function addReadings(deviceId: string, readings: { minutesAgo: number; cpm: number }[]) {
  sql(
    `INSERT INTO measurements (device_id, measured_at, cpm, usvh) VALUES ` +
      readings
        .map((r) => `(${q(deviceId)}, UTC_TIMESTAMP() - INTERVAL ${r.minutesAgo} MINUTE, ${r.cpm}, ${(r.cpm * 0.0057).toFixed(4)})`)
        .join(', ') +
      ';',
  )
}

const CSRF = /name="csrf_token" value="([^"]+)"/

/** GET `tokenFrom`, then POST `form` to `path` with that page's CSRF token (redirects followed). */
export async function postForm(
  api: APIRequestContext,
  path: string,
  form: Record<string, string>,
  tokenFrom = path,
): Promise<APIResponse> {
  const page = await api.get(tokenFrom)
  const token = CSRF.exec(await page.text())?.[1]
  if (!token) throw new Error(`no CSRF token on ${tokenFrom}`)
  return api.post(path, { form: { csrf_token: token, ...form } })
}

/** Signs in through the login form and returns the session. A sign-in that
 * works never counts against the login limit. */
export async function signIn(username: string, password = PASSWORD) {
  const api = await request.newContext({ baseURL: BASE })
  try {
    const res = await postForm(api, '/login', { username, password })
    expect(new URL(res.url()).pathname, `sign in as ${username}`).toBe('/')
    return await api.storageState()
  } finally {
    await api.dispose()
  }
}

export type State = Awaited<ReturnType<typeof signIn>>

/** Publishes like a Geiger counter: over Mosquitto's TLS listener, as `user`,
 * on its own topic unless `topic` names another. Several payloads go out back
 * to back on one connection, as the firmware sends "connected" and its pending
 * reading after a reconnect. */
export function mqttPublish(
  user: string,
  password: string,
  payload: object | string | (object | string)[],
  topic = `/${user}/impulses`,
): { ok: boolean; out: string } {
  const lines = (Array.isArray(payload) ? payload : [payload]).map((p) => (typeof p === 'string' ? p : JSON.stringify(p)))
  try {
    execFileSync(
      'docker',
      [
        'exec', '-i', CONTAINER.mosquitto, 'mosquitto_pub', '-h', 'localhost', '-p', '8883',
        '--cafile', '/mosquitto/certs/ca.crt', '--insecure', '-q', '1',
        '-u', user, '-P', password, '-t', topic, '-l',
      ],
      { input: lines.join('\n') + '\n', stdio: ['pipe', 'pipe', 'pipe'] },
    )
    return { ok: true, out: '' }
  } catch (e) {
    return { ok: false, out: String((e as { stderr?: Buffer }).stderr ?? e) }
  }
}

/** Subscribes to `topic` as `user` over the TLS listener, runs `meanwhile` once
 * the broker acknowledged the subscription, and returns mosquitto_sub's debug
 * log: a message delivered shows as "received PUBLISH". It listens 5 s in all,
 * as there is no event for a message that must not arrive. */
export async function mqttListen(user: string, password: string, topic: string, meanwhile: () => void): Promise<string> {
  const sub = spawn('docker', [
    'exec', CONTAINER.mosquitto, 'mosquitto_sub', '-h', 'localhost', '-p', '8883',
    '--cafile', '/mosquitto/certs/ca.crt', '--insecure', '-u', user, '-P', password, '-t', topic, '-d', '-W', '5',
  ])
  let log = ''
  for (const out of [sub.stdout, sub.stderr]) out.on('data', (d) => (log += d))
  const exited = once(sub, 'exit')
  await expect.poll(() => log, { message: 'subscribed' }).toContain('received SUBACK')
  meanwhile()
  await exited
  return log
}

/** The text of the newest mail Mailpit caught for `to`, once it caught `count` of them. */
export async function mailTo(to: string, count = 1): Promise<string> {
  let ids: string[] = []
  await expect
    .poll(async () => {
      const res = await fetch(`${MAIL}/api/v1/search?query=${encodeURIComponent(`to:"${to}"`)}`)
      ids = ((await res.json()).messages ?? []).map((m: { ID: string }) => m.ID)
      return ids.length
    }, { message: `${count} mail(s) to ${to}` })
    .toBeGreaterThanOrEqual(count)
  return (await (await fetch(`${MAIL}/api/v1/message/${ids[0]}`)).json()).Text
}

/** The password-reset link in a reset mail. */
export const resetLink = (mail: string) => /(http\S+\/reset-password\/[0-9a-f]{64})/.exec(mail)?.[1] ?? `no link in: ${mail}`

const ORIGIN = new URL(BASE).origin

/** Fails the test on an uncaught page error, a 5xx, a request leaving the stack (the app
 * serves everything itself, Chart.js included), or anything the Content-Security-Policy
 * refused (the browser reports that on the console). */
async function guard(ctx: BrowserContext, problems: string[]) {
  ctx.on('weberror', (e) => problems.push(`page error: ${e.error().message}`))
  ctx.on('console', (m) => {
    if (m.type() === 'error' && /Content Security Policy/i.test(m.text())) problems.push(`csp: ${m.text()}`)
  })
  ctx.on('response', (r) => {
    if (r.status() >= 500) problems.push(`${r.status()} ${r.request().method()} ${r.url()}`)
  })
  await ctx.route(
    (url) => url.origin !== ORIGIN,
    (route) => {
      problems.push(`unexpected request: ${route.request().url()}`)
      return route.abort()
    },
  )
}

type Fixtures = {
  problems: string[]
  /** A new page signed in with `state` (signed out without), guarded like `page`. */
  open: (state?: State | string) => Promise<Page>
}

export const test = base.extend<Fixtures>({
  problems: [
    async ({}, use) => {
      const problems: string[] = []
      await use(problems)
      expect(problems, 'page errors, 5xx, foreign requests').toEqual([])
    },
    { auto: true },
  ],
  context: async ({ context, problems }, use) => {
    await guard(context, problems)
    await use(context)
  },
  open: async ({ browser, problems }, use, info) => {
    const opened: BrowserContext[] = []
    const u = info.project.use
    await use(async (state) => {
      const ctx = await browser.newContext({
        baseURL: u.baseURL,
        viewport: u.viewport,
        locale: u.locale,
        timezoneId: u.timezoneId,
        storageState: state,
      })
      opened.push(ctx)
      await guard(ctx, problems)
      return ctx.newPage()
    })
    for (const ctx of opened) await ctx.close()
  },
})

export { expect }

export const flash = (page: Page) => page.locator('body > .success, body > .error')

/** Opens `path`, fills its fields (by label) and presses `button`. Returns the
 * POST's response status once the answer has loaded. */
export async function submitForm(page: Page, path: string, fields: Record<string, string>, button: string) {
  await page.goto(path)
  for (const [label, value] of Object.entries(fields)) await page.getByLabel(label, { exact: true }).fill(value)
  const [res] = await Promise.all([
    page.waitForResponse((r) => r.request().method() === 'POST'),
    page.getByRole('button', { name: button, exact: true }).click(),
  ])
  await page.waitForLoadState()
  return res.status()
}
