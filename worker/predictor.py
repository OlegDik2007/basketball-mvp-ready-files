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
MIN_EDGE = float(os.getenv("MIN_EDGE", "0.06"))
MIN_STRONG_EDGE = float(os.getenv("MIN_STRONG_EDGE", "0.085"))
MIN_ODDS = float(os.getenv("MIN_ODDS", "1.65"))
MAX_ODDS = float(os.getenv("MAX_ODDS", "2.75"))
MIN_SAMPLE_SIZE = int(os.getenv("MIN_SAMPLE_SIZE", "10"))


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
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"},
            timeout=15,
        )
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
        CREATE TABLE IF NOT EXISTS model_adjustments (
            id SERIAL PRIMARY KEY,
            bucket_type TEXT NOT NULL,
            bucket_name TEXT NOT NULL,
            sample_size INT DEFAULT 0,
            accuracy NUMERIC DEFAULT 0,
            roi NUMERIC DEFAULT 0,
            probability_penalty NUMERIC DEFAULT 0,
            edge_penalty NUMERIC DEFAULT 0,
            reason TEXT,
            updated_at TIMESTAMP DEFAULT NOW(),
            UNIQUE(bucket_type, bucket_name)
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
            signal_level TEXT DEFAULT 'PASS',
            risk_level TEXT DEFAULT 'HIGH',
            reason TEXT,
            status TEXT DEFAULT 'open',
            result_profit NUMERIC DEFAULT 0,
            created_at TIMESTAMP DEFAULT NOW(),
            settled_at TIMESTAMP,
            UNIQUE (game_id, recommendation)
        )
    """)
    for sql in [
        "ALTER TABLE bet_recommendations ADD COLUMN IF NOT EXISTS signal_level TEXT DEFAULT 'PASS'",
        "ALTER TABLE bet_recommendations ADD COLUMN IF NOT EXISTS risk_level TEXT DEFAULT 'HIGH'",
        "ALTER TABLE bet_recommendations ADD COLUMN IF NOT EXISTS reason TEXT",
        "ALTER TABLE bet_recommendations ADD COLUMN IF NOT EXISTS result_profit NUMERIC DEFAULT 0",
        "ALTER TABLE bet_recommendations ADD COLUMN IF NOT EXISTS settled_at TIMESTAMP",
    ]:
        cur.execute(sql)


def get_bankroll_settings(cur):
    cur.execute("SELECT bankroll, max_stake_pct, kelly_fraction FROM bankroll_settings WHERE id = 1")
    row = cur.fetchone()
    if not row:
        return DEFAULT_BANKROLL, MAX_STAKE_PCT, KELLY_FRACTION
    return float(row[0]), float(row[1]), float(row[2])


def calculate_kelly_stake(decimal_odds, model_probability, bankroll, max_stake_pct, kelly_fraction):
    b = float(decimal_odds) - 1
    p = float(model_probability)
    q = 1 - p
    if b <= 0:
        return 0, 0
    full_kelly = (b * p - q) / b
    if full_kelly <= 0:
        return 0, 0
    stake_pct = min(max(full_kelly * kelly_fraction, 0), max_stake_pct)
    return round(stake_pct, 4), round(bankroll * stake_pct, 2)


def odds_bucket(odds):
    odds = float(odds)
    if odds < 1.8:
        return "LOW_ODDS_1.65_1.79"
    if odds <= 2.2:
        return "MID_ODDS_1.80_2.20"
    return "HIGH_ODDS_2.21_2.75"


def load_adjustments(cur):
    cur.execute("SELECT bucket_type, bucket_name, probability_penalty, edge_penalty, reason FROM model_adjustments")
    adjustments = {}
    for bucket_type, bucket_name, probability_penalty, edge_penalty, reason in cur.fetchall():
        adjustments[(bucket_type, bucket_name)] = {
            "probability_penalty": float(probability_penalty or 0),
            "edge_penalty": float(edge_penalty or 0),
            "reason": reason or "",
        }
    return adjustments


def get_adjustment(adjustments, bucket_type, bucket_name):
    return adjustments.get((bucket_type, bucket_name), {"probability_penalty": 0, "edge_penalty": 0, "reason": ""})


def rebuild_model_adjustments(cur):
    """
    Learns from previous graded recommendations.
    If a bucket has enough history and poor performance, future signals are penalized.
    This does NOT place bets; it only makes recommendations stricter.
    """
    analysis_sql = """
        WITH graded AS (
            SELECT
                b.signal_level,
                CASE
                    WHEN b.selected_odds < 1.8 THEN 'LOW_ODDS_1.65_1.79'
                    WHEN b.selected_odds <= 2.2 THEN 'MID_ODDS_1.80_2.20'
                    ELSE 'HIGH_ODDS_2.21_2.75'
                END AS odds_bucket,
                b.stake_amount,
                b.result_profit,
                CASE
                    WHEN g.home_score IS NULL OR g.away_score IS NULL OR g.home_score = g.away_score THEN NULL
                    WHEN LOWER(b.selected_team)=LOWER(CASE WHEN g.home_score > g.away_score THEN g.home_team ELSE g.away_team END) THEN 1
                    ELSE 0
                END AS correct
            FROM bet_recommendations b
            JOIN games g ON g.id = b.game_id
            WHERE g.home_score IS NOT NULL
            AND g.away_score IS NOT NULL
            AND g.home_score != g.away_score
        )
        SELECT 'signal_level' AS bucket_type, signal_level AS bucket_name,
               COUNT(*) AS sample_size,
               AVG(correct)::numeric AS accuracy,
               CASE WHEN SUM(stake_amount) > 0 THEN SUM(result_profit) / SUM(stake_amount) ELSE 0 END AS roi
        FROM graded
        WHERE signal_level IS NOT NULL
        GROUP BY signal_level
        UNION ALL
        SELECT 'odds_bucket' AS bucket_type, odds_bucket AS bucket_name,
               COUNT(*) AS sample_size,
               AVG(correct)::numeric AS accuracy,
               CASE WHEN SUM(stake_amount) > 0 THEN SUM(result_profit) / SUM(stake_amount) ELSE 0 END AS roi
        FROM graded
        GROUP BY odds_bucket
    """
    cur.execute(analysis_sql)
    rows = cur.fetchall()
    for bucket_type, bucket_name, sample_size, accuracy, roi in rows:
        sample_size = int(sample_size or 0)
        accuracy = float(accuracy or 0)
        roi = float(roi or 0)
        probability_penalty = 0
        edge_penalty = 0
        reason = "No penalty: not enough data or performance acceptable."

        if sample_size >= MIN_SAMPLE_SIZE:
            if accuracy < 0.48 or roi < -0.08:
                probability_penalty = 0.035
                edge_penalty = 0.02
                reason = f"Auto penalty: weak bucket. Accuracy {round(accuracy*100,1)}%, ROI {round(roi*100,1)}%, sample {sample_size}."
            elif accuracy < 0.52 or roi < 0:
                probability_penalty = 0.015
                edge_penalty = 0.01
                reason = f"Auto light penalty: bucket under watch. Accuracy {round(accuracy*100,1)}%, ROI {round(roi*100,1)}%, sample {sample_size}."

        cur.execute("""
            INSERT INTO model_adjustments (
                bucket_type, bucket_name, sample_size, accuracy, roi,
                probability_penalty, edge_penalty, reason, updated_at
            )
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,NOW())
            ON CONFLICT (bucket_type, bucket_name)
            DO UPDATE SET
                sample_size=EXCLUDED.sample_size,
                accuracy=EXCLUDED.accuracy,
                roi=EXCLUDED.roi,
                probability_penalty=EXCLUDED.probability_penalty,
                edge_penalty=EXCLUDED.edge_penalty,
                reason=EXCLUDED.reason,
                updated_at=NOW()
        """, (
            bucket_type, bucket_name, sample_size, round(accuracy, 4), round(roi, 4),
            probability_penalty, edge_penalty, reason
        ))


def classify_signal(edge, odds, home_signal_adj, away_signal_adj):
    conflict = abs(home_signal_adj - away_signal_adj) < 0.01
    if odds < MIN_ODDS or odds > MAX_ODDS:
        return "PASS", "HIGH", f"Odds {odds} outside filter range {MIN_ODDS}-{MAX_ODDS}."
    if edge >= MIN_STRONG_EDGE and not conflict:
        return "STRONG BET", "MEDIUM", "Strong edge and clear signal context."
    if edge >= MIN_EDGE:
        return "MEDIUM BET", "MEDIUM", "Edge passed filter, but not strong enough for top signal."
    return "PASS", "HIGH", "Edge below minimum filter."


def already_alerted(cur, game_id, recommendation):
    cur.execute("SELECT id FROM value_bet_alerts WHERE game_id=%s AND recommendation=%s LIMIT 1", (game_id, recommendation))
    return cur.fetchone() is not None


def mark_alerted(cur, game_id, recommendation, edge, stake_amount):
    cur.execute("""
        INSERT INTO value_bet_alerts (game_id, recommendation, edge, stake_amount)
        VALUES (%s,%s,%s,%s)
        ON CONFLICT (game_id, recommendation) DO NOTHING
    """, (game_id, recommendation, edge, stake_amount))


def save_bet_recommendation(cur, game_id, selected_team, recommendation, selected_odds, model_probability, fair_probability, edge, bankroll, stake_pct, stake_amount, signal_level, risk_level, reason):
    cur.execute("""
        INSERT INTO bet_recommendations (
            game_id, selected_team, recommendation, selected_odds, model_probability,
            fair_probability, edge, bankroll, stake_pct, stake_amount,
            signal_level, risk_level, reason
        )
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (game_id, recommendation)
        DO UPDATE SET
            selected_odds=EXCLUDED.selected_odds,
            model_probability=EXCLUDED.model_probability,
            fair_probability=EXCLUDED.fair_probability,
            edge=EXCLUDED.edge,
            bankroll=EXCLUDED.bankroll,
            stake_pct=EXCLUDED.stake_pct,
            stake_amount=EXCLUDED.stake_amount,
            signal_level=EXCLUDED.signal_level,
            risk_level=EXCLUDED.risk_level,
            reason=EXCLUDED.reason,
            created_at=NOW()
    """, (
        game_id, selected_team, recommendation, selected_odds, round(model_probability, 4),
        round(fair_probability, 4), round(edge, 4), round(bankroll, 2),
        round(stake_pct, 4), round(stake_amount, 2), signal_level, risk_level, reason
    ))


def get_team_signal_impact(cur, team_name):
    cur.execute("""
        SELECT COALESCE(SUM(impact_score), 0)
        FROM news_signals
        WHERE LOWER(team)=LOWER(%s)
        AND created_at >= NOW() - INTERVAL '48 hours'
    """, (team_name,))
    total_impact = float(cur.fetchone()[0] or 0)
    return clamp(total_impact * 0.005, -0.08, 0.08)


def run_predictions():
    conn = db()
    cur = conn.cursor()
    ensure_money_tables(cur)
    rebuild_model_adjustments(cur)
    adjustments = load_adjustments(cur)
    bankroll, max_stake_pct, kelly_fraction = get_bankroll_settings(cur)

    cur.execute("""
        SELECT id, home_team, away_team, home_odds, away_odds
        FROM games
        WHERE home_odds IS NOT NULL AND away_odds IS NOT NULL
    """)
    rows = cur.fetchall()

    for game_id, home, away, h_odds, a_odds in rows:
        home_implied = implied_probability(h_odds)
        away_implied = implied_probability(a_odds)
        if home_implied is None or away_implied is None:
            continue

        bookmaker_total = home_implied + away_implied
        fair_home = home_implied / bookmaker_total
        fair_away = away_implied / bookmaker_total
        home_signal_adj = get_team_signal_impact(cur, home)
        away_signal_adj = get_team_signal_impact(cur, away)

        h_prob = clamp(fair_home + 0.03 + (home_signal_adj - away_signal_adj))
        a_prob = clamp(1 - h_prob)
        edge_home = h_prob - fair_home
        edge_away = a_prob - fair_away

        rec = "NO BET"
        selected_team = None
        selected_odds = None
        model_team_prob = None
        fair_team_prob = None
        alert_edge = None

        if edge_home >= MIN_EDGE:
            selected_team, selected_odds, model_team_prob, fair_team_prob, alert_edge = home, h_odds, h_prob, fair_home, edge_home
        elif edge_away >= MIN_EDGE:
            selected_team, selected_odds, model_team_prob, fair_team_prob, alert_edge = away, a_odds, a_prob, fair_away, edge_away

        if selected_team:
            bucket = odds_bucket(selected_odds)
            odds_adj = get_adjustment(adjustments, "odds_bucket", bucket)
            model_team_prob = clamp(model_team_prob - odds_adj["probability_penalty"])
            alert_edge = alert_edge - odds_adj["edge_penalty"]

            signal_level, risk_level, reason = classify_signal(alert_edge, float(selected_odds), home_signal_adj, away_signal_adj)
            signal_adj = get_adjustment(adjustments, "signal_level", signal_level)
            model_team_prob = clamp(model_team_prob - signal_adj["probability_penalty"])
            alert_edge = alert_edge - signal_adj["edge_penalty"]
            signal_level, risk_level, reason = classify_signal(alert_edge, float(selected_odds), home_signal_adj, away_signal_adj)

            if signal_level != "PASS":
                rec = f"{signal_level}: {selected_team} moneyline"
                stake_pct, stake_amount = calculate_kelly_stake(selected_odds, model_team_prob, bankroll, max_stake_pct, kelly_fraction)
                learn_note = ""
                if odds_adj["reason"] or signal_adj["reason"]:
                    learn_note = f" Auto-learning: {odds_adj['reason']} {signal_adj['reason']}"
                reason = f"{reason} Model {round(model_team_prob*100,1)}% vs market {round(fair_team_prob*100,1)}%. Adjusted edge {round(alert_edge*100,1)}%. Odds bucket {bucket}.{learn_note}"
                save_bet_recommendation(cur, game_id, selected_team, rec, float(selected_odds), model_team_prob, fair_team_prob, alert_edge, bankroll, stake_pct, stake_amount, signal_level, risk_level, reason)

                if stake_amount > 0 and not already_alerted(cur, game_id, rec):
                    message = f"""
🏀 <b>{signal_level}</b>

<b>{away}</b> @ <b>{home}</b>
Pick: <b>{selected_team} moneyline</b>
Odds: <b>{selected_odds}</b>
Risk: <b>{risk_level}</b>

Model after learning: <b>{round(model_team_prob * 100, 1)}%</b>
Market: <b>{round(fair_team_prob * 100, 1)}%</b>
Adjusted edge: <b>{round(alert_edge * 100, 1)}%</b>
Suggested stake: <b>${stake_amount}</b> ({round(stake_pct * 100, 2)}%)

Reason: {reason}

⚠️ You decide manually. No automatic bet placed.
"""
                    if send_telegram_alert(message):
                        mark_alerted(cur, game_id, rec, round(alert_edge, 4), stake_amount)

        cur.execute("""
            INSERT INTO predictions (game_id, win_prob_home, win_prob_away, edge_home, recommendation)
            VALUES (%s,%s,%s,%s,%s)
        """, (game_id, round(h_prob, 4), round(a_prob, 4), round(edge_home, 4), rec))

    conn.commit()
    cur.close()
    conn.close()
    print(f"Predictions done for {len(rows)} games", flush=True)
