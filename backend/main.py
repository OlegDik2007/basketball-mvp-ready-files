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
            "impact_score": float(r[5]) if r[5] else 0
        }
        for r in rows
    ]
