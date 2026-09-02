import streamlit as st
from pathlib import Path


ICON = Path(__file__).resolve().parents[1] / "assets" / "page_icon.png"


NAV_CSS = """
<style>

/* =========================================================
   Shared sidebar styling
========================================================= */

.nav-title {
    margin-top: 10px;
    margin-bottom: 18px;

    color: #E2BC5B;

    font-size: .72rem;
    font-weight: 900;

    letter-spacing: .14em;

    text-transform: uppercase;
}


/* =========================================================
   Preserve Streamlit's native sidebar reopen control
========================================================= */

[data-testid="stHeader"] {
    display: block !important;
    visibility: visible !important;
    opacity: 1 !important;
    background: transparent !important;
}

[data-testid="stSidebarCollapsedControl"],
[data-testid="collapsedControl"] {
    display: flex !important;
    visibility: visible !important;
    opacity: 1 !important;
    pointer-events: auto !important;
    z-index: 999999 !important;
}


/* =========================================================
   Mobile fallback navigation
========================================================= */

.st-key-mobile_nav {
    display: none;
}


@media (max-width: 700px) {

    /*
    Streamlit's native collapsed-sidebar arrow is not
    consistently rendered on mobile Safari.

    This permanent button guarantees that navigation
    is always available from every page.
    */

    .st-key-mobile_nav {
        display: block !important;

        position: fixed !important;

        top: .65rem !important;
        left: .65rem !important;

        width: 42px !important;

        z-index: 9999999 !important;
    }


    .st-key-mobile_nav button {
        width: 42px !important;
        min-width: 42px !important;
        height: 42px !important;

        padding: 0 !important;

        border-radius: 12px !important;

        border:
            1px solid rgba(226,188,91,.45) !important;

        background:
            radial-gradient(
                120% 140% at 20% 0%,
                #101E1D 0%,
                #0C1917 100%
            ) !important;

        color: #FFF5E7 !important;

        font-size: 1.1rem !important;
        font-weight: 900 !important;

        box-shadow:
            0 2px 10px rgba(0,0,0,.30) !important;
    }


    .st-key-mobile_nav button:hover {
        border-color:
            rgba(226,188,91,.75) !important;
    }


    /*
    Popover containing navigation links.
    */

    div[data-testid="stPopoverBody"] {
        background: #0C1917 !important;

        border:
            1px solid rgba(226,188,91,.35) !important;

        border-radius: 14px !important;

        box-shadow:
            0 8px 30px rgba(0,0,0,.45) !important;
    }


    div[data-testid="stPopoverBody"]
    a[data-testid="stPageLink-NavLink"] {
        min-height: 42px !important;

        display: flex !important;
        align-items: center !important;

        border-radius: 8px !important;
    }
}


@media (min-width: 701px) {

    .st-key-mobile_nav {
        display: none !important;
    }
}

</style>
"""


def _render_page_links():

    st.page_link(
        "pages/01_Season_Standings.py",
        label="Season Standings",
    )

    st.page_link(
        "pages/02_My_Team.py",
        label="My Team",
    )

    st.page_link(
        "pages/04_Free_Agent.py",
        label="Free Agent",
    )

    st.page_link(
        "pages/02_Weekly_Matchups.py",
        label="Weekly Matchups",
    )

    st.page_link(
        "pages/03_Teams.py",
        label="Teams",
    )

    st.page_link(
        "pages/90_Settings.py",
        label="Settings",
    )


def render_nav():

    st.markdown(
        NAV_CSS,
        unsafe_allow_html=True,
    )


    # ========================================================
    # Normal desktop / Streamlit sidebar
    # ========================================================

    with st.sidebar:

        if ICON.exists():
            st.image(
                str(ICON),
                width=82,
            )

        st.markdown(
            '<div class="nav-title">Legacy</div>',
            unsafe_allow_html=True,
        )

        _render_page_links()


    # ========================================================
    # Permanent mobile navigation fallback
    # ========================================================

    with st.container(
        key="mobile_nav",
    ):

        with st.popover(
            "☰",
            use_container_width=False,
        ):

            if ICON.exists():
                st.image(
                    str(ICON),
                    width=58,
                )

            st.markdown(
                '<div class="nav-title">Legacy</div>',
                unsafe_allow_html=True,
            )

            _render_page_links()