from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import importlib
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]


class _FakePostgrest:
    def __init__(self):
        self.tokens = []

    def auth(self, token):
        self.tokens.append(token)


class _FakeAuth:
    def set_session(self, access_token, refresh_token):
        return SimpleNamespace(
            session=SimpleNamespace(
                access_token="refreshed-user-jwt",
                refresh_token="refreshed-refresh-token",
            )
        )


class _FakeClient:
    def __init__(self):
        self.auth = _FakeAuth()
        self.postgrest = _FakePostgrest()


class AuthenticatedSeasonClientTests(unittest.TestCase):
    def test_refreshed_user_jwt_is_explicitly_applied_to_postgrest(self):
        loaded_auth = sys.modules.get("auth")
        if loaded_auth is not None and not hasattr(loaded_auth, "ACCESS_KEY"):
            sys.modules.pop("auth", None)
        auth = importlib.import_module("auth")
        fake_client = _FakeClient()
        session_state = {
            auth.ACCESS_KEY: "original-user-jwt",
            auth.REFRESH_KEY: "original-refresh-token",
        }
        env = {
            "SUPABASE_URL": "https://project.supabase.co",
            "SUPABASE_ANON_KEY": "anon-key",
        }

        with patch.dict(auth.os.environ, env, clear=True), patch.object(
            auth.st, "session_state", session_state
        ), patch.object(auth, "create_client", return_value=fake_client):
            result = auth._sb(session_state[auth.ACCESS_KEY])

        self.assertIs(result, fake_client)
        self.assertEqual(fake_client.postgrest.tokens, ["refreshed-user-jwt"])
        self.assertEqual(session_state[auth.ACCESS_KEY], "refreshed-user-jwt")
        self.assertEqual(session_state[auth.REFRESH_KEY], "refreshed-refresh-token")

    def test_season_resolver_call_sites_use_authenticated_client(self):
        expected = {
            ROOT / "pages" / "02_My_Team.py":
                "SeasonResolver(auth_client()).get_active_season(league_id)",
            ROOT / "pages" / "90_Settings.py":
                "SeasonResolver(sb_client).get_active_season(active_league_id)",
            ROOT / "services" / "app_context.py":
                "SeasonResolver(auth_client()).get_active_season(league_id)",
        }
        for path, marker in expected.items():
            with self.subTest(path=path.name):
                source = path.read_text()
                self.assertIn(marker, source)
                self.assertIn("auth_client", source)


if __name__ == "__main__":
    unittest.main()
