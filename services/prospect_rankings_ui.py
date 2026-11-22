"""Website half of the Prospect Power Rankings (the app's /prospects page): same edition, same stars (api/prospects.py)."""
from __future__ import annotations

import html
from typing import Any

import streamlit as st

from api import prospects

POS_TABS = ["All players", "QB", "RB", "WR", "TE"]
MOVE = {"up": ("▲", "#4CC38A"), "down": ("▼", "#E08A8A"), "new": ("NEW", "#C8BEAD"), "same": ("–", "#C8BEAD")}


def _move(p: dict[str, Any]) -> str:
    mark, color = MOVE.get(p.get("move") or "same", MOVE["same"])
    if p.get("move") in ("up", "down") and p.get("was"):
        mark += f" {abs(int(p['was']) - int(p['rank']))}"
    return f'<span style="color:{color};font-weight:900;font-size:11px">{mark}</span>'


def render(client: Any, user_id: str) -> None:
    page = prospects.page(client, user_id)
    ed = page.get("edition")
    st.subheader("Prospect Power Rankings")
    if not ed:
        st.info("The first board comes out on Tuesday.")
        return
    starred = set(page.get("starred") or [])
    st.markdown(f"### {html.escape(ed.get('headline') or '')}")
    if ed.get("dek"):
        st.caption(ed["dek"])
    st.caption(f"By Legacy GM · {ed['date']} · No. {ed.get('number') or 1} · Superflex rankings")
    if ed.get("lede"):
        st.write(ed["lede"])
    tab = st.segmented_control("Position", POS_TABS, default="All players", key="prospect_rank_pos") or "All players"
    pos_rank: dict[str, int] = {}
    rows = []
    for p in ed.get("players") or []:
        pos_rank[p["pos"]] = pos_rank.get(p["pos"], 0) + 1
        if tab == "All players" or p["pos"] == tab:
            rows.append((p, pos_rank[p["pos"]]))
    for p, pr in rows:
        rank = p["rank"] if tab == "All players" else pr
        with st.container(border=True):
            left, right = st.columns([1, 5])
            with left:
                st.markdown(
                    f'<div style="text-align:center"><div style="color:#C89B4A;font-weight:900;font-size:26px">{rank}</div>'
                    f'<img src="{html.escape(p["photo"])}" style="width:72px;height:72px;border-radius:12px;object-fit:cover;background:#2C2512" '
                    f'onerror="this.style.visibility=\'hidden\'"/></div>', unsafe_allow_html=True)
            with right:
                tags = " · ".join([p.get("role") or "", *p.get("tags", [])]).strip(" ·")
                st.markdown(
                    f'<b style="font-size:17px">{html.escape(p["name"])}</b> {_move(p)}<br>'
                    f'<span style="color:#C8BEAD;font-size:12px">{p["pos"]}{pr} · {html.escape(p.get("school") or "")} · {html.escape(p.get("size") or "")}</span><br>'
                    f'<span style="color:#4CC38A;font-size:11px;font-weight:800">{html.escape(tags)}</span><br>'
                    f'<span style="color:#C8BEAD;font-size:12px">{html.escape(p.get("line") or "")}</span><br>'
                    f'<span style="color:#C8BEAD;font-size:12px">NFL mock: {html.escape(p.get("mock") or "—")} · Boards: {html.escape(p.get("boards") or "—")}</span>',
                    unsafe_allow_html=True)
                st.write(p.get("text") or "")
                on = p["athlete_id"] in starred
                if st.button("★ Starred" if on else "☆ Star", key=f"prospect_star_{p['athlete_id']}"):
                    prospects.set_star(client, user_id, p["athlete_id"], not on)
                    st.rerun()
    if ed.get("sources"):
        st.caption("Sources the GM read this week: " + " · ".join(
            f"[{s['title'] or s['url']}]({s['url']})" for s in ed["sources"]))
