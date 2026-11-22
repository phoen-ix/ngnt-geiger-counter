"""Tests for admin routes — settings, SMTP, user management."""

from datetime import datetime
from unittest.mock import patch

import pytest

from tests.testutils import queue_before_request, queue_settings

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
         'created_at': datetime(2026, 7, 1, 12, 0)},
    ])

    resp = admin_client.get('/admin')
    assert resp.status_code == 200
    assert b'Global Settings' in resp.data
    assert b'admin' in resp.data
    assert b'user1' in resp.data
    # created_at is stored in UTC and shown in display_timezone (Europe/Vienna)
    assert b'2026-01-01 01:00:00' in resp.data
    assert b'2026-07-01 14:00:00' in resp.data


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
    import app as app_module
    assert len(setting_calls) == len(app_module.SETTINGS_KEYS)


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
        'smtp_ehlo_name': 'mx.example.com',
    })
    assert resp.status_code == 302
    setting_calls = [params for sql, params in mock_cursor.execute_log
                     if 'INSERT INTO settings' in sql]
    import app as app_module
    assert len(setting_calls) == len(app_module.SMTP_KEYS)
    assert ('smtp_ehlo_name', 'mx.example.com') in setting_calls


def test_the_stored_smtp_password_never_goes_back_to_the_page(admin_client, mock_cursor):
    queue_before_request(mock_cursor, role='admin')
    queue_settings(mock_cursor, smtp_user='mailer', smtp_password='s3cret-pw')
    mock_cursor.add_response(fetchall=[])

    resp = admin_client.get('/admin')
    assert b'SMTP Password' in resp.data
    assert b's3cret-pw' not in resp.data
    assert b'placeholder="stored; empty keeps it"' in resp.data


def _saved_smtp(admin_client, mock_cursor, **form):
    queue_before_request(mock_cursor, role='admin')
    admin_client.post('/admin', data={'action': 'save_smtp', 'smtp_host': 'smtp.example.com', 'smtp_port': '587',
                                      'smtp_from': 'noreply@example.com', 'smtp_tls': '1', **form})
    return dict(params for sql, params in mock_cursor.execute_log if 'INSERT INTO settings' in sql)


def test_an_empty_smtp_password_keeps_the_stored_one(admin_client, mock_cursor):
    saved = _saved_smtp(admin_client, mock_cursor, smtp_user='mailer', smtp_password='')
    assert saved['smtp_user'] == 'mailer'
    assert 'smtp_password' not in saved


def test_without_an_smtp_user_the_password_is_cleared(admin_client, mock_cursor):
    assert _saved_smtp(admin_client, mock_cursor, smtp_user='', smtp_password='')['smtp_password'] == ''


# ── Role and visibility: the value the button asks for ───────────────────────

def _set(admin_client, mock_cursor, form, target):
    """POST one Role/Public button for user 2 (`target`: their row, None if gone); the UPDATEs and flashes."""
    queue_before_request(mock_cursor, role='admin')
    mock_cursor.add_response(fetchone=target)
    resp = admin_client.post('/admin', data={'target_user_id': '2', **form})
    assert resp.status_code == 302
    with admin_client.session_transaction() as sess:
        flashes = sess.pop('_flashes', [])
    return [params for sql, params in mock_cursor.execute_log if sql.startswith('UPDATE users')], flashes


@pytest.mark.parametrize('form, target, updates, message', [
    ({'action': 'set_role', 'role': 'admin'}, {'username': 'bob', 'was': 'user'},
     [('admin', 2)], ('success', 'Role updated.')),
    ({'action': 'set_role', 'role': 'user'}, {'username': 'bob', 'was': 'admin'},
     [('user', 2)], ('success', 'Role updated.')),
    ({'action': 'set_public', 'public': '1'}, {'username': 'bob', 'was': 0},
     [(1, 2)], ('success', 'Visibility updated.')),
    ({'action': 'set_public', 'public': '0'}, {'username': 'bob', 'was': 1},
     [(0, 2)], ('success', 'Visibility updated.')),
    # A double click, or a tab opened before the last change: nothing changes, and the page says so.
    ({'action': 'set_role', 'role': 'admin'}, {'username': 'bob', 'was': 'admin'},
     [], ('success', 'bob is already admin.')),
    ({'action': 'set_public', 'public': '1'}, {'username': 'bob', 'was': 1},
     [], ('success', 'bob is already public.')),
    ({'action': 'set_public', 'public': '0'}, {'username': 'bob', 'was': 0},
     [], ('success', 'bob is already private.')),
    ({'action': 'set_role', 'role': 'admin'}, None, [], ('error', 'User not found.')),
    ({'action': 'set_role', 'role': 'root'}, {'username': 'bob', 'was': 'user'},
     [], ('error', 'Invalid request.')),
    ({'action': 'set_public', 'public': 'yes'}, {'username': 'bob', 'was': 0},
     [], ('error', 'Invalid request.')),
])
def test_role_and_visibility_are_set_to_the_value_asked_for(admin_client, mock_cursor, form, target, updates, message):
    assert _set(admin_client, mock_cursor, form, target) == (updates, [message])


def test_set_role_cannot_change_self(admin_client, mock_cursor):
    queue_before_request(mock_cursor, role='admin')
    admin_client.post('/admin', data={'action': 'set_role', 'role': 'user', 'target_user_id': '1'})
    assert not [sql for sql, _ in mock_cursor.execute_log if sql.startswith('UPDATE users')]
    with admin_client.session_transaction() as sess:
        assert sess['_flashes'] == [('error', 'You cannot change your own role.')]


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
        'action': 'set_role',
        'role': 'admin',
        'target_user_id': 'not-a-number',
    }, follow_redirects=True)
    assert b'Invalid request' in resp.data
