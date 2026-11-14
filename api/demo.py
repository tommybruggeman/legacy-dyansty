"""Apple's App Review login: it sees a real team, but can't change anything.

The demo login is linked to a real team (scripts/app_review_login.py) so every screen has data.
Without this guard a reviewer could set that owner's lineup, bid, trade or drop players.
Asking the GM, saving targets and phone alerts stay on, since they only touch the demo login's own rows.
"""
from __future__ import annotations

from typing import Any

EMAIL = "applereview@legacydynasty.app"
MESSAGE = "This is a demo account, so changes are turned off."

# Writes the demo login may still make: they only touch its own rows.
_ALLOWED_PREFIXES = (
    "/front-office/",
    "/market/targets/",
    "/me/push-token",
    "/me/live-activity",
    "/me/alerts",
    "/me/crash",
)


def is_demo(user: Any) -> bool:
    return str(getattr(user, "email", "") or "").strip().lower() == EMAIL


def blocked(user: Any, method: str, path: str) -> bool:
    """True when the demo login tries a change it isn't allowed to make."""
    if method.upper() in {"GET", "HEAD", "OPTIONS"} or not is_demo(user):
        return False
    return not path.startswith(_ALLOWED_PREFIXES)
