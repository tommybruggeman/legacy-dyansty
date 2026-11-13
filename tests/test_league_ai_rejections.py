import unittest

from league_ai.client import ClaudeClient, _classify, human_message
from league_ai.config import LeagueAIConfig


class BadRequestError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class _Messages:
    def __init__(self, exc):
        self.exc = exc
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        raise self.exc


class _SDK:
    def __init__(self, exc):
        self.messages = _Messages(exc)


CFG = LeagueAIConfig(enabled=True, api_key_present=True, model="m", max_output_tokens=1000, max_tool_calls=3, timeout_seconds=5)
NO_CREDIT = BadRequestError("Error code: 400 - Your credit balance is too low to access the Anthropic API.")


class RejectionTests(unittest.TestCase):
    def test_empty_balance_is_not_reported_as_a_bug(self):
        self.assertEqual(_classify(NO_CREDIT), "out_of_credits")
        self.assertIn("credits have run out", human_message("out_of_credits"))
        self.assertNotIn("bug", human_message("out_of_credits"))

    def test_malformed_request_is_still_a_bad_request(self):
        self.assertEqual(_classify(BadRequestError("messages.1.content: tool_result must follow tool_use")), "bad_request")

    def test_no_credit_answer_skips_the_effort_retry(self):
        sdk = _SDK(NO_CREDIT)
        out = ClaudeClient(CFG, sdk_client=sdk).run(system=[], messages=[{"role": "user", "content": "hi"}], turn_effort="low")
        self.assertFalse(out.ok)
        self.assertEqual(out.error_code, "out_of_credits")
        self.assertEqual(sdk.messages.calls, 1)  # no second paid attempt without the per-turn effort


if __name__ == "__main__":
    unittest.main()
