from datetime import date
from types import SimpleNamespace

from api import account, demo, front_office, usage_log


def test_demo_login_is_read_only():
    reviewer = SimpleNamespace(email="AppleReview@legacydynasty.app")
    owner = SimpleNamespace(email="owner@example.com")
    assert demo.blocked(reviewer, "POST", "/my-team/lineup")
    assert demo.blocked(reviewer, "POST", "/waivers/bid")
    assert demo.blocked(reviewer, "DELETE", "/me")
    assert not demo.blocked(reviewer, "GET", "/my-team")
    assert not demo.blocked(reviewer, "POST", "/front-office/chat/stream")
    assert not demo.blocked(reviewer, "POST", "/front-office/report")
    assert not demo.blocked(reviewer, "POST", "/market/targets/123")
    assert not demo.blocked(owner, "POST", "/my-team/lineup")


def test_screen_names_merge_ids():
    assert usage_log.screen_name("/player/4046") == "/player/[id]"
    assert usage_log.screen_name("/team/0b7c1f2e-1111-2222-3333-444455556666?x=1") == "/team/[id]"
    assert usage_log.screen_name("/game/8/3") == "/game/[id]/[id]"
    assert usage_log.screen_name("") == "/"


def test_screen_rows_clip_and_skip_bad():
    rows = usage_log.rows(user_id="u", league_id="l", build="86", source="app",
                          views=[{"screen": "/", "seconds": 99999}, {"screen": "/x", "seconds": "bad"}, {"screen": "/y", "seconds": -4}])
    assert len(rows) == 1 and rows[0]["seconds"] == usage_log.MAX_SECONDS


def test_crash_line_has_no_raw_user_id():
    line = account.crash_line("user-123", {"message": "boom", "screen": "/player/1"})
    assert "user-123" not in line and "boom" in line and "app_crash" in line


class _Count:
    def __init__(self, n):
        self.n = n

    def table(self, name):
        assert name == "league_ai_usage"
        return self

    def select(self, *a, **k):
        return self

    def eq(self, *a):
        return self

    def gte(self, *a):
        return self

    def execute(self):
        return SimpleNamespace(count=self.n)


def test_daily_limit_uses_the_usage_log():
    front_office.clear()
    day = date(2026, 11, 14)
    assert not front_office.take_question("u9", day, client=_Count(front_office.DAILY_QUESTIONS))
    assert front_office.take_question("u9", day, client=_Count(3))
    front_office.clear()
