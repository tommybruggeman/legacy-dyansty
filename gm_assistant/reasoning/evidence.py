from __future__ import annotations

from gm_assistant.reasoning.models import QuestionAnalysis, EvidenceBundle


def collect_evidence(
    analysis: QuestionAnalysis,
    owner_team_name: str,
    *,
    answer_asset_question=None,
    answer_team_strategy=None,
) -> EvidenceBundle:
    """
    Temporary bridge between V2 reasoning and the old evaluators.

    V2 decides what evidence it needs.
    Old engines still provide the actual player/fact data.
    """
    bundle = EvidenceBundle()

    if analysis.needs_player_lookup and analysis.player_name:
        from auth import service_client

        sb = service_client()

        rows = (
            sb.table("player_brain_context")
            .select("*")
            .ilike("player_name", analysis.player_name)
            .eq("current_owner", owner_team_name)
            .limit(1)
            .execute()
            .data
            or []
        )

        if not rows:
            rows = (
                sb.table("player_brain_context")
                .select("*")
                .ilike("player_name", f"%{analysis.player_name}%")
                .eq("current_owner", owner_team_name)
                .limit(1)
                .execute()
                .data
                or []
            )

        if rows:
            bundle.player = rows[0]
            bundle.facts.append({
                "kind": "player_brain_context",
                "importance": 1.0,
                "text": rows[0].get("brain_summary", ""),
                "data": rows[0],
            })

    if analysis.intent == "free_agent_targets":
        from auth import service_client

        sb = service_client()

        rows = (
            sb.table("player_brain_context")
            .select("*")
            .is_("current_owner", "null")
            .order("brain_score", desc=True)
            .limit(40)
            .execute()
            .data
            or []
        )

        retired_names = {
            "Tom Brady", "Drew Brees", "Matt Ryan", "Steve Smith", "Tiki Barber",
            "Calvin Johnson", "Arian Foster", "Anquan Boldin", "Larry Fitzgerald",
            "Brandon Marshall", "Wes Welker", "Jordy Nelson", "Demaryius Thomas",
            "Marshawn Lynch", "Dion Lewis", "Doug Baldwin", "Adrian Peterson",
            "C.J. Spiller", "Steven Jackson", "Reggie Wayne", "Eli Manning",
            "Jason Witten",
        }

        seen = set()
        clean = []
        for r in rows:
            name = r.get("player_name")
            sid = r.get("sleeper_id") or name

            if name in retired_names:
                continue

            if sid in seen:
                continue
            seen.add(sid)

            if str(r.get("pos") or "") not in {"QB", "RB", "WR", "TE"}:
                continue

            if str(r.get("market_pool") or "").upper() not in {"FA", "FREE_AGENT", "WAIVERS", "FA_AUCTION", ""}:
                continue

            # Avoid old historical profiles masquerading as FA value.
            if float(r.get("future_score") or 0) <= 33 and float(r.get("situation_score") or 0) == 0:
                continue

            clean.append(r)

        rows = clean[:8]

        bundle.facts = [
            {
                "kind": "free_agent_target",
                "importance": 0.86,
                "text": r.get("brain_summary") or "",
                "data": r,
            }
            for r in rows
        ]

    if analysis.intent == "trade_partner_search":
        from auth import service_client

        sb = service_client()

        my_team = (
            sb.table("team_brain_context")
            .select("*")
            .eq("owner_team_name", owner_team_name)
            .limit(1)
            .execute()
            .data
            or []
        )

        teams = (
            sb.table("team_brain_context")
            .select("*")
            .neq("owner_team_name", owner_team_name)
            .execute()
            .data
            or []
        )

        players = (
            sb.table("player_brain_context")
            .select("player_name,pos,current_owner,salary,years,brain_score,present_score,contract_score")
            .neq("current_owner", owner_team_name)
            .execute()
            .data
            or []
        )

        candidates = []
        for t in teams:
            owner = t.get("owner_team_name")
            roster = [p for p in players if p.get("current_owner") == owner]

            rb_targets = sorted(
                [p for p in roster if p.get("pos") == "RB"],
                key=lambda x: float(x.get("present_score") or x.get("brain_score") or 0),
                reverse=True,
            )[:3]

            te_targets = sorted(
                [p for p in roster if p.get("pos") == "TE"],
                key=lambda x: float(x.get("present_score") or x.get("brain_score") or 0),
                reverse=True,
            )[:3]

            qb_need = float(t.get("qb_score") or 0) < 45
            wr_need = float(t.get("wr_score") or 0) < 45
            rb_surplus = float(t.get("rb_score") or 0) >= 45
            te_surplus = float(t.get("te_score") or 0) >= 45

            fit = 0
            if qb_need:
                fit += 18
            if wr_need:
                fit += 12
            if rb_surplus:
                fit += 18
            if te_surplus:
                fit += 14
            fit += min(float(t.get("rb_score") or 0), 70) * 0.15
            fit += min(float(t.get("te_score") or 0), 70) * 0.12

            candidates.append({
                "team": owner,
                "fit_score": round(fit, 2),
                "qb_score": t.get("qb_score"),
                "rb_score": t.get("rb_score"),
                "wr_score": t.get("wr_score"),
                "te_score": t.get("te_score"),
                "team_window": t.get("team_window"),
                "need_positions": t.get("need_positions") or [],
                "surplus_positions": t.get("surplus_positions") or [],
                "rb_targets": rb_targets,
                "te_targets": te_targets,
            })

        candidates = sorted(candidates, key=lambda x: x["fit_score"], reverse=True)[:6]

        bundle.facts = [
            {
                "kind": "trade_partner_fit",
                "importance": 0.9,
                "text": f"{c['team']} trade fit {c['fit_score']}",
                "data": c,
            }
            for c in candidates
        ]

    if analysis.intent == "win_now_player_ranking":
        from auth import service_client

        sb = service_client()

        rows = (
            sb.table("player_brain_context")
            .select("*")
            .eq("current_owner", owner_team_name)
            .execute()
            .data
            or []
        )

        ranked = []
        for r in rows:
            present = float(r.get("present_score") or 0)
            role = float(r.get("role_score") or 0)
            situation = float(r.get("situation_score") or 0)
            risk = float(r.get("risk_score") or 0)
            contract = float(r.get("contract_score") or 0)

            win_now_score = round(
                present * 0.45
                + role * 0.25
                + situation * 0.15
                + contract * 0.05
                - risk * 0.10,
                2,
            )

            ranked.append({
                **r,
                "win_now_rank_score": win_now_score,
            })

        wants_worst = any(w in analysis.raw_question.lower() for w in ["worst", "least", "bad"])
        ranked = sorted(
            ranked,
            key=lambda x: x["win_now_rank_score"],
            reverse=not wants_worst,
        )[:8]

        bundle.facts = [
            {
                "kind": "win_now_player_rank",
                "importance": 0.9,
                "text": (
                    f"{r.get('player_name')} ({r.get('pos')}) win-now score {r.get('win_now_rank_score')}: "
                    f"present {float(r.get('present_score') or 0):.1f}, role {float(r.get('role_score') or 0):.1f}, "
                    f"situation {float(r.get('situation_score') or 0):.1f}, risk {float(r.get('risk_score') or 0):.1f}, "
                    f"${float(r.get('salary') or 0):.0f}/{float(r.get('years') or 0):.0f} yrs."
                ),
                "data": r,
            }
            for r in ranked
        ]

    if analysis.intent == "contract_audit":
        from gm_assistant.evidence.builders import calculate_points_per_dollar

        result = calculate_points_per_dollar(
            question=analysis.raw_question,
            owner_team_name=owner_team_name,
        )

        bad_contracts = []
        for r in result.data or []:
            salary = float(r.get("salary") or 0)
            years = float(r.get("years") or 0)
            ppg = float(r.get("ppg") or 0)
            contract = float(r.get("contract_score") or 0)

            liability = 0
            if salary >= 35:
                liability += 35
            elif salary >= 25:
                liability += 25
            elif salary >= 15:
                liability += 14
            elif salary >= 8:
                liability += 8

            if years >= 3:
                liability += 20
            elif years >= 2:
                liability += 10

            if contract <= 25:
                liability += 25
            elif contract <= 45:
                liability += 12
            elif contract >= 80:
                liability -= 25
            elif contract >= 65:
                liability -= 12

            if ppg < 8:
                liability += 15
            elif ppg < 11:
                liability += 8
            elif ppg >= 20:
                liability -= 25
            elif ppg >= 16:
                liability -= 12

            if salary <= 3 and years <= 1:
                liability -= 25

            liability = max(0, round(liability))
            if liability <= 0:
                continue

            if salary <= 3 and years <= 1:
                action = "churn candidate, but not a harmful contract"
            elif salary >= 20 and years >= 2:
                action = "shop first, do not cut unless dead-cap math works"
            elif contract <= 35:
                action = "market-check or restructure"
            else:
                action = "monitor"

            bad_contracts.append({
                "kind": "roster_liability",
                "importance": 0.88,
                "text": (
                    f"{r.get('player')} is a roster pressure point: "
                    f"${salary:.0f}/{years:.0f} yrs, contract {contract:.1f}, "
                    f"PPG {ppg:.1f}; suggested action: {action}."
                ),
                "data": {
                    "player": r.get("player"),
                    "pos": r.get("pos"),
                    "salary": salary,
                    "years": years,
                    "contract": contract,
                    "ppg": ppg,
                    "liability": liability,
                    "action": action,
                },
            })

        bundle.facts = sorted(
            bad_contracts,
            key=lambda f: f["data"]["liability"],
            reverse=True,
        )[:5]

    return bundle
