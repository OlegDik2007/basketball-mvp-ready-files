import os
from datetime import datetime, timezone
from typing import List, Optional

import psycopg2
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
API_KEY = os.getenv("API_KEY", "change-me")
ALLOWED_ORIGINS = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "http://localhost:8000").split(",") if o.strip()]
DEFAULT_BANKROLL = float(os.getenv("DEFAULT_BANKROLL", "1000"))
MAX_STAKE_PCT = float(os.getenv("MAX_STAKE_PCT", "0.03"))

app = FastAPI(title="Basketball Analytics Private MVP", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-API-Key"],
)


def require_key(x_api_key: Optional[str] = Header(default=None)):
    if API_KEY and API_KEY != "change-me" and x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")


def conn():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not configured")
    return psycopg2.connect(DATABASE_URL)


def init_db():
    with conn() as c, c.cursor() as cur:
        cur.execute("""
        CREATE TABLE IF NOT EXISTS games (
            id SERIAL PRIMARY KEY,
            home_team TEXT NOT NULL,
            away_team TEXT NOT NULL,
            game_time TIMESTAMP,
            home_odds NUMERIC,
            away_odds NUMERIC,
            status TEXT DEFAULT 'scheduled',
            home_score INT,
            away_score INT,
            source TEXT DEFAULT 'manual',
            is_anomaly BOOLEAN DEFAULT false,
            anomaly_reason TEXT,
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW(),
            UNIQUE(home_team, away_team, game_time)
        );
        CREATE TABLE IF NOT EXISTS data_anomalies (
            id SERIAL PRIMARY KEY,
            anomaly_type TEXT,
            source TEXT,
            home_team TEXT,
            away_team TEXT,
            payload TEXT,
            reason TEXT,
            severity TEXT DEFAULT 'medium',
            created_at TIMESTAMP DEFAULT NOW()
        );
        CREATE TABLE IF NOT EXISTS news_signals (
            id SERIAL PRIMARY KEY,
            league TEXT DEFAULT 'NBA',
            team TEXT,
            player TEXT,
            signal_type TEXT,
            signal_text TEXT,
            impact_score NUMERIC DEFAULT 0,
            source TEXT DEFAULT 'manual',
            created_at TIMESTAMP DEFAULT NOW()
        );
        CREATE TABLE IF NOT EXISTS bet_recommendations (
            id SERIAL PRIMARY KEY,
            game_id INT REFERENCES games(id) ON DELETE CASCADE,
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
            UNIQUE(game_id, recommendation)
        );
        CREATE INDEX IF NOT EXISTS idx_games_time ON games(game_time DESC);
        CREATE INDEX IF NOT EXISTS idx_bets_status ON bet_recommendations(status);
        """)


@app.on_event("startup")
def startup():
    init_db()


class GameImport(BaseModel):
    home_team: str
    away_team: str
    game_time: Optional[datetime] = None
    home_odds: Optional[float] = Field(default=None, gt=1.0)
    away_odds: Optional[float] = Field(default=None, gt=1.0)
    status: str = "scheduled"
    source: str = "manual"

class GamesPayload(BaseModel):
    games: List[GameImport]

class ResultImport(BaseModel):
    home_team: str
    away_team: str
    game_time: Optional[datetime] = None
    home_score: int = Field(ge=0, le=250)
    away_score: int = Field(ge=0, le=250)
    status: str = "final"
    source: str = "manual"

class ResultsPayload(BaseModel):
    results: List[ResultImport]

class NewsSignal(BaseModel):
    league: str = "NBA"
    team: Optional[str] = None
    player: Optional[str] = None
    signal_type: str = "general"
    signal_text: str
    impact_score: float = Field(default=0, ge=-10, le=10)
    source: str = "manual"

class BetResult(BaseModel):
    status: str


def odds_anomalies(home, away):
    reasons = []
    if home is None or away is None:
        return ["missing odds"]
    implied = (1 / home) + (1 / away)
    if home > 15 or away > 15:
        reasons.append("odds too high; likely American odds not converted")
    if implied < 0.85 or implied > 1.25:
        reasons.append(f"unrealistic implied probability sum {implied:.3f}")
    return reasons


def model_for_game(home_odds, away_odds, home_team, away_team):
    # Simple private MVP model: market baseline + small home/team-news placeholder.
    h_imp, a_imp = 1 / float(home_odds), 1 / float(away_odds)
    total = h_imp + a_imp
    home_prob = min(max((h_imp / total) + 0.015, 0.03), 0.97)
    away_prob = 1 - home_prob
    home_edge = home_prob - h_imp
    away_edge = away_prob - a_imp
    if home_edge >= away_edge:
        team, odds, prob, edge = home_team, float(home_odds), home_prob, home_edge
    else:
        team, odds, prob, edge = away_team, float(away_odds), away_prob, away_edge
    signal = "STRONG BET" if edge >= 0.06 else "MEDIUM BET" if edge >= 0.025 else "PASS"
    stake_pct = 0 if signal == "PASS" else min(MAX_STAKE_PCT, max(0.005, edge / 3))
    return {
        "team": team,
        "odds": odds,
        "prob": prob,
        "fair": 1 / odds,
        "edge": edge,
        "signal": signal,
        "stake_pct": stake_pct,
        "stake": round(DEFAULT_BANKROLL * stake_pct, 2),
        "reason": f"Model probability {prob:.1%}; market implied {1/odds:.1%}; edge {edge:.1%}.",
    }


def save_recommendation(cur, game_id, pick):
    cur.execute("""
        INSERT INTO bet_recommendations
        (game_id, selected_team, recommendation, selected_odds, model_probability, fair_probability, edge, bankroll, stake_pct, stake_amount, signal_level, risk_level, reason)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (game_id, recommendation) DO UPDATE SET
          selected_odds=EXCLUDED.selected_odds, model_probability=EXCLUDED.model_probability,
          fair_probability=EXCLUDED.fair_probability, edge=EXCLUDED.edge, stake_pct=EXCLUDED.stake_pct,
          stake_amount=EXCLUDED.stake_amount, signal_level=EXCLUDED.signal_level, reason=EXCLUDED.reason
    """, (game_id, pick["team"], pick["team"], pick["odds"], pick["prob"], pick["fair"], pick["edge"], DEFAULT_BANKROLL, pick["stake_pct"], pick["stake"], pick["signal"], "MEDIUM" if pick["signal"] != "PASS" else "HIGH", pick["reason"]))


@app.get("/")
def root():
    return {"status": "ok", "dashboard": "/dashboard", "docs": "/docs"}

@app.get("/health")
def health():
    return {"status": "ok", "time": datetime.now(timezone.utc).isoformat()}

@app.post("/games/import", dependencies=[Depends(require_key)])
def import_games(payload: GamesPayload):
    saved = anomalies = 0
    with conn() as c, c.cursor() as cur:
        for g in payload.games:
            reasons = odds_anomalies(g.home_odds, g.away_odds)
            is_bad = bool(reasons)
            if is_bad:
                anomalies += 1
                cur.execute("INSERT INTO data_anomalies (anomaly_type,source,home_team,away_team,payload,reason,severity) VALUES (%s,%s,%s,%s,%s,%s,%s)", ("odds_import", g.source, g.home_team, g.away_team, g.model_dump_json(), "; ".join(reasons), "high"))
            cur.execute("""
                INSERT INTO games (home_team,away_team,game_time,home_odds,away_odds,status,source,is_anomaly,anomaly_reason)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (home_team, away_team, game_time) DO UPDATE SET
                  home_odds=EXCLUDED.home_odds, away_odds=EXCLUDED.away_odds, status=EXCLUDED.status,
                  source=EXCLUDED.source, is_anomaly=EXCLUDED.is_anomaly, anomaly_reason=EXCLUDED.anomaly_reason, updated_at=NOW()
                RETURNING id
            """, (g.home_team, g.away_team, g.game_time, None if is_bad else g.home_odds, None if is_bad else g.away_odds, "anomaly" if is_bad else g.status, g.source, is_bad, "; ".join(reasons)))
            game_id = cur.fetchone()[0]
            if not is_bad and g.home_odds and g.away_odds:
                save_recommendation(cur, game_id, model_for_game(g.home_odds, g.away_odds, g.home_team, g.away_team))
            saved += 1
    return {"status": "ok", "saved": saved, "anomalies": anomalies}

@app.post("/results/import", dependencies=[Depends(require_key)])
def import_results(payload: ResultsPayload):
    saved = 0
    with conn() as c, c.cursor() as cur:
        for r in payload.results:
            cur.execute("""
                UPDATE games SET home_score=%s, away_score=%s, status=%s, updated_at=NOW()
                WHERE LOWER(home_team)=LOWER(%s) AND LOWER(away_team)=LOWER(%s)
                  AND (%s::timestamp IS NULL OR DATE(game_time)=DATE(%s::timestamp))
            """, (r.home_score, r.away_score, r.status, r.home_team, r.away_team, r.game_time, r.game_time))
            saved += cur.rowcount
    return {"status": "ok", "updated": saved}

@app.post("/news-signal", dependencies=[Depends(require_key)])
def news_signal(signal: NewsSignal):
    with conn() as c, c.cursor() as cur:
        cur.execute("INSERT INTO news_signals (league,team,player,signal_type,signal_text,impact_score,source) VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id", (signal.league, signal.team, signal.player, signal.signal_type, signal.signal_text, signal.impact_score, signal.source))
        return {"status": "saved", "id": cur.fetchone()[0]}

@app.post("/bets/{bet_id}/result", dependencies=[Depends(require_key)])
def settle_bet(bet_id: int, result: BetResult):
    status = result.status.lower()
    if status not in {"won", "lost", "push", "void", "open"}:
        raise HTTPException(400, "status must be won/lost/push/void/open")
    with conn() as c, c.cursor() as cur:
        cur.execute("SELECT stake_amount, selected_odds FROM bet_recommendations WHERE id=%s", (bet_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "bet not found")
        stake, odds = float(row[0] or 0), float(row[1] or 0)
        profit = stake * (odds - 1) if status == "won" else -stake if status == "lost" else 0
        cur.execute("UPDATE bet_recommendations SET status=%s,result_profit=%s,settled_at=CASE WHEN %s='open' THEN NULL ELSE NOW() END WHERE id=%s", (status, round(profit, 2), status, bet_id))
    return {"status": "updated", "profit": round(profit, 2)}


def fetch_all(sql, params=()):
    with conn() as c, c.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()

@app.get("/games")
def games():
    data = fetch_all("SELECT id,home_team,away_team,game_time,home_odds,away_odds,status,home_score,away_score,is_anomaly,anomaly_reason FROM games ORDER BY COALESCE(game_time, created_at) DESC LIMIT 100")
    return [{"id":r[0],"home_team":r[1],"away_team":r[2],"game_time":str(r[3]) if r[3] else None,"home_odds":float(r[4]) if r[4] else None,"away_odds":float(r[5]) if r[5] else None,"status":r[6],"home_score":r[7],"away_score":r[8],"is_anomaly":r[9],"anomaly_reason":r[10]} for r in data]

@app.get("/bets")
def bets():
    data = fetch_all("SELECT b.id,g.home_team,g.away_team,b.recommendation,b.selected_odds,b.model_probability,b.edge,b.stake_amount,b.signal_level,b.status,b.result_profit,b.reason FROM bet_recommendations b JOIN games g ON g.id=b.game_id ORDER BY b.id DESC LIMIT 100")
    return [{"id":r[0],"home_team":r[1],"away_team":r[2],"recommendation":r[3],"selected_odds":float(r[4]),"model_probability":float(r[5]),"edge":float(r[6]),"stake_amount":float(r[7]),"signal_level":r[8],"status":r[9],"result_profit":float(r[10] or 0),"reason":r[11]} for r in data]

@app.get("/top-bets")
def top_bets():
    return [b for b in bets() if b["status"] == "open" and b["signal_level"] != "PASS"][:3]

@app.get("/anomalies")
def anomalies():
    data = fetch_all("SELECT id,anomaly_type,source,home_team,away_team,reason,severity,created_at FROM data_anomalies ORDER BY id DESC LIMIT 100")
    return [{"id":r[0],"type":r[1],"source":r[2],"home_team":r[3],"away_team":r[4],"reason":r[5],"severity":r[6],"created_at":str(r[7])} for r in data]

@app.get("/performance")
def performance():
    r = fetch_all("SELECT COUNT(*) FILTER (WHERE status='open'), COUNT(*) FILTER (WHERE status!='open'), COUNT(*) FILTER (WHERE status='won'), COUNT(*) FILTER (WHERE status='lost'), COALESCE(SUM(stake_amount) FILTER (WHERE status!='open'),0), COALESCE(SUM(result_profit),0) FROM bet_recommendations")[0]
    risked, profit = float(r[4]), float(r[5])
    return {"open_bets":int(r[0]),"settled_bets":int(r[1]),"wins":int(r[2]),"losses":int(r[3]),"risked":round(risked,2),"profit":round(profit,2),"roi":round((profit/risked*100) if risked else 0,2)}

@app.get("/dashboard", response_class=HTMLResponse)
def dashboard():
    return """
<!doctype html><html><head><meta name='viewport' content='width=device-width,initial-scale=1'><title>Basketball Analytics</title><style>body{font-family:Arial;background:#0f172a;color:#e5e7eb;margin:0}header{background:#111827;padding:24px}.wrap{padding:20px;max-width:1200px;margin:auto}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}.card,table{background:#111827;border:1px solid #334155;border-radius:14px;padding:14px}table{width:100%;border-collapse:collapse;margin-top:18px}th,td{padding:10px;border-bottom:1px solid #334155;text-align:left}.pos{color:#22c55e}.neg{color:#f87171}.pill{font-weight:700;color:#facc15}</style></head><body><header><h1>🏀 Private Basketball Analytics</h1><div>Games • Top bets • Performance • Anomalies</div></header><div class='wrap'><div class='grid'><div class='card'>Games<br><b id='games'>0</b></div><div class='card'>Top Bets<br><b id='top'>0</b></div><div class='card'>Profit<br><b id='profit'>$0</b></div><div class='card'>ROI<br><b id='roi'>0%</b></div></div><h2>Top Bets</h2><table><thead><tr><th>Game</th><th>Pick</th><th>Signal</th><th>Odds</th><th>Edge</th><th>Stake</th><th>Reason</th></tr></thead><tbody id='rows'></tbody></table><h2>Anomalies</h2><table><tbody id='anoms'></tbody></table></div><script>const money=x=>'$'+Number(x||0).toFixed(2);const pct=x=>(Number(x||0)*100).toFixed(1)+'%';async function j(u){return (await fetch(u)).json()}async function load(){const [g,t,p,a]=await Promise.all([j('/games'),j('/top-bets'),j('/performance'),j('/anomalies')]);games.textContent=g.length;top.textContent=t.length;profit.textContent=money(p.profit);roi.textContent=Number(p.roi||0).toFixed(1)+'%';rows.innerHTML=t.map(b=>`<tr><td>${b.away_team} @ ${b.home_team}</td><td>${b.recommendation}</td><td class='pill'>${b.signal_level}</td><td>${b.selected_odds}</td><td>${pct(b.edge)}</td><td>${money(b.stake_amount)}</td><td>${b.reason}</td></tr>`).join('')||'<tr><td>No current value bets</td></tr>';anoms.innerHTML=a.map(x=>`<tr><td>${x.away_team||'-'} @ ${x.home_team||'-'}</td><td class='neg'>${x.reason}</td><td>${x.created_at}</td></tr>`).join('')||'<tr><td>No anomalies</td></tr>'}load();setInterval(load,30000)</script></body></html>
"""
