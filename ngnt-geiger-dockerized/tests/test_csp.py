"""The Content-Security-Policy and what it depends on: pages without inline script."""

import re
from html.parser import HTMLParser
from pathlib import Path

TEMPLATES = Path(__file__).parent.parent / 'app' / 'templates'
# The only way a template may load a script: one of the app's own static files.
_STATIC_URL = re.compile(r"\{\{ url_for\('static', filename='[\w.-]+'\) \}\}")


def test_every_response_carries_the_policy(client):
    """A static file needs no database, and takes the same after_request hook as the pages."""
    policy = client.get('/static/style.css').headers['Content-Security-Policy']
    for directive in (
        "default-src 'self'",
        "script-src 'self'",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    ):
        assert directive in policy.split('; ')


def test_scripts_are_never_allowed_inline_or_from_elsewhere(client):
    policy = client.get('/static/style.css').headers['Content-Security-Policy']
    script_src = next(d for d in policy.split('; ') if d.startswith('script-src '))
    assert script_src == "script-src 'self'"


def _without_statements(template: str) -> str:
    """Jinja statements are not markup: inside a tag (`{% if online %} selected{% endif %}`)
    the HTML parser would read their words as attributes."""
    return re.sub(r'\{%.*?%\}', '', template, flags=re.S)


class _TemplateScan(HTMLParser):
    """Collects what the policy refuses, from parsed tags (any case, any spacing) rather than
    from regular expressions over the markup."""

    def __init__(self):
        super().__init__()
        self.offenders = []

    def handle_starttag(self, tag, attrs):
        given = dict(attrs)
        if tag == 'script' and given.get('type') != 'application/json':
            if not _STATIC_URL.fullmatch(given.get('src') or ''):
                self.offenders.append(f"<script src={given.get('src')!r}>")
        if tag == 'style':
            self.offenders.append('<style>')
        for name, value in attrs:
            if name.startswith('on'):
                self.offenders.append(f'<{tag} {name}=...>')
            if value and value.strip().lower().startswith('javascript:'):
                self.offenders.append(f'<{tag} {name}="javascript:...">')


def test_templates_have_nothing_the_policy_would_refuse():
    """An inline <script>, an on*= handler or a script from another origin would just stop
    working in the browser. JSON data blocks are not executed and are fine."""
    offenders = []
    for template in sorted(TEMPLATES.glob('*.html')):
        scan = _TemplateScan()
        scan.feed(_without_statements(template.read_text(encoding='utf-8')))
        scan.close()
        offenders += [f'{template.name}: {found}' for found in scan.offenders]
    assert offenders == []


def test_the_scan_sees_what_it_is_there_for():
    scan = _TemplateScan()
    scan.feed(_without_statements(
        '<SCRIPT>alert(1)</SCRIPT><script src="https://cdn.example/x.js"></script >'
        '<form onsubmit="return go()"><a href="JavaScript:go()">x</a><style>p{}</style>'
        '<script type="application/json" id="d">{}</script>'
        """<script src="{{ url_for('static', filename='app.js') }}" defer></script>"""
        "<option value=\"all\"{% if online == 'all' %} selected{% endif %}>"
    ))
    assert scan.offenders == [
        '<script src=None>',
        "<script src='https://cdn.example/x.js'>",
        '<form onsubmit=...>',
        '<a href="javascript:...">',
        '<style>',
    ]
