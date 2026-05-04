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
        CASE
            WHEN g.home_score > g.away_score THEN 1
            ELSE 0
        END AS home_win
    FROM bet_recommendations b
    JOIN games g ON g.id = b.game_id
    WHERE g.home_score IS NOT NULL
      AND g.away_score IS NOT NULL
      AND g.home_score != g.away_score
    """
    df = pd.read_sql(query, conn)
    conn.close()

    if df.empty:
        return df

    # Feature engineering
    df["odds_diff"] = df["home_odds"] - df["away_odds"]
    df["implied_home"] = 1 / df["home_odds"]
    df["implied_away"] = 1 / df["away_odds"]
    df["market_gap"] = df["implied_home"] - df["implied_away"]

    # Encode signal_level
    df["signal_encoded"] = df["signal_level"].map({
        "STRONG BET": 2,
        "MEDIUM BET": 1,
        "PASS": 0
    }).fillna(0)

    return df


def train_model():
    df = load_training_data()

    if len(df) < 100:
        print("Not enough data for ML training")
        return

    features = [
        "home_odds",
        "away_odds",
        "edge",
        "confidence_score",
        "odds_diff",
        "market_gap",
        "signal_encoded"
    ]

    X = df[features]
    y = df["home_win"]

    if XGB_AVAILABLE:
        model = xgb.XGBClassifier(
            n_estimators=120,
            max_depth=4,
            learning_rate=0.08,
            subsample=0.9,
            colsample_bytree=0.9,
            eval_metric="logloss"
        )
    else:
        model = LogisticRegression(max_iter=200)

    model.fit(X, y)

    joblib.dump(model, MODEL_PATH)
    print("✅ XGBoost model trained")


def load_model():
    if not os.path.exists(MODEL_PATH):
        return None
    return joblib.load(MODEL_PATH)


def predict(home_odds, away_odds, edge, confidence, signal_level):
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

    X = [[
        home_odds,
        away_odds,
        edge,
        confidence,
        odds_diff,
        market_gap,
        signal_encoded
    ]]

    prob = model.predict_proba(X)[0][1]
    return prob
