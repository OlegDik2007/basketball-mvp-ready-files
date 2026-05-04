import os

try:
    from ml_model_xgb import predict as ml_predict
except Exception:
    ml_predict = None

MIN_ML_EVAL_GAMES = int(os.getenv("MIN_ML_EVAL_GAMES", "50"))
DEFAULT_ML_WEIGHT = float(os.getenv("DEFAULT_ML_WEIGHT", "0.30"))
MAX_ML_WEIGHT = float(os.getenv("MAX_ML_WEIGHT", "0.70"))


def clamp(value, min_value=0.01, max_value=0.99):
    return max(min_value, min(max_value, value))


def get_model_performance(cur):
    """
    Reads model_evaluations and decides safe ML weight.
    If not enough data, ML stays secondary.
    """
    cur.execute("""
        SELECT
            COUNT(*) AS total,
            COUNT(*) FILTER (WHERE rule_correct = true) AS rule_correct,
            COUNT(*) FILTER (WHERE ml_correct = true) AS ml_correct
        FROM model_evaluations
    """)
    row = cur.fetchone()
    total = int(row[0] or 0)
    rule_correct = int(row[1] or 0)
    ml_correct = int(row[2] or 0)

    rule_accuracy = (rule_correct / total) if total else 0
    ml_accuracy = (ml_correct / total) if total else 0

    if total < MIN_ML_EVAL_GAMES:
        return {
            "total": total,
            "rule_accuracy": rule_accuracy,
            "ml_accuracy": ml_accuracy,
            "ml_weight": DEFAULT_ML_WEIGHT,
            "reason": f"Not enough ML evaluation data ({total}/{MIN_ML_EVAL_GAMES}). Using conservative ML weight."
        }

    if ml_accuracy > rule_accuracy + 0.04:
        ml_weight = MAX_ML_WEIGHT
        reason = "ML is clearly outperforming Rule. Using higher ML weight."
    elif ml_accuracy > rule_accuracy + 0.02:
        ml_weight = 0.55
        reason = "ML is moderately outperforming Rule. Using medium-high ML weight."
    elif rule_accuracy > ml_accuracy + 0.02:
        ml_weight = 0.20
        reason = "Rule is outperforming ML. Keeping ML as secondary signal."
    else:
        ml_weight = 0.40
        reason = "ML and Rule are close. Using balanced hybrid weight."

    return {
        "total": total,
        "rule_accuracy": rule_accuracy,
        "ml_accuracy": ml_accuracy,
        "ml_weight": ml_weight,
        "reason": reason
    }


def hybrid_probability(cur, rule_prob_home, home_odds, away_odds, edge_home, confidence_score, signal_level):
    """
    Returns final home probability and explanation.
    Rule probability is always available.
    ML probability is used only if trained model exists.
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
            "reason": "ML model not trained/available. Using Rule model only."
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
        "evaluated_games": perf["total"]
    }
