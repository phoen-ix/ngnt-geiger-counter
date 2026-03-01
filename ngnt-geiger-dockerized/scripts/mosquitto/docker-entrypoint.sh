#!/bin/ash
set -e

mosquitto_passwd -b /mosquitto/config/passwd.txt $MQTT_GEIGER_USER $MQTT_GEIGER_USERPW
mosquitto_passwd -b /mosquitto/config/passwd.txt $MQTT_PYTHON_USER $MQTT_PYTHON_USERPW

# Set permissions
user="$(id -u)"
if [ "$user" = '0' ]; then
	[ -d "/mosquitto" ] && chown -R mosquitto:mosquitto /mosquitto || true
fi


exec "$@"
