import { mkdirSync, writeFileSync } from 'node:fs'

import { ADMIN_STATE, AUTH, USER_STATE, createUser, signIn, test } from './support'

/* Runs once before the specs, after the first-run use cases
 * (tests/usecases/first-run.spec.ts), in which the bootstrap admin took PASSWORD
 * and pointed the reset mail at Mailpit on the admin page: saves the shared
 * admin and user sessions for the sweep and the feature specs. */

test('shared sessions', async () => {
  mkdirSync(AUTH, { recursive: true })
  writeFileSync(ADMIN_STATE, JSON.stringify(await signIn('admin')))
  writeFileSync(USER_STATE, JSON.stringify(await signIn(createUser().username)))
})
