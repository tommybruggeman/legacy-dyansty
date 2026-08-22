from auth import service_client


class RookiePlayerMapper:
    """
    Maps archetype EV → real rookie players
    """

    def __init__(self):
        sb = service_client()

        raw = (
            sb.table("rookie_draft_outcomes")
            .select("player_name,pos,rookie_rank,draft_year,outcome_score,salary")
            .execute()
            .data
            or []
        )

        # filter + sanitize
        self.players = [
            p for p in raw
            if p.get("player_name") is not None
        ]

        # group by position
        self.by_pos = {}

        for p in self.players:
            pos = p.get("pos")
            self.by_pos.setdefault(pos, []).append(p)

        # sort safely (NONE SAFE)
        for pos in self.by_pos:
            self.by_pos[pos].sort(
                key=lambda x: (x.get("outcome_score") or 0),
                reverse=True
            )

    # -----------------------------
    # GET TOP PLAYERS BY POSITION
    # -----------------------------
    def get_players(self, pos: str, top_k: int = 5):
        return self.by_pos.get(pos, [])[:top_k]

    # -----------------------------
    # BEST PLAYER
    # -----------------------------
    def best_player(self, pos: str):
        pool = self.by_pos.get(pos, [])
        return pool[0] if pool else None
