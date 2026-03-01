"""Tests for account routes — profile, password change."""

from unittest.mock import patch

from werkzeug.security import generate_password_hash

from tests.testutils import queue_before_request


# ── Auth requirement ────────────────────────────────────────────────────────

def test_account_requires_login(client):
    resp = client.get('/account')
    assert resp.status_code == 302
    assert '/login' in resp.headers['Location']


# ── GET /account ────────────────────────────────────────────────────────────

def test_account_get_renders(auth_client, mock_cursor):
    queue_before_request(mock_cursor)
    mock_cursor.add_response(fetchone={
        'id': 1, 'username': 'testuser', 'email': 'test@example.com',
        'pepper': 'mysecret', 'public': False, 'role': 'user',
    })

    resp = auth_client.get('/account')
    assert resp.status_code == 200
    assert b'testuser' in resp.data
    assert b'test@example.com' in resp.data


# ── Update profile ──────────────────────────────────────────────────────────

def test_update_profile_email(auth_client, mock_cursor):
    queue_before_request(mock_cursor)
    # UPDATE (no fetch)

    resp = auth_client.post('/account', data={
        'action': 'update_profile',
        'email': 'new@example.com',
        'pepper': 'mysecret',
        'public': '0',
    })
    assert resp.status_code == 302
    update_calls = [
        (sql, params) for sql, params in mock_cursor.execute_log
        if 'UPDATE users SET email' in sql
    ]
    assert len(update_calls) == 1
    _, params = update_calls[0]
    assert params[0] == 'new@example.com'


def test_update_profile_pepper(auth_client, mock_cursor):
    queue_before_request(mock_cursor)

    resp = auth_client.post('/account', data={
        'action': 'update_profile',
        'email': '',
        'pepper': 'newpepper',
        'public': '0',
    })
    assert resp.status_code == 302
    update_calls = [
        (sql, params) for sql, params in mock_cursor.execute_log
        if 'UPDATE users SET email' in sql
    ]
    assert len(update_calls) == 1
    _, params = update_calls[0]
    assert params[1] == 'newpepper'


def test_update_profile_public_toggle(auth_client, mock_cursor):
    queue_before_request(mock_cursor)

    resp = auth_client.post('/account', data={
        'action': 'update_profile',
        'email': '',
        'pepper': '',
        'public': '1',
    })
    assert resp.status_code == 302
    update_calls = [
        (sql, params) for sql, params in mock_cursor.execute_log
        if 'UPDATE users SET email' in sql
    ]
    assert len(update_calls) == 1
    _, params = update_calls[0]
    assert params[2] is True


# ── Change password ─────────────────────────────────────────────────────────

def test_change_password_success(auth_client, mock_cursor):
    queue_before_request(mock_cursor)
    # SELECT password_hash
    pw_hash = generate_password_hash('oldpassword')
    mock_cursor.add_response(fetchone={'password_hash': pw_hash})
    # UPDATE password (no fetch)

    resp = auth_client.post('/account', data={
        'action': 'change_password',
        'current_password': 'oldpassword',
        'new_password': 'newpassword123',
        'new_password2': 'newpassword123',
    })
    assert resp.status_code == 302
    # Verify pw_version increment
    update_calls = [sql for sql, _ in mock_cursor.execute_log
                    if 'pw_version' in sql and sql.strip().startswith('UPDATE')]
    assert len(update_calls) == 1


def test_change_password_wrong_current(auth_client, mock_cursor):
    # POST: before_request + password check
    queue_before_request(mock_cursor)
    pw_hash = generate_password_hash('realpassword')
    mock_cursor.add_response(fetchone={'password_hash': pw_hash})
    # Redirect to GET /account: before_request + user data
    queue_before_request(mock_cursor)
    mock_cursor.add_response(fetchone={
        'id': 1, 'username': 'testuser', 'email': 'test@example.com',
        'pepper': 'mysecret', 'public': False, 'role': 'user',
    })

    resp = auth_client.post('/account', data={
        'action': 'change_password',
        'current_password': 'wrongpassword',
        'new_password': 'newpassword123',
        'new_password2': 'newpassword123',
    }, follow_redirects=True)
    assert b'incorrect' in resp.data


def test_change_password_too_short(auth_client, mock_cursor):
    # POST: before_request + password check
    queue_before_request(mock_cursor)
    pw_hash = generate_password_hash('oldpassword')
    mock_cursor.add_response(fetchone={'password_hash': pw_hash})
    # Redirect to GET /account: before_request + user data
    queue_before_request(mock_cursor)
    mock_cursor.add_response(fetchone={
        'id': 1, 'username': 'testuser', 'email': 'test@example.com',
        'pepper': 'mysecret', 'public': False, 'role': 'user',
    })

    resp = auth_client.post('/account', data={
        'action': 'change_password',
        'current_password': 'oldpassword',
        'new_password': '12345',
        'new_password2': '12345',
    }, follow_redirects=True)
    assert b'at least 6 characters' in resp.data


def test_change_password_mismatch(auth_client, mock_cursor):
    # POST: before_request + password check
    queue_before_request(mock_cursor)
    pw_hash = generate_password_hash('oldpassword')
    mock_cursor.add_response(fetchone={'password_hash': pw_hash})
    # Redirect to GET /account: before_request + user data
    queue_before_request(mock_cursor)
    mock_cursor.add_response(fetchone={
        'id': 1, 'username': 'testuser', 'email': 'test@example.com',
        'pepper': 'mysecret', 'public': False, 'role': 'user',
    })

    resp = auth_client.post('/account', data={
        'action': 'change_password',
        'current_password': 'oldpassword',
        'new_password': 'newpassword123',
        'new_password2': 'different',
    }, follow_redirects=True)
    assert b'do not match' in resp.data


def test_change_password_removes_initial_file(auth_client, mock_cursor):
    queue_before_request(mock_cursor)
    pw_hash = generate_password_hash('oldpassword')
    mock_cursor.add_response(fetchone={'password_hash': pw_hash})

    with patch('app.os.remove') as mock_remove:
        auth_client.post('/account', data={
            'action': 'change_password',
            'current_password': 'oldpassword',
            'new_password': 'newpassword123',
            'new_password2': 'newpassword123',
        })

    mock_remove.assert_called_once_with('/app/data/admin_initial_password.txt')
