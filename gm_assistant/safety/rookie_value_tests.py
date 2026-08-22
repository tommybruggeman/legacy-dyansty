from __future__ import annotations

from gm_assistant.engines.rookie_value_engine import decide_rookie_pick_strategy


def run_tests() -> bool:
    print("\\n🧪 Running rookie value-vs-need tests...\\n")

    tests = []

    # Jeanty/Ward style case: elite RB over need QB at 1.01.
    d1 = decide_rookie_pick_strategy(
        pick_label="1.01",
        best_player_name="Ashton Jeanty",
        best_player_pos="RB",
        need_player_name="Cam Ward",
        need_player_pos="QB",
        best_player_score=96,
        need_player_score=84,
        team_need_score=90,
    )
    tests.append(("elite_gap_blocks_need", d1.decision == "DRAFT_BEST_PLAYER_OR_TRADE_BACK"))

    # Close tier later: need can break tie.
    d2 = decide_rookie_pick_strategy(
        pick_label="1.08",
        best_player_name="WR Prospect",
        best_player_pos="WR",
        need_player_name="RB Prospect",
        need_player_pos="RB",
        best_player_score=82,
        need_player_score=79,
        team_need_score=85,
    )
    tests.append(("late_need_breaks_tie", d2.decision in {"NEED_CAN_BREAK_TIE", "DRAFT_FOR_NEED_WITHIN_TIER"}))

    # Mid pick, moderate gap: best player still preferred.
    d3 = decide_rookie_pick_strategy(
        pick_label="1.05",
        best_player_name="WR Prospect",
        best_player_pos="WR",
        need_player_name="TE Prospect",
        need_player_pos="TE",
        best_player_score=88,
        need_player_score=78,
        team_need_score=75,
    )
    tests.append(("mid_gap_preserves_value", d3.decision == "DRAFT_BEST_PLAYER"))

    passed = 0
    failed = 0

    for name, ok in tests:
        if ok:
            passed += 1
            print(f"✅ {name}")
        else:
            failed += 1
            print(f"❌ {name}")

    print("\\n" + "=" * 70)
    print(f"Passed: {passed}")
    print(f"Failed: {failed}")
    print("=" * 70)

    return failed == 0


if __name__ == "__main__":
    ok = run_tests()
    raise SystemExit(0 if ok else 1)
