import { seedDemo } from './seed'

/* Seeds once per run. In a beforeAll, a worker restarted after a failed test
 * would seed (and sign in) again and soon hit the login rate limit (10/min),
 * failing every later test with a 429. The workers inherit the sessions. */
export default async function globalSetup(): Promise<void> {
  process.env.DOCS_SESSIONS = JSON.stringify(await seedDemo())
}
