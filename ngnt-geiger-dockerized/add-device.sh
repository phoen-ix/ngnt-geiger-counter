#!/bin/bash
set -euo pipefail

if [ $# -ne 1 ]; then
    echo "Usage: $0 <MAC_ADDRESS>"
    echo "Example: $0 AA:BB:CC:DD:EE:FF"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="$SCRIPT_DIR/.env"
DEVICES_CONF="$SCRIPT_DIR/config/mosquitto/devices.conf"

if [ ! -f "$ENV_FILE" ]; then
    echo "Error: .env not found at $ENV_FILE" >&2
    exit 1
fi

MQTT_PEPPER=$(grep '^MQTT_PEPPER=' "$ENV_FILE" | cut -d= -f2)
if [ -z "$MQTT_PEPPER" ]; then
    echo "Error: MQTT_PEPPER not set in .env" >&2
    exit 1
fi

mac_hex=$(echo "$1" | tr -d ':' | tr 'A-F' 'a-f')
if [ ${#mac_hex} -ne 12 ]; then
    echo "Error: Invalid MAC address (expected format: AA:BB:CC:DD:EE:FF)" >&2
    exit 1
fi

username="geiger_${mac_hex: -6}"
password=$(echo -n "$mac_hex" | openssl dgst -sha256 -hmac "$MQTT_PEPPER" | sed 's/.*= //' | head -c 16)

echo "MAC:      $1"
echo "Username: $username"
echo "Password: $password"
echo ""

# Persist to devices.conf (survives container restarts and resets)
if grep -q "^$username " "$DEVICES_CONF" 2>/dev/null; then
    echo "Device already in devices.conf — skipping"
else
    echo "$username $password" >> "$DEVICES_CONF"
    echo "Saved to devices.conf"
fi

# Add to running Mosquitto
docker exec ngnt-geiger-mosquitto \
    mosquitto_passwd -b /mosquitto/config/passwd.txt "$username" "$password"

echo "Added to Mosquitto. Restart the broker to apply:"
echo "  docker restart ngnt-geiger-mosquitto"
