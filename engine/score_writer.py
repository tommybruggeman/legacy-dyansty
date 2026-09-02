from __future__ import annotations

# ============================================================
# Score Writer
#
# Responsible for writing valuation results
# back into Supabase.
# ============================================================

from auth import service_client

supabase = service_client()


# ============================================================
# Player Values
# ============================================================

def upsert_player_values(
    ctx: dict,
    scored_roster,
):
    """
    Writes player valuation data into:

        player_values

    Uses:
        league_id + player_id

    as the conflict key.
    """

    rows = []

    for _, r in scored_roster.iterrows():

        rows.append(
            {
                "league_id": ctx["league_id"],

                "player_id": str(
                    r.get("player_id")
                    or r.get("sleeper_player_id")
                    or r.get("player")
                ),

                "player_name": (
                    r.get("player_name")
                    or r.get("player")
                ),

                "position": (
                    r.get("position")
                    or r.get("pos")
                ),

                "team": (
                    r.get("team")
                    or r.get("owner_name")
                    or r.get("team_name")
                ),

                "base_value_score": r.get(
                    "base_value_score",
                    0,
                ),

                "contract_value_score": r.get(
                    "contract_value_score",
                    0,
                ),

                "roster_fit_score": r.get(
                    "roster_fit_score",
                    0,
                ),

                "positional_need_score": r.get(
                    "positional_need_score",
                    0,
                ),

                "trade_value_score": r.get(
                    "trade_value_score",
                    0,
                ),
            }
        )

    if not rows:
        return

    # --------------------------------------------------------
    # Deduplicate
    #
    # Prevents:
    #
    # ON CONFLICT DO UPDATE command cannot affect row
    # a second time
    # --------------------------------------------------------

    deduped = {}

    for row in rows:
        key = (
            row["league_id"],
            row["player_id"],
        )

        deduped[key] = row

    final_rows = list(deduped.values())

    # --------------------------------------------------------
    # Upsert
    # --------------------------------------------------------

    supabase.table(
        "player_values"
    ).upsert(
        final_rows,
        on_conflict="league_id,player_id",
    ).execute()