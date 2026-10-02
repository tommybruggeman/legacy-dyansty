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
    """Session state plus a cookie bridge whose browser side is simulated."""

    def __init__(self, browser_cookie=None):
        self.session_state = {}
        self.context = SimpleNamespace(url="https://legacy-dynasty.streamlit.app/My_Team")
        self.browser_cookie = browser_cookie or ""
        self.writes = []
        self.components = SimpleNamespace(v2=SimpleNamespace(component=lambda name, js: self._mount))

    def _mount(self, key, data, on_cookie_change, height):
        if data["write"]:
            self.writes.append(data["write"])
            value = data["write"].split(";", 1)[0].split("=", 1)[1]
            self.browser_cookie = "" if "Max-Age=0" in data["write"] else value
        if self.browser_cookie != data["reported"]:
            self.session_state[key] = {"cookie": self.browser_cookie}
            on_cookie_change()

    def switch_page(self, page):
        raise RuntimeError(f"switch_page:{page}")

    def stop(self):
        raise RuntimeError("stop")


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
        self._orig = (auth.st, auth._sb_anon, auth._auth_bridge)
        auth._auth_bridge = None

    def tearDown(self):
        auth.st, auth._sb_anon, auth._auth_bridge = self._orig

    def install(self, browser_cookie=None, client=None):
        fake = FakeStreamlit(browser_cookie)
        auth.st = fake
        if client is not None:
            auth._sb_anon = lambda: client
        return fake

    def load_page(self):
        """First run of a fresh tab: waits for the browser report, then reruns."""
        with self.assertRaisesRegex(RuntimeError, "^stop$"):
            auth.require_login()
        auth.require_login()

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
        st = self.install(cookie, client)

        self.load_page()

        self.assertEqual(client.auth.calls, [("set", access, "r1")])
        self.assertEqual(st.session_state[auth.USER_KEY]["id"], "user-1")
        self.assertEqual(st.session_state[auth.ACTIVE_LEAGUE_KEY], "league-2")
        self.assertEqual(st.session_state[auth.ROLE_KEY], "commissioner")
        self.assertEqual(st.writes, [])

    def test_expired_access_token_refreshes_and_rewrites_cookie(self):
        new_access = _jwt(time.time() + 3600)
        cookie = auth._encode_cookie({"a": _jwt(time.time() - 10), "r": "r1", "l": None})
        client = FakeClient(FakeAuth(_session(new_access, "r2")), memberships=[{"league_id": "league-1", "role": "member"}])
        st = self.install(cookie, client)

        self.load_page()

        self.assertEqual(client.auth.calls, [("refresh", "r1")])
        self.assertEqual(st.session_state[auth.REFRESH_KEY], "r2")
        self.assertEqual(len(st.writes), 1)
        self.assertIn("Secure", st.writes[0])
        self.assertEqual(auth._decode_cookie(st.browser_cookie)["r"], "r2")

        # Nothing changed on the next rerun, so no second cookie write.
        auth.require_login()
        self.assertEqual(len(st.writes), 1)

    def test_revoked_cookie_redirects_and_is_cleared(self):
        cookie = auth._encode_cookie({"a": _jwt(time.time() - 10), "r": "dead", "l": None})
        st = self.install(cookie, FakeClient(FakeAuth(error=RuntimeError("revoked"))))

        with self.assertRaisesRegex(RuntimeError, "^stop$"):
            auth.require_login()
        with self.assertRaisesRegex(RuntimeError, "switch_page:home.py"):
            auth.require_login()

        self.assertEqual(st.browser_cookie, "")

    def test_sign_out_deletes_cookie_and_is_not_undone(self):
        access = _jwt(time.time() + 3600)
        cookie = auth._encode_cookie({"a": access, "r": "r1", "l": None})
        st = self.install(cookie, FakeClient(FakeAuth(_session(access, "r1"))))

        self.load_page()
        auth.sign_out()

        self.assertTrue(auth.sync_auth_cookie())
        self.assertFalse(auth.is_logged_in())
        self.assertEqual(st.browser_cookie, "")

    def test_sign_in_writes_cookie_on_next_page(self):
        access = _jwt(time.time() + 3600)
        st = self.install(None, FakeClient(FakeAuth(_session(access, "r1"))))

        with self.assertRaisesRegex(RuntimeError, "^stop$"):
            auth.require_login()
        st.session_state.update(
            {
                auth.USER_KEY: {"id": "user-1", "email": "gm@example.com"},
                auth.ACCESS_KEY: access,
                auth.REFRESH_KEY: "r1",
            }
        )
        auth.require_login()

        self.assertEqual(auth._decode_cookie(st.browser_cookie)["r"], "r1")

    def test_no_cookie_means_signed_out_without_writes(self):
        st = self.install()
        with self.assertRaisesRegex(RuntimeError, "^stop$"):
            auth.require_login()
        with self.assertRaisesRegex(RuntimeError, "switch_page:home.py"):
            auth.require_login()
        self.assertEqual(st.writes, [])


if __name__ == "__main__":
    unittest.main()
