# Handover — NGNT Geiger Counter

This document is a technical handover for anyone (human or AI assistant) continuing work on this project. It describes the current state of every component, the design decisions made, known issues, and concrete next steps.

Last updated: 2026-03-01 (LCD 1 Hz throttle, pending MQTT flush on reconnect, reduced heap allocs)

---

## Project summary

A DIY Geiger counter (hardware + firmware) that ships radiation measurements over MQTT to a self-hosted server stack. The server stores measurements in MariaDB and serves a web dashboard.

The project lives at: https://github.com/phoen-ix/ngnt-geiger-counter

---

## Current state at a glance

| Area | Status | Notes |
|------|--------|-------|
| Hardware design | ✅ Done | Published on Printables, no planned changes |
| Firmware v2 (`.ino`) | ✅ Done | WiFi, MQTT, NTP, JSON payload |
| MQTT broker (Mosquitto) | ✅ Done | Auth, ACL, Docker, entrypoint password generation |
| DB schema (`dbinit.sql`) | ✅ Done | `measurements` table, auto-applied on first start |
| Python subscriber (`mqtt_bro_impulses.py`) | ✅ Done | Parses JSON, inserts into MariaDB |
| PHP web dashboard (`app/index.php`) | ✅ Done | Chart.js, configurable time range (1h/6h/24h/7d), table of recent readings |
| `.gitignore` / `.env.example` | ✅ Done | Ready for GitHub |
| MQTT over TLS | ❌ Not started | See future ideas |
| Dashboard: configurable time range | ✅ Done | `?range=` GET param (1h/6h/24h/7d) |
| Dashboard: data export (CSV/JSON) | ❌ Not started | |
| Auto-provisioning (MAC + pepper) | ✅ Done | Firmware derives credentials from MAC; `add-device.sh` registers on server |
| Multiple device support | ❌ Not started | Schema supports it, dashboard does not yet filter |
| Grafana integration | ❌ Not started | |
| Front plate v2 (switches) | ❌ Not started | Hardware only |

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    Physical device                      │
│                                                         │
│  RadiationD v1.1    Wemos D1 R2 (ESP8266)   LCD 20×4   │
│  Geiger-Müller  ──▶  GPIO 12 interrupt  ──▶  I²C       │
│  tube                                                   │
└───────────────────────┬─────────────────────────────────┘
                        │  WiFi → MQTT (port 2883)
                        │  topic: /geiger00/impulses
                        │  payload: JSON (see Data contract)
                        ▼
┌─────────────────────────────────────────────────────────┐
│               Docker stack  (ngnt-geiger-dockerized/)   │
│                                                         │
│  ┌──────────────┐     ┌────────────────┐                │
│  │  Mosquitto   │────▶│  PM2 + Python  │                │
│  │  172.18.1.30 │     │  172.18.1.40   │                │
│  └──────────────┘     └───────┬────────┘                │
│                               │ INSERT                  │
│                               ▼                         │
│                      ┌────────────────┐                 │
│                      │    MariaDB     │                 │
│                      │  172.18.1.20   │                 │
│                      └───────┬────────┘                 │
│                               │ SELECT (PDO)            │
│                               ▼                         │
│                      ┌────────────────┐                 │
│                      │  PHP / Apache  │                 │
│                      │  172.18.1.10   │                 │
│                      │  → host :1880  │                 │
│                      └────────────────┘                 │
└─────────────────────────────────────────────────────────┘
```

---

## Data contract

### MQTT topic
```
/geiger00/impulses
```
The topic prefix is the MQTT username (`mqttUser` in the sketch). The Python subscriber uses the wildcard `/+/impulses` to support multiple devices.

### Message types published to this topic

**Measurement** (every 60 seconds):
```json
{"id":"geiger00","ts":"2026-02-24 14:30:00","cpm":42,"usvh":0.2394}
```

**Connection notice** (on successful (re)connect):
```json
{"id":"geiger00","status":"connected"}
```

**Last will** (sent by broker if device disconnects ungracefully):
```json
{"id":"geiger00","status":"offline"}
```

The Python subscriber distinguishes these by the presence of the `cpm` key — only measurement messages are stored in the database.

### `cpm` vs `usvh`
- `cpm` is the raw count of pulses over the last 60-second window (effectively counts per minute).
- `usvh` = `cpm × cpmConstant` — the conversion constant defaults to `0.0057` for the RadiationD v1.1 / SBM-20 tube. Other tubes use different constants; the value is configurable via the WiFiManager portal.

---

## Database schema

Database name: `ngnt-geigercounter` (configurable via `MARIADB_DATABASE` in `.env`)

```sql
CREATE TABLE measurements (
  id          INT AUTO_INCREMENT PRIMARY KEY,
  device_id   VARCHAR(50)  NOT NULL,   -- MQTT user (e.g. "geiger00")
  measured_at DATETIME     NOT NULL,   -- UTC timestamp from the device
  cpm         INT          NOT NULL,
  usvh        FLOAT        NOT NULL,
  created_at  TIMESTAMP    NOT NULL DEFAULT current_timestamp()
);

-- Index for time-range queries per device
KEY idx_device_measured (device_id, measured_at)
```

The schema is in `dbinit.sql` and is mounted as `/docker-entrypoint-initdb.d/init.sql` in the MariaDB container — it runs automatically on first start when the data directory is empty.

### Partitioning

The `measurements` table is partitioned by `RANGE COLUMNS(measured_at)` with quarterly partitions. Benefits:
- Queries with a time-range `WHERE` clause only scan the relevant partition(s).
- Old partitions can be dropped instantly (`ALTER TABLE measurements DROP PARTITION p2026_q1`) instead of slow row-by-row `DELETE`.

**Partition management is fully automated** — no manual maintenance needed:

- `dbinit.sql` creates the table with only a `p_future` catch-all partition, then defines a stored procedure `ensure_partitions()` and a monthly `EVENT` (`maintain_partitions`) that calls it.
- `ensure_partitions()` reads `information_schema.PARTITIONS` to find the current highest explicit boundary and keeps adding quarterly partitions (via `REORGANIZE PARTITION p_future`) until there are at least **2 years of headroom** from today. It is safe to call at any time.
- The event runs on the 1st of every month. On first startup the procedure is called immediately to bootstrap the initial partitions — works correctly regardless of what year the project is deployed.
- The event scheduler is enabled via `config/mariadb/event-scheduler.cnf` (mounted read-only into the MariaDB container).

To inspect the current partition state:
```sql
SELECT PARTITION_NAME, PARTITION_DESCRIPTION, TABLE_ROWS
FROM information_schema.PARTITIONS
WHERE TABLE_SCHEMA = 'ngnt-geigercounter' AND TABLE_NAME = 'measurements';
```

Note: InnoDB requires the partition key to be part of every unique index, so the primary key is `(id, measured_at)` rather than just `(id)`. `id` remains `AUTO_INCREMENT` and behaviour is unchanged from the application's perspective.

---

## File-by-file notes

### `geiger_counter_v2.0.ino`

- Credentials and server address are **hardcoded constants** near the top (Arduino has no env system). They can be kept in sync with `.env` manually, or auto-provisioned via the MAC + pepper mechanism (see below).
- **Auto-provisioning:** If the "MQTT Pepper" field is set in the WiFiManager portal and the MQTT User/Password are left at their compile-time defaults (`geiger00`/`geiger00PW`), the firmware derives unique credentials from the device's MAC address + pepper using HMAC-SHA256. Derived credentials are never saved to flash — they're computed fresh on every boot.
- `mqttUser` doubles as the MQTT client ID, the username, and the MQTT topic prefix.
- `keepAlive` is set to 1200 s (20 min) — intentionally long because the device only publishes once per minute and we don't want reconnect churn.
- After 12 failed MQTT reconnect attempts, the device opens the WiFiManager config portal (AP mode) so the user can correct settings — it no longer blindly restarts.
- The `events()` call at the bottom of `loop()` is required by the ezTime library for periodic NTP re-sync.
- **Timezone**, **CPM conversion factor**, and **dead time** are configurable via the WiFiManager portal (no re-flash needed). Timezone defaults to `Europe/Vienna`; CPM factor defaults to `0.0057` (SBM-20 tube); dead time defaults to `200` µs (SBM-20, use 50–90 for J305). Invalid CPM values (zero, negative, non-numeric) and invalid dead times (zero, negative, > 10000) are silently ignored and the previous value is kept.
- The ISR (`impulse()`) increments the counter with a configurable dead-time debounce (default 200 µs). This filters signal bounce/ringing on the GPIO pin without losing real pulses.
- `impulseCounter` is snapshot with `noInterrupts()`/`interrupts()` before use, avoiding race conditions between the ISR and the main loop (consistent CPM/uSv/h values and no lost pulses on reset).
- **Clock-aligned measurements:** After NTP sync, the firmware waits for the next :00 second boundary before starting its first measurement window. This ensures every published reading covers an exact wall-clock minute. Timing uses `Geiger.minute()` (NTP-derived) instead of `millis()`, so there is no drift. The LCD shows a live countdown during the wait ("Waiting for :00") and during normal operation ("Next reading").
- **Staggered MQTT publish:** The MQTT message is built at the :00 boundary (capturing the correct timestamp), but the actual publish is delayed by a random 1–57 s offset chosen fresh each cycle. This spreads broker load when multiple devices report simultaneously. The LCD updates immediately with the new reading; only the network send is deferred. If the MQTT connection drops while a publish is pending, the reading is flushed immediately after `reconnect()` succeeds — no data loss on transient disconnections.
- The MQTT JSON message is built with `snprintf` into a 192-byte stack buffer (`pendingMqttBuf`) instead of `String` concatenation, avoiding heap fragmentation on the ESP8266. The buffer is sized to accommodate long MQTT usernames (up to 31 chars) plus JSON overhead.
- **LCD 1 Hz throttle:** All LCD row writes are gated behind a `millis()` check (`lastLcdUpdate`) so the display refreshes once per second instead of every loop iteration. This eliminates thousands of redundant I²C transactions per second. The minute-transition block (counter snapshot, MQTT message build, `lcd.clear()` on state change) remains outside the gate for immediate execution. After an `lcd.clear()`, `lastLcdUpdate` is reset to 0 to force an immediate repaint.
- **Reduced String allocations:** The two `const String` date/time format variables (`dateOrder`, `timestampOrder`) were replaced with a single `const char* lcdDateTimeFmt` and a single `dateTime()` call per LCD update. Combined with the 1 Hz throttle, this reduces heap String allocations from thousands/sec to 1/sec.
- The device is publish-only — there is no MQTT subscribe or callback.
- **LCD states:** The 20×4 display shows contextual information for every phase: "Initializing..." during boot, "Syncing time..." during NTP sync, "Waiting for :00" with a countdown after NTP until the first wall-clock minute boundary, "Next reading" countdown during the first measurement cycle, then live date/time and CPM/uSv/h values during normal operation (the countdown is hidden once readings are available). Connection errors show the error code, server address, and retry attempt count. After 12 failed MQTT attempts, the AP portal screen is shown.

### `ngnt-geiger-dockerized/scripts/pm2/mqtt_bro_impulses.py`

- Runs inside the `ngnt-geiger-subscriber` container. Docker's `restart: unless-stopped` handles auto-restart on crash — no process manager needed.
- All config comes from environment variables passed by docker-compose (`MARIADB_*`, `MQTT_*`, `IPV4_NETWORK`).
- **Async:** built on `asyncio` with two concurrent coroutines managed by `asyncio.TaskGroup`:
  - `mqtt_listener` — subscribes to `/+/impulses`, validates each message, pushes a `(device_id, ts, cpm, usvh)` tuple onto an `asyncio.Queue`.
  - `batch_writer` — drains the queue and flushes to MariaDB using `executemany()`. Flushes when `BATCH_MAX_SIZE` (50 rows) is reached or after `BATCH_MAX_SECONDS` (5 s), whichever comes first.
- **Connection pool:** `aiomysql.create_pool(minsize=2, maxsize=10)` — created once at startup, shared across all flushes. No per-message connection overhead.
- **MQTT reconnection:** exponential backoff on `aiomqtt.MqttError` (1 s → 2 s → … → 60 s cap). Resubscribes automatically after reconnect.
- **DB error handling:** exponential backoff on flush errors (1 s → 2 s → … → 60 s cap), matching the MQTT reconnect pattern. Batch is preserved across retries but capped at 10,000 rows to prevent unbounded memory growth.
- **Cleanup:** The database connection pool is closed cleanly on shutdown via `try`/`finally`.
- Silently skips messages without a `cpm` field (connection/will messages).

### `ngnt-geiger-dockerized/Dockerfiles/DockerfilePhpApache`

- Only the `pdo_mysql` extension is installed — it is the only one used by `index.php`. The `mysqli`, `calendar`, and `sockets` extensions were previously installed but are unused and have been removed to keep the image slim.
- `mod_headers` is enabled and the Apache security config sets `X-Content-Type-Options: nosniff` and `X-Frame-Options: sameorigin`.
- Uses [mlocati/docker-php-extension-installer](https://github.com/mlocati/docker-php-extension-installer) to install the extension cleanly without manual dependency management.

### `ngnt-geiger-dockerized/Dockerfiles/DockerfileSubscriber`

- Uses `aiomqtt` (async MQTT client) and `aiomysql` (pure-Python async MySQL/MariaDB driver) — both are pure Python, so no C compiler or `libmariadb` system libraries are needed. The image is a plain `pip install` on top of `python:slim`.
- Runs as a non-root `subscriber` user inside the container.
- Previously used `paho-mqtt` + the `mariadb` C extension (requiring `gcc` and `libmariadb-dev` at build time).

### `ngnt-geiger-dockerized/app/index.php`

- Single-file PHP dashboard — no framework, no build step.
- DB credentials come from environment variables set in `docker-compose.yml` for the php_apache service.
- The page has a `<meta http-equiv="refresh" content="60">` for auto-reload (preserves the selected time range).
- **Time range selector:** pill buttons at the top let the user choose 1h / 6h / 24h (default) / 7d. Selection is passed as `?range=` GET parameter. Invalid values fall back to `24h`. Only hardcoded interval literals from a whitelist reach SQL — no user input is interpolated.
- Chart.js 4.4.0 loaded from jsDelivr CDN. If deploying offline, download and serve locally.
- Chart data is embedded as JSON directly in the HTML (PHP → `json_encode`). No separate API endpoint.
- The dose rate card turns orange when uSv/h > 0.5 (roughly 5× typical background).
- Database errors are logged to stderr (`error_log()`) with full details; users see only a generic "Could not connect to the database." message.

### `ngnt-geiger-dockerized/add-device.sh`

- Server-side helper script for MAC-based auto-provisioning.
- Takes a MAC address (e.g. `AA:BB:CC:DD:EE:FF`), reads `MQTT_PEPPER` from `.env`, and computes the same username/password that the firmware derives.
- Persists credentials to `config/mosquitto/devices.conf` (survives container restarts) and adds them to the running Mosquitto via `docker exec mosquitto_passwd`.
- `devices.conf` is bind-mounted into the container and re-read by `docker-entrypoint.sh` on every start.

### `ngnt-geiger-dockerized/scripts/mosquitto/docker-entrypoint.sh`

- Mounted at `/docker-entrypoint.sh` inside the container, overriding the official eclipse-mosquitto entrypoint.
- Runs `mosquitto_passwd -b` to (re)generate `config/mosquitto/passwd.txt` from the `MQTT_*` env vars on every container start.
- This means you can change MQTT passwords by editing `.env` and restarting the container — no manual `mosquitto_passwd` commands needed.
- Also re-provisions any auto-provisioned devices listed in `config/mosquitto/devices.conf`.

### `ngnt-geiger-dockerized/config/mosquitto/passwd.txt`

- Committed to git (bcrypt hashes of the **default** passwords from `.env.example`).
- Overwritten on every container start by the entrypoint script.
- Required as a pre-existing file for the Docker bind-mount to work correctly; without it Docker would create a directory at that path.

### `ngnt-geiger-dockerized/volumes/`

- Contains only **runtime data** — MariaDB files and Mosquitto persistence.
- Safe to delete for a reset: `rm -rf volumes/mariadb/* volumes/mosquitto/data/*`
  - The `*` glob in bash does not match dotfiles, so `.gitkeep` files survive.
- The PHP app source is in `app/` (not `volumes/`) precisely so it is not wiped on reset.

---

## Credentials and secrets

| Secret | Location | Notes |
|--------|----------|-------|
| All server-side credentials | `.env` (gitignored) | Template in `.env.example` |
| MQTT server address + credentials | `geiger_counter_v2.0.ino` lines 56–59 | Manual sync, or use auto-provisioning |
| MQTT pepper (auto-provisioning) | `.env` (`MQTT_PEPPER`) + device portal | Same value on both sides |
| Auto-provisioned device passwords | `config/mosquitto/devices.conf` (gitignored) | Plaintext, generated by `add-device.sh` |
| WiFi AP password | `geiger_counter_v2.0.ino` line 51 | Only used during initial WiFi setup |

**Never commit `.env`.** It is in `.gitignore`. Only `.env.example` is tracked.

---

## Docker image versions (tested)

| Image | Version |
|-------|---------|
| `mariadb` | 11.4.10 |
| `eclipse-mosquitto` | 2.1.2-alpine |
| `php` (Apache) | 8.4.18 |
| `python` (subscriber) | 3.13.12-slim |

---

## Known issues / limitations

1. **No TLS on MQTT** — The broker listens on plain TCP port 1883 (exposed as 2883). Credentials are sent in the clear over WiFi. Acceptable on a trusted home network; not suitable for public internet exposure without adding TLS.

2. **MariaDB major version upgrade (10.11 → 11.4)** — If upgrading an existing deployment with data in `volumes/mariadb/`, take a `mysqldump` backup before restarting. The official MariaDB Docker image runs `mariadb-upgrade` automatically on first start, but a backup is strongly recommended. Fresh installs are unaffected.


---

## Suggested next steps (in rough priority order)

### 1. Dashboard: CSV export
Add a simple `export.php` that runs `SELECT * FROM measurements ORDER BY measured_at DESC` and outputs `Content-Type: text/csv`.

### 2. MQTT over TLS
Generate a self-signed cert (or use Let's Encrypt). Add a second listener block to `mosquitto.conf` on port 8883 with `cafile`, `certfile`, `keyfile`. Update the sketch to use WiFiClientSecure and load the CA cert.

### 3. Multi-device dashboard
The schema already stores `device_id`. Add a device selector dropdown to `index.php` and filter queries with `WHERE device_id = ?`.

### 4. Grafana integration
MariaDB can be used directly as a Grafana data source. Add a `grafana` service to `docker-compose.yml`, mount a provisioning config pointing at MariaDB, and provision a dashboard JSON.

---

## Development tips

**Viewing MQTT traffic live:**
```bash
docker exec -it ngnt-geiger-mosquitto \
  mosquitto_sub -h localhost -p 1883 \
  -u pythonUSR -P <MQTT_PYTHON_USERPW> -t '#' -v
```

**Tailing the Python subscriber logs:**
```bash
docker logs -f ngnt-geiger-subscriber
```

**Inserting a test measurement manually (no hardware needed):**
```bash
docker exec -it ngnt-geiger-mosquitto \
  mosquitto_pub -h localhost -p 1883 \
  -u geiger00 -P <MQTT_GEIGER_USERPW> \
  -t /geiger00/impulses \
  -m '{"id":"geiger00","ts":"2026-02-24 12:00:00","cpm":15,"usvh":0.0855}'
```

**Connecting to MariaDB directly:**
```bash
docker exec -it ngnt-geiger-mariadb \
  mariadb -u mariadb_usr -p ngnt-geigercounter
```

**Updating the GitHub release after changes:**

The `v2.0` tag and release body must both be updated manually after pushing changes:

```bash
# 1. Move the tag to the latest commit
git tag -f v2.0 HEAD
git push --force origin v2.0

# 2. Find the release ID (the /releases/tags/ endpoint breaks after a force-pushed tag)
TOKEN=$(git remote get-url origin | sed 's|https://[^:]*:\(.*\)@github.com.*|\1|')
RELEASE_ID=$(curl -s -H "Authorization: token $TOKEN" \
  "https://api.github.com/repos/phoen-ix/ngnt-geiger-counter/releases" \
  | python3 -c "import sys,json; print(next(r['id'] for r in json.load(sys.stdin) if r['tag_name']=='v2.0'))")

# 3. Update the release body using the numeric release ID
curl -s -X PATCH \
  -H "Authorization: token $TOKEN" \
  -H "Content-Type: application/json" \
  "https://api.github.com/repos/phoen-ix/ngnt-geiger-counter/releases/$RELEASE_ID" \
  -d '{"tag_name": "v2.0", "body": "…updated release notes…"}'
```

**Important:** After `git push --force` moves a tag, the GitHub API endpoint `/releases/tags/v2.0` returns 404 until the release object is re-associated with the new tag. Use the `/releases` list endpoint to find the numeric release ID, then PATCH by ID. The `tag_name` field in the PATCH body re-links the release to the moved tag.

Both steps are needed — the API call updates the text but does **not** move the tag. Without `git tag -f` + `git push --force origin v2.0`, the release page keeps pointing at the old commit and shows a stale timestamp.

**Full reset:**
```bash
cd ngnt-geiger-dockerized
docker compose down
rm -rf volumes/mariadb/* volumes/mosquitto/data/*
docker compose up -d
```
