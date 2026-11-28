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
    Every device exists, named Cellar, its owner has an email and the admin's default threshold is
    0.5; `threshold` is the device's own, `alerted` its row's flag. The first `fail` batch writes
    raise as a lost connection does."""

    def __init__(self, fail=0, threshold=None, alerted=False):
        self.fail = fail
        self.rows = []
        self.written = asyncio.Event()
        self.threshold, self.alerted = threshold, alerted
        self.rowcount = 1

    async def fetchall(self):
        return [('default_alert_threshold', '0.5'), ('smtp_host', 'smtp.geiger.test')]

    async def fetchone(self):
        return 'Cellar', self.threshold, 'owner@geiger.test'

    @contextlib.asynccontextmanager
    async def acquire(self):
        yield self

    @contextlib.asynccontextmanager
    async def cursor(self):
        yield self

    async def execute(self, sql, args=None):
        # The client's FOUND_ROWS: an UPDATE counts the rows its WHERE matched.
        self.rowcount = 1
        if sql.startswith('UPDATE devices SET alerted'):
            above = args[0]
            self.rowcount = int(self.alerted != above)
            self.alerted = above

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


# ── The alert mail ──────────────────────────────────────────────────────────
# Owner decision 2026-11-28: a dose rate above the counter's threshold mails its owner once, through
# the admin's SMTP settings; the next mail only after a reading at or below it (the dashboard's
# card is orange above, as before).

@pytest.fixture
def mails(sub, monkeypatch):
    """What the subscriber hands to the admin's SMTP server: (to, subject, body)."""
    sent = []
    monkeypatch.setattr(sub.mailer, 'send_mail',
                        lambda to, subject, body, settings: sent.append((to, subject, body)) or True)
    return sent


def readings(sub, pool, *usvh):
    async def run():
        for v in usvh:
            await sub.check_threshold(pool, DEVICE, '2026-11-28 10:00:00', v)
    asyncio.run(run())


def test_a_dose_rate_above_the_threshold_mails_the_owner_once_until_it_was_back_at_or_below(sub, mails):
    readings(sub, FakePool(threshold=0.1), 0.05, 0.2, 0.3, 0.1, 0.25)
    assert [(to, body.split(' at ')[0]) for to, _, body in mails] == [
        ('owner@geiger.test', 'Cellar measured 0.2000 µSv/h'), ('owner@geiger.test', 'Cellar measured 0.2500 µSv/h')]
    assert 'alert threshold of 0.1 µSv/h' in mails[0][2]
    assert 'Cellar' in mails[0][1]


def test_without_a_threshold_of_its_own_the_admins_default_applies(sub, mails):
    readings(sub, FakePool(), 0.4, 0.6)
    assert len(mails) == 1
    assert 'Cellar measured 0.6000 µSv/h' in mails[0][2] and 'alert threshold of 0.5 µSv/h' in mails[0][2]


def test_a_restart_while_above_sends_no_second_mail(sub, mails):
    """Which side of the threshold the last reading was on is the device's row, not the process's memory."""
    readings(sub, FakePool(threshold=0.1, alerted=True), 0.3, 0.4)
    assert mails == []


def test_the_mail_has_gone_out_when_the_reading_is_queued(sub, mails):
    """The owner's inbox holds it by the time the reading can be on the dashboard (e2e U6 reads it then)."""
    row = asyncio.run(first_queued(sub, [json.dumps({**GOOD, 'usvh': 0.6}).encode()]))
    assert row == (DEVICE, '2026-11-22 10:00:00', 12, 0.6)
    assert len(mails) == 1
