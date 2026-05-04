import os
import requests
import psycopg2
from dotenv import load_dotenv

load_dotenv()

ODDS_API_KEY = os.getenv("ODDS_API_KEY")
DATABASE_URL = os.getenv("DATABASE_URL")


def db():
    return psycopg2.connect(DATABASE_URL)


def fetch_odds():
    url = "https://api.the-odds-api.com/v4/sports/basketball_nba/odds"
    params = {
        "apiKey": ODDS_API_KEY,
        "regions": "us",
        "markets": "h2h"
    }
    res = requests.get(url, params=params)
    return res.json() if res.status_code == 200 else []


def collect_odds():
    games = fetch_odds()
    if not games:
        print("No data")
        return

    conn = db()
    cur = conn.cursor()

    for g in games:
        home = g.get("home_team")
        away = g.get("away_team")
        time = g.get("commence_time")

        try:
            odds = g["bookmakers"][0]["markets"][0]["outcomes"]
            home_odds = odds[0]["price"]
            away_odds = odds[1]["price"]
        except:
            continue

        cur.execute("""
            INSERT INTO games (home_team, away_team, game_time, home_odds, away_odds)
            VALUES (%s,%s,%s,%s,%s)
        """, (home, away, time, home_odds, away_odds))

    conn.commit()
    cur.close()
    conn.close()

    print(f"Saved {len(games)} games")
