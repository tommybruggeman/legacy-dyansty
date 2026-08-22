from __future__ import annotations

from gm_assistant.gm_brain import answer_gm_question
from auth import service_client


def print_data_needs(player_name: str, season: int | None = None):
    if season is None:
        from snapshot.runtime.season import get_current_season
        season = get_current_season()
    sb = service_client()

    rows = (
        sb.table("legacy_source_task_queue")
        .select("source_id,resolver,priority,status,needs")
        .eq("season", season)
        .eq("player_name", player_name)
        .order("priority")
        .execute()
        .data or []
    )

    if not rows:
        print("No queued data needs.")
        return

    print("\nDATA NEEDS:")
    for r in rows:
        print(f"- P{r['priority']} {r['source_id']} via {r['resolver']}: {r['needs']}")


def run_question(question: str, owner: str = "Tommy Bruggeman"):
    print("=" * 100)
    print("QUESTION:", question)
    print("=" * 100)

    result = answer_gm_question(question, owner)

    if isinstance(result, dict):
        print("\nDECISION:", result.get("decision"))
        print("\nANSWER:")
        print(result.get("summary") or result.get("answer") or result)
    else:
        print("\nANSWER:")
        print(result)

    for player in ["Fernando Mendoza", "Chandler Morris", "Cade Klubnik"]:
        if player.lower() in question.lower():
            print_data_needs(player)


if __name__ == "__main__":
    tests = [
        "Should I draft Fernando Mendoza?",
        "Should I draft Chandler Morris or Cade Klubnik?",
        "Which rookie QB is the best value?",
        "Which players on my team should I move before the season?",
        "How can I use my QB depth to upgrade RB?",
    ]

    for q in tests:
        run_question(q)
        print("\n\n")
