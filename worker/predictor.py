import os
import psycopg2
import requests
from dotenv import load_dotenv

load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
DEFAULT_BANKROLL = float(os.getenv("DEFAULT_BANKROLL", "1000"))
MAX_STAKE_PCT = float(os.getenv("MAX_STAKE_PCT", "0.05"))
KELLY_FRACTION = float(os.getenv("KELLY_FRACTION", "0.25"))
MIN_EDGE = float(os.getenv("MIN_EDGE", "0.05"))


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
        print("Telegram not configured. Skipping alert.", flush=True)
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        response = requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "HTML"
        }, timeout=15)

        if response.status_code != 200:
            print("Telegram error:", response.status_code, response.text, flush=True)
            return False

        return True
    except Exception as e:
        print("Telegram request failed:", e, flush=True)
        return False


def ensure_money_tables(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS bankroll_settings (
            id SERIAL PRIMARY KEY,
            bankroll NUMERIC NOT NULL DEFAULT 1000,
            max_stake_pct NUMERIC NOT NULL DEFAULT 0.05,
            kelly_fraction NUMERIC NOT NULL DEFAULT 0.25,
            updated_at TIMESTAMP DEFAULT NOW()
        )
    """)

    cur.execute("""
        INSERT INTO bankroll_settings (id, bankroll, max_stake_pct, kelly_fraction)
        VALUES (1, %s, %s, %s)
        ON CONFLICT (id) DO NOTHING
    """, (DEFAULT_BANKROLL, MAX_STAKE_PCT, KELLY_FRACTION))

    cur.execute("""
        CREATE TABLE IF NOT EXISTS value_bet_alerts (
            id SERIAL PRIMARY KEY,
            game_id INT,
            recommendation TEXT,
            edge NUMERIC,
            stake_amount NUMERIC,
            sent_at TIMESTAMP DEFAULT NOW(),
            UNIQUE (game_id, recommendation)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS bet_recommendations (
            id SERIAL PRIMARY KEY,
            game_id INT,
            selected_team TEXT,
            recommendation TEXT,
            selected_odds NUMERIC,
            model_probability NUMERIC,
            fair_probability NUMERIC,
            edge NUMERIC,
            bankroll NUMERIC,
            stake_pct NUMERIC,
            stake_amount NUMERIC,
            status TEXT DEFAULT 'open',
            created_at TIMESTAMP DEFAULT NOW(),
            UNIQUE (game_id, recommendation)
        )
    """)


def get_bankroll_settings(cur):
    cur.execute("""
        SELECT bankroll, max_stake_pct, kelly_fraction
        FROM bankroll_settings
        WHERE id = 1
    """)
    row = cur.fetchone()
    if not row:
        return DEFAULT_BANKROLL, MAX_STAKE_PCT, KELLY_FRACTION
    return float(row[0]), float(row[1]), float(row[2])


def calculate_kelly_stake(decimal_odds, model_probability, bankroll, max_stake_pct, kelly_fraction):
    """
    Fractional Kelly stake sizing.
    b = decimal_odds - 1
    p = model probability
    q = 1 - p
    Kelly = (b*p - q) / b
    We use fractional Kelly and cap stake to protect bankroll.
    """
    b = float(decimal_odds) - 1
    p = float(model_probability)
    q = 1 - p

    if b <= 0:
        return 0, 0

    full_kelly = (b * p - q) / b
    if full_kelly <= 0:
        return 0, 0

    stake_pct = full_kelly * kelly_fraction
    stake_pct = min(stake_pct, max_stake_pct)
    stake_pct = max(stake_pct, 0)
    stake_amount = bankroll * stake_pct

    return round(stake_pct, 4), round(stake_amount, 2)


def already_alerted(cur, game_id, recommendation):
    cur.execute("""
        SELECT id FROM value_bet_alerts
        WHERE game_id = %s AND recommendation = %s
        LIMIT 1
    """, (game_id, recommendation))
    return cur.fetchone() is not None


def mark_alerted(cur, game_id, recommendation, edge, stake_amount):
    cur.execute("""
        INSERT INTO value_bet_alerts (game_id, recommendation, edge, stake_amount)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (game_id, recommendation) DO NOTHING
    """, (game_id, recommendation, edge, stake_amount))


def save_bet_recommendation(cur, game_id, selected_team, recommendation, selected_odds, model_probability, fair_probability, edge, bankroll, stake_pct, stake_amount):
    cur.execute("""
        INSERT INTO bet_recommendations (
            game_id,
            selected_team,
            recommendation,
            selected_odds,
            model_probability,
            fair_probability,
            edge,
            bankroll,
            stake_pct,
            stake_amount
        )
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (game_id, recommendation)
        DO UPDATE SET
            selected_odds = EXCLUDED.selected_odds,
            model_probability = EXCLUDED.model_probability,
            fair_probability = EXCLUDED.fair_probability,
            edge = EXCLUDED.edge,
            bankroll = EXCLUDED.bankroll,
            stake_pct = EXCLUDED.stake_pct,
            stake_amount = EXCLUDED.stake_amount,
            created_at = NOW()
    """, (
        game_id,
        selected_team,
        recommendation,
        selected_odds,
        round(model_probability, 4),
        round(fair_probability, 4),
        round(edge, 4),
        round(bankroll, 2),
        round(stake_pct, 4),
        round(stake_amount, 2)
    ))


def get_team_signal_impact(cur, team_name):
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
    ensure_money_tables(cur)
    bankroll, max_stake_pct, kelly_fraction = get_bankroll_settings(cur)

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
        fair_team_prob = fair_home
        selected_team = home
        selected_odds = h_odds

        if edge_home >= MIN_EDGE:
            rec = f"BET {home} moneyline"
            alert_edge = edge_home
            model_team_prob = h_prob
            fair_team_prob = fair_home
            selected_team = home
            selected_odds = h_odds
        elif edge_away >= MIN_EDGE:
            rec = f"BET {away} moneyline"
            alert_edge = edge_away
            model_team_prob = a_prob
            fair_team_prob = fair_away
            selected_team = away
            selected_odds = a_odds

        stake_pct = 0
        stake_amount = 0
        if rec != "NO BET":
            stake_pct, stake_amount = calculate_kelly_stake(
                selected_odds,
                model_team_prob,
                bankroll,
                max_stake_pct,
                kelly_fraction
            )
            save_bet_recommendation(
                cur,
                game_id,
                selected_team,
                rec,
                float(selected_odds),
                model_team_prob,
                fair_team_prob,
                alert_edge,
                bankroll,
                stake_pct,
                stake_amount
            )

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

        if rec != "NO BET" and stake_amount > 0 and not already_alerted(cur, game_id, rec):
            message = f"""
🏀 <b>Value Bet Alert</b>

<b>{away}</b> @ <b>{home}</b>

Recommendation: <b>{rec}</b>
Selected team: <b>{selected_team}</b>
Odds: <b>{selected_odds}</b>

Model probability: <b>{round(model_team_prob * 100, 1)}%</b>
Fair market probability: <b>{round(fair_team_prob * 100, 1)}%</b>
Edge: <b>{round(alert_edge * 100, 1)}%</b>

💰 Bankroll: <b>${round(bankroll, 2)}</b>
🎯 Suggested stake: <b>${stake_amount}</b> ({round(stake_pct * 100, 2)}%)
Risk rule: {round(kelly_fraction * 100)}% Kelly, capped at {round(max_stake_pct * 100, 1)}% bankroll

Home signal adjustment: {round(home_signal_adj * 100, 1)}%
Away signal adjustment: {round(away_signal_adj * 100, 1)}%

⚠️ Analytics signal only. No guaranteed outcome.
"""
            if send_telegram_alert(message):
                mark_alerted(cur, game_id, rec, round(alert_edge, 4), stake_amount)

    conn.commit()
    cur.close()
    conn.close()

    print(f"Predictions done for {len(rows)} games", flush=True)
