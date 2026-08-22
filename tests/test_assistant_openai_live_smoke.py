from __future__ import annotations

import json
import os
import unittest

from gm_assistant.evaluation import synthetic_qb_shortage_request
from gm_assistant.openai_reasoning import (
    OpenAIReasoningProvider,
    configuration_status,
    live_smoke_permitted,
    validate_reasoning_response,
)


@unittest.skipUnless(
    os.getenv("LEGACY_OPENAI_LIVE_TEST", "").strip().lower() in {"1", "true", "yes", "on"}
    and live_smoke_permitted(),
    "Live OpenAI smoke test requires LEGACY_OPENAI_LIVE_TEST=1, OPENAI_REASONING_ENABLED=true, and a local OPENAI_API_KEY.",
)
class OpenAILiveSmokeTest(unittest.TestCase):
    def test_synthetic_qb_shortage_trade_smoke(self):
        status = configuration_status()
        self.assertTrue(status.configuration_valid)
        self.assertTrue(status.live_testing_permitted)

        request = synthetic_qb_shortage_request()
        result = OpenAIReasoningProvider.from_environment().reason(request)

        if not result.ok:
            details = result.trace.provider_error_details if result.trace else {}
            self.fail(f"{result.error_code}: {details}")
        self.assertIsNotNone(result.response)
        validation = validate_reasoning_response(request, result.response)
        if not validation.ok:
            self.fail(_validation_failure_diagnostic(request, result.response, validation.errors))
        self.assertNotIn("sk-", repr(result.response.to_payload()))
        self.assertNotIn("9838a0a1", repr(result.response.to_payload()))
        self.assertLessEqual(len(result.trace.evidence_domains_included if result.trace else []), 8)


def _validation_failure_diagnostic(request, response, errors):
    returned_refs = list(response.facts_used or [])
    allowed_refs = sorted(set(request.allowed_fact_refs or []))
    allowed_set = set(allowed_refs)
    unknown_refs = [ref for ref in returned_refs if ref not in allowed_set]
    classification = {
        ref: _classify_unknown_ref(ref, allowed_set, request.to_payload())
        for ref in unknown_refs
    }
    payload = {
        "validation_errors": list(errors),
        "parsed_structured_response": response.to_payload(),
        "returned_fact_refs": returned_refs,
        "allowed_fact_refs": allowed_refs,
        "unknown_fact_refs": unknown_refs,
        "unknown_ref_classification": classification,
    }
    return json.dumps(payload, indent=2, sort_keys=True, default=str)


def _classify_unknown_ref(ref, allowed_refs, request_payload):
    normalized = _normalize_ref(ref)
    normalized_allowed = {_normalize_ref(item): item for item in allowed_refs}
    if normalized in normalized_allowed:
        return {
            "category": "formatting_mismatch",
            "closest_allowed_ref": normalized_allowed[normalized],
        }
    base = str(ref or "").split(":", 1)[0].strip()
    normalized_base = _normalize_ref(base)
    if normalized_base in normalized_allowed:
        return {
            "category": "formatting_mismatch",
            "closest_allowed_ref": normalized_allowed[normalized_base],
            "reason": "The model returned a valid fact ID with extra descriptive text appended.",
        }
    if _ref_text_present(ref, request_payload):
        return {
            "category": "missing_evidence_id",
            "reason": "The concept appears in the request payload, but the exact ID is not in allowed_fact_refs.",
        }
    return {
        "category": "model_fabrication",
        "reason": "The returned fact ID is not present in allowed_fact_refs and does not appear in the request payload.",
    }


def _normalize_ref(ref):
    return str(ref or "").strip().lower().replace("-", "_").replace(" ", "_")


def _ref_text_present(ref, payload):
    needle = _normalize_ref(ref).split(".")[-1]
    return bool(needle and needle in _normalize_ref(json.dumps(payload, sort_keys=True, default=str)))


if __name__ == "__main__":
    unittest.main()
