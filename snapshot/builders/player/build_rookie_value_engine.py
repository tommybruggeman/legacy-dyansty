from snapshot.config.league_settings import get_league_settings
from auth import service_client


def compute_rookie_value(prospect_score, usage_score, scheme_score, rookie_years):
    base = (
        prospect_score * 0.5 +
        usage_score * 0.3 +
        scheme_score * 0.2
    )

    window_multiplier = min(1.0, rookie_years / 3)

    return base * window_multiplier


def main():
    sb = service_client()
    settings = get_league_settings()

    rookie_years = settings.rookie_contract_years

    prospects = sb.table("player_prospect_context").select("*").execute().data or []
    usage = sb.table("player_usage_context").select("*").execute().data or []

    def norm(x):
        return str(x).lower().strip()

    usage_map = {
        norm(u["player_name"]): u
        for u in usage
        if u.get("player_name")
    }

    rows = []

    for p in prospects:
        name = p["player_name"]

        u = usage_map.get(norm(name), {})

        prospect_score = p.get("prospect_score", 0)
        usage_score = u.get("usage_score", 50)
        scheme_score = 70

        rookie_value = compute_rookie_value(
            prospect_score,
            usage_score,
            scheme_score,
            rookie_years
        )

        rows.append({
            "player_name": name,
            "player_name": p["player_name"],
            "rookie_value_score": rookie_value,
            "prospect_score": prospect_score,
            "usage_score": usage_score,
        })

    print(f"Built rookie value rows: {len(rows)}")

    if rows:
        sb.table("rookie_value_context").upsert(
            rows,
            on_conflict="canonical_player_id",
        ).execute()

    print(f"Upserted rookie value rows: {len(rows)}")


if __name__ == "__main__":
    main()
