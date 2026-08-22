import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "tests/phase_e_pagination_integration/run_phase_e_hosted_certification.py"
SPEC = importlib.util.spec_from_file_location("phase_e_hosted_runner", RUNNER)
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


class ModernSupabaseHeaderTests(unittest.TestCase):
    def test_publishable_key_is_apikey_only_for_password_sign_in(self):
        headers = runner.api_headers("sb_publishable_example", "publishable")
        self.assertEqual(headers, {"apikey": "sb_publishable_example"})
        self.assertNotIn("Authorization", headers)

    def test_secret_key_is_apikey_only_for_admin_requests(self):
        headers = runner.api_headers("sb_secret_example", "secret")
        self.assertEqual(headers, {"apikey": "sb_secret_example"})
        self.assertNotIn("Authorization", headers)

    def test_user_jwt_is_the_only_authorization_bearer(self):
        token = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1Iiwicm9sZSI6ImF1dGhlbnRpY2F0ZWQifQ.signature"
        headers = runner.api_headers("sb_publishable_example", "publishable", user_token=token)
        self.assertEqual(headers["apikey"], "sb_publishable_example")
        self.assertEqual(headers["Authorization"], f"Bearer {token}")

    def test_opaque_project_keys_are_rejected_as_bearers(self):
        for key in ("sb_publishable_example", "sb_secret_example"):
            with self.assertRaisesRegex(ValueError, "user access token"):
                runner.api_headers("sb_publishable_example", "publishable", user_token=key)
        with self.assertRaisesRegex(ValueError, "user access token"):
            runner.api_headers("sb_secret_example", "secret", user_token="user.jwt.token")

    def test_sanitized_diagnostic_contains_no_key_material(self):
        raw = b'{"error_code":"bad_jwt","message":"invalid project API key"}'
        message = runner.sanitized_http_error("https://example.test/auth/v1/admin/users", 401, raw, "secret")
        self.assertIn("endpoint=/auth/v1/admin/users", message)
        self.assertIn("status=401", message)
        self.assertIn("key_type=secret", message)
        self.assertNotIn("sb_secret_", message)


if __name__ == "__main__":
    unittest.main()
