from __future__ import annotations


class RookieArchetypeEngine:
    """
    Converts players into fantasy-relevant archetypes.
    This is NOT scoring. This is role classification.
    """

    # ---------------------------
    # QB ARCHETYPES
    # ---------------------------
    def qb_archetype(self, d: dict) -> dict:
        rushing = float(d.get("rushing_upside", 0))
        draft = float(d.get("draft_signal", 0))

        if rushing > 0.6:
            return {
                "qb_type": "RUSHING_UPSIDE_STARTER",
                "qb_start_prob": min(1.0, draft / 60),
                "rushing_upside": rushing,
            }

        if draft > 70:
            return {
                "qb_type": "TRADITIONAL_HIGH_DRAFT",
                "qb_start_prob": min(0.8, draft / 80),
                "rushing_upside": rushing,
            }

        return {
            "qb_type": "DEVELOPMENTAL_QB",
            "qb_start_prob": min(0.3, draft / 100),
            "rushing_upside": rushing * 0.5,
        }

    # ---------------------------
    # RB ARCHETYPES
    # ---------------------------
    def rb_archetype(self, d: dict) -> dict:
        pass_game = float(d.get("pass_game_role", 0))
        draft = float(d.get("draft_signal", 0))

        if pass_game > 0.6:
            return {
                "rb_type": "THREE_DOWN_PASSING_BACK",
                "workload_prob": min(1.0, draft / 55),
                "pass_catching": pass_game,
            }

        if draft > 65:
            return {
                "rb_type": "EARLY_DOWN_BELLCOW",
                "workload_prob": min(0.9, draft / 70),
                "pass_catching": pass_game * 0.6,
            }

        return {
            "rb_type": "COMMITTEE_BACK",
            "workload_prob": min(0.4, draft / 100),
            "pass_catching": pass_game * 0.4,
        }

    # ---------------------------
    # WR ARCHETYPES
    # ---------------------------
    def wr_archetype(self, d: dict) -> dict:
        separation = float(d.get("athletic_profile", 0))
        draft = float(d.get("draft_signal", 0))

        if separation > 0.7:
            return {
                "wr_type": "ALPHA_TARGET_EARNER",
                "target_share_prob": min(1.0, draft / 60),
                "separation": separation,
            }

        if draft > 70:
            return {
                "wr_type": "HIGH_DRAFT_ROTATIONAL_WR",
                "target_share_prob": min(0.6, draft / 90),
                "separation": separation,
            }

        return {
            "wr_type": "DEPTH_ROTATION_WR",
            "target_share_prob": min(0.3, draft / 100),
            "separation": separation * 0.6,
        }

    # ---------------------------
    # TE ARCHETYPES
    # ---------------------------
    def te_archetype(self, d: dict) -> dict:
        size = float(d.get("size_profile", 0))
        draft = float(d.get("draft_signal", 0))

        if size > 0.7:
            return {
                "te_type": "RED_ZONE_WEAPON",
                "route_share": min(0.7, draft / 80),
                "red_zone_role": size,
            }

        if draft > 65:
            return {
                "te_type": "RECEIVING_TIGHT_END",
                "route_share": min(0.8, draft / 70),
                "red_zone_role": size * 0.6,
            }

        return {
            "te_type": "BLOCKING_OR_DEPTH_TE",
            "route_share": min(0.4, draft / 100),
            "red_zone_role": size * 0.4,
        }

    # ---------------------------
    # ROUTER
    # ---------------------------
    def build(self, pos: str, d: dict) -> dict:
        pos = (pos or "").upper()

        if pos == "QB":
            return self.qb_archetype(d)
        if pos == "RB":
            return self.rb_archetype(d)
        if pos == "WR":
            return self.wr_archetype(d)
        if pos == "TE":
            return self.te_archetype(d)

        return {}
