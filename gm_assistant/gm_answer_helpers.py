from __future__ import annotations

from auth import service_client


def _num(v, default=0.0):
    try:
        if v is None:
            return default
        return float(v)
    except Exception:
        return default


def _name(r):
    return (r.get("player_name") or r.get("player") or "Unknown").strip()


def _pos(r):
    return r.get("pos") or "?"


def _fmt_money(v):
    return f"${_num(v):g}"


def _client():
    return service_client()


def load_asset_rows():
    try:
        return _client().table("roster_asset_values").select("*").execute().data or []
    except Exception:
        return []


def load_nfl_intelligence_rows():
    try:
        return _client().table("player_nfl_intelligence").select("*").execute().data or []
    except Exception:
        return []


def _nfl_key(r):
    return (str(r.get("sleeper_id")), str(r.get("owner_team_name")))


def _merge_nfl(row, nfl_by_key):
    merged = dict(row)
    nfl = nfl_by_key.get(_nfl_key(row), {})
    if nfl:
        merged["nfl_intelligence_score"] = _num(nfl.get("nfl_intelligence_score"), 50)
        merged["nfl_intelligence_grade"] = nfl.get("nfl_intelligence_grade")
        merged["nfl_intelligence_flags"] = nfl.get("nfl_intelligence_flags") or []
        merged["nfl_intelligence_summary"] = nfl.get("nfl_intelligence_summary")
        merged["availability_score"] = _num(nfl.get("availability_score"), 50)
        merged["opportunity_score"] = _num(nfl.get("opportunity_score"), 50)
        merged["role_stability_score"] = _num(nfl.get("role_stability_score"), 50)
        merged["injury_risk_score"] = _num(nfl.get("injury_risk_score"), 0)
        merged["depth_chart_risk_score"] = _num(nfl.get("depth_chart_risk_score"), 0)
    else:
        merged["nfl_intelligence_score"] = 50
        merged["nfl_intelligence_grade"] = "UNKNOWN_CONTEXT"
        merged["nfl_intelligence_flags"] = []
        merged["nfl_intelligence_summary"] = None
        merged["availability_score"] = 50
        merged["opportunity_score"] = 50
        merged["role_stability_score"] = 50
        merged["injury_risk_score"] = 0
        merged["depth_chart_risk_score"] = 0
    return merged


def load_asset_rows_with_nfl():
    assets = load_asset_rows()
    nfl = load_nfl_intelligence_rows()
    nfl_by_key = {_nfl_key(r): r for r in nfl}
    return [_merge_nfl(r, nfl_by_key) for r in assets]


def nfl_warning_text(row):
    grade = row.get("nfl_intelligence_grade")
    score = _num(row.get("nfl_intelligence_score"), 50)
    flags = row.get("nfl_intelligence_flags") or []

    if grade in {"BAD_CONTEXT", "RISKY_CONTEXT"} or score < 50:
        flag_text = ", ".join(flags) if flags else "context risk"
        return f" NFL risk: **{grade} {score:.1f}** ({flag_text})."

    return ""


def market_check_candidates(owner_team_name: str, limit: int = 5):
    rows = [r for r in load_asset_rows_with_nfl() if r.get("owner_team_name") == owner_team_name]

    candidates = []
    for r in rows:
        salary = _num(r.get("salary"))
        contract = _num(r.get("contract_value_score"))
        trade = _num(r.get("market_liquidity_score"))
        engine = _num(r.get("engine_player_score") or r.get("dynasty_asset_score"))
        recent = _num(r.get("win_now_asset_score"))
        nfl_score = _num(r.get("nfl_intelligence_score"), 50)

        # Market-check score should surface name value + expensive/fragile contracts,
        # but discount players whose NFL situation is so bad that the market may already know.
        score = (
            trade * 0.35
            + engine * 0.15
            + recent * 0.10
            + salary * 0.30
            - contract * 0.20
            + max(0, 55 - nfl_score) * 0.25
        )

        if r.get("cornerstone_flag") is True:
            continue

        if trade < 35 and engine < 45:
            continue

        candidates.append({
            "player": _name(r),
            "pos": _pos(r),
            "salary": salary,
            "years": _num(r.get("years")),
            "contract": contract,
            "trade": trade,
            "engine": engine,
            "recent": recent,
            "nfl": nfl_score,
            "nfl_grade": r.get("nfl_intelligence_grade"),
            "nfl_flags": r.get("nfl_intelligence_flags") or [],
            "nfl_warning": nfl_warning_text(r),
            "score": score,
        })

    return sorted(candidates, key=lambda x: x["score"], reverse=True)[:limit]


def trade_target_candidates(owner_team_name: str, question: str = "", limit: int = 8):
    q = str(question or "").lower()

    wanted = []
    if "rb" in q or "running back" in q:
        wanted.append("RB")
    if "te" in q or "tight end" in q:
        wanted.append("TE")
    if "wr" in q or "receiver" in q:
        wanted.append("WR")
    if "qb" in q or "quarterback" in q:
        wanted.append("QB")

    if not wanted:
        wanted = ["RB", "TE"]

    rows = [r for r in load_asset_rows_with_nfl() if r.get("owner_team_name") != owner_team_name]

    candidates = []
    for r in rows:
        pos = _pos(r)
        if pos not in wanted:
            continue

        salary = _num(r.get("salary"))
        contract = _num(r.get("contract_value_score"))
        trade = _num(r.get("market_liquidity_score"))
        engine = _num(r.get("engine_player_score") or r.get("dynasty_asset_score"))
        recent = _num(r.get("win_now_asset_score"))
        nfl_score = _num(r.get("nfl_intelligence_score"), 50)

        score = (
            recent * 0.25
            + engine * 0.20
            + contract * 0.20
            + trade * 0.10
            + nfl_score * 0.20
            - salary * 0.10
        )

        if engine < 35 and recent < 35:
            continue

        candidates.append({
            "player": _name(r),
            "team": r.get("owner_team_name"),
            "pos": pos,
            "salary": salary,
            "years": _num(r.get("years")),
            "contract": contract,
            "trade": trade,
            "engine": engine,
            "recent": recent,
            "nfl": nfl_score,
            "nfl_grade": r.get("nfl_intelligence_grade"),
            "nfl_flags": r.get("nfl_intelligence_flags") or [],
            "nfl_warning": nfl_warning_text(r),
            "score": score,
        })

    return sorted(candidates, key=lambda x: x["score"], reverse=True)[:limit]


def format_market_check(candidates):
    if not candidates:
        return "I do not have enough ranked roster data to identify a clean market-check candidate yet."

    lines = []
    for i, c in enumerate(candidates, 1):
        lines.append(
            f"{i}. **{c['player']}** ({c['pos']}) — "
            f"{_fmt_money(c['salary'])}/{c['years']:g} yrs, "
            f"trade {c['trade']:.1f}, contract {c['contract']:.1f}, "
            f"recent {c['recent']:.1f}, NFL {c['nfl']:.1f}.{c['nfl_warning']}"
        )
    return "\n".join(lines)


def format_trade_targets(candidates):
    if not candidates:
        return "I do not have enough league-wide target data to rank specific trade targets yet."

    lines = []
    for i, c in enumerate(candidates, 1):
        lines.append(
            f"{i}. **{c['player']}** ({c['pos']}, {c['team']}) — "
            f"{_fmt_money(c['salary'])}/{c['years']:g} yrs, "
            f"recent {c['recent']:.1f}, contract {c['contract']:.1f}, "
            f"trade {c['trade']:.1f}, NFL {c['nfl']:.1f}.{c['nfl_warning']}"
        )
    return "\n".join(lines)


def player_nfl_context_note(player_name: str, owner_team_name: str | None = None):
    rows = load_asset_rows_with_nfl()
    needle = str(player_name or "").lower().strip()

    matches = []
    for r in rows:
        name = _name(r).lower()
        if needle and needle in name:
            if owner_team_name is None or r.get("owner_team_name") == owner_team_name:
                matches.append(r)

    if not matches:
        return ""

    r = matches[0]
    grade = r.get("nfl_intelligence_grade")
    score = _num(r.get("nfl_intelligence_score"), 50)
    flags = r.get("nfl_intelligence_flags") or []
    summary = r.get("nfl_intelligence_summary")

    if grade in {"BAD_CONTEXT", "RISKY_CONTEXT"} or score < 50:
        flags_text = ", ".join(flags) if flags else "context risk"
        return (
            f"\n\nNFL context warning: **{grade} ({score:.1f})**. "
            f"Flags: {flags_text}. "
            f"{summary or ''}"
        )

    return (
        f"\n\nNFL context: **{grade} ({score:.1f})**. "
        f"{summary or ''}"
    )


def load_contract_efficiency_rows():
    try:
        return _client().table("player_contract_efficiency").select("*").execute().data or []
    except Exception:
        return []


def player_contract_efficiency_note(player_name: str, owner_team_name: str | None = None):
    rows = load_contract_efficiency_rows()
    needle = str(player_name or "").lower().strip()

    all_matches = []
    owner_matches = []

    for r in rows:
        name = str(r.get("player_name") or "").lower().strip()
        if needle and needle in name:
            all_matches.append(r)
            if owner_team_name is None or r.get("owner_team_name") == owner_team_name:
                owner_matches.append(r)

    matches = owner_matches or all_matches

    if not matches:
        return ""

    r = sorted(
        matches,
        key=lambda x: float(x.get("contract_efficiency_score") or 0),
        reverse=True,
    )[0]

    summary = r.get("contract_efficiency_summary") or ""
    grade = r.get("contract_efficiency_grade")
    rank = r.get("position_contract_rank")
    pct = r.get("position_contract_percentile")
    profile = r.get("evidence_profile")
    owner = r.get("owner_team_name")

    owner_text = ""
    if owner_team_name and owner and owner != owner_team_name:
        owner_text = f" Current league context: he is on **{owner}**."

    return (
        f"\n\nLeague-relative contract view: {summary} "
        f"(grade={grade}, position rank={rank}, percentile={pct}, evidence={profile})."
        f"{owner_text}"
    )



def load_gm_argument_rows():
    try:
        return _client().table("player_gm_arguments").select("*").execute().data or []
    except Exception:
        return []


def player_gm_arguments(player_name: str, owner_team_name: str | None = None, argument_type: str = "contract"):
    rows = load_gm_argument_rows()
    needle = str(player_name or "").lower().strip()

    all_matches = []
    owner_matches = []

    for r in rows:
        name = str(r.get("player_name") or "").lower().strip()
        if needle and needle in name and r.get("argument_type") == argument_type:
            all_matches.append(r)
            if owner_team_name is None or r.get("owner_team_name") == owner_team_name:
                owner_matches.append(r)

    matches = owner_matches or all_matches

    return sorted(
        matches,
        key=lambda x: float(x.get("weight") or 0),
        reverse=True,
    )
