import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")


def db():
    return psycopg2.connect(DATABASE_URL)


def implied_probability(decimal_odds):
    if not decimal_odds:
        return None
    decimal_odds = float(decimal_odds)
    if decimal_odds <= 1:
        return None
    return 1 / decimal_odds


def clamp(value, min_value=0.01, max_value=0.99):
    return max(min_value, min(max_value, value))


def get_team_signal_impact(cur, team_name):
    """
    Reads recent OpenClaw/news signals for a team and converts impact_score
    into probability adjustment.

    Rule:
    - impact_score is from -10 to +10
    - every 1 point = 0.5% probability move
    - total adjustment is capped between -8% and +8%
    """
    cur.execute("""
        SELECT COALESCE(SUM(impact_score), 0)
        FROM news_signals
        WHERE LOWER(team) = LOWER(%s)
        AND created_at >= NOW() - INTERVAL '48 hours'
    """, (team_name,))

    total_impact = float(cur.fetchone()[0] or 0)
    probability_adjustment = total_impact * 0.005
    return clamp(probability_adjustment, -0.08, 0.08)


def run_predictions():
    conn = db()
    cur = conn.cursor()

    cur.execute("""
        SELECT id, home_team, away_team, home_odds, away_odds
        FROM games
        WHERE home_odds IS NOT NULL
        AND away_odds IS NOT NULL
    """)
    rows = cur.fetchall()

    for r in rows:
        game_id, home, away, h_odds, a_odds = r

        home_implied = implied_probability(h_odds)
        away_implied = implied_probability(a_odds)

        if home_implied is None or away_implied is None:
            continue

        bookmaker_total = home_implied + away_implied
        fair_home = home_implied / bookmaker_total
        fair_away = away_implied / bookmaker_total

        home_advantage = 0.03
        home_signal_adj = get_team_signal_impact(cur, home)
        away_signal_adj = get_team_signal_impact(cur, away)

        signal_diff = home_signal_adj - away_signal_adj

        h_prob = clamp(fair_home + home_advantage + signal_diff)
        a_prob = clamp(1 - h_prob)

        edge_home = h_prob - fair_home
        edge_away = a_prob - fair_away

        rec = "NO BET"
        if edge_home >= 0.05:
            rec = f"BET {home} moneyline"
        elif edge_away >= 0.05:
            rec = f"BET {away} moneyline"

        cur.execute("""
            INSERT INTO predictions (
                game_id,
                win_prob_home,
                win_prob_away,
                edge_home,
                recommendation
            )
            VALUES (%s,%s,%s,%s,%s)
        """, (
            game_id,
            round(h_prob, 4),
            round(a_prob, 4),
            round(edge_home, 4),
            rec
        ))

    conn.commit()
    cur.close()
    conn.close()

    print(f"Predictions done for {len(rows)} games")
