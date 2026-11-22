"""Every guarded route refuses whoever it is not for: walked from the routes the app serves.

Until 2026-11-22 the routes were checked by a hand list of GETs in the e2e sweep and by the
decorators on dummy views; a user's POST to /admin was tried for 2 of its 5 actions. This walks
`app.url_map` and calls every guarded rule and method as each kind of session the app knows, and
posts every action the page's own forms send. A new route is covered the day it is added, and
fails until POLICY says who may open it.
"""

import inspect
import re
import sys
from collections import defaultdict
from pathlib import Path

import pytest

from tests.testutils import MockCursor, MockDB

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / 'app' / 'templates'

# Who may open each route: None everyone, 'login' a signed-in account, 'admin' an admin.
POLICY = {
    'GET /': None, 'GET /login': None, 'POST /login': None, 'GET /register': None, 'POST /register': None,
    'GET /logout': None, 'GET /forgot-password': None, 'POST /forgot-password': None,
    'GET /reset-password/<token>': None, 'POST /reset-password/<token>': None,
    'GET /devices': 'login', 'POST /devices': 'login', 'GET /account': 'login', 'POST /account': 'login',
    'GET /admin': 'admin', 'POST /admin': 'admin',
}

# The sessions a request can carry: (session, the before_request row, what they may open).
USER = {'user_id': 2, 'username': 'someone', 'role': 'user', 'pw_version': 0}
ADMIN = {**USER, 'role': 'admin'}
PERSONAS = {
    'anonymous': (None, None, set()),
    'deleted account': (USER, None, set()),
    'password changed elsewhere': (USER, {'pw_version': 1, 'role': 'user', 'session_timeout': '1440'}, set()),
    'user': (USER, {'pw_version': 0, 'role': 'user', 'session_timeout': '1440'}, {'login'}),
    'demoted admin': (ADMIN, {'pw_version': 0, 'role': 'user', 'session_timeout': '1440'}, {'login'}),
    'admin': (ADMIN, {'pw_version': 0, 'role': 'admin', 'session_timeout': '1440'}, {'login', 'admin'}),
}


class LenientCursor(MockCursor):
    """Past its queue it answers like an empty database whose rows have every column as NULL:
    enough for any view to render, so reaching a view is told apart from being refused."""

    def fetchone(self):
        row = super().fetchone()
        return row if self._pos <= len(self._responses) else defaultdict(lambda: None)


@pytest.fixture
def walk(app_instance):
    helpers = sys.modules['helpers']
    guards = {
        helpers.admin_required(lambda: None).__code__: 'admin',
        helpers.login_required(lambda: None).__code__: 'login',
    }
    routes = []
    for rule in app_instance.url_map.iter_rules():
        if rule.endpoint == 'static':
            continue
        view = app_instance.view_functions[rule.endpoint]
        for method in sorted(rule.methods - {'HEAD', 'OPTIONS'}):
            routes.append((f'{method} {rule.rule}', guards.get(view.__code__), view))
    return routes


def _actions(view):
    """The `action` values of the forms on the page the view renders (they post back to it)."""
    template = re.search(r"render_template\('([\w.]+)'", inspect.getsource(view)).group(1)
    return re.findall(r'name="action" value="(\w+)"', (TEMPLATES / template).read_text())


def test_the_walk_sees_the_app(walk):
    assert len(walk) >= 16, walk
    assert {key: guard for key, guard, _ in walk} == POLICY
    assert sorted(a for key, _, view in walk if key == 'POST /admin' for a in _actions(view.__wrapped__)) == [
        'delete_user', 'save_settings', 'save_smtp', 'set_public', 'set_role']


@pytest.mark.parametrize('persona', PERSONAS)
def test_every_guarded_route_refuses_whoever_it_is_not_for(app_instance, walk, persona):
    session, row, may = PERSONAS[persona]
    import app as app_module

    checked = 0
    for key, guard, view in walk:
        if not guard:
            continue
        method, path = key.split(' ', 1)
        forms = [{'action': a, 'target_user_id': '3', 'device_db_id': '1', 'mac_address': 'AA:BB:CC:DD:EE:FF'}
                 for a in _actions(view.__wrapped__)] if method == 'POST' else [None]
        for form in forms if guard not in may else [None]:
            cursor = LenientCursor()
            if session:
                cursor.add_response(fetchone=row)
            with app_instance.test_client() as client:
                if session:
                    with client.session_transaction() as sess:
                        sess.update(session)
                with pytest.MonkeyPatch.context() as m:
                    m.setattr(app_module, 'get_db', lambda cursor=cursor: MockDB(cursor))
                    resp = client.open(path, method=method, data=form)
            where = (persona, key, form and form['action'])
            if guard in may:
                # Reached the view: its page, or back to it after a POST.
                assert resp.status_code == 200 or resp.headers['Location'] == path, where
            else:
                refused_to = '/' if 'login' in may else '/login'  # signed in, but not an admin
                assert (resp.status_code, resp.headers.get('Location')) == (302, refused_to), where
                # Nothing past the session check ran.
                assert len(cursor.execute_log) == (1 if session else 0), (where, cursor.execute_log)
            checked += 1
    assert checked >= 6


def test_the_e2e_sweep_opens_every_page(walk):
    sweep = (ROOT / 'e2e' / 'tests' / 'sweep.spec.ts').read_text()
    visited = set(re.findall(r"(?:path: |signedOut\()[`']([^`']+)[`']", sweep))
    visited = {p.replace('${token}', '<token>') for p in visited}
    assert visited == {key.split(' ', 1)[1] for key, _, _ in walk if key.startswith('GET ')}
