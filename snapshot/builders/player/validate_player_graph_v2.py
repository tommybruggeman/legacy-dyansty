from __future__ import annotations

from collections import Counter, defaultdict
from auth import service_client


TARGET_TABLE = "player_graph_v2_validation"


def _num(v, default=0.0):
    try:
        if v is None:
            return default
        return float(v)
    except Exception:
        return default


def _flag(row: dict, code: str, severity: str, message: str):
    return {
        "canonical_player_id": row.get("canonical_player_id"),
        "player_name": row.get("player_name"),
        "pos": row.get("pos"),
        "owner_team_name": row.get("owner_team_name"),
        "severity": severity,
        "code": code,
        "message": message,
        "snapshot": {
            "is_rostered": row.get("is_rostered"),
            "is_free_agent": row.get("is_free_agent"),
            "is_active": row.get("is_active"),
            "is_retired": row.get("is_retired"),
            "is_rookie": row.get("is_rookie"),
            "availability_status": row.get("availability_status"),
            "expected_ppg": row.get("expected_ppg"),
            "dynasty_asset_score": row.get("dynasty_asset_score"),
            "contract_efficiency_score": row.get("contract_efficiency_score"),
            "salary": row.get("salary"),
            "years": row.get("years"),
            "sleeper_id": row.get("sleeper_id"),
            "gsis_id": row.get("gsis_id"),
            "nfl_team": row.get("nfl_team"),
            "player_flags": row.get("player_flags"),
        },
    }


def validate_player_graph_v2():
    sb = service_client()

    rows = sb.table("player_graph_v2").select("*").execute().data or []
    issues = []

    name_pos = defaultdict(list)
    sleeper_ids = defaultdict(list)
    gsis_ids = defaultdict(list)

    for r in rows:
        name = str(r.get("search_name") or "").strip()
        pos = r.get("pos")
        if name and pos:
            name_pos[(name, pos)].append(r)

        if r.get("sleeper_id"):
            sleeper_ids[str(r.get("sleeper_id"))].append(r)

        if r.get("gsis_id"):
            gsis_ids[str(r.get("gsis_id"))].append(r)

    for r in rows:
        name = r.get("player_name")
        ppg = _num(r.get("expected_ppg"))
        dynasty = _num(r.get("dynasty_asset_score"))
        contract = _num(r.get("contract_efficiency_score"))
        salary = _num(r.get("salary"))
        years = _num(r.get("years"))

        is_rostered = bool(r.get("is_rostered"))
        is_free_agent = bool(r.get("is_free_agent"))
        is_active = bool(r.get("is_active"))
        is_retired = bool(r.get("is_retired"))
        owner = r.get("owner_team_name")
        nfl_team = r.get("nfl_team")

        if is_rostered and not owner:
            issues.append(_flag(
                r,
                "ROSTERED_WITHOUT_OWNER",
                "error",
                f"{name} is marked rostered but has no owner_team_name.",
            ))

        if owner and is_free_agent:
            issues.append(_flag(
                r,
                "OWNER_AND_FREE_AGENT",
                "error",
                f"{name} has an owner but is also marked free agent.",
            ))

        if is_retired and ppg > 0:
            issues.append(_flag(
                r,
                "RETIRED_WITH_PROJECTION",
                "error",
                f"{name} is retired/inactive but has expected PPG {ppg}.",
            ))

        if not is_active and ppg > 8:
            issues.append(_flag(
                r,
                "INACTIVE_WITH_PROJECTION",
                "warning",
                f"{name} is inactive but has expected PPG {ppg}.",
            ))

        if ppg >= 12 and dynasty <= 5:
            issues.append(_flag(
                r,
                "PROJECTION_WITHOUT_DYNASTY",
                "warning",
                f"{name} has strong expected PPG {ppg} but dynasty score {dynasty}. Possible historical/stale production leak.",
            ))

        if is_free_agent and dynasty >= 45:
            issues.append(_flag(
                r,
                "HIGH_VALUE_FREE_AGENT",
                "warning",
                f"{name} is marked free agent with dynasty score {dynasty}. Check ownership sync.",
            ))

        if is_free_agent and ppg >= 12:
            issues.append(_flag(
                r,
                "HIGH_PPG_FREE_AGENT",
                "warning",
                f"{name} is marked free agent with expected PPG {ppg}. Check ownership/availability.",
            ))

        if salary > 0 and years <= 0:
            issues.append(_flag(
                r,
                "SALARY_WITHOUT_YEARS",
                "warning",
                f"{name} has salary ${salary:g} but years {years:g}.",
            ))

        if salary <= 0 and years > 0:
            issues.append(_flag(
                r,
                "YEARS_WITHOUT_SALARY",
                "warning",
                f"{name} has years {years:g} but no salary.",
            ))

        if is_rostered and salary <= 0:
            issues.append(_flag(
                r,
                "ROSTERED_WITHOUT_CONTRACT",
                "info",
                f"{name} is rostered but has no salary. Could be missing contract import or roster-only player.",
            ))

        if ppg > 20 and r.get("pos") not in {"QB"}:
            issues.append(_flag(
                r,
                "NON_QB_EXTREME_PROJECTION",
                "warning",
                f"{name} has non-QB expected PPG {ppg}. Check projection source.",
            ))

        if not nfl_team and is_active and ppg > 5:
            issues.append(_flag(
                r,
                "ACTIVE_PROJECTION_WITHOUT_NFL_TEAM",
                "info",
                f"{name} has projection but no NFL team.",
            ))

        if contract <= 0 and salary > 0 and ppg > 5:
            issues.append(_flag(
                r,
                "MISSING_CONTRACT_EFFICIENCY",
                "info",
                f"{name} has salary and projection but no contract efficiency score.",
            ))

    for key, matches in name_pos.items():
        if len(matches) > 1:
            display = ", ".join(
                f"{m.get('player_name')}[{m.get('canonical_player_id')}]"
                for m in matches[:5]
            )
            issues.append({
                "canonical_player_id": f"{key[0]}|{key[1]}",
                "player_name": key[0],
                "pos": key[1],
                "owner_team_name": None,
                "severity": "info",
                "code": "DUPLICATE_NAME_POS",
                "message": f"Multiple rows share name/pos: {display}",
                "snapshot": {"count": len(matches)},
            })

    for sid, matches in sleeper_ids.items():
        if len(matches) > 1:
            issues.append({
                "canonical_player_id": sid,
                "player_name": matches[0].get("player_name"),
                "pos": matches[0].get("pos"),
                "owner_team_name": matches[0].get("owner_team_name"),
                "severity": "warning",
                "code": "DUPLICATE_SLEEPER_ID",
                "message": f"Sleeper ID {sid} appears on {len(matches)} graph rows.",
                "snapshot": {
                    "players": [
                        {
                            "player": m.get("player_name"),
                            "pos": m.get("pos"),
                            "canonical_player_id": m.get("canonical_player_id"),
                        }
                        for m in matches
                    ]
                },
            })

    for gid, matches in gsis_ids.items():
        if len(matches) > 1:
            issues.append({
                "canonical_player_id": gid,
                "player_name": matches[0].get("player_name"),
                "pos": matches[0].get("pos"),
                "owner_team_name": matches[0].get("owner_team_name"),
                "severity": "warning",
                "code": "DUPLICATE_GSIS_ID",
                "message": f"GSIS ID {gid} appears on {len(matches)} graph rows.",
                "snapshot": {
                    "players": [
                        {
                            "player": m.get("player_name"),
                            "pos": m.get("pos"),
                            "canonical_player_id": m.get("canonical_player_id"),
                        }
                        for m in matches
                    ]
                },
            })

    if issues:
        sb.table(TARGET_TABLE).delete().neq("canonical_player_id", "__never__").execute()
        sb.table(TARGET_TABLE).upsert(
            issues,
            on_conflict="canonical_player_id,code",
        ).execute()
    else:
        try:
            sb.table(TARGET_TABLE).delete().neq("canonical_player_id", "__never__").execute()
        except Exception:
            pass

    counts = Counter(i["severity"] for i in issues)
    codes = Counter(i["code"] for i in issues)

    print("\nPLAYER GRAPH V2 HEALTH")
    print("=" * 60)
    print(f"Players checked: {len(rows)}")
    print(f"Issues: {len(issues)}")
    print(f"Errors: {counts.get('error', 0)}")
    print(f"Warnings: {counts.get('warning', 0)}")
    print(f"Info: {counts.get('info', 0)}")
    print("\nTop issue codes:")
    for code, count in codes.most_common(12):
        print(f"- {code}: {count}")

    print("\nHighest priority examples:")
    severity_order = {"error": 0, "warning": 1, "info": 2}
    for issue in sorted(issues, key=lambda x: (severity_order.get(x["severity"], 9), x["code"]))[:20]:
        print(f"[{issue['severity'].upper()}] {issue['code']}: {issue['message']}")

    return issues


if __name__ == "__main__":
    validate_player_graph_v2()
