import { readFileSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { expect, test, type Browser, type BrowserContext, type Locator, type Page, type TestInfo } from '@playwright/test'

import { BASE, DEVICES, PEOPLE, RESET_TOKEN, type Sessions, type Who } from './seed'

/* The docs tour: one screenshot per screen (docs/screenshots/README.md is the
 * gallery) of the demo stack in ./seed.ts. Run it through
 * scripts/docs-screenshots.sh. It is also a render check: every test asserts
 * something page-specific before it shoots, and an uncaught page error or a
 * request leaving the stack fails the test.
 *
 * All dates are rendered by the server (UTC from the DB, shown in the display
 * timezone), so there is no browser clock to fix: the seed anchors them. */

const OUT = path.resolve(
  process.env.DOCS_SCREENSHOT_DIR ?? fileURLToPath(new URL('../../../docs/screenshots', import.meta.url)),
)

// dashboard.html loads Chart.js from jsDelivr; serve the same release from
// node_modules instead (package.json pins it to the template's version).
const CHART_DIR = fileURLToPath(new URL('../node_modules/chart.js/', import.meta.url))
const CHART_VERSION = JSON.parse(readFileSync(path.join(CHART_DIR, 'package.json'), 'utf8')).version
const CHART_URL = `https://cdn.jsdelivr.net/npm/chart.js@${CHART_VERSION}/dist/chart.umd.min.js`
const ORIGIN = new URL(BASE).origin

// Seeded by ./global-setup.ts.
const sessions: Sessions = JSON.parse(process.env.DOCS_SESSIONS ?? '{}')
let pageErrors: string[] = []
const opened: BrowserContext[] = []

test.beforeEach(async ({ context, page }) => {
  pageErrors = []
  await prepare(context)
  page.on('pageerror', (e) => pageErrors.push(e.message))
})

test.afterEach(async () => {
  for (const ctx of opened.splice(0)) await ctx.close()
  expect(pageErrors).toEqual([])
})

/** Only the stack and the local Chart.js copy, anything else fails the test;
 * and Chart.js without animations, so the chart is drawn in its final state
 * at once (`reducedMotion` does not reach a canvas). */
async function prepare(ctx: BrowserContext): Promise<void> {
  await ctx.addInitScript(() => {
    let chart: any
    Object.defineProperty(window, 'Chart', {
      configurable: true,
      get: () => chart,
      set: (value) => {
        chart = value
        chart.defaults.animation = false
      },
    })
  })
  await ctx.route(
    (url) => url.origin !== ORIGIN,
    (route) => {
      if (route.request().url() === CHART_URL) {
        return route.fulfill({ path: path.join(CHART_DIR, 'dist/chart.umd.js'), contentType: 'text/javascript' })
      }
      pageErrors.push(`unexpected request: ${route.request().url()}`)
      return route.abort()
    },
  )
}

/** A page signed in as `who`, with the project's settings (viewport, locale, ...). */
async function as(browser: Browser, info: TestInfo, who: Who): Promise<Page> {
  const u = info.project.use
  const ctx = await browser.newContext({
    baseURL: u.baseURL,
    viewport: u.viewport,
    deviceScaleFactor: u.deviceScaleFactor,
    locale: u.locale,
    timezoneId: u.timezoneId,
    userAgent: u.userAgent,
    ...u.contextOptions,
    storageState: sessions[who],
  })
  opened.push(ctx)
  await prepare(ctx)
  const page = await ctx.newPage()
  page.on('pageerror', (e) => pageErrors.push(e.message))
  return page
}

/** The chart exists and has been laid out at its final size. */
async function chartDrawn(page: Page): Promise<void> {
  await expect(page.locator('canvas#chart')).toBeVisible()
  await page.waitForFunction(() => Boolean((window as any).Chart?.getChart('chart')))
  // Its ResizeObserver redraws once after the first layout.
  await page.evaluate(() => new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(done))))
}

/** `clipAbove`: the page from the top down to just above that element.
 * `fullPage`: the whole page, at most 2400 CSS px. Otherwise the viewport. */
async function shoot(page: Page, name: string, opts: { fullPage?: boolean; clipAbove?: string } = {}) {
  await page.evaluate(() => document.fonts.ready)
  await page.evaluate(() => window.scrollTo(0, 0))
  await page.mouse.move(0, 0)
  const width = page.viewportSize()!.width
  let clip: { x: number; y: number; width: number; height: number } | undefined
  if (opts.clipAbove) {
    const box = await page.locator(opts.clipAbove).boundingBox()
    if (!box) throw new Error(`[docs] ${opts.clipAbove} is not on the page`)
    clip = { x: 0, y: 0, width, height: Math.min(2400, Math.floor(box.y)) }
  } else if (opts.fullPage) {
    const height = await page.evaluate(() => document.documentElement.scrollHeight)
    clip = { x: 0, y: 0, width, height: Math.min(2400, height) }
  }
  await page.screenshot({
    path: path.join(OUT, `${name}.png`),
    animations: 'disabled',
    caret: 'hide',
    fullPage: Boolean(clip),
    clip,
  })
}

const heading = (page: Page, text: string) => expect(page.getByRole('heading', { name: text })).toBeVisible()

/** Each select is as wide as its longest option, so no option text is cut off. */
async function selectsFit(selects: Locator): Promise<void> {
  for (const select of await selects.all()) {
    const [width, needed] = await select.evaluate((el: HTMLElement) => {
      const width = el.offsetWidth
      const prev = el.style.width
      el.style.width = 'max-content'
      const needed = el.offsetWidth
      el.style.width = prev
      return [width, needed]
    })
    expect(width, await select.getAttribute('id') ?? 'select').toBeGreaterThanOrEqual(needed)
  }
}

/** Every form field starts at the same x, however long its label. */
async function fieldsAligned(page: Page): Promise<void> {
  const lefts = await page
    .locator('.form-row > :nth-child(2)')
    .evaluateAll((els) => els.map((el) => el.getBoundingClientRect().left))
  expect(new Set(lefts).size).toBe(1)
}

/** The page never scrolls sideways, no field is squeezed too narrow to type in,
 *  and no blank spacer label leaves an empty line above a stacked button. */
async function fitsPhone(page: Page): Promise<void> {
  const { scrollWidth, innerWidth, narrowest, blankLabels } = await page.evaluate(() => ({
    scrollWidth: document.documentElement.scrollWidth,
    innerWidth: window.innerWidth,
    narrowest: Math.min(
      ...[...document.querySelectorAll<HTMLElement>('input:not([type=hidden]), select')].map((el) => el.offsetWidth),
    ),
    blankLabels: [...document.querySelectorAll<HTMLElement>('.form-row label')].filter(
      (el) => el.offsetHeight > 0 && !el.textContent?.trim(),
    ).length,
  }))
  expect(scrollWidth, page.url()).toBeLessThanOrEqual(innerWidth)
  expect(narrowest, page.url()).toBeGreaterThanOrEqual(100)
  expect(blankLabels, page.url()).toBe(0)
}

/* --- signed out --------------------------------------------------------- */

test('login', async ({ page }) => {
  await page.goto('/login')
  await heading(page, 'Login')
  await shoot(page, 'login')
})

test('login: wrong password', async ({ page }) => {
  await page.goto('/login')
  await page.fill('#username', PEOPLE.lena.username)
  await page.fill('#password', 'not-the-password')
  await page.click('button[type=submit]')
  await expect(page.locator('.error')).toHaveText('Invalid username or password.')
  await shoot(page, 'login-error')
})

test('register', async ({ page }) => {
  await page.goto('/register')
  await heading(page, 'Create Account')
  await shoot(page, 'register')
})

test('forgot password', async ({ page }) => {
  await page.goto('/forgot-password')
  await heading(page, 'Forgot Password')
  await shoot(page, 'forgot-password')
})

test('reset password', async ({ page }) => {
  await page.goto(`/reset-password/${RESET_TOKEN}`)
  await heading(page, 'Set New Password')
  await shoot(page, 'reset-password')
})

/* --- signed in ---------------------------------------------------------- */

test('dashboard', async ({ browser }, info) => {
  const page = await as(browser, info, 'lena')
  await page.goto('/')
  await expect(page.locator('header p')).toContainText('Last reading:')
  await expect(page.locator('.card-unit', { hasText: 'online' })).toBeVisible()
  await chartDrawn(page)
  // The unit keeps its case (the other headers are upper-cased by CSS).
  await expect(page.locator('th', { hasText: 'Sv/h' })).toHaveText('μSv/h', { useInnerText: true })
  // Header, cards, chart and the first rows of the table.
  await shoot(page, 'dashboard', { clipAbove: 'tbody tr:nth-child(8)' })
})

test('dashboard: offline device, no readings in range', async ({ browser }, info) => {
  const page = await as(browser, info, 'lena')
  await page.goto(`/?device=${DEVICES.basement.id}`)
  await expect(page.locator('#device-select')).toHaveValue(DEVICES.basement.id)
  await expect(page.locator('.card-unit', { hasText: 'offline' })).toBeVisible()
  await expect(page.getByText('No data available for the last 24 hours.')).toBeVisible()
  await shoot(page, 'dashboard-device-offline')
})

test('devices', async ({ browser }, info) => {
  const page = await as(browser, info, 'lena')
  await page.goto('/devices')
  await heading(page, 'Your Devices')
  await expect(page.locator('tbody tr')).toHaveCount(2)
  await expect(page.locator('.status-online')).toHaveCount(1)
  await shoot(page, 'devices')
})

test('devices: no pepper yet', async ({ browser }, info) => {
  const page = await as(browser, info, 'jonas')
  await page.goto('/devices')
  await expect(page.getByText('You need to set a pepper')).toBeVisible()
  await expect(page.getByText('No devices registered yet.')).toBeVisible()
  await shoot(page, 'devices-no-pepper')
})

test('account', async ({ browser }, info) => {
  const page = await as(browser, info, 'lena')
  await page.goto('/account')
  await expect(page.locator('#pepper')).toHaveValue(PEOPLE.lena.pepper)
  await selectsFit(page.locator('.form-row select'))
  await fieldsAligned(page)
  await shoot(page, 'account', { fullPage: true })
})

test('admin', async ({ browser }, info) => {
  const page = await as(browser, info, 'admin')
  await page.goto('/admin')
  await heading(page, 'Users')
  await expect(page.locator('tbody tr')).toHaveCount(1 + Object.keys(PEOPLE).length)
  await expect(page.locator('#smtp-host')).toHaveValue('smtp.geiger-demo.example')
  await selectsFit(page.locator('.form-row select'))
  await fieldsAligned(page)
  await shoot(page, 'admin', { fullPage: true })
})

/* --- phone (390 x 844) -------------------------------------------------- */

test('phone: every page fits', async ({ browser, page }, info) => {
  const phone = { width: 390, height: 844 }
  await page.setViewportSize(phone)
  for (const url of ['/login', '/register', '/forgot-password', `/reset-password/${RESET_TOKEN}`]) {
    await page.goto(url)
    await fitsPhone(page)
  }
  for (const [who, urls] of [
    ['lena', ['/', `/?device=${DEVICES.basement.id}`, '/devices', '/account']],
    ['admin', ['/admin']],
  ] as const) {
    const signedIn = await as(browser, info, who)
    await signedIn.setViewportSize(phone)
    for (const url of urls) {
      await signedIn.goto(url)
      await fitsPhone(signedIn)
    }
  }
})

test('dashboard: phone', async ({ browser }, info) => {
  const page = await as(browser, info, 'lena')
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/')
  await expect(page.locator('.card-unit', { hasText: 'online' })).toBeVisible()
  await chartDrawn(page)
  await fitsPhone(page)
  // The plot itself, not just the axis labels, gets most of the chart's height.
  const plot = await page.evaluate(() => {
    const chart = (window as any).Chart.getChart('chart')
    return { area: chart.chartArea.height, total: chart.height }
  })
  expect(plot.area).toBeGreaterThan(plot.total / 2)
  await shoot(page, 'dashboard.mobile', { clipAbove: 'tbody tr:nth-child(6)' })
})
