"""A test step a person would not take is a finding about the product, never a fix for the test.

Adapted from fileHeron's scan (test_frontend_test_hygiene.py), whose picker bug hid for a year
behind a retry loop, a blur and a sleep in its tests. This reads EVERY e2e file and every pytest
file, not a list of today's, for those shapes and for the ones the 2026-11-22 audit found here:
publishing, reloading and resubmitting inside `expect.poll`, a loop that posts until refused, and
scripts that edit the page. Each use kept is in an allowlist with its count and why it is not a
workaround; a use that goes away must leave the list too.
"""

import fnmatch
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
E2E = ROOT / 'e2e'
E2E_FILES = sorted(p for p in E2E.rglob('*.ts') if 'node_modules' not in p.parts)
# Not this file: its self-test quotes the shapes.
UNIT_FILES = sorted(p for p in (ROOT / 'tests').rglob('*.py') if p != Path(__file__).resolve())

# Something a person does, or that changes what the server holds.
_ACTION = re.compile(
    r'\.(?:click|dblclick|press|fill|type|check|uncheck|selectOption|hover|tap|setInputFiles|pressSequentially'
    r'|reload|goto)\(|\bkeyboard\.|\bmouse\.|\b(?:mqttPublish|postForm|submitForm)\(|\.post\(')
# A page script that changes the page instead of reading it.
_SETS_STATE = re.compile(
    r'\b(?:removeAttribute|setAttribute|click|scrollTo|scrollBy|focus|blur|dispatchEvent|submit|defineProperty)\('
    r'|\.\w+\s*=(?![=>])')

E2E_RULES = {
    'wait': re.compile(r'\b(?:waitForTimeout|setTimeout|requestAnimationFrame)\('),
    'force': re.compile(r'\bforce:\s*true'),
    'dispatch': re.compile(r'\.dispatchEvent\('),
    'focus-blur': re.compile(r'\.(?:focus|blur)\(\)'),
    # A click, and in the next statement the page is left: wait for what the person sees first.
    'click-then-leave': re.compile(
        r'\.(?:click|dblclick)\([^\n]*\)\n\s*await (?:[\w.]+\.(?:reload|goto|close)|submitForm)\('),
    # A loop that runs until some result shows up.
    'until-loop': re.compile(r'\bwhile\s*\(|\bfor\s*\([^;\n]*;[^;\n]*&&[^;\n]*;'),
}
UNIT_RULES = {
    'sleep': re.compile(r'\b(?:time|asyncio)\.sleep\('),
}

# (file relative to ngnt-geiger-dockerized/, rule) -> (count, why it is not a workaround).
E2E_ALLOWED = {
    ('e2e/tests/auth.spec.ts', 'retry-action'): (
        2, 'registration allows 5 a minute per address and the whole suite is one address: the register '
           'form, and a post past its email maxlength, go again after a 429'),
    ('e2e/tests/mqtt.spec.ts', 'retry-action'): (
        8, 'Mosquitto picks up new logins by a 5-s poll, so a counter connects until accepted as the '
           'firmware does; the devices page has no live update, so a person reloads it'),
    ('e2e/tests/admin.spec.ts', 'retry-action'): (
        2, 'a counter connects until Mosquitto took its login in, and again until the deletion reached it'),
    ('e2e/tests/devices.spec.ts', 'retry-action'): (
        2, 'a counter connects until Mosquitto took its login in (5-s reload poll)'),
    ('e2e/tests/repeat.spec.ts', 'retry-action'): (
        4, 'a counter connects until Mosquitto took its login in, or until its removal reached it'),
    ('e2e/tests/devices.spec.ts', 'script-state'): (
        1, "drops the MAC field's pattern to reach the server's own check; a forged post would do too"),
    ('e2e/docs/tour.spec.ts', 'script-state'): (
        3, 'screenshot preparation: Chart.js animation off, a select measured at max-content and '
           'put back, the page scrolled to the top before a shot'),
    ('e2e/docs/tour.spec.ts', 'wait'): (
        2, 'two animation frames after the chart exists, so the screenshot shows its last frame'),
}
UNIT_ALLOWED = {}


def _calls(src, opener):
    """The argument text of every call that starts with `opener` (which ends at its '(')."""
    for m in re.finditer(opener, src):
        depth, i, quote = 1, m.end(), None
        while depth and i < len(src):
            c = src[i]
            if quote:
                quote = None if c == quote and src[i - 1] != '\\' else quote
            elif c in '\'"`':
                quote = c
            else:
                depth += {'(': 1, ')': -1}.get(c, 0)
            i += 1
        yield src[m.end():i - 1]


def _e2e_findings(src):
    found = {name: len(rule.findall(src)) for name, rule in E2E_RULES.items()}
    retries = [*_calls(src, r'expect\s*\.poll\('), *_calls(src, r'expect\(\s*async')]
    found['retry-action'] = sum(bool(_ACTION.search(body)) for body in retries)
    scripts = [*_calls(src, r'\.evaluate(?:All)?\('), *_calls(src, r'addInitScript\(')]
    found['script-state'] = sum(bool(_SETS_STATE.search(body)) for body in scripts)
    return {k: v for k, v in found.items() if v}


def _unit_findings(src):
    return {k: v for k, rule in UNIT_RULES.items() if (v := len(rule.findall(src)))}


def _scan(files, findings):
    return {(str(p.relative_to(ROOT)), rule): n for p in files for rule, n in findings(p.read_text()).items()}


def test_no_e2e_step_a_person_would_not_take():
    assert len(E2E_FILES) >= 15, f'scanned only {len(E2E_FILES)} files: is the glob still right?'
    assert _scan(E2E_FILES, _e2e_findings) == {k: n for k, (n, _) in E2E_ALLOWED.items()}


def test_no_unit_test_sleeps():
    assert len(UNIT_FILES) >= 12, f'scanned only {len(UNIT_FILES)} files: is the glob still right?'
    assert _scan(UNIT_FILES, _unit_findings) == {k: n for k, (n, _) in UNIT_ALLOWED.items()}


def test_every_allowlisted_use_has_a_reason():
    for key, (n, why) in E2E_ALLOWED.items():
        assert n > 0 and len(why) > 30, key


def test_every_spec_runs_in_exactly_one_playwright_project():
    # A hand list in testMatch let a new spec file match no project and never run.
    config = (E2E / 'playwright.config.ts').read_text()
    projects = re.findall(r"\{ name: '(\w+)', testMatch: ('[^']+'|/[^/]+/)(?:, testIgnore: (/[^/]+/))?", config)
    assert len(projects) >= 4, projects

    def matches(pattern, name):
        return re.search(pattern[1:-1], name) if pattern.startswith('/') else fnmatch.fnmatch(name, pattern[1:-1])

    specs = sorted(p.name for p in (E2E / 'tests').glob('*.spec.ts'))
    assert len(specs) >= 10
    for name in specs:
        runs_in = [p for p, match, skip in projects if matches(match, name) and not (skip and matches(skip, name))]
        assert len(runs_in) == 1, (name, runs_in)


def test_the_scan_catches_what_it_is_for():
    # fileHeron's three shapes, verbatim.
    arrow_down = """
  await expect(async () => {
    if (!(await opt.isVisible())) await box.press('ArrowDown')
    await expect(opt).toBeVisible({ timeout: 1_000 })
  }).toPass()
"""
    assert _e2e_findings(arrow_down) == {'retry-action': 1}
    assert _e2e_findings('await picker.blur()\nawait page.waitForTimeout(300)\n') == {'focus-blur': 1, 'wait': 1}
    assert _e2e_findings('await row.click({ force: true })') == {'force': 1}
    assert _e2e_findings("await el.dispatchEvent('click')") == {'dispatch': 1}
    left_at_once = "  await page.getByRole('button', { name: 'Save' }).click()\n  await page.reload()\n"
    assert _e2e_findings(left_at_once) == {'click-then-leave': 1}
    # This repo's, verbatim from the 2026-11-22 audit.
    reload_until = ("  await expect.poll(async () => (await page.reload(), "
                    "(await row.getByRole('cell').nth(3).textContent())?.trim())).toBe('online')\n")
    assert _e2e_findings(reload_until) == {'retry-action': 1}
    publish_until = "  await expect.poll(() => mqttPublish(id, password, { id, status: 'connected' }).ok).toBe(true)\n"
    assert _e2e_findings(publish_until) == {'retry-action': 1}
    nav_race = ("    await nav(page).getByRole('link', { name: 'Login' }).click()\n"
                "    await submitForm(page, '/login', { Username: user.username, Password: PASSWORD }, 'Login')\n")
    assert _e2e_findings(nav_race) == {'click-then-leave': 1}
    post_until = 'for (let i = 0; i < 4 * limit && !statuses.includes(429); i++) {'
    assert _e2e_findings(post_until) == {'until-loop': 1}
    assert _e2e_findings("await mac.evaluate((el) => el.removeAttribute('pattern'))") == {'script-state': 1}
    assert _e2e_findings("await page.evaluate(() => { document.querySelector('#x').value = 'y' })") == {
        'script-state': 1}
    # What is fine: reading in a poll or a script, waiting for what a person sees.
    fine = """
  await expect.poll(() => sql(`SELECT COUNT(*) FROM measurements WHERE device_id = ${q(id)};`)).toBe('1')
  await expect.poll(() => page.evaluate(() => (window as any).Chart?.getChart('chart')?.data.datasets.length)).toBe(1)
  expect(await mac.evaluate((el: HTMLInputElement) => el.validity.patternMismatch)).toBe(true)
  await page.getByRole('button', { name: 'Save' }).click()
  await expect(flash(page)).toHaveText('Saved.')
  await page.reload()
"""
    assert _e2e_findings(fine) == {}
    assert _unit_findings('    time.sleep(1)\n    await asyncio.sleep(0.1)\n') == {'sleep': 2}
