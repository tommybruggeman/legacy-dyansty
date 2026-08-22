from __future__ import annotations

from pprint import pprint

from gm_assistant.identity.evidence_health import player_evidence_health


WATCHLIST = [
    "Jeremy McNichols",
    "Garrett Wilson",
    "Brandon Aiyuk",
    "Matthew Golden",
    "Kenny Gainwell",
    "Evan Engram",
    "Emanuel Wilson",
]


def main():
    for name in WATCHLIST:
        print("\\n" + "=" * 100)
        print(name)
        pprint(player_evidence_health(name))


if __name__ == "__main__":
    main()
