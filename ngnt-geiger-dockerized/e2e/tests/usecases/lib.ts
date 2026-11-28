import { execFileSync, spawn } from 'node:child_process'
import { once } from 'node:events'
import { randomBytes } from 'node:crypto'

import type { Page } from '@playwright/test'

import {
  BASE, CONTAINER, MAIL, PASSWORD, addReadings, createDevice, createUser, expect, flash, mqttCredentials, mqttPublish, q,
  randomMac, resetLink, sql, test as base, uid, type User,
} from '../support'

/* Use cases: a chain in which one person hands something to another - an
 * account, a password file, a mailed link, a pepper, a setting - and the next
 * one USES it (COVERAGE.c.md).
 *
 * The feature specs set their preconditions up over HTTP and SQL: every
 * sign-in was signIn() (a form POST from an API context) or the admin session
 * setup.ts saved, the pepper a counter is set up with was SQL, and the
 * counter's last will was published by the test. Of 17 hand-overs, 2 were
 * followed to the next person in the browser (2026-11-22).
 *
 * The rules here, pinned by tests/test_e2e_hygiene.py:
 * - Every person acts in their own browser and logs in through the FORM - the
 *   admin too (`browserOf(THE_ADMIN)`).
 * - What is handed over is the SAME thing that is used: the password in the
 *   file the server wrote, the mail to that address (`inboxOf`), the pepper on
 *   the counter's screen.
 * - The outcome is read on the affected person's screen.
 * - A use-case spec imports only this file and calls no API. Facts a use case
 *   needs but is not about - an account, a counter that has been reporting -
 *   come from `given` below. A Geiger counter is a program, not a person: what
 *   Mosquitto lets it do is what it sees (`aCounter`). */

export { expect, flash, uid }

/** The site's address, as the admin types it into Base URL. */
export const SITE_URL = BASE

export type Person = { username: string; password: string }

/** The bootstrap admin, once the first-run use case gave it PASSWORD. */
export const THE_ADMIN: Person = { username: 'admin', password: PASSWORD }

export const nav = (page: Page) => page.getByRole('navigation')

/** Fills in the login form and presses Login; what happens next is the test's to say. */
export async function tryToLogIn(page: Page, who: Person) {
  await page.goto('/login')
  await page.getByLabel('Username').fill(who.username)
  await page.getByLabel('Password', { exact: true }).fill(who.password)
  await page.getByRole('button', { name: 'Login', exact: true }).click()
}

export async function logIn(page: Page, who: Person) {
  await tryToLogIn(page, who)
  await expect(flash(page)).toHaveText('Logged in.')
}

export const test = base.extend<{
  /** A person's own browser - their own cookies - logged in through the form when `who` is given. */
  browserOf: (who?: Person) => Promise<Page>
}>({
  browserOf: async ({ open }, use) => {
    await use(async (who) => {
      const page = await open()
      if (who) await logIn(page, who)
      return page
    })
  },
})

const PASSWORD_FILE = '/app/data/admin_initial_password.txt'

/** data/admin_initial_password.txt as the operator reads it on the server; null once it is gone. */
export function adminPasswordFile(): string | null {
  try {
    return execFileSync('docker', ['exec', CONTAINER.flask, 'sh', '-c', 'test -e "$0" || exit 3; cat "$0"', PASSWORD_FILE], {
      stdio: ['ignore', 'pipe', 'pipe'],
    }).toString()
  } catch (e) {
    if ((e as { status?: number }).status === 3) return null
    throw e
  }
}

/** The mails to `to`, newest first, once `count` have come: what that person finds in their inbox. */
export async function inboxOf(to: string, count = 1): Promise<{ from: string; text: string }[]> {
  let found: { ID: string; From: { Address: string } }[] = []
  await expect
    .poll(async () => {
      const res = await fetch(`${MAIL}/api/v1/search?query=${encodeURIComponent(`to:"${to}"`)}`)
      found = (await res.json()).messages ?? []
      return found.length
    }, { message: `mail to ${to}` })
    .toBeGreaterThanOrEqual(count)
  return Promise.all(
    found.map(async (m) => ({ from: m.From.Address, text: (await (await fetch(`${MAIL}/api/v1/message/${m.ID}`)).json()).Text })),
  )
}

/** The reset link in a mail, as the person clicks it. */
export const linkIn = resetLink

const utcNow = () => new Date().toISOString().slice(0, 19).replace('T', ' ')

/** A Geiger counter on its owner's desk: the MAC on its label and the pepper on
 * its screen - the firmware makes one up (8 hex digits) unless the owner typed
 * theirs into its setup portal. It logs in to Mosquitto's TLS listener with what
 * the firmware derives from those two, and retries every second until let in
 * (Mosquitto reads new logins every 5 s). */
export function aCounter(pepper = randomBytes(4).toString('hex')) {
  const mac = randomMac()
  const { id, password } = mqttCredentials(mac, pepper)
  const topic = `/${id}/impulses`
  const connected = { id, status: 'connected' }
  /** It reconnects until let in, says "connected", sends a reading of `cpm` if given, and disconnects. */
  const connects = (cpm?: number) =>
    expect
      .poll(() => mqttPublish(id, password, cpm == null ? [connected] : [connected, { id, ts: utcNow(), cpm, usvh: +(cpm * 0.0057).toFixed(4) }]).ok, {
        message: `${id} let in`,
        intervals: [1_000],
      })
      .toBe(true)
  return {
    mac,
    pepper,
    id,
    connects,
    /** It reconnects until the broker refuses its login. */
    isLockedOut: () =>
      expect
        .poll(() => mqttPublish(id, password, connected).out, { message: `${id} refused`, intervals: [1_000] })
        .toContain('not authorised'),
    /** Switched on and left on: once let in (it says "connected" then), it holds a connection
     * with the firmware's last will ("offline" on its own topic) until `powerCut()`. */
    async switchOn() {
      await connects()
      // A terminal (-t): into a pipe the client's log comes only when it exits.
      const sub = spawn('docker', [
        'exec', '-t', CONTAINER.mosquitto, 'sh', '-c', 'echo $$; exec mosquitto_sub "$@"', 'sh',
        '-h', 'localhost', '-p', '8883', '--cafile', '/mosquitto/certs/ca.crt', '--insecure', '-d',
        '-u', id, '-P', password, '-t', topic, '--will-topic', topic, '--will-payload', JSON.stringify({ id, status: 'offline' }),
      ])
      let log = ''
      sub.stdout.on('data', (d) => (log += d))
      const exited = once(sub, 'exit')
      await expect.poll(() => log, { message: `${id} connected with its last will` }).toContain('received SUBACK')
      return {
        /** The power goes: the connection drops without a goodbye, so the broker publishes the last will. */
        async powerCut() {
          expect(sub.exitCode, `${id} still connected when the power went`).toBeNull()
          execFileSync('docker', ['exec', CONTAINER.mosquitto, 'kill', '-9', /^\d+/.exec(log)![0]])
          await exited
        },
      }
    },
  }
}

/** Background facts, made with SQL: what a use case needs but is not about.
 * Never the act under test, and never the check of its outcome. */
export const given = {
  /** An account that exists, with an email and a pepper (`pepper: null`: none yet). */
  user: (opts: Parameters<typeof createUser>[0] = {}): User & Person & { email: string } => ({
    ...createUser(opts),
    password: PASSWORD,
  }),
  /** A counter `owner` has had for a while: its row, status and a reading as the subscriber
   * writes them, `minutesAgo` 1. It has no login in Mosquitto. */
  counter(owner: User, opts: { status?: 'online' | 'offline'; lastSeenMinutesAgo?: number; lastSeenAt?: string; cpm?: number } = {}) {
    const dev = createDevice(owner, { name: `Counter ${uid()}`, status: opts.status, lastSeenMinutesAgo: opts.lastSeenMinutesAgo })
    if (opts.lastSeenAt) sql(`UPDATE devices SET last_seen = ${q(opts.lastSeenAt)} WHERE device_id = ${q(dev.id)};`)
    if (opts.cpm != null) addReadings(dev.id, [{ minutesAgo: 1, cpm: opts.cpm }])
    return dev as typeof dev & { name: string }
  },
  /** Someone else's public counter: the dashboard lists the counters to pick from once two are visible. */
  publicCounter: () => createDevice(createUser({ public: true })),
  /** The site settings as a fresh stack has them, put back after a use case changed them. */
  settingsBack: () =>
    sql(
      'INSERT INTO settings (`key`, `value`) VALUES ' +
        Object.entries(SETTINGS)
          .map(([k, v]) => `(${q(k)}, ${q(v)})`)
          .join(', ') +
        ' ON DUPLICATE KEY UPDATE `value` = VALUES(`value`);',
    ),
}

const SETTINGS = {
  display_timezone: 'Europe/Vienna',
  offline_timeout_minutes: '5',
  default_alert_threshold: '0.5',
  session_timeout_minutes: '1440',
  registration_enabled: '1',
}
