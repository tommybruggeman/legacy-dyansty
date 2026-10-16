import unittest
from datetime import datetime, timedelta, timezone

from league_ai.learning import Ledger
from league_ai.learning_job import track_trade_outcomes
from league_ai.trade_finder import owner_profiles, pitch_record
from tests.test_league_ai_learning import MemDB

NOW = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)
CHASE = {"sleeper_id": "1", "name": "Ja'Marr Chase", "position": "WR"}
BIJAN = {"sleeper_id": "2", "name": "Bijan Robinson", "position": "RB"}
NABERS = {"sleeper_id": "3", "name": "Malik Nabers", "position": "WR"}
HALL = {"sleeper_id": "4", "name": "Breece Hall", "position": "RB"}


def _call(cid, gets, gives, days_ago, partner="Nando"):
    return {"id": cid, "league_id": "L", "user_id": "U", "kind": "trade", "status": "open", "pick": [gets], "over": [gives], "partner": partner,
            "created_at": (NOW - timedelta(days=days_ago)).isoformat()}


def _leg(tid, i, pid, days_ago):
    return {"league_id": "L", "event_id": f"sleeper:{tid}:p{i}", "kind": "trade", "player_id": pid, "occurred_at": (NOW - timedelta(days=days_ago)).isoformat()}


class TrackOutcomesTests(unittest.TestCase):
    def _db(self):
        return MemDB({
            "league_ai_predictions": [_call("made", CHASE, BIJAN, 5), _call("stale", NABERS, HALL, 12), _call("fresh", NABERS, BIJAN, 3)],
            "league_history_events": [_leg("t1", 0, "1", 2), _leg("t1", 1, "2", 2),   # Chase for Bijan, after it was asked
                                      _leg("t0", 0, "3", 40), _leg("t0", 1, "4", 40)],  # Nabers for Hall long before: not this pitch
            "league_ai_trade_outcomes": [],
        })

    def test_completed_trade_is_accepted_and_stale_pitch_is_not_completed(self):
        db = self._db()
        out = track_trade_outcomes(db, now=NOW)
        self.assertEqual(out, {"accepted": 1, "not_completed": 1})
        rows = {r["prediction_id"]: r for r in db.tables["league_ai_trade_outcomes"]}
        self.assertEqual(rows["made"]["outcome"], "accepted")
        self.assertEqual(rows["made"]["sleeper_transaction_id"], "t1")
        self.assertEqual(rows["stale"]["outcome"], "not_completed")
        self.assertNotIn("fresh", rows)  # still inside the waiting window

    def test_runs_once_per_call_and_dry_run_writes_nothing(self):
        db = self._db()
        self.assertEqual(track_trade_outcomes(db, now=NOW, dry_run=True), {"accepted": 1, "not_completed": 1})
        self.assertEqual(db.tables["league_ai_trade_outcomes"], [])
        track_trade_outcomes(db, now=NOW)
        self.assertEqual(track_trade_outcomes(db, now=NOW), {"accepted": 0, "not_completed": 0})
        self.assertEqual(len(db.tables["league_ai_trade_outcomes"]), 2)


class RecordResponseTests(unittest.TestCase):
    def test_decline_from_chat_attaches_to_the_logged_pitch(self):
        db = MemDB({"league_ai_predictions": [_call("p1", CHASE, BIJAN, 1)], "league_ai_trade_outcomes": [], "nfl_players": []})
        ledger = Ledger(db, league_id="L", user_id="U", league_team_id="T", season=2026, week=7, resolve_player=lambda n: {"Ja'Marr Chase": CHASE}.get(n))
        out = ledger.record_trade_response("declined", partner="Nando", receive=["Ja'Marr Chase"], note="wants a 1st on top")
        self.assertTrue(out.get("recorded"), out)
        row = db.tables["league_ai_trade_outcomes"][0]
        self.assertEqual((row["outcome"], row["source"], row["note"]), ("declined", "chat", "wants a 1st on top"))
        self.assertEqual(ledger.trade_outcomes(partner="nando")[0]["outcome"], "declined")
        self.assertIn("error", ledger.record_trade_response("maybe"))


class ProfileTests(unittest.TestCase):
    def test_owner_style_from_trade_history(self):
        details = lambda players, picks: {"participants": ["Grady", "Nando"], "players": players, "picks": picks}
        rows = []
        for n in range(3):
            d = details([{"player": f"Vet {n}", "position": "RB", "from": "Grady", "to": "Nando"}],
                        [{"pick": "2027 R1", "from": "Nando", "to": "Grady"}])
            rows.append({"event_id": f"sleeper:x{n}:p0", "season": 2026, "player_id": f"v{n}", "player_name": f"Vet {n}", "details": d})
            rows.append({"event_id": f"sleeper:x{n}:k0", "season": 2026, "player_id": None, "player_name": "2027 R1 pick", "details": d})
        prof = owner_profiles(rows, {}, 2026)
        self.assertEqual(prof["Nando"]["trades"], 3)
        self.assertEqual(prof["Nando"]["positions_acquired"], {"RB": 3})
        self.assertIn("spends draft picks", prof["Nando"]["style"])
        self.assertIn("collects draft picks", prof["Grady"]["style"])
        self.assertEqual(prof["Grady"]["favorite_partners"], {"Nando": 3})
        self.assertEqual(pitch_record([{"partner": "Nando", "outcome": "declined"}, {"partner": "Nando", "outcome": "accepted"}]),
                         {"Nando": {"declined": 1, "accepted": 1}})


if __name__ == "__main__":
    unittest.main()
