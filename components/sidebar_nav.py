import streamlit as st
from pathlib import Path

ICON = Path(__file__).resolve().parents[1] / "assets" / "page_icon.png"

NAV_CSS = """
<style>
[data-testid="stSidebarNav"] {
    display: none;
}

[data-testid="stSidebar"] {
    background-color: #163728 !important;
}

.nav-title {
    font-size: 0.8rem;
    text-transform: uppercase;
    letter-spacing: .08em;
    color: #C89B4A;
    margin: 0.5rem 0 1rem 0;
    font-weight: 700;
    text-align: center;
}

[data-testid="stSidebar"] a[aria-current="page"] {
    background-color: #263F35 !important;
    color: #F5EBD7 !important;
    border-radius: 8px !important;
}

[data-testid="stSidebar"] a[aria-current="page"]:hover {
    background-color: #263F35 !important;
}
</style>
"""

def render_nav():
    st.markdown(NAV_CSS, unsafe_allow_html=True)

    with st.sidebar:
        if ICON.exists():
            st.image(str(ICON), width=82)

        st.markdown('<div class="nav-title">Legacy</div>', unsafe_allow_html=True)

        st.page_link("pages/01_Season_Standings.py", label="Season Standings")
        st.page_link("pages/02_My_Team.py", label="My Team")
        st.page_link("pages/02_Weekly_Matchups.py", label="Weekly Matchups")
        st.page_link("pages/03_Teams.py", label="Teams")
        st.page_link("pages/90_Settings.py", label="Settings")