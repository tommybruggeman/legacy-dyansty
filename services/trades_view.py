"""HTML for the website's Trades page: one card per trade, the same feed and numbers the app shows (api/trade_feed.py)."""
from __future__ import annotations

import html
from datetime import date
from typing import Any, Mapping

CSS = """
<style>
.tf-head{display:flex;justify-content:space-between;align-items:baseline;margin:.2rem 0 .8rem}
.tf-head h2{margin:0;font-size:1.35rem;font-weight:900;color:#FFF5E7}
.tf-head span{color:#9DA89C;font-size:.72rem;font-weight:800;letter-spacing:.08em}
.tf-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:14px}
.tf-card{background:#101E1D;border:1px solid rgba(226,188,91,.18);border-radius:18px;padding:14px 16px;box-shadow:0 2px 10px rgba(0,0,0,.28)}
.tf-card.mine{border-color:#E2BC5B}
.tf-top{display:flex;justify-content:space-between;align-items:center;margin-bottom:6px}
.tf-when{color:#9DA89C;font-size:.78rem;font-weight:700}
.tf-pill{font-size:.68rem;font-weight:900;letter-spacing:.06em;padding:3px 9px;border-radius:999px}
.tf-pill.live{color:#6FD39A;background:#163626}.tf-pill.muted{color:#9DA89C;background:#0C1917}
.tf-side{padding-top:8px}.tf-side+.tf-side{border-top:1px solid rgba(255,255,255,.06);margin-top:8px}
.tf-sidehead{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:4px}
.tf-owner{color:#E2BC5B;font-weight:900;font-size:.92rem}
.tf-meta{color:#9DA89C;font-size:.74rem}
.tf-row{display:flex;align-items:center;gap:10px;padding:4px 0}
.tf-dot{width:34px;height:34px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:.62rem;font-weight:900;border:1px solid #E2BC5B;color:#E2BC5B;flex:none}
.tf-name{color:#FFF5E7;font-weight:800;font-size:.92rem;line-height:1.15}
.tf-sub{color:#9DA89C;font-size:.74rem}
.tf-pts{margin-left:auto;color:#FFF5E7;font-weight:900}
.tf-value{display:flex;align-items:center;gap:8px;margin-top:10px;color:#9DA89C;font-size:.66rem;font-weight:900;letter-spacing:.05em}
.tf-bar{flex:1;display:flex;gap:2px;height:6px;border-radius:3px;overflow:hidden}
.tf-note{color:#9DA89C;font-size:.8rem;text-align:center;margin-top:12px}
</style>
"""


def _e(text: Any) -> str:
    return html.escape(str(text or ""))


def _money(n: float | None) -> str:
    n = float(n or 0)
    return f"{'-' if n < 0 else ''}${abs(n):.1f}"


def _short(name: str) -> str:
    parts = str(name or "").split()
    return f"{parts[0]} {parts[1][0]}." if len(parts) > 1 else str(name or "")


def _when(t: Mapping[str, Any]) -> str:
    try:
        d = date.fromisoformat(t["date"]).strftime("%b %-d, %Y")
    except (KeyError, ValueError):
        d = ""
    return d + (f" · Wk {t['week']}" if t.get("week") else "")


def _side(side: Mapping[str, Any], me: str | None, played: bool) -> str:
    who = "You get" if side["owner"] == me else f"{_e(side['owner'])} gets"
    meta = []
    if played and side.get("points_since") is not None:
        meta.append(f"{round(side['points_since'])} pts since")
    if side.get("cap_change"):
        meta.append(f"{'+' if side['cap_change'] > 0 else ''}{_money(side['cap_change'])} cap")
    rows = []
    for p in side.get("players") or []:
        sub = " · ".join(x for x in [p.get("pos"), _money(p["salary"]) if p.get("salary") is not None else None,
                                      f"{p['years']} yr{'' if p['years'] == 1 else 's'}" if p.get("years") else None] if x)
        pts = f"<div class='tf-pts'>{round(p['points_since'])}</div>" if played and p.get("points_since") is not None else ""
        rows.append(f"<div class='tf-row'><div class='tf-dot'>{_e(p.get('pos') or '?')}</div><div><div class='tf-name'>{_e(p['name'])}</div>"
                    f"<div class='tf-sub'>{_e(sub)}</div></div>{pts}</div>")
    for pk in side.get("picks") or []:
        orig = f" ({_e(_short(pk['original_owner']))}'s)" if pk.get("original_owner") and pk["original_owner"] != side["owner"] else ""
        rows.append(f"<div class='tf-row'><div class='tf-dot'>PICK</div><div class='tf-name'>{_e(pk['label'])}{orig}</div></div>")
    if (side.get("faab") or 0) > 0:
        rows.append(f"<div class='tf-sub'>+{_money(side['faab'])} FAAB</div>")
    return (f"<div class='tf-side'><div class='tf-sidehead'><span class='tf-owner'>{who}</span><span class='tf-meta'>{' · '.join(meta)}</span></div>"
            + "".join(rows) + "</div>")


def card(t: Mapping[str, Any], me: str | None) -> str:
    win = t.get("winning")
    if win:
        label = f"{'YOU' if win['owner'] == me else _e(_short(win['owner'])).upper()} +{round(win['by'])}"
        pill = f"<span class='tf-pill live'>{label}</span>"
    else:
        pill = "<span class='tf-pill muted'>TOO EARLY</span>"
    played = bool(t.get("weeks_since"))
    sides = "".join(_side(s, me, played) for s in t["sides"])
    shares = [s.get("market_share") or 0 for s in t["sides"]]
    value = ""
    if any(shares):
        parts = "".join(f"<div style='flex:{max(x, .02)};background:{'#E2BC5B' if i == 0 else '#5B7A6C'}'></div>" for i, x in enumerate(shares))
        value = f"<div class='tf-value'>VALUE NOW<div class='tf-bar'>{parts}</div>{' / '.join(f'{round(x * 100)}%' for x in shares)}</div>"
    mine = " mine" if me and me in t.get("owners", []) else ""
    return f"<div class='tf-card{mine}'><div class='tf-top'><span class='tf-when'>{_when(t)}</span>{pill}</div>{sides}{value}</div>"


def page_html(trades: list[Mapping[str, Any]], me: str | None, total: int) -> str:
    cards = "".join(card(t, me) for t in trades) or "<div class='tf-note'>No trades here yet.</div>"
    return (f"{CSS}<div class='tf-head'><h2>League trades</h2><span>{total} TOTAL</span></div><div class='tf-grid'>{cards}</div>"
            "<div class='tf-note'>Winning = more points scored since the trade by what each side got. Value = today's dynasty market value.</div>")
