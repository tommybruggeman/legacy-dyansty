from __future__ import annotations

import csv
import io
from difflib import SequenceMatcher

import streamlit as st

from auth import require_login, current_user, _sb
from services.import_resolver import normalize_name, build_candidate_label

st.set_page_config(page_title="Review Player Matches", page_icon="🏈", layout="wide")

require_login("home.py")
u = current_user()

access = st.session_state.get("sb_access_token")
refresh = st.session_state.get("sb_refresh_token")

if not access:
    st.error("No access token. Please sign in again.")
    st.stop()

sb = _sb(access)

try:
    sb.auth.set_session(access, refresh)
except Exception:
    pass

league_id = st.session_state.get("import_league_id") or st.session_state.get("active_league_id")

if not league_id:
    st.error("Missing league context. Go back to Import Contracts.")
    st.stop()

st.markdown(
    """
    <style>
        [data-testid="stSidebar"] {display: none;}
        [data-testid="collapsedControl"] {display: none;}

        .block-container {
            max-width: 1180px;
            padding-top: 4rem;
            padding-bottom: 5rem;
        }

        h1, h2, h3, p { color: #F5EBD7; }
        label, .stCaption { color: #CFC6B4 !important; }

        div[data-testid="stVerticalBlockBorderWrapper"] {
            background: rgba(13, 36, 26, 0.72);
            border: 1px solid rgba(200, 155, 74, 0.22);
            border-radius: 24px;
            padding: 16px;
        }

        .status-pill {
            display: inline-block;
            padding: 6px 12px;
            border-radius: 999px;
            background: rgba(200, 155, 74, 0.18);
            color: #E2BC5B;
            font-size: .78rem;
            font-weight: 800;
            margin-bottom: 1rem;
        }

        .review-title {
            font-size: 1.45rem;
            font-weight: 900;
            color: #F5EBD7;
            margin-bottom: 8px;
        }

        .review-copy {
            color: #CFC6B4;
            font-size: .95rem;
            margin-bottom: 0;
        }

        .reason-box {
            background: rgba(200, 155, 74, 0.16);
            border: 1px solid rgba(200, 155, 74, 0.18);
            border-radius: 14px;
            padding: 12px 14px;
            color: #F5EBD7;
            font-weight: 700;
            margin: 14px 0;
        }

        .hint-box {
            background: rgba(31, 84, 95, 0.45);
            border: 1px solid rgba(31, 84, 95, 0.55);
            border-radius: 14px;
            padding: 12px 14px;
            color: #8FD6FF;
            margin-top: 16px;
            margin-bottom: 20px;
        }

        .progress-box {
            background: rgba(200, 155, 74, 0.10);
            border: 1px solid rgba(200, 155, 74, 0.18);
            border-radius: 16px;
            padding: 14px 16px;
            color: #F5EBD7;
            font-weight: 800;
        }

        .stButton button,
        .stDownloadButton button,
        .stFormSubmitButton button {
            background: linear-gradient(135deg, #C89B4A, #B88735);
            color: #071A12;
            border: none;
            border-radius: 12px;
            font-weight: 800;
            min-height: 48px;
        }

        .stButton button:hover,
        .stDownloadButton button:hover,
        .stFormSubmitButton button:hover {
            transform: translateY(-1px);
            opacity: 0.95;
        }

        .stTextInput > div > div > input,
        .stSelectbox > div > div {
            background-color: rgba(17, 43, 32, 0.85);
            border-radius: 12px;
            border: 1px solid rgba(200, 155, 74, 0.22);
            color: #F5EBD7;
        }

        hr { border-color: rgba(255,255,255,0.08); }

        @keyframes spin {
            0% { transform: rotate(0deg); }
            100% { transform: rotate(360deg); }
        }
    </style>
    """,
    unsafe_allow_html=True,
)

FANTASY_POSITIONS = {"QB", "RB", "WR", "TE", "K", "DEF"}

REASON_TEXT = {
    "NOT_FOUND": "No automatic match was made. Choose the correct Sleeper player below.",
    "AMBIGUOUS_NEEDS_POSITION": "Multiple Sleeper players share this name. Choose the correct one.",
    "AMBIGUOUS_DUPLICATE_NAME_AND_POSITION": "Multiple players share this name and position. Choose the correct one.",
    "POSITION_MISMATCH_OR_DUPLICATE_NAME": "Name found, but position did not match cleanly. Choose the correct one.",
}


def is_bad_player_row(row: dict) -> bool:
    name = str(row.get("player_name") or "").strip().lower()
    pos = str(row.get("player_position") or "").strip().lower()
    return name in {"", "nan", "none"} or pos == "nan"


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_all_sleeper_players() -> list[dict]:
    players = []
    page_size = 1000
    start = 0

    while True:
        batch = (
            sb.table("sleeper_players")
            .select("sleeper_player_id, full_name, search_name, position, team, status, is_active")
            .range(start, start + page_size - 1)
            .execute()
            .data
            or []
        )

        players.extend(batch)

        if len(batch) < page_size:
            break

        start += page_size

    return players


def similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, normalize_name(a), normalize_name(b)).ratio()


def split_name(name: str) -> tuple[str, str]:
    parts = normalize_name(name).split()

    if len(parts) >= 2:
        return parts[0], parts[-1]

    return normalize_name(name), ""


def smart_name_match(
    csv_name: str,
    player_name: str,
    csv_pos: str | None,
    player_pos: str | None,
) -> bool:
    score = similarity(csv_name, player_name)

    if score >= 0.80:
        return True

    csv_first, csv_last = split_name(csv_name)
    p_first, p_last = split_name(player_name)

    same_last = csv_last and p_last and csv_last == p_last
    same_initial = csv_first and p_first and csv_first[0] == p_first[0]
    same_pos = csv_pos and player_pos and csv_pos.upper() == player_pos.upper()

    return bool(same_last and same_initial and same_pos)


def candidate_score(player: dict, csv_name: str, csv_pos: str | None) -> tuple:
    pos = (player.get("position") or "").upper()
    status = (player.get("status") or "").lower()
    is_active = player.get("is_active")
    full_name = player.get("full_name") or ""
    search_name = player.get("search_name") or ""

    best_name_score = max(similarity(csv_name, full_name), similarity(csv_name, search_name))

    exact_pos = 0 if csv_pos and pos == csv_pos else 1
    fantasy_pos = 0 if pos in FANTASY_POSITIONS else 1
    active = 0 if is_active is True or status == "active" else 1

    return (-best_name_score, exact_pos, fantasy_pos, active, pos)


def find_candidates(players: list[dict], csv_name: str, csv_pos: str | None) -> list[dict]:
    target = normalize_name(csv_name)
    seen_ids = set()
    matches = []

    for p in players:
        pid = p.get("sleeper_player_id")

        if not pid or pid in seen_ids:
            continue

        full = p.get("full_name") or ""
        search = p.get("search_name") or ""
        pos = p.get("position") or ""

        keys = {normalize_name(full), normalize_name(search)}
        exact_match = target in keys
        fuzzy_match = (
            smart_name_match(csv_name, full, csv_pos, pos)
            or smart_name_match(csv_name, search, csv_pos, pos)
        )

        if exact_match or fuzzy_match:
            seen_ids.add(pid)
            matches.append(p)

    matches.sort(key=lambda p: candidate_score(p, csv_name, csv_pos))

    return [
        {
            "id": p.get("sleeper_player_id"),
            "label": build_candidate_label(p),
            "raw": p,
        }
        for p in matches[:8]
        if p.get("sleeper_player_id")
    ]


def make_player_lookup_options(players: list[dict]) -> tuple[list[str], dict[str, str]]:
    options = ["-- choose search result --"]
    label_to_id = {}
    seen_labels = set()

    sorted_players = sorted(
        players,
        key=lambda x: ((x.get("full_name") or "").lower(), x.get("position") or ""),
    )

    for p in sorted_players:
        pid = p.get("sleeper_player_id")

        if not pid:
            continue

        label = build_candidate_label(p)

        if label in seen_labels:
            continue

        seen_labels.add(label)
        options.append(label)
        label_to_id[label] = pid

    return options, label_to_id


def get_search_matches(
    search_term: str,
    all_options: list[str],
    max_results: int = 12,
) -> list[str]:
    term = search_term.lower().strip()

    if not term:
        return []

    return [
        label
        for label in all_options
        if label != "-- choose search result --" and term in label.lower()
    ][:max_results]


def save_match(row_id: str, resolved_id: str) -> None:
    sb.table("contract_import_staging").update(
        {
            "resolved_sleeper_player_id": resolved_id,
            "match_status": "matched_manual",
            "match_reason": "MANUAL_FIX",
        }
    ).eq("id", row_id).execute()


def clear_review_state(row_id: str) -> None:
    for key in [
        f"pick_{row_id}",
        f"search_text_{row_id}",
        f"search_{row_id}",
        f"manual_{row_id}",
    ]:
        st.session_state.pop(key, None)


st.title("Review Player Matches")
st.caption("Review unmatched players, choose suggested matches, or search the full Sleeper database.")

loading_slot = st.empty()

loading_slot.markdown(
    """
    <div class="review-shell" style="text-align:center; min-height:260px; display:flex; flex-direction:column; justify-content:center; align-items:center;">
        <div style="width:54px;height:54px;border:4px solid rgba(245,235,215,0.16);border-top:4px solid #C89B4A;border-radius:50%;animation:spin 1s linear infinite;margin-bottom:22px;"></div>
        <div class="review-title">Loading player matches</div>
        <p class="review-copy">Checking unmatched players against the Sleeper database...</p>
    </div>
    """,
    unsafe_allow_html=True,
)

sleeper_players = fetch_all_sleeper_players()
all_player_options, all_player_label_to_id = make_player_lookup_options(sleeper_players)

unresolved = (
    sb.table("contract_import_staging")
    .select("id, row_num, player_name, player_position, match_reason, match_candidates")
    .eq("league_id", league_id)
    .eq("uploaded_by", u["id"])
    .eq("match_status", "unresolved")
    .order("row_num")
    .execute()
    .data
    or []
)

deduped_unresolved = []
seen_keys = set()

for r in unresolved:
    key = (
        str(r.get("player_name") or "").strip().lower(),
        str(r.get("player_position") or "").strip().upper(),
        int(r.get("row_num") or 0),
    )

    if key in seen_keys:
        continue

    seen_keys.add(key)
    deduped_unresolved.append(r)

unresolved = deduped_unresolved

still_unresolved = []
auto_updates = 0

for row in unresolved:
    if is_bad_player_row(row):
        sb.table("contract_import_staging").delete().eq("id", row["id"]).execute()
        continue

    csv_name = row.get("player_name") or ""
    csv_pos = (row.get("player_position") or "").upper() or None

    fresh_candidates = find_candidates(
        sleeper_players,
        csv_name,
        csv_pos,
    )

    if len(fresh_candidates) == 1:
        sb.table("contract_import_staging").update(
            {
                "resolved_sleeper_player_id": fresh_candidates[0]["id"],
                "match_status": "matched_auto_review",
                "match_reason": "FUZZY_AUTO_MATCH",
            }
        ).eq("id", row["id"]).execute()

        auto_updates += 1
        continue

    row["fresh_candidates"] = fresh_candidates
    still_unresolved.append(row)

loading_slot.empty()

if auto_updates > 0:
    st.success(f"Auto-resolved {auto_updates} player(s).")
    st.rerun()

if "review_index" not in st.session_state:
    st.session_state["review_index"] = 0

if st.session_state["review_index"] >= len(still_unresolved):
    st.session_state["review_index"] = max(0, len(still_unresolved) - 1)

if not still_unresolved:
    with st.container(border=True):
        st.markdown('<span class="status-pill">Complete</span>', unsafe_allow_html=True)
        st.markdown('<div class="review-title">All player matches are resolved</div>', unsafe_allow_html=True)
        st.markdown('<p class="review-copy">Your contracts are ready for final review.</p>', unsafe_allow_html=True)

    if st.button("Continue to Final Review", type="primary", use_container_width=True):
        st.switch_page("pages/04_Commit_Contracts.py")

    st.stop()

total_remaining = len(still_unresolved)
current_index = st.session_state["review_index"]
current_row = still_unresolved[current_index]

top_left, top_right = st.columns([2, 1], gap="large")

with top_left:
    with st.container(border=True):
        st.markdown('<span class="status-pill">Needs Review</span>', unsafe_allow_html=True)
        st.markdown(
            f'<div class="review-title">{total_remaining} player(s) need confirmation</div>',
            unsafe_allow_html=True,
        )
        st.markdown(
            f'<p class="review-copy">Reviewing player {current_index + 1} of {total_remaining}. Save this match to move to the next player.</p>',
            unsafe_allow_html=True,
        )

        progress_value = (current_index + 1) / total_remaining
        st.progress(progress_value)

with top_right:
    buf = io.StringIO()
    w = csv.DictWriter(
        buf,
        fieldnames=[
            "full_name",
            "search_name",
            "position",
            "team",
            "status",
            "is_active",
            "sleeper_player_id",
        ],
    )
    w.writeheader()

    for p in sleeper_players:
        w.writerow(
            {
                "full_name": p.get("full_name"),
                "search_name": p.get("search_name"),
                "position": p.get("position"),
                "team": p.get("team"),
                "status": p.get("status"),
                "is_active": p.get("is_active"),
                "sleeper_player_id": p.get("sleeper_player_id"),
            }
        )

    st.write("")
    st.write("")
    st.download_button(
        "Download Sleeper ID Reference",
        data=buf.getvalue().encode("utf-8"),
        file_name="sleeper_players.csv",
        mime="text/csv",
        use_container_width=True,
    )

st.markdown(
    """
    <div class="hint-box">
        Best path: choose a suggested match first. If the name is misspelled, use the search box. The page now saves one player at a time so it does not reload every unresolved player after each choice.
    </div>
    """,
    unsafe_allow_html=True,
)

rid = current_row["id"]
csv_name = current_row.get("player_name") or ""
csv_pos = (current_row.get("player_position") or "").upper() or None
reason = current_row.get("match_reason") or "UNKNOWN"
reason_text = REASON_TEXT.get(reason, reason)
fresh_candidates = current_row.get("fresh_candidates") or []

with st.container(border=True):
    header_cols = st.columns([2, 1, 1])

    with header_cols[0]:
        st.markdown(f"### {csv_name}")

    with header_cols[1]:
        st.metric("CSV Position", csv_pos or "—")

    with header_cols[2]:
        st.caption(f"Row {current_row['row_num']}")

    st.markdown(f'<div class="reason-box">{reason_text}</div>', unsafe_allow_html=True)

    suggested_labels = [c["label"] for c in fresh_candidates]
    suggested_label_to_id = {c["label"]: c["id"] for c in fresh_candidates}

    with st.form(f"review_form_{rid}", clear_on_submit=False):
        if fresh_candidates:
            picked = st.selectbox(
                "Suggested matches",
                options=["-- choose suggested match --"] + suggested_labels,
                key=f"pick_{rid}",
            )
        else:
            picked = "-- choose suggested match --"
            st.caption("No strong suggested matches found. Use full Sleeper search below.")

        search_term = st.text_input(
            "Search full Sleeper player database",
            key=f"search_text_{rid}",
            placeholder="Type player name if suggested match is missing",
        )

        searched = "-- choose search result --"

        if search_term.strip():
            matches = get_search_matches(search_term, all_player_options)

            if matches:
                searched = st.selectbox(
                    "Search results",
                    options=["-- choose search result --"] + matches,
                    key=f"search_{rid}",
                )
            else:
                st.caption("No search results found. Try a shorter name or use the manual Sleeper ID fallback.")

        manual_value = st.text_input(
            "Manual Sleeper Player ID fallback",
            key=f"manual_{rid}",
            placeholder="Example: 4984",
        ).strip()

        nav_cols = st.columns([1, 1, 2], gap="medium")

        with nav_cols[0]:
            previous_clicked = st.form_submit_button(
                "← Previous",
                use_container_width=True,
            )

        with nav_cols[1]:
            skip_clicked = st.form_submit_button(
                "Skip for now",
                use_container_width=True,
            )

        with nav_cols[2]:
            save_clicked = st.form_submit_button(
                "Save Match + Next Player",
                type="primary",
                use_container_width=True,
            )

    if previous_clicked:
        st.session_state["review_index"] = max(0, current_index - 1)
        st.rerun()

    if skip_clicked:
        st.session_state["review_index"] = min(total_remaining - 1, current_index + 1)
        st.rerun()

    if save_clicked:
        resolved_id = None

        if manual_value:
            resolved_id = manual_value
        elif searched != "-- choose search result --":
            resolved_id = all_player_label_to_id.get(searched)
        elif picked != "-- choose suggested match --":
            resolved_id = suggested_label_to_id.get(picked)

        if not resolved_id:
            st.error("Choose a suggested match, search result, or enter a manual Sleeper Player ID.")
            st.stop()

        save_match(rid, resolved_id)
        clear_review_state(rid)

        st.success(f"Saved match for {csv_name}.")

        remaining_check = (
            sb.table("contract_import_staging")
            .select("id", count="exact")
            .eq("league_id", league_id)
            .eq("uploaded_by", u["id"])
            .eq("match_status", "unresolved")
            .execute()
            .count
            or 0
        )

        if remaining_check <= 0:
            st.switch_page("pages/04_Commit_Contracts.py")

        st.session_state["review_index"] = min(current_index, max(0, remaining_check - 1))
        st.rerun()

st.divider()

bottom_left, bottom_right = st.columns([1, 1], gap="medium")

with bottom_left:
    if st.button("Refresh unresolved list", use_container_width=True):
        st.rerun()

with bottom_right:
    if st.button("Continue to Final Review if Complete", type="primary", use_container_width=True):
        remaining_check = (
            sb.table("contract_import_staging")
            .select("id", count="exact")
            .eq("league_id", league_id)
            .eq("uploaded_by", u["id"])
            .eq("match_status", "unresolved")
            .execute()
            .count
            or 0
        )

        if remaining_check > 0:
            st.warning(f"{remaining_check} player(s) still need confirmation.")
            st.stop()

        st.switch_page("pages/04_Commit_Contracts.py")