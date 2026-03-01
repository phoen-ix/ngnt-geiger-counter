"""Unit tests for helpers.py — credential derivation, provisioning, email, decorators."""

import os
from unittest.mock import patch, MagicMock

import pytest

from tests.testutils import MockCursor, MockDB


# ── derive_mqtt_credentials ─────────────────────────────────────────────────

def test_derive_mqtt_credentials_known_values():
    from helpers import derive_mqtt_credentials
    username, password = derive_mqtt_credentials('AA:BB:CC:DD:EE:FF', 'mysecret')
    assert username == 'geiger_ddeeff'
    assert len(password) == 16
    # Deterministic: same inputs always produce same output
    u2, p2 = derive_mqtt_credentials('AA:BB:CC:DD:EE:FF', 'mysecret')
    assert (username, password) == (u2, p2)


def test_derive_mqtt_credentials_case_insensitive_mac():
    from helpers import derive_mqtt_credentials
    u1, p1 = derive_mqtt_credentials('AA:BB:CC:DD:EE:FF', 'pepper')
    u2, p2 = derive_mqtt_credentials('aa:bb:cc:dd:ee:ff', 'pepper')
    assert u1 == u2
    assert p1 == p2


def test_derive_mqtt_credentials_dash_separator():
    from helpers import derive_mqtt_credentials
    u1, p1 = derive_mqtt_credentials('AA:BB:CC:DD:EE:FF', 'pepper')
    u2, p2 = derive_mqtt_credentials('AA-BB-CC-DD-EE-FF', 'pepper')
    assert u1 == u2
    assert p1 == p2


def test_derive_mqtt_credentials_username_format():
    from helpers import derive_mqtt_credentials
    username, _ = derive_mqtt_credentials('11:22:33:44:55:66', 'x')
    assert username == 'geiger_445566'


def test_derive_mqtt_credentials_different_pepper_different_password():
    from helpers import derive_mqtt_credentials
    _, p1 = derive_mqtt_credentials('AA:BB:CC:DD:EE:FF', 'pepper1')
    _, p2 = derive_mqtt_credentials('AA:BB:CC:DD:EE:FF', 'pepper2')
    assert p1 != p2


# ── provision_device / unprovision_device ───────────────────────────────────

def test_provision_device_creates_entry(tmp_path):
    from helpers import provision_device
    conf = tmp_path / 'devices.conf'
    conf.touch()
    with patch('helpers.MOSQUITTO_CONFIG_DIR', str(tmp_path)):
        provision_device('geiger_aabbcc', 'secret123')
    assert 'geiger_aabbcc secret123\n' in conf.read_text()


def test_provision_device_creates_reload_flag(tmp_path):
    from helpers import provision_device
    conf = tmp_path / 'devices.conf'
    conf.touch()
    with patch('helpers.MOSQUITTO_CONFIG_DIR', str(tmp_path)):
        provision_device('geiger_aabbcc', 'secret123')
    assert (tmp_path / '.reload').exists()


def test_provision_device_idempotent(tmp_path):
    from helpers import provision_device
    conf = tmp_path / 'devices.conf'
    conf.touch()
    with patch('helpers.MOSQUITTO_CONFIG_DIR', str(tmp_path)):
        provision_device('geiger_aabbcc', 'secret123')
        provision_device('geiger_aabbcc', 'secret123')
    lines = [l for l in conf.read_text().splitlines() if l.strip()]
    assert len(lines) == 1


def test_provision_device_multiple_devices(tmp_path):
    from helpers import provision_device
    conf = tmp_path / 'devices.conf'
    conf.touch()
    with patch('helpers.MOSQUITTO_CONFIG_DIR', str(tmp_path)):
        provision_device('geiger_aabbcc', 'pw1')
        provision_device('geiger_ddeeff', 'pw2')
    text = conf.read_text()
    assert 'geiger_aabbcc pw1' in text
    assert 'geiger_ddeeff pw2' in text


def test_unprovision_device_removes_entry(tmp_path):
    from helpers import unprovision_device
    conf = tmp_path / 'devices.conf'
    conf.write_text('geiger_aabbcc pw1\ngeiger_ddeeff pw2\n')
    with patch('helpers.MOSQUITTO_CONFIG_DIR', str(tmp_path)):
        unprovision_device('geiger_aabbcc')
    text = conf.read_text()
    assert 'geiger_aabbcc' not in text
    assert 'geiger_ddeeff pw2' in text


def test_unprovision_device_creates_reload_flag(tmp_path):
    from helpers import unprovision_device
    conf = tmp_path / 'devices.conf'
    conf.write_text('geiger_aabbcc pw1\n')
    with patch('helpers.MOSQUITTO_CONFIG_DIR', str(tmp_path)):
        unprovision_device('geiger_aabbcc')
    assert (tmp_path / '.reload').exists()


def test_unprovision_device_preserves_others(tmp_path):
    from helpers import unprovision_device
    conf = tmp_path / 'devices.conf'
    conf.write_text('geiger_aabbcc pw1\ngeiger_ddeeff pw2\ngeiger_112233 pw3\n')
    with patch('helpers.MOSQUITTO_CONFIG_DIR', str(tmp_path)):
        unprovision_device('geiger_ddeeff')
    lines = [l for l in conf.read_text().splitlines() if l.strip()]
    assert len(lines) == 2
    assert 'geiger_aabbcc' in conf.read_text()
    assert 'geiger_112233' in conf.read_text()


def test_unprovision_device_nonexistent(tmp_path):
    from helpers import unprovision_device
    conf = tmp_path / 'devices.conf'
    conf.write_text('geiger_aabbcc pw1\n')
    with patch('helpers.MOSQUITTO_CONFIG_DIR', str(tmp_path)):
        unprovision_device('geiger_nonexistent')
    assert 'geiger_aabbcc pw1' in conf.read_text()


# ── get_settings ────────────────────────────────────────────────────────────

def test_get_settings_returns_dict():
    from helpers import get_settings
    cursor = MockCursor()
    cursor.add_response(fetchall=[
        {'key': 'display_timezone', 'value': 'UTC'},
        {'key': 'offline_timeout_minutes', 'value': '10'},
    ])
    db = MockDB(cursor)
    result = get_settings(db)
    assert result == {'display_timezone': 'UTC', 'offline_timeout_minutes': '10'}


# ── Auth decorators ─────────────────────────────────────────────────────────

def test_login_required_redirects(app_instance):
    with app_instance.test_request_context():
        from helpers import login_required
        from flask import session

        @login_required
        def dummy():
            return 'ok'

        session.clear()
        resp = dummy()
        assert resp.status_code == 302
        assert '/login' in resp.headers['Location']


def test_login_required_passes(app_instance):
    with app_instance.test_request_context():
        from helpers import login_required
        from flask import session

        @login_required
        def dummy():
            return 'ok'

        session['user_id'] = 1
        assert dummy() == 'ok'


def test_admin_required_redirects_no_session(app_instance):
    with app_instance.test_request_context():
        from helpers import admin_required
        from flask import session

        @admin_required
        def dummy():
            return 'ok'

        session.clear()
        resp = dummy()
        assert resp.status_code == 302
        assert '/login' in resp.headers['Location']


def test_admin_required_redirects_non_admin(app_instance):
    with app_instance.test_request_context():
        from helpers import admin_required
        from flask import session

        @admin_required
        def dummy():
            return 'ok'

        session['user_id'] = 1
        session['role'] = 'user'
        resp = dummy()
        assert resp.status_code == 302


def test_admin_required_passes(app_instance):
    with app_instance.test_request_context():
        from helpers import admin_required
        from flask import session

        @admin_required
        def dummy():
            return 'ok'

        session['user_id'] = 1
        session['role'] = 'admin'
        assert dummy() == 'ok'


# ── send_password_reset_email ───────────────────────────────────────────────

def test_send_reset_email_no_smtp_host():
    from helpers import send_password_reset_email
    result = send_password_reset_email('user@example.com', 'token123', {'smtp_host': ''})
    assert result is False


def test_send_reset_email_success():
    from helpers import send_password_reset_email
    settings = {
        'smtp_host': 'smtp.example.com',
        'smtp_port': '587',
        'smtp_user': 'user',
        'smtp_password': 'pass',
        'smtp_from': 'noreply@example.com',
        'smtp_tls': '1',
        'base_url': 'https://geiger.example.com',
    }
    with patch('helpers.smtplib.SMTP') as mock_smtp:
        mock_server = MagicMock()
        mock_smtp.return_value = mock_server
        result = send_password_reset_email('user@example.com', 'token123', settings)

    assert result is True
    mock_server.starttls.assert_called_once()
    mock_server.login.assert_called_once_with('user', 'pass')
    mock_server.sendmail.assert_called_once()
    call_args = mock_server.sendmail.call_args[0]
    assert call_args[0] == 'noreply@example.com'
    assert call_args[1] == ['user@example.com']
    assert 'token123' in call_args[2]


def test_send_reset_email_no_tls():
    from helpers import send_password_reset_email
    settings = {
        'smtp_host': 'smtp.example.com',
        'smtp_port': '25',
        'smtp_user': '',
        'smtp_password': '',
        'smtp_from': 'noreply@example.com',
        'smtp_tls': '0',
        'base_url': '',
    }
    with patch('helpers.smtplib.SMTP') as mock_smtp:
        mock_server = MagicMock()
        mock_smtp.return_value = mock_server
        result = send_password_reset_email('user@example.com', 'token123', settings)

    assert result is True
    mock_server.starttls.assert_not_called()
    mock_server.login.assert_not_called()


def test_send_reset_email_failure():
    from helpers import send_password_reset_email
    settings = {
        'smtp_host': 'smtp.example.com',
        'smtp_port': '587',
        'smtp_user': 'user',
        'smtp_password': 'pass',
        'smtp_from': 'noreply@example.com',
        'smtp_tls': '1',
        'base_url': '',
    }
    with patch('helpers.smtplib.SMTP', side_effect=ConnectionRefusedError('refused')):
        result = send_password_reset_email('user@example.com', 'token123', settings)

    assert result is False
