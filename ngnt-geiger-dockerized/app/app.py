import os
import re
import secrets
import time
from datetime import datetime, timezone

from flask import (Flask, flash, redirect, render_template, request, session,
                   url_for)
from werkzeug.security import check_password_hash, generate_password_hash

from helpers import (admin_required, derive_mqtt_credentials, get_db,
                     get_settings, login_required, provision_device,
                     send_password_reset_email, unprovision_device)

app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'dev-key-change-me')


# ── Template context ─────────────────────────────────────────────────────────

@app.context_processor
def inject_site_name():
    try:
        db = get_db()
        settings = get_settings(db)
        db.close()
        return {'site_name': settings.get('site_name', 'NGNT Geiger Counter')}
    except Exception:
        return {'site_name': 'NGNT Geiger Counter'}


# ── Admin bootstrap ─────────────────────────────────────────────────────────

def bootstrap_admin():
    db = get_db()
    with db.cursor() as cur:
        cur.execute("SELECT COUNT(*) AS cnt FROM users")
        if cur.fetchone()['cnt'] == 0:
            password = secrets.token_urlsafe(20)
            pw_hash = generate_password_hash(password)
            cur.execute(
                "INSERT INTO users (username, password_hash, role) VALUES (%s, %s, 'admin')",
                ('admin', pw_hash),
            )
            print(f'\n{"="*60}', flush=True)
            print(f'  Admin account created', flush=True)
            print(f'  Username: admin', flush=True)
            print(f'  Password: {password}', flush=True)
            print(f'{"="*60}\n', flush=True)

            os.makedirs('/app/data', exist_ok=True)
            with open('/app/data/admin_initial_password.txt', 'w') as f:
                f.write(f'Username: admin\nPassword: {password}\n')
    db.close()


# Run bootstrap with retries (DB might not be ready)
def bootstrap_admin_with_retry():
    print('[bootstrap] waiting for database...', flush=True)
    for attempt in range(30):
        try:
            bootstrap_admin()
            return
        except Exception:
            time.sleep(2)
    print('[bootstrap] gave up waiting for database after 60s', flush=True)


bootstrap_admin_with_retry()


# ── Timezone helper ──────────────────────────────────────────────────────────

def utc_to_local(utc_str: str, tz_name: str) -> str:
    from zoneinfo import ZoneInfo
    dt = datetime.strptime(utc_str, '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)
    local = dt.astimezone(ZoneInfo(tz_name))
    return local.strftime('%Y-%m-%d %H:%M:%S')


def is_device_online(device: dict, offline_timeout: int) -> bool:
    if device.get('status') == 'offline':
        return False
    last_seen = device.get('last_seen')
    if not last_seen:
        return False
    if isinstance(last_seen, str):
        last_seen = datetime.strptime(last_seen, '%Y-%m-%d %H:%M:%S')
    age_min = (datetime.utcnow() - last_seen).total_seconds() / 60
    return age_min <= offline_timeout


# ── Auth routes ──────────────────────────────────────────────────────────────

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        db = get_db()
        with db.cursor() as cur:
            cur.execute("SELECT * FROM users WHERE username = %s", (username,))
            user = cur.fetchone()
        db.close()
        if user and check_password_hash(user['password_hash'], password):
            session['user_id'] = user['id']
            session['username'] = user['username']
            session['role'] = user['role']
            flash('Logged in.', 'success')
            return redirect(url_for('dashboard'))
        flash('Invalid username or password.', 'error')
    return render_template('login.html')


@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        email = request.form.get('email', '').strip() or None
        password = request.form.get('password', '')
        password2 = request.form.get('password2', '')

        if not username or not password:
            flash('Username and password are required.', 'error')
        elif len(username) < 3 or len(username) > 50:
            flash('Username must be 3-50 characters.', 'error')
        elif len(password) < 6:
            flash('Password must be at least 6 characters.', 'error')
        elif password != password2:
            flash('Passwords do not match.', 'error')
        else:
            db = get_db()
            with db.cursor() as cur:
                cur.execute("SELECT id FROM users WHERE username = %s", (username,))
                if cur.fetchone():
                    flash('Username already taken.', 'error')
                else:
                    pw_hash = generate_password_hash(password)
                    cur.execute(
                        "INSERT INTO users (username, email, password_hash) VALUES (%s, %s, %s)",
                        (username, email, pw_hash),
                    )
                    flash('Account created. Please log in.', 'success')
                    db.close()
                    return redirect(url_for('login'))
            db.close()
    return render_template('register.html')


@app.route('/logout')
def logout():
    session.clear()
    flash('Logged out.', 'success')
    return redirect(url_for('dashboard'))


@app.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        db = get_db()
        settings = get_settings(db)
        with db.cursor() as cur:
            cur.execute("SELECT id, email FROM users WHERE username = %s", (username,))
            user = cur.fetchone()
            if user and user['email']:
                token = secrets.token_hex(32)
                cur.execute(
                    "INSERT INTO password_resets (user_id, token, expires_at) "
                    "VALUES (%s, %s, DATE_ADD(NOW(), INTERVAL 1 HOUR))",
                    (user['id'], token),
                )
                send_password_reset_email(user['email'], token, settings)
        db.close()
        # Always show success to prevent user enumeration
        flash('If that account exists and has an email, a reset link has been sent.', 'success')
        return redirect(url_for('login'))
    return render_template('forgot_password.html')


@app.route('/reset-password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    db = get_db()
    with db.cursor() as cur:
        cur.execute(
            "SELECT pr.*, u.username FROM password_resets pr "
            "JOIN users u ON pr.user_id = u.id "
            "WHERE pr.token = %s AND pr.used = FALSE AND pr.expires_at > NOW()",
            (token,),
        )
        reset = cur.fetchone()
    if not reset:
        db.close()
        flash('Invalid or expired reset link.', 'error')
        return redirect(url_for('login'))

    if request.method == 'POST':
        password = request.form.get('password', '')
        password2 = request.form.get('password2', '')
        if len(password) < 6:
            flash('Password must be at least 6 characters.', 'error')
        elif password != password2:
            flash('Passwords do not match.', 'error')
        else:
            pw_hash = generate_password_hash(password)
            with db.cursor() as cur:
                cur.execute("UPDATE users SET password_hash = %s WHERE id = %s", (pw_hash, reset['user_id']))
                cur.execute("UPDATE password_resets SET used = TRUE WHERE id = %s", (reset['id'],))
            db.close()
            flash('Password reset. Please log in.', 'success')
            return redirect(url_for('login'))

    db.close()
    return render_template('reset_password.html')


# ── Dashboard ────────────────────────────────────────────────────────────────

RANGE_OPTIONS = {
    '1h':  ('1 HOUR',  'Last hour'),
    '6h':  ('6 HOUR',  'Last 6 hours'),
    '24h': ('24 HOUR', 'Last 24 hours'),
    '7d':  ('7 DAY',   'Last 7 days'),
}


@app.route('/')
def dashboard():
    range_key = request.args.get('range', '24h')
    if range_key not in RANGE_OPTIONS:
        range_key = '24h'
    sql_interval, range_label = RANGE_OPTIONS[range_key]
    device_param = request.args.get('device', 'all')

    db = get_db()
    settings = get_settings(db)
    display_tz = settings.get('display_timezone', 'Europe/Vienna')
    offline_timeout = int(settings.get('offline_timeout_minutes', '5'))
    default_alert = float(settings.get('default_alert_threshold', '0.5'))

    # Determine visible devices based on auth
    with db.cursor() as cur:
        user_id = session.get('user_id')
        role = session.get('role')

        if role == 'admin':
            cur.execute(
                "SELECT d.*, u.username AS owner FROM devices d "
                "JOIN users u ON d.user_id = u.id ORDER BY d.device_id"
            )
        elif user_id:
            cur.execute(
                "SELECT d.*, u.username AS owner FROM devices d "
                "JOIN users u ON d.user_id = u.id "
                "WHERE d.user_id = %s OR u.public = TRUE ORDER BY d.device_id",
                (user_id,),
            )
        else:
            cur.execute(
                "SELECT d.*, u.username AS owner FROM devices d "
                "JOIN users u ON d.user_id = u.id "
                "WHERE u.public = TRUE ORDER BY d.device_id"
            )
        visible_devices = cur.fetchall()

    visible_ids = [d['device_id'] for d in visible_devices]
    device_names = {d['device_id']: d['display_name'] for d in visible_devices}
    device_alerts = {d['device_id']: d['alert_threshold'] for d in visible_devices}
    device_online_map = {d['device_id']: is_device_online(d, offline_timeout) for d in visible_devices}

    def device_label(dev_id):
        name = device_names.get(dev_id)
        return name if name else dev_id

    # Validate device param
    device = device_param if device_param in visible_ids else 'all'
    device_qs = f'&device={device}' if device != 'all' else ''

    latest = None
    chart_data = []
    recent = []

    if visible_ids:
        with db.cursor() as cur:
            placeholders = ','.join(['%s'] * len(visible_ids))
            filter_ids = [device] if device != 'all' else visible_ids

            if not filter_ids:
                filter_ids = visible_ids

            ph = ','.join(['%s'] * len(filter_ids))

            # Latest measurement
            cur.execute(
                f"SELECT device_id, measured_at, cpm, usvh FROM measurements "
                f"WHERE device_id IN ({ph}) ORDER BY measured_at DESC LIMIT 1",
                filter_ids,
            )
            latest = cur.fetchone()

            # Chart data
            cur.execute(
                f"SELECT measured_at, cpm, usvh FROM measurements "
                f"WHERE device_id IN ({ph}) AND measured_at >= NOW() - INTERVAL {sql_interval} "
                f"ORDER BY measured_at ASC",
                filter_ids,
            )
            chart_data = cur.fetchall()

            # Recent measurements
            cur.execute(
                f"SELECT device_id, measured_at, cpm, usvh FROM measurements "
                f"WHERE device_id IN ({ph}) AND measured_at >= NOW() - INTERVAL {sql_interval} "
                f"ORDER BY measured_at DESC LIMIT 100",
                filter_ids,
            )
            recent = cur.fetchall()

    db.close()

    # Convert timestamps for display
    if latest:
        ma = latest['measured_at']
        latest['measured_at_local'] = utc_to_local(
            ma.strftime('%Y-%m-%d %H:%M:%S') if isinstance(ma, datetime) else str(ma),
            display_tz,
        )

    for row in chart_data:
        ma = row['measured_at']
        row['measured_at_local'] = utc_to_local(
            ma.strftime('%Y-%m-%d %H:%M:%S') if isinstance(ma, datetime) else str(ma),
            display_tz,
        )

    for row in recent:
        ma = row['measured_at']
        row['measured_at_local'] = utc_to_local(
            ma.strftime('%Y-%m-%d %H:%M:%S') if isinstance(ma, datetime) else str(ma),
            display_tz,
        )

    chart_labels = [r['measured_at_local'] for r in chart_data]
    chart_cpm = [int(r['cpm']) for r in chart_data]
    chart_usvh = [float(r['usvh']) for r in chart_data]

    # Alert threshold
    active_alert = default_alert
    if device != 'all' and device_alerts.get(device) is not None:
        active_alert = float(device_alerts[device])
    elif device == 'all' and latest:
        lid = latest['device_id']
        if device_alerts.get(lid) is not None:
            active_alert = float(device_alerts[lid])

    return render_template('dashboard.html',
        range_options=RANGE_OPTIONS,
        range=range_key,
        range_label=range_label,
        device=device,
        device_qs=device_qs,
        visible_devices=visible_devices,
        device_label=device_label,
        device_online=device_online_map,
        latest=latest,
        chart_data=chart_data,
        chart_labels=chart_labels,
        chart_cpm=chart_cpm,
        chart_usvh=chart_usvh,
        recent=recent,
        active_alert=active_alert,
    )


# ── Device management ────────────────────────────────────────────────────────

@app.route('/devices', methods=['GET', 'POST'])
@login_required
def devices():
    db = get_db()
    user_id = session['user_id']

    with db.cursor() as cur:
        cur.execute("SELECT pepper FROM users WHERE id = %s", (user_id,))
        user_pepper = cur.fetchone()['pepper']

    if request.method == 'POST':
        action = request.form.get('action')

        if action == 'add_device':
            mac = request.form.get('mac_address', '').strip().upper()
            display_name = request.form.get('display_name', '').strip() or None

            if not re.match(r'^([0-9A-F]{2}:){5}[0-9A-F]{2}$', mac):
                flash('Invalid MAC address format. Use AA:BB:CC:DD:EE:FF.', 'error')
            elif not user_pepper:
                flash('Set a pepper in your account settings first.', 'error')
            else:
                device_id, mqtt_password = derive_mqtt_credentials(mac, user_pepper)
                with db.cursor() as cur:
                    cur.execute("SELECT id FROM devices WHERE device_id = %s", (device_id,))
                    if cur.fetchone():
                        flash(f'Device {device_id} already registered.', 'error')
                    else:
                        cur.execute(
                            "INSERT INTO devices (user_id, device_id, mac_address, display_name, mqtt_password, provisioned) "
                            "VALUES (%s, %s, %s, %s, %s, TRUE)",
                            (user_id, device_id, mac, display_name, mqtt_password),
                        )
                        provision_device(device_id, mqtt_password)
                        flash(f'Device {device_id} registered and provisioned.', 'success')

        elif action == 'update_device':
            device_db_id = request.form.get('device_db_id')
            display_name = request.form.get('display_name', '').strip() or None
            with db.cursor() as cur:
                cur.execute(
                    "UPDATE devices SET display_name = %s WHERE id = %s AND user_id = %s",
                    (display_name, device_db_id, user_id),
                )
            flash('Device updated.', 'success')

        elif action == 'delete_device':
            device_db_id = request.form.get('device_db_id')
            with db.cursor() as cur:
                cur.execute(
                    "SELECT device_id FROM devices WHERE id = %s AND user_id = %s",
                    (device_db_id, user_id),
                )
                dev = cur.fetchone()
                if dev:
                    unprovision_device(dev['device_id'])
                    cur.execute("DELETE FROM devices WHERE id = %s AND user_id = %s", (device_db_id, user_id))
                    flash('Device deleted and unprovisioned.', 'success')

        db.close()
        return redirect(url_for('devices'))

    # GET
    with db.cursor() as cur:
        cur.execute("SELECT * FROM devices WHERE user_id = %s ORDER BY device_id", (user_id,))
        user_devices = cur.fetchall()

    db.close()
    return render_template('devices.html', devices=user_devices, user_pepper=user_pepper)


# ── Account ──────────────────────────────────────────────────────────────────

@app.route('/account', methods=['GET', 'POST'])
@login_required
def account():
    db = get_db()
    user_id = session['user_id']

    if request.method == 'POST':
        action = request.form.get('action')

        if action == 'update_profile':
            email = request.form.get('email', '').strip() or None
            pepper = request.form.get('pepper', '').strip() or None
            public = request.form.get('public', '0') == '1'
            with db.cursor() as cur:
                cur.execute(
                    "UPDATE users SET email = %s, pepper = %s, `public` = %s WHERE id = %s",
                    (email, pepper, public, user_id),
                )
            flash('Profile updated.', 'success')

        elif action == 'change_password':
            current = request.form.get('current_password', '')
            new_pw = request.form.get('new_password', '')
            new_pw2 = request.form.get('new_password2', '')

            with db.cursor() as cur:
                cur.execute("SELECT password_hash FROM users WHERE id = %s", (user_id,))
                user = cur.fetchone()

            if not check_password_hash(user['password_hash'], current):
                flash('Current password is incorrect.', 'error')
            elif len(new_pw) < 6:
                flash('New password must be at least 6 characters.', 'error')
            elif new_pw != new_pw2:
                flash('New passwords do not match.', 'error')
            else:
                pw_hash = generate_password_hash(new_pw)
                with db.cursor() as cur:
                    cur.execute("UPDATE users SET password_hash = %s WHERE id = %s", (pw_hash, user_id))
                # Remove initial password file if admin changes password
                try:
                    os.remove('/app/data/admin_initial_password.txt')
                except FileNotFoundError:
                    pass
                flash('Password changed.', 'success')

        db.close()
        return redirect(url_for('account'))

    # GET
    with db.cursor() as cur:
        cur.execute("SELECT * FROM users WHERE id = %s", (user_id,))
        user = cur.fetchone()

    db.close()
    return render_template('account.html', user=user)


# ── Admin ────────────────────────────────────────────────────────────────────

SETTINGS_KEYS = [
    'site_name', 'display_timezone', 'offline_timeout_minutes',
    'default_cpm_factor', 'default_alert_threshold',
]
SMTP_KEYS = [
    'smtp_host', 'smtp_port', 'smtp_user', 'smtp_password', 'smtp_from', 'smtp_tls',
]


@app.route('/admin', methods=['GET', 'POST'])
@admin_required
def admin():
    db = get_db()

    if request.method == 'POST':
        action = request.form.get('action')

        if action == 'save_settings':
            with db.cursor() as cur:
                stmt = "UPDATE settings SET `value` = %s WHERE `key` = %s"
                for key in SETTINGS_KEYS:
                    val = request.form.get(key)
                    if val is not None:
                        cur.execute(stmt, (val.strip(), key))
            flash('Settings saved.', 'success')

        elif action == 'save_smtp':
            with db.cursor() as cur:
                stmt = "UPDATE settings SET `value` = %s WHERE `key` = %s"
                for key in SMTP_KEYS:
                    val = request.form.get(key)
                    if val is not None:
                        cur.execute(stmt, (val.strip(), key))
            flash('SMTP settings saved.', 'success')

        elif action == 'toggle_role':
            target_id = int(request.form.get('target_user_id'))
            if target_id != session['user_id']:
                with db.cursor() as cur:
                    cur.execute("SELECT role FROM users WHERE id = %s", (target_id,))
                    u = cur.fetchone()
                    if u:
                        new_role = 'user' if u['role'] == 'admin' else 'admin'
                        cur.execute("UPDATE users SET role = %s WHERE id = %s", (new_role, target_id))
                flash('Role updated.', 'success')

        elif action == 'toggle_public':
            target_id = int(request.form.get('target_user_id'))
            with db.cursor() as cur:
                cur.execute("UPDATE users SET `public` = NOT `public` WHERE id = %s", (target_id,))
            flash('Visibility updated.', 'success')

        elif action == 'delete_user':
            target_id = int(request.form.get('target_user_id'))
            if target_id != session['user_id']:
                with db.cursor() as cur:
                    # Unprovision all devices
                    cur.execute("SELECT device_id FROM devices WHERE user_id = %s", (target_id,))
                    for dev in cur.fetchall():
                        unprovision_device(dev['device_id'])
                    cur.execute("DELETE FROM users WHERE id = %s", (target_id,))
                flash('User deleted.', 'success')

        db.close()
        return redirect(url_for('admin'))

    # GET
    settings = get_settings(db)
    with db.cursor() as cur:
        cur.execute(
            "SELECT u.*, (SELECT COUNT(*) FROM devices d WHERE d.user_id = u.id) AS device_count "
            "FROM users u ORDER BY u.id"
        )
        users = cur.fetchall()

    db.close()
    return render_template('admin.html', settings=settings, users=users)
