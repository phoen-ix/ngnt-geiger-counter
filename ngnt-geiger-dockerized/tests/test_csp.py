"""The Content-Security-Policy and what it depends on: pages without inline script."""

import re
from pathlib import Path

TEMPLATES = Path(__file__).parent.parent / 'app' / 'templates'


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


def test_templates_have_nothing_the_policy_would_refuse():
    """An inline <script>, an on*= handler or a script from another origin would just stop
    working in the browser. JSON data blocks are not executed and are fine."""
    offenders = []
    for template in sorted(TEMPLATES.glob('*.html')):
        text = template.read_text(encoding='utf-8')
        for tag in re.findall(r'<script\b[^>]*>', text):
            if 'type="application/json"' in tag:
                continue
            if not re.search(r'''src="\{\{ url_for\('static', filename='[\w.-]+'\) \}\}"''', tag):
                offenders.append(f'{template.name}: {tag}')
        offenders += [f'{template.name}: {m}' for m in re.findall(r'\son[a-z]+\s*=\s*"[^"]*"', text)]
        offenders += [f'{template.name}: {m}' for m in re.findall(r'javascript:[^"\']*', text)]
        offenders += [f'{template.name}: {m}' for m in re.findall(r'<style\b[^>]*>', text)]
    assert offenders == []
