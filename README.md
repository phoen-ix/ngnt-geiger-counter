# not great, not terrible — geiger counter

A DIY radiation monitor built around a RadiationD v1.1 Cajoe sensor, a Wemos D1 R2 (ESP8266), and a 20x4 I2C LCD — housed in a 3D-printed case.

**v3.0** — user accounts, web-based device provisioning, Flask dashboard (replaces PHP), per-user public/private visibility, admin panel with SMTP password reset, session security hardening.

![assembled device](https://user-images.githubusercontent.com/100175489/219118323-df211fda-93e7-4437-bd8e-3e14d5e2e7f8.jpg)

---

## Repository layout

```
ngnt-geiger-counter/
├── geiger_counter_v2.0.ino          # ESP8266 firmware (Wemos D1 R2)
└── ngnt-geiger-dockerized/
    ├── app/                         # Flask web application
    │   ├── app.py                   # Routes, admin bootstrap
    │   ├── helpers.py               # Credential derivation, provisioning, auth
    │   ├── requirements.txt
    │   ├── static/style.css
    │   └── templates/               # Jinja2 templates (9 files)
    ├── dbinit.sql                   # DB schema — auto-applied on first start
    ├── config/                      # Static config for Mosquitto & MariaDB
    ├── docker-compose.yml
    ├── Dockerfiles/
    ├── scripts/
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

## Firmware — `geiger_counter_v2.0.ino`

### Required Arduino libraries

Install these via the Arduino IDE Library Manager:

| Library | Version tested |
|---------|---------------|
| [LiquidCrystal_I2C](https://github.com/johnrickman/LiquidCrystal_I2C) | any |
| [WiFiManager](https://github.com/tzapu/WiFiManager) | 2.0.15-rc.1 |
| [ezTime](https://github.com/ropg/ezTime) | 0.8.3 |
| [PubSubClient](https://pubsubclient.knolleary.net/) | 2.8.0 |
| [ArduinoJson](https://arduinojson.org/) | v6 or v7 |

Board support: **ESP8266 Arduino core >= 3.0.0** (`esp8266` board package in the IDE).

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
| MQTT Port | `2883` | `MOSQUITTO_PORTS` in `.env` |
| MQTT User | `geiger00` | leave at default for auto-provisioning |
| MQTT Password | `geiger00PW` | leave at default for auto-provisioning |
| MQTT Pepper | *(empty)* | must match your user's pepper in the web UI |
| Timezone | `Europe/Vienna` | [tz database name](https://en.wikipedia.org/wiki/List_of_tz_database_time_zones) |
| CPM Factor (uSv/h) | `0.0057` | Conversion constant for your GM tube |
| Dead time (us) | `200` | ISR debounce (200 for SBM-20, 50-90 for J305) |

### What the firmware does

- On boot, if no WiFi credentials are saved, it opens an access point (`GeigerCounter` / `wifiApPass`) and serves a captive portal.
- After connecting, it synchronises time via NTP and connects to the MQTT broker.
- If MQTT connection fails after 12 attempts, the device opens the config portal again.
- Every 60 seconds it publishes a JSON measurement to `/<device_id>/impulses`, clock-aligned to wall-clock minutes.

**MQTT message format** (published every 60 s):
```json
{"id":"geiger_aabbcc","ts":"2026-02-24 14:30:00","cpm":42,"usvh":0.2394}
```

---

## Device provisioning (auto-provisioning via web UI)

v3.0 replaces the manual `add-device.sh` script with web-based provisioning:

1. Create a user account on the web dashboard
2. In **Account** settings, set your MQTT pepper (any string)
3. Flash the device and enter the same pepper in the WiFiManager portal
4. In **Devices**, enter your device's WiFi MAC address
5. The server derives the same MQTT credentials as the firmware and provisions them to Mosquitto automatically (within 5 seconds)
6. The device connects on next boot

**How it works:** Both firmware and server compute `username = geiger_<last 6 hex of MAC>` and `password = HMAC-SHA256(pepper, mac)[:16]`. The pepper is stored per-user, so different users can have different peppers.

---

## Backend — `ngnt-geiger-dockerized/`

Four Docker containers:

```
Geiger counter
     |  WiFi / MQTT
     v
+-----------------+        +------------------+
|   Mosquitto     |------->|  Python          |
|  MQTT broker    |        |  subscriber      |
|  :2883          |        +--------+---------+
+-----------------+                 | INSERT
                                    v
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
# or
cat data/admin_initial_password.txt
```

**4. Log in and configure**

Navigate to `http://<your-server>:1880`, log in as `admin`, and:
- Change the admin password (Account page) — this deletes `admin_initial_password.txt`
- Set the **Base URL** (Admin > Global Settings) to your server's public URL (e.g. `https://geiger.example.com`) — used in password reset emails
- Configure SMTP settings (Admin page) if you want password reset emails
- Adjust global settings (timezone, alert thresholds, registration open/disabled, etc.)

**5. Register devices**

Create user accounts, set peppers, and register devices via the Devices page. See "Device provisioning" above.

### Dashboard visibility

- **Anonymous visitors** see devices belonging to users with public profiles
- **Logged-in users** see their own devices plus public devices
- **Admins** see all devices

### Reset (wipe all data and start fresh)

```bash
docker compose down
rm -rf volumes/mariadb/* volumes/mosquitto/data/* data/
docker compose up -d
```

### Container overview

| Container | Image / Dockerfile | Exposes | Role |
|-----------|-------------------|---------|------|
| `ngnt-geiger-mariadb` | `mariadb:11.4.10` | 3306 (internal) | Persistent storage |
| `ngnt-geiger-mosquitto` | `eclipse-mosquitto:2.1.2-alpine` | 2883 -> 1883 | MQTT broker with auto-reload |
| `ngnt-geiger-subscriber` | `DockerfileSubscriber` (Python 3.13-slim) | — | Async MQTT subscriber -> DB |
| `ngnt-geiger-flask` | `DockerfileFlask` (Python 3.13-slim) | 1880 -> 8000 | Flask web dashboard + user management |

All containers share the internal bridge network `172.18.1.0/24` (configurable via `IPV4_NETWORK` in `.env`).

---

## Open / future ideas

- Front plate revision: cutouts for two toggle switches (LCD backlight, speaker mute)
- Web dashboard: CSV/JSON data export
- MQTT over TLS (port 8883) for public-facing deployments
- Grafana integration (MariaDB datasource)
