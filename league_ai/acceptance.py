"""How a trade offer compares with the trades owners in this league actually said yes to. No model call.

The ladder rungs (open / close / walk away) are priced by share: what the partner gets back over what they give up,
valued their way. Whether a given share gets accepted here was never checked. This keeps, per league, the share every
side of every completed trade got (valued with that owner's learned now weight from trade_backtest), and for any offer
says how many real accepted sides got that share or less. A low count means the offer is stingier than nearly every deal
that ever went through here; a high one means owners have taken worse.

The weekly learning job rebuilds the list (league_ai_experiments, experiment 'trade_accept_curve_v1', no migration).
The ideas log keeps each rung's numeric share, so once enough logged ideas settle (happened / not made) a real
acceptance model can be fitted on top (settled_by_share shows the counts so far).
"""
from __future__ import annotations

from bisect import bisect_right
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

EXPERIMENT = "trade_accept_curve_v1"
MIN_SIDES = 6          # fewer accepted sides than this and the comparison says nothing
OWNER_PRIOR = 4        # league deals' worth of weight mixed into each owner's own record
SHARE_BINS = ((0.0, 0.85), (0.85, 1.0), (1.0, 1.15), (1.15, 9.99))


def accepted_by_owner(trades: Iterable[Mapping[str, Any]]) -> dict[str, list[float]]:
    """Owner -> the share each of their completed trades gave them, valued with their learned now weight."""
    from league_ai.trade_backtest import DEFAULT_WEIGHT, fit_weight, side_share

    trades = list(trades)
    weights: dict[str, float] = {}
    out: dict[str, list[float]] = {}
    for t in trades:
        for owner, side in (t.get("sides") or {}).items():
            if owner not in weights:
                weights[owner] = fit_weight(trades, owner) or DEFAULT_WEIGHT
            s = side_share(side, weights[owner])
            if s is not None:
                out.setdefault(owner, []).append(round(min(s, 3.0), 3))
    return {o: sorted(v) for o, v in out.items()}


def accepted_shares(trades: Iterable[Mapping[str, Any]]) -> list[float]:
    """The share each side of each completed trade got."""
    return sorted(s for v in accepted_by_owner(trades).values() for s in v)


def owner_rate(by_owner: Mapping[str, list[float]], owner: str | None, share: float | None) -> float | None:
    """Share of this owner's past deals that gave them this share or less, pulled toward the league with OWNER_PRIOR
    deals' weight (an owner with two trades says little on their own). None without enough league trades."""
    league = sorted(s for v in by_owner.values() for s in v)
    if share is None or len(league) < MIN_SIDES:
        return None
    lg = bisect_right(league, share) / len(league)
    mine = next((v for o, v in by_owner.items() if owner and o.casefold() == owner.casefold()), [])
    n = len(mine)
    own = bisect_right(mine, share) / n if n else lg
    return (n * own + OWNER_PRIOR * lg) / (n + OWNER_PRIOR)


def save(client: Any, league_id: str, by_owner: Mapping[str, list[float]], now: datetime | None = None) -> None:
    now = now or datetime.now(timezone.utc)
    client.table("league_ai_experiments").insert({
        "league_id": league_id, "user_id": "00000000-0000-0000-0000-000000000000", "experiment": EXPERIMENT, "question": "weekly accepted-trade shares",
        "result": {"shares": sorted(s for v in by_owner.values() for s in v), "by_owner": dict(by_owner), "computed_at": now.isoformat()}, "cost_usd": 0,
    }).execute()


def load_by_owner(client: Any, league_id: str) -> dict[str, list[float]]:
    try:
        rows = (client.table("league_ai_experiments").select("result").eq("league_id", league_id).eq("experiment", EXPERIMENT)
                .order("created_at", desc=True).limit(1).execute().data or [])
    except Exception:
        return {}
    res = (rows[0].get("result") or {}) if rows else {}
    return {str(o): sorted(float(x) for x in v) for o, v in (res.get("by_owner") or {"": res.get("shares") or []}).items()}


def load(client: Any, league_id: str) -> list[float]:
    return sorted(s for v in load_by_owner(client, league_id).values() for s in v)


def compare(shares: list[float], share: float | None) -> str | None:
    """'7 of 22 real trades here gave the accepting side this share or less', or None without enough trades."""
    if share is None or len(shares) < MIN_SIDES:
        return None
    n = bisect_right(shares, share)
    return f"{n} of {len(shares)} sides of real trades here got this share or less"


def settled_by_share(rows: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, int]]:
    """Logged ideas (idea_log rows) that settled, by the share their closing offer gave the partner."""
    out = {f"{lo:.2f}-{hi:.2f}": {"happened": 0, "not_made": 0} for lo, hi in SHARE_BINS}
    for r in rows:
        res = r.get("result") or {}
        status = res.get("status")
        if status not in ("happened", "not_made"):
            continue
        for idea in res.get("ideas") or []:
            s = (idea.get("their_share") or {}).get("close")
            if s is None:
                continue
            for lo, hi in SHARE_BINS:
                if lo <= float(s) < hi:
                    out[f"{lo:.2f}-{hi:.2f}"][status] += 1
    return {k: v for k, v in out.items() if v["happened"] or v["not_made"]}
