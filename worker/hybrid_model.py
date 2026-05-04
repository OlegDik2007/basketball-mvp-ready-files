import os

try:
    from ml_model_xgb import predict as ml_predict
except Exception:
    ml_predict = None

MIN_ML_EVAL_GAMES = int(os.getenv("MIN_ML_EVAL_GAMES", "50"))
DEFAULT_ML_WEIGHT = float(os.getenv("DEFAULT_ML_WEIGHT", "0.30"))
MAX_ML_WEIGHT = float(os.getenv("MAX_ML_WEIGHT", "0.70"))
MIN_ML_WEIGHT = float(os.getenv("MIN_ML_WEIGHT", "0.15"))
ROI_WEIGHT_MODE = os.getenv("ROI_WEIGHT_MODE", "true").lower() == "true"


def clamp(value, min_value=0.01, max_value=0.99):
    return max(min_value, min(max_value, value))


def get_model_performance(cur):
    """
    Reads model_evaluations and chooses safe ML weight.

    Priority:
    1. If not enough data -> conservative ML weight.
    2. If ROI data exists -> use ROI-based weighting.
    3. Fallback -> use accuracy-based weighting.

    Note: ROI is estimated using model pick correctness + available odds.
    This is for model selection, not automatic betting.
    """
    cur.execute("""
        SELECT
            COUNT(*) AS total,
            COUNT(*) FILTER (WHERE rule_correct = true) AS rule_correct,
            COUNT(*) FILTER (WHERE ml_correct = true) AS ml_correct,
            COALESCE(SUM(
                CASE
                    WHEN rule_correct = true AND rule_pick_home = 1 THEN (g.home_odds - 1)
                    WHEN rule_correct = true AND rule_pick_home = 0 THEN (g.away_odds - 1)
                    WHEN rule_correct = false THEN -1
                    ELSE 0
                END
            ), 0) AS rule_profit_units,
            COALESCE(SUM(
                CASE
                    WHEN ml_correct = true AND ml_pick_home = 1 THEN (g.home_odds - 1)
                    WHEN ml_correct = true AND ml_pick_home = 0 THEN (g.away_odds - 1)
                    WHEN ml_correct = false THEN -1
                    ELSE 0
                END
            ), 0) AS ml_profit_units
        FROM model_evaluations e
        JOIN games g ON g.id = e.game_id
        WHERE g.home_odds IS NOT NULL
          AND g.away_odds IS NOT NULL
    """)
    row = cur.fetchone()

    total = int(row[0] or 0)
    rule_correct = int(row[1] or 0)
    ml_correct = int(row[2] or 0)
    rule_profit_units = float(row[3] or 0)
    ml_profit_units = float(row[4] or 0)

    rule_accuracy = (rule_correct / total) if total else 0
    ml_accuracy = (ml_correct / total) if total else 0
    rule_roi = (rule_profit_units / total) if total else 0
    ml_roi = (ml_profit_units / total) if total else 0

    if total < MIN_ML_EVAL_GAMES:
        return {
            "total": total,
            "rule_accuracy": rule_accuracy,
            "ml_accuracy": ml_accuracy,
            "rule_roi": rule_roi,
            "ml_roi": ml_roi,
            "ml_weight": DEFAULT_ML_WEIGHT,
            "reason": f"Not enough model evaluation data ({total}/{MIN_ML_EVAL_GAMES}). Using conservative ML weight."
        }

    if ROI_WEIGHT_MODE:
        roi_gap = ml_roi - rule_roi

        if ml_roi > 0 and roi_gap > 0.06:
            ml_weight = MAX_ML_WEIGHT
            reason = "ML has clearly better ROI. Using higher ML weight."
        elif ml_roi > 0 and roi_gap > 0.025:
            ml_weight = 0.55
            reason = "ML has moderately better ROI. Using medium-high ML weight."
        elif rule_roi > ml_roi + 0.025:
            ml_weight = MIN_ML_WEIGHT
            reason = "Rule has better ROI. Keeping ML as small secondary signal."
        elif ml_roi < 0 and rule_roi >= 0:
            ml_weight = MIN_ML_WEIGHT
            reason = "ML ROI is negative while Rule is not. Reducing ML weight."
        else:
            ml_weight = 0.40
            reason = "ROI is close or mixed. Using balanced hybrid weight."
    else:
        if ml_accuracy > rule_accuracy + 0.04:
            ml_weight = MAX_ML_WEIGHT
            reason = "ML is clearly outperforming Rule by accuracy. Using higher ML weight."
        elif ml_accuracy > rule_accuracy + 0.02:
            ml_weight = 0.55
            reason = "ML is moderately outperforming Rule by accuracy. Using medium-high ML weight."
        elif rule_accuracy > ml_accuracy + 0.02:
            ml_weight = MIN_ML_WEIGHT
            reason = "Rule is outperforming ML by accuracy. Keeping ML as secondary signal."
        else:
            ml_weight = 0.40
            reason = "ML and Rule accuracy are close. Using balanced hybrid weight."

    return {
        "total": total,
        "rule_accuracy": rule_accuracy,
        "ml_accuracy": ml_accuracy,
        "rule_roi": rule_roi,
        "ml_roi": ml_roi,
        "ml_weight": ml_weight,
        "reason": reason
    }


def hybrid_probability(cur, rule_prob_home, home_odds, away_odds, edge_home, confidence_score, signal_level):
    """
    Returns final home probability and explanation.
    Rule probability is always available.
    ML probability is used only if trained model exists.
    Weight is selected by ROI first, then accuracy fallback.
    """
    perf = get_model_performance(cur)

    ml_prob = None
    if ml_predict:
        try:
            ml_prob = ml_predict(
                float(home_odds),
                float(away_odds),
                float(edge_home or 0),
                int(confidence_score or 50),
                signal_level or "PASS"
            )
        except Exception as e:
            ml_prob = None
            perf["reason"] += f" ML unavailable: {e}"

    if ml_prob is None:
        return clamp(rule_prob_home), {
            "rule_prob": round(rule_prob_home, 4),
            "ml_prob": None,
            "ml_weight": 0,
            "rule_weight": 1,
            "reason": "ML model not trained/available. Using Rule model only.",
            "rule_roi": round(perf.get("rule_roi", 0), 4),
            "ml_roi": round(perf.get("ml_roi", 0), 4),
        }

    ml_weight = perf["ml_weight"]
    rule_weight = 1 - ml_weight
    final_prob = clamp((rule_prob_home * rule_weight) + (float(ml_prob) * ml_weight))

    return final_prob, {
        "rule_prob": round(rule_prob_home, 4),
        "ml_prob": round(float(ml_prob), 4),
        "ml_weight": round(ml_weight, 2),
        "rule_weight": round(rule_weight, 2),
        "reason": perf["reason"],
        "rule_accuracy": round(perf["rule_accuracy"], 4),
        "ml_accuracy": round(perf["ml_accuracy"], 4),
        "rule_roi": round(perf.get("rule_roi", 0), 4),
        "ml_roi": round(perf.get("ml_roi", 0), 4),
        "evaluated_games": perf["total"]
    }
