import os
import psycopg2
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from dotenv import load_dotenv
from pydantic import BaseModel
from typing import Optional, List

load_dotenv()

app = FastAPI(title="Basketball Betting Analytics MVP")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


def db():
    return psycopg2.connect(os.getenv("DATABASE_URL"))


def ensure_core_tables(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS games (
            id SERIAL PRIMARY KEY,
            home_team TEXT,
            away_team TEXT,
            game_time TIMESTAMP,
            home_odds NUMERIC,
            away_odds NUMERIC,
            status TEXT DEFAULT 'scheduled',
            home_score INT,
            away_score INT,
            source TEXT DEFAULT 'openclaw',
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW()
        )
    """)
    for sql in [
        "ALTER TABLE games ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'scheduled'",
        "ALTER TABLE games ADD COLUMN IF NOT EXISTS home_score INT",
        "ALTER TABLE games ADD COLUMN IF NOT EXISTS away_score INT",
        "ALTER TABLE games ADD COLUMN IF NOT EXISTS source TEXT DEFAULT 'openclaw'",
        "ALTER TABLE games ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT NOW()",
        "ALTER TABLE games ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT NOW()",
    ]:
        cur.execute(sql)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS predictions (
            id SERIAL PRIMARY KEY,
            game_id INT,
            win_prob_home NUMERIC,
            win_prob_away NUMERIC,
            edge_home NUMERIC,
            recommendation TEXT,
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS news_signals (
            id SERIAL PRIMARY KEY,
            league TEXT DEFAULT 'NBA',
            team TEXT,
            player TEXT,
            signal_type TEXT,
            signal_text TEXT,
            impact_score NUMERIC,
            source TEXT DEFAULT 'openclaw',
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)


def ensure_tracking_tables(cur):
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


class NewsSignal(BaseModel):
    league: Optional[str] = "NBA"
    team: Optional[str] = None
    player: Optional[str] = None
    signal_type: Optional[str] = None
    signal_text: Optional[str] = None
    impact_score: Optional[float] = 0
    source: Optional[str] = "openclaw"


class GameImport(BaseModel):
    home_team: str
    away_team: str
    game_time: Optional[str] = None
    home_odds: Optional[float] = None
    away_odds: Optional[float] = None
    status: Optional[str] = "scheduled"
    source: Optional[str] = "openclaw"


class GamesImportPayload(BaseModel):
    games: List[GameImport]


class ResultImport(BaseModel):
    home_team: str
    away_team: str
    game_time: Optional[str] = None
    home_score: int
    away_score: int
    status: Optional[str] = "final"
    source: Optional[str] = "openclaw"


class ResultsImportPayload(BaseModel):
    results: List[ResultImport]


class BetResult(BaseModel):
    status: str


@app.get("/")
def root():
    return {
        "message": "Basketball Betting Analytics API",
        "dashboard": "/dashboard",
        "top3": "/top-bets",
        "accuracy": "/accuracy",
        "audit": "/audit",
        "openclaw_imports": ["POST /games/import", "POST /results/import", "POST /news-signal"]
    }


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard():
    return """
<!DOCTYPE html>
<html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Basketball Analytics Dashboard</title>
<style>
body{margin:0;font-family:Arial;background:#0f172a;color:#e5e7eb}header{padding:24px;background:#111827;border-bottom:1px solid #334155}.wrap{padding:24px;max-width:1250px;margin:auto}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:14px;margin:20px 0}.card{background:#111827;border:1px solid #334155;border-radius:16px;padding:16px}.label{color:#94a3b8;font-size:13px}.metric{font-size:28px;font-weight:700;margin-top:6px}.pos{color:#22c55e}.neg{color:#f87171}.no{color:#94a3b8}.bet{color:#22c55e;font-weight:700}.strong{color:#22c55e;font-weight:800}.medium{color:#facc15;font-weight:800}table{width:100%;border-collapse:collapse;background:#111827;border:1px solid #334155;border-radius:14px;overflow:hidden;margin-bottom:26px}th,td{padding:10px;border-bottom:1px solid #1f2937;text-align:left;font-size:14px}th{background:#1f2937;color:#cbd5e1}.btn{padding:9px 12px;border-radius:10px;border:1px solid #475569;background:#1d4ed8;color:white;cursor:pointer}.mini{font-size:12px;color:#94a3b8}@media(max-width:700px){table{display:block;overflow-x:auto;white-space:nowrap}}
</style></head>
<body><header><h1>🏀 Basketball Betting Analytics</h1><div>Top 3 daily signals • Audit accuracy • OpenClaw + Neon</div></header>
<div class="wrap">
<button class="btn" onclick="loadData()">Refresh</button><div class="mini" id="updated">Loading...</div>
<div class="grid">
<div class="card"><div class="label">Games</div><div class="metric" id="gamesCount">0</div></div>
<div class="card"><div class="label">Top Bets Today</div><div class="metric" id="topCount">0</div></div>
<div class="card"><div class="label">Accuracy</div><div class="metric" id="accuracy">0%</div></div>
<div class="card"><div class="label">Correct / Graded</div><div class="metric" id="correctCount">0/0</div></div>
<div class="card"><div class="label">Profit</div><div class="metric" id="profit">$0</div></div>
<div class="card"><div class="label">ROI</div><div class="metric" id="roi">0%</div></div>
</div>
<h2>🏆 Top 3 Bets Today</h2><table><thead><tr><th>Rank</th><th>Game</th><th>Signal</th><th>Pick</th><th>Odds</th><th>Edge</th><th>Stake</th><th>Reason</th></tr></thead><tbody id="topBetsTable"></tbody></table>
<h2>✅ Accuracy Audit: picks vs real winner</h2><table><thead><tr><th>ID</th><th>Game</th><th>Pick</th><th>Score</th><th>Actual Winner</th><th>Matched?</th><th>Signal</th><th>Edge</th></tr></thead><tbody id="auditTable"></tbody></table>
<h2>💰 Bet Tracking</h2><table><thead><tr><th>ID</th><th>Game</th><th>Pick</th><th>Odds</th><th>Edge</th><th>Stake</th><th>Status</th><th>Profit</th><th>Actions</th></tr></thead><tbody id="betsTable"></tbody></table>
<h2>🧠 Signals</h2><table><thead><tr><th>Team</th><th>Player</th><th>Type</th><th>Signal</th><th>Impact</th></tr></thead><tbody id="signalsTable"></tbody></table>
</div>
<script>
function pct(x){return x==null?'-':(x*100).toFixed(1)+'%'}function money(x){return '$'+Number(x||0).toFixed(2)}function edge(x){return x==null?'-':(x*100).toFixed(1)+'%'}function cls(x){return Number(x)>=0?'pos':'neg'}async function j(p){const r=await fetch(p);if(!r.ok)throw new Error(p);return r.json()}async function settle(id,status){await fetch('/bets/'+id+'/result',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({status})});loadData()}
async function loadData(){try{const [games,top,sigs,bets,perf,acc,audit]=await Promise.all([j('/games'),j('/top-bets'),j('/news-signals'),j('/bets'),j('/performance'),j('/accuracy'),j('/audit')]);document.getElementById('gamesCount').textContent=games.length;document.getElementById('topCount').textContent=top.length;document.getElementById('profit').textContent=money(perf.profit);document.getElementById('profit').className='metric '+cls(perf.profit);document.getElementById('roi').textContent=Number(perf.roi||0).toFixed(1)+'%';document.getElementById('roi').className='metric '+cls(perf.roi);document.getElementById('accuracy').textContent=Number(acc.accuracy_pct||0).toFixed(1)+'%';document.getElementById('accuracy').className='metric '+cls(acc.accuracy_pct);document.getElementById('correctCount').textContent=`${acc.correct}/${acc.graded}`;document.getElementById('updated').textContent='Updated: '+new Date().toLocaleString();document.getElementById('topBetsTable').innerHTML=top.length?top.map((b,i)=>`<tr><td>#${i+1}</td><td>${b.away_team||'-'} @ ${b.home_team||'-'}</td><td class='${b.signal_level==='STRONG BET'?'strong':'medium'}'>${b.signal_level}</td><td>${b.recommendation}</td><td>${b.selected_odds}</td><td class='${cls(b.edge)}'>${edge(b.edge)}</td><td>${money(b.stake_amount)}</td><td>${b.reason||''}</td></tr>`).join(''):'<tr><td colspan=8 class=no>No top bets today</td></tr>';document.getElementById('auditTable').innerHTML=audit.length?audit.map(a=>`<tr><td>${a.bet_id}</td><td>${a.away_team||'-'} @ ${a.home_team||'-'}</td><td>${a.selected_team}</td><td>${a.away_score??'-'} - ${a.home_score??'-'}</td><td>${a.actual_winner||'-'}</td><td class='${a.is_correct?'pos':'neg'}'>${a.is_correct===null?'Pending':(a.is_correct?'YES':'NO')}</td><td>${a.signal_level||'-'}</td><td>${edge(a.edge)}</td></tr>`).join(''):'<tr><td colspan=8 class=no>No graded picks yet</td></tr>';document.getElementById('betsTable').innerHTML=bets.length?bets.map(b=>`<tr><td>${b.id}</td><td>${b.away_team||'-'} @ ${b.home_team||'-'}</td><td class='bet'>${b.recommendation}</td><td>${b.selected_odds}</td><td class='${cls(b.edge)}'>${edge(b.edge)}</td><td>${money(b.stake_amount)}</td><td>${b.status}</td><td class='${cls(b.result_profit)}'>${money(b.result_profit)}</td><td><button onclick="settle(${b.id},'won')">Won</button> <button onclick="settle(${b.id},'lost')">Lost</button> <button onclick="settle(${b.id},'push')">Push</button></td></tr>`).join(''):'<tr><td colspan=9 class=no>No tracked bets yet</td></tr>';document.getElementById('signalsTable').innerHTML=sigs.map(s=>`<tr><td>${s.team||'-'}</td><td>${s.player||'-'}</td><td>${s.signal_type||'-'}</td><td>${s.signal_text||'-'}</td><td class='${cls(s.impact_score)}'>${s.impact_score}</td></tr>`).join('')}catch(e){document.getElementById('updated').textContent='Error: '+e.message}}
loadData();setInterval(loadData,30000);
</script></body></html>
    """


@app.post("/games/import")
def import_games(payload: GamesImportPayload):
    conn = db(); cur = conn.cursor(); ensure_core_tables(cur); saved = 0
    for g in payload.games:
        cur.execute("""SELECT id FROM games WHERE LOWER(home_team)=LOWER(%s) AND LOWER(away_team)=LOWER(%s) AND COALESCE(DATE(game_time), CURRENT_DATE)=COALESCE(DATE(%s::timestamp), CURRENT_DATE) LIMIT 1""", (g.home_team, g.away_team, g.game_time))
        existing = cur.fetchone()
        if existing:
            cur.execute("""UPDATE games SET game_time=COALESCE(%s, game_time), home_odds=COALESCE(%s, home_odds), away_odds=COALESCE(%s, away_odds), status=COALESCE(%s,status), source=%s, updated_at=NOW() WHERE id=%s""", (g.game_time, g.home_odds, g.away_odds, g.status, g.source, existing[0]))
        else:
            cur.execute("""INSERT INTO games (home_team, away_team, game_time, home_odds, away_odds, status, source) VALUES (%s,%s,%s,%s,%s,%s,%s)""", (g.home_team, g.away_team, g.game_time, g.home_odds, g.away_odds, g.status, g.source))
        saved += 1
    conn.commit(); cur.close(); conn.close(); return {"status":"ok","saved":saved}


@app.post("/results/import")
def import_results(payload: ResultsImportPayload):
    conn = db(); cur = conn.cursor(); ensure_core_tables(cur); saved = 0
    for r in payload.results:
        cur.execute("SELECT id FROM games WHERE LOWER(home_team)=LOWER(%s) AND LOWER(away_team)=LOWER(%s) ORDER BY id DESC LIMIT 1", (r.home_team, r.away_team))
        row = cur.fetchone()
        if row:
            cur.execute("UPDATE games SET home_score=%s, away_score=%s, status=%s, source=%s, updated_at=NOW() WHERE id=%s", (r.home_score, r.away_score, r.status, r.source, row[0]))
        else:
            cur.execute("INSERT INTO games (home_team, away_team, game_time, home_score, away_score, status, source) VALUES (%s,%s,%s,%s,%s,%s,%s)", (r.home_team, r.away_team, r.game_time, r.home_score, r.away_score, r.status, r.source))
        saved += 1
    conn.commit(); cur.close(); conn.close(); return {"status":"ok","saved":saved}


@app.post("/news-signal")
def create_news_signal(signal: NewsSignal):
    conn = db(); cur = conn.cursor(); ensure_core_tables(cur)
    cur.execute("INSERT INTO news_signals (league, team, player, signal_type, signal_text, impact_score, source) VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id", (signal.league, signal.team, signal.player, signal.signal_type, signal.signal_text, signal.impact_score, signal.source))
    signal_id = cur.fetchone()[0]
    conn.commit(); cur.close(); conn.close(); return {"status":"saved","id":signal_id}


@app.get("/news-signals")
def get_news_signals():
    conn = db(); cur = conn.cursor(); ensure_core_tables(cur)
    cur.execute("SELECT id, team, player, signal_type, signal_text, impact_score FROM news_signals ORDER BY created_at DESC LIMIT 50")
    rows = cur.fetchall(); cur.close(); conn.close()
    return [{"id":r[0],"team":r[1],"player":r[2],"signal_type":r[3],"signal_text":r[4],"impact_score":float(r[5]) if r[5] is not None else 0} for r in rows]


@app.get("/games")
def get_games():
    conn = db(); cur = conn.cursor(); ensure_core_tables(cur); conn.commit()
    cur.execute("SELECT id, home_team, away_team, game_time, home_odds, away_odds, status, home_score, away_score FROM games ORDER BY id DESC LIMIT 100")
    rows = cur.fetchall(); cur.close(); conn.close()
    return [{"id":r[0],"home_team":r[1],"away_team":r[2],"game_time":str(r[3]) if r[3] else None,"home_odds":float(r[4]) if r[4] is not None else None,"away_odds":float(r[5]) if r[5] is not None else None,"status":r[6],"home_score":r[7],"away_score":r[8]} for r in rows]


@app.get("/predictions")
def get_predictions():
    conn = db(); cur = conn.cursor(); ensure_core_tables(cur); conn.commit()
    cur.execute("""SELECT p.id,p.game_id,g.home_team,g.away_team,g.game_time,g.home_odds,g.away_odds,p.win_prob_home,p.win_prob_away,p.edge_home,p.recommendation FROM predictions p LEFT JOIN games g ON g.id=p.game_id ORDER BY p.id DESC LIMIT 100""")
    rows = cur.fetchall(); cur.close(); conn.close()
    return [{"id":r[0],"game_id":r[1],"home_team":r[2],"away_team":r[3],"game_time":str(r[4]) if r[4] else None,"home_odds":float(r[5]) if r[5] is not None else None,"away_odds":float(r[6]) if r[6] is not None else None,"win_prob_home":float(r[7]) if r[7] is not None else None,"win_prob_away":float(r[8]) if r[8] is not None else None,"edge_home":float(r[9]) if r[9] is not None else None,"recommendation":r[10]} for r in rows]


@app.get("/top-bets")
def get_top_bets():
    conn = db(); cur = conn.cursor(); ensure_tracking_tables(cur); conn.commit()
    cur.execute("""
        SELECT b.id,b.game_id,g.home_team,g.away_team,b.selected_team,b.recommendation,b.selected_odds,b.model_probability,b.edge,b.stake_amount,b.status,b.result_profit,b.signal_level,b.risk_level,b.reason,b.created_at
        FROM bet_recommendations b LEFT JOIN games g ON g.id=b.game_id
        WHERE DATE(b.created_at)=CURRENT_DATE AND b.status='open'
        ORDER BY CASE WHEN b.signal_level='STRONG BET' THEN 1 WHEN b.signal_level='MEDIUM BET' THEN 2 ELSE 3 END, b.edge DESC, b.stake_amount DESC
        LIMIT 3
    """)
    rows = cur.fetchall(); cur.close(); conn.close()
    return [{"id":r[0],"game_id":r[1],"home_team":r[2],"away_team":r[3],"selected_team":r[4],"recommendation":r[5],"selected_odds":float(r[6]) if r[6] is not None else None,"model_probability":float(r[7]) if r[7] is not None else None,"edge":float(r[8]) if r[8] is not None else None,"stake_amount":float(r[9]) if r[9] is not None else 0,"status":r[10],"result_profit":float(r[11]) if r[11] is not None else 0,"signal_level":r[12],"risk_level":r[13],"reason":r[14],"created_at":str(r[15]) if r[15] else None} for r in rows]


@app.get("/value-bets")
def get_value_bets():
    return get_top_bets()


@app.get("/bets")
def get_bets():
    conn = db(); cur = conn.cursor(); ensure_tracking_tables(cur); conn.commit()
    cur.execute("""SELECT b.id,b.game_id,g.home_team,g.away_team,b.selected_team,b.recommendation,b.selected_odds,b.model_probability,b.edge,b.stake_amount,b.status,b.result_profit,b.created_at FROM bet_recommendations b LEFT JOIN games g ON g.id=b.game_id ORDER BY b.id DESC LIMIT 100""")
    rows = cur.fetchall(); cur.close(); conn.close()
    return [{"id":r[0],"game_id":r[1],"home_team":r[2],"away_team":r[3],"selected_team":r[4],"recommendation":r[5],"selected_odds":float(r[6]) if r[6] is not None else None,"model_probability":float(r[7]) if r[7] is not None else None,"edge":float(r[8]) if r[8] is not None else None,"stake_amount":float(r[9]) if r[9] is not None else 0,"status":r[10],"result_profit":float(r[11]) if r[11] is not None else 0,"created_at":str(r[12]) if r[12] else None} for r in rows]


@app.post("/bets/{bet_id}/result")
def update_bet_result(bet_id: int, result: BetResult):
    status = result.status.lower()
    if status not in ["won", "lost", "push", "void", "open"]:
        return {"error":"status must be won, lost, push, void, or open"}
    conn = db(); cur = conn.cursor(); ensure_tracking_tables(cur)
    cur.execute("SELECT stake_amount, selected_odds FROM bet_recommendations WHERE id=%s", (bet_id,))
    row = cur.fetchone()
    if not row:
        cur.close(); conn.close(); return {"error":"bet not found"}
    stake = float(row[0] or 0); odds = float(row[1] or 0); profit = 0
    if status == "won": profit = stake * (odds - 1)
    elif status == "lost": profit = -stake
    cur.execute("UPDATE bet_recommendations SET status=%s, result_profit=%s, settled_at=CASE WHEN %s='open' THEN NULL ELSE NOW() END WHERE id=%s", (status, round(profit,2), status, bet_id))
    conn.commit(); cur.close(); conn.close(); return {"status":"updated","bet_id":bet_id,"result":status,"profit":round(profit,2)}


@app.get("/audit")
def audit():
    conn = db(); cur = conn.cursor(); ensure_tracking_tables(cur); conn.commit()
    cur.execute("""
        SELECT
            b.id, b.game_id, g.home_team, g.away_team, b.selected_team,
            b.recommendation, b.signal_level, b.edge, b.stake_amount,
            g.home_score, g.away_score,
            CASE
                WHEN g.home_score IS NULL OR g.away_score IS NULL THEN NULL
                WHEN g.home_score > g.away_score THEN g.home_team
                WHEN g.away_score > g.home_score THEN g.away_team
                ELSE 'PUSH'
            END AS actual_winner,
            CASE
                WHEN g.home_score IS NULL OR g.away_score IS NULL THEN NULL
                WHEN g.home_score = g.away_score THEN NULL
                WHEN LOWER(b.selected_team) = LOWER(CASE WHEN g.home_score > g.away_score THEN g.home_team ELSE g.away_team END) THEN true
                ELSE false
            END AS is_correct,
            b.created_at
        FROM bet_recommendations b
        LEFT JOIN games g ON g.id = b.game_id
        ORDER BY b.id DESC
        LIMIT 200
    """)
    rows = cur.fetchall(); cur.close(); conn.close()
    return [{
        "bet_id": r[0], "game_id": r[1], "home_team": r[2], "away_team": r[3],
        "selected_team": r[4], "recommendation": r[5], "signal_level": r[6],
        "edge": float(r[7]) if r[7] is not None else None,
        "stake_amount": float(r[8]) if r[8] is not None else 0,
        "home_score": r[9], "away_score": r[10], "actual_winner": r[11],
        "is_correct": r[12], "created_at": str(r[13]) if r[13] else None
    } for r in rows]


@app.get("/accuracy")
def accuracy():
    conn = db(); cur = conn.cursor(); ensure_tracking_tables(cur); conn.commit()
    cur.execute("""
        SELECT
            COUNT(*) FILTER (WHERE g.home_score IS NOT NULL AND g.away_score IS NOT NULL AND g.home_score != g.away_score) AS graded,
            COUNT(*) FILTER (
                WHERE g.home_score IS NOT NULL AND g.away_score IS NOT NULL AND g.home_score != g.away_score
                AND LOWER(b.selected_team) = LOWER(CASE WHEN g.home_score > g.away_score THEN g.home_team ELSE g.away_team END)
            ) AS correct
        FROM bet_recommendations b
        LEFT JOIN games g ON g.id = b.game_id
    """)
    row = cur.fetchone(); cur.close(); conn.close()
    graded = int(row[0] or 0); correct = int(row[1] or 0)
    accuracy_pct = (correct / graded * 100) if graded else 0
    return {"graded": graded, "correct": correct, "wrong": graded - correct, "accuracy_pct": round(accuracy_pct, 2)}


@app.get("/performance")
def performance():
    conn = db(); cur = conn.cursor(); ensure_tracking_tables(cur); conn.commit()
    cur.execute("""SELECT COUNT(*) FILTER (WHERE status='open'), COUNT(*) FILTER (WHERE status!='open'), COUNT(*) FILTER (WHERE status='won'), COUNT(*) FILTER (WHERE status='lost'), COALESCE(SUM(stake_amount) FILTER (WHERE status!='open'),0), COALESCE(SUM(result_profit),0) FROM bet_recommendations""")
    r = cur.fetchone(); cur.close(); conn.close()
    open_bets, settled, wins, losses, risked, profit = int(r[0]), int(r[1]), int(r[2]), int(r[3]), float(r[4] or 0), float(r[5] or 0)
    roi = (profit / risked * 100) if risked else 0
    win_rate = (wins / (wins + losses) * 100) if (wins + losses) else 0
    return {"open_bets":open_bets,"settled_bets":settled,"wins":wins,"losses":losses,"risked":round(risked,2),"profit":round(profit,2),"roi":round(roi,2),"win_rate":round(win_rate,2)}
