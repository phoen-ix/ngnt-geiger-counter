"""Shared mock infrastructure and helpers for the test suite."""


class MockCursor:
    """A cursor that serves queued responses in order.

    Position advances on fetchone()/fetchall(), NOT on execute().
    This means INSERT/UPDATE/DELETE queries that don't fetch don't consume a slot.
    """

    def __init__(self):
        self._responses = []
        self._pos = 0
        self.execute_log = []
        self.rowcount = 1

    def execute(self, sql, params=None):
        self.execute_log.append((sql, params))

    def fetchone(self):
        if self._pos < len(self._responses):
            result = self._responses[self._pos].get('fetchone')
            self._pos += 1
            return result
        self._pos += 1
        return None

    def fetchall(self):
        if self._pos < len(self._responses):
            result = self._responses[self._pos].get('fetchall', [])
            self._pos += 1
            return result
        self._pos += 1
        return []

    def add_response(self, fetchone=None, fetchall=None):
        """Queue a response for the next fetchone()/fetchall() call."""
        self._responses.append({'fetchone': fetchone, 'fetchall': fetchall})

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


class MockDB:
    """Minimal DB connection mock wrapping a shared cursor."""

    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor

    def close(self):
        pass


# ── Helper functions for building mock responses ────────────────────────────

DEFAULT_SETTINGS = {
    'offline_timeout_minutes': '5',
    'display_timezone': 'Europe/Vienna',
    'default_cpm_factor': '0.0057',
    'default_alert_threshold': '0.5',
    'registration_enabled': '1',
    'session_timeout_minutes': '1440',
    'base_url': '',
    'smtp_host': '',
    'smtp_port': '587',
    'smtp_user': '',
    'smtp_password': '',
    'smtp_from': '',
    'smtp_tls': '1',
}


def make_settings_response(**overrides):
    """Build a fetchall response for get_settings()."""
    settings = dict(DEFAULT_SETTINGS)
    settings.update(overrides)
    return [{'key': k, 'value': v} for k, v in settings.items()]


def queue_before_request(cursor, role='user', pw_version=0):
    """Queue the 2 DB calls that before_request makes for authenticated requests."""
    cursor.add_response(fetchone={'pw_version': pw_version, 'role': role})
    cursor.add_response(fetchone={'value': '1440'})


def queue_settings(cursor, **overrides):
    """Queue a get_settings() response (fetchall of key/value rows)."""
    cursor.add_response(fetchall=make_settings_response(**overrides))
