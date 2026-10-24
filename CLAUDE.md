@HANDOVER.md

## Commands

All web-app work happens in `ngnt-geiger-dockerized/`.

- Tests (mocked DB, no Docker): `pip install -r app/requirements-dev.txt && python -m pytest`
- Docs screenshots (Docker + Node 26): `cd e2e && npm ci && npx playwright install chromium && cd .. && scripts/docs-screenshots.sh`
  (`PORT_BASE=24800 …` on the fleet VM, wrapped in `slot heavy ngnt-geiger-counter -- …`)
- Fleet: `fleet check ngnt-geiger-counter` (pytest + pip-audit; `--with docs` adds the tour)
- Firmware: Arduino IDE only, no build in CI.

## Invariants

- `helpers.derive_mqtt_credentials()` and the firmware (`geiger_counter_v2.2.ino`) must derive the same
  username/password from MAC + pepper. Change both or neither.
- `dbinit.sql` creates and `USE`s the database `ngnt-geigercounter` by name; `MARIADB_DATABASE` must stay that.
- The subscriber reaches MariaDB and Mosquitto by IP (`${IPV4_NETWORK}.20` / `.30`), not by alias: keep the static
  `ipv4_address` entries. `IPV4_NETWORK` must be a /24 no other Docker network on the host uses.
- `docker-compose.yml` is the production stack: fixed `container_name`s, bridge name `br-ngnt-geiger`, bind mounts
  into the checkout (`volumes/`, `data/`, `config/mosquitto/`, `app/`). Throwaway stacks only via
  `docker-compose.e2e.yml` with an explicit project name (`ngnt-geiger-counter_docs` / `ngnt-geiger-counter_e2e`) and
  `--env-file .env.example`; never project `ngnt-geiger-dockerized` (what a bare `docker compose` here calls itself).
- Fleet VM: port block 24800–24809, 127.0.0.1 only. Never touch the live stacks (`phytotron-*`, `vib-*`).
- The Flask image holds only the dependencies; the app is `app/`, mounted read-only at `/src`. Keep it outside
  `/app`, where `data/` and the Mosquitto config dir are mounted: mounts nested in a bind-mounted `app/` leave
  root-owned dirs in the checkout. Gunicorn runs with `--preload` so the admin bootstrap happens once.
- Every new form needs the hidden `csrf_token` field (Flask-WTF `CSRFProtect` checks every POST).
- Tests: `MockCursor` advances on `fetchone()`/`fetchall()`, not on `execute()`; every logged-in request needs
  `queue_before_request(cursor)` (twice with `follow_redirects=True`). Details in HANDOVER.md.
- `dashboard.html` loads Chart.js 4.4.0 from jsDelivr; `e2e/package.json` pins the same version for the tour's
  offline copy. Bump both together.
- Screenshot names in `docs/screenshots/` are stable (README and the gallery link them). The tour's readings end at
  the start of the current UTC hour, so regenerating in another hour changes the dashboard timestamps.
