#!/bin/ash
set -e

PASSWD_FILE="/mosquitto/config/passwd.txt"
DEVICES_CONF="/mosquitto/config/devices.conf"
RELOAD_FLAG="/mosquitto/config/.reload"

# ── Build initial password file ──────────────────────────────────────────────

rebuild_passwd() {
    # Truncate (can't use -c on a bind-mounted file) then add the subscriber user
    : > "$PASSWD_FILE"
    mosquitto_passwd -b "$PASSWD_FILE" "$MQTT_PYTHON_USER" "$MQTT_PYTHON_USERPW"

    # Re-provision auto-provisioned devices (if any)
    if [ -f "$DEVICES_CONF" ]; then
        while read -r dev_user dev_pass; do
            [ -z "$dev_user" ] && continue
            mosquitto_passwd -b "$PASSWD_FILE" "$dev_user" "$dev_pass"
        done < "$DEVICES_CONF"
    fi

    # passwd.txt must be owned by root but readable by the mosquitto group
    chown root:mosquitto "$PASSWD_FILE"
    chmod 0640 "$PASSWD_FILE"
}

rebuild_passwd

# Data and log dirs need mosquitto ownership for the daemon to write
chown -R mosquitto:mosquitto /mosquitto/data /mosquitto/log 2>/dev/null || true

# Clean up any stale reload flag
rm -f "$RELOAD_FLAG"

# ── Start Mosquitto in background ────────────────────────────────────────────

"$@" &
MOSQUITTO_PID=$!

# ── Reload watcher ───────────────────────────────────────────────────────────
# Polls every 5s for the .reload flag file. When found, rebuilds the password
# file from scratch and sends SIGHUP to Mosquitto to reload config.

reload_watcher() {
    while kill -0 "$MOSQUITTO_PID" 2>/dev/null; do
        if [ -f "$RELOAD_FLAG" ]; then
            echo "[entrypoint] reload flag detected — rebuilding passwd.txt"
            rm -f "$RELOAD_FLAG"
            rebuild_passwd
            kill -HUP "$MOSQUITTO_PID" 2>/dev/null || true
            echo "[entrypoint] SIGHUP sent to Mosquitto"
        fi
        sleep 5
    done
}

reload_watcher &

# ── Signal forwarding ────────────────────────────────────────────────────────
# Forward SIGTERM/SIGINT to Mosquitto so docker stop works cleanly

trap "kill $MOSQUITTO_PID 2>/dev/null; wait $MOSQUITTO_PID" TERM INT

wait $MOSQUITTO_PID
