import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")


def db():
    return psycopg2.connect(DATABASE_URL)


def ensure_clv_tables(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS odds_snapshots (
            id SERIAL PRIMARY KEY,
            game_id INT,
            home_team TEXT,
            away_team TEXT,
            home_odds NUMERIC,
            away_odds NUMERIC,
            snapshot_type TEXT DEFAULT 'update',
            source TEXT DEFAULT 'openclaw',
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS clv_results (
            id SERIAL PRIMARY KEY,
            bet_id INT UNIQUE,
            game_id INT,
            selected_team TEXT,
            selected_odds NUMERIC,
            closing_odds NUMERIC,
            clv_decimal NUMERIC,
            clv_percent NUMERIC,
            clv_status TEXT,
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)


def snapshot_current_odds(snapshot_type="update"):
    conn = db()
    cur = conn.cursor()
    ensure_clv_tables(cur)

    cur.execute("""
        SELECT id, home_team, away_team, home_odds, away_odds, source
        FROM games
        WHERE home_odds IS NOT NULL
          AND away_odds IS NOT NULL
          AND COALESCE(is_anomaly, false) = false
    """)

    rows = cur.fetchall()
    saved = 0

    for game_id, home, away, home_odds, away_odds, source in rows:
        cur.execute("""
            INSERT INTO odds_snapshots (
                game_id, home_team, away_team, home_odds, away_odds, snapshot_type, source
            )
            VALUES (%s,%s,%s,%s,%s,%s,%s)
        """, (game_id, home, away, home_odds, away_odds, snapshot_type, source or "openclaw"))
        saved += 1

    conn.commit()
    cur.close()
    conn.close()

    print(f"✅ Saved {saved} odds snapshots as {snapshot_type}")


def calculate_clv():
    """
    CLV = selected_odds / closing_odds - 1
    Positive CLV means you got a better price than closing market.
    """
    conn = db()
    cur = conn.cursor()
    ensure_clv_tables(cur)

    cur.execute("""
        SELECT
            b.id,
            b.game_id,
            b.selected_team,
            b.selected_odds,
            g.home_team,
            g.away_team
        FROM bet_recommendations b
        JOIN games g ON g.id = b.game_id
        WHERE b.selected_odds IS NOT NULL
    """)

    bets = cur.fetchall()
    calculated = 0

    for bet_id, game_id, selected_team, selected_odds, home_team, away_team in bets:
        cur.execute("""
            SELECT home_odds, away_odds
            FROM odds_snapshots
            WHERE game_id = %s
              AND snapshot_type IN ('closing', 'final_check')
            ORDER BY created_at DESC
            LIMIT 1
        """, (game_id,))

        row = cur.fetchone()
        if not row:
            continue

        closing_home_odds, closing_away_odds = row
        if selected_team and selected_team.lower() == (home_team or "").lower():
            closing_odds = float(closing_home_odds)
        elif selected_team and selected_team.lower() == (away_team or "").lower():
            closing_odds = float(closing_away_odds)
        else:
            continue

        selected_odds = float(selected_odds)
        if closing_odds <= 1:
            continue

        clv_decimal = (selected_odds / closing_odds) - 1
        clv_percent = clv_decimal * 100

        if clv_percent > 1.0:
            clv_status = "POSITIVE_CLV"
        elif clv_percent < -1.0:
            clv_status = "NEGATIVE_CLV"
        else:
            clv_status = "NEUTRAL_CLV"

        cur.execute("""
            INSERT INTO clv_results (
                bet_id, game_id, selected_team, selected_odds, closing_odds,
                clv_decimal, clv_percent, clv_status, created_at
            )
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,NOW())
            ON CONFLICT (bet_id)
            DO UPDATE SET
                selected_odds = EXCLUDED.selected_odds,
                closing_odds = EXCLUDED.closing_odds,
                clv_decimal = EXCLUDED.clv_decimal,
                clv_percent = EXCLUDED.clv_percent,
                clv_status = EXCLUDED.clv_status,
                created_at = NOW()
        """, (
            bet_id,
            game_id,
            selected_team,
            selected_odds,
            closing_odds,
            round(clv_decimal, 4),
            round(clv_percent, 2),
            clv_status,
        ))
        calculated += 1

    conn.commit()
    cur.close()
    conn.close()

    print(f"✅ Calculated CLV for {calculated} bets")


if __name__ == "__main__":
    snapshot_current_odds("manual")
    calculate_clv()
