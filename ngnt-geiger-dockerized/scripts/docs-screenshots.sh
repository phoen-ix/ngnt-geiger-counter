#!/usr/bin/env bash
# Regenerate the docs screenshots: build the images, start a throwaway Flask +
# MariaDB stack under its own compose project, seed it and run the docs tour
# (e2e/docs/) against it, tear everything down again.
#
#   scripts/docs-screenshots.sh [outdir]      # default: <repo>/docs/screenshots
#
# PORT_BASE (default 1880): the dashboard on 127.0.0.1:PORT_BASE.
# COMPOSE_PROJECT_NAME (default ngnt-geiger-counter_docs): never a live
# project's name. ARTIFACTS: where Playwright's test results go (default: a
# temp dir). The stack reads .env.example (never .env) and keeps its state in
# named volumes, so a local instance in this checkout is not touched.
# Node + `npm ci` in e2e/ are the caller's.
set -euo pipefail

APP="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="${1:-$APP/../docs/screenshots}"
OUT="$(mkdir -p "$OUT" && cd "$OUT" && pwd)"
cd "$APP"
ARTIFACTS="${ARTIFACTS:-$(mktemp -d)}"
mkdir -p "$ARTIFACTS"
PORT_BASE="${PORT_BASE:-1880}"
export COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-ngnt-geiger-counter_docs}"

case "${COMPOSE_PROJECT_NAME,,}" in
  ngnt-geiger-dockerized | phytotron | phytotron-dev | vib | fileheron | keyb-dev)
    echo "docs-screenshots: refusing the live project name '$COMPOSE_PROJECT_NAME'" >&2
    exit 1 ;;
esac

export E2E_WEB_PORT="$PORT_BASE" E2E_MQTT_PORT="$((PORT_BASE + 8))"
# A /24 of its own per port block (24800 -> 10.248.0), clear of Docker's
# default pools and of the live stack's IPV4_NETWORK.
export IPV4_NETWORK="10.$((PORT_BASE / 100 % 256)).$((PORT_BASE % 100))"

compose() {
  docker compose --env-file .env.example -f docker-compose.yml -f docker-compose.e2e.yml "$@"
}
teardown() {
  rc=$?
  [ "$rc" = 0 ] || compose logs --tail=200 >"$ARTIFACTS/stack.log" 2>&1 || true
  compose down -v --remove-orphans --rmi local || true
  exit "$rc"
}
trap teardown EXIT

# No Mosquitto or subscriber: the tour seeds readings with SQL.
compose up -d --build --wait ngnt-geiger-flask ngnt-geiger-mariadb

cd e2e
E2E_BASE_URL="http://127.0.0.1:$PORT_BASE" \
  DOCS_SCREENSHOT_DIR="$OUT" \
  DOCS_DB_CONTAINER="$COMPOSE_PROJECT_NAME-ngnt-geiger-mariadb" \
  DOCS_FLASK_CONTAINER="$COMPOSE_PROJECT_NAME-ngnt-geiger-flask" \
  npx playwright test -c playwright.docs.config.ts --output "$ARTIFACTS/test-results"
echo "docs-screenshots: $(find "$OUT" -name '*.png' | wc -l) PNGs in $OUT"
