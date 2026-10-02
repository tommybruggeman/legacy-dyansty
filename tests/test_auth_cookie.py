import base64
import json
import time
import importlib.util
import unittest
from pathlib import Path
from types import SimpleNamespace

# Other test modules stub sys.modules["auth"], so load the real file directly.
_spec = importlib.util.spec_from_file_location("auth_under_test", Path(__file__).resolve().parents[1] / "auth.py")
auth = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(auth)


def _jwt(exp: float) -> str:
    claims = base64.urlsafe_b64encode(json.dumps({"exp": int(exp)}).encode()).decode().rstrip("=")
    return f"header.{claims}.sig"


class FakeStreamlit:
    def __init__(self, cookies=None):
        self.session_state = {}
        self.context = SimpleNamespace(cookies=dict(cookies or {}), url="http://localhost:8501/Front_Office")
        self.html_calls = []

    def html(self, body, unsafe_allow_javascript=False):
        self.html_calls.append(body)

    def switch_page(self, page):
        raise RuntimeError(f"switch_page:{page}")


class FakeAuth:
    def __init__(self, session=None, error=None):
        self.session = session
        self.error = error
        self.calls = []

    def refresh_session(self, refresh):
        self.calls.append(("refresh", refresh))
        if self.error:
            raise self.error
        return SimpleNamespace(session=self.session)

    def set_session(self, access, refresh):
        self.calls.append(("set", access, refresh))
        if self.error:
            raise self.error
        return SimpleNamespace(session=self.session)


class FakeQuery:
    def __init__(self, rows):
        self.rows = rows

    def __getattr__(self, name):
        return lambda *args, **kwargs: self

    def execute(self):
        return SimpleNamespace(data=self.rows)


class FakeClient:
    def __init__(self, auth_api, memberships=()):
        self.auth = auth_api
        self.memberships = list(memberships)
        self.postgrest = SimpleNamespace(auth=lambda token: None)

    def table(self, name):
        assert name == "league_memberships"
        return FakeQuery(self.memberships)


def _session(access, refresh="r2"):
    return SimpleNamespace(
        access_token=access,
        refresh_token=refresh,
        user=SimpleNamespace(id="user-1", email="gm@example.com"),
    )


class AuthCookieTests(unittest.TestCase):
    def setUp(self):
        self._orig_st = auth.st
        self._orig_anon = auth._sb_anon

    def tearDown(self):
        auth.st = self._orig_st
        auth._sb_anon = self._orig_anon

    def install(self, cookies=None, client=None):
        fake = FakeStreamlit(cookies)
        auth.st = fake
        if client is not None:
            auth._sb_anon = lambda: client
        return fake

    def test_cookie_round_trip(self):
        payload = {"a": "access", "r": "refresh", "l": "league-1"}
        self.assertEqual(auth._decode_cookie(auth._encode_cookie(payload)), payload)
        self.assertIsNone(auth._decode_cookie("not-base64!"))
        self.assertIsNone(auth._decode_cookie(auth._encode_cookie({"a": "only-access"})))

    def test_refreshed_tab_restores_user_and_league_from_cookie(self):
        access = _jwt(time.time() + 3600)
        cookie = auth._encode_cookie({"a": access, "r": "r1", "l": "league-2"})
        client = FakeClient(
            FakeAuth(_session(access, "r1")),
            memberships=[
                {"league_id": "league-1", "role": "member"},
                {"league_id": "league-2", "role": "commissioner"},
            ],
        )
        st = self.install({auth.AUTH_COOKIE: cookie}, client)

        self.assertTrue(auth.is_logged_in())
        self.assertEqual(client.auth.calls, [("set", access, "r1")])
        self.assertEqual(st.session_state[auth.USER_KEY]["id"], "user-1")
        self.assertEqual(st.session_state[auth.ACTIVE_LEAGUE_KEY], "league-2")
        self.assertEqual(st.session_state[auth.ROLE_KEY], "commissioner")

    def test_expired_access_token_refreshes_and_rewrites_cookie(self):
        new_access = _jwt(time.time() + 3600)
        cookie = auth._encode_cookie({"a": _jwt(time.time() - 10), "r": "r1", "l": None})
        client = FakeClient(FakeAuth(_session(new_access, "r2")), memberships=[{"league_id": "league-1", "role": "member"}])
        st = self.install({auth.AUTH_COOKIE: cookie}, client)

        auth.require_login()

        self.assertEqual(client.auth.calls, [("refresh", "r1")])
        self.assertEqual(st.session_state[auth.REFRESH_KEY], "r2")
        self.assertEqual(len(st.html_calls), 1)
        self.assertEqual(auth._decode_cookie(st.session_state[auth.COOKIE_WRITTEN_KEY])["r"], "r2")

        # Nothing changed on the next rerun, so no second cookie write.
        auth.require_login()
        self.assertEqual(len(st.html_calls), 1)

    def test_revoked_cookie_redirects_and_is_cleared(self):
        cookie = auth._encode_cookie({"a": _jwt(time.time() - 10), "r": "dead", "l": None})
        st = self.install({auth.AUTH_COOKIE: cookie}, FakeClient(FakeAuth(error=RuntimeError("revoked"))))

        with self.assertRaisesRegex(RuntimeError, "switch_page:home.py"):
            auth.require_login()

        # home.py's signed-out view clears it after the redirect.
        self.assertEqual(st.html_calls, [])
        auth.persist_auth_cookie()
        self.assertEqual(len(st.html_calls), 1)
        self.assertIn("Max-Age=0", st.html_calls[0])

    def test_sign_out_is_not_undone_by_cookie(self):
        access = _jwt(time.time() + 3600)
        cookie = auth._encode_cookie({"a": access, "r": "r1", "l": None})
        st = self.install({auth.AUTH_COOKIE: cookie}, FakeClient(FakeAuth(_session(access, "r1"))))

        self.assertTrue(auth.is_logged_in())
        auth.sign_out()

        self.assertFalse(auth.is_logged_in())
        auth.persist_auth_cookie()
        self.assertIn("Max-Age=0", st.html_calls[-1])

    def test_no_cookie_means_signed_out_without_writes(self):
        st = self.install()
        self.assertFalse(auth.is_logged_in())
        auth.persist_auth_cookie()
        self.assertEqual(st.html_calls, [])


if __name__ == "__main__":
    unittest.main()
