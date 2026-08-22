from __future__ import annotations

import os


def optional_streamlit_secret(name: str) -> str:
    """Return an optional Streamlit secret without requiring secrets.toml."""
    try:
        import streamlit as st

        return str(st.secrets.get(name, "")).strip()
    except Exception:
        return ""


def configured_value(name: str) -> str:
    """Prefer the process/root-dotenv value, then optional Streamlit secrets."""
    return os.getenv(name, "").strip() or optional_streamlit_secret(name)
