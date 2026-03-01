"""Tests for device management routes — CRUD + provisioning."""

from unittest.mock import patch

from tests.testutils import queue_before_request


# ── Auth requirement ────────────────────────────────────────────────────────

def test_devices_requires_login(client):
    resp = client.get('/devices')
    assert resp.status_code == 302
    assert '/login' in resp.headers['Location']


# ── GET /devices ────────────────────────────────────────────────────────────

def test_devices_get_lists_devices(auth_client, mock_cursor):
    queue_before_request(mock_cursor)
    # SELECT pepper
    mock_cursor.add_response(fetchone={'pepper': 'mysecret'})
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


def test_devices_get_empty(auth_client, mock_cursor):
    queue_before_request(mock_cursor)
    mock_cursor.add_response(fetchone={'pepper': 'mysecret'})
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
    device_id = mock_prov.call_args[0][0]
    assert device_id == 'geiger_ddeeff'


def test_add_device_invalid_mac(auth_client, mock_cursor):
    # POST: before_request + pepper check
    queue_before_request(mock_cursor)
    mock_cursor.add_response(fetchone={'pepper': 'mysecret'})
    # Redirect to GET /devices: before_request + pepper + device list
    queue_before_request(mock_cursor)
    mock_cursor.add_response(fetchone={'pepper': 'mysecret'})
    mock_cursor.add_response(fetchall=[])

    resp = auth_client.post('/devices', data={
        'action': 'add_device',
        'mac_address': 'not-a-mac',
        'display_name': '',
    }, follow_redirects=True)
    assert b'Invalid MAC address' in resp.data


def test_add_device_no_pepper(auth_client, mock_cursor):
    # POST: before_request + pepper check
    queue_before_request(mock_cursor)
    mock_cursor.add_response(fetchone={'pepper': None})
    # Redirect to GET /devices: before_request + pepper + device list
    queue_before_request(mock_cursor)
    mock_cursor.add_response(fetchone={'pepper': None})

    resp = auth_client.post('/devices', data={
        'action': 'add_device',
        'mac_address': 'AA:BB:CC:DD:EE:FF',
        'display_name': '',
    }, follow_redirects=True)
    assert b'Set a pepper' in resp.data


def test_add_device_duplicate(auth_client, mock_cursor):
    # POST: before_request + pepper + duplicate check
    queue_before_request(mock_cursor)
    mock_cursor.add_response(fetchone={'pepper': 'mysecret'})
    mock_cursor.add_response(fetchone={'id': 99})
    # Redirect to GET /devices: before_request + pepper + device list
    queue_before_request(mock_cursor)
    mock_cursor.add_response(fetchone={'pepper': 'mysecret'})
    mock_cursor.add_response(fetchall=[])

    resp = auth_client.post('/devices', data={
        'action': 'add_device',
        'mac_address': 'AA:BB:CC:DD:EE:FF',
        'display_name': '',
    }, follow_redirects=True)
    assert b'already registered' in resp.data


# ── Update device ───────────────────────────────────────────────────────────

def test_update_device_success(auth_client, mock_cursor):
    queue_before_request(mock_cursor)
    mock_cursor.add_response(fetchone={'pepper': 'mysecret'})
    # UPDATE (no fetch)

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
