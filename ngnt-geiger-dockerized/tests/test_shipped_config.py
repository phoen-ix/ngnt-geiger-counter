"""What the shipped docker-compose.yml and .env.example open up on the host.

Owner decision of 2026-11-22: the client address for the rate limits comes from a reverse proxy only
when TRUSTED_PROXY_HOPS says so (see the admin guide).
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV = dict(re.findall(r'^(\w+)=(.*)$', (ROOT / '.env.example').read_text(), re.M))
COMPOSE = (ROOT / 'docker-compose.yml').read_text()


def _service(name):
    return COMPOSE.split(f'\n    {name}:\n', 1)[1].split('\n\n', 1)[0]


def _defaults(block):
    """{VAR: its default} of every ${VAR} / ${VAR:-default} in `block`."""
    return dict(re.findall(r'\$\{(\w+)(?::-([^}]*))?\}', block))


def test_the_web_app_trusts_no_proxy_unless_told():
    flask = _service('ngnt-geiger-flask')
    assert 'TRUSTED_PROXY_HOPS: ${TRUSTED_PROXY_HOPS:-0}' in flask
    assert ENV['TRUSTED_PROXY_HOPS'] == '0'
