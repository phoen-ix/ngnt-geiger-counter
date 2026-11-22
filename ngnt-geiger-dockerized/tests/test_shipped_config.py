"""What the shipped docker-compose.yml and .env.example open up on the host.

Owner decisions of 2026-11-22: the plain (non-TLS) MQTT listener is published on 127.0.0.1 only, an
old counter without TLS opts back in through .env; the client address for the rate limits comes from
a reverse proxy only when TRUSTED_PROXY_HOPS says so (see the admin guide).
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


def test_the_plain_mqtt_listener_is_published_on_localhost_only():
    ports = _service('ngnt-geiger-mosquitto').split('ports:', 1)[1].split('environment:', 1)[0]
    ports = _defaults(ports)
    assert set(ports) == {'MOSQUITTO_PORTS', 'MOSQUITTO_PORTS_TLS'}
    for spec in (ENV['MOSQUITTO_PORTS'], ports['MOSQUITTO_PORTS']):
        assert re.fullmatch(r'127\.0\.0\.1:\d+:1883', spec), spec
    # Counters connect over TLS, from the network.
    assert ENV['MOSQUITTO_PORTS_TLS'] == '8883:8883'


def test_the_web_app_trusts_no_proxy_unless_told():
    flask = _service('ngnt-geiger-flask')
    assert 'TRUSTED_PROXY_HOPS: ${TRUSTED_PROXY_HOPS:-0}' in flask
    assert ENV['TRUSTED_PROXY_HOPS'] == '0'
