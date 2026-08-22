from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch
import sys
import unittest

from services.config import configured_value, optional_streamlit_secret


class SharedConfigurationTests(unittest.TestCase):
    def test_environment_value_does_not_require_streamlit_secrets(self):
        with patch.dict("os.environ", {"SLEEPER_LEAGUE_ID": "production-id"}, clear=True):
            self.assertEqual(configured_value("SLEEPER_LEAGUE_ID"), "production-id")

    def test_missing_secrets_file_is_treated_as_optional(self):
        missing_secrets = SimpleNamespace(
            get=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                RuntimeError("no secrets file")
            )
        )
        fake_streamlit = SimpleNamespace(secrets=missing_secrets)
        with patch.dict(sys.modules, {"streamlit": fake_streamlit}):
            self.assertEqual(optional_streamlit_secret("SLEEPER_LEAGUE_ID"), "")


if __name__ == "__main__":
    unittest.main()
