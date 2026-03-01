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

# Set permissions
user="$(id -u)"
if [ "$user" = '0' ]; then
	[ -d "/mosquitto" ] && chown -R mosquitto:mosquitto /mosquitto || true
fi


exec "$@"
