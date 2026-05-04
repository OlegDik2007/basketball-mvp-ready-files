import os
import psycopg2
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
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
        "dashboard": "/dashboard",
        "endpoints": [
            "/health",
            "/games",
            "/predictions",
            "/value-bets",
            "/news-signals"
        ]
    }


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard():
    return """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Basketball Betting Analytics</title>
  <style>
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: Arial, sans-serif;
      background: #0f172a;
      color: #e5e7eb;
    }
    header {
      padding: 24px;
      background: linear-gradient(135deg, #1e3a8a, #111827);
      border-bottom: 1px solid #334155;
    }
    h1 { margin: 0 0 8px; font-size: 28px; }
    .sub { color: #cbd5e1; }
    .wrap { padding: 24px; max-width: 1200px; margin: 0 auto; }
    .grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
      gap: 16px;
      margin-bottom: 24px;
    }
    .card {
      background: #111827;
      border: 1px solid #334155;
      border-radius: 16px;
      padding: 18px;
      box-shadow: 0 10px 24px rgba(0,0,0,.25);
    }
    .metric { font-size: 30px; font-weight: 700; margin-top: 6px; }
    .label { color: #94a3b8; font-size: 13px; }
    .section-title { margin: 28px 0 12px; font-size: 20px; }
    table {
      width: 100%;
      border-collapse: collapse;
      background: #111827;
      border: 1px solid #334155;
      border-radius: 14px;
      overflow: hidden;
    }
    th, td {
      padding: 12px;
      border-bottom: 1px solid #1f2937;
      text-align: left;
      font-size: 14px;
    }
    th { color: #cbd5e1; background: #1f2937; }
    .bet { color: #22c55e; font-weight: 700; }
    .no { color: #94a3b8; }
    .negative { color: #f87171; }
    .positive { color: #22c55e; }
    .btn {
      display: inline-block;
      padding: 10px 14px;
      border-radius: 10px;
      border: 1px solid #475569;
      color: white;
      background: #1d4ed8;
      cursor: pointer;
      margin-top: 14px;
    }
    .small { color: #94a3b8; font-size: 12px; }
    @media (max-width: 700px) {
      table { display: block; overflow-x: auto; white-space: nowrap; }
    }
  </style>
</head>
<body>
  <header>
    <h1>🏀 Basketball Betting Analytics</h1>
    <div class="sub">Odds + OpenClaw signals + prediction engine</div>
  </header>

  <div class="wrap">
    <button class="btn" onclick="loadData()">Refresh Dashboard</button>
    <div class="small" id="updated">Loading...</div>

    <div class="grid">
      <div class="card">
        <div class="label">Games</div>
        <div class="metric" id="gamesCount">0</div>
      </div>
      <div class="card">
        <div class="label">Predictions</div>
        <div class="metric" id="predictionsCount">0</div>
      </div>
      <div class="card">
        <div class="label">Value Bets</div>
        <div class="metric" id="valueCount">0</div>
      </div>
      <div class="card">
        <div class="label">OpenClaw Signals</div>
        <div class="metric" id="signalsCount">0</div>
      </div>
    </div>

    <h2 class="section-title">🔥 Value Bets</h2>
    <table>
      <thead>
        <tr>
          <th>Game</th>
          <th>Odds</th>
          <th>Home Win %</th>
          <th>Edge</th>
          <th>Recommendation</th>
        </tr>
      </thead>
      <tbody id="valueBetsTable"></tbody>
    </table>

    <h2 class="section-title">📊 Latest Predictions</h2>
    <table>
      <thead>
        <tr>
          <th>Game</th>
          <th>Home Odds</th>
          <th>Away Odds</th>
          <th>Home Win %</th>
          <th>Away Win %</th>
          <th>Edge</th>
          <th>Recommendation</th>
        </tr>
      </thead>
      <tbody id="predictionsTable"></tbody>
    </table>

    <h2 class="section-title">🧠 OpenClaw News Signals</h2>
    <table>
      <thead>
        <tr>
          <th>Team</th>
          <th>Player</th>
          <th>Type</th>
          <th>Signal</th>
          <th>Impact</th>
        </tr>
      </thead>
      <tbody id="signalsTable"></tbody>
    </table>
  </div>

<script>
function pct(x) {
  if (x === null || x === undefined) return '-';
  return (x * 100).toFixed(1) + '%';
}
function edge(x) {
  if (x === null || x === undefined) return '-';
  return (x * 100).toFixed(1) + '%';
}
function clsImpact(x) {
  return Number(x) < 0 ? 'negative' : 'positive';
}
async function fetchJson(path) {
  const res = await fetch(path);
  if (!res.ok) throw new Error(path + ' failed');
  return await res.json();
}
async function loadData() {
  try {
    const [games, predictions, valueBets, signals] = await Promise.all([
      fetchJson('/games'),
      fetchJson('/predictions'),
      fetchJson('/value-bets'),
      fetchJson('/news-signals')
    ]);

    document.getElementById('gamesCount').textContent = games.length;
    document.getElementById('predictionsCount').textContent = predictions.length;
    document.getElementById('valueCount').textContent = valueBets.length;
    document.getElementById('signalsCount').textContent = signals.length;
    document.getElementById('updated').textContent = 'Updated: ' + new Date().toLocaleString();

    document.getElementById('valueBetsTable').innerHTML = valueBets.length ? valueBets.map(p => `
      <tr>
        <td>${p.away_team || '-'} @ ${p.home_team || '-'}</td>
        <td>${p.home_odds || '-'} / ${p.away_odds || '-'}</td>
        <td>${pct(p.win_prob_home)}</td>
        <td class="${Number(p.edge_home) >= 0 ? 'positive' : 'negative'}">${edge(p.edge_home)}</td>
        <td class="bet">${p.recommendation}</td>
      </tr>
    `).join('') : '<tr><td colspan="5" class="no">No value bets right now</td></tr>';

    document.getElementById('predictionsTable').innerHTML = predictions.map(p => `
      <tr>
        <td>${p.away_team || '-'} @ ${p.home_team || '-'}</td>
        <td>${p.home_odds || '-'}</td>
        <td>${p.away_odds || '-'}</td>
        <td>${pct(p.win_prob_home)}</td>
        <td>${pct(p.win_prob_away)}</td>
        <td class="${Number(p.edge_home) >= 0 ? 'positive' : 'negative'}">${edge(p.edge_home)}</td>
        <td class="${p.recommendation && p.recommendation !== 'NO BET' ? 'bet' : 'no'}">${p.recommendation || '-'}</td>
      </tr>
    `).join('');

    document.getElementById('signalsTable').innerHTML = signals.map(s => `
      <tr>
        <td>${s.team || '-'}</td>
        <td>${s.player || '-'}</td>
        <td>${s.signal_type || '-'}</td>
        <td>${s.signal_text || '-'}</td>
        <td class="${clsImpact(s.impact_score)}">${s.impact_score}</td>
      </tr>
    `).join('');
  } catch (e) {
    document.getElementById('updated').textContent = 'Error: ' + e.message;
  }
}
loadData();
setInterval(loadData, 30000);
</script>
</body>
</html>
    """


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
