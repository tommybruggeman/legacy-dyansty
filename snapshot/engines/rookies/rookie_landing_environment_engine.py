from __future__ import annotations


class RookieLandingEnvironmentEngine:
    """
    Translates team context into fantasy opportunity signals.

    This is NOT player talent.
    This is opportunity creation.
    """

    # ---------------------------
    # QB ENVIRONMENT
    # ---------------------------
    def qb_environment(self, d: dict) -> float:
        pass_rate = float(d.get("team_pass_rate", 0.5))
        o_line = float(d.get("o_line_quality", 0.5))
        weapons = float(d.get("skill_weapons", 0.5))

        return (
            0.4 * pass_rate +
            0.3 * o_line +
            0.3 * weapons
        )

    # ---------------------------
    # RB ENVIRONMENT
    # ---------------------------
    def rb_environment(self, d: dict) -> float:
        run_rate = float(d.get("team_run_rate", 0.5))
        run_block = float(d.get("run_blocking", 0.5))
        competition = float(d.get("rb_depth_chart_clarity", 0.5))

        # less competition = better environment
        return (
            0.4 * run_rate +
            0.3 * run_block +
            0.3 * (1 - competition)
        )

    # ---------------------------
    # WR ENVIRONMENT
    # ---------------------------
    def wr_environment(self, d: dict) -> float:
        qb_quality = float(d.get("qb_quality", 0.5))
        pass_volume = float(d.get("team_pass_volume", 0.5))
        target_competition = float(d.get("wr_depth_clarity", 0.5))

        return (
            0.4 * qb_quality +
            0.4 * pass_volume +
            0.2 * (1 - target_competition)
        )

    # ---------------------------
    # TE ENVIRONMENT
    # ---------------------------
    def te_environment(self, d: dict) -> float:
        red_zone_rate = float(d.get("red_zone_pass_rate", 0.5))
        qb_quality = float(d.get("qb_quality", 0.5))
        target_competition = float(d.get("te_depth_clarity", 0.5))

        return (
            0.4 * red_zone_rate +
            0.4 * qb_quality +
            0.2 * (1 - target_competition)
        )

    # ---------------------------
    # ROUTER
    # ---------------------------
    def build(self, pos: str, d: dict) -> float:
        pos = (pos or "").upper()

        if pos == "QB":
            return self.qb_environment(d)
        if pos == "RB":
            return self.rb_environment(d)
        if pos == "WR":
            return self.wr_environment(d)
        if pos == "TE":
            return self.te_environment(d)

        return 0.5
