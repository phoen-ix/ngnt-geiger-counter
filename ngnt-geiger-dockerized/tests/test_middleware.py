"""Tests for before_request middleware, CSRF, and rate limiting."""

import re
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from unittest.mock import patch

import pytest
from itsdangerous import TimestampSigner
from werkzeug.security import generate_password_hash

from tests.testutils import queue_settings

# ── before_request: role refreshed ──────────────────────────────────────────

def test_before_request_role_refreshed(client, mock_cursor):
    """Role should be refreshed from DB on every request."""
    with client.session_transaction() as sess:
        sess['user_id'] = 1
        sess['username'] = 'testuser'
        sess['role'] = 'user'
        sess['pw_version'] = 0

    # before_request: DB returns role='admin' (promoted)
    mock_cursor.add_response(fetchone={'pw_version': 0, 'role': 'admin', 'session_timeout': '1440'})
    # Dashboard (now admin): get_settings + all devices query
    queue_settings(mock_cursor)
    mock_cursor.add_response(fetchall=[])

    client.get('/')
    with client.session_transaction() as sess:
        assert sess['role'] == 'admin'


# ── before_request: the cookie outlives the session timeout ─────────────────

def test_the_cookie_outlives_the_session_timeout(client, mock_cursor):
    """The server ends a session idle past the timeout (below); the browser keeps the cookie 31 days
    longer, so that a person back after the timeout still sends it and is told why. Until 2026-11-28
    it expired with the timeout and the person landed on /login with no word of why."""
    with client.session_transaction() as sess:
        sess.update(user_id=1, username='testuser', role='user', pw_version=0)
    mock_cursor.add_response(fetchone={'pw_version': 0, 'role': 'user', 'session_timeout': '30'})
    queue_settings(mock_cursor)
    mock_cursor.add_response(fetchall=[])

    resp = client.get('/')
    expires = parsedate_to_datetime(re.search('Expires=([^;]+)', resp.headers['Set-Cookie']).group(1))
    assert timedelta(days=31, minutes=29) < expires - datetime.now(UTC) <= timedelta(days=31, minutes=30)


# ── before_request: the session timeout decides on every worker ────────────

def _signed_in_minutes_ago(client, monkeypatch, minutes):
    """A signed-in session whose cookie was last renewed `minutes` ago (Flask renews it on
    every request, so that is how long the person has been idle)."""
    real = TimestampSigner.get_timestamp
    with monkeypatch.context() as m:
        m.setattr(TimestampSigner, 'get_timestamp', lambda self: real(self) - minutes * 60)
        with client.session_transaction() as sess:
            sess.update(user_id=1, username='testuser', role='user', pw_version=0)


@pytest.mark.parametrize('worker_lifetime', [timedelta(days=31), timedelta(minutes=1440)],
                         ids=['a worker after a start', 'a worker that saw the old timeout'])
@pytest.mark.parametrize(('idle', 'signed_in'), [(29, True), (31, False)], ids=['idle 29 min', 'idle 31 min'])
def test_the_session_timeout_ends_an_idle_session_on_every_worker(
        client, mock_cursor, app_instance, monkeypatch, worker_lifetime, idle, signed_in):
    """Flask checks the cookie's age before the request reads the setting, against the lifetime
    this gunicorn worker set last: Flask's 31 days after a start, the old value after an admin
    lowered it. Either let a session idle past the timeout back in, and renewed it."""
    _signed_in_minutes_ago(client, monkeypatch, idle)
    monkeypatch.setattr(app_instance, 'permanent_session_lifetime', worker_lifetime)
    mock_cursor.add_response(fetchone={'pw_version': 0, 'role': 'user', 'session_timeout': '30'})
    mock_cursor.add_response(fetchone={'pepper': None})
    queue_settings(mock_cursor)
    mock_cursor.add_response(fetchall=[])

    resp = client.get('/devices')
    if signed_in:
        assert resp.status_code == 200
    else:
        assert (resp.status_code, resp.headers['Location']) == (302, '/login')


# ── before_request: a session that ends says why ───────────────────────────
# Owner decision 2026-11-28: a person whose session ends lands on /login with the reason.

SESSION_ENDS = {
    'idle past the timeout': (31, {'pw_version': 0, 'role': 'user', 'session_timeout': '30'},
                              'You were signed out after being idle'),
    'password changed on another device': (0, {'pw_version': 1, 'role': 'user', 'session_timeout': '1440'},
                                           'Your password was changed on another device'),
    'account deleted': (0, None, 'This account no longer exists'),
}


@pytest.mark.parametrize('path', ['/', '/devices'])
@pytest.mark.parametrize(('idle', 'row', 'reason'), SESSION_ENDS.values(), ids=SESSION_ENDS)
def test_a_session_that_ends_lands_on_the_login_page_with_the_reason(
        client, mock_cursor, app_instance, monkeypatch, path, idle, row, reason):
    monkeypatch.setattr(app_instance, 'permanent_session_lifetime', timedelta(days=31))  # a worker after a start
    _signed_in_minutes_ago(client, monkeypatch, idle)
    mock_cursor.add_response(fetchone=row)

    resp = client.get(path, follow_redirects=True)
    assert resp.request.path == '/login'
    assert f'<div class="error">{reason}</div>' in resp.text
    with client.session_transaction() as sess:
        assert 'user_id' not in sess


def test_past_a_one_minute_timeout_the_worker_that_applied_it_still_says_why(
        client, mock_cursor, app_instance, monkeypatch):
    """Every request applies the timeout to the cookie it renews. Until 2026-11-28 it also became the
    age Flask itself checks the next cookie against, before the setting is read: past it, Flask had
    dropped the session already and nothing was left to say why."""
    monkeypatch.setattr(app_instance, 'permanent_session_lifetime', timedelta(days=31))
    row = {'pw_version': 0, 'role': 'user', 'session_timeout': '1'}
    with app_instance.test_client() as other:  # someone else's page load on this worker
        with other.session_transaction() as sess:
            sess.update(user_id=2, username='other', role='user', pw_version=0)
        mock_cursor.add_response(fetchone=row)
        mock_cursor.add_response(fetchone={'pepper': None})
        queue_settings(mock_cursor)
        mock_cursor.add_response(fetchall=[])
        assert other.get('/devices').status_code == 200
    _signed_in_minutes_ago(client, monkeypatch, 2)
    mock_cursor.add_response(fetchone=row)

    resp = client.get('/devices', follow_redirects=True)
    assert resp.request.path == '/login'
    assert 'You were signed out after being idle' in resp.text


# ── before_request: DB error swallowed ──────────────────────────────────────

def test_before_request_db_error_keeps_session(client, mock_cursor, app_instance):
    """A DB error in before_request is logged and logs nobody out."""
    with client.session_transaction() as sess:
        sess['user_id'] = 1
        sess['username'] = 'testuser'
        sess['role'] = 'user'
        sess['pw_version'] = 0

    # In TESTING mode Flask propagates exceptions, so the route's get_db()
    # call will raise.  Temporarily disable propagation to verify that
    # before_request itself catches the error and the route is reached.
    app_instance.config['PROPAGATE_EXCEPTIONS'] = False
    try:
        with patch('app.get_db', side_effect=Exception('DB down')):
            resp = client.get('/')
            # before_request catches its error, but the route also needs
            # the DB and fails: 500
            assert resp.status_code == 500
        with client.session_transaction() as sess:
            assert sess['user_id'] == 1
    finally:
        app_instance.config['PROPAGATE_EXCEPTIONS'] = True


# ── before_request: skips static ────────────────────────────────────────────

def test_before_request_skips_static(client, mock_cursor):
    """Requests to static files should not trigger DB queries."""
    with client.session_transaction() as sess:
        sess['user_id'] = 1
        sess['username'] = 'testuser'
        sess['role'] = 'user'
        sess['pw_version'] = 0

    resp = client.get('/static/style.css')
    assert resp.status_code == 200
    assert mock_cursor.execute_log == []


# ── CSRF protection ─────────────────────────────────────────────────────────

def test_csrf_rejects_without_token(app_instance, mock_cursor):
    """With CSRF enabled, POSTs without a token should be rejected."""
    app_instance.config['WTF_CSRF_ENABLED'] = True
    try:
        with app_instance.test_client() as c:
            resp = c.post('/login', data={
                'username': 'test',
                'password': 'test',
            })
            assert resp.status_code == 400
    finally:
        app_instance.config['WTF_CSRF_ENABLED'] = False


# ── Rate limiting ───────────────────────────────────────────────────────────

@pytest.fixture
def limited(app_instance, mock_cursor):
    """POST/GET as a new browser from `ip` (the peer, or the address a proxy put in
    X-Forwarded-For) with the rate limits on; returns the status."""
    import app as app_module
    app_instance.config['RATELIMIT_ENABLED'] = True
    app_module.limiter.reset()

    def send(path, form=None, ip=None, user=None):
        if path == '/forgot-password' and form:
            queue_settings(mock_cursor)
        if path in ('/login', '/forgot-password') and form:
            mock_cursor.add_response(fetchone=user)  # the account named in the form
        headers = {'X-Forwarded-For': ip} if ip else {}
        with app_instance.test_client() as c:
            return (c.post(path, data=form, headers=headers) if form else c.get(path, headers=headers)).status_code
    try:
        yield send
    finally:
        app_instance.config['RATELIMIT_ENABLED'] = False


def _fail(name='alice'):
    return {'username': name, 'password': 'wrong'}


def test_failed_sign_ins_are_counted_per_ip_and_account(limited):
    assert [limited('/login', _fail()) for _ in range(10)] == [200] * 10
    assert limited('/login', _fail()) == 429
    assert limited('/login', _fail(' ALICE ')) == 429  # the same account
    # Someone else behind the same address signs in as usual.
    assert limited('/login', _fail('bob')) == 200


def test_page_views_and_successful_sign_ins_do_not_count(limited):
    alice = {'id': 2, 'username': 'alice', 'role': 'user', 'pw_version': 0,
             'password_hash': generate_password_hash('right', method='pbkdf2:sha256:1000')}
    assert {limited('/login') for _ in range(15)} == {200}
    assert {limited('/login', {'username': 'alice', 'password': 'right'}, user=alice) for _ in range(15)} == {302}
    assert [limited('/login', _fail()) for _ in range(10)] == [200] * 10
    assert limited('/login', _fail()) == 429


def test_reset_requests_are_counted_per_ip_and_account(limited):
    assert [limited('/forgot-password', {'username': 'alice'}) for _ in range(5)] == [302] * 5
    assert limited('/forgot-password', {'username': 'alice'}) == 429
    assert limited('/forgot-password', {'username': 'bob'}) == 302


def test_without_proxy_hops_x_forwarded_for_is_ignored(limited):
    # TRUSTED_PROXY_HOPS=0 (the default): the peer address counts, whatever the client claims.
    for i in range(10):
        limited('/login', _fail(), ip=f'203.0.113.{i}')
    assert limited('/login', _fail(), ip='198.51.100.7') == 429


def test_behind_the_configured_proxy_each_client_address_is_counted(app_instance, limited, monkeypatch):
    monkeypatch.setattr(app_instance.wsgi_app, 'x_for', 1)  # TRUSTED_PROXY_HOPS=1
    for _ in range(10):
        limited('/login', _fail(), ip='203.0.113.1')
    assert limited('/login', _fail(), ip='203.0.113.1') == 429
    assert limited('/login', _fail(), ip='203.0.113.2') == 200
    # The proxy appends the address it saw: what the client put in front is not trusted.
    assert limited('/login', _fail(), ip='203.0.113.2, 203.0.113.1') == 429


def test_the_proxy_hops_come_from_the_environment(app_instance):
    # Exactly the configured hops; nothing else a proxy sends is trusted.
    assert (app_instance.wsgi_app.x_for, app_instance.wsgi_app.x_proto, app_instance.wsgi_app.x_host) == (0, 0, 0)
