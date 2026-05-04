import os
import psycopg2
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from pydantic import BaseModel
from typing import Optional

load_dotenv()

app = FastAPI(title="Basketball Betting Analytics MVP")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def db():
    return psycopg2.connect(os.getenv("DATABASE_URL"))


@app.get("/")
def root():
    return {
        "message": "Basketball Betting Analytics API",
        "endpoints": [
            "/health",
            "/games",
            "/predictions",
            "/value-bets",
            "/news-signals"
        ]
    }


@app.get("/health")
def health():
    return {"status": "ok"}


class NewsSignal(BaseModel):
    league: Optional[str] = "NBA"
    team: Optional[str] = None
    player: Optional[str] = None
    signal_type: Optional[str] = None
    signal_text: Optional[str] = None
    impact_score: Optional[float] = 0
    source: Optional[str] = "openclaw"


@app.post("/news-signal")
def create_news_signal(signal: NewsSignal):
    conn = db()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO news_signals (
            league, team, player, signal_type, signal_text, impact_score, source
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        RETURNING id
    """, (
        signal.league,
        signal.team,
        signal.player,
        signal.signal_type,
        signal.signal_text,
        signal.impact_score,
        signal.source
    ))

    signal_id = cur.fetchone()[0]

    conn.commit()
    cur.close()
    conn.close()

    return {"status": "saved", "id": signal_id}


@app.get("/news-signals")
def get_news_signals():
    conn = db()
    cur = conn.cursor()

    cur.execute("""
        SELECT id, team, player, signal_type, signal_text, impact_score
        FROM news_signals
        ORDER BY created_at DESC
        LIMIT 50
    """)

    rows = cur.fetchall()

    cur.close()
    conn.close()

    return [
        {
            "id": r[0],
            "team": r[1],
            "player": r[2],
            "signal_type": r[3],
            "signal_text": r[4],
            "impact_score": float(r[5]) if r[5] is not None else 0
        }
        for r in rows
    ]


@app.get("/games")
def get_games():
    conn = db()
    cur = conn.cursor()

    cur.execute("""
        SELECT id, home_team, away_team, game_time, home_odds, away_odds
        FROM games
        ORDER BY id DESC
        LIMIT 100
    """)

    rows = cur.fetchall()
    cur.close()
    conn.close()

    return [
        {
            "id": r[0],
            "home_team": r[1],
            "away_team": r[2],
            "game_time": str(r[3]) if r[3] else None,
            "home_odds": float(r[4]) if r[4] is not None else None,
            "away_odds": float(r[5]) if r[5] is not None else None,
        }
        for r in rows
    ]


@app.get("/predictions")
def get_predictions():
    conn = db()
    cur = conn.cursor()

    cur.execute("""
        SELECT
            p.id,
            p.game_id,
            g.home_team,
            g.away_team,
            g.game_time,
            g.home_odds,
            g.away_odds,
            p.win_prob_home,
            p.win_prob_away,
            p.edge_home,
            p.recommendation
        FROM predictions p
        LEFT JOIN games g ON g.id = p.game_id
        ORDER BY p.id DESC
        LIMIT 100
    """)

    rows = cur.fetchall()
    cur.close()
    conn.close()

    return [
        {
            "id": r[0],
            "game_id": r[1],
            "home_team": r[2],
            "away_team": r[3],
            "game_time": str(r[4]) if r[4] else None,
            "home_odds": float(r[5]) if r[5] is not None else None,
            "away_odds": float(r[6]) if r[6] is not None else None,
            "win_prob_home": float(r[7]) if r[7] is not None else None,
            "win_prob_away": float(r[8]) if r[8] is not None else None,
            "edge_home": float(r[9]) if r[9] is not None else None,
            "recommendation": r[10],
        }
        for r in rows
    ]


@app.get("/value-bets")
def get_value_bets():
    conn = db()
    cur = conn.cursor()

    cur.execute("""
        SELECT
            p.id,
            p.game_id,
            g.home_team,
            g.away_team,
            g.game_time,
            g.home_odds,
            g.away_odds,
            p.win_prob_home,
            p.win_prob_away,
            p.edge_home,
            p.recommendation
        FROM predictions p
        LEFT JOIN games g ON g.id = p.game_id
        WHERE p.recommendation IS NOT NULL
        AND p.recommendation != 'NO BET'
        ORDER BY p.edge_home DESC
        LIMIT 50
    """)

    rows = cur.fetchall()
    cur.close()
    conn.close()

    return [
        {
            "id": r[0],
            "game_id": r[1],
            "home_team": r[2],
            "away_team": r[3],
            "game_time": str(r[4]) if r[4] else None,
            "home_odds": float(r[5]) if r[5] is not None else None,
            "away_odds": float(r[6]) if r[6] is not None else None,
            "win_prob_home": float(r[7]) if r[7] is not None else None,
            "win_prob_away": float(r[8]) if r[8] is not None else None,
            "edge_home": float(r[9]) if r[9] is not None else None,
            "recommendation": r[10],
        }
        for r in rows
    ]
