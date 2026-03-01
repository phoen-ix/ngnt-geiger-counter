# Handover — NGNT Geiger Counter

This document is a technical handover for anyone (human or AI assistant) continuing work on this project. It describes the current state of every component, the design decisions made, known issues, and concrete next steps.

Last updated: 2026-03-01 (v3.3 — firmware v2.2, Mosquitto reload fix)

---

## Project summary

A DIY Geiger counter (hardware + firmware) that ships radiation measurements over MQTT to a self-hosted server stack. The server stores measurements in MariaDB and serves a web dashboard with user accounts and per-user device management.

The project lives at: https://github.com/phoen-ix/ngnt-geiger-counter

---

## Current state at a glance

| Area | Status | Notes |
|------|--------|-------|
| Hardware design | Done | Published on Printables, no planned changes |
| Firmware v2.2 (`.ino`) | Done | WiFi, MQTT over TLS, auto-generated pepper, info screen on MQTT failure |
| MQTT broker (Mosquitto) | Done | Auth, ACL, TLS (self-signed, auto-generated), Docker, entrypoint with reload watcher |
| DB schema (`dbinit.sql`) | Done | `users`, `devices`, `password_resets`, `measurements`, `settings` |
| Python subscriber (`mqtt_bro_impulses.py`) | Done | UPDATE-only device status (devices must be pre-registered) |
| Flask dashboard (`app/app.py`) | Done | User accounts, device registration, visibility rules |
| Admin page | Done | Global settings, SMTP config, user management |
| Device provisioning | Done | Web UI → Mosquitto auto-reload (replaces add-device.sh) |
| Password reset | Done | Token-based, email via SMTP |
| Test suite | Done | 98 pytest tests, mocked DB, no external deps |
| MQTT over TLS | Done | Self-signed certs, `setInsecure()` on ESP8266, ports 8883 (TLS) + 1883 (plain) |
| Data export (CSV/JSON) | Not started | |
| Grafana integration | Not started | |

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    Physical device                      │
│                                                         │
│  RadiationD v1.1    Wemos D1 R2 (ESP8266)   LCD 20x4   │
│  Geiger-Muller  ──▶  GPIO 12 interrupt  ──▶  I2C       │
│  tube                                                   │
└───────────────────────┬─────────────────────────────────┘
                        │  WiFi → MQTT over TLS (port 8883)
                        │  topic: /<device_id>/impulses
                        │  payload: JSON (see Data contract)
                        ▼
┌─────────────────────────────────────────────────────────┐
│               Docker stack  (ngnt-geiger-dockerized/)   │
│                                                         │
│  ┌──────────────┐     ┌────────────────┐                │
│  │  Mosquitto   │────▶│  Python        │                │
│  │  172.18.1.30 │     │  subscriber    │                │
│  │  :8883 (TLS) │     │  172.18.1.40   │                │
│  │  :1883 (int) │                                       │
│  └──────────────┘     └───────┬────────┘                │
│         ▲                     │ INSERT                  │
│         │ .reload flag        ▼                         │
│         │              ┌────────────────┐               │
│  ┌──────┴───────┐      │    MariaDB     │               │
│  │  Flask       │      │  172.18.1.20   │               │
│  │  172.18.1.10 │◀─────┘                │               │
│  │  → host:1880 │ SELECT                │               │
│  └──────────────┘                                       │
└─────────────────────────────────────────────────────────┘
```

Flask writes `devices.conf` and a `.reload` flag when provisioning devices. Both Flask and Mosquitto mount the same host directory (`./config/mosquitto`), so the flag is visible to both containers. Mosquitto's entrypoint polls for the flag every 5 seconds and regenerates `passwd.txt` + sends SIGHUP.

---

## Data contract

### MQTT topic
```
/<device_id>/impulses
```
The topic prefix is the device's MQTT username (e.g. `geiger_aabbcc`). The Python subscriber uses the wildcard `/+/impulses`.

### Message types

**Measurement** (every 60 seconds):
```json
{"id":"geiger_aabbcc","ts":"2026-02-24 14:30:00","cpm":42,"usvh":0.2394}
```

**Connection notice** (on successful (re)connect):
```json
{"id":"geiger_aabbcc","status":"connected"}
```

**Last will** (sent by broker if device disconnects ungracefully):
```json
{"id":"geiger_aabbcc","status":"offline"}
```

---

## Database schema

Database name: `ngnt-geigercounter` (configurable via `MARIADB_DATABASE` in `.env`)

### `users` table

```sql
CREATE TABLE users (
  id            INT AUTO_INCREMENT PRIMARY KEY,
  username      VARCHAR(50)  NOT NULL UNIQUE,
  email         VARCHAR(255) DEFAULT NULL,
  password_hash VARCHAR(255) NOT NULL,
  role          ENUM('admin','user') NOT NULL DEFAULT 'user',
  pepper        VARCHAR(64)  DEFAULT NULL,    -- for MQTT credential derivation
  public        BOOLEAN      NOT NULL DEFAULT FALSE,
  pw_version    INT          NOT NULL DEFAULT 0,  -- incremented on password change
  created_at    TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

- `pepper`: shared secret for MAC-based MQTT credential derivation. Set per-user in account settings.
- `public`: if TRUE, anonymous visitors and other users can see this user's devices on the dashboard.
- `pw_version`: incremented on every password change. Stored in the session cookie; `before_request` compares it to the DB value and clears the session on mismatch (invalidates other sessions after password change).
- First admin user is auto-created on startup if table is empty.

### `devices` table

```sql
CREATE TABLE devices (
  id              INT AUTO_INCREMENT PRIMARY KEY,
  user_id         INT          NOT NULL,
  device_id       VARCHAR(50)  NOT NULL UNIQUE,   -- MQTT username (geiger_AABBCC)
  mac_address     VARCHAR(17)  NOT NULL,
  display_name    VARCHAR(100) DEFAULT NULL,
  mqtt_password   VARCHAR(64)  NOT NULL,
  status          ENUM('online','offline') NOT NULL DEFAULT 'offline',
  last_seen       DATETIME     DEFAULT NULL,
  cpm_factor      FLOAT        DEFAULT NULL,
  alert_threshold FLOAT        DEFAULT NULL,
  provisioned     BOOLEAN      NOT NULL DEFAULT FALSE,
  created_at      TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);
```

- `device_id` stays UNIQUE for subscriber compatibility (it uses `WHERE device_id = %s`).
- Devices are registered via the web UI, not auto-created by the subscriber.
- The subscriber only does `UPDATE` (no INSERT) — unregistered devices are ignored.
- `ON DELETE CASCADE` ensures user deletion cleans up devices.

### `password_resets` table

```sql
CREATE TABLE password_resets (
  id         INT AUTO_INCREMENT PRIMARY KEY,
  user_id    INT NOT NULL,
  token      VARCHAR(64) NOT NULL UNIQUE,
  expires_at DATETIME NOT NULL,
  used       BOOLEAN NOT NULL DEFAULT FALSE,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);
```

### `measurements` table

Unchanged from v2.0. Partitioned by `RANGE COLUMNS(measured_at)` with quarterly partitions. No FK to devices — measurements persist even if devices/users are deleted.

### `settings` table

Key-value store for runtime settings. `site_name` is an env var (`SITE_NAME`), not stored here.

| Key | Default | Used by |
|-----|---------|---------|
| `display_timezone` | `Europe/Vienna` | Dashboard timestamp display |
| `offline_timeout_minutes` | `5` | Dashboard offline detection |
| `default_cpm_factor` | `0.0057` | Admin page placeholder |
| `default_alert_threshold` | `0.5` | Dashboard dose rate card |
| `base_url` | *(empty)* | Password reset email links |
| `smtp_host` | *(empty)* | Password reset email |
| `smtp_port` | `587` | Password reset email |
| `smtp_user` | *(empty)* | Password reset email |
| `smtp_password` | *(empty)* | Password reset email |
| `smtp_from` | *(empty)* | Password reset email |
| `smtp_tls` | `1` | Password reset email |
| `registration_enabled` | `1` | Register route (open/disabled) |
| `session_timeout_minutes` | `1440` | Session cookie lifetime (admin-configurable) |

---

## File-by-file notes

### `geiger_counter_v2.2.ino`

Changed in v3.3: auto-generates pepper on first boot; reconnect portal replaced with LCD info screen.
Changed in v3.2: uses `WiFiClientSecure` + `setInsecure()` for MQTT over TLS; default port changed from 2883 to 8883.

Key points:
- **Auto-generated pepper** (v3.3) — On first boot (or after flash erase), if `cfgMqttPepper` is empty, the firmware generates 8 random hex chars using `ESP.random()` (hardware RNG) and saves to flash. The WiFiManager portal's pepper field is pre-filled with this value.
- **Info screen on MQTT failure** (v3.3) — After 12 failed MQTT connection attempts, instead of opening a WiFiManager config portal, the LCD displays the device's MAC address, MQTT user ID, and pepper for 2 minutes, then retries. The user can use these values to register the device in the web UI.
- `WiFiClientSecure::setInsecure()` — encrypts the wire but does not verify the server certificate. Prevents passive eavesdropping; does not prevent active MITM. Acceptable for home WiFi.
- If pepper is set and MQTT User/Password are at defaults, the firmware derives credentials from MAC + pepper using HMAC-SHA256.
- The derived `device_id` format is `geiger_<last 6 hex of MAC>`.
- The server's `helpers.py:derive_mqtt_credentials()` must produce identical results.

### `app/app.py`

Single-file Flask app with all routes. Key components:

**CSRF protection:** All POST forms include a `csrf_token` hidden field. Flask-WTF's `CSRFProtect` validates every POST request automatically.

**Rate limiting:** Flask-Limiter protects auth endpoints — login (10/min), register (5/min), forgot-password (5/min). Uses in-memory storage (per-worker). No default limits on other routes.

**Session security (`before_request`):** On every authenticated request, queries `pw_version` and `role` from the DB. If the user was deleted, the session is cleared. If `pw_version` doesn't match (password was changed elsewhere), the session is cleared. The role is always refreshed from the DB, so admin role changes take effect immediately without requiring re-login. Session timeout is loaded from the `session_timeout_minutes` setting and applied via `app.permanent_session_lifetime`.

**Input validation:** Usernames restricted to `[a-zA-Z0-9_-]` (3-50 chars). User-controlled strings in JS contexts use `|tojson` filter to prevent XSS. Invalid `display_timezone` values fall back to UTC instead of crashing.

**Admin bootstrap:** On startup, checks if `users` table is empty. If so, creates `admin` user with random 20-char password, prints to stdout, and saves to `/app/data/admin_initial_password.txt` (mode 0600). The file is deleted when the admin changes their password.

**Routes:**

| Route | Auth | Purpose |
|-------|------|---------|
| `GET /` | None | Dashboard with visibility rules |
| `GET/POST /login` | None | Login form |
| `GET/POST /register` | None | Registration form |
| `GET /logout` | User | Clear session |
| `GET/POST /forgot-password` | None | Request password reset email |
| `GET/POST /reset-password/<token>` | None | Set new password via token |
| `GET/POST /devices` | User | Device list + add/update/delete |
| `GET/POST /account` | User | Profile + change password |
| `GET/POST /admin` | Admin | Settings, SMTP, user management |

**Dashboard visibility:**
- Anonymous: devices where `users.public = TRUE`
- Logged-in user: own devices + public devices
- Admin: all devices

**Device registration flow:**
1. User must have pepper set (redirected to account if not)
2. Validate MAC format (AA:BB:CC:DD:EE:FF)
3. Derive MQTT credentials from MAC + pepper
4. Check for duplicate device_id
5. INSERT into devices table
6. Write to `devices.conf` + create `.reload` flag
7. Mosquitto reload watcher picks it up within 5 seconds

### `app/helpers.py`

- `get_db()`: returns a PyMySQL connection (per-request, synchronous)
- `get_settings()`: loads all settings from DB
- `login_required`, `admin_required`: decorators checking `session['user_id']` / `session['role']`
- `derive_mqtt_credentials(mac, pepper)`: HMAC-SHA256 credential derivation matching firmware
- `provision_device()` / `unprovision_device()`: manage `devices.conf` with file locking (`fcntl.flock`) and `.reload` flag
- `send_password_reset_email()`: SMTP using settings from DB; `base_url` from DB settings, `site_name` from `SITE_NAME` env var

### `app/templates/`

9 Jinja2 templates extending `base.html`:
- `base.html` — skeleton with nav (conditional on auth state), flash messages
- `login.html`, `register.html`, `forgot_password.html`, `reset_password.html` — auth forms
- `dashboard.html` — range bar, device dropdown, cards, Chart.js chart, measurements table
- `devices.html` — device list + add form
- `account.html` — profile + change password
- `admin.html` — global settings (incl. base URL), SMTP settings, user management table

### `app/static/style.css`

Extracted from inline CSS in the former `index.php`/`admin.php`. Dark theme with consistent variables.

### `scripts/pm2/mqtt_bro_impulses.py`

Changed from v2.0:
- `upsert_device()` renamed to `update_device_status()` — uses plain `UPDATE ... WHERE device_id = %s` instead of `INSERT ... ON DUPLICATE KEY UPDATE`
- Returns `bool` indicating if the device exists (rowcount > 0)
- Measurement branch checks the return value — unregistered devices are skipped with a warning log

### `scripts/mosquitto/docker-entrypoint.sh`

Enhanced from v2.0:
- Removed `MQTT_GEIGER_USER`/`MQTT_GEIGER_USERPW` provisioning (devices now come from `devices.conf` only)
- `rebuild_passwd()` function truncates `passwd.txt` and rebuilds from scratch
- **TLS cert generation** (v3.2): on first start, generates a self-signed CA + server certificate (RSA 2048, 10-year validity) in `/mosquitto/certs/`. Idempotent — skips if `server.crt` already exists. Certs persist via volume mount.
- Starts Mosquitto as a background process (can't use `exec "$@"` because the watcher needs to run)
- Background reload watcher: polls every 5 seconds for `.reload` flag, calls `rebuild_passwd()` + SIGHUP
- Signal forwarding: traps SIGTERM/SIGINT and forwards to Mosquitto PID
- **Volume mount** (v3.3): the entire `./config/mosquitto` directory is mounted at `/mosquitto/config` (previously individual file mounts, which prevented Flask's `.reload` flag from being visible inside the container)

### `Dockerfiles/DockerfileFlask`

Python 3.13-slim + pip install of Flask, Flask-WTF, Flask-Limiter, PyMySQL, Gunicorn. Runs Gunicorn with 2 workers and `--preload` on port 8000. The `--preload` flag ensures the admin bootstrap runs once in the master process instead of once per worker.

### `Dockerfiles/DockerfileMosquitto`

Added in v3.2. Extends `eclipse-mosquitto:2.1.2-alpine` with `openssl` (needed by entrypoint for cert generation).

### `Dockerfiles/DockerfileSubscriber`

Unchanged from v2.0. Uses `aiomqtt` + `aiomysql`.

### `tests/` — pytest test suite

98 tests run on the host without Docker or external dependencies. All DB access is mocked via `MockCursor`/`MockDB` classes in `testutils.py`.

**Key design decisions:**

- **`MockCursor` position advances on `fetchone()`/`fetchall()`, NOT on `execute()`** — this means INSERT/UPDATE/DELETE queries that don't fetch don't consume a response slot. Responses are queued with `add_response(fetchone=...)` or `add_response(fetchall=...)`.
- **Bootstrap isolation** — `app.py` calls `bootstrap_admin_with_retry()` at import time. The session-scoped `app_instance` fixture patches `helpers.get_db` before importing `app` to prevent real DB connections.
- **`before_request` overhead** — every authenticated request triggers 2 DB queries (user lookup + session timeout). The `queue_before_request(cursor)` helper queues these; every test with a logged-in session must call it.
- **`follow_redirects=True` quirk** — when a POST redirects to a GET, `before_request` fires twice (once per request). Tests using `follow_redirects=True` must queue responses for both the POST and the redirected GET.
- **CSRF/rate limiting** — disabled globally via `WTF_CSRF_ENABLED=False` and `RATELIMIT_ENABLED=False`. Dedicated tests in `test_middleware.py` re-enable them to verify they work.

**Files:**

| File | Purpose |
|------|---------|
| `conftest.py` | Fixtures: `app_instance` (session), `mock_cursor` (per-test), `client`, `auth_client`, `admin_client` |
| `testutils.py` | `MockCursor`, `MockDB`, `queue_before_request()`, `queue_settings()` |
| `test_helpers.py` | Unit tests for `helpers.py` functions (credential derivation, provisioning, decorators, email) |
| `test_auth.py` | Login, register, logout, forgot/reset password |
| `test_dashboard.py` | Visibility rules, range/device filtering, empty state |
| `test_devices.py` | Device CRUD, provisioning/unprovisioning |
| `test_account.py` | Profile update, password change validation |
| `test_admin.py` | Settings, SMTP, role/public toggle, user management |
| `test_middleware.py` | `before_request` session handling, CSRF, rate limiting |

---

## Credentials and secrets

| Secret | Location | Notes |
|--------|----------|-------|
| TLS certificates | `volumes/mosquitto/certs/` (gitignored) | Auto-generated on first start |
| Server-side credentials | `.env` (gitignored) | Template in `.env.example` |
| Flask secret key | `.env` (`FLASK_SECRET_KEY`) | Used for session signing |
| Site name | `.env` (`SITE_NAME`) | Page titles, nav bar, email subjects |
| Initial admin password | stdout + `data/admin_initial_password.txt` | Deleted on password change |
| MQTT pepper | Per-user in DB (`users.pepper`) + device portal | Same value on both sides |
| Device MQTT passwords | `config/mosquitto/devices.conf` (gitignored) + DB | Managed by Flask provisioning |
| SMTP password | DB `settings` table | Configured in admin UI |

**Never commit `.env`.** It is in `.gitignore`.

---

## Docker image versions (tested)

| Image | Version |
|-------|---------|
| `mariadb` | 11.4.10 |
| `eclipse-mosquitto` | 2.1.2-alpine |
| `python` (Flask) | 3.13-slim |
| `python` (subscriber) | 3.13-slim |

---

## Known issues / limitations

1. **MQTT TLS uses `setInsecure()`** — The firmware encrypts the MQTT connection but does not verify the server certificate (no protection against active MITM). This is acceptable on a home WiFi network where the primary threat is passive eavesdropping. The plain listener (port 1883) is still available internally for the Python subscriber and backward-compatible devices.

2. **Synchronous DB in Flask** — PyMySQL is synchronous; each request blocks a Gunicorn worker during DB queries. With 2 workers this is fine for small deployments. For higher load, increase workers or switch to an async framework.

3. **Pepper stored in plaintext** — The user's pepper is stored as plaintext in the DB because it needs to be used for credential derivation. The MQTT password (derived from pepper + MAC) is also stored in plaintext in `devices.conf`. This is acceptable because Mosquitto needs the plaintext to generate its own password file.

4. **In-memory rate limiting** — Flask-Limiter uses in-memory storage, so limits are per Gunicorn worker and reset on restart. For stricter enforcement, configure a shared backend (Redis/Memcached) or use a reverse proxy.

---

## Suggested next steps

### 1. CSV/JSON data export
Add a `/export` route that streams measurements as CSV with appropriate `Content-Disposition` header.

### 2. Grafana integration
Add a `grafana` service to `docker-compose.yml` with MariaDB as a data source.

---

## Development tips

**Viewing MQTT traffic live:**
```bash
docker exec -it ngnt-geiger-mosquitto \
  mosquitto_sub -h localhost -p 1883 \
  -u pythonUSR -P <MQTT_PYTHON_USERPW> -t '#' -v
```

**Tailing the Flask logs:**
```bash
docker logs -f ngnt-geiger-flask
```

**Tailing the Python subscriber logs:**
```bash
docker logs -f ngnt-geiger-subscriber
```

**Inserting a test measurement manually:**
First register a device via the web UI, then:
```bash
docker exec -it ngnt-geiger-mosquitto \
  mosquitto_pub -h localhost -p 1883 \
  -u <device_id> -P <mqtt_password> \
  -t /<device_id>/impulses \
  -m '{"id":"<device_id>","ts":"2026-03-01 12:00:00","cpm":15,"usvh":0.0855}'
```

**Connecting to MariaDB directly:**
```bash
docker exec -it ngnt-geiger-mariadb \
  mariadb -u mariadb_usr -p ngnt-geigercounter
```

**Full reset:**
```bash
cd ngnt-geiger-dockerized
docker compose down
rm -rf volumes/mariadb/* volumes/mosquitto/data/* volumes/mosquitto/certs/* data/
docker compose up -d
```
