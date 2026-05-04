import os
import psycopg2
from datetime import date
from dotenv import load_dotenv

load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")


def db():
    return psycopg2.connect(DATABASE_URL)


def ensure_monthly_report_table(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS monthly_model_reports (
            id SERIAL PRIMARY KEY,
            report_month TEXT UNIQUE,
            total_games INT DEFAULT 0,
            rule_correct INT DEFAULT 0,
            ml_correct INT DEFAULT 0,
            rule_accuracy NUMERIC DEFAULT 0,
            ml_accuracy NUMERIC DEFAULT 0,
            winner TEXT,
            recommendation TEXT,
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)


def generate_monthly_report(report_month=None):
    """
    Creates monthly model comparison report from model_evaluations.
    report_month format: YYYY-MM. If omitted, current month is used.
    """
    if report_month is None:
        report_month = date.today().strftime("%Y-%m")

    conn = db()
    cur = conn.cursor()
    ensure_monthly_report_table(cur)

    cur.execute("""
        SELECT
            COUNT(*) AS total_games,
            COUNT(*) FILTER (WHERE rule_correct = true) AS rule_correct,
            COUNT(*) FILTER (WHERE ml_correct = true) AS ml_correct
        FROM model_evaluations
        WHERE TO_CHAR(evaluated_at, 'YYYY-MM') = %s
    """, (report_month,))

    row = cur.fetchone()
    total_games = int(row[0] or 0)
    rule_correct = int(row[1] or 0)
    ml_correct = int(row[2] or 0)

    rule_accuracy = round((rule_correct / total_games * 100), 2) if total_games else 0
    ml_accuracy = round((ml_correct / total_games * 100), 2) if total_games else 0

    if total_games < 30:
        winner = "NOT_ENOUGH_DATA"
        recommendation = "Keep showing both Rule and ML. Need at least 30 evaluated games for this month."
    elif ml_accuracy > rule_accuracy + 2:
        winner = "ML"
        recommendation = "ML performed better this month. Consider giving ML more weight, but keep Rule visible."
    elif rule_accuracy > ml_accuracy + 2:
        winner = "RULE"
        recommendation = "Rule model performed better this month. Keep ML as secondary until it improves."
    else:
        winner = "TIE"
        recommendation = "Both models are close. Use hybrid view and continue collecting data."

    cur.execute("""
        INSERT INTO monthly_model_reports (
            report_month,
            total_games,
            rule_correct,
            ml_correct,
            rule_accuracy,
            ml_accuracy,
            winner,
            recommendation,
            created_at
        )
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,NOW())
        ON CONFLICT (report_month)
        DO UPDATE SET
            total_games = EXCLUDED.total_games,
            rule_correct = EXCLUDED.rule_correct,
            ml_correct = EXCLUDED.ml_correct,
            rule_accuracy = EXCLUDED.rule_accuracy,
            ml_accuracy = EXCLUDED.ml_accuracy,
            winner = EXCLUDED.winner,
            recommendation = EXCLUDED.recommendation,
            created_at = NOW()
    """, (
        report_month,
        total_games,
        rule_correct,
        ml_correct,
        rule_accuracy,
        ml_accuracy,
        winner,
        recommendation,
    ))

    conn.commit()
    cur.close()
    conn.close()

    print("📊 Monthly Model Report")
    print(f"Month: {report_month}")
    print(f"Total games: {total_games}")
    print(f"Rule accuracy: {rule_accuracy}% ({rule_correct}/{total_games})")
    print(f"ML accuracy: {ml_accuracy}% ({ml_correct}/{total_games})")
    print(f"Winner: {winner}")
    print(f"Recommendation: {recommendation}")


if __name__ == "__main__":
    generate_monthly_report()
