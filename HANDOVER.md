# Handover — NGNT Geiger Counter

This document is a technical handover for anyone (human or AI assistant) continuing work on this project. It describes the current state of every component, the design decisions made, known issues, and concrete next steps.

Last updated: 2026-03-01 (v3.0 — user accounts, Flask, web-based device provisioning)

---

## Project summary

A DIY Geiger counter (hardware + firmware) that ships radiation measurements over MQTT to a self-hosted server stack. The server stores measurements in MariaDB and serves a web dashboard with user accounts and per-user device management.

The project lives at: https://github.com/phoen-ix/ngnt-geiger-counter

---

## Current state at a glance

| Area | Status | Notes |
|------|--------|-------|
| Hardware design | Done | Published on Printables, no planned changes |
| Firmware v2 (`.ino`) | Done | WiFi, MQTT, NTP, JSON payload — unchanged in v3 |
| MQTT broker (Mosquitto) | Done | Auth, ACL, Docker, entrypoint with background reload watcher |
| DB schema (`dbinit.sql`) | Done | `users`, `devices`, `password_resets`, `measurements`, `settings` |
| Python subscriber (`mqtt_bro_impulses.py`) | Done | UPDATE-only device status (devices must be pre-registered) |
| Flask dashboard (`app/app.py`) | Done | User accounts, device registration, visibility rules |
| Admin page | Done | Global settings, SMTP config, user management |
| Device provisioning | Done | Web UI → Mosquitto auto-reload (replaces add-device.sh) |
| Password reset | Done | Token-based, email via SMTP |
| MQTT over TLS | Not started | See future ideas |
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
                        │  WiFi → MQTT (port 2883)
                        │  topic: /<device_id>/impulses
                        │  payload: JSON (see Data contract)
                        ▼
┌─────────────────────────────────────────────────────────┐
│               Docker stack  (ngnt-geiger-dockerized/)   │
│                                                         │
│  ┌──────────────┐     ┌────────────────┐                │
│  │  Mosquitto   │────▶│  Python        │                │
│  │  172.18.1.30 │     │  subscriber    │                │
│  │              │     │  172.18.1.40   │                │
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

Flask writes `devices.conf` and a `.reload` flag when provisioning devices. Mosquitto's entrypoint polls for the flag every 5 seconds and regenerates `passwd.txt` + sends SIGHUP.

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
  created_at    TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

- `pepper`: shared secret for MAC-based MQTT credential derivation. Set per-user in account settings.
- `public`: if TRUE, anonymous visitors and other users can see this user's devices on the dashboard.
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

Key-value store. v3 adds SMTP fields and `site_name`:

| Key | Default | Used by |
|-----|---------|---------|
| `display_timezone` | `Europe/Vienna` | Dashboard timestamp display |
| `offline_timeout_minutes` | `5` | Dashboard offline detection |
| `default_cpm_factor` | `0.0057` | Admin page placeholder |
| `default_alert_threshold` | `0.5` | Dashboard dose rate card |
| `smtp_host` | *(empty)* | Password reset email |
| `smtp_port` | `587` | Password reset email |
| `smtp_user` | *(empty)* | Password reset email |
| `smtp_password` | *(empty)* | Password reset email |
| `smtp_from` | *(empty)* | Password reset email |
| `smtp_tls` | `1` | Password reset email |
| `site_name` | `NGNT Geiger Counter` | Page titles, nav |

---

## File-by-file notes

### `geiger_counter_v2.0.ino`

Unchanged in v3.0.

Key points for v3 integration:
- If pepper is set and MQTT User/Password are at defaults, the firmware derives credentials from MAC + pepper using HMAC-SHA256.
- The derived `device_id` format is `geiger_<last 6 hex of MAC>`.
- The server's `helpers.py:derive_mqtt_credentials()` must produce identical results.

### `app/app.py`

Single-file Flask app with all routes. Key components:

**Admin bootstrap:** On startup, checks if `users` table is empty. If so, creates `admin` user with random 20-char password, prints to stdout, and saves to `/app/data/admin_initial_password.txt`. The file is deleted when the admin changes their password.

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
- `send_password_reset_email()`: SMTP using settings from DB

### `app/templates/`

9 Jinja2 templates extending `base.html`:
- `base.html` — skeleton with nav (conditional on auth state), flash messages
- `login.html`, `register.html`, `forgot_password.html`, `reset_password.html` — auth forms
- `dashboard.html` — range bar, device dropdown, cards, Chart.js chart, measurements table
- `devices.html` — device list + add form
- `account.html` — profile + change password
- `admin.html` — global settings, SMTP settings, user management table

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
- Starts Mosquitto as a background process (can't use `exec "$@"` because the watcher needs to run)
- Background reload watcher: polls every 5 seconds for `.reload` flag, calls `rebuild_passwd()` + SIGHUP
- Signal forwarding: traps SIGTERM/SIGINT and forwards to Mosquitto PID

### `Dockerfiles/DockerfileFlask`

Python 3.13-slim + pip install of Flask, PyMySQL, Gunicorn. Runs Gunicorn with 2 workers and `--preload` on port 8000. The `--preload` flag ensures the admin bootstrap runs once in the master process instead of once per worker.

### `Dockerfiles/DockerfileSubscriber`

Unchanged from v2.0. Uses `aiomqtt` + `aiomysql`.

---

## Credentials and secrets

| Secret | Location | Notes |
|--------|----------|-------|
| Server-side credentials | `.env` (gitignored) | Template in `.env.example` |
| Flask secret key | `.env` (`FLASK_SECRET_KEY`) | Used for session signing |
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

1. **No TLS on MQTT** — The broker listens on plain TCP. Credentials are sent in the clear over WiFi. Acceptable on a trusted home network.

2. **No rate limiting** — Login, registration, and password reset endpoints have no rate limiting. Add a reverse proxy (nginx, Caddy) with rate limiting for public-facing deployments.

3. **Synchronous DB in Flask** — PyMySQL is synchronous; each request blocks a Gunicorn worker during DB queries. With 2 workers this is fine for small deployments. For higher load, increase workers or switch to an async framework.

4. **Pepper stored in plaintext** — The user's pepper is stored as plaintext in the DB because it needs to be used for credential derivation. The MQTT password (derived from pepper + MAC) is also stored in plaintext in `devices.conf`. This is acceptable because Mosquitto needs the plaintext to generate its own password file.

---

## Suggested next steps

### 1. CSV/JSON data export
Add a `/export` route that streams measurements as CSV with appropriate `Content-Disposition` header.

### 2. MQTT over TLS
Add a second Mosquitto listener on port 8883 with TLS certificates. Update the firmware to use `WiFiClientSecure`.

### 3. Grafana integration
Add a `grafana` service to `docker-compose.yml` with MariaDB as a data source.

### 4. Rate limiting
Add Flask-Limiter or deploy behind a reverse proxy with rate limiting for public deployments.

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
rm -rf volumes/mariadb/* volumes/mosquitto/data/* data/
docker compose up -d
```
