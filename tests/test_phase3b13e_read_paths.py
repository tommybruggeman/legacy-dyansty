from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ACTIVE = [
    "services/app_context.py", "services/free_agents.py", "services/publication_context.py",
    "gm_assistant/request_context.py", "gm_assistant/data.py", "gm_assistant/repositories/cap.py",
    "gm_assistant/coach_brain.py", "pages/02_My_Team.py", "pages/03_Teams.py",
    "pages/04_Free_Agent.py", "pages/_82_Trades.py", "pages/90_Settings.py",
]


def source(path):
    return (ROOT / path).read_text()


def test_active_paths_have_no_fixed_authority_year_or_legacy_cap_view():
    joined = "\n".join(source(path) for path in ACTIVE)
    assert "v_team_caps" not in joined
    assert "2025" not in joined
    assert "2026" not in joined


def test_context_generation_is_in_cache_and_ai_paths():
    assert "context_generation" in source("services/app_context.py")
    assert "context_generation" in source("pages/04_Free_Agent.py")
    assert "context_generation" in source("gm_assistant/request_context.py")
    assert "assistant_context.context_generation" in source("gm_assistant/data.py")


def test_published_authorities_are_explicit():
    publication = source("services/publication_context.py")
    assert "rollover_target_cap_authority_publications" in publication
    assert "rollover_target_market_visibility_rows" in publication
    assert "publication_context_generations" in publication
    assert "SeasonResolver" in source("pages/02_My_Team.py")
    assert "SeasonResolver" in source("pages/_82_Trades.py")
    assert "SeasonResolver" in source("pages/90_Settings.py")
