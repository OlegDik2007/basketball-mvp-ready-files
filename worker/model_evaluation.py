import os
import psycopg2
from dotenv import load_dotenv
from ml_model_xgb import predict as ml_predict

load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")


def db():
    return psycopg2.connect(DATABASE_URL)


def implied_probability(decimal_odds):
    if not decimal_odds or float(decimal_odds) <= 1:
        return None
    return 1 / float(decimal_odds)


def ensure_eval_table(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS model_evaluations (
            id SERIAL PRIMARY KEY,
            game_id INT UNIQUE,
            home_team TEXT,
            away_team TEXT,
            actual_home_win INT,
            rule_prob_home NUMERIC,
            ml_prob_home NUMERIC,
            rule_pick_home INT,
            ml_pick_home INT,
            rule_correct BOOLEAN,
            ml_correct BOOLEAN,
            edge NUMERIC,
            confidence_score INT,
            evaluated_at TIMESTAMP DEFAULT NOW()
        )
    """)


def evaluate_models():
    conn = db()
    cur = conn.cursor()
    ensure_eval_table(cur)

    cur.execute("""
        SELECT
            g.id,
            g.home_team,
            g.away_team,
            g.home_odds,
            g.away_odds,
            g.home_score,
            g.away_score,
            COALESCE(b.edge, 0),
            COALESCE(b.confidence_score, 50),
            COALESCE(b.signal_level, 'PASS')
        FROM games g
        LEFT JOIN bet_recommendations b ON b.game_id = g.id
        WHERE g.home_odds IS NOT NULL
          AND g.away_odds IS NOT NULL
          AND g.home_score IS NOT NULL
          AND g.away_score IS NOT NULL
          AND g.home_score != g.away_score
          AND COALESCE(g.is_anomaly, false) = false
    """)

    rows = cur.fetchall()
    evaluated = 0

    for row in rows:
        game_id, home, away, h_odds, a_odds, h_score, a_score, edge, confidence, signal_level = row

        h_imp = implied_probability(h_odds)
        a_imp = implied_probability(a_odds)
        if h_imp is None or a_imp is None:
            continue

        total = h_imp + a_imp
        rule_prob_home = h_imp / total
        rule_pick_home = 1 if rule_prob_home >= 0.5 else 0
        actual_home_win = 1 if h_score > a_score else 0
        rule_correct = bool(rule_pick_home == actual_home_win)

        ml_prob_home = ml_predict(float(h_odds), float(a_odds), float(edge or 0), int(confidence or 50), signal_level)
        if ml_prob_home is None:
            ml_prob_home = rule_prob_home

        ml_pick_home = 1 if ml_prob_home >= 0.5 else 0
        ml_correct = bool(ml_pick_home == actual_home_win)

        cur.execute("""
            INSERT INTO model_evaluations (
                game_id,
                home_team,
                away_team,
                actual_home_win,
                rule_prob_home,
                ml_prob_home,
                rule_pick_home,
                ml_pick_home,
                rule_correct,
                ml_correct,
                edge,
                confidence_score,
                evaluated_at
            )
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())
            ON CONFLICT (game_id)
            DO UPDATE SET
                rule_prob_home = EXCLUDED.rule_prob_home,
                ml_prob_home = EXCLUDED.ml_prob_home,
                rule_pick_home = EXCLUDED.rule_pick_home,
                ml_pick_home = EXCLUDED.ml_pick_home,
                rule_correct = EXCLUDED.rule_correct,
                ml_correct = EXCLUDED.ml_correct,
                edge = EXCLUDED.edge,
                confidence_score = EXCLUDED.confidence_score,
                evaluated_at = NOW()
        """, (
            game_id,
            home,
            away,
            actual_home_win,
            round(rule_prob_home, 4),
            round(float(ml_prob_home), 4),
            rule_pick_home,
            ml_pick_home,
            rule_correct,
            ml_correct,
            round(float(edge or 0), 4),
            int(confidence or 50),
        ))
        evaluated += 1

    conn.commit()
    cur.close()
    conn.close()

    print(f"✅ Model evaluation completed for {evaluated} games")


if __name__ == "__main__":
    evaluate_models()
