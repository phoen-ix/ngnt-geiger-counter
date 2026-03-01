"""Tests for auth routes: login, register, logout, forgot/reset password."""

from unittest.mock import patch

from werkzeug.security import generate_password_hash

from tests.testutils import queue_before_request, queue_settings


# ── Login ───────────────────────────────────────────────────────────────────

def test_login_get_renders_form(client):
    resp = client.get('/login')
    assert resp.status_code == 200
    assert b'Login' in resp.data


def test_login_valid_credentials(client, mock_cursor):
    pw_hash = generate_password_hash('correct-password')
    mock_cursor.add_response(fetchone={
        'id': 1, 'username': 'testuser', 'password_hash': pw_hash,
        'role': 'user', 'pw_version': 0,
    })

    resp = client.post('/login', data={
        'username': 'testuser',
        'password': 'correct-password',
    })
    assert resp.status_code == 302
    with client.session_transaction() as sess:
        assert sess['user_id'] == 1
        assert sess['username'] == 'testuser'
        assert sess['role'] == 'user'
        assert sess['pw_version'] == 0


def test_login_invalid_password(client, mock_cursor):
    pw_hash = generate_password_hash('correct-password')
    mock_cursor.add_response(fetchone={
        'id': 1, 'username': 'testuser', 'password_hash': pw_hash,
        'role': 'user', 'pw_version': 0,
    })

    resp = client.post('/login', data={
        'username': 'testuser',
        'password': 'wrong-password',
    })
    assert resp.status_code == 200
    assert b'Invalid username or password' in resp.data


def test_login_nonexistent_user(client, mock_cursor):
    mock_cursor.add_response(fetchone=None)

    resp = client.post('/login', data={
        'username': 'nonexistent',
        'password': 'anything',
    })
    assert resp.status_code == 200
    assert b'Invalid username or password' in resp.data


def test_login_sets_session_permanent(client, mock_cursor):
    pw_hash = generate_password_hash('password')
    mock_cursor.add_response(fetchone={
        'id': 5, 'username': 'user5', 'password_hash': pw_hash,
        'role': 'admin', 'pw_version': 3,
    })

    client.post('/login', data={'username': 'user5', 'password': 'password'})
    with client.session_transaction() as sess:
        assert sess['user_id'] == 5
        assert sess['role'] == 'admin'
        assert sess['pw_version'] == 3


# ── Register ────────────────────────────────────────────────────────────────

def test_register_get_renders_form(client, mock_cursor):
    queue_settings(mock_cursor, registration_enabled='1')
    resp = client.get('/register')
    assert resp.status_code == 200
    assert b'Create Account' in resp.data


def test_register_disabled(client, mock_cursor):
    queue_settings(mock_cursor, registration_enabled='0')
    resp = client.get('/register', follow_redirects=True)
    assert b'Registration is currently disabled' in resp.data


def test_register_success(client, mock_cursor):
    # GET settings check for the POST
    queue_settings(mock_cursor, registration_enabled='1')
    # Duplicate username check → not found
    mock_cursor.add_response(fetchone=None)
    # INSERT succeeds (no fetch)

    resp = client.post('/register', data={
        'username': 'newuser',
        'email': 'new@example.com',
        'password': 'password123',
        'password2': 'password123',
    })
    assert resp.status_code == 302
    # Verify INSERT was executed
    insert_calls = [sql for sql, _ in mock_cursor.execute_log if 'INSERT INTO users' in sql]
    assert len(insert_calls) == 1


def test_register_username_taken(client, mock_cursor):
    queue_settings(mock_cursor, registration_enabled='1')
    mock_cursor.add_response(fetchone={'id': 1})  # username exists

    resp = client.post('/register', data={
        'username': 'existing',
        'email': '',
        'password': 'password123',
        'password2': 'password123',
    })
    assert resp.status_code == 200
    assert b'already taken' in resp.data


def test_register_username_too_short(client, mock_cursor):
    queue_settings(mock_cursor, registration_enabled='1')

    resp = client.post('/register', data={
        'username': 'ab',
        'email': '',
        'password': 'password123',
        'password2': 'password123',
    })
    assert resp.status_code == 200
    assert b'3-50 characters' in resp.data


def test_register_username_too_long(client, mock_cursor):
    queue_settings(mock_cursor, registration_enabled='1')

    resp = client.post('/register', data={
        'username': 'a' * 51,
        'email': '',
        'password': 'password123',
        'password2': 'password123',
    })
    assert resp.status_code == 200
    assert b'3-50 characters' in resp.data


def test_register_username_bad_chars(client, mock_cursor):
    queue_settings(mock_cursor, registration_enabled='1')

    resp = client.post('/register', data={
        'username': 'user name!',
        'email': '',
        'password': 'password123',
        'password2': 'password123',
    })
    assert resp.status_code == 200
    assert b'letters, digits, underscores' in resp.data


def test_register_password_too_short(client, mock_cursor):
    queue_settings(mock_cursor, registration_enabled='1')

    resp = client.post('/register', data={
        'username': 'validuser',
        'email': '',
        'password': '12345',
        'password2': '12345',
    })
    assert resp.status_code == 200
    assert b'at least 6 characters' in resp.data


def test_register_password_mismatch(client, mock_cursor):
    queue_settings(mock_cursor, registration_enabled='1')

    resp = client.post('/register', data={
        'username': 'validuser',
        'email': '',
        'password': 'password123',
        'password2': 'password456',
    })
    assert resp.status_code == 200
    assert b'do not match' in resp.data


def test_register_empty_fields(client, mock_cursor):
    queue_settings(mock_cursor, registration_enabled='1')

    resp = client.post('/register', data={
        'username': '',
        'email': '',
        'password': '',
        'password2': '',
    })
    assert resp.status_code == 200
    assert b'required' in resp.data


# ── Logout ──────────────────────────────────────────────────────────────────

def test_logout_clears_session(auth_client, mock_cursor):
    # before_request for the GET /logout
    queue_before_request(mock_cursor)

    resp = auth_client.get('/logout')
    assert resp.status_code == 302
    with auth_client.session_transaction() as sess:
        assert 'user_id' not in sess


# ── Forgot password ─────────────────────────────────────────────────────────

def test_forgot_password_get(client):
    resp = client.get('/forgot-password')
    assert resp.status_code == 200
    assert b'Forgot Password' in resp.data


def test_forgot_password_valid_user_with_email(client, mock_cursor):
    # get_settings (fetchall)
    queue_settings(mock_cursor)
    # DELETE expired resets (execute only, no fetch)
    # SELECT user
    mock_cursor.add_response(fetchone={'id': 1, 'email': 'user@example.com'})
    # INSERT reset token (execute only, no fetch)

    with patch('app.send_password_reset_email') as mock_send:
        resp = client.post('/forgot-password', data={'username': 'testuser'})

    assert resp.status_code == 302
    mock_send.assert_called_once()
    assert mock_send.call_args[0][0] == 'user@example.com'


def test_forgot_password_user_without_email(client, mock_cursor):
    queue_settings(mock_cursor)
    mock_cursor.add_response(fetchone={'id': 1, 'email': None})

    with patch('app.send_password_reset_email') as mock_send:
        resp = client.post('/forgot-password', data={'username': 'testuser'})

    assert resp.status_code == 302
    mock_send.assert_not_called()


def test_forgot_password_nonexistent_user(client, mock_cursor):
    queue_settings(mock_cursor)
    mock_cursor.add_response(fetchone=None)

    resp = client.post('/forgot-password', data={'username': 'nonexistent'})
    assert resp.status_code == 302
    # Same success message always shown (anti-enumeration)


def test_forgot_password_cleans_expired_tokens(client, mock_cursor):
    queue_settings(mock_cursor)
    mock_cursor.add_response(fetchone=None)

    client.post('/forgot-password', data={'username': 'x'})
    # Verify DELETE expired resets was executed
    delete_calls = [sql for sql, _ in mock_cursor.execute_log if 'DELETE FROM password_resets' in sql]
    assert len(delete_calls) == 1


# ── Reset password ──────────────────────────────────────────────────────────

def test_reset_password_get_valid_token(client, mock_cursor):
    mock_cursor.add_response(fetchone={
        'id': 1, 'user_id': 1, 'token': 'abc123', 'used': False,
        'username': 'testuser',
    })

    resp = client.get('/reset-password/abc123')
    assert resp.status_code == 200
    assert b'Set New Password' in resp.data


def test_reset_password_get_invalid_token(client, mock_cursor):
    mock_cursor.add_response(fetchone=None)

    resp = client.get('/reset-password/badtoken', follow_redirects=True)
    assert b'Invalid or expired' in resp.data


def test_reset_password_post_success(client, mock_cursor):
    # First call: validate token (GET-like check within POST)
    mock_cursor.add_response(fetchone={
        'id': 1, 'user_id': 1, 'token': 'abc123', 'used': False,
        'username': 'testuser',
    })
    # UPDATE user password + UPDATE reset used (no fetch)

    resp = client.post('/reset-password/abc123', data={
        'password': 'newpassword',
        'password2': 'newpassword',
    })
    assert resp.status_code == 302
    # Verify pw_version was incremented
    update_calls = [sql for sql, _ in mock_cursor.execute_log if 'pw_version' in sql]
    assert len(update_calls) == 1


def test_reset_password_post_too_short(client, mock_cursor):
    mock_cursor.add_response(fetchone={
        'id': 1, 'user_id': 1, 'token': 'abc123', 'used': False,
        'username': 'testuser',
    })

    resp = client.post('/reset-password/abc123', data={
        'password': '12345',
        'password2': '12345',
    })
    assert resp.status_code == 200
    assert b'at least 6 characters' in resp.data


def test_reset_password_post_mismatch(client, mock_cursor):
    mock_cursor.add_response(fetchone={
        'id': 1, 'user_id': 1, 'token': 'abc123', 'used': False,
        'username': 'testuser',
    })

    resp = client.post('/reset-password/abc123', data={
        'password': 'newpassword',
        'password2': 'different',
    })
    assert resp.status_code == 200
    assert b'do not match' in resp.data
