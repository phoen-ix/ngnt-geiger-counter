#!/bin/ash
set -e

mosquitto_passwd -b /mosquitto/config/passwd.txt $MQTT_GEIGER_USER $MQTT_GEIGER_USERPW
mosquitto_passwd -b /mosquitto/config/passwd.txt $MQTT_PYTHON_USER $MQTT_PYTHON_USERPW

# Re-provision auto-provisioned devices (if any)
DEVICES_CONF="/mosquitto/config/devices.conf"
if [ -f "$DEVICES_CONF" ]; then
    while read -r dev_user dev_pass; do
        [ -z "$dev_user" ] && continue
        mosquitto_passwd -b /mosquitto/config/passwd.txt "$dev_user" "$dev_pass"
    done < "$DEVICES_CONF"
fi

# passwd.txt must be owned by root with restricted permissions
chmod 0700 /mosquitto/config/passwd.txt
chown root:root /mosquitto/config/passwd.txt

# Data and log dirs need mosquitto ownership for the daemon to write
chown -R mosquitto:mosquitto /mosquitto/data /mosquitto/log 2>/dev/null || true


exec "$@"
