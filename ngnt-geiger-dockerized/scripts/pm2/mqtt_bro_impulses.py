#!/usr/bin/python3

import asyncio
import json
import os
from datetime import datetime

import aiomqtt
import aiomysql
from pymysql.constants import CLIENT

import mailer  # app/mailer.py: compose mounts app/ at /opt/app, on PYTHONPATH

# ── Config ────────────────────────────────────────────────────────────────────
_net           = os.getenv('IPV4_NETWORK')
DB_HOST        = _net + '.20'
DB_PORT        = 3306
DB_NAME        = os.getenv('MARIADB_DATABASE')
DB_USER        = os.getenv('MARIADB_USER')
DB_PASS        = os.getenv('MARIADB_PASSWORD')

MQTT_HOST      = _net + '.30'
MQTT_PORT      = 1883
MQTT_USER      = os.getenv('MQTT_PYTHON_USER')
MQTT_PASS      = os.getenv('MQTT_PYTHON_USERPW')

SITE_NAME      = os.getenv('SITE_NAME', 'NGNT Geiger Counter')

BATCH_MAX_SIZE    = 50    # flush after this many rows
BATCH_MAX_SECONDS = 5.0   # or after this many seconds, whichever comes first


# ── Reading validation ───────────────────────────────────────────────────────
def reading(msg: dict) -> tuple[str, int, float] | None:
    """(ts, cpm, usvh) as the measurements table takes them, or None. A counter may send
    anything, and one value MariaDB refuses fails the whole batch it is written in."""
    try:
        ts, cpm, usvh = msg['ts'], int(msg['cpm']), float(msg['usvh'])
        datetime.strptime(ts, '%Y-%m-%d %H:%M:%S')  # the firmware's UTC.dateTime("Y-m-d H:i:s")
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    # cpm is an INT, usvh a FLOAT column; NaN and infinity fail the second test too.
    if -2**31 <= cpm < 2**31 and abs(usvh) < 3.4e38:
        return ts, cpm, usvh
    return None


# ── Device status update ─────────────────────────────────────────────────────
async def update_device_status(pool: aiomysql.Pool, device_id: str, status: str,
                               update_last_seen: bool = True) -> bool:
    if update_last_seen:
        sql = ("UPDATE devices SET status = %s, last_seen = NOW() "
               "WHERE device_id = %s")
    else:
        sql = ("UPDATE devices SET status = %s "
               "WHERE device_id = %s")
    try:
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, (status, device_id))
                return cur.rowcount > 0
    except Exception as e:
        print(f"[device] update error for {device_id}: {e}")
        return False


# ── Alert mail ───────────────────────────────────────────────────────────────
async def check_threshold(pool: aiomysql.Pool, device_id: str, ts: str, usvh: float) -> None:
    """Mails the owner, through the admin's SMTP settings, when a reading is above the counter's
    alert threshold (its own on Devices, else the admin's default; above as the dashboard's orange
    card): once, and again only after a reading at or below it. devices.alerted keeps which side
    the last reading was on, so a restart sends no second mail."""
    try:
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT `key`, `value` FROM settings")
                settings = dict(await cur.fetchall())
                await cur.execute(
                    "SELECT d.display_name, d.alert_threshold, u.email FROM devices d "
                    "JOIN users u ON u.id = d.user_id WHERE d.device_id = %s", (device_id,))
                name, threshold, email = await cur.fetchone()
                if threshold is None:
                    threshold = float(settings.get('default_alert_threshold', '0.5'))
                above = usvh > threshold
                # Matches only when the reading is on the other side of the last one.
                await cur.execute("UPDATE devices SET alerted = %s WHERE device_id = %s AND alerted != %s",
                                  (above, device_id, above))
                if not (above and cur.rowcount and email):
                    return
    except Exception as e:
        print(f"[alert] check failed for {device_id}: {e}")
        return
    name = name or device_id
    body = (f"{name} measured {usvh:.4f} µSv/h at {ts} UTC, above its alert threshold of {threshold:g} µSv/h.\n\n"
            f"The next alert comes once its dose rate was back at or below the threshold and rises above it again.")
    if await asyncio.to_thread(mailer.send_mail, email, f"Dose rate alert: {name} — {SITE_NAME}", body, settings):
        print(f"[alert] mailed the owner of {device_id}: {usvh} µSv/h above {threshold:g}")


# ── DB writer ─────────────────────────────────────────────────────────────
async def flush(pool: aiomysql.Pool, batch: list) -> None:
    async with pool.acquire() as conn:
        async with conn.cursor() as cur:
            await cur.executemany(
                "INSERT INTO measurements (device_id, measured_at, cpm, usvh) "
                "VALUES (%s, %s, %s, %s)",
                batch,
            )
    print(f"[db] flushed {len(batch)} row(s)")


async def batch_writer(pool: aiomysql.Pool, queue: asyncio.Queue) -> None:
    batch = []
    db_backoff = 1
    while True:
        try:
            batch.append(await asyncio.wait_for(queue.get(), timeout=BATCH_MAX_SECONDS))
            # drain any further items already waiting in the queue
            while len(batch) < BATCH_MAX_SIZE and not queue.empty():
                batch.append(queue.get_nowait())
            if len(batch) < BATCH_MAX_SIZE:
                continue
        except TimeoutError:
            if not batch:
                continue

        # A full batch, or rows left after BATCH_MAX_SECONDS without a new one. A failed
        # write keeps the batch and is retried after a pause: the writer never ends.
        try:
            await flush(pool, batch)
            batch = []
            db_backoff = 1
        except Exception as e:
            print(f"[db] flush error: {e} — retrying in {db_backoff}s (batch size: {len(batch)})")
            if len(batch) > 10_000:
                dropped = len(batch) - 10_000
                batch = batch[-10_000:]
                print(f"[db] batch cap reached, dropped {dropped} oldest row(s)")
            await asyncio.sleep(db_backoff)
            db_backoff = min(db_backoff * 2, 60)


# ── MQTT listener ─────────────────────────────────────────────────────────────
async def mqtt_listener(pool: aiomysql.Pool, queue: asyncio.Queue) -> None:
    reconnect_interval = 1
    while True:
        try:
            async with aiomqtt.Client(
                hostname=MQTT_HOST,
                port=MQTT_PORT,
                username=MQTT_USER,
                password=MQTT_PASS,
            ) as client:
                await client.subscribe('/+/impulses')
                print(f"[mqtt] connected to {MQTT_HOST}:{MQTT_PORT}, listening on /+/impulses")
                reconnect_interval = 1

                async for message in client.messages:
                    try:
                        msg = json.loads(message.payload)
                    except (ValueError, RecursionError):  # not JSON, or nested too deep
                        print(f"[mqtt] bad payload, skipping: {message.payload!r}")
                        continue

                    if not isinstance(msg, dict):
                        continue

                    # The topic is the one the broker's ACL checked against the
                    # device's own login (/<device_id>/impulses); the payload's
                    # id is whatever the sender wrote.
                    device_id = message.topic.value.split('/')[1]
                    if msg.get('id', device_id) != device_id:
                        print(f"[mqtt] id {msg.get('id')!r} on {message.topic.value}, skipping")
                        continue

                    # Branch 1: measurement (has cpm key)
                    if 'cpm' in msg:
                        row = reading(msg)
                        if row is None:
                            print(f"[mqtt] missing or invalid fields, skipping: {msg}")
                            continue

                        exists = await update_device_status(pool, device_id, 'online', update_last_seen=True)
                        if not exists:
                            print(f"[mqtt] unregistered device {device_id}, skipping measurement")
                            continue

                        # Before queueing: by the time the reading is stored, its mail is out.
                        await check_threshold(pool, device_id, row[0], row[2])
                        await queue.put((device_id, *row))
                        print(f"[mqtt] queued: device={device_id} ts={row[0]} cpm={row[1]} usvh={row[2]}")

                    # Branch 2: connection notice
                    elif msg.get('status') == 'connected':
                        print(f"[mqtt] device connected: {device_id}")
                        await update_device_status(pool, device_id, 'online', update_last_seen=True)

                    # Branch 3: last will (offline)
                    elif msg.get('status') == 'offline':
                        print(f"[mqtt] device offline: {device_id}")
                        await update_device_status(pool, device_id, 'offline', update_last_seen=False)

        except aiomqtt.MqttError as e:
            print(f"[mqtt] connection lost: {e} — reconnecting in {reconnect_interval}s")
            await asyncio.sleep(reconnect_interval)
            reconnect_interval = min(reconnect_interval * 2, 60)


# ── Entry point ───────────────────────────────────────────────────────────────
async def create_pool() -> aiomysql.Pool:
    interval = 1
    while True:
        try:
            pool = await aiomysql.create_pool(
                host=DB_HOST,
                port=DB_PORT,
                user=DB_USER,
                password=DB_PASS,
                db=DB_NAME,
                charset='utf8mb4',
                autocommit=True,
                minsize=2,
                maxsize=10,
                # rowcount = matched rows, not changed ones: an UPDATE that sets
                # the same status and last_seen (two messages in one second)
                # still finds the device.
                client_flag=CLIENT.FOUND_ROWS,
            )
            print(f"[db] pool ready — {DB_HOST}:{DB_PORT}/{DB_NAME} (min=2, max=10)")
            return pool
        except Exception as e:
            print(f"[db] connect failed: {e} — retrying in {interval}s")
            await asyncio.sleep(interval)
            interval = min(interval * 2, 60)


async def main() -> None:
    pool = await create_pool()

    try:
        queue: asyncio.Queue = asyncio.Queue()

        async with asyncio.TaskGroup() as tg:
            tg.create_task(batch_writer(pool, queue))
            tg.create_task(mqtt_listener(pool, queue))
    finally:
        pool.close()
        await pool.wait_closed()
        print("[db] pool closed")


if __name__ == '__main__':
    asyncio.run(main())
