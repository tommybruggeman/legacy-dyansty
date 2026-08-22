import re

PICK_WORDS = {
    "first pick": "1.01",
    "second pick": "1.02",
    "third pick": "1.03",
    "fourth pick": "1.04",
    "fifth pick": "1.05",
}

def extract_entities(question: str, default_team: str | None = None) -> dict:
    q = question.lower()

    entities = {
        "team": default_team,
        "pick": None,
        "player": None,
        "position": None,
    }

    pick_match = re.search(r"\b1\.(0[1-9]|10|11|12)\b", q)
    if pick_match:
        entities["pick"] = pick_match.group(0)

    for phrase, pick in PICK_WORDS.items():
        if phrase in q:
            entities["pick"] = pick

    for pos in ["qb", "rb", "wr", "te"]:
        if re.search(rf"\b{pos}\b", q):
            entities["position"] = pos.upper()

    return entities
