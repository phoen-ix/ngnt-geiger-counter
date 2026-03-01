"""Tests for admin routes — settings, SMTP, user management."""

from unittest.mock import patch

from tests.testutils import queue_before_request, queue_settings


# ── Auth requirements ───────────────────────────────────────────────────────

def test_admin_requires_login(client):
    resp = client.get('/admin')
    assert resp.status_code == 302
    assert '/login' in resp.headers['Location']


def test_admin_requires_admin_role(auth_client, mock_cursor):
    """Regular user is redirected away from admin page."""
    queue_before_request(mock_cursor, role='user')

    resp = auth_client.get('/admin')
    assert resp.status_code == 302


# ── GET /admin ──────────────────────────────────────────────────────────────

def test_admin_get_renders(admin_client, mock_cursor):
    queue_before_request(mock_cursor, role='admin')
    queue_settings(mock_cursor)
    mock_cursor.add_response(fetchall=[
        {'id': 1, 'username': 'admin', 'email': 'admin@example.com',
         'role': 'admin', 'public': False, 'device_count': 2,
         'created_at': '2026-01-01 00:00:00'},
        {'id': 2, 'username': 'user1', 'email': None,
         'role': 'user', 'public': True, 'device_count': 1,
         'created_at': '2026-02-01 00:00:00'},
    ])

    resp = admin_client.get('/admin')
    assert resp.status_code == 200
    assert b'Global Settings' in resp.data
    assert b'admin' in resp.data
    assert b'user1' in resp.data


# ── Save settings ───────────────────────────────────────────────────────────

def test_save_settings(admin_client, mock_cursor):
    queue_before_request(mock_cursor, role='admin')
    # INSERT/UPDATE for each SETTINGS_KEY (no fetch)

    resp = admin_client.post('/admin', data={
        'action': 'save_settings',
        'base_url': 'https://geiger.example.com',
        'display_timezone': 'UTC',
        'offline_timeout_minutes': '10',
        'default_cpm_factor': '0.0057',
        'default_alert_threshold': '0.5',
        'registration_enabled': '1',
        'session_timeout_minutes': '720',
    })
    assert resp.status_code == 302
    # Verify INSERT/UPDATE was executed for settings
    setting_calls = [sql for sql, _ in mock_cursor.execute_log
                     if 'INSERT INTO settings' in sql]
    assert len(setting_calls) == 7  # one per SETTINGS_KEY


# ── Save SMTP ───────────────────────────────────────────────────────────────

def test_save_smtp(admin_client, mock_cursor):
    queue_before_request(mock_cursor, role='admin')

    resp = admin_client.post('/admin', data={
        'action': 'save_smtp',
        'smtp_host': 'smtp.example.com',
        'smtp_port': '587',
        'smtp_user': 'user',
        'smtp_password': 'pass',
        'smtp_from': 'noreply@example.com',
        'smtp_tls': '1',
    })
    assert resp.status_code == 302
    setting_calls = [sql for sql, _ in mock_cursor.execute_log
                     if 'INSERT INTO settings' in sql]
    assert len(setting_calls) == 6  # one per SMTP_KEY


# ── Toggle role ─────────────────────────────────────────────────────────────

def test_toggle_role_success(admin_client, mock_cursor):
    queue_before_request(mock_cursor, role='admin')
    # SELECT role for target user
    mock_cursor.add_response(fetchone={'role': 'user'})
    # UPDATE role (no fetch)

    resp = admin_client.post('/admin', data={
        'action': 'toggle_role',
        'target_user_id': '2',  # different from session user_id=1
    })
    assert resp.status_code == 302
    update_calls = [(sql, params) for sql, params in mock_cursor.execute_log
                    if 'UPDATE users SET role' in sql]
    assert len(update_calls) == 1
    _, params = update_calls[0]
    assert params[0] == 'admin'  # toggled from user to admin


def test_toggle_role_cannot_change_self(admin_client, mock_cursor):
    queue_before_request(mock_cursor, role='admin')

    resp = admin_client.post('/admin', data={
        'action': 'toggle_role',
        'target_user_id': '1',  # same as session user_id
    })
    assert resp.status_code == 302
    # Verify no role UPDATE was executed
    update_calls = [sql for sql, _ in mock_cursor.execute_log
                    if 'UPDATE users SET role' in sql]
    assert len(update_calls) == 0


# ── Toggle public ───────────────────────────────────────────────────────────

def test_toggle_public(admin_client, mock_cursor):
    queue_before_request(mock_cursor, role='admin')
    # UPDATE public (no fetch)

    resp = admin_client.post('/admin', data={
        'action': 'toggle_public',
        'target_user_id': '2',
    })
    assert resp.status_code == 302
    update_calls = [sql for sql, _ in mock_cursor.execute_log
                    if 'UPDATE users SET' in sql and 'public' in sql]
    assert len(update_calls) == 1


# ── Delete user ─────────────────────────────────────────────────────────────

def test_delete_user_success(admin_client, mock_cursor):
    queue_before_request(mock_cursor, role='admin')
    # SELECT device_ids for target user
    mock_cursor.add_response(fetchall=[
        {'device_id': 'geiger_aabbcc'},
    ])
    # DELETE user (no fetch)

    with patch('app.unprovision_device') as mock_unprov:
        resp = admin_client.post('/admin', data={
            'action': 'delete_user',
            'target_user_id': '2',
        })

    assert resp.status_code == 302
    mock_unprov.assert_called_once_with('geiger_aabbcc')
    delete_calls = [sql for sql, _ in mock_cursor.execute_log if 'DELETE FROM users' in sql]
    assert len(delete_calls) == 1


def test_delete_user_cannot_delete_self(admin_client, mock_cursor):
    queue_before_request(mock_cursor, role='admin')

    with patch('app.unprovision_device') as mock_unprov:
        resp = admin_client.post('/admin', data={
            'action': 'delete_user',
            'target_user_id': '1',  # same as session user_id
        })

    assert resp.status_code == 302
    mock_unprov.assert_not_called()
    delete_calls = [sql for sql, _ in mock_cursor.execute_log if 'DELETE FROM users' in sql]
    assert len(delete_calls) == 0


def test_delete_user_unprovisions_all_devices(admin_client, mock_cursor):
    queue_before_request(mock_cursor, role='admin')
    mock_cursor.add_response(fetchall=[
        {'device_id': 'geiger_111111'},
        {'device_id': 'geiger_222222'},
    ])

    with patch('app.unprovision_device') as mock_unprov:
        admin_client.post('/admin', data={
            'action': 'delete_user',
            'target_user_id': '3',
        })

    assert mock_unprov.call_count == 2
    mock_unprov.assert_any_call('geiger_111111')
    mock_unprov.assert_any_call('geiger_222222')


# ── Invalid target_user_id ──────────────────────────────────────────────────

def test_invalid_target_user_id(admin_client, mock_cursor):
    # POST: before_request
    queue_before_request(mock_cursor, role='admin')
    # Redirect to GET /admin: before_request + settings + user list
    queue_before_request(mock_cursor, role='admin')
    queue_settings(mock_cursor)
    mock_cursor.add_response(fetchall=[
        {'id': 1, 'username': 'admin', 'email': 'admin@example.com',
         'role': 'admin', 'public': False, 'device_count': 0,
         'created_at': '2026-01-01 00:00:00'},
    ])

    resp = admin_client.post('/admin', data={
        'action': 'toggle_role',
        'target_user_id': 'not-a-number',
    }, follow_redirects=True)
    assert b'Invalid request' in resp.data
