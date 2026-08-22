from __future__ import annotations

import re
import pandas as pd

from auth import service_client


# ============================================================
# Helpers
# ============================================================

def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


def money(x) -> str:
    try:
        return f"${float(x):g}"
    except Exception:
        return "$?"


def load_table(table: str) -> pd.DataFrame:
    try:
        sb = service_client()
        return pd.DataFrame(sb.table(table).select("*").execute().data or [])
    except Exception as e:
        print(f"Unable to load {table}: {e}")
        return pd.DataFrame()


def get_brain_tables():
    return {
        "players": load_table("player_intelligence"),
        "teams": load_table("team_intelligence"),
        "league": load_table("league_intelligence"),
        "roster": load_table("roster"),
    }


def find_player(players: pd.DataFrame, roster: pd.DataFrame, question: str):
    q = normalize(question)

    candidates = []

    for _, r in roster.iterrows():
        name = str(r.get("player") or r.get("player_name") or "").strip()
        if not name:
            continue

        n = normalize(name)

        if n in q:
            candidates.append((100, r))
            continue

        parts = [p for p in n.split() if len(p) >= 4]
        if any(p in q for p in parts):
            candidates.append((60, r))

    if not candidates:
        return None

    candidates = sorted(candidates, key=lambda x: x[0], reverse=True)
    roster_row = candidates[0][1]

    sleeper_id = str(roster_row.get("sleeper_id"))

    p = pd.DataFrame()
    if not players.empty and "sleeper_id" in players.columns:
        p = players[players["sleeper_id"].astype(str) == sleeper_id]

    if p.empty:
        out = roster_row.to_dict()
    else:
        out = {**roster_row.to_dict(), **p.iloc[0].to_dict()}

    return out


def my_roster(roster: pd.DataFrame, owner_team_name: str) -> pd.DataFrame:
    if roster.empty or "owner_team_name" not in roster.columns:
        return pd.DataFrame()
    return roster[roster["owner_team_name"] == owner_team_name].copy()


def enrich_roster_with_player_intel(roster_df: pd.DataFrame, players: pd.DataFrame) -> pd.DataFrame:
    if roster_df.empty:
        return roster_df

    df = roster_df.copy()

    if "player_name" not in df.columns and "player" in df.columns:
        df["player_name"] = df["player"]

    if players.empty or "sleeper_id" not in players.columns:
        return df

    keep = [
        "sleeper_id",
        "engine_score",
        "engine_tier",
        "recent_production_score",
        "contract_value_score",
        "trade_value_score",
        "age_curve_score",
        "dynasty_rank",
        "position_rank",
        "tier",
        "recent_avg_ppg_ppr",
        "development_score",
        "breakout_probability",
        "elite_probability",
        "decline_probability",
        "expected_ppg_next",
    ]

    df = df.merge(
        players[[c for c in keep if c in players.columns]],
        on="sleeper_id",
        how="left",
    )

    return df


# ============================================================
# Intent
# ============================================================

def detect_intent(question: str) -> str:
    q = normalize(question)

    if any(x in q for x in ["overpaid", "bad contract", "worst contract", "too expensive"]):
        return "overpaid"

    if any(x in q for x in ["trade target", "trade targets", "who should i trade for", "realistic target", "acquire"]):
        return "trade_targets"

    if any(x in q for x in ["biggest weakness", "weakness", "hole", "need", "stopping my team", "why can't", "why cant"]):
        return "team_weakness"

    if any(x in q for x in ["cut", "drop", "get rid", "dump"]):
        return "cuts"

    if any(x in q for x in ["build around", "core", "foundation", "keep"]):
        return "core"

    return "general"


# ============================================================
# Writers
# ============================================================

def write_player_answer(player: dict) -> str:
    name = player.get("player_name") or player.get("player")
    pos = player.get("pos") or player.get("position")
    salary = player.get("salary")
    years = player.get("years")

    engine = player.get("engine_score")
    contract = player.get("contract_value_score")
    production = player.get("recent_production_score")
    age = player.get("age_curve_score")
    trade = player.get("trade_value_score")

    recommendation = "HOLD"

    if contract is not None and engine is not None:
        if float(engine or 0) >= 70 and float(contract or 0) >= 55:
            recommendation = "HOLD / BUILD AROUND"
        elif float(engine or 0) >= 65 and float(contract or 0) < 35:
            recommendation = "HOLD, BUT ONLY MOVE FOR A PREMIUM"
        elif float(engine or 0) < 45 and float(contract or 0) < 45:
            recommendation = "SHOP"
        elif float(contract or 0) >= 75 and float(engine or 0) >= 45:
            recommendation = "GOOD VALUE HOLD"

    lines = [
        f"On **{name}**, my read is: **{recommendation}**.",
        f"He is a **{pos}** at **{money(salary)}** with **{years:g} years** left.",
    ]

    metrics = []
    if engine is not None:
        metrics.append(f"engine score **{float(engine):.1f}**")
    if production is not None:
        metrics.append(f"recent production **{float(production):.1f}**")
    if contract is not None:
        metrics.append(f"contract value **{float(contract):.1f}**")
    if trade is not None:
        metrics.append(f"trade value **{float(trade):.1f}**")
    if age is not None:
        metrics.append(f"age curve **{float(age):.1f}**")

    if metrics:
        lines.append("The key signals are " + ", ".join(metrics) + ".")

    if contract is not None and engine is not None:
        if float(engine or 0) >= 65 and float(contract or 0) < 40:
            lines.append("The player quality is real, but the contract is not especially efficient. That makes him more of a premium hold than an automatic untouchable.")
        elif float(contract or 0) >= 70:
            lines.append("The contract is doing real work for you. I would be careful moving him unless the offer upgrades your long-term roster.")
        elif float(engine or 0) < 45:
            lines.append("The profile is replaceable enough that I would test the market.")

    lines.append(f"My move: **{recommendation}**.")

    return "\n\n".join(lines)


def write_player_list(title: str, players: pd.DataFrame, max_rows: int = 6) -> str:
    if players.empty:
        return "I do not see a strong list from the current intelligence tables."

    lines = [title]

    for _, r in players.head(max_rows).iterrows():
        name = r.get("player_name") or r.get("player")
        pos = r.get("pos")
        salary = money(r.get("salary"))
        years = r.get("years")
        engine = r.get("engine_score")
        contract = r.get("contract_value_score")

        detail = f"- **{name}** ({pos}, {salary}/{years:g} yrs)"
        bits = []
        if pd.notna(engine):
            bits.append(f"engine {float(engine):.1f}")
        if pd.notna(contract):
            bits.append(f"contract {float(contract):.1f}")
        if bits:
            detail += " — " + ", ".join(bits)

        lines.append(detail)

    return "\n".join(lines)


def write_team_weakness(owner_team_name: str, my: pd.DataFrame, team_row: dict | None) -> str:
    lines = [f"Here’s my GM read on **{owner_team_name}**."]

    if team_row:
        lines.append(
            f"Your team profile is **{team_row.get('league_window_label') or team_row.get('window_label')}**. "
            f"Current strategy: **{team_row.get('trade_strategy') or team_row.get('trade_posture')}**."
        )

        weaknesses = team_row.get("weaknesses")
        needs = team_row.get("needs_summary")

        if weaknesses:
            lines.append(f"The league-relative weaknesses showing up are **{weaknesses}**.")
        if needs:
            lines.append(f"The roster needs showing up are **{needs}**.")

    if not my.empty:
        pos = (
            my.groupby("pos")
            .agg(
                count=("player_name", "count"),
                avg_engine=("engine_score", "mean"),
                avg_contract=("contract_value_score", "mean"),
                total_salary=("salary", "sum"),
            )
            .reset_index()
        )

        weak_pos = pos.sort_values(["avg_engine", "avg_contract"], ascending=True).head(2)
        expensive_pos = pos.sort_values("total_salary", ascending=False).head(2)

        lines.append(
            "From the player intelligence table, the positions I would audit first are "
            + ", ".join(f"**{r.pos}**" for _, r in weak_pos.iterrows())
            + "."
        )

        lines.append(
            "The biggest salary concentration is "
            + ", ".join(f"**{r.pos}**" for _, r in expensive_pos.iterrows())
            + "."
        )

        pressure = my.sort_values(["contract_value_score", "engine_score"], ascending=[True, True]).head(4)
        names = ", ".join(f"**{r.get('player_name')}**" for _, r in pressure.iterrows())
        if names:
            lines.append(f"The first contracts I would review are {names}.")

    lines.append("My move: do not make a panic trade. Use the weak spots to target upgrades, but protect your elite core unless the deal clearly improves your title odds.")

    return "\n\n".join(lines)


# ============================================================
# Main Brain
# ============================================================

def answer(question: str, owner_team_name: str, league_id: str | None = None) -> str:
    brain = get_brain_tables()

    players = brain["players"]
    teams = brain["teams"]
    roster = brain["roster"]

    q = normalize(question)
    intent = detect_intent(question)

    my = enrich_roster_with_player_intel(
        my_roster(roster, owner_team_name),
        players,
    )

    player = find_player(players, roster, question)
    if player:
        return write_player_answer(player)

    team_row = None
    if not teams.empty and "owner_team_name" in teams.columns:
        rows = teams[teams["owner_team_name"] == owner_team_name]
        if not rows.empty:
            team_row = rows.iloc[0].to_dict()

    if intent == "overpaid":
        candidates = my.copy()

        candidates["salary_num"] = candidates["salary"].fillna(0).astype(float)
        candidates["engine_num"] = candidates["engine_score"].fillna(50).astype(float)
        candidates["production_num"] = candidates["recent_production_score"].fillna(candidates["engine_num"]).astype(float)
        candidates["contract_num"] = candidates["contract_value_score"].fillna(50).astype(float)

        # Expensive is not the same thing as overpaid.
        # This penalizes bad contracts only when the production/engine does not justify the salary.
        candidates["overpaid_score"] = (
            candidates["salary_num"] * 1.2
            + (60 - candidates["contract_num"]) * 0.8
            + (55 - candidates["production_num"]) * 0.5
            - candidates["engine_num"] * 0.35
        )

        # Elite producers can be expensive without being "bad contracts."
        candidates.loc[candidates["engine_num"] >= 80, "overpaid_score"] -= 25
        candidates.loc[candidates["production_num"] >= 75, "overpaid_score"] -= 20

        candidates = candidates.sort_values("overpaid_score", ascending=False)

        return write_player_list(
            "The contracts I would audit first are — not automatically cut, but review against trade/replacement options:",
            candidates
        )

    if intent == "cuts":
        candidates = my.sort_values(["engine_score", "contract_value_score"], ascending=True)
        return write_player_list("If you need to create flexibility, I would start by reviewing:", candidates)

    if intent == "core":
        candidates = my.sort_values(["engine_score", "recent_production_score", "contract_value_score"], ascending=False)
        return write_player_list("The players I would be most careful about moving are:", candidates)

    if intent == "team_weakness":
        return write_team_weakness(owner_team_name, my, team_row)

    if intent == "trade_targets":
        if roster.empty:
            return "I need league roster data before I can recommend realistic trade targets."

        league_pool = roster[roster["owner_team_name"] != owner_team_name].copy()
        league_pool = enrich_roster_with_player_intel(league_pool, players)

        candidates = league_pool.copy()

        candidates["engine_num"] = candidates["engine_score"].fillna(50).astype(float)
        candidates["contract_num"] = candidates["contract_value_score"].fillna(50).astype(float)
        candidates["production_num"] = candidates["recent_production_score"].fillna(candidates["engine_num"]).astype(float)
        candidates["salary_num"] = candidates["salary"].fillna(0).astype(float)

        # Team-relative importance: top players on their own team are much harder to buy.
        candidates["team_asset_rank"] = (
            candidates.groupby("owner_team_name")["engine_num"]
            .rank(method="first", ascending=False)
        )

        candidates["team_pos_rank"] = (
            candidates.groupby(["owner_team_name", "pos"])["engine_num"]
            .rank(method="first", ascending=False)
        )

        candidates["team_pos_count"] = (
            candidates.groupby(["owner_team_name", "pos"])["player_name"]
            .transform("count")
        )

        candidates["core_penalty"] = 0
        candidates.loc[candidates["team_asset_rank"] <= 2, "core_penalty"] += 35
        candidates.loc[(candidates["team_asset_rank"] <= 5) & (candidates["engine_num"] >= 65), "core_penalty"] += 20
        candidates.loc[(candidates["pos"] == "QB") & (candidates["engine_num"] >= 60), "core_penalty"] += 25

        candidates["surplus_bonus"] = 0
        candidates.loc[
            (candidates["team_pos_count"] >= 4) & (candidates["team_pos_rank"] >= 3),
            "surplus_bonus"
        ] += 18

        # We want targets who help you, but are not obvious untouchables.
        candidates["target_score"] = (
            candidates["engine_num"] * 0.30
            + candidates["contract_num"] * 0.25
            + candidates["production_num"] * 0.20
            + candidates["surplus_bonus"]
            - candidates["salary_num"] * 0.15
            - candidates["core_penalty"]
        )

        # Avoid mystery/default-score players being treated as real targets.
        if "recent_games" in candidates.columns:
            candidates = candidates[
                candidates["recent_games"].fillna(0).astype(float) >= 8
            ].copy()

        if "recent_avg_ppg_ppr" in candidates.columns:
            candidates = candidates[
                candidates["recent_avg_ppg_ppr"].fillna(0).astype(float) >= 6
            ].copy()

        # Avoid pure replacement-level recommendations.
        candidates = candidates[candidates["engine_num"] >= 45].copy()

        candidates = candidates.sort_values("target_score", ascending=False)

        return write_player_list(
            "The first realistic trade targets I would explore are:",
            candidates,
        )

    return write_team_weakness(owner_team_name, my, team_row)


def answer_asset_question(question: str, owner_team_name: str, league_id: str | None = None) -> str:
    return answer(question, owner_team_name, league_id=league_id)
