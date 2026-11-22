"""Lists kept by hand in two places agree: each test reads both sides.

From the 2026-11-22 audit: the mocked settings, the admin form's labels in its error messages and
the e2e rate-limit spec each copied a list from elsewhere, and nothing failed when one side changed.
"""

import html
import re
from pathlib import Path

from tests.testutils import DEFAULT_SETTINGS

ROOT = Path(__file__).resolve().parents[1]


def test_the_mocked_settings_are_the_ones_a_new_database_gets(app_instance):
    import app as app_module
    sql = (ROOT / 'dbinit.sql').read_text()
    seed = sql[sql.index('INSERT IGNORE INTO `settings`'):]
    seed = dict(re.findall(r"\('(\w+)',\s*'([^']*)'\)", seed[:seed.index(';')]))
    assert seed == DEFAULT_SETTINGS
    # ... and the admin page saves exactly those.
    assert sorted(app_module.SETTINGS_KEYS + app_module.SMTP_KEYS) == sorted(seed)


def test_the_error_messages_name_the_fields_as_the_admin_form_labels_them(app_instance):
    import app as app_module
    page = (ROOT / 'app' / 'templates' / 'admin.html').read_text()
    names = dict(re.findall(r'id="([\w-]+)" name="(\w+)"', page))
    labels = re.findall(r'<label for="([\w-]+)">([^<]+)</label>', page)
    assert {names[for_]: html.unescape(text) for for_, text in labels} == app_module.SETTING_LABELS


def test_the_e2e_rate_limits_are_the_apps():
    app_src = (ROOT / 'app' / 'app.py').read_text()
    limits = dict(re.findall(r"@app\.route\('([^']+)'.*\n@limiter\.limit\(\"(\d+)/minute\"", app_src))
    spec = (ROOT / 'e2e' / 'tests' / 'limits.spec.ts').read_text()
    assert dict(re.findall(r"\['(/[\w-]+)', (\d+),", spec)) == limits
    assert len(limits) == 3


def test_the_coverage_counts_are_the_tables():
    coverage = (ROOT / 'e2e' / 'COVERAGE.md').read_text()
    rows = [line for line in coverage.splitlines() if line.startswith('| ')]
    done, missing = (sum(line.count(mark) for line in rows) for mark in ('✅', '❌'))
    total, said_done, said_missing = map(int, re.search(r'\*\*(\d+) items: (\d+) ✅, (\d+) ❌', coverage).groups())
    assert (total, said_done, said_missing) == (done + missing, done, missing)
