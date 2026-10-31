#!/usr/bin/env bash
# Run the e2e suite (e2e/tests/): build the images, start the whole stack
# (Flask, MariaDB, Mosquitto, the MQTT subscriber, Mailpit) under its own
# compose project, run Playwright against it, tear everything down again.
#
#   scripts/e2e.sh [playwright args]          # e.g. --grep devices
#
# PORT_BASE (default 1880): the dashboard on 127.0.0.1:PORT_BASE, Mailpit on
# +5, MQTT/TLS on +8. COMPOSE_PROJECT_NAME (default ngnt-geiger-counter_e2e):
# never a live project's name. ARTIFACTS: where the stack log and Playwright's
# results and report go (default: a temp dir). The stack reads .env.example
# (never .env) and keeps its state in named volumes, so a local instance in
# this checkout is not touched. Node + `npm ci` in e2e/ are the caller's.
set -euo pipefail

APP="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$APP"
ARTIFACTS="${ARTIFACTS:-$(mktemp -d)}"
mkdir -p "$ARTIFACTS"
ARTIFACTS="$(cd "$ARTIFACTS" && pwd)"
PORT_BASE="${PORT_BASE:-1880}"
export COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-ngnt-geiger-counter_e2e}"

case "${COMPOSE_PROJECT_NAME,,}" in
  ngnt-geiger-dockerized | phytotron | phytotron-dev | vib | fileheron | keyb-dev)
    echo "e2e: refusing the live project name '$COMPOSE_PROJECT_NAME'" >&2
    exit 1 ;;
esac

export E2E_WEB_PORT="$PORT_BASE" E2E_MAIL_PORT="$((PORT_BASE + 5))" E2E_MQTT_PORT="$((PORT_BASE + 8))"
# A /24 of its own per port block (24800 -> 10.248.0), as in docs-screenshots.sh.
export IPV4_NETWORK="10.$((PORT_BASE / 100 % 256)).$((PORT_BASE % 100))"

compose() {
  docker compose --env-file .env.example -f docker-compose.yml -f docker-compose.e2e.yml "$@"
}
teardown() {
  rc=$?
  compose logs --no-color >"$ARTIFACTS/stack.log" 2>&1 || true
  compose down -v --remove-orphans --rmi local || true
  exit "$rc"
}
trap teardown EXIT

compose up -d --build --wait

cd e2e
E2E_BASE_URL="http://127.0.0.1:$PORT_BASE" \
  E2E_MAIL_URL="http://127.0.0.1:$E2E_MAIL_PORT" \
  E2E_ARTIFACTS="$ARTIFACTS" \
  npx playwright test "$@"
