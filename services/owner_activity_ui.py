"""Settings > League tools > Owner activity on the website: the same numbers, sessions, replays and analyst as
the app's Owner activity page (api/usage_stats.py, api/usage_ai.py). Only accounts in app_activity_viewers
(the league owner) see it; the database enforces that too.
"""
from __future__ import annotations

import html
from typing import Any

import pandas as pd
import streamlit as st

from api import usage_ai, usage_stats

RANGES = {"7 days": 7, "30 days": 30, "90 days": 90, "Year": 365}
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def is_viewer(client: Any, user_id: str | None) -> bool:
    if not user_id:
        return False
    try:
        return bool(client.table("app_activity_viewers").select("user_id").eq("user_id", user_id).limit(1).execute().data)
    except Exception:
        return False


@st.cache_data(ttl=60, show_spinner=False)
def _load(_client: Any, league_id: str, days: int) -> tuple[list[dict], list[dict], dict[str, dict]]:
    from api import commissioner

    rows, ai = usage_stats.load(_client, league_id, days=days)
    owners = usage_stats.owners_from_teams(commissioner.overview(_client, league_id)["teams"])
    return rows, ai, owners


def render_owner_activity(client: Any, league_id: str, user_id: str | None) -> None:
    if not is_viewer(client, user_id):
        st.info("Owner activity is private to the league owner's account.")
        return
    st.caption("Only you see this. Every page, tap, session and replay from the app and the website, from our own database.")
    c1, c2, c3 = st.columns([2, 2, 2])
    days = RANGES[c1.segmented_control("Range", list(RANGES), default="30 days", key="oa_range") or "30 days"]
    by = (c2.segmented_control("Group by", ["Day", "Week", "Month"], default="Day", key="oa_by") or "Day").lower()
    tz = c3.selectbox("Time zone", ["America/Denver", "America/Boise", "America/Chicago", "America/New_York", "America/Los_Angeles"], key="oa_tz")
    rows, ai, owners = _load(client, league_id, days)
    owner_names = {o["name"]: uid for uid, o in owners.items()}
    a = usage_stats.build(rows, owners=owners, ai_rows=ai, days=days, tz=tz, by=by)

    tabs = st.tabs(["Overview", "Pages", "Flows", "When", "Owners", "Sessions", "Ask"])
    t = a["totals"]
    with tabs[0]:
        cols = st.columns(4)
        cols[0].metric("Active today", f"{t['active_today']}/{t['owners']}")
        cols[1].metric("Active 7 days", f"{t['active_7d']}/{t['owners']}")
        cols[2].metric("Sessions", t["sessions"], help=f"{t['sessions_per_owner_day']} per owner per day")
        cols[3].metric("Typical session", f"{t['median_session_min']} min")
        cols = st.columns(4)
        cols[0].metric("Actions", t["actions"], help=f"{t['taps']} taps, {t['swipes']} swipes")
        cols[1].metric("GM questions", t["gm_questions"], help=f"${t['gm_cost']:.2f}")
        cols[2].metric("Alert opens", t["push_opens"])
        cols[3].metric("Errors", t["errors"])
        daily = pd.DataFrame(a["daily"]).set_index("date")
        st.markdown("**Active owners by day**")
        st.bar_chart(daily["owners"], color="#C89B4A", height=180)
        if a["quiet"]:
            st.markdown(f"**Gone quiet (7+ days):** {html.escape(', '.join(a['quiet']))}")
        if a["builds"]:
            st.markdown("**Builds in use:** " + ", ".join(f"{b['build']} ({b['owners']})" for b in a["builds"]))
    with tabs[1]:
        st.markdown(f"**Page visits by {by}**")
        if a["periods"]:
            st.dataframe(pd.DataFrame([{k: p[k] for k in ("period", "owners", "sessions", "minutes", "page_visits", "actions")} for p in reversed(a["periods"])]),
                         hide_index=True, use_container_width=True)
            pick = st.selectbox("Open a period", [p["period"] for p in reversed(a["periods"])], key="oa_period")
            p = next(x for x in a["periods"] if x["period"] == pick)
            c1, c2 = st.columns(2)
            c1.dataframe(pd.DataFrame(p["visits"]).rename(columns={"name": "page", "count": "visits"}), hide_index=True, use_container_width=True)
            c2.dataframe(pd.DataFrame(p["top_flows"]).rename(columns={"path": "flow", "count": "times"}), hide_index=True, use_container_width=True)
        st.markdown("**All pages**")
        st.dataframe(pd.DataFrame(a["screens"]), hide_index=True, use_container_width=True)
        st.markdown("**Where people tap**")
        cols = st.columns(4)
        for i, tap in enumerate(a["taps"][:8]):
            dots = "".join(f'<div style="position:absolute;left:{x * 100:.1f}%;top:{y * 100:.1f}%;width:8px;height:8px;margin:-4px;border-radius:4px;background:#C89B4A;opacity:.45"></div>'
                           for x, y in tap["spots"])
            cols[i % 4].markdown(f'<div style="position:relative;width:100px;height:206px;border:2px solid #3A3524;border-radius:14px;background:#071A12;overflow:hidden">{dots}</div>'
                                 f'<div style="font-size:13px;margin:4px 0 12px">{html.escape(tap["name"])} · {tap["count"]}</div>', unsafe_allow_html=True)
        st.markdown(f"**Never opened:** {', '.join(a['unused']['screens']) or 'none'}")
        st.markdown(f"**Never done:** {', '.join(a['unused']['actions']) or 'none'}")
    with tabs[2]:
        c1, c2 = st.columns(2)
        c1.markdown("**First move each day**")
        c1.dataframe(pd.DataFrame(a["first_moves"]), hide_index=True, use_container_width=True)
        c2.markdown("**How sessions start**")
        c2.dataframe(pd.DataFrame(a["paths"]["starts"]), hide_index=True, use_container_width=True)
        c1.markdown("**Most common next step**")
        c1.dataframe(pd.DataFrame(a["paths"]["pairs"]), hide_index=True, use_container_width=True)
        c2.markdown("**What gets done**")
        c2.dataframe(pd.DataFrame(a["actions"]), hide_index=True, use_container_width=True)
    with tabs[3]:
        st.markdown("**Day and hour** (events)")
        top = max([1, *[v for row in a["heatmap"] for v in row]])
        cells = "".join(
            f'<div style="display:flex;align-items:center"><span style="width:36px;font-size:12px;color:#C8BEAD">{DAYS[d]}</span>'
            + "".join(f'<span title="{v}" style="width:22px;height:22px;margin:1px;border-radius:3px;background:{"#C89B4A" if v else "#112B20"};'
                      f'opacity:{(0.2 + 0.8 * v / top) if v else 1:.2f}"></span>' for v in row) + "</div>"
            for d, row in enumerate(a["heatmap"]))
        hours = "".join(f'<span style="width:24px;font-size:10px;color:#C8BEAD">{(h % 12 or 12) if h % 3 == 0 else ""}</span>' for h in range(24))
        st.markdown(f'{cells}<div style="display:flex;margin-left:36px">{hours}</div>', unsafe_allow_html=True)
        st.dataframe(pd.DataFrame([{"day": w["day"], "avg owners": w["avg_owners"], "events": w["events"], "top pages": ", ".join(w["top_screens"]),
                                    "top actions": ", ".join(w["top_actions"])} for w in a["weekdays"]]), hide_index=True, use_container_width=True)
    with tabs[4]:
        st.dataframe(pd.DataFrame([{**{k: o[k] for k in ("name", "last_seen", "days_active", "sessions", "minutes", "actions", "taps", "gm_questions", "gm_cost",
                                                          "app_share", "build", "favorite_first_move")},
                                    "device": (o.get("device") or {}).get("os"), "time zone": (o.get("device") or {}).get("tz")} for o in a["owners"]]),
                     hide_index=True, use_container_width=True)
    with tabs[5]:
        who = st.selectbox("Owner", ["Everyone", *sorted(owner_names)], key="oa_owner")
        picked = usage_stats.build(rows, owners=owners, days=days, tz=tz, owner=owner_names.get(who))["sessions"] if who != "Everyone" else a["sessions"]
        if not picked:
            st.info("No sessions in this range.")
        else:
            labels = [f"{s['owner']} · {s['start'][:16].replace('T', ' ')} · {s['minutes']} min · {s['source']}" for s in picked]
            i = st.selectbox("Session", range(len(picked)), format_func=lambda k: labels[k], key="oa_session")
            _replay(client, picked[i])
    with tabs[6]:
        q = st.text_input("Ask anything about how owners use the app", key="oa_q", placeholder="What do owners do first on Thursdays?")
        deep = st.checkbox("Dig deeper (slower, a few cents)", key="oa_deep")
        if st.button("Ask", key="oa_ask") and q.strip():
            with st.spinner("Looking through the activity..."):
                try:
                    out = usage_ai.ask(q, rows=rows, owners=owners, ai_rows=ai, days=days, tz=tz, deep=deep)
                    from league_ai import usage
                    u = out["usage"]
                    usage.record(client, usage.usage_row(league_id=league_id, user_id=user_id, feature="usage_analyst", model=out["model"],
                                                         input_tokens=u.get("input_tokens", 0), output_tokens=u.get("output_tokens", 0),
                                                         cache_read_tokens=u.get("cache_read_tokens", 0), cache_write_tokens=u.get("cache_write_tokens", 0),
                                                         tool_calls=out["tools"], latency_ms=out["latency_ms"]))
                    st.session_state.setdefault("oa_log", []).insert(0, (q, out["answer"]))
                except Exception as exc:
                    st.error(f"The analyst couldn't answer just now ({type(exc).__name__}).")
        for question, answer in st.session_state.get("oa_log", []):
            st.markdown(f"**{html.escape(question)}**")
            st.markdown(answer)


def _replay(client: Any, session: dict[str, Any]) -> None:
    frames = session.get("frames") or []
    c1, c2 = st.columns([1, 2])
    with c1:
        if frames:
            try:
                signed = client.storage.from_("replays").create_signed_urls([f["path"] for f in frames], 3600)
                urls = {x["path"]: x.get("signedURL") or x.get("signedUrl") for x in signed or []}
            except Exception:
                urls = {}
            k = st.slider("Picture", 1, len(frames), 1, key=f"oa_frame_{session['start']}") - 1
            frame = frames[k]
            from datetime import datetime
            at = datetime.fromisoformat(frame["at"]).timestamp()
            taps = [s for s in session["steps"] if s["kind"] == "tap" and s.get("detail") and at - 2 < datetime.fromisoformat(s["t"]).timestamp() <= at]
            rings = "".join(f'<div style="position:absolute;left:{s["detail"]["x"] * 100:.1f}%;top:{s["detail"]["y"] * 100:.1f}%;width:28px;height:28px;margin:-14px;'
                            f'border-radius:14px;border:3px solid #FF5A5A;background:rgba(255,90,90,.25)"></div>' for s in taps)
            url = urls.get(frame["path"]) or ""
            st.markdown(f'<div style="position:relative;width:260px;border:3px solid #3A3524;border-radius:22px;overflow:hidden">'
                        f'<img src="{html.escape(url)}" style="width:100%;display:block"/>{rings}</div>', unsafe_allow_html=True)
            st.caption(f"{k + 1} of {len(frames)}")
        else:
            st.info("No replay pictures for this session (website, or an older app build).")
    with c2:
        st.dataframe(pd.DataFrame([{"time": s["at"], "what": s["name"], "seconds": s["secs"] or ""} for s in session["steps"]]),
                     hide_index=True, use_container_width=True, height=520)
