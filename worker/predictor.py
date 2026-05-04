import os
import psycopg2
import requests
from dotenv import load_dotenv

load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")


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


def send_telegram_alert(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram not configured. Skipping alert.")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        response = requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "HTML"
        }, timeout=15)

        if response.status_code != 200:
            print("Telegram error:", response.status_code, response.text)
            return False

        return True
    except Exception as e:
        print("Telegram request failed:", e)
        return False


def ensure_alert_table(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS value_bet_alerts (
            id SERIAL PRIMARY KEY,
            game_id INT,
            recommendation TEXT,
            edge NUMERIC,
            sent_at TIMESTAMP DEFAULT NOW(),
            UNIQUE (game_id, recommendation)
        )
    """)


def already_alerted(cur, game_id, recommendation):
    cur.execute("""
        SELECT id FROM value_bet_alerts
        WHERE game_id = %s AND recommendation = %s
        LIMIT 1
    """, (game_id, recommendation))
    return cur.fetchone() is not None


def mark_alerted(cur, game_id, recommendation, edge):
    cur.execute("""
        INSERT INTO value_bet_alerts (game_id, recommendation, edge)
        VALUES (%s, %s, %s)
        ON CONFLICT (game_id, recommendation) DO NOTHING
    """, (game_id, recommendation, edge))


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
    ensure_alert_table(cur)

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
        alert_edge = edge_home
        model_team_prob = h_prob
        selected_team = home
        selected_odds = h_odds

        if edge_home >= 0.05:
            rec = f"BET {home} moneyline"
            alert_edge = edge_home
            model_team_prob = h_prob
            selected_team = home
            selected_odds = h_odds
        elif edge_away >= 0.05:
            rec = f"BET {away} moneyline"
            alert_edge = edge_away
            model_team_prob = a_prob
            selected_team = away
            selected_odds = a_odds

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

        if rec != "NO BET" and not already_alerted(cur, game_id, rec):
            message = f"""
🏀 <b>Value Bet Alert</b>

<b>{away}</b> @ <b>{home}</b>

Recommendation: <b>{rec}</b>
Selected team: <b>{selected_team}</b>
Odds: <b>{selected_odds}</b>

Model probability: <b>{round(model_team_prob * 100, 1)}%</b>
Edge: <b>{round(alert_edge * 100, 1)}%</b>

Home signal adjustment: {round(home_signal_adj * 100, 1)}%
Away signal adjustment: {round(away_signal_adj * 100, 1)}%

⚠️ This is an analytics signal, not a guarantee.
"""
            if send_telegram_alert(message):
                mark_alerted(cur, game_id, rec, round(alert_edge, 4))

    conn.commit()
    cur.close()
    conn.close()

    print(f"Predictions done for {len(rows)} games")
