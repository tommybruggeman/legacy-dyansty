"""Screenshots in the GM chat and leagues outside Legacy (league_ai.screenshots, league_ai.tools.outside_league)."""
import base64
import io
import json
import unittest
from types import SimpleNamespace
from unittest import mock

from league_ai import prompt, runtime, screenshots, service
from league_ai.client import Trace
from league_ai.service import AnswerResult
from league_ai.tools import ToolRegistry
from league_ai.tools import outside_league as ol

PNG = base64.b64encode(b"\x89PNG\r\n\x1a\nfake").decode()


def shot(data=PNG, media="image/png"):
    return {"media_type": media, "data": data}


class CleanTests(unittest.TestCase):
    def test_accepts_up_to_ten(self):
        self.assertEqual(len(screenshots.clean([shot()] * 10)), 10)
        with self.assertRaises(screenshots.ScreenshotError):
            screenshots.clean([shot()] * 11)

    def test_data_url_and_jpg_alias(self):
        out = screenshots.clean([{"data": f"data:image/jpg;base64,{PNG}"}])
        self.assertEqual(out[0].media_type, "image/jpeg")
        self.assertEqual(out[0].data, PNG)
        self.assertEqual(out[0].block()["source"]["type"], "base64")

    def test_rejects_bad_type_bad_base64_and_too_big(self):
        for bad in ([shot(media="application/pdf")], [shot(data="not base64!!")], [shot(data="")]):
            with self.assertRaises(screenshots.ScreenshotError):
                screenshots.clean(bad)
        big = base64.b64encode(b"x" * (screenshots.MAX_IMAGE_BYTES + 1)).decode()
        with self.assertRaises(screenshots.ScreenshotError):
            screenshots.clean([shot(data=big)])

    def test_shrink_makes_a_small_jpeg(self):
        from PIL import Image

        buf = io.BytesIO()
        Image.new("RGBA", (3000, 6000), (10, 20, 30, 255)).save(buf, format="PNG")
        out = screenshots.shrink(buf.getvalue(), "image/png")
        self.assertEqual(out.media_type, "image/jpeg")
        img = Image.open(io.BytesIO(base64.b64decode(out.data)))
        self.assertEqual(max(img.size), screenshots.LONG_EDGE)


class FakeSdk:
    def __init__(self, text="Sleeper trade proposal: you give Bijan Robinson RB ATL, you get Puka Nacua WR LAR.", error=None):
        self.text, self.error, self.calls = text, error, []
        self.messages = self

    def create(self, **kw):
        self.calls.append(kw)
        if self.error:
            raise self.error
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=self.text)],
                               usage=SimpleNamespace(input_tokens=1600, output_tokens=80, cache_read_input_tokens=0, cache_creation_input_tokens=0))


class ReadTests(unittest.TestCase):
    def test_read_sends_images_then_question(self):
        sdk = FakeSdk()
        out = screenshots.read(screenshots.clean([shot(), shot()]), "should I accept?", sdk_client=sdk)
        self.assertIn("Puka Nacua", out.text)
        content = sdk.calls[0]["messages"][0]["content"]
        self.assertEqual([b["type"] for b in content], ["image", "image", "text"])
        self.assertEqual(sdk.calls[0]["model"], screenshots.MODEL)
        self.assertEqual(out.usage["input_tokens"], 1600)

    def test_read_fails_open(self):
        out = screenshots.read(screenshots.clean([shot()]), "x", sdk_client=FakeSdk(error=RuntimeError("down")))
        self.assertEqual(out.text, "")
        self.assertEqual(out.error, "RuntimeError")


class ServiceTests(unittest.TestCase):
    def _answer(self, history, images=None, reading=None):
        seen = {}

        class Client:
            def run(self, **kw):
                seen.update(kw)
                return SimpleNamespace(ok=True, text="Accept.", trace=Trace(), error_code=None, evidence=[])

        cfg = SimpleNamespace(enabled=True, api_key_present=True, model="m")
        asker = SimpleNamespace()
        service.answer(question="should I accept?", history=history, asker=asker, sources=None, client=Client(), config=cfg, pack="P",
                       images=images, screenshot_reading=reading)
        return seen["messages"]

    def test_images_go_in_the_latest_message(self):
        msgs = self._answer([], screenshots.clean([shot()]), "reading here")
        content = msgs[-1]["content"]
        self.assertEqual(content[0]["type"], "image")
        self.assertIn("reading here", content[-1]["text"])
        self.assertIn("I attached a screenshot", content[-1]["text"])

    def test_earlier_screenshot_comes_back_as_its_reading(self):
        history = [{"role": "user", "content": "should I accept?", "photos": 2, "screenshot": "you give Bijan"},
                   {"role": "assistant", "content": "Decline."}]
        msgs = self._answer(history)
        self.assertIn("Earlier in this chat I sent 2 screenshots", msgs[0]["content"])
        self.assertIn("you give Bijan", msgs[0]["content"])
        self.assertIsInstance(msgs[-1]["content"], str)  # no images re-sent


class ChatTurnTests(unittest.TestCase):
    def test_reading_is_saved_routed_and_given_to_the_fact_check(self):
        saved, seen, rows, checked = {}, {}, [], {}

        class Mem:
            def get_conversation(self, cid):
                return None

            def save_conversation(self, cid, msgs):
                saved["msgs"] = msgs
                return "c1"

        def fake_answer(**kw):
            seen.update(kw)
            return AnswerResult(True, "Decline it.", Trace(model="m"), evidence=[], pack="P")

        def fake_check(text, **kw):
            checked.update(kw)
            return SimpleNamespace(ran=False, fixed=False, problems=[])

        owner = runtime.Owner(user_id="u", league_id="l", league_team_id="t", season=2026, owner_name="Tommy", team_name="T")
        reading = screenshots.Reading("Trade proposal: you give Bijan Robinson, you get Puka Nacua", {"input_tokens": 10, "output_tokens": 5})
        with mock.patch.object(runtime, "answer", fake_answer), mock.patch.object(runtime.Owner, "memory", lambda self, c: Mem()), \
                mock.patch.object(runtime, "record_usage", lambda c, row: rows.append(row)), \
                mock.patch.object(runtime.screenshots, "read", lambda shots, q: reading), \
                mock.patch.object(runtime.fact_check, "check", fake_check), mock.patch.object(runtime.fact_check, "enabled", lambda: True):
            turn = runtime.chat_turn(None, owner, question="", history=[], conversation_id=None, pack="P", tools=None,
                                     images=screenshots.clean([shot()] * 3))
        user = saved["msgs"][0]
        self.assertEqual(user["content"], screenshots.DEFAULT_QUESTION)
        self.assertEqual(user["photos"], 3)
        self.assertIn("Bijan", user["screenshot"])
        self.assertEqual(len(seen["images"]), 3)
        self.assertEqual(seen["turn_effort"], "high")  # the trade is only in the picture, still a trade question
        self.assertNotIn(runtime.NOT_A_TRADE_NOTE, seen["week_context"] or "")
        self.assertEqual(checked["evidence"][0]["tool"], "owner_screenshot")
        self.assertIn("screenshot_read", [r["feature"] for r in rows])
        self.assertEqual(turn.result.screenshot, reading.text)


RAW = {
    "league": {"name": "Brothers", "season": "2026", "status": "in_season", "roster_positions": ["QB", "RB", "WR", "TE", "SUPER_FLEX", "BN", "BN"],
               "scoring_settings": {"rec": 1.0, "bonus_rec_te": 0.5, "pass_td": 4.0}, "settings": {"type": 2, "taxi_slots": 3, "playoff_teams": 6}},
    "rosters": [
        {"roster_id": 1, "owner_id": "u1", "players": ["100", "200", "DAL"], "starters": ["100", "0"], "taxi": ["200"], "reserve": [],
         "settings": {"wins": 3, "losses": 6, "fpts": 900, "fpts_decimal": 50}},
        {"roster_id": 2, "owner_id": "u2", "players": ["300"], "starters": ["300"], "taxi": [], "reserve": [],
         "settings": {"wins": 7, "losses": 2, "fpts": 1100}},
    ],
    "users": [{"user_id": "u1", "display_name": "tommy", "metadata": {"team_name": "Condors"}}, {"user_id": "u2", "display_name": "rival"}],
    "traded_picks": [{"season": "2027", "round": 1, "roster_id": 1, "owner_id": 2}, {"season": "2027", "round": 2, "roster_id": 2, "owner_id": 2}],
}
NAMES = {"100": "Josh Allen QB BUF", "200": "Rookie Guy WR NYJ", "300": "Bijan Robinson RB ATL"}


class OutsideLeagueTests(unittest.TestCase):
    def test_id_from_link(self):
        self.assertEqual(ol.league_id_from("https://sleeper.com/leagues/1180234567890123456/team"), "1180234567890123456")
        self.assertIsNone(ol.league_id_from("my other league"))

    def test_summary(self):
        out = ol.summarize(RAW, lambda ids: NAMES)
        self.assertEqual(out["type"], "dynasty")
        self.assertEqual(out["scoring"]["format"], "PPR, superflex, TE premium +0.5")
        self.assertEqual(out["starting_lineup"], ["QB", "RB", "WR", "TE", "SUPER_FLEX"])
        self.assertEqual([t["owner"] for t in out["teams"]], ["rival", "tommy"])  # best record first
        mine = out["teams"][1]
        self.assertEqual(mine["record"], "3-6")
        self.assertEqual(mine["points_for"], 900.5)
        self.assertEqual(mine["starters"], ["Josh Allen QB BUF"])
        self.assertEqual(mine["taxi"], ["Rookie Guy WR NYJ"])
        self.assertEqual(mine["bench"], ["DAL D/ST"])
        self.assertEqual(out["traded_picks"], ["2027 round 1: tommy's pick, owned by rival"])
        self.assertIn("not Legacy", out["note"])

    def test_team_filter(self):
        out = ol.summarize(RAW, lambda ids: NAMES, team="condors")
        self.assertNotIn("starters", out["teams"][0])
        self.assertIn("starters", out["teams"][1])
        out = ol.summarize(RAW, lambda ids: NAMES, team="nobody")  # no match: every roster
        self.assertTrue(all("starters" in t for t in out["teams"]))

    def test_tool_errors_and_cache(self):
        reg = ToolRegistry()
        for spec in ol.outside_league_tools(None, own_sleeper_id="999999999999999"):
            reg.register(spec)
        self.assertIn("isn't a Sleeper league ID", reg.call("get_outside_league", {"league": "abc"}))
        self.assertIn("That's this league", reg.call("get_outside_league", {"league": "999999999999999"}))
        ol._cache.clear()
        calls = []

        def fake_get(url):
            calls.append(url)
            key = url.rsplit("/", 1)[-1]
            return {"rosters": RAW["rosters"], "users": RAW["users"], "traded_picks": []}.get(key, RAW["league"])

        with mock.patch.object(ol, "_get", fake_get), mock.patch.object(ol, "_player_names", lambda c, ids: NAMES):
            first = json.loads(reg.call("get_outside_league", {"league": "123456789012345678"}))
            reg.call("get_outside_league", {"league": "https://sleeper.com/leagues/123456789012345678"})
        self.assertEqual(first["league"], "Brothers")
        self.assertEqual(len(calls), 4)  # second call came from the cache
        ol._cache.clear()


class PromptTests(unittest.TestCase):
    def test_prompt_covers_other_leagues_and_teams(self):
        text = prompt.SYSTEM_PROMPT
        self.assertIn("Screenshots, other teams and other leagues", text)
        self.assertIn("get_outside_league", text)
        self.assertIn("never call log_prediction for it", text)
        self.assertNotIn("Stay in scope: this league and NFL football.", text)
        self.assertIn("get_outside_league", runtime.TOOL_STATUS)


if __name__ == "__main__":
    unittest.main()
