# not great, not terrible — geiger counter

A DIY radiation monitor built around a RadiationD v1.1 Cajoe sensor, a Wemos D1 R2 (ESP8266), and a 20x4 I2C LCD — housed in a 3D-printed case.

**v3.3** — Firmware v2.2: auto-generated pepper, info screen replaces reconnect portal. MQTT over TLS. Comprehensive test suite (98 pytest tests). User accounts, web-based device provisioning, Flask dashboard, per-user visibility, admin panel with SMTP password reset, session security hardening, CSRF protection, rate limiting.

![assembled device](https://user-images.githubusercontent.com/100175489/219118323-df211fda-93e7-4437-bd8e-3e14d5e2e7f8.jpg)

---

## Repository layout

```
ngnt-geiger-counter/
├── geiger_counter_v2.2.ino          # ESP8266 firmware (Wemos D1 R2)
└── ngnt-geiger-dockerized/
    ├── app/                         # Flask web application
    │   ├── app.py                   # Routes, admin bootstrap
    │   ├── helpers.py               # Credential derivation, provisioning, auth
    │   ├── requirements.txt
    │   ├── static/style.css
    │   └── templates/               # Jinja2 templates (9 files)
    ├── tests/                       # pytest test suite (98 tests)
    │   ├── conftest.py              # Fixtures (app, client, auth, admin)
    │   ├── testutils.py             # MockCursor, MockDB, queue helpers
    │   └── test_*.py                # 7 test modules
    ├── pytest.ini
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

## Firmware — `geiger_counter_v2.2.ino`

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
# or
cat data/admin_initial_password.txt
```

**4. Log in and configure**

Navigate to `http://<your-server>:1880`, log in as `admin`, and:
- Change the admin password (Account page) — this deletes `admin_initial_password.txt`
- Set the **Base URL** (Admin > Global Settings) to your server's public URL (e.g. `https://geiger.example.com`) — used in password reset emails
- Configure SMTP settings (Admin page) if you want password reset emails
- Adjust global settings (timezone, alert thresholds, session timeout, registration open/disabled, etc.)

**5. Register devices**

Create user accounts, set peppers, and register devices via the Devices page. See "Device provisioning" above.

### Dashboard visibility

- **Anonymous visitors** see devices belonging to users with public profiles
- **Logged-in users** see their own devices plus public devices
- **Admins** see all devices

### Reset (wipe all data and start fresh)

```bash
docker compose down
rm -rf volumes/mariadb/* volumes/mosquitto/data/* volumes/mosquitto/certs/* data/
docker compose up -d
```

### Container overview

| Container | Image / Dockerfile | Exposes | Role |
|-----------|-------------------|---------|------|
| `ngnt-geiger-mariadb` | `mariadb:11.4.10` | 3306 (internal) | Persistent storage |
| `ngnt-geiger-mosquitto` | `DockerfileMosquitto` (eclipse-mosquitto + openssl) | 8883 (TLS), 2883 -> 1883 (plain) | MQTT broker with TLS + auto-reload |
| `ngnt-geiger-subscriber` | `DockerfileSubscriber` (Python 3.13-slim) | — | Async MQTT subscriber -> DB |
| `ngnt-geiger-flask` | `DockerfileFlask` (Python 3.13-slim) | 1880 -> 8000 | Flask web dashboard + user management |

All containers share the internal bridge network `172.18.1.0/24` (configurable via `IPV4_NETWORK` in `.env`).

---

## Running tests

The test suite runs on the host (no Docker needed) and uses mocked DB connections — no external dependencies required.

```bash
cd ngnt-geiger-dockerized
pip install flask flask-wtf flask-limiter pymysql pytest
FLASK_TESTING=1 python -m pytest tests/ -v
```

98 tests cover all routes and middleware:

<details>
<summary><b>test_helpers</b> — 18 tests</summary>

- `test_derive_mqtt_credentials_known_values`
- `test_derive_mqtt_credentials_case_insensitive_mac`
- `test_derive_mqtt_credentials_dash_separator`
- `test_derive_mqtt_credentials_username_format`
- `test_derive_mqtt_credentials_different_pepper_different_password`
- `test_provision_device_creates_entry`
- `test_provision_device_creates_reload_flag`
- `test_provision_device_idempotent`
- `test_provision_device_multiple_devices`
- `test_unprovision_device_removes_entry`
- `test_unprovision_device_creates_reload_flag`
- `test_unprovision_device_preserves_others`
- `test_unprovision_device_nonexistent`
- `test_get_settings_returns_dict`
- `test_login_required_redirects` / `test_login_required_passes`
- `test_admin_required_redirects_no_session` / `test_admin_required_redirects_non_admin` / `test_admin_required_passes`
- `test_send_reset_email_no_smtp_host` / `test_send_reset_email_success` / `test_send_reset_email_no_tls` / `test_send_reset_email_failure`
</details>

<details>
<summary><b>test_auth</b> — 17 tests</summary>

- `test_login_get_renders_form`
- `test_login_valid_credentials`
- `test_login_invalid_password`
- `test_login_nonexistent_user`
- `test_login_sets_session_permanent`
- `test_register_get_renders_form`
- `test_register_disabled`
- `test_register_success`
- `test_register_username_taken` / `test_register_username_too_short` / `test_register_username_too_long` / `test_register_username_bad_chars`
- `test_register_password_too_short` / `test_register_password_mismatch` / `test_register_empty_fields`
- `test_logout_clears_session`
- `test_forgot_password_get` / `test_forgot_password_valid_user_with_email` / `test_forgot_password_user_without_email` / `test_forgot_password_nonexistent_user` / `test_forgot_password_cleans_expired_tokens`
- `test_reset_password_get_valid_token` / `test_reset_password_get_invalid_token`
- `test_reset_password_post_success` / `test_reset_password_post_too_short` / `test_reset_password_post_mismatch`
</details>

<details>
<summary><b>test_dashboard</b> — 8 tests</summary>

- `test_dashboard_anonymous_public_only`
- `test_dashboard_logged_in_own_and_public`
- `test_dashboard_admin_sees_all`
- `test_dashboard_empty_state`
- `test_dashboard_range_filter_1h`
- `test_dashboard_range_invalid_defaults_24h`
- `test_dashboard_device_filter`
- `test_dashboard_device_filter_invalid_ignored`
</details>

<details>
<summary><b>test_devices</b> — 10 tests</summary>

- `test_devices_requires_login`
- `test_devices_get_lists_devices` / `test_devices_get_empty`
- `test_add_device_success` / `test_add_device_invalid_mac` / `test_add_device_no_pepper` / `test_add_device_duplicate`
- `test_update_device_success`
- `test_delete_device_success` / `test_delete_device_nonexistent` / `test_delete_device_calls_unprovision`
</details>

<details>
<summary><b>test_account</b> — 10 tests</summary>

- `test_account_requires_login`
- `test_account_get_renders`
- `test_update_profile_email` / `test_update_profile_pepper` / `test_update_profile_public_toggle`
- `test_change_password_success` / `test_change_password_wrong_current` / `test_change_password_too_short` / `test_change_password_mismatch`
- `test_change_password_removes_initial_file`
</details>

<details>
<summary><b>test_admin</b> — 12 tests</summary>

- `test_admin_requires_login` / `test_admin_requires_admin_role`
- `test_admin_get_renders`
- `test_save_settings` / `test_save_smtp`
- `test_toggle_role_success` / `test_toggle_role_cannot_change_self`
- `test_toggle_public`
- `test_delete_user_success` / `test_delete_user_cannot_delete_self` / `test_delete_user_unprovisions_all_devices`
- `test_invalid_target_user_id`
</details>

<details>
<summary><b>test_middleware</b> — 7 tests</summary>

- `test_before_request_user_deleted_clears_session`
- `test_before_request_pw_version_mismatch`
- `test_before_request_role_refreshed`
- `test_before_request_session_timeout`
- `test_before_request_db_error_swallowed`
- `test_before_request_skips_static`
- `test_csrf_rejects_without_token`
- `test_rate_limiting_enforced`
</details>

---

## Security notes

- **MQTT TLS** — The firmware uses `WiFiClientSecure::setInsecure()` which encrypts traffic but does not verify the server certificate. This prevents passive eavesdropping of MQTT credentials over WiFi but does not protect against active man-in-the-middle attacks. Acceptable for a home network deployment.
- The plain MQTT listener (port 2883 -> 1883) remains available for backward compatibility with un-updated devices and internal container-to-container traffic.

## Open / future ideas

- Front plate revision: cutouts for two toggle switches (LCD backlight, speaker mute)
- Web dashboard: CSV/JSON data export
- Grafana integration (MariaDB datasource)
