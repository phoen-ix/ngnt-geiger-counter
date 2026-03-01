"""Shared test fixtures for the NGNT Geiger Counter Flask app."""

import os
import sys
from unittest.mock import patch

import pytest

# ── Path setup ──────────────────────────────────────────────────────────────
# app.py does `from helpers import ...` which requires app/ on sys.path
APP_DIR = os.path.join(os.path.dirname(__file__), '..', 'app')
sys.path.insert(0, os.path.abspath(APP_DIR))

# Set required env vars before any imports
os.environ.setdefault('MARIADB_USER', 'test')
os.environ.setdefault('MARIADB_PASSWORD', 'test')
os.environ.setdefault('MARIADB_DATABASE', 'testdb')
os.environ.setdefault('FLASK_SECRET_KEY', 'test-secret-key')
os.environ.setdefault('SITE_NAME', 'Test Geiger')

from tests.testutils import MockCursor, MockDB  # noqa: E402


# ── App fixture (session-scoped) ────────────────────────────────────────────

@pytest.fixture(scope='session')
def app_instance():
    """Import app.py with get_db mocked to avoid real DB during bootstrap."""
    bootstrap_cursor = MockCursor()
    # bootstrap_admin checks: SELECT COUNT(*) AS cnt FROM users
    bootstrap_cursor.add_response(fetchone={'cnt': 1})  # users exist → skip

    def make_bootstrap_db():
        return MockDB(bootstrap_cursor)

    with patch('helpers.get_db', side_effect=make_bootstrap_db):
        # Clear cached modules to force fresh import under mock
        for mod in list(sys.modules):
            if mod in ('app', 'helpers'):
                del sys.modules[mod]
        import app as app_module

    flask_app = app_module.app
    flask_app.config['TESTING'] = True
    flask_app.config['WTF_CSRF_ENABLED'] = False
    flask_app.config['RATELIMIT_ENABLED'] = False
    yield flask_app


# ── Per-test fixtures ───────────────────────────────────────────────────────

@pytest.fixture
def mock_cursor():
    """Fresh MockCursor per test, patched as app.get_db."""
    cursor = MockCursor()

    def make_db():
        return MockDB(cursor)

    with patch('app.get_db', side_effect=make_db):
        yield cursor


@pytest.fixture
def client(app_instance, mock_cursor):
    """Flask test client with CSRF and rate limiting disabled."""
    import app as app_module
    app_instance.config['RATELIMIT_ENABLED'] = False
    try:
        app_module.limiter.reset()
    except Exception:
        pass
    with app_instance.test_client() as c:
        yield c


@pytest.fixture
def auth_client(client, mock_cursor):
    """Client with a regular user session pre-set."""
    with client.session_transaction() as sess:
        sess['user_id'] = 1
        sess['username'] = 'testuser'
        sess['role'] = 'user'
        sess['pw_version'] = 0
    return client


@pytest.fixture
def admin_client(client, mock_cursor):
    """Client with an admin session pre-set."""
    with client.session_transaction() as sess:
        sess['user_id'] = 1
        sess['username'] = 'admin'
        sess['role'] = 'admin'
        sess['pw_version'] = 0
    return client
