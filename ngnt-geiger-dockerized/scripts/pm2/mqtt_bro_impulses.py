#!/usr/bin/python3

import asyncio
import json
import os

import aiomqtt
import aiomysql

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

BATCH_MAX_SIZE    = 50    # flush after this many rows
BATCH_MAX_SECONDS = 5.0   # or after this many seconds, whichever comes first


# ── DB writer ─────────────────────────────────────────────────────────────────
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
            row = await asyncio.wait_for(queue.get(), timeout=BATCH_MAX_SECONDS)
            batch.append(row)
            queue.task_done()

            # drain any further items already waiting in the queue
            while len(batch) < BATCH_MAX_SIZE:
                try:
                    batch.append(queue.get_nowait())
                    queue.task_done()
                except asyncio.QueueEmpty:
                    break

            if len(batch) >= BATCH_MAX_SIZE:
                await flush(pool, batch)
                batch = []
                db_backoff = 1

        except asyncio.TimeoutError:
            if batch:
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
async def mqtt_listener(queue: asyncio.Queue) -> None:
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
                    except (json.JSONDecodeError, ValueError):
                        print(f"[mqtt] bad payload, skipping: {message.payload!r}")
                        continue

                    if not isinstance(msg, dict) or 'cpm' not in msg:
                        continue

                    device_id = msg.get('id', 'unknown')
                    ts        = msg.get('ts')
                    cpm       = msg.get('cpm')
                    usvh      = msg.get('usvh')

                    if ts is None or cpm is None or usvh is None:
                        print(f"[mqtt] missing fields, skipping: {msg}")
                        continue

                    await queue.put((device_id, ts, int(cpm), float(usvh)))
                    print(f"[mqtt] queued: device={device_id} ts={ts} cpm={cpm} usvh={usvh}")

        except aiomqtt.MqttError as e:
            print(f"[mqtt] connection lost: {e} — reconnecting in {reconnect_interval}s")
            await asyncio.sleep(reconnect_interval)
            reconnect_interval = min(reconnect_interval * 2, 60)


# ── Entry point ───────────────────────────────────────────────────────────────
async def main() -> None:
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
    )
    print(f"[db] pool ready — {DB_HOST}:{DB_PORT}/{DB_NAME} (min=2, max=10)")

    try:
        queue: asyncio.Queue = asyncio.Queue()

        async with asyncio.TaskGroup() as tg:
            tg.create_task(batch_writer(pool, queue))
            tg.create_task(mqtt_listener(queue))
    finally:
        pool.close()
        await pool.wait_closed()
        print("[db] pool closed")


asyncio.run(main())
