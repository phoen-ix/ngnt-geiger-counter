"""Unit tests for helpers.py — credential derivation, provisioning, email, decorators."""

import base64
import hashlib
import os
import re
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

def _hash_matches(line, device_id, password):
    """A devices.conf line in Mosquitto's $7$ (PBKDF2-SHA512) password-file format."""
    user, _, rounds, salt, digest = line.strip().split('$')
    expected = hashlib.pbkdf2_hmac('sha512', password.encode(), base64.b64decode(salt), int(rounds))
    return user == f'{device_id}:' and base64.b64decode(digest) == expected


def test_provision_device_stores_only_a_hash(tmp_path):
    from helpers import provision_device
    conf = tmp_path / 'devices.conf'
    conf.touch()
    with patch('helpers.MOSQUITTO_CONFIG_DIR', str(tmp_path)):
        provision_device('geiger_aabbcc', 'secret123')
    text = conf.read_text()
    assert 'secret123' not in text
    assert _hash_matches(text, 'geiger_aabbcc', 'secret123')


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
    first, second = conf.read_text().splitlines()
    assert _hash_matches(first, 'geiger_aabbcc', 'pw1')
    assert _hash_matches(second, 'geiger_ddeeff', 'pw2')


def test_legacy_plaintext_lines_are_hashed(tmp_path):
    from helpers import hash_legacy_device_passwords, provision_device
    conf = tmp_path / 'devices.conf'
    conf.write_text('geiger_aabbcc pw1\n')
    with patch('helpers.MOSQUITTO_CONFIG_DIR', str(tmp_path)):
        provision_device('geiger_ddeeff', 'pw2')
        provision_device('geiger_aabbcc', 'pw1')   # already there in the old format
        hashed_new = conf.read_text().splitlines()[1]
        hash_legacy_device_passwords()
    first, second = conf.read_text().splitlines()
    assert _hash_matches(first, 'geiger_aabbcc', 'pw1')
    assert second == hashed_new


def test_hash_legacy_device_passwords_without_a_file(tmp_path):
    from helpers import hash_legacy_device_passwords
    with patch('helpers.MOSQUITTO_CONFIG_DIR', str(tmp_path)):
        hash_legacy_device_passwords()
    assert not (tmp_path / 'devices.conf').exists()


def test_unprovision_device_removes_hashed_entry(tmp_path):
    from helpers import provision_device, unprovision_device
    conf = tmp_path / 'devices.conf'
    conf.touch()
    with patch('helpers.MOSQUITTO_CONFIG_DIR', str(tmp_path)):
        provision_device('geiger_aabbcc', 'pw1')
        provision_device('geiger_ddeeff', 'pw2')
        unprovision_device('geiger_aabbcc')
    [line] = conf.read_text().splitlines()
    assert _hash_matches(line, 'geiger_ddeeff', 'pw2')


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
        'smtp_from': 'noreply@geiger.example',
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
    assert call_args[0] == 'noreply@geiger.example'
    assert call_args[1] == ['user@example.com']
    assert 'token123' in call_args[2]


def test_send_reset_email_no_tls():
    from helpers import send_password_reset_email
    settings = {
        'smtp_host': 'smtp.example.com',
        'smtp_port': '25',
        'smtp_user': '',
        'smtp_password': '',
        'smtp_from': 'noreply@geiger.example',
        'smtp_tls': '0',
        'base_url': 'https://geiger.example.com',
    }
    with patch('helpers.smtplib.SMTP') as mock_smtp:
        mock_server = MagicMock()
        mock_smtp.return_value = mock_server
        result = send_password_reset_email('user@example.com', 'token123', settings)

    assert result is True
    mock_server.starttls.assert_not_called()
    mock_server.login.assert_not_called()


def test_send_reset_email_empty_from_uses_smtp_user():
    from helpers import send_password_reset_email
    settings = {'smtp_host': 'smtp.example.com', 'smtp_user': 'geiger@geiger.example', 'smtp_from': '',
                'base_url': 'https://geiger.example.com'}
    with patch('helpers.smtplib.SMTP') as mock_smtp:
        send_password_reset_email('user@example.com', 'token123', settings)
    assert mock_smtp.return_value.sendmail.call_args[0][0] == 'geiger@geiger.example'


def test_send_reset_email_failure():
    from helpers import send_password_reset_email
    settings = {
        'smtp_host': 'smtp.example.com',
        'smtp_port': '587',
        'smtp_user': 'user',
        'smtp_password': 'pass',
        'smtp_from': 'noreply@geiger.example',
        'smtp_tls': '1',
        'base_url': 'https://geiger.example.com',
    }
    with patch('helpers.smtplib.SMTP', side_effect=ConnectionRefusedError('refused')):
        result = send_password_reset_email('user@example.com', 'token123', settings)

    assert result is False


RESET_SMTP = {'smtp_host': 'smtp.example.com', 'smtp_port': '587', 'smtp_from': 'noreply@geiger.example',
              'base_url': 'https://geiger.example.com'}


def test_send_reset_email_headers_ehlo_and_verified_tls(monkeypatch):
    import email
    import ssl
    from helpers import send_password_reset_email
    monkeypatch.setenv('SITE_NAME', 'Geiger')
    with patch('helpers.smtplib.SMTP') as mock_smtp:
        assert send_password_reset_email('user@example.com', 'token123', RESET_SMTP)
    assert mock_smtp.call_args.kwargs == {'local_hostname': 'geiger.example', 'timeout': 10}
    context = mock_smtp.return_value.starttls.call_args.kwargs['context']
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
    msg = email.message_from_string(mock_smtp.return_value.sendmail.call_args[0][2])
    assert msg['From'] == 'Geiger <noreply@geiger.example>'
    assert msg['Message-ID'].endswith('@geiger.example>') and msg['Date']
    assert msg['Auto-Submitted'] == 'auto-generated'
    assert msg.get_content_type() == 'text/plain'


def test_send_reset_email_ehlo_name_from_settings():
    from helpers import send_password_reset_email
    with patch('helpers.smtplib.SMTP') as mock_smtp:
        assert send_password_reset_email('user@example.com', 'token123',
                                         {**RESET_SMTP, 'smtp_ehlo_name': 'mx.geiger.example'})
    assert mock_smtp.call_args.kwargs['local_hostname'] == 'mx.geiger.example'


def test_send_reset_email_port_465_is_implicit_tls():
    import ssl
    from helpers import send_password_reset_email
    with patch('helpers.smtplib.SMTP_SSL') as mock_ssl, patch('helpers.smtplib.SMTP') as mock_smtp:
        assert send_password_reset_email('user@example.com', 'token123', {**RESET_SMTP, 'smtp_port': '465'})
    mock_smtp.assert_not_called()
    assert mock_ssl.call_args.kwargs['context'].verify_mode == ssl.CERT_REQUIRED
    mock_ssl.return_value.starttls.assert_not_called()


@pytest.mark.parametrize('sender', ['noreply@example.com', 'geiger@geiger.local', 'geiger@localhost', ''])
def test_send_reset_email_refuses_a_placeholder_from(sender):
    from helpers import send_password_reset_email
    with patch('helpers.smtplib.SMTP') as mock_smtp:
        assert send_password_reset_email('user@example.com', 'token123', {**RESET_SMTP, 'smtp_from': sender}) is False
    mock_smtp.assert_not_called()


def test_send_reset_email_without_base_url_sends_nothing():
    """The link used to fall back to http://localhost:8000, which no user can open."""
    from helpers import send_password_reset_email
    with patch('helpers.smtplib.SMTP') as mock_smtp:
        assert send_password_reset_email('user@example.com', 'token123', {**RESET_SMTP, 'base_url': ''}) is False
    mock_smtp.assert_not_called()
