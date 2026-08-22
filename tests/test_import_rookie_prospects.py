from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from scripts.import_rookie_prospects import (
    PLAYER_UNIVERSE_COLUMNS,
    import_rookie_prospects,
    project_player_universe_upserts,
)
from services.rookie_prospects import build_completed_draft_import_plan


def official_record(**updates):
    row = {
        "player_name": "Test Rookie",
        "search_name": "test rookie",
        "pos": "WR",
        "nfl_team": "DEN",
        "draft_year": 2026,
        "rookie_class_year": 2026,
        "draft_round": 2,
        "draft_pick": 40,
        "college": "Boise State",
        "source": "NFL.com 2026 Draft final results",
        "source_updated_at": "2026-04-26",
    }
    row.update(updates)
    return row


class Response:
    def __init__(self, data=None):
        self.data = data


class Query:
    def __init__(self, client, table):
        self.client = client
        self.table = table
        self.action = "select"

    def select(self, _columns):
        return self

    def upsert(self, payload, on_conflict=None):
        self.action = "upsert"
        self.client.events.append(("upsert", payload, on_conflict))
        return self

    def delete(self):
        self.action = "delete"
        self.client.events.append(("delete", self.table))
        return self

    def eq(self, column, value):
        self.client.events.append(("eq", column, value))
        return self

    def execute(self):
        if self.action == "select":
            self.client.events.append(("select", self.table))
            return Response(self.client.existing)
        if self.action == "upsert" and self.client.fail_upsert:
            self.client.events.append(("upsert_failed",))
            raise RuntimeError("simulated PostgREST failure")
        self.client.events.append(("execute",))
        return Response([])


class Client:
    def __init__(self, existing=(), fail_upsert=False):
        self.existing = list(existing)
        self.fail_upsert = fail_upsert
        self.events = []

    def table(self, name):
        return Query(self, name)


class RookieImporterPersistenceTest(unittest.TestCase):
    def _files(self, record=None, sleeper=None):
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        input_path = root / "rookies.json"
        sleeper_path = root / "sleeper.json"
        input_path.write_text(json.dumps([record or official_record()]))
        sleeper_path.write_text(json.dumps(sleeper or {
            "9001": {"full_name": "Test Rookie", "position": "WR", "team": "DEN", "active": True}
        }))
        return temp, input_path, sleeper_path

    def test_source_metadata_stays_in_report_but_not_upsert(self):
        plan = build_completed_draft_import_plan(
            [official_record()],
            {"9001": {"full_name": "Test Rookie", "position": "WR", "team": "DEN", "active": True}},
            [],
        )
        self.assertEqual(plan.reports[0].source, "NFL.com 2026 Draft final results")
        self.assertEqual(plan.reports[0].source_updated_at, "2026-04-26")
        payload = project_player_universe_upserts([dict(plan.upserts[0])])
        self.assertNotIn("source", payload[0])
        self.assertNotIn("source_updated_at", payload[0])

    def test_projection_keeps_all_valid_rookie_fields(self):
        payload = project_player_universe_upserts([{
            **official_record(), "sleeper_id": "9001", "canonical_player_id": "9001",
            "gsis_id": "00-001", "nfl_status": "Active", "active": True,
            "market_pool": "ROOKIE_PROSPECT", "years_exp": 0, "has_contract": False,
        }])[0]
        expected = {
            "sleeper_id", "canonical_player_id", "gsis_id", "player_name", "search_name", "pos", "nfl_team",
            "nfl_status", "active", "market_pool", "rookie_class_year", "draft_year", "draft_round",
            "draft_pick", "years_exp", "college", "has_contract",
        }
        self.assertTrue(expected <= payload.keys())
        self.assertTrue(payload.keys() <= PLAYER_UNIVERSE_COLUMNS)

    def test_unknown_field_fails_before_supabase_write(self):
        temp, input_path, sleeper_path = self._files()
        self.addCleanup(temp.cleanup)
        client = Client(existing=[{
            "sleeper_id": "9001", "player_name": "Test Rookie", "pos": "WR", "mystery_column": "bad",
        }])
        with self.assertRaisesRegex(ValueError, "mystery_column"):
            import_rookie_prospects(input_path, apply=True, sleeper_data_path=sleeper_path, client=client)
        self.assertFalse(any(event[0] in {"upsert", "delete"} for event in client.events))

    def test_dry_run_reports_same_counts_and_performs_no_write(self):
        temp, input_path, sleeper_path = self._files()
        self.addCleanup(temp.cleanup)
        client = Client()
        result = import_rookie_prospects(input_path, sleeper_data_path=sleeper_path, client=client)
        self.assertTrue(result["dry_run"])
        self.assertEqual((result["official_drafted_fantasy_players"], result["matched_canonical"]), (1, 1))
        self.assertEqual(result["matching_table"][0]["source_updated_at"], "2026-04-26")
        self.assertFalse(any(event[0] in {"upsert", "delete"} for event in client.events))

    def test_apply_uses_sanitized_payload_before_cleanup(self):
        temp, input_path, sleeper_path = self._files()
        self.addCleanup(temp.cleanup)
        client = Client()
        result = import_rookie_prospects(input_path, apply=True, sleeper_data_path=sleeper_path, client=client)
        self.assertFalse(result["dry_run"])
        upsert = next(event for event in client.events if event[0] == "upsert")
        self.assertNotIn("source", upsert[1][0])
        self.assertNotIn("source_updated_at", upsert[1][0])
        self.assertTrue(upsert[1][0].keys() <= PLAYER_UNIVERSE_COLUMNS)
        delete_positions = [index for index, event in enumerate(client.events) if event[0] == "delete"]
        self.assertTrue(not delete_positions or client.events.index(upsert) < min(delete_positions))

    def test_failed_upsert_never_reaches_synthetic_cleanup(self):
        temp, input_path, sleeper_path = self._files()
        self.addCleanup(temp.cleanup)
        client = Client(existing=[{
            "sleeper_id": "prospect_2026_test_rookie_wr", "player_name": "Test Rookie", "search_name": "test rookie",
            "pos": "WR", "college": "Boise State", "rookie_class_year": 2026, "draft_year": 2026,
            "nfl_status": "PROSPECT", "active": False, "market_pool": "ROOKIE_PROSPECT",
        }], fail_upsert=True)
        with self.assertRaisesRegex(RuntimeError, "simulated PostgREST failure"):
            import_rookie_prospects(input_path, apply=True, sleeper_data_path=sleeper_path, client=client)
        self.assertTrue(any(event[0] == "upsert_failed" for event in client.events))
        self.assertFalse(any(event[0] == "delete" for event in client.events))


if __name__ == "__main__":
    unittest.main()
