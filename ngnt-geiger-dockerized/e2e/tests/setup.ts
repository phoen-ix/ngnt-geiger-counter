import { execFileSync } from 'node:child_process'
import { mkdirSync, writeFileSync } from 'node:fs'

import { request } from '@playwright/test'

import {
  ADMIN_STATE, AUTH, BASE, CONTAINER, PASSWORD, USER_STATE, createUser, expect, postForm, signIn, test,
} from './support'

/* Runs once before the specs: the bootstrap admin gets PASSWORD (its first one
 * is random, in data/), password-reset mail goes to Mailpit, and the shared
 * admin and user sessions are saved for the sweep and the admin specs. */

test('admin password, SMTP to Mailpit, shared sessions', async () => {
  mkdirSync(AUTH, { recursive: true })
  let initial: string | undefined
  try {
    const file = execFileSync('docker', ['exec', CONTAINER.flask, 'cat', '/app/data/admin_initial_password.txt'], {
      stdio: ['ignore', 'pipe', 'ignore'],
    }).toString()
    initial = /^Password: (.+)$/m.exec(file)?.[1]
  } catch {
    // Already changed (a second run against the same stack): the file is gone.
  }
  if (initial) {
    const api = await request.newContext({ baseURL: BASE, storageState: await signIn('admin', initial) })
    const res = await postForm(api, '/account', {
      action: 'change_password',
      current_password: initial,
      new_password: PASSWORD,
      new_password2: PASSWORD,
    })
    expect(await res.text()).toContain('Password changed.')
    await api.dispose()
  }

  const admin = await request.newContext({ baseURL: BASE, storageState: await signIn('admin') })
  const smtp = await postForm(admin, '/admin', {
    action: 'save_smtp',
    smtp_host: 'mailpit',
    smtp_port: '1025',
    smtp_user: '',
    smtp_password: '',
    smtp_from: 'geiger@geiger-e2e.example',
    smtp_tls: '0',
  })
  expect(await smtp.text()).toContain('SMTP settings saved.')
  const settings = await postForm(admin, '/admin', {
    action: 'save_settings',
    base_url: BASE,
    display_timezone: 'Europe/Vienna',
    offline_timeout_minutes: '5',
    default_alert_threshold: '0.5',
    session_timeout_minutes: '1440',
    registration_enabled: '1',
  })
  expect(await settings.text()).toContain('Settings saved.')
  writeFileSync(ADMIN_STATE, JSON.stringify(await admin.storageState()))
  await admin.dispose()

  writeFileSync(USER_STATE, JSON.stringify(await signIn(createUser().username)))
})
