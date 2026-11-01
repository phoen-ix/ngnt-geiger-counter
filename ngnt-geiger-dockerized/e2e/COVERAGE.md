# E2E coverage

Every user-facing feature of the web app and the MQTT pipeline, mapped to the test that exercises it
(`tests/<spec> › <title>`), or ❌ with the reason it is not an e2e test here.

Extracted 2026-10-31 with (in `ngnt-geiger-dockerized/`):

```bash
grep -n "@app.route" app/app.py                                   # 9 routes (+ Flask's /static)
grep -no 'name="action" value="[a-z_]*"' app/templates/*.html     # 10 form actions
grep -n "onsubmit\|onchange" app/templates/*.html                 # 2 confirm dialogs, 1 auto-submit
grep -n "flash(" app/app.py                                       # messages / error states
grep -n "elif msg.get\|if 'cpm' in msg" scripts/pm2/mqtt_bro_impulses.py   # 3 MQTT message kinds
```

**122 items: 118 ✅, 4 ❌ (3 × (c), 1 × (a)).** Roles: guest (signed out), user, admin. The suite runs as `setup` →
`main` → `global` (site-wide settings, alone) → `limits` (last); see `playwright.config.ts`.

## Routes × roles (breadth)

`sweep.spec.ts` visits every route as every role: right page, its heading, the site heading, no page error,
no 5xx, no request leaving the stack.

| Route | guest | user | admin |
|---|---|---|---|
| `GET /` | ✅ `sweep › as guest › GET /` | ✅ `sweep › as user › GET /` | ✅ `sweep › as admin › GET /` |
| `GET /login` | ✅ `… › as guest › GET /login` | ✅ `… › as user › GET /login` | ✅ `… › as admin › GET /login` |
| `GET /register` | ✅ `… › as guest › GET /register` | ✅ `… › as user › GET /register` | ✅ `… › as admin › GET /register` |
| `GET /forgot-password` | ✅ `… › as guest › GET /forgot-password` | ✅ `… › as user › …` | ✅ `… › as admin › …` |
| `GET /reset-password/<token>` | ✅ `… › as guest › GET /reset-password/<token>` | ✅ `… › as user › …` | ✅ `… › as admin › …` |
| `GET /devices` | ✅ → `/login`, `… › as guest › GET /devices` | ✅ `… › as user › GET /devices` | ✅ `… › as admin › GET /devices` |
| `GET /account` | ✅ → `/login`, `… › as guest › GET /account` | ✅ `… › as user › GET /account` | ✅ `… › as admin › GET /account` |
| `GET /admin` | ✅ → `/login`, `… › as guest › GET /admin` | ✅ → `/`, `… › as user › GET /admin` | ✅ `… › as admin › GET /admin` |
| `GET /logout` | ✅ `… › as guest › GET /logout` | ✅ `… › as user › GET /logout` | ✅ `… › as admin › GET /logout` |
| nav links per role | ✅ `… › as guest › nav links` | ✅ `… › as user › nav links` | ✅ `… › as admin › nav links` |
| `GET /static/style.css` | ✅ `sweep › the stylesheet is served` | — same file | — same file |
| unknown path → 404 | ✅ `sweep › an unknown path is a 404` | — same handler | — same handler |

## Sign-up, sign-in, password reset (`auth.spec.ts`)

| Feature / state | Test |
|---|---|
| Register → "Account created" → sign in with it (role user, email stored) | ✅ `register › a new account can sign in` |
| Register: username too short | ✅ `register › invalid input is refused with its reason and the form keeps the username` |
| Register: username with invalid characters | ✅ same |
| Register: password too short | ✅ same |
| Register: passwords differ | ✅ same |
| Register: username taken | ✅ same |
| Register: form keeps username after an error | ✅ same |
| Register: email over 255 characters (was a 500) | ✅ `register › an email over 255 characters is refused, not a server error` |
| Register: empty required fields stopped by the browser | ✅ `register › empty required fields are stopped in the browser` |
| Register: "Username and password are required." (only reachable without the browser's `required`) | ❌ (c) `tests/test_auth.py::test_register_empty_fields` |
| Register disabled → `/login` with message | ✅ `global › registration disabled: the register page sends people to the login page` |
| Login: success, "Logged in.", nav switches | ✅ `sign in and out › sign in, then sign out` |
| Login: wrong password / unknown user, same message | ✅ `sign in and out › a wrong password or an unknown user gets the same message` |
| Logout: "Logged out.", protected pages back to `/login` | ✅ `sign in and out › sign in, then sign out` |
| Links login ↔ forgot-password ↔ register | ✅ `sign in and out › the login page links to forgot-password and register` |
| Forgot password → mail (Mailpit) with the `base_url` link → new password → sign in; old password fails | ✅ `password reset › mail link sets a new password once; the old one stops working` |
| Reset link is single-use | ✅ same |
| Forgot password: unknown user / user without email → same message, no token, no mail | ✅ `password reset › no mail for an unknown user or one without an email, same message` |
| Reset: unknown token, expired token → `/login` "Invalid or expired reset link." | ✅ `password reset › an unknown or expired link goes back to the login page` |
| Reset: password too short / mismatched, token stays unused | ✅ `password reset › a short or mismatched new password is refused` |
| Session ends when the password changes elsewhere (`pw_version`) | ✅ `account › change the password: this session stays, the others are signed out` |
| Session ends when the user is deleted | ✅ `admin › delete a user after confirming: devices unprovisioned, their session ends` |
| Role change applies without signing in again | ✅ `admin › promote a user to admin and back; the role applies without signing in again` |
| Session lifetime = session timeout setting | ✅ `global › the session timeout sets how long a sign-in lasts` |
| Rate limit: login 10/min → 429 | ✅ `limits › POST /login answers 429 after 10 a minute` |
| Rate limit: register 5/min → 429 | ✅ `limits › POST /register answers 429 after 5 a minute` |
| Rate limit: forgot-password 5/min → 429 | ✅ `limits › POST /forgot-password answers 429 after 5 a minute` |
| CSRF: POST without token → 400 | ✅ `limits › a form posted without its CSRF token is refused` |
| Bootstrap admin: random first password in `data/`, removed after the change | ✅ `setup › admin password, SMTP to Mailpit, shared sessions` (reads it, changes it, the file is gone) |

## Dashboard (`dashboard.spec.ts`)

| Feature / state | Test |
|---|---|
| Guest sees public users' counters only | ✅ `visibility: guests see public counters, users also their own, admins all` |
| User sees own + public | ✅ same |
| Admin sees all | ✅ same |
| `?device=` of a counter you may not see → all devices | ✅ same |
| Device filter (auto-submitting select), keeps range, header without device name, table/cards of that device | ✅ `the device filter shows one counter, keeps the range and the header drops the device name` |
| "All devices" again | ✅ same |
| Only one visible counter → no device filter | ❌ (c) `tests/test_dashboard.py::test_dashboard_single_device_has_no_device_filter` (every other test's public counters are visible in e2e) |
| No visible counters at all → "Waiting for first measurement" | ❌ (c) `tests/test_dashboard.py::test_dashboard_empty_state` (same reason); the per-counter empty state is ✅ below |
| Ranges 1 h / 6 h / 24 h / 7 d: active tab, headings, table rows, chart points | ✅ `the time ranges: each lists the readings inside it` |
| Unknown range → 24 h | ✅ same |
| Auto-refresh every 60 s with range and device | ✅ same (`meta refresh`) |
| Cards: CPM, dose rate (4 decimals), readings count, "100+" and 100 rows max | ✅ `cards: dose rate turns orange above the alert threshold; 100+ readings` |
| Dose rate orange above the device's alert threshold | ✅ same |
| Dose rate orange above the default alert threshold | ✅ `global › the offline timeout and the default alert threshold change the dashboard cards` |
| Device card online / offline (status + last seen within the offline timeout) | ✅ `online means status online and seen within the offline timeout` |
| Offline timeout setting changes it | ✅ `global › the offline timeout and the default alert threshold change the dashboard cards` |
| Timestamps in the display timezone (Europe/Vienna) | ✅ `timestamps are shown in the display timezone` |
| Another display timezone | ✅ `global › the display timezone applies to dashboard and devices dates` |
| Counter without readings: "Waiting for first measurement", empty cards, no chart, empty table | ✅ `a counter without readings: empty cards, chart and table` |
| Chart drawn from the readings | ✅ `the time ranges: each lists the readings inside it` (Chart.js data points) |

## Devices (`devices.spec.ts`, `mqtt.spec.ts`)

| Feature / state | Test |
|---|---|
| Register a device: lower-case MAC accepted, ID derived like the firmware, no password column in the DB, listed (offline, —, Yes) | ✅ `devices › register a device: credentials derived like the firmware, listed as not yet seen` |
| Malformed MAC: browser pattern, server message | ✅ `devices › a malformed MAC is refused, in the browser and by the server` |
| Device ID already registered (any user) | ✅ `devices › a MAC whose device ID is taken, by anyone, is refused` |
| No pepper: no form, link to account; server refuses the POST | ✅ `devices › without a pepper there is no form, only a link to the account page` |
| Rename; clear name → device ID placeholder | ✅ `devices › rename a device, and clear its name back to the device ID` |
| Display name over 100 characters, add and rename (was a 500) | ✅ `devices › a display name over 100 characters is refused, not a server error` |
| Delete: confirm dialog text, cancel keeps it, accept deletes | ✅ `devices › delete a device after confirming; cancelling keeps it` |
| Only own devices listed; others' cannot be renamed or deleted | ✅ `devices › only your own devices are listed, and another user's cannot be renamed or deleted` |
| Last seen in the display timezone | ✅ `global › the display timezone applies to dashboard and devices dates` |
| Provisioning reaches Mosquitto (devices.conf + reload): derived credentials accepted over TLS, wrong ones refused | ✅ `mqtt › register, connect, measure, go offline, delete` |
| MQTT "connected" → online + last seen | ✅ same |
| MQTT measurement → stored → dashboard row, CPM card, online | ✅ same |
| MQTT last will "offline" → offline | ✅ same |
| Delete unprovisions: credentials refused again | ✅ same |
| A counter's payload naming another device is ignored (was stored) | ✅ `mqtt › a counter cannot report readings or status for another user's device` |
| Invalid JSON / missing fields skipped, the subscriber keeps going | ✅ `mqtt › malformed messages are skipped and the next reading is stored` |

## Account (`account.spec.ts`)

| Feature / state | Test |
|---|---|
| Profile: username shown read-only, email, pepper, public saved and shown again | ✅ `update the profile: email, pepper and public visibility` |
| Public → guests see the counters; private hides them again | ✅ same |
| Pepper set → device form appears | ✅ same |
| Cleared email/pepper stored as NULL | ✅ same |
| Non-ASCII or over-64 pepper, over-255 email refused (were a 500 on adding a device / saving) | ✅ `a non-ASCII or over-long pepper and an over-long email are refused` |
| Change password: wrong current, too short, mismatched | ✅ `change the password: wrong current, too short and mismatched are refused` |
| Change password: this session stays (was signed out without a message), other sessions end, new password works | ✅ `change the password: this session stays, the others are signed out` |

## Admin (`admin.spec.ts`, `global.spec.ts`)

| Feature / state | Test |
|---|---|
| User list: username, email, role, public, device count, created (display timezone) | ✅ `admin › the user list shows email, role, visibility, device count and creation date` |
| Own row: role locked, no delete | ✅ same |
| Toggle role user ↔ admin | ✅ `admin › promote a user to admin and back; the role applies without signing in again` |
| Toggle public Yes ↔ No (dashboard follows) | ✅ `admin › toggle a user public and private` |
| Delete user: confirm text, cancel keeps, accept deletes, devices unprovisioned in Mosquitto | ✅ `admin › delete a user after confirming: devices unprovisioned, their session ends` |
| Malformed target user id → "Invalid request." | ✅ `admin › a malformed user id is refused` |
| Non-admin: page and POSTs redirected to `/`, nothing changed | ✅ `admin › a user cannot open or post to the admin page` |
| Global settings saved and shown; CPM factor read-only | ✅ `global › settings are saved and shown again; the CPM factor is read-only` |
| Registration open/disabled | ✅ `global › registration disabled: the register page sends people to the login page` |
| Display timezone | ✅ `global › the display timezone applies to dashboard and devices dates` |
| Offline timeout, default alert threshold | ✅ `global › the offline timeout and the default alert threshold change the dashboard cards` |
| Session timeout | ✅ `global › the session timeout sets how long a sign-in lasts` |
| Invalid settings refused (empty session/offline timeout locked everyone out / broke the dashboard; text threshold; unknown timezone; over-255 value) | ✅ `global › invalid settings are refused, nothing is saved and nobody is locked out` |
| Base URL used in the reset mail | ✅ `auth › password reset › mail link sets a new password once; the old one stops working` |
| SMTP settings saved and shown, password masked, STARTTLS select | ✅ `global › SMTP settings are saved and shown again, the password masked` |
| SMTP port empty refused (broke every reset mail) | ✅ same |
| SMTP delivery (host, port, from, no TLS) | ✅ `setup` + `auth › password reset › mail link …` (Mailpit) |

## Hardware

| Feature / state | Test |
|---|---|
| Firmware v2.2 on the counter: WiFiManager portal, LCD, pepper generation, MQTT client | ❌ (a) needs the ESP8266 + tube; no simulator. The suite plays the counter instead: `mosquitto_pub` over the same TLS listener and topic, with credentials derived the firmware's way (`mqtt.spec.ts`) |
