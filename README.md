# not great, not terrible — geiger counter

A DIY radiation monitor built around a RadiationD v1.1 Cajoe sensor, a Wemos D1 R2 (ESP8266), and a 20x4 I2C LCD — housed in a 3D-printed case. It publishes counts per minute over MQTT (TLS) to a self-hosted Docker stack: Mosquitto, a Python subscriber, MariaDB and a Flask web dashboard with user accounts and web-based device provisioning.

<img src="docs/screenshots/dashboard.png" alt="Web dashboard: CPM, dose rate and online status of the balcony counter, a 24-hour chart and the latest readings" width="800">

**Status: v3.4.** The hardware design, firmware v2.2 (MQTT over TLS, auto-generated pepper, info screen on MQTT failure) and the dockerized backend (dashboard, per-user visibility, device provisioning, admin panel with SMTP password reset, CSRF protection, rate limiting) are done; MariaDB 12.3 LTS, Python 3.15, CI runs pytest, pip-audit and the screenshot tour. Open ideas are at the end.

![assembled device](https://user-images.githubusercontent.com/100175489/219118323-df211fda-93e7-4437-bd8e-3e14d5e2e7f8.jpg)

---

## Repository layout

```
ngnt-geiger-counter/
├── geiger_counter_v2.2.ino          # ESP8266 firmware (Wemos D1 R2)
├── HANDOVER.md                      # Technical handover: design, schema, decisions
├── docs/screenshots/                # Web UI screenshots + gallery (generated)
└── ngnt-geiger-dockerized/
    ├── app/                         # Flask web application
    │   ├── app.py                   # Routes, admin bootstrap
    │   ├── helpers.py               # Credential derivation, provisioning, auth
    │   ├── requirements.txt         # Production deps (pinned)
    │   ├── requirements-dev.txt     # Adds pytest for local testing
    │   ├── static/style.css
    │   └── templates/               # Jinja2 templates
    ├── tests/                       # pytest suite (mocked DB)
    │   ├── conftest.py              # Fixtures (app, client, auth, admin)
    │   ├── testutils.py             # MockCursor, MockDB, queue helpers
    │   └── test_*.py
    ├── e2e/                         # Playwright docs tour (own Node package)
    ├── pytest.ini
    ├── dbinit.sql                   # DB schema — auto-applied on first start
    ├── config/                      # Static config for Mosquitto & MariaDB
    ├── docker-compose.yml
    ├── docker-compose.e2e.yml       # Overlay for throwaway stacks (docs tour)
    ├── Dockerfiles/
    ├── scripts/
    │   ├── docs-screenshots.sh      # Regenerates docs/screenshots/
    │   ├── mosquitto/               # Container entrypoint (passwords + reload watcher)
    │   └── pm2/                     # Python MQTT→DB subscriber
    ├── data/                        # Runtime data (admin initial password)
    ├── volumes/                     # Runtime DB data — safe to wipe for a reset
    ├── .env.example                 # Copy to .env and fill in before first run
    └── log/mosquitto/
```

---

## Hardware

### Parts list

| Qty | Part |
|-----|------|
| 1 | RadiationD v1.1 Cajoe (search "diy geiger" on auction sites) |
| 1 | Wemos D1 R2 (ESP8266) |
| 1 | LCD2004 with I2C interface (buy one with the I2C backpack already soldered on) |
| 1 | 3D-printed case — [Printables model 399474](https://www.printables.com/model/399474-ngnt-geiger-counter-not-great-not-terrible) |
| 7 | M-F Dupont cables |
| 8 | M3x8 mm screws + nuts |
| 4 | M3x12 mm screws + nuts |
| 4 | M3 spacers >=35 mm + matching screws + nuts |

### Assembly

1. Print the front and back plates.
2. Mount the Wemos to the back plate (4x M3x8 mm + nuts).
3. Mount the LCD to the front plate (4x M3x12 mm + nuts).
4. Remove the 4 acrylic-retaining screws from the RadiationD; mount the board to the front plate (4x M3x8 mm).
5. Connect Dupont cables per the pinout below.
6. Use M3 spacers, screws, and nuts to join front and back plate.
7. Flash the firmware (see below).

### Wiring

**LCD -> Wemos D1 R2**

| LCD | Wemos |
|-----|-------|
| GND | GND |
| VCC | 3.3 V |
| SDA | D2 (SDA) |
| SCL | D1 (SCL) |

**RadiationD v1.1 -> Wemos D1 R2**

| RadiationD | Wemos |
|------------|-------|
| GND | GND |
| 5 V | 5 V |
| VIN | D6 (GPIO 12) |

---

## Firmware — `geiger_counter_v2.2.ino`

### Required Arduino libraries

Install these via the Arduino IDE Library Manager:

| Library | Version tested |
|---------|---------------|
| [LiquidCrystal_I2C](https://github.com/johnrickman/LiquidCrystal_I2C) | any |
| [WiFiManager](https://github.com/tzapu/WiFiManager) | 2.0.17 |
| [ezTime](https://github.com/ropg/ezTime) | 0.8.3 |
| [PubSubClient](https://pubsubclient.knolleary.net/) | 2.8.0 |
| [ArduinoJson](https://arduinojson.org/) | 7.4.3 (6.21.6 also builds) |

Board support: **ESP8266 Arduino core >= 3.0.0** (`esp8266` board package in the IDE); built against 3.1.2 for the Wemos D1 R2 & mini.

### Configuration

#### Compile-time only (edit before flashing)

```cpp
const String deviceHostname = "GeigerCounter"; // WiFi AP name & mDNS hostname
const String wifiApPass     = "wifiApPass";    // password for the setup AP
const char* lcdDateTimeFmt  = "d.m.y    H:i:s"; // date+time format on the LCD
```

#### Configurable via the WiFiManager portal (no re-flash needed)

| Field | Default | Notes |
|-------|---------|-------|
| MQTT Server | `your.server.address` | your server's hostname or IP |
| MQTT Port | `8883` | TLS port; `MOSQUITTO_PORTS_TLS` in `.env` |
| MQTT User | `geiger00` | leave at default for auto-provisioning |
| MQTT Password | `geiger00PW` | leave at default for auto-provisioning |
| MQTT Pepper | *(auto-generated)* | auto-generated on first boot if empty; must match your user's pepper in the web UI |
| Timezone | `Europe/Vienna` | [tz database name](https://en.wikipedia.org/wiki/List_of_tz_database_time_zones) |
| CPM Factor (uSv/h) | `0.0057` | Conversion constant for your GM tube |
| Dead time (us) | `200` | ISR debounce (200 for SBM-20, 50-90 for J305) |

### What the firmware does

- On first boot, if no pepper is configured, the firmware auto-generates one (8 random hex chars via hardware RNG) and saves it to flash.
- If no WiFi credentials are saved, it opens an access point (`GeigerCounter` / `wifiApPass`) and serves a captive portal. The pepper field is pre-filled with the auto-generated value.
- After connecting, it synchronises time via NTP and connects to the MQTT broker.
- If MQTT connection fails after 12 attempts, the LCD displays the device's MAC address, MQTT user ID, and pepper for 2 minutes (use these to register the device in the web UI), then retries.
- Every 60 seconds it publishes a JSON measurement to `/<device_id>/impulses`, clock-aligned to wall-clock minutes.

**MQTT message format** (published every 60 s):
```json
{"id":"geiger_aabbcc","ts":"2026-02-24 14:30:00","cpm":42,"usvh":0.2394}
```

---

## Device provisioning (auto-provisioning via web UI)

v3.0 replaces the manual `add-device.sh` script with web-based provisioning:

1. Flash the device — on first boot it auto-generates a pepper and shows it on the LCD / serial
2. Create a user account on the web dashboard
3. In **Account** settings, set your MQTT pepper to the value shown on the device
4. In **Devices**, enter your device's WiFi MAC address (also shown on the LCD after 12 failed MQTT attempts)
5. The server derives the same MQTT credentials as the firmware and provisions them to Mosquitto automatically (within 5 seconds)
6. The device connects on next retry cycle

**How it works:** Both firmware and server compute `username = geiger_<last 6 hex of MAC>` and `password = HMAC-SHA256(pepper, mac)[:16]`. The pepper is stored per-user, so different users can have different peppers.

<img src="docs/screenshots/account.png" alt="Account page: email, MQTT pepper, public/private profile and the change-password form" width="800">

<img src="docs/screenshots/devices.png" alt="Devices page: the register-device form and two registered counters, one online and one offline, with MAC, last seen and provisioning state" width="800">

---

## Backend — `ngnt-geiger-dockerized/`

Four Docker containers:

```
Geiger counter
     |  WiFi / MQTT (TLS, port 8883)
     v
+-----------------+        +------------------+
|   Mosquitto     |------->|  Python          |
|  MQTT broker    |        |  subscriber      |
|  :8883 (TLS)    |        +--------+---------+
|  :2883 (plain)  |                 | INSERT
+-----------------+                 v
                           +------------------+
                           |    MariaDB       |
                           |  :3306 (internal)|
                           +--------+---------+
                                    | SELECT
                                    v
                           +------------------+
                           |  Flask/Gunicorn  |
                           |  dashboard :1880 |
                           +------------------+
```

### First-time setup

**1. Clone and configure**
```bash
git clone https://github.com/phoen-ix/ngnt-geiger-counter.git
cd ngnt-geiger-counter/ngnt-geiger-dockerized

cp .env.example .env
# Edit .env — change all passwords, FLASK_SECRET_KEY, and SITE_NAME before proceeding
```

**2. Start the stack**
```bash
docker compose up -d --build
```

On first start:
- MariaDB runs `dbinit.sql` and creates all tables
- Flask creates an `admin` user with a random password, printed to stdout and saved to `data/admin_initial_password.txt`

**3. Get the admin password**
```bash
docker compose logs ngnt-geiger-flask | grep Password
# or (the file belongs to root, mode 0600)
docker compose exec ngnt-geiger-flask cat /app/data/admin_initial_password.txt
```

**4. Log in and configure**

Navigate to `http://<your-server>:1880`, log in as `admin`, and:
- Change the admin password (Account page) — this deletes `admin_initial_password.txt`
- Set the **Base URL** (Admin > Global Settings) to your server's public URL (e.g. `https://geiger.example.com`) — used in password reset emails
- Configure SMTP settings (Admin page) if you want password reset emails
- Adjust global settings (timezone, alert thresholds, session timeout, registration open/disabled, etc.)

<img src="docs/screenshots/admin.png" alt="Admin page: global settings, SMTP settings for password-reset email, and the user list with role, visibility and device count" width="800">

**5. Register devices**

Create user accounts, set peppers, and register devices via the Devices page. See "Device provisioning" above.

### Dashboard visibility

- **Anonymous visitors** see devices belonging to users with public profiles
- **Logged-in users** see their own devices plus public devices
- **Admins** see all devices

Pick one counter in the **Device** dropdown to see only its readings; an unplugged one shows as offline:

<img src="docs/screenshots/dashboard-device-offline.png" alt="Dashboard filtered to the basement counter: its last reading two days ago, status offline, no readings in the last 24 hours" width="800">

### Upgrading MariaDB

The MariaDB version comes from `DOCKER_IMAGE_MARIADB_TAG` in `.env` (`.env.example` ships `12.3.3`); an existing
install keeps its own value until you change it. The service sets `MARIADB_AUTO_UPGRADE=1`, so after raising the tag
the container upgrades `volumes/mariadb` in place on its first start. Take a verified dump first
(`docker compose exec ngnt-geiger-mariadb mariadb-dump ...`) — a major-version upgrade cannot be rolled back.

### Leftovers in `app/` (installs from before 2026-10-24)

Older versions mounted `app/` read-write at `/app` with `data/` and `config/mosquitto/` nested inside, so Docker left
root-owned `app/data/`, `app/mosquitto-config/` (empty mount points) and `app/__pycache__/` in the checkout. The app
is now mounted read-only at `/src`. After pulling, recreate the container and remove them once:

```bash
docker compose up -d ngnt-geiger-flask
sudo rm -rf app/data app/mosquitto-config app/__pycache__
```

### Reset (wipe all data and start fresh)

The containers write these directories as root and the MariaDB user, hence `sudo`:

```bash
docker compose down
sudo rm -rf volumes/mariadb/* volumes/mosquitto/data/* volumes/mosquitto/certs/* data/
docker compose up -d
```

### Container overview

| Container | Image / Dockerfile | Exposes | Role |
|-----------|-------------------|---------|------|
| `ngnt-geiger-mariadb` | `mariadb:12.3.3` | 3306 (internal) | Persistent storage |
| `ngnt-geiger-mosquitto` | `DockerfileMosquitto` (eclipse-mosquitto + openssl) | 8883 (TLS), 2883 -> 1883 (plain) | MQTT broker with TLS + auto-reload |
| `ngnt-geiger-subscriber` | `DockerfileSubscriber` (`python:3.15.1-slim-trixie`) | — | Async MQTT subscriber -> DB |
| `ngnt-geiger-flask` | `DockerfileFlask` (`python:3.15.1-slim-trixie`) | 1880 -> 8000 | Flask web dashboard + user management |

All containers share the internal bridge network `10.188.1.0/24` (`IPV4_NETWORK` in `.env`). It must not overlap any other Docker network on the host; the subscriber reaches MariaDB (`.20`) and Mosquitto (`.30`) by these fixed addresses.

---

## Running tests

The test suite runs on the host (no Docker needed) and uses mocked DB connections — no external dependencies required.

```bash
cd ngnt-geiger-dockerized
pip install -r app/requirements-dev.txt
python -m pytest -v
```

It covers every route and the middleware (session checks, CSRF, rate limiting); `python -m pytest --collect-only -q` lists the tests.

---

## Screenshots

Every screenshot here comes from the docs tour in `ngnt-geiger-dockerized/e2e/docs/`: it starts a throwaway Flask + MariaDB stack, seeds fictional users, three counters and a day of readings, and photographs each screen. The [gallery](docs/screenshots/README.md) shows them all with a line on what each screen does.

```bash
cd ngnt-geiger-dockerized/e2e && npm ci && npx playwright install chromium && cd ..
scripts/docs-screenshots.sh                          # writes ../docs/screenshots/*.png
PORT_BASE=24800 scripts/docs-screenshots.sh /tmp/shots   # another port / output dir
```

It needs Docker and Node 26, runs as compose project `ngnt-geiger-counter_docs` on `127.0.0.1:${PORT_BASE:-1880}`, reads `.env.example` (never `.env`) and keeps its data in named volumes, so it does not touch a local instance in the same checkout — pass another `PORT_BASE` if that one uses port 1880. The readings end at the start of the current UTC hour, so a run in another hour changes the dashboard's timestamps. CI runs the tour on every push as a render check; the **Docs screenshots** workflow (Actions, manual) returns the PNGs as an artifact.

---

## Security notes

- **MQTT TLS** — The firmware uses `WiFiClientSecure::setInsecure()` which encrypts traffic but does not verify the server certificate. This prevents passive eavesdropping of MQTT credentials over WiFi but does not protect against active man-in-the-middle attacks. Acceptable for a home network deployment.
- The plain MQTT listener (port 2883 -> 1883) remains available for backward compatibility with un-updated devices and internal container-to-container traffic.
- **Flask secret key** — `FLASK_SECRET_KEY` is mandatory; the app will refuse to start if it is not set.
- **DB connection pooling** — Flask uses `DBUtils.PooledDB` to recycle MariaDB connections instead of opening a new one per request.
- **Mosquitto logging** — Set to `warning` level. Change to `debug` in `config/mosquitto/conf.d/mosquitto_addons.conf` only when troubleshooting.
- **Credential files** — `devices.conf` and `passwd.txt` are gitignored and excluded from version control. They are managed at runtime by Flask provisioning and the Mosquitto entrypoint.

## Open / future ideas

- Front plate revision: cutouts for two toggle switches (LCD backlight, speaker mute)
- Web dashboard: CSV/JSON data export
- Grafana integration (MariaDB datasource)
