from __future__ import annotations


def evaluate_player_action(row: dict) -> dict:
    name = row.get("player_name") or row.get("player") or "Unknown Player"
    pos = row.get("pos", "")

    salary = float(row.get("salary_num", row.get("salary", 0)) or 0)
    years = float(row.get("years_num", row.get("years", 0)) or 0)
    roi = float(row.get("contract_roi_score", 0) or 0)
    dead_cap = float(row.get("dead_cap_estimate", 0) or 0)

    win_now = float(row.get("win_now_score", 0) or 0)
    asset = float(row.get("dynasty_asset_score", 0) or 0)
    production = float(row.get("production_score", win_now) or 0)

    if roi >= 18 and asset >= 60:
        action = "KEEP"
        reason = "strong asset with positive contract ROI"

    elif roi >= 14 and win_now >= 55:
        action = "KEEP / START"
        reason = "useful current production relative to contract cost"

    elif asset >= 65 and roi < 10:
        action = "SHOP"
        reason = "name value remains stronger than contract value"

    elif salary >= 25 and years >= 2 and roi < 8:
        if dead_cap <= salary:
            action = "DROP / EAT DEAD CAP"
            reason = "contract is underwater enough that exiting may be better than holding"
        else:
            action = "SHOP OR BENCH"
            reason = "bad contract, but dead cap makes immediate drop painful"

    elif salary >= 15 and roi < 8:
        action = "SHOP"
        reason = "contract cost is outpacing player value"

    elif asset < 35 and win_now < 35:
        action = "CUT CANDIDATE"
        reason = "limited asset value and limited current utility"

    else:
        action = "HOLD"
        reason = "no urgent action; value and cost are relatively balanced"

    return {
        "player_name": name,
        "pos": pos,
        "recommended_action": action,
        "action_reason": reason,
        "contract_roi_score": round(roi, 2),
        "dead_cap_estimate": round(dead_cap, 2),
        "salary": salary,
        "years": years,
        "win_now_score": win_now,
        "dynasty_asset_score": asset,
        "production_score": production,
    }


def evaluate_actions(df):
    if df.empty:
        return df

    out = df.copy()
    evaluations = out.apply(lambda r: evaluate_player_action(r.to_dict()), axis=1)

    import pandas as pd
    eval_df = pd.DataFrame(list(evaluations))

    for col in eval_df.columns:
        out[col] = eval_df[col]

    return out
