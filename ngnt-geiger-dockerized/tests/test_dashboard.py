"""Tests for the dashboard route — visibility, filtering, empty state."""

from datetime import datetime, timezone

from tests.testutils import queue_before_request, queue_settings


def _make_device(device_id='geiger_aabbcc', owner='testuser', status='online',
                 last_seen=None, alert_threshold=None, display_name=None):
    """Build a device dict matching the DB SELECT d.*, u.username AS owner."""
    return {
        'id': 1,
        'user_id': 1,
        'device_id': device_id,
        'mac_address': 'AA:BB:CC:DD:EE:FF',
        'display_name': display_name,
        'mqtt_password': 'secret',
        'status': status,
        'last_seen': last_seen or datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S'),
        'cpm_factor': None,
        'alert_threshold': alert_threshold,
        'provisioned': True,
        'created_at': '2026-01-01 00:00:00',
        'owner': owner,
    }


def _make_measurement(device_id='geiger_aabbcc', cpm=42, usvh=0.2394):
    return {
        'device_id': device_id,
        'measured_at': datetime(2026, 3, 1, 12, 0, 0),
        'cpm': cpm,
        'usvh': usvh,
    }


def _queue_dashboard_responses(cursor, devices, measurements=None):
    """Queue the standard dashboard DB calls after settings."""
    # Devices query (fetchall)
    cursor.add_response(fetchall=devices)

    if devices:
        # Latest measurement (fetchone)
        latest = measurements[0] if measurements else None
        cursor.add_response(fetchone=latest)
        # Chart data (fetchall)
        cursor.add_response(fetchall=measurements or [])
        # Recent measurements (fetchall)
        cursor.add_response(fetchall=measurements or [])


# ── Anonymous visibility ────────────────────────────────────────────────────

def test_dashboard_anonymous_public_only(client, mock_cursor):
    """Anonymous visitors should only see public devices."""
    queue_settings(mock_cursor)
    dev = _make_device(device_id='geiger_public', owner='pubuser')
    _queue_dashboard_responses(mock_cursor, [dev])

    resp = client.get('/')
    assert resp.status_code == 200
    # Verify the query used was the public-only query
    queries = [sql for sql, _ in mock_cursor.execute_log if 'public' in sql.lower()]
    assert len(queries) > 0


# ── Logged-in visibility ───────────────────────────────────────────────────

def test_dashboard_logged_in_own_and_public(auth_client, mock_cursor):
    """Logged-in user sees own devices plus public devices."""
    queue_before_request(mock_cursor)
    queue_settings(mock_cursor)

    own_dev = _make_device(device_id='geiger_own', owner='testuser')
    pub_dev = _make_device(device_id='geiger_pub', owner='other')
    _queue_dashboard_responses(mock_cursor, [own_dev, pub_dev])

    resp = auth_client.get('/')
    assert resp.status_code == 200
    assert b'geiger_own' in resp.data
    assert b'geiger_pub' in resp.data


# ── Admin visibility ────────────────────────────────────────────────────────

def test_dashboard_admin_sees_all(admin_client, mock_cursor):
    """Admin sees all devices regardless of public flag."""
    queue_before_request(mock_cursor, role='admin')
    queue_settings(mock_cursor)

    dev1 = _make_device(device_id='geiger_private', owner='privateuser')
    dev2 = _make_device(device_id='geiger_other', owner='other')
    _queue_dashboard_responses(mock_cursor, [dev1, dev2])

    resp = admin_client.get('/')
    assert resp.status_code == 200
    # Admin query should NOT have a WHERE public = TRUE filter
    device_queries = [sql for sql, _ in mock_cursor.execute_log
                      if 'FROM devices' in sql and 'public' not in sql.lower()]
    assert len(device_queries) > 0


# ── Empty state ─────────────────────────────────────────────────────────────

def test_dashboard_empty_state(client, mock_cursor):
    """Dashboard with no visible devices shows empty state."""
    queue_settings(mock_cursor)
    _queue_dashboard_responses(mock_cursor, [])

    resp = client.get('/')
    assert resp.status_code == 200


# ── Range filter ────────────────────────────────────────────────────────────

def test_dashboard_range_filter_1h(client, mock_cursor):
    """?range=1h uses 1 HOUR interval."""
    queue_settings(mock_cursor)
    dev = _make_device()
    _queue_dashboard_responses(mock_cursor, [dev], [_make_measurement()])

    resp = client.get('/?range=1h')
    assert resp.status_code == 200
    interval_queries = [sql for sql, _ in mock_cursor.execute_log if '1 HOUR' in sql]
    assert len(interval_queries) > 0


def test_dashboard_range_invalid_defaults_24h(client, mock_cursor):
    """Invalid range parameter defaults to 24h."""
    queue_settings(mock_cursor)
    dev = _make_device()
    _queue_dashboard_responses(mock_cursor, [dev], [_make_measurement()])

    resp = client.get('/?range=bogus')
    assert resp.status_code == 200
    interval_queries = [sql for sql, _ in mock_cursor.execute_log if '24 HOUR' in sql]
    assert len(interval_queries) > 0


# ── Device filter ───────────────────────────────────────────────────────────

def test_dashboard_device_filter(client, mock_cursor):
    """?device=geiger_aabbcc filters to that device."""
    queue_settings(mock_cursor)
    dev = _make_device(device_id='geiger_aabbcc')
    _queue_dashboard_responses(mock_cursor, [dev], [_make_measurement()])

    resp = client.get('/?device=geiger_aabbcc')
    assert resp.status_code == 200


def test_dashboard_device_filter_invalid_ignored(client, mock_cursor):
    """Invalid device param falls back to 'all'."""
    queue_settings(mock_cursor)
    dev = _make_device(device_id='geiger_aabbcc')
    _queue_dashboard_responses(mock_cursor, [dev], [_make_measurement()])

    resp = client.get('/?device=nonexistent')
    assert resp.status_code == 200
