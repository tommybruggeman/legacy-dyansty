from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from auth import service_client


INPUT_DIR = Path("data/prospects")


def _num(series, default=0):
    return pd.to_numeric(series, errors="coerce").fillna(default)


def draft_capital_score(round_: float, pick: float) -> float:
    if pd.isna(round_):
        return 50

    round_ = float(round_)

    if pd.isna(pick):
        pick = 0

    pick = float(pick)

    # Pre-draft/projected capital.
    # If we only know projected round, avoid treating pick 0 as 1.01.
    if pick <= 0:
        if round_ == 1:
            return 88
        if round_ == 2:
            return 76
        if round_ == 3:
            return 62
        return 50

    # Actual draft capital.
    if pick <= 5:
        return 100
    if pick <= 10:
        return 96
    if pick <= 20:
        return 92
    if pick <= 32:
        return 88
    if pick <= 48:
        return 82
    if pick <= 64:
        return 75
    if pick <= 100:
        return 62
    return 45


def production_score(row) -> float:
    pos = str(row.get("position", "")).upper()
    ypg = float(row.get("college_yards_per_game", 0) or 0)
    td_rate = float(row.get("college_td_rate", 0) or 0)
    rec_pg = float(row.get("college_receptions_per_game", 0) or 0)

    if pos == "QB":
        score = min(100, (ypg / 350) * 70 + (td_rate / 3.5) * 30)
    elif pos == "RB":
        score = min(100, (ypg / 170) * 65 + (td_rate / 1.6) * 25 + (rec_pg / 3) * 10)
    elif pos == "WR":
        score = min(100, (ypg / 115) * 60 + (td_rate / 1.0) * 20 + (rec_pg / 7) * 20)
    elif pos == "TE":
        score = min(100, (ypg / 85) * 55 + (td_rate / 0.7) * 20 + (rec_pg / 6.5) * 25)
    else:
        score = 50

    return round(max(30, score), 2)


def declare_score(value: str) -> float:
    v = str(value or "").lower()
    if "junior" in v or "early" in v:
        return 90
    if "senior" in v:
        return 75
    if "grad" in v:
        return 65
    return 70



def load_signal_weights() -> dict:
    sb = service_client()

    rows = (
        sb.table("prospect_signal_weights")
        .select("*")
        .execute()
        .data or []
    )

    return {r["pos"]: r for r in rows}


def _weight(row: dict, key: str, default: float) -> float:
    try:
        return float(row.get(key) if row.get(key) is not None else default)
    except Exception:
        return default


def _historical_prior(row: dict) -> float:
    # Converts historical hit-rate context into a 0-100 prior.
    # Starter hit rate matters most, elite rate adds upside signal,
    # and year 1/2/3 average impact captures development curve.
    starter = _weight(row, "starter_hit_rate", 20)
    elite = _weight(row, "elite_hit_rate", 5)
    y1 = _weight(row, "avg_year1_impact", 25)
    y2 = _weight(row, "avg_year2_impact", 32)
    y3 = _weight(row, "avg_year3_impact", 35)

    return round(
        starter * 0.35
        + elite * 0.20
        + y1 * 0.15
        + y2 * 0.15
        + y3 * 0.15,
        2,
    )


def calculate_scores(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    df["draft_round"] = _num(df["draft_round"])
    df["draft_pick"] = _num(df["draft_pick"])

    df["draft_capital_score"] = df.apply(
        lambda r: draft_capital_score(r["draft_round"], r["draft_pick"]),
        axis=1,
    )

    df["college_production_score"] = df.apply(production_score, axis=1)
    df["declare_score"] = df["declare_class"].apply(declare_score)

    df["offensive_line_score"] = _num(df["offensive_line_score"], 70)
    df["scheme_fit_score"] = _num(df["scheme_fit_score"], 70)
    df["opportunity_score"] = _num(df["opportunity_score"], 70)

    df["landing_spot_score"] = (
        df["offensive_line_score"] * 0.30
        + df["scheme_fit_score"] * 0.35
        + df["opportunity_score"] * 0.35
    ).round(2)

    signal_weights = load_signal_weights()

    def learned_prospect_score(r):
        pos = r.get("position")
        weights = signal_weights.get(pos, {})

        draft_w = _weight(weights, "draft_capital", 0.32)
        consensus_w = _weight(weights, "consensus_rank", 0.20)
        production_w = _weight(weights, "production", 0.22)
        age_w = _weight(weights, "age_declare", 0.10)
        situation_w = _weight(weights, "situation", 0.10)
        market_w = _weight(weights, "market", 0.08)

        historical_prior = _historical_prior(weights)

        # Until true consensus_rank is stored in this input file, use draft capital
        # as a proxy. Once discovery sources are wired, this becomes consensus_score.
        consensus_proxy = r["draft_capital_score"]

        raw = (
            r["draft_capital_score"] * draft_w
            + consensus_proxy * consensus_w
            + r["college_production_score"] * production_w
            + r["declare_score"] * age_w
            + r["landing_spot_score"] * situation_w
            + historical_prior * market_w
        )

        total_weight = draft_w + consensus_w + production_w + age_w + situation_w + market_w

        return round(raw / total_weight, 2) if total_weight else 0

    df["historical_position_prior"] = df["position"].apply(
        lambda pos: _historical_prior(signal_weights.get(pos, {}))
    )

    df["prospect_score"] = df.apply(learned_prospect_score, axis=1).round(2)

    return df


def build_rows() -> list[dict]:
    files = sorted(INPUT_DIR.glob("prospect_inputs_*.csv"))

    if not files:
        raise FileNotFoundError(f"No prospect input files found in: {INPUT_DIR}")

    frames = []
    for file in files:
        print(f"Loading prospect input: {file}")
        try:
            df = pd.read_csv(file)
        except pd.errors.EmptyDataError:
            print(f"Skipping empty prospect input: {file}")
            continue

        if df.empty:
            print(f"Skipping empty prospect input: {file}")
            continue

        frames.append(df)

    df = pd.concat(frames, ignore_index=True)

    if "risk_notes" in df.columns:
        before = len(df)
        df = df[
            ~df["risk_notes"].astype(str).str.contains("Auto-detected from Sleeper", case=False, na=False)
        ].copy()
        removed = before - len(df)
        if removed:
            print(f"Removed {removed} placeholder Sleeper-detected prospect rows")
    df = calculate_scores(df)

    now = datetime.now(timezone.utc).isoformat()

    allowed_cols = [
        "player_name",
        "draft_year",
        "position",
        "nfl_team",
        "college",
        "final_college_season",
        "declare_class",
        "college_games",
        "college_yards_per_game",
        "college_td_rate",
        "college_receptions_per_game",
        "draft_round",
        "draft_pick",
        "draft_capital_score",
        "landing_spot_score",
        "offensive_line_score",
        "scheme_fit_score",
        "opportunity_score",
        "historical_position_prior",
        "prospect_score",
        "fantasy_role",
        "risk_notes",
        "upside_notes",
    ]

    clean_df = df[allowed_cols].copy()
    clean_df = clean_df.where(pd.notnull(clean_df), None)

    rows = clean_df.to_dict("records")

    for row in rows:
        row["created_at"] = now

    return rows



def print_score_debug(df: pd.DataFrame) -> None:
    cols = [
        "player_name",
        "position",
        "draft_round",
        "draft_pick",
        "draft_capital_score",
        "college_yards_per_game",
        "college_td_rate",
        "college_receptions_per_game",
        "college_dominator_rating",
        "landing_spot_score",
        "scheme_fit_score",
        "opportunity_score",
        "historical_position_prior",
        "prospect_score",
    ]

    print("\nPROSPECT SCORE DEBUG")
    print("=" * 100)

    existing = [c for c in cols if c in df.columns]
    print(df[existing].sort_values("prospect_score", ascending=False).head(40).to_string(index=False))

def main() -> None:
    sb = service_client()
    rows = build_rows()

    try:
        debug_df = pd.DataFrame(rows)
        if not debug_df.empty:
            print_score_debug(debug_df)
    except Exception as e:
        print(f"Could not print score debug: {e}")

    print(f"Built computed prospect context rows: {len(rows)}")

    if rows:
        sb.table("player_prospect_context").upsert(
            rows,
            on_conflict="draft_year,player_name",
        ).execute()

    print(f"Upserted {len(rows)} computed prospect context rows.")


if __name__ == "__main__":
    main()
