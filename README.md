# not great, not terrible — geiger counter

A DIY radiation monitor built around a RadiationD v1.1 Cajoe sensor, a Wemos D1 R2 (ESP8266), and a 20×4 I²C LCD — housed in a 3D-printed case. Version 2 adds WiFi, NTP time sync, MQTT telemetry, and a self-hosted server stack (Docker) for long-term data storage and a live web dashboard.

![assembled device](https://user-images.githubusercontent.com/100175489/219118323-df211fda-93e7-4437-bd8e-3e14d5e2e7f8.jpg)

---

## Repository layout

```
ngnt-geiger-counter/
├── geiger_counter_v2.0.ino          # ESP8266 firmware (Wemos D1 R2)
└── ngnt-geiger-dockerized/
    ├── app/                         # PHP web dashboard source
    │   └── index.php
    ├── dbinit.sql                   # DB schema — auto-applied on first start
    ├── config/                      # Static config for Mosquitto & Apache
    ├── docker-compose.yml
    ├── Dockerfiles/
    ├── scripts/
    │   ├── mosquitto/               # Container entrypoint (generates passwords)
    │   └── pm2/                     # Python MQTT→DB subscriber
    ├── volumes/                     # Runtime data only — safe to wipe for a reset
    │   ├── mariadb/
    │   └── mosquitto/data/
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
| 1 | LCD2004 with I²C interface (buy one with the I²C backpack already soldered on) |
| 1 | 3D-printed case — [Printables model 399474](https://www.printables.com/model/399474-ngnt-geiger-counter-not-great-not-terrible) |
| 7 | M-F Dupont cables |
| 8 | M3×8 mm screws + nuts |
| 4 | M3×12 mm screws + nuts |
| 4 | M3 spacers ≥35 mm + matching screws + nuts |

### Assembly

1. Print the front and back plates.
2. Mount the Wemos to the back plate (4× M3×8 mm + nuts).
3. Mount the LCD to the front plate (4× M3×12 mm + nuts).
4. Remove the 4 acrylic-retaining screws from the RadiationD; mount the board to the front plate (4× M3×8 mm).
5. Connect Dupont cables per the pinout below.
6. Use M3 spacers, screws, and nuts to join front and back plate.
7. Flash the firmware (see below).

### Wiring

**LCD → Wemos D1 R2**

| LCD | Wemos |
|-----|-------|
| GND | GND |
| VCC | 3.3 V |
| SDA | D2 (SDA) |
| SCL | D1 (SCL) |

**RadiationD v1.1 → Wemos D1 R2**

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

Board support: **ESP8266 Arduino core >= 3.0.0** (`esp8266` board package in the IDE). Core 3.0.0 is required for LittleFS, which the firmware uses to persist MQTT configuration.

### Configuration

#### Compile-time only (edit before flashing)

```cpp
const String deviceHostname = "GeigerCounter"; // WiFi AP name & mDNS hostname
const String wifiApPass     = "wifiApPass";    // password for the setup AP
const String localTimezone  = "Europe/Vienna"; // tz database name for NTP
const String dateOrder      = "d.m.y";         // date format on the LCD
```

`wifiApPass` is the password for the temporary WiFi access point the device opens on first boot so you can enter your home network credentials. It is **not** your home network password.

#### Configurable via the WiFiManager portal (no re-flash needed)

The firmware uses **LittleFS + WiFiManager custom parameters** to store MQTT settings on the device's flash. When the setup portal is open, four extra fields appear:

| Field | Default | Must match |
|-------|---------|------------|
| MQTT Server | `your.server.address` | your server's hostname or IP |
| MQTT Port | `2883` | `MOSQUITTO_PORTS` in `.env` |
| MQTT User | `geiger00` | `MQTT_GEIGER_USER` in `.env` |
| MQTT Password | `geiger00PW` | `MQTT_GEIGER_USERPW` in `.env` |

Settings are saved to `/config.json` on the device flash and reloaded on every boot. To reconfigure, reset the WiFi settings (hold the reset method of your choice, or call `wifiManager.resetSettings()`) to trigger the portal again.

### What the firmware does

- On boot, if no WiFi credentials are saved, it opens an access point (`GeigerCounter` / `wifiApPass`) and serves a captive portal to configure the network. Credentials are stored in flash and reused on subsequent boots.
- After connecting, it synchronises time via NTP (ezTime) and connects to the MQTT broker.
- An interrupt on GPIO 12 increments a counter on every falling edge from the Geiger-Müller tube.
- Every 60 seconds it publishes a JSON measurement to `/geiger00/impulses`, resets the counter, and updates the LCD.

**MQTT message format** (published every 60 s):
```json
{"id":"geiger00","ts":"2026-02-24 14:30:00","cpm":42,"usvh":0.2394}
```

---

## Backend — `ngnt-geiger-dockerized/`

Four Docker containers working together:

```
Geiger counter
     │  WiFi / MQTT
     ▼
┌─────────────────┐        ┌──────────────────┐
│   Mosquitto     │───────▶│  PM2 + Python    │
│  MQTT broker    │        │  subscriber      │
│  :2883          │        └────────┬─────────┘
└─────────────────┘                 │ INSERT
                                    ▼
                           ┌──────────────────┐
                           │    MariaDB       │
                           │  :3306 (internal)│
                           └────────┬─────────┘
                                    │ SELECT
                                    ▼
                           ┌──────────────────┐
                           │  PHP / Apache    │
                           │  dashboard :1880 │
                           └──────────────────┘
```

### First-time setup

**1. Clone and configure**
```bash
git clone https://github.com/phoen-ix/ngnt-geiger-counter.git
cd ngnt-geiger-counter/ngnt-geiger-dockerized

cp .env.example .env
# Edit .env — change all passwords before proceeding
```

**2. Start the stack**
```bash
docker compose up -d --build
```

On first start, MariaDB runs `dbinit.sql` automatically and creates the `measurements` table.

**3. Open the dashboard**

Navigate to `http://<your-server>:1880` (or whatever port you set for `PHP_APACHE_PORTS`).

### Reset (wipe all data and start fresh)

```bash
docker compose down
rm -rf volumes/mariadb/* volumes/mosquitto/data/*
# The * glob leaves .gitkeep files in place
docker compose up -d
```

### Applying the schema to an existing database

If MariaDB already has data from a previous run, the init script won't fire again. Import manually:

```bash
docker exec -i ngnt-geiger-mariadb \
  mariadb -u root -p$(grep MARIADB_ROOT_PASSWORD .env | cut -d= -f2) \
  ngnt-geigercounter < dbinit.sql
```

### Container overview

| Container | Image / Dockerfile | Exposes | Role |
|-----------|-------------------|---------|------|
| `ngnt-geiger-mariadb` | `mariadb:11.4.10` | 3306 (internal) | Persistent storage |
| `ngnt-geiger-mosquitto` | `eclipse-mosquitto:2.1.2-alpine` | 2883 → 1883 | MQTT broker |
| `ngnt-geiger-subscriber` | `DockerfileSubscriber` (Python 3.13.12-slim) | — | Async MQTT subscriber → DB (batched writes, connection pool) |
| `ngnt-geiger-php_apache` | `DockerfilePhpApache` (PHP 8.4.18 Apache) | 1880 → 80 | Web dashboard |

All containers share the internal bridge network `172.18.1.0/24` (configurable via `IPV4_NETWORK` in `.env`).

---

## Open / future ideas

- Front plate revision: cutouts for two toggle switches (LCD backlight, speaker mute)
- Web dashboard: configurable time range, CSV/JSON data export
- MQTT over TLS (port 8883) for public-facing deployments
- Support for multiple geiger counter devices on the same backend
- Grafana integration (MariaDB datasource)
