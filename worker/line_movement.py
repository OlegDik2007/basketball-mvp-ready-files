import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")


def db():
    return psycopg2.connect(DATABASE_URL)


def ensure_line_tables(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS line_movement_predictions (
            id SERIAL PRIMARY KEY,
            game_id INT UNIQUE,
            home_team TEXT,
            away_team TEXT,
            first_home_odds NUMERIC,
            last_home_odds NUMERIC,
            first_away_odds NUMERIC,
            last_away_odds NUMERIC,
            home_move_pct NUMERIC,
            away_move_pct NUMERIC,
            predicted_home_direction TEXT,
            predicted_away_direction TEXT,
            early_value_team TEXT,
            confidence_score INT DEFAULT 50,
            reason TEXT,
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW()
        )
    """)


def movement_pct(first, last):
    if not first or not last or float(first) <= 0:
        return 0
    return ((float(last) - float(first)) / float(first)) * 100


def confidence_from_move(home_move, away_move, snapshots_count):
    strength = max(abs(home_move), abs(away_move))
    score = 50 + min(strength * 6, 30)
    if snapshots_count >= 4:
        score += 10
    if snapshots_count >= 8:
        score += 10
    return int(max(1, min(100, round(score))))


def predict_line_movement():
    conn = db()
    cur = conn.cursor()
    ensure_line_tables(cur)

    cur.execute("""
        SELECT DISTINCT game_id
        FROM odds_snapshots
        WHERE game_id IS NOT NULL
    """)
    game_ids = [r[0] for r in cur.fetchall()]

    saved = 0

    for game_id in game_ids:
        cur.execute("""
            SELECT home_team, away_team, home_odds, away_odds, created_at
            FROM odds_snapshots
            WHERE game_id = %s
            ORDER BY created_at ASC
        """, (game_id,))
        snaps = cur.fetchall()
        if len(snaps) < 2:
            continue

        first = snaps[0]
        last = snaps[-1]
        home_team, away_team = last[0], last[1]
        first_home_odds = float(first[2])
        first_away_odds = float(first[3])
        last_home_odds = float(last[2])
        last_away_odds = float(last[3])

        home_move = movement_pct(first_home_odds, last_home_odds)
        away_move = movement_pct(first_away_odds, last_away_odds)

        # In decimal odds, odds going DOWN usually means market is moving toward that team.
        if home_move < -1:
            predicted_home_direction = "STEAM_TOWARD_HOME"
        elif home_move > 1:
            predicted_home_direction = "DRIFT_AWAY_FROM_HOME"
        else:
            predicted_home_direction = "STABLE_HOME"

        if away_move < -1:
            predicted_away_direction = "STEAM_TOWARD_AWAY"
        elif away_move > 1:
            predicted_away_direction = "DRIFT_AWAY_FROM_AWAY"
        else:
            predicted_away_direction = "STABLE_AWAY"

        early_value_team = None
        if home_move < -1 and abs(home_move) >= abs(away_move):
            early_value_team = home_team
        elif away_move < -1 and abs(away_move) > abs(home_move):
            early_value_team = away_team

        confidence = confidence_from_move(home_move, away_move, len(snaps))

        reason = (
            f"Snapshots: {len(snaps)}. "
            f"Home odds moved {round(home_move, 2)}% ({first_home_odds} → {last_home_odds}). "
            f"Away odds moved {round(away_move, 2)}% ({first_away_odds} → {last_away_odds}). "
            f"Decimal odds decreasing means market support for that side."
        )

        cur.execute("""
            INSERT INTO line_movement_predictions (
                game_id, home_team, away_team,
                first_home_odds, last_home_odds,
                first_away_odds, last_away_odds,
                home_move_pct, away_move_pct,
                predicted_home_direction, predicted_away_direction,
                early_value_team, confidence_score, reason,
                created_at, updated_at
            )
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW(),NOW())
            ON CONFLICT (game_id)
            DO UPDATE SET
                last_home_odds = EXCLUDED.last_home_odds,
                last_away_odds = EXCLUDED.last_away_odds,
                home_move_pct = EXCLUDED.home_move_pct,
                away_move_pct = EXCLUDED.away_move_pct,
                predicted_home_direction = EXCLUDED.predicted_home_direction,
                predicted_away_direction = EXCLUDED.predicted_away_direction,
                early_value_team = EXCLUDED.early_value_team,
                confidence_score = EXCLUDED.confidence_score,
                reason = EXCLUDED.reason,
                updated_at = NOW()
        """, (
            game_id,
            home_team,
            away_team,
            first_home_odds,
            last_home_odds,
            first_away_odds,
            last_away_odds,
            round(home_move, 2),
            round(away_move, 2),
            predicted_home_direction,
            predicted_away_direction,
            early_value_team,
            confidence,
            reason,
        ))
        saved += 1

    conn.commit()
    cur.close()
    conn.close()

    print(f"✅ Line movement predictions saved for {saved} games")


if __name__ == "__main__":
    predict_line_movement()
