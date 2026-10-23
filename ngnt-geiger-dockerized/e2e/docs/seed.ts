import { execFileSync } from 'node:child_process'

import { expect, request, type APIRequestContext } from '@playwright/test'

/* The demo stack the docs tour photographs: three fictional users and three
 * Geiger counters, seeded through the app's own forms (register, account,
 * devices, admin). SQL only does what no form can: the measurement time series
 * and device status (the subscriber writes those from MQTT), backdated
 * created_at values, and a password-reset token (normally emailed).
 *
 * IDEMPOTENT: users and devices are looked up before they are created, the
 * SQL part replaces what it wrote before. A second run against the same stack
 * ends in the same state. */

export const BASE = process.env.E2E_BASE_URL ?? 'http://127.0.0.1:1880'
const PROJECT = process.env.COMPOSE_PROJECT_NAME ?? 'ngnt-geiger-counter_docs'
const DB = process.env.DOCS_DB_CONTAINER ?? `${PROJECT}-ngnt-geiger-mariadb`
const FLASK = process.env.DOCS_FLASK_CONTAINER ?? `${PROJECT}-ngnt-geiger-flask`

/** Every account's password once the seed has run (the admin's initial one is random). */
export const PASSWORD = 'geiger-docs-tour-7Kq2'

export const PEOPLE = {
  lena: { username: 'lena_hofer', email: 'lena.hofer@geiger-demo.example', pepper: '3f9a71c2', public: true },
  markus: { username: 'markus_steiner', email: 'markus.steiner@geiger-demo.example', pepper: 'b41e07d5', public: false },
  // No pepper, no devices: the onboarding state of the devices page.
  jonas: { username: 'jonas_berger', email: 'jonas.berger@geiger-demo.example', pepper: '', public: false },
}
type Person = keyof typeof PEOPLE

/** device_id = geiger_ + the MAC's last 6 hex digits (helpers.derive_mqtt_credentials). */
export const DEVICES = {
  balcony: { owner: 'lena', mac: '5C:CF:7F:3A:91:C4', name: 'Balcony', id: 'geiger_3a91c4' },
  basement: { owner: 'lena', mac: '5C:CF:7F:8E:02:B7', name: 'Basement', id: 'geiger_8e02b7' },
  shed: { owner: 'markus', mac: 'A4:CF:12:6D:E5:38', name: 'Garden shed', id: 'geiger_6de538' },
} as const

export const RESET_TOKEN = 'd0c5' + '7e57'.repeat(15)

export type Who = 'admin' | Person
export type Sessions = Record<Who, Awaited<ReturnType<APIRequestContext['storageState']>>>

const CSRF = /name="csrf_token" value="([^"]+)"/

/** GET `path`, then POST its form with the page's CSRF token; returns the page the redirect lands on. */
async function submit(api: APIRequestContext, path: string, form: Record<string, string>) {
  const page = await api.get(path)
  expect(page.ok(), `GET ${path}`).toBe(true)
  const token = CSRF.exec(await page.text())?.[1]
  if (!token) throw new Error(`[seed] no CSRF token on ${path}`)
  const res = await api.post(path, { form: { csrf_token: token, ...form } })
  expect(res.ok(), `POST ${path}: ${res.status()}`).toBe(true)
  return { path: new URL(res.url()).pathname, html: await res.text() }
}

async function signIn(username: string, password: string): Promise<APIRequestContext | null> {
  const api = await request.newContext({ baseURL: BASE })
  const res = await submit(api, '/login', { username, password })
  if (res.path === '/') return api
  await api.dispose()
  return null
}

function sql(statements: string): void {
  execFileSync('docker', ['exec', '-i', DB, 'sh', '-c', 'mariadb -uroot -p"$MARIADB_ROOT_PASSWORD" "$MARIADB_DATABASE"'], {
    input: statements,
  })
}

/** The bootstrap admin's password: the random one from data/ until the seed changed it. */
async function adminSession(): Promise<APIRequestContext> {
  let initial: string | undefined
  try {
    const file = execFileSync('docker', ['exec', FLASK, 'cat', '/app/data/admin_initial_password.txt'], {
      stdio: ['ignore', 'pipe', 'ignore'],
    }).toString()
    initial = /^Password: (.+)$/m.exec(file)?.[1]
  } catch {
    // Already changed: the file is gone.
  }
  if (initial) {
    const api = await signIn('admin', initial)
    if (!api) throw new Error('[seed] the initial admin password from data/ does not work')
    await submit(api, '/account', {
      action: 'change_password',
      current_password: initial,
      new_password: PASSWORD,
      new_password2: PASSWORD,
    })
    await api.dispose()
  }
  const api = await signIn('admin', PASSWORD)
  if (!api) throw new Error('[seed] cannot sign in as admin')
  return api
}

/** The series ends at the start of the current UTC hour, not at "now", so two
 * runs within the same hour render identical timestamps. The server computes
 * the dashboard windows (NOW() - INTERVAL 24 HOUR), which still cover 23 h of
 * readings before the anchor. */
const ANCHOR = "DATE_FORMAT(UTC_TIMESTAMP(), '%Y-%m-%d %H:00:00')"

/** Counts per minute: a slow drift plus two hashed "random" terms, so the
 * series looks measured but is the same on every run. */
const cpm = (base: number, spread: number, dev: string) =>
  `${base} + CRC32(CONCAT('a', '${dev}', seq)) % ${spread} + CRC32(CONCAT('b', '${dev}', seq)) % ${spread} ` +
  `+ ROUND(3 * SIN(seq / 170) + 1.5 * SIN(seq / 53))`

function series(dev: string, endHoursAgo: number, minutes: number, base: number, spread: number): string {
  return (
    `INSERT INTO measurements (device_id, measured_at, cpm, usvh) ` +
    `SELECT '${dev}', @anchor - INTERVAL ${endHoursAgo} HOUR - INTERVAL seq MINUTE, c, ROUND(c * 0.0057, 4) ` +
    `FROM (SELECT seq, ${cpm(base, spread, dev)} AS c FROM seq_0_to_${minutes - 1}) s;\n`
  )
}

export async function seedDemo(): Promise<Sessions> {
  const admin = await adminSession()
  const sessions = {} as Sessions

  await submit(admin, '/admin', {
    action: 'save_settings',
    base_url: 'https://radiation.geiger-demo.example',
    display_timezone: 'Europe/Vienna',
    // Device "last seen" is pinned to the hour anchor (up to 59 min ago), so
    // the default 5 would show every device offline.
    offline_timeout_minutes: '90',
    default_alert_threshold: '0.5',
    session_timeout_minutes: '1440',
    registration_enabled: '1',
  })
  await submit(admin, '/admin', {
    action: 'save_smtp',
    smtp_host: 'smtp.geiger-demo.example',
    smtp_port: '587',
    smtp_user: 'geiger@geiger-demo.example',
    smtp_password: 'not-a-real-password',
    smtp_from: 'geiger@geiger-demo.example',
    smtp_tls: '1',
  })

  const users = await (await admin.get('/admin')).text()
  for (const [who, p] of Object.entries(PEOPLE) as [Person, (typeof PEOPLE)[Person]][]) {
    if (!users.includes(`<td>${p.username}</td>`)) {
      const anon = await request.newContext({ baseURL: BASE })
      const res = await submit(anon, '/register', {
        username: p.username,
        email: p.email,
        password: PASSWORD,
        password2: PASSWORD,
      })
      expect(res.path, `register ${p.username}`).toBe('/login')
      await anon.dispose()
    }
    const api = await signIn(p.username, PASSWORD)
    if (!api) throw new Error(`[seed] cannot sign in as ${p.username}`)
    await submit(api, '/account', {
      action: 'update_profile',
      email: p.email,
      pepper: p.pepper,
      public: p.public ? '1' : '0',
    })
    const listed = await (await api.get('/devices')).text()
    for (const d of Object.values(DEVICES).filter((d) => d.owner === who)) {
      if (listed.includes(`<td>${d.id}</td>`)) continue
      const res = await submit(api, '/devices', { action: 'add_device', mac_address: d.mac, display_name: d.name })
      expect(res.html, `register ${d.id}`).toContain(`Device ${d.id} registered and provisioned.`)
    }
    // The redirect after the last POST consumed its flash message.
    sessions[who] = await api.storageState()
    await api.dispose()
  }
  sessions.admin = await admin.storageState()
  await admin.dispose()

  const { balcony, basement, shed } = DEVICES
  sql(
    `SET @anchor = ${ANCHOR};\n` +
      `DELETE FROM measurements WHERE device_id IN ('${balcony.id}', '${basement.id}', '${shed.id}');\n` +
      // One reading a minute, like the firmware: 23 h for the two live devices,
      // 6 h ending two days ago for the basement one (unplugged since).
      series(balcony.id, 0, 23 * 60, 15, 3) +
      series(shed.id, 0, 23 * 60, 12, 3) +
      series(basement.id, 50, 6 * 60, 30, 6) +
      `UPDATE devices SET status = 'online', last_seen = @anchor WHERE device_id IN ('${balcony.id}', '${shed.id}');\n` +
      `UPDATE devices SET status = 'offline', last_seen = @anchor - INTERVAL 50 HOUR WHERE device_id = '${basement.id}';\n` +
      `UPDATE users SET created_at = CASE username ` +
      `WHEN 'admin' THEN '2026-02-14 18:02:11' ` +
      `WHEN '${PEOPLE.lena.username}' THEN '2026-02-15 09:41:27' ` +
      `WHEN '${PEOPLE.markus.username}' THEN '2026-03-03 20:15:02' ` +
      `WHEN '${PEOPLE.jonas.username}' THEN '2026-06-12 07:58:40' ELSE created_at END;\n` +
      `INSERT INTO password_resets (user_id, token, expires_at) ` +
      `SELECT id, '${RESET_TOKEN}', UTC_TIMESTAMP() + INTERVAL 1 DAY FROM users WHERE username = '${PEOPLE.jonas.username}' ` +
      `ON DUPLICATE KEY UPDATE used = FALSE, expires_at = VALUES(expires_at);\n`,
  )
  return sessions
}
