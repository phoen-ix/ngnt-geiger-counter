"""Tests for device management routes — CRUD + provisioning."""

import re
from unittest.mock import patch

import pytest

from tests.testutils import queue_before_request, queue_settings

# ── GET /devices ────────────────────────────────────────────────────────────

def test_devices_get_lists_devices(auth_client, mock_cursor):
    queue_before_request(mock_cursor)
    # SELECT pepper
    mock_cursor.add_response(fetchone={'pepper': 'mysecret'})
    queue_settings(mock_cursor)
    # SELECT devices
    mock_cursor.add_response(fetchall=[
        {'id': 1, 'device_id': 'geiger_aabbcc', 'display_name': 'Kitchen',
         'mac_address': 'AA:BB:CC:DD:EE:FF', 'status': 'online',
         'last_seen': '2026-03-01 12:00:00', 'provisioned': True},
    ])

    resp = auth_client.get('/devices')
    assert resp.status_code == 200
    assert b'geiger_aabbcc' in resp.data
    assert b'Kitchen' in resp.data
    # last_seen is stored in UTC and shown in display_timezone (Europe/Vienna)
    assert b'2026-03-01 13:00:00' in resp.data


def test_devices_get_empty(auth_client, mock_cursor):
    queue_before_request(mock_cursor)
    mock_cursor.add_response(fetchone={'pepper': 'mysecret'})
    queue_settings(mock_cursor)
    mock_cursor.add_response(fetchall=[])

    resp = auth_client.get('/devices')
    assert resp.status_code == 200
    assert b'No devices registered' in resp.data


# ── Add device ──────────────────────────────────────────────────────────────

def test_add_device_success(auth_client, mock_cursor):
    queue_before_request(mock_cursor)
    # SELECT pepper
    mock_cursor.add_response(fetchone={'pepper': 'mysecret'})
    # SELECT duplicate check → not found
    mock_cursor.add_response(fetchone=None)
    # INSERT (no fetch)

    with patch('app.provision_device') as mock_prov:
        resp = auth_client.post('/devices', data={
            'action': 'add_device',
            'mac_address': 'AA:BB:CC:DD:EE:FF',
            'display_name': 'Kitchen Counter',
        })

    assert resp.status_code == 302
    mock_prov.assert_called_once()
    device_id, password = mock_prov.call_args[0]
    assert device_id == 'geiger_ddeeff'
    # Only Mosquitto gets the password (as a hash in devices.conf), never the DB.
    [(sql, params)] = [q for q in mock_cursor.execute_log if q[0].startswith('INSERT INTO devices')]
    assert 'mqtt_password' not in sql
    assert password not in params


def test_startup_drops_the_plaintext_password_column(app_instance, mock_cursor):
    import app as app_module
    app_module.migrate_schema()
    assert ('ALTER TABLE devices DROP COLUMN IF EXISTS mqtt_password', None) in mock_cursor.execute_log
    # And the column the subscriber's alert mail keeps its state in.
    assert any(sql.startswith('ALTER TABLE devices ADD COLUMN IF NOT EXISTS alerted ')
               for sql, _ in mock_cursor.execute_log)
    # An older database gets the settings rows dbinit.sql seeds only on a new one.
    assert any('INSERT IGNORE INTO settings' in sql and 'smtp_ehlo_name' in sql for sql, _ in mock_cursor.execute_log)


# A refused add shows the page at once, the form keeping what was typed: before_request +
# pepper (+ duplicate check) + settings + device list, no redirect.

def test_add_device_invalid_mac(auth_client, mock_cursor):
    queue_before_request(mock_cursor)
    mock_cursor.add_response(fetchone={'pepper': 'mysecret'})
    queue_settings(mock_cursor)
    mock_cursor.add_response(fetchall=[])

    resp = auth_client.post('/devices', data={
        'action': 'add_device',
        'mac_address': 'not-a-mac',
        'display_name': 'Kitchen',
    })
    assert resp.status_code == 200
    assert b'Invalid MAC address' in resp.data
    assert b'value="not-a-mac"' in resp.data
    assert b'value="Kitchen"' in resp.data


def test_add_device_no_pepper(auth_client, mock_cursor):
    queue_before_request(mock_cursor)
    mock_cursor.add_response(fetchone={'pepper': None})
    queue_settings(mock_cursor)

    resp = auth_client.post('/devices', data={
        'action': 'add_device',
        'mac_address': 'AA:BB:CC:DD:EE:FF',
        'display_name': '',
    })
    assert b'Set a pepper' in resp.data


def test_add_device_duplicate(auth_client, mock_cursor):
    queue_before_request(mock_cursor)
    mock_cursor.add_response(fetchone={'pepper': 'mysecret'})
    mock_cursor.add_response(fetchone={'id': 99})
    queue_settings(mock_cursor)
    mock_cursor.add_response(fetchall=[])

    resp = auth_client.post('/devices', data={
        'action': 'add_device',
        'mac_address': 'aa:bb:cc:dd:ee:ff',
        'display_name': '',
    })
    assert b'already registered' in resp.data
    assert b'value="aa:bb:cc:dd:ee:ff"' in resp.data


# ── Update device ───────────────────────────────────────────────────────────

def test_update_device_success(auth_client, mock_cursor):
    queue_before_request(mock_cursor)
    mock_cursor.add_response(fetchone={'pepper': 'mysecret'})
    # SELECT the user's device → found; UPDATE (no fetch)
    mock_cursor.add_response(fetchone={'device_id': 'geiger_aabbcc'})

    resp = auth_client.post('/devices', data={
        'action': 'update_device',
        'device_db_id': '1',
        'display_name': 'New Name',
    })
    assert resp.status_code == 302
    # Verify UPDATE query includes user_id check
    update_calls = [
        (sql, params) for sql, params in mock_cursor.execute_log
        if 'UPDATE devices SET display_name' in sql
    ]
    assert len(update_calls) == 1
    sql, params = update_calls[0]
    assert 'user_id' in sql


def test_update_device_not_yours(auth_client, mock_cursor):
    queue_before_request(mock_cursor)
    mock_cursor.add_response(fetchone={'pepper': 'mysecret'})
    mock_cursor.add_response(fetchone=None)  # not among the user's devices

    resp = auth_client.post('/devices', data={
        'action': 'update_device',
        'device_db_id': '7',
        'display_name': 'Hijacked',
    })
    assert resp.status_code == 302
    assert ('SELECT device_id FROM devices WHERE id = %s AND user_id = %s', ('7', 1)) in mock_cursor.execute_log
    assert not [sql for sql, _ in mock_cursor.execute_log if sql.startswith('UPDATE devices')]
    with auth_client.session_transaction() as sess:
        assert sess['_flashes'] == [('error', 'Device not found.')]


@pytest.mark.parametrize('typed, stored', [('0.05', 0.05), (' 2 ', 2.0), ('0', 0.0), ('', None)])
def test_update_device_sets_its_alert_threshold(auth_client, mock_cursor, typed, stored):
    queue_before_request(mock_cursor)
    mock_cursor.add_response(fetchone={'pepper': 'mysecret'})
    mock_cursor.add_response(fetchone={'device_id': 'geiger_aabbcc'})
    auth_client.post('/devices', data={
        'action': 'update_device', 'device_db_id': '1', 'display_name': 'Shed', 'alert_threshold': typed})
    assert [params for sql, params in mock_cursor.execute_log if sql.startswith('UPDATE devices')] == [
        ('Shed', stored, '1', 1)]


@pytest.mark.parametrize('typed', ['abc', '-0.1', 'nan', 'inf', '1e999'])
def test_update_device_refuses_an_alert_threshold_that_is_no_number_of_at_least_0(auth_client, mock_cursor, typed):
    queue_before_request(mock_cursor)
    mock_cursor.add_response(fetchone={'pepper': 'mysecret'})
    mock_cursor.add_response(fetchone={'device_id': 'geiger_aabbcc'})
    auth_client.post('/devices', data={
        'action': 'update_device', 'device_db_id': '1', 'display_name': 'Shed', 'alert_threshold': typed})
    assert not [sql for sql, _ in mock_cursor.execute_log if sql.startswith('UPDATE devices')]
    with auth_client.session_transaction() as sess:
        assert sess['_flashes'] == [('error', 'Alert threshold must be a number of at least 0.')]


def test_devices_page_shows_each_alert_threshold_and_the_default(auth_client, mock_cursor):
    queue_before_request(mock_cursor)
    mock_cursor.add_response(fetchone={'pepper': 'mysecret'})
    queue_settings(mock_cursor, default_alert_threshold='0.7')
    mock_cursor.add_response(fetchall=[
        {'id': 1, 'device_id': 'geiger_aabbcc', 'display_name': None, 'mac_address': 'AA:BB:CC:DD:EE:FF',
         'status': 'offline', 'last_seen': None, 'provisioned': True, 'alert_threshold': 0.05},
        {'id': 2, 'device_id': 'geiger_ddeeff', 'display_name': None, 'mac_address': 'AA:BB:CC:DD:EE:FE',
         'status': 'offline', 'last_seen': None, 'provisioned': True, 'alert_threshold': None},
    ])
    page = auth_client.get('/devices').data.decode()
    fields = re.findall(r'<input type="number" name="alert_threshold"[^>]*>', page)
    assert len(fields) == 2
    assert 'value="0.05"' in fields[0] and 'value=""' in fields[1]
    assert all('placeholder="0.7"' in f for f in fields)


# ── Delete device ───────────────────────────────────────────────────────────

def test_delete_device_success(auth_client, mock_cursor):
    queue_before_request(mock_cursor)
    mock_cursor.add_response(fetchone={'pepper': 'mysecret'})
    # SELECT device_id → found
    mock_cursor.add_response(fetchone={'device_id': 'geiger_aabbcc'})
    # DELETE (no fetch)

    with patch('app.unprovision_device') as mock_unprov:
        resp = auth_client.post('/devices', data={
            'action': 'delete_device',
            'device_db_id': '1',
        })

    assert resp.status_code == 302
    mock_unprov.assert_called_once_with('geiger_aabbcc')


def test_delete_device_nonexistent(auth_client, mock_cursor):
    queue_before_request(mock_cursor)
    mock_cursor.add_response(fetchone={'pepper': 'mysecret'})
    # SELECT device → not found (wrong user_id)
    mock_cursor.add_response(fetchone=None)

    with patch('app.unprovision_device') as mock_unprov:
        resp = auth_client.post('/devices', data={
            'action': 'delete_device',
            'device_db_id': '999',
        })

    assert resp.status_code == 302
    mock_unprov.assert_not_called()
    # The mock finds nothing whatever the SQL: what keeps another user's counter is the owner clause.
    assert ('SELECT device_id FROM devices WHERE id = %s AND user_id = %s', ('999', 1)) in mock_cursor.execute_log


def test_delete_device_calls_unprovision(auth_client, mock_cursor):
    queue_before_request(mock_cursor)
    mock_cursor.add_response(fetchone={'pepper': 'mysecret'})
    mock_cursor.add_response(fetchone={'device_id': 'geiger_112233'})

    with patch('app.unprovision_device') as mock_unprov:
        auth_client.post('/devices', data={
            'action': 'delete_device',
            'device_db_id': '5',
        })

    mock_unprov.assert_called_once_with('geiger_112233')
    # Verify DELETE query was also executed
    delete_calls = [sql for sql, _ in mock_cursor.execute_log if 'DELETE FROM devices' in sql]
    assert len(delete_calls) == 1
