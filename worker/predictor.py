import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")


def db():
    return psycopg2.connect(DATABASE_URL)


def run_predictions():
    conn = db()
    cur = conn.cursor()

    cur.execute("SELECT id, home_team, away_team, home_odds, away_odds FROM games")
    rows = cur.fetchall()

    for r in rows:
        game_id, home, away, h_odds, a_odds = r

        if not h_odds or not a_odds:
            continue

        h_prob = 0.55
        a_prob = 0.45

        edge = h_prob - (1 / float(h_odds))

        rec = "NO BET"
        if edge > 0.05:
            rec = f"BET {home}"

        cur.execute("""
            INSERT INTO predictions (game_id, win_prob_home, win_prob_away, edge_home, recommendation)
            VALUES (%s,%s,%s,%s,%s)
        """, (game_id, h_prob, a_prob, edge, rec))

    conn.commit()
    cur.close()
    conn.close()

    print("Predictions done")
