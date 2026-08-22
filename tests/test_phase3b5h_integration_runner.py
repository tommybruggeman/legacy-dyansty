import contextlib
import importlib.util
import io
from pathlib import Path
import unittest


RUNNER = Path(__file__).resolve().parents[1] / "supabase/tests/phase3b5h_integration/run_phase3b5h_integration.py"


class IntegrationRunnerCredentialSafetyTests(unittest.TestCase):
    def test_redaction_removes_fake_database_url_everywhere(self):
        spec = importlib.util.spec_from_file_location("phase3b5h_runner", RUNNER)
        module = importlib.util.module_from_spec(spec)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            spec.loader.exec_module(module)
        secret = "postgresql://user:FAKE_SECRET@example.invalid:5432/db"
        rendered = module.redact(f"stdout={secret}\nstderr={secret}", secret)
        captured = out.getvalue() + err.getvalue() + rendered
        self.assertNotIn(secret, captured)
        self.assertNotIn("FAKE_SECRET", captured)
        self.assertIn("[REDACTED_DATABASE_URL]", rendered)


if __name__ == "__main__":
    unittest.main()
