from __future__ import annotations

import numpy as np
import pandas as pd
from auth import service_client

TARGET_TABLE = "player_franchise_security_engine"


def clamp(x, lo=0, hi=100):
    try:
        if pd.isna(x):
            return 0.0
        return max(lo, min(hi, float(x)))
    except Exception:
        return 0.0


def tier(score):
    if score >= 90:
        return "FRANCHISE LOCK"
    if score >= 80:
        return "VERY SECURE"
    if score >= 70:
        return "SECURE STARTER"
    if score >= 55:
        return "MODERATE SECURITY"
    if score >= 40:
        return "VOLATILE SECURITY"
    return "REPLACEMENT RISK"


def build_player_franchise_security_engine():
    sb = service_client()

    engine = pd.DataFrame(sb.table("player_engine_scores").select("*").execute().data or [])
    situation = pd.DataFrame(sb.table("player_situation_context").select("*").execute().data or [])
    market = pd.DataFrame(sb.table("player_market_value_engine").select("*").execute().data or [])
    consensus = pd.DataFrame(sb.table("player_market_consensus").select("*").execute().data or [])
    players = pd.DataFrame(sb.table("players").select("*").execute().data or [])

    if engine.empty:
        print("No player_engine_scores found.")
        return pd.DataFrame()

    for df in [engine, situation, market, consensus, players]:
        if not df.empty and "sleeper_id" in df.columns:
            df["sleeper_id"] = df["sleeper_id"].astype(str)

    if not situation.empty:
        situation = (
            situation.sort_values(["sleeper_id", "situation_score"], ascending=[True, False])
            .drop_duplicates("sleeper_id", keep="first")
        )

    df = engine.copy()

    if not situation.empty:
        df = df.merge(
            situation[[
                "sleeper_id",
                "nfl_team",
                "situation_score",
                "role_security_score",
                "depth_chart_pressure_score",
                "team_environment_score",
                "scheme_fit_score",
                "qb_environment_score",
                "offensive_line_score",
            ]],
            on="sleeper_id",
            how="left",
        )

    if not market.empty:
        df = df.merge(
            market[["sleeper_id", "market_value_score"]],
            on="sleeper_id",
            how="left",
        )

    if not consensus.empty:
        df = df.merge(
            consensus[["sleeper_id", "market_consensus_score"]],
            on="sleeper_id",
            how="left",
        )

    if not players.empty:
        df = df.merge(
            players.rename(columns={
                "full_name": "players_full_name",
                "team": "players_team",
                "position": "players_position",
            })[["sleeper_id", "players_full_name", "players_team", "players_position"]],
            on="sleeper_id",
            how="left",
        )

    rows = []

    for _, r in df.iterrows():
        name = r.get("player_name") or r.get("players_full_name")
        pos = r.get("pos") or r.get("players_position")
        nfl_team = r.get("nfl_team") or r.get("team") or r.get("players_team")

        role_security = clamp(r.get("role_security_score", 50))
        depth_pressure = clamp(r.get("depth_chart_pressure_score", 50))
        team_environment = clamp(r.get("team_environment_score", 50))
        scheme_fit = clamp(r.get("scheme_fit_score", 50))
        qb_environment = clamp(r.get("qb_environment_score", 50))
        offensive_line = clamp(r.get("offensive_line_score", 50))

        recent = clamp(r.get("recent_production_score"))
        career = clamp(r.get("career_score_scaled", r.get("career_score")))
        trend = clamp(r.get("trend_score"))
        durability = clamp(r.get("durability_score"))

        market_value = clamp(r.get("market_value_score"))
        consensus = clamp(r.get("market_consensus_score"))
        market_anchor = consensus if consensus > 0 else market_value

        offensive_ecosystem = clamp(
            team_environment * 0.30
            + scheme_fit * 0.25
            + qb_environment * 0.25
            + offensive_line * 0.20
        )

        production_trust = clamp(
            recent * 0.35
            + career * 0.30
            + trend * 0.20
            + durability * 0.15
        )

        market_trust = clamp(
            market_anchor * 0.70
            + market_value * 0.30
        )

        replacement_risk = clamp(
            depth_pressure * 0.40
            + max(0, 55 - role_security) * 0.35
            + max(0, 50 - market_trust) * 0.25
        )

        if pos == "QB":
            franchise_security = clamp(
                role_security * 0.30
                + offensive_ecosystem * 0.25
                + production_trust * 0.20
                + market_trust * 0.15
                + (100 - replacement_risk) * 0.10
            )
        else:
            franchise_security = clamp(
                role_security * 0.25
                + production_trust * 0.25
                + market_trust * 0.25
                + offensive_ecosystem * 0.15
                + (100 - replacement_risk) * 0.10
            )

        row = {
            "sleeper_id": str(r.get("sleeper_id")),
            "player_name": name,
            "pos": pos,
            "nfl_team": nfl_team,
            "franchise_security_score": round(franchise_security, 2),
            "role_security_score": round(role_security, 2),
            "replacement_risk_score": round(replacement_risk, 2),
            "offensive_ecosystem_score": round(offensive_ecosystem, 2),
            "production_trust_score": round(production_trust, 2),
            "market_trust_score": round(market_trust, 2),
        }

        row["security_tier"] = tier(row["franchise_security_score"])
        row["security_summary"] = (
            f"{name} franchise security {row['franchise_security_score']}: {row['security_tier']}. "
            f"Role {row['role_security_score']}, ecosystem {row['offensive_ecosystem_score']}, "
            f"production trust {row['production_trust_score']}, market trust {row['market_trust_score']}, "
            f"replacement risk {row['replacement_risk_score']}."
        )

        rows.append(row)

    out = pd.DataFrame(rows)
    out = out.dropna(subset=["sleeper_id"])
    out = out[out["sleeper_id"].astype(str).str.strip() != ""]
    out = out.sort_values("franchise_security_score", ascending=False)
    out = out.drop_duplicates("sleeper_id", keep="first")
    out = out.replace([np.inf, -np.inf], None)
    out = out.where(pd.notnull(out), None)

    rows = out.to_dict("records")

    if rows:
        sb.table(TARGET_TABLE).upsert(rows, on_conflict="sleeper_id").execute()

    print(f"Upserted {len(rows)} player_franchise_security_engine rows.")
    return out


if __name__ == "__main__":
    df = build_player_franchise_security_engine()
    if not df.empty:
        print(
            df[
                [
                    "player_name",
                    "pos",
                    "franchise_security_score",
                    "role_security_score",
                    "offensive_ecosystem_score",
                    "production_trust_score",
                    "market_trust_score",
                    "replacement_risk_score",
                    "security_tier",
                ]
            ]
            .sort_values("franchise_security_score", ascending=False)
            .head(60)
            .to_string(index=False)
        )
