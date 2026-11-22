"""The MQTT subscriber (scripts/pm2/mqtt_bro_impulses.py) keeps running whatever a counter sends.

Any account can register a counter, and a counter may publish anything on its own topic. Until
2026-11-22 one reading with a wrong-typed field (`"cpm": "x"`) raised in the listener and ended
the subscriber, with every reading not yet written; a value MariaDB refuses (NaN, a cpm past
INT) failed the whole batch on every retry; and a write that failed after a quiet 5 s (MariaDB
restarting) escaped the retry and ended the subscriber too.

The broker and the database are fakes shaped like the aiomqtt and aiomysql calls the script makes.
"""

import asyncio
import contextlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'pm2' / 'mqtt_bro_impulses.py'
DEVICE = 'geiger_aabbcc'
GOOD = {'id': DEVICE, 'ts': '2026-11-22 10:00:00', 'cpm': 12, 'usvh': 0.0684}


@pytest.fixture
def sub(monkeypatch):
    monkeypatch.setenv('IPV4_NETWORK', '10.0.0')
    spec = importlib.util.spec_from_file_location('mqtt_bro_impulses', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakePool:
    """aiomysql's pool as the script uses it: acquire() -> connection, cursor() -> cursor.
    Every device exists; the first `fail` batch writes raise as a lost connection does."""

    rowcount = 1

    def __init__(self, fail=0):
        self.fail = fail
        self.rows = []
        self.written = asyncio.Event()

    @contextlib.asynccontextmanager
    async def acquire(self):
        yield self

    @contextlib.asynccontextmanager
    async def cursor(self):
        yield self

    async def execute(self, sql, args):
        pass

    async def executemany(self, sql, rows):
        if self.fail:
            self.fail -= 1
            raise ConnectionError('Lost connection to MySQL server during query')
        self.rows += rows
        self.written.set()


def broker(payloads):
    """An aiomqtt.Client that delivers `payloads` on the device's topic, then stays connected."""

    class Client:
        def __init__(self, **_):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def subscribe(self, topic):
            pass

        @property
        def messages(self):
            return self._messages()

        async def _messages(self):
            for payload in payloads:
                yield SimpleNamespace(payload=payload, topic=SimpleNamespace(value=f'/{DEVICE}/impulses'))
            await asyncio.Event().wait()

    return Client


async def first_queued(sub, payloads):
    """Runs the listener over `payloads`; returns the first queued row and the listener task."""
    queue = asyncio.Queue()
    sub.aiomqtt.Client = broker(payloads)
    task = asyncio.create_task(sub.mqtt_listener(FakePool(), queue))
    got = asyncio.create_task(queue.get())
    await asyncio.wait({task, got}, timeout=10, return_when=asyncio.FIRST_COMPLETED)
    if task.done():
        task.result()  # raises what ended the listener
    assert got.done(), 'the good reading was never queued'
    assert queue.empty()
    task.cancel()
    return got.result()


BAD_READINGS = {
    'cpm text': {**GOOD, 'cpm': 'x'},
    'usvh text': {**GOOD, 'usvh': 'x'},
    'cpm list': {**GOOD, 'cpm': [1]},
    'cpm null': {**GOOD, 'cpm': None},
    'ts number': {**GOOD, 'ts': 12345},
    'ts text': {**GOOD, 'ts': 'yesterday'},
    'cpm past INT': {**GOOD, 'cpm': 2**31},
    'cpm infinite': {**GOOD, 'cpm': float('inf')},
    'usvh NaN': {**GOOD, 'usvh': float('nan')},
    'usvh infinite': {**GOOD, 'usvh': float('-inf')},
    'usvh past FLOAT': {**GOOD, 'usvh': 1e39},
}


@pytest.mark.parametrize('bad', BAD_READINGS.values(), ids=BAD_READINGS.keys())
def test_a_reading_the_table_cannot_take_is_skipped_and_the_next_one_queued(sub, bad):
    # json.dumps writes NaN and Infinity as the bare words json.loads reads back.
    row = asyncio.run(first_queued(sub, [json.dumps(bad).encode(), json.dumps(GOOD).encode()]))
    assert row == (DEVICE, '2026-11-22 10:00:00', 12, 0.0684)


def test_a_payload_nested_too_deep_to_parse_is_skipped(sub):
    row = asyncio.run(first_queued(sub, [b'[' * 100_000, json.dumps(GOOD).encode()]))
    assert row == (DEVICE, '2026-11-22 10:00:00', 12, 0.0684)


def test_a_write_that_fails_after_a_quiet_spell_is_retried(sub, monkeypatch):
    monkeypatch.setattr(sub, 'BATCH_MAX_SECONDS', 0.05)
    row = (DEVICE, '2026-11-22 10:00:00', 12, 0.0684)

    async def run():
        pool = FakePool(fail=1)
        queue = asyncio.Queue()
        queue.put_nowait(row)
        task = asyncio.create_task(sub.batch_writer(pool, queue))
        written = asyncio.create_task(pool.written.wait())
        await asyncio.wait({task, written}, timeout=10, return_when=asyncio.FIRST_COMPLETED)
        if task.done():
            task.result()  # raises what ended the writer
        task.cancel()
        return pool.rows

    assert asyncio.run(run()) == [row]
