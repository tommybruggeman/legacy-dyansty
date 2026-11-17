from scoring import espn_feed as e


def _game(team, text, distance, yards, kind="Rush"):
    play = {"type": {"text": kind}, "text": text, "start": {"distance": distance}, "statYardage": yards}
    return {"drives": {"previous": [{"team": {"abbreviation": team}, "plays": [play]}]}}


def test_plain_run_first_down():
    n = e.Names(); n.add("ARI", "2", "Zonovan Knight")
    assert e.first_downs(_game("ARI", "Z.Knight right tackle to ARZ 48 for 6 yards (L.Murchison).", 3, 6), n) == [("2", "rush_fd")]
    assert e.first_downs(_game("ARI", "(Shotgun) Z.Knight left tackle to LA 47 for 5 yards.", 10, 5), n) == []


def test_direct_snap_run_counts_for_the_runner():
    n = e.Names(); n.add("LV", "1", "Michael Mayer"); n.add("BUF", "3", "Anthony Beck")
    assert e.first_downs(_game("LV", "Direct snap to M.Mayer.  M.Mayer right tackle to LV 39 for 2 yards (N.Emmanwori).", 2, 2), n) == [("1", "rush_fd")]
    assert e.first_downs(_game("BUF", "(Shotgun) Direct snap to A.Beck.  A.Beck up the middle to BUF 38 for 2 yards.", 1, 2), n) == [("3", "rush_fd")]
