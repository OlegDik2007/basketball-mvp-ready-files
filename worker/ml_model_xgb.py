import os
import psycopg2
import pandas as pd
import joblib

try:
    import xgboost as xgb
    XGB_AVAILABLE = True
except ImportError:
    from sklearn.linear_model import LogisticRegression
    XGB_AVAILABLE = False

DATABASE_URL = os.getenv("DATABASE_URL")
MODEL_PATH = "model_xgb.pkl"
MIN_TRAINING_ROWS = int(os.getenv("MIN_TRAINING_ROWS", "100"))


def db():
    return psycopg2.connect(DATABASE_URL)


def load_training_data():
    conn = db()
    query = """
    SELECT
        g.home_odds,
        g.away_odds,
        b.edge,
        b.confidence_score,
        b.signal_level,
        COALESCE(c.clv_percent, 0) AS clv_percent,
        CASE
            WHEN c.clv_status = 'POSITIVE_CLV' THEN 1
            WHEN c.clv_status = 'NEGATIVE_CLV' THEN -1
            ELSE 0
        END AS clv_signal,
        CASE
            WHEN g.home_score > g.away_score THEN 1
            ELSE 0
        END AS home_win
    FROM bet_recommendations b
    JOIN games g ON g.id = b.game_id
    LEFT JOIN clv_results c ON c.bet_id = b.id
    WHERE g.home_score IS NOT NULL
      AND g.away_score IS NOT NULL
      AND g.home_score != g.away_score
      AND g.home_odds IS NOT NULL
      AND g.away_odds IS NOT NULL
      AND COALESCE(g.is_anomaly, false) = false
    """
    df = pd.read_sql(query, conn)
    conn.close()

    if df.empty:
        return df

    df["odds_diff"] = df["home_odds"] - df["away_odds"]
    df["implied_home"] = 1 / df["home_odds"]
    df["implied_away"] = 1 / df["away_odds"]
    df["market_gap"] = df["implied_home"] - df["implied_away"]

    df["signal_encoded"] = df["signal_level"].map({
        "STRONG BET": 2,
        "MEDIUM BET": 1,
        "PASS": 0
    }).fillna(0)

    # Keep CLV influence controlled so one bad scrape does not dominate training.
    df["clv_percent_capped"] = df["clv_percent"].clip(lower=-15, upper=15)

    # Sample weight: positive CLV examples matter more, negative CLV examples matter less.
    # This trains the model toward picks that beat closing line, not only picks that win.
    df["sample_weight"] = 1.0 + (df["clv_percent_capped"] / 20.0)
    df["sample_weight"] = df["sample_weight"].clip(lower=0.35, upper=1.75)

    return df


def feature_columns():
    return [
        "home_odds",
        "away_odds",
        "edge",
        "confidence_score",
        "odds_diff",
        "market_gap",
        "signal_encoded",
        "clv_percent_capped",
        "clv_signal"
    ]


def train_model():
    df = load_training_data()

    if len(df) < MIN_TRAINING_ROWS:
        print(f"Not enough data for ML training: {len(df)}/{MIN_TRAINING_ROWS}")
        return

    features = feature_columns()
    X = df[features]
    y = df["home_win"]
    sample_weight = df["sample_weight"]

    if XGB_AVAILABLE:
        model = xgb.XGBClassifier(
            n_estimators=160,
            max_depth=4,
            learning_rate=0.06,
            subsample=0.9,
            colsample_bytree=0.9,
            eval_metric="logloss"
        )
    else:
        model = LogisticRegression(max_iter=300)

    model.fit(X, y, sample_weight=sample_weight)

    joblib.dump(model, MODEL_PATH)
    print("✅ XGBoost ML model trained with CLV features and CLV sample weighting")


def load_model():
    if not os.path.exists(MODEL_PATH):
        return None
    return joblib.load(MODEL_PATH)


def predict(home_odds, away_odds, edge, confidence, signal_level, clv_percent=0, clv_signal=0):
    model = load_model()
    if not model:
        return None

    odds_diff = home_odds - away_odds
    implied_home = 1 / home_odds
    implied_away = 1 / away_odds
    market_gap = implied_home - implied_away

    signal_encoded = {
        "STRONG BET": 2,
        "MEDIUM BET": 1,
        "PASS": 0
    }.get(signal_level, 0)

    clv_percent_capped = max(-15, min(15, float(clv_percent or 0)))
    clv_signal = int(clv_signal or 0)

    X = [[
        home_odds,
        away_odds,
        edge,
        confidence,
        odds_diff,
        market_gap,
        signal_encoded,
        clv_percent_capped,
        clv_signal
    ]]

    prob = model.predict_proba(X)[0][1]
    return prob
