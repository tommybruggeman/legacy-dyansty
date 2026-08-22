from __future__ import annotations

from gm_assistant.reasoning.models import BrainState, QuestionAnalysis, EvidenceBundle, BrainDecision


def make_decision(
    analysis: QuestionAnalysis,
    state: BrainState,
    evidence: EvidenceBundle | None = None,
) -> BrainDecision:
    evidence = evidence or EvidenceBundle()

    if analysis.intent == "change_team_goal":
        return BrainDecision(
            action="UPDATE_GOAL",
            confidence=0.99,
            thesis="Perfect. That changes how I evaluate every move from here.",
            reasons=[
                "I’m optimizing for championship odds now, not maximum long-term dynasty value.",
                "That means veterans, expensive short-term production, and pick consolidation are on the table if they create a weekly lineup edge.",
                "The priority becomes turning surplus into playoff starters, not collecting abstract value.",
            ],
            caveats=[
                "I still would not make reckless moves. A win-now team should overpay only when the deal clearly improves the starting lineup."
            ],
            next_action="From here, every move should answer one question: does this make your playoff lineup harder to beat?",
        )

    if analysis.intent == "player_decision":
        p = evidence.player or {}
        name = p.get("player_name") or analysis.player_name or "that player"

        if not p:
            return BrainDecision(
                action="NEED_PLAYER_EVIDENCE",
                confidence=0.45,
                thesis=f"I would not make a call on {name} yet because I do not have the player evidence loaded.",
                reasons=[
                    "The brain should never treat missing data as bad data.",
                    "Before recommending trade, hold, or cut, I need the player contract, production, and roster context."
                ],
                next_action="Run the player lookup first, then make the decision."
            )

        pos = p.get("pos")
        salary = float(p.get("salary") or 0)
        years = float(p.get("years") or 0)
        past = float(p.get("past_score") or 0)
        present = float(p.get("present_score") or 0)
        future = float(p.get("future_score") or 0)
        situation = float(p.get("situation_score") or 0)
        contract = float(p.get("contract_score") or 0)
        dynasty = float(p.get("dynasty_score") or 0)
        market = float(p.get("market_score") or 0)
        risk = float(p.get("risk_score") or 0)
        brain = float(p.get("brain_score") or 0)
        role = p.get("team_role") or "role unclear"
        trajectory = p.get("career_trajectory") or "trajectory unclear"
        situation_grade = p.get("situation_grade") or "situation unclear"

        reasons = [
            f"Profile: {pos}, ${salary:.0f}/{years:.0f} yrs, brain {brain:.1f}, contract {contract:.1f}, market {market:.1f}.",
            f"Production/future: past {past:.1f}, present {present:.1f}, future {future:.1f}.",
            f"Context: role {role}, trajectory {trajectory}, situation {situation_grade}, risk {risk:.1f}.",
        ]

        if analysis.decision_type == "drop":
            if salary >= 10 or market >= 45 or future >= 45 or dynasty >= 45:
                return BrainDecision(
                    action="DO_NOT_DROP",
                    confidence=0.9,
                    thesis=f"I would not cut {name}.",
                    reasons=reasons + [
                        "Even if the contract is painful, there is still enough market/future value that cutting is the worst exit.",
                        "A cut turns a recoverable asset problem into dead-cap damage with no return."
                    ],
                    next_action="Shop him first, package him second, and only revisit cutting if the league gives you no market and the dead-cap math is acceptable.",
                )

            return BrainDecision(
                action="DROP_OR_CHURN",
                confidence=0.82,
                thesis=f"I would be open to cutting {name}.",
                reasons=reasons + [
                    "The player does not show enough production, future value, or market leverage to protect the spot.",
                ],
                next_action="Compare him against the best FA/waiver upside before finalizing the cut.",
            )

        if analysis.decision_type == "trade":
            if contract <= 30 and salary >= 20 and market >= 50:
                return BrainDecision(
                    action="SHOP_DO_NOT_DUMP",
                    confidence=0.88,
                    thesis=f"I would shop {name}, but I would not dump him.",
                    reasons=reasons + [
                        "The contract is inefficient, but the player still has enough name/market value to bring back something useful.",
                        "For a win-now team, the goal is not just escaping the contract — it is turning that value into a better weekly starter.",
                    ],
                    caveats=[
                        "If the best offer is a discount package or picks-only return, holding is better than selling low."
                    ],
                    next_action="Quietly ask for RB/TE or weekly lineup help back. Set the price before you shop.",
                )

            if contract <= 25 and salary >= 15:
                return BrainDecision(
                    action="MARKET_CHECK",
                    confidence=0.8,
                    thesis=f"I would market-check {name}.",
                    reasons=reasons + [
                        "The contract is creating pressure, but the return still has to improve your roster.",
                    ],
                    next_action="Test the market, but do not force the move unless it upgrades your playoff lineup.",
                )

            if future >= 55 and salary < 20:
                return BrainDecision(
                    action="HOLD",
                    confidence=0.78,
                    thesis=f"I would lean hold on {name}.",
                    reasons=reasons + [
                        "The future profile is strong enough that selling now risks moving him before the value fully shows up.",
                    ],
                    next_action="Only move him if another manager pays for the future upside.",
                )

            return BrainDecision(
                action="HOLD_OR_PRICE_CHECK",
                confidence=0.74,
                thesis=f"I would not force a trade on {name}.",
                reasons=reasons + [
                    "The move only makes sense if the return clearly improves your starting lineup or cleans up a real contract problem.",
                ],
                next_action="Set a price first, then shop only above that line.",
            )

    if analysis.intent == "contract_audit":
        facts = evidence.facts[:8]
        q = (getattr(analysis, "question", "") or "").lower()

        is_best_value = any(x in q for x in [
            "best value",
            "best contract",
            "best contracts",
            "value contract",
            "most efficient",
            "points per dollar",
            "ppd",
        ])

        if not facts:
            return BrainDecision(
                action="NEED_CONTRACT_AUDIT",
                confidence=0.6,
                thesis="I need the roster contract audit before I can rank the actual contracts.",
                reasons=[],
            )

        if is_best_value:
            ranked = []
            for f in facts:
                data = f.get("data", {})
                player = data.get("player") or data.get("player_name") or f.get("player")
                salary = float(data.get("salary") or 0)
                years = float(data.get("years") or 0)
                ppg = float(data.get("ppg") or data.get("season_ppg") or data.get("primary_ppg") or 0)
                ppd = float(data.get("points_per_dollar") or ((ppg / salary) if salary else 0))
                if player and salary > 0:
                    ranked.append((ppd, ppg, player, salary, years))

            ranked = sorted(ranked, reverse=True)[:5]

            reasons = [
                f"{player}: ${salary:.0f}/{years:.0f} yrs, {ppg:.2f} PPG, {ppd:.2f} pts/$."
                for ppd, ppg, player, salary, years in ranked
            ]

            return BrainDecision(
                action="CONTRACT_BEST_VALUE",
                confidence=0.84,
                thesis="Your best contract values are the players giving you the most usable production per dollar.",
                reasons=reasons,
                caveats=[
                    "I would not toss these contracts into deals casually unless they clearly upgrade your starting lineup."
                ],
                next_action="Protect the cheap usable producers unless they are part of a real consolidation trade.",
            )

        reasons = []
        for f in facts[:5]:
            data = f.get("data", {})
            player = data.get("player") or f.get("player")
            salary = data.get("salary")
            years = data.get("years")
            action = data.get("action")
            liability = data.get("liability")
            if player:
                reasons.append(f"{player}: ${salary:.0f}/{years:.0f} yrs, liability {liability}, recommended action: {action}.")

        return BrainDecision(
            action="CONTRACT_AUDIT",
            confidence=0.84,
            thesis="The contracts hurting you most are the ones combining real salary, multiple years, and weak weekly return.",
            reasons=reasons,
            caveats=[
                "I would separate bad contracts from cut candidates. Some bad contracts should still be shopped first."
            ],
            next_action="Start by market-checking the expensive inefficient players before cutting anyone with name value.",
        )


    if analysis.intent in {"team_overview", "team_needs", "league_context", "lineup_decision", "general_question"}:
        goal = state.team_goal or "balanced"

        if analysis.intent == "team_overview":
            if goal == "win_now":
                return BrainDecision(
                    action="WIN_NOW_TEAM_REVIEW",
                    confidence=0.78,
                    thesis="If the goal is a title, I’m judging this roster by playoff lineup strength, not long-term comfort.",
                    reasons=[
                        "The first question is whether your best weekly lineup can beat the top teams.",
                        "The second question is whether your surplus can fix the positions that actually swing playoff matchups.",
                        "For this roster, I would protect the QB foundation and use WR/QB surplus to hunt RB or TE upgrades.",
                    ],
                    caveats=[
                        "I would not call this a blind all-in. It should be a targeted contention push."
                    ],
                    next_action="The next move should be identifying which player or pick package can become a real RB/TE starter.",
                )

            return BrainDecision(
                action="TEAM_REVIEW",
                confidence=0.72,
                thesis="I’d evaluate the roster by direction first, then by the specific move available.",
                reasons=[
                    "The key is separating foundation pieces from movable leverage.",
                    "A good GM answer should not just list strengths and weaknesses. It should tell you what to do next.",
                ],
                next_action="Start with the positions where you either have surplus or a clear weekly disadvantage.",
            )

        if analysis.intent == "team_needs":
            return BrainDecision(
                action="IDENTIFY_NEEDS",
                confidence=0.76,
                thesis="I’d focus on the needs that change your weekly playoff ceiling, not just the thinnest depth spots.",
                reasons=[
                    "For a contender, a need only matters if it shows up in the starting lineup.",
                    "Depth is useful, but the championship swing usually comes from upgrading one weak starter.",
                    "RB and TE are the positions I would pressure-test first before spending assets elsewhere.",
                ],
                next_action="Rank every possible move by how much it improves your playoff starting lineup.",
            )

        if analysis.intent == "league_context":
            return BrainDecision(
                action="LEAGUE_CONTEXT",
                confidence=0.68,
                thesis="I’d read the league through trade leverage: who needs what you have, and who has what you need.",
                reasons=[
                    "The best deal is usually not with the team that has the best player. It is with the team whose roster problem you can solve.",
                    "If you have QB/WR surplus, the league scan should start with teams weak at QB or WR but strong enough at RB/TE to trade from there.",
                ],
                caveats=[
                    "This answer needs league-wide roster evidence to name the best partners confidently."
                ],
                next_action="The next engine should rank trade partners by roster fit, not just player value.",
            )

        if analysis.intent == "lineup_decision":
            return BrainDecision(
                action="LINEUP_REVIEW",
                confidence=0.7,
                thesis="For a win-now team, I care about who helps your weekly lineup, not who looks best in dynasty value.",
                reasons=[
                    "Lineup decisions should be driven by role, projection, matchup, and replacement level.",
                    "A bench asset only matters if he can become a starter, protect against injury, or be traded into a starter.",
                ],
                next_action="Compare the players by projected weekly edge, not by name value.",
            )

        return BrainDecision(
            action="DIRECT_GM_ANSWER",
            confidence=0.65,
            thesis="I’d answer the specific GM question first, then bring in roster context only if it changes the decision.",
            reasons=[
                "The brain should not default to trade mode.",
                "It should decide whether this is about roster direction, lineup strength, contracts, market leverage, or player value.",
            ],
            next_action="If the question is broad, narrow it to the next actionable football decision.",
        )


    if analysis.intent == "team_strengths":
        return BrainDecision(
            action="TEAM_STRENGTHS",
            confidence=0.78,
            thesis="Your biggest strength is the QB foundation and the flexibility it creates.",
            reasons=[
                "In superflex, strong QB rooms give you both weekly floor and trade leverage.",
                "Your WR room also gives you movable value, which matters because your title path likely requires RB/TE improvement.",
                "The point is not just that you have good players. It is that your strengths line up with what other teams may need."
            ],
            next_action="Protect the true QB foundation, then decide which surplus QB/WR value can be converted into RB or TE help.",
        )

    if analysis.intent == "core_player_review":
        return BrainDecision(
            action="CORE_PLAYER_REVIEW",
            confidence=0.78,
            thesis="I would not start by shopping your true foundation pieces.",
            reasons=[
                "For a title push, protect players who give you a weekly advantage or irreplaceable superflex value.",
                "The movable group should be expensive/name-value players whose trade value can become a better starter.",
                "Do not confuse 'available in the right deal' with 'actively shopping.'"
            ],
            next_action="Split the roster into protected core, movable leverage, and churn candidates before making calls.",
        )

    if analysis.intent == "win_now_player_ranking":
        facts = evidence.facts or []
        wants_worst = any(w in analysis.raw_question.lower() for w in ["worst", "least", "bad"])

        if not facts:
            return BrainDecision(
                action="WIN_NOW_PLAYER_RANKING",
                confidence=0.55,
                thesis="I could not load the roster ranking evidence yet.",
                reasons=[],
                next_action="Check player_brain_context for this roster.",
            )

        reasons = []
        for i, f in enumerate(facts[:8], start=1):
            d = f.get("data") or {}
            reasons.append(
                f"{i}. {d.get('player_name')} ({d.get('pos')}) — win-now {d.get('win_now_rank_score')}, "
                f"present {float(d.get('present_score') or 0):.1f}, role {float(d.get('role_score') or 0):.1f}, "
                f"situation {float(d.get('situation_score') or 0):.1f}, risk {float(d.get('risk_score') or 0):.1f}, "
                f"${float(d.get('salary') or 0):.0f}/{float(d.get('years') or 0):.0f} yrs."
            )

        if wants_worst:
            thesis = "Your worst win-now players are the ones least likely to help a playoff lineup right now."
            next_action = "Use these spots as churn candidates, trade throw-ins, or upgrade paths."
        else:
            thesis = "Your best win-now players are the ones most likely to give you weekly playoff lineup value."
            next_action = "Protect the top true starters, then use the lower-ranked names to improve RB/TE."

        return BrainDecision(
            action="WIN_NOW_PLAYER_RANKING",
            confidence=0.86,
            thesis=thesis,
            reasons=reasons,
            next_action=next_action,
        )

    if analysis.intent == "trade_partner_search":
        facts = evidence.facts or []

        if not facts:
            return BrainDecision(
                action="TRADE_PARTNER_SEARCH",
                confidence=0.55,
                thesis="I could not load trade partner evidence yet.",
                reasons=[],
                next_action="Check team_brain_context and player_brain_context.",
            )

        reasons = []
        for i, f in enumerate(facts[:5], start=1):
            d = f.get("data") or {}
            rb_names = ", ".join([p.get("player_name") for p in d.get("rb_targets", [])[:2] if p.get("player_name")])
            te_names = ", ".join([p.get("player_name") for p in d.get("te_targets", [])[:2] if p.get("player_name")])

            target_bits = []
            if rb_names:
                target_bits.append(f"RBs: {rb_names}")
            if te_names:
                target_bits.append(f"TEs: {te_names}")

            reasons.append(
                f"{i}. {d.get('team')} — fit {d.get('fit_score')}, "
                f"QB {d.get('qb_score')}, RB {d.get('rb_score')}, WR {d.get('wr_score')}, TE {d.get('te_score')}. "
                f"{'; '.join(target_bits) if target_bits else 'No obvious RB/TE target loaded.'}"
            )

        return BrainDecision(
            action="TRADE_PARTNER_SEARCH",
            confidence=0.84,
            thesis="I would call the teams where your surplus lines up with their weakness and their RB/TE depth lines up with your need.",
            reasons=reasons,
            next_action="Start with the top two fits and ask specifically about RB/TE starters, not vague availability.",
        )

    if analysis.intent == "qb_surplus_strategy":
        return BrainDecision(
            action="QB_SURPLUS_STRATEGY",
            confidence=0.84,
            thesis="Yes, I would explore using QB depth to get RB help, but I would not weaken your foundation QB slot.",
            reasons=[
                "In superflex, QB depth is leverage. You should spend the surplus, not the foundation.",
                "The right return is a starting RB or TE who enters your playoff lineup.",
                "The best partner is a team thin at QB but strong enough at RB or TE to move a starter."
            ],
            next_action="Rank your QBs into foundation, movable, and throw-in before making offers.",
        )

    if analysis.intent == "trade_package":
        return BrainDecision(
            action="TRADE_PACKAGE",
            confidence=0.72,
            thesis="A fair Garrett Wilson trade should bring back a starter, not just make the contract disappear.",
            reasons=[
                "Because Wilson still has name value, I would use him as the centerpiece for RB or TE help.",
                "Because the contract is inefficient, I would expect either a slightly older producer, a smaller add-on, or salary/value balancing.",
                "For your goal, I would avoid a picks-only return unless the pick immediately becomes a starter trade chip."
            ],
            next_action="Start with Wilson for a usable RB/TE starter, then balance with picks or salary depending on the player tier.",
        )

    if analysis.intent == "non_trade_paths":
        return BrainDecision(
            action="NON_TRADE_PATHS",
            confidence=0.82,
            thesis="You are probably a little too trade-focused if every fix has to be a blockbuster.",
            reasons=[
                "Trades matter, but a title push also comes from FA churn, lineup optimization, injury insulation, and avoiding bad cuts.",
                "You should still trade if it creates a starting-lineup upgrade, but not every problem needs a major asset move.",
                "Small edges matter over a full season."
            ],
            next_action="Separate improvement paths into trades, FA/waivers, lineup decisions, and cap cleanup.",
        )

    if analysis.intent == "safe_path":
        return BrainDecision(
            action="SAFE_PATH",
            confidence=0.8,
            thesis="The safest path is to improve without touching your true foundation.",
            reasons=[
                "Keep the QB foundation intact.",
                "Market-check expensive inefficient contracts before cutting them.",
                "Use smaller assets, depth WRs, and non-premium picks to improve RB/TE depth first."
            ],
            next_action="Make one medium-sized RB/TE improvement before considering an all-in move.",
        )

    if analysis.intent == "aggressive_path":
        return BrainDecision(
            action="AGGRESSIVE_PATH",
            confidence=0.8,
            thesis="The aggressive path is consolidating surplus into one real playoff starter.",
            reasons=[
                "That likely means packaging a name-value WR/QB piece with a pick or salary flexibility.",
                "The target has to be a weekly starter at RB or TE.",
                "Do not spread assets across three minor upgrades if one true starter is available."
            ],
            next_action="Identify the best attainable RB/TE starter and build the package around that player.",
        )

    if analysis.intent == "first_move":
        return BrainDecision(
            action="FIRST_MOVE",
            confidence=0.84,
            thesis="The first move should be a market check, not a forced trade.",
            reasons=[
                "Start with Garrett Wilson and Isiah Pacheco because they combine name value with contract pressure.",
                "You are testing whether the league still values them enough to become RB/TE help.",
                "If the market is weak, hold the useful player and solve the roster another way."
            ],
            next_action="Send feelers for RB/TE upgrades using Wilson or Pacheco, but do not accept discounts.",
        )

    if analysis.intent == "player_contract_fit":
        return BrainDecision(
            action="PLAYER_CONTRACT_FIT",
            confidence=0.78,
            thesis="For a win-now team, Garrett Wilson's contract is a problem only if it blocks a better starting lineup.",
            reasons=[
                "The contract is inefficient, but the player still has market value.",
                "That means the right move is shop first, not cut.",
                "If the return does not clearly improve RB, TE, or weekly playoff points, holding is better than dumping."
            ],
            next_action="Judge the contract by opportunity cost: what starter can that cap or trade value become?",
        )

    if analysis.intent == "strategy_tradeoff":
        return BrainDecision(
            action="STRATEGY_TRADEOFF",
            confidence=0.86,
            thesis="With your stated goal, prioritize lineup points over future value — but only when the points enter your starting lineup.",
            reasons=[
                "A title push should not chase abstract dynasty value.",
                "But future value still matters if the veteran upgrade is only marginal.",
                "The rule should be simple: spend future value only for a real weekly edge."
            ],
            next_action="Use future assets for RB/TE starters, not depth or cosmetic upgrades.",
        )

    if analysis.intent == "target_recommendations":
        return BrainDecision(
            action="TARGET_RECOMMENDATIONS",
            confidence=0.68,
            thesis="The right RB target is a player who improves your playoff lineup and is gettable with your surplus.",
            reasons=[
                "I would prioritize weekly role over dynasty name value.",
                "Your best outgoing leverage is likely WR/QB value, picks, or salary balancing.",
                "This needs the target engine to name the best five players, but the profile is clear: usable touches, stable role, fair contract."
            ],
            next_action="Run the RB target scan across FAs and trade candidates, then build offers around WR/QB surplus.",
        )

    if analysis.intent == "free_agent_targets":
        facts = evidence.facts or []

        if not facts:
            return BrainDecision(
                action="FREE_AGENT_TARGETS",
                confidence=0.55,
                thesis="I could not find usable free-agent targets yet.",
                reasons=[],
                next_action="Check that player_brain_context has FA rows with current_owner null.",
            )

        reasons = []
        for i, f in enumerate(facts[:5], start=1):
            d = f.get("data") or {}
            reasons.append(
                f"{i}. {d.get('player_name')} ({d.get('pos')}) — brain {float(d.get('brain_score') or 0):.1f}, "
                f"present {float(d.get('present_score') or 0):.1f}, future {float(d.get('future_score') or 0):.1f}, "
                f"situation {float(d.get('situation_score') or 0):.1f}, contract {float(d.get('contract_score') or 0):.1f}."
            )

        return BrainDecision(
            action="FREE_AGENT_TARGETS",
            confidence=0.84,
            thesis="These are the free agents I would check first based on usable value, future profile, situation, and contract efficiency.",
            reasons=reasons,
            next_action="Use FA targets as churn/upside plays first; do not treat them like trade-target starters unless their role score supports it.",
        )


    return BrainDecision(
        action="ANSWER_DIRECTLY",
        confidence=0.65,
        thesis="I’d answer the specific question first and only bring in roster context if it changes the decision.",
        reasons=[],
    )
