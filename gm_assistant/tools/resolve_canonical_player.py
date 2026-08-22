
def resolve_canonical_player(player_name: str):
    if not player_name:
        return None

    from auth import service_client
    sb = service_client()

    # 0. INITIAL MATCH (FAST PATH)
    def initial_form(n):
        parts = n.split()
        if len(parts) >= 2:
            return (parts[0][0] + ". " + parts[-1]).lower()
        return None

    initial_lookup = {
        "d. achane": "9226",
        "j. conner": "8138",
    }

    key = initial_form(player_name)
    if key and key in initial_lookup:
        return initial_lookup[key]

    # 1. canonical seeds
    canon = sb.table("player_identity_context")         .select("canonical_player_id,player_name")         .execute().data or []

    best = None
    best_score = 0

    def normalize(x):
        import re
        x = str(x).lower()
        x = x.replace("'", "")
        x = x.replace(".", "")
        x = re.sub(r"[^a-z0-9\s]", "", x)
        x = re.sub(r"\s+", " ", x).strip()
        return x

    def score(a, b):
        a = normalize(a)
        b = normalize(b)

        a_tokens = set(a.split())
        b_tokens = set(b.split())

        return len(a_tokens & b_tokens) / max(len(a_tokens), 1)

    for c in canon:
        s = score(player_name, c["player_name"])
        if s > best_score:
            best_score = s
            best = c["canonical_player_id"]

    if best_score >= 0.45:
        return best

    # 2. alias fallback
    aliases = sb.table("player_identity_aliases")         .select("canonical_player_id,alias")         .execute().data or []

    for a in aliases:
        if a["alias"].lower() == player_name.lower():
            return a["canonical_player_id"]

    return None
