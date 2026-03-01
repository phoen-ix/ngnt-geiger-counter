import fcntl
import hashlib
import hmac
import os
import smtplib
import ssl
from email.mime.text import MIMEText
from functools import wraps

import pymysql
from flask import redirect, session, url_for


# ── Database ─────────────────────────────────────────────────────────────────

def get_db():
    return pymysql.connect(
        host='mariadb',
        port=3306,
        user=os.environ['MARIADB_USER'],
        password=os.environ['MARIADB_PASSWORD'],
        database=os.environ['MARIADB_DATABASE'],
        charset='utf8mb4',
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
    )


def get_settings(db):
    with db.cursor() as cur:
        cur.execute("SELECT `key`, `value` FROM settings")
        return {row['key']: row['value'] for row in cur.fetchall()}


# ── Auth decorators ──────────────────────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        if session.get('role') != 'admin':
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return decorated


# ── Credential derivation ────────────────────────────────────────────────────
# Must match firmware: geiger_counter_v2.0.ino

def derive_mqtt_credentials(mac_address: str, pepper: str) -> tuple[str, str]:
    mac_hex = mac_address.replace(':', '').replace('-', '').lower()
    username = f'geiger_{mac_hex[-6:]}'
    password = hmac.new(
        pepper.encode('ascii'),
        mac_hex.encode('ascii'),
        hashlib.sha256,
    ).hexdigest()[:16]
    return username, password


# ── Mosquitto provisioning ───────────────────────────────────────────────────

MOSQUITTO_CONFIG_DIR = os.environ.get('MOSQUITTO_CONFIG_DIR', '/app/mosquitto-config')


def provision_device(device_id: str, mqtt_password: str) -> None:
    devices_conf = os.path.join(MOSQUITTO_CONFIG_DIR, 'devices.conf')
    reload_flag = os.path.join(MOSQUITTO_CONFIG_DIR, '.reload')

    with open(devices_conf, 'a+') as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.seek(0)
        lines = f.readlines()
        for line in lines:
            parts = line.strip().split()
            if parts and parts[0] == device_id:
                fcntl.flock(f, fcntl.LOCK_UN)
                return  # already provisioned
        f.write(f'{device_id} {mqtt_password}\n')
        fcntl.flock(f, fcntl.LOCK_UN)

    # Signal Mosquitto to reload
    with open(reload_flag, 'w') as f:
        f.write('1')


def unprovision_device(device_id: str) -> None:
    devices_conf = os.path.join(MOSQUITTO_CONFIG_DIR, 'devices.conf')
    reload_flag = os.path.join(MOSQUITTO_CONFIG_DIR, '.reload')

    with open(devices_conf, 'r+') as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        lines = f.readlines()
        f.seek(0)
        f.truncate()
        for line in lines:
            parts = line.strip().split()
            if parts and parts[0] == device_id:
                continue
            f.write(line)
        fcntl.flock(f, fcntl.LOCK_UN)

    with open(reload_flag, 'w') as f:
        f.write('1')


# ── Email ────────────────────────────────────────────────────────────────────

def send_password_reset_email(email: str, token: str, settings: dict) -> bool:
    smtp_host = settings.get('smtp_host', '')
    if not smtp_host:
        return False

    smtp_port = int(settings.get('smtp_port', '587'))
    smtp_user = settings.get('smtp_user', '')
    smtp_password = settings.get('smtp_password', '')
    smtp_from = settings.get('smtp_from', smtp_user)
    smtp_tls = settings.get('smtp_tls', '1') == '1'
    site_name = os.environ.get('SITE_NAME', 'NGNT Geiger Counter')

    base_url = settings.get('base_url', '').rstrip('/')
    if not base_url:
        base_url = 'http://localhost:8000'
    reset_url = f'{base_url}/reset-password/{token}'

    body = (
        f'You requested a password reset for your {site_name} account.\n\n'
        f'Click the link below to set a new password:\n{reset_url}\n\n'
        f'This link expires in 1 hour.\n\n'
        f'If you did not request this, ignore this email.'
    )

    msg = MIMEText(body)
    msg['Subject'] = f'Password Reset — {site_name}'
    msg['From'] = smtp_from
    msg['To'] = email

    try:
        if smtp_tls:
            server = smtplib.SMTP(smtp_host, smtp_port)
            server.starttls(context=ssl.create_default_context())
        else:
            server = smtplib.SMTP(smtp_host, smtp_port)
        if smtp_user:
            server.login(smtp_user, smtp_password)
        server.sendmail(smtp_from, [email], msg.as_string())
        server.quit()
        return True
    except Exception as e:
        print(f'[email] send failed: {e}')
        return False
