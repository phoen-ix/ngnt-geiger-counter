import base64
import fcntl
import hashlib
import hmac
import logging
import os
from functools import wraps

import pymysql
from dbutils.pooled_db import PooledDB
from flask import redirect, session, url_for

from mailer import send_mail

logger = logging.getLogger(__name__)


# ── Database ─────────────────────────────────────────────────────────────────

_pool = None


def _get_pool():
    global _pool
    if _pool is None:
        _pool = PooledDB(
            creator=pymysql,
            maxconnections=10,
            mincached=2,
            maxcached=5,
            host='mariadb',
            port=3306,
            user=os.environ['MARIADB_USER'],
            password=os.environ['MARIADB_PASSWORD'],
            database=os.environ['MARIADB_DATABASE'],
            charset='utf8mb4',
            cursorclass=pymysql.cursors.DictCursor,
            autocommit=True,
        )
    return _pool


def get_db():
    return _get_pool().connection()


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
# Must match firmware: geiger_counter_v2.2.ino

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
# devices.conf holds "<device id>:<hash>" lines in Mosquitto's password-file format, which
# the Mosquitto entrypoint copies into passwd.txt. $7$ is PBKDF2-SHA512 with a 12-byte salt
# (mosquitto_passwd -H sha512-pbkdf2); the broker checks it on every CONNECT.
MOSQUITTO_PBKDF2_ROUNDS = 10_000


def mosquitto_password_hash(password: str) -> str:
    salt = os.urandom(12)
    digest = hashlib.pbkdf2_hmac('sha512', password.encode(), salt, MOSQUITTO_PBKDF2_ROUNDS)
    return (f'$7${MOSQUITTO_PBKDF2_ROUNDS}${base64.b64encode(salt).decode()}'
            f'${base64.b64encode(digest).decode()}')


def _device_of(line: str) -> str:
    """Device id of a devices.conf line ("id:hash", or the older plaintext "id password")."""
    return line.strip().split(':', 1)[0].split(' ', 1)[0]


def provision_device(device_id: str, mqtt_password: str) -> None:
    devices_conf = os.path.join(MOSQUITTO_CONFIG_DIR, 'devices.conf')
    reload_flag = os.path.join(MOSQUITTO_CONFIG_DIR, '.reload')

    with open(devices_conf, 'a+') as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.seek(0)
        lines = f.readlines()
        for line in lines:
            if _device_of(line) == device_id:
                fcntl.flock(f, fcntl.LOCK_UN)
                return  # already provisioned
        f.write(f'{device_id}:{mosquitto_password_hash(mqtt_password)}\n')
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
            if _device_of(line) == device_id:
                continue
            f.write(line)
        fcntl.flock(f, fcntl.LOCK_UN)

    with open(reload_flag, 'w') as f:
        f.write('1')


def hash_legacy_device_passwords() -> None:
    """Rewrite the plaintext "id password" lines of older versions as "id:hash"."""
    devices_conf = os.path.join(MOSQUITTO_CONFIG_DIR, 'devices.conf')
    try:
        f = open(devices_conf, 'r+')
    except FileNotFoundError:
        return
    with f:
        fcntl.flock(f, fcntl.LOCK_EX)
        lines = f.readlines()
        hashed = [f'{parts[0]}:{mosquitto_password_hash(parts[1])}\n'
                  if len(parts := line.split()) == 2 else line for line in lines]
        if hashed != lines:
            f.seek(0)
            f.truncate()
            f.writelines(hashed)
        fcntl.flock(f, fcntl.LOCK_UN)


# ── Email ────────────────────────────────────────────────────────────────────

def send_password_reset_email(email: str, token: str, settings: dict) -> bool:
    base_url = settings.get('base_url', '').rstrip('/')
    if not base_url:
        if settings.get('smtp_host'):
            logger.error('reset mail not sent: Base URL is not set, so the link would lead nowhere')
        return False
    reset_url = f'{base_url}/reset-password/{token}'
    site_name = os.environ.get('SITE_NAME', 'NGNT Geiger Counter')
    body = (
        f'You requested a password reset for your {site_name} account.\n\n'
        f'Click the link below to set a new password:\n{reset_url}\n\n'
        f'This link expires in 1 hour.\n\n'
        f'If you did not request this, ignore this email.'
    )
    return send_mail(email, f'Password Reset — {site_name}', body, settings)
