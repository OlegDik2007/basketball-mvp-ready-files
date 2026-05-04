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
            status TEXT DEFAULT 'open',
            result_profit NUMERIC DEFAULT 0,
            created_at TIMESTAMP DEFAULT NOW(),
            settled_at TIMESTAMP,
            UNIQUE (game_id, recommendation)
        )
    """)
    cur.execute("""
        ALTER TABLE bet_recommendations
        ADD COLUMN IF NOT EXISTS result_profit NUMERIC DEFAULT 0
    """)
    cur.execute("""
        ALTER TABLE bet_recommendations
        ADD COLUMN IF NOT EXISTS settled_at TIMESTAMP
    """)


class NewsSignal(BaseModel):
    league: Optional[str] = "NBA"
    team: Optional[str] = None
    player: Optional[str] = None
    signal_type: Optional[str] = None
    signal_text: Optional[str] = None
    impact_score: Optional[float] = 0
    source: Optional[str] = "openclaw"


class BetResult(BaseModel):
    status: str  # won, lost, push, void, open


@app.get("/")
def root():
    return {
        "message": "Basketball Betting Analytics API",
        "dashboard": "/dashboard",
        "endpoints": ["/health", "/games", "/predictions", "/value-bets", "/bets", "/performance", "/news-signals"]
    }


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard():
    return """
<!DOCTYPE html><html><head><meta charset='UTF-8'><meta name='viewport' content='width=device-width,initial-scale=1.0'>
<title>Basketball Analytics Dashboard</title>
<style>
body{margin:0;font-family:Arial;background:#0f172a;color:#e5e7eb}header{padding:24px;background:linear-gradient(135deg,#1e3a8a,#111827);border-bottom:1px solid #334155}h1{margin:0 0 8px}.sub{color:#cbd5e1}.wrap{padding:24px;max-width:1250px;margin:auto}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:14px;margin:20px 0}.card{background:#111827;border:1px solid #334155;border-radius:16px;padding:16px}.label{color:#94a3b8;font-size:13px}.metric{font-size:28px;font-weight:700;margin-top:6px}.pos{color:#22c55e}.neg{color:#f87171}.no{color:#94a3b8}.bet{color:#22c55e;font-weight:700}table{width:100%;border-collapse:collapse;background:#111827;border:1px solid #334155;border-radius:14px;overflow:hidden;margin-bottom:26px}th,td{padding:10px;border-bottom:1px solid #1f2937;text-align:left;font-size:14px}th{background:#1f2937;color:#cbd5e1}.btn{padding:9px 12px;border-radius:10px;border:1px solid #475569;background:#1d4ed8;color:white;cursor:pointer}.mini{font-size:12px;color:#94a3b8}.win{background:#14532d}.loss{background:#7f1d1d}.push{background:#374151}@media(max-width:700px){table{display:block;overflow-x:auto;white-space:nowrap}}
</style></head><body><header><h1>🏀 Basketball Betting Analytics</h1><div class='sub'>Odds + OpenClaw signals + bankroll + profit tracking</div></header><div class='wrap'>
<button class='btn' onclick='loadData()'>Refresh</button><div class='mini' id='updated'>Loading...</div>
<div class='grid'>
<div class='card'><div class='label'>Games</div><div class='metric' id='gamesCount'>0</div></div>
<div class='card'><div class='label'>Predictions</div><div class='metric' id='predictionsCount'>0</div></div>
<div class='card'><div class='label'>Open Bets</div><div class='metric' id='openBets'>0</div></div>
<div class='card'><div class='label'>Settled Bets</div><div class='metric' id='settledBets'>0</div></div>
<div class='card'><div class='label'>Profit</div><div class='metric' id='profit'>$0</div></div>
<div class='card'><div class='label'>ROI</div><div class='metric' id='roi'>0%</div></div>
<div class='card'><div class='label'>Win Rate</div><div class='metric' id='winRate'>0%</div></div>
<div class='card'><div class='label'>Signals</div><div class='metric' id='signalsCount'>0</div></div>
</div>
<h2>💰 Bet Recommendations / Tracking</h2><table><thead><tr><th>ID</th><th>Game</th><th>Pick</th><th>Odds</th><th>Edge</th><th>Stake</th><th>Status</th><th>Profit</th><th>Actions</th></tr></thead><tbody id='betsTable'></tbody></table>
<h2>🔥 Value Bets</h2><table><thead><tr><th>Game</th><th>Odds</th><th>Home Win %</th><th>Edge</th><th>Recommendation</th></tr></thead><tbody id='valueBetsTable'></tbody></table>
<h2>📊 Latest Predictions</h2><table><thead><tr><th>Game</th><th>Home Odds</th><th>Away Odds</th><th>Home %</th><th>Away %</th><th>Edge</th><th>Recommendation</th></tr></thead><tbody id='predictionsTable'></tbody></table>
<h2>🧠 OpenClaw Signals</h2><table><thead><tr><th>Team</th><th>Player</th><th>Type</th><th>Signal</th><th>Impact</th></tr></thead><tbody id='signalsTable'></tbody></table>
</div><script>
function pct(x){return x==null?'-':(x*100).toFixed(1)+'%'}function money(x){return '$'+Number(x||0).toFixed(2)}function edge(x){return x==null?'-':(x*100).toFixed(1)+'%'}function cls(x){return Number(x)>=0?'pos':'neg'}async function j(p){const r=await fetch(p);if(!r.ok)throw new Error(p);return r.json()}async function settle(id,status){await fetch('/bets/'+id+'/result',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({status})});loadData()}
async function loadData(){try{const [games,preds,vals,sigs,bets,perf]=await Promise.all([j('/games'),j('/predictions'),j('/value-bets'),j('/news-signals'),j('/bets'),j('/performance')]);
document.getElementById('gamesCount').textContent=games.length;document.getElementById('predictionsCount').textContent=preds.length;document.getElementById('signalsCount').textContent=sigs.length;document.getElementById('openBets').textContent=perf.open_bets;document.getElementById('settledBets').textContent=perf.settled_bets;document.getElementById('profit').textContent=money(perf.profit);document.getElementById('profit').className='metric '+cls(perf.profit);document.getElementById('roi').textContent=Number(perf.roi||0).toFixed(1)+'%';document.getElementById('roi').className='metric '+cls(perf.roi);document.getElementById('winRate').textContent=Number(perf.win_rate||0).toFixed(1)+'%';document.getElementById('updated').textContent='Updated: '+new Date().toLocaleString();
document.getElementById('betsTable').innerHTML=bets.length?bets.map(b=>`<tr><td>${b.id}</td><td>${b.away_team||'-'} @ ${b.home_team||'-'}</td><td class='bet'>${b.recommendation}</td><td>${b.selected_odds}</td><td class='${cls(b.edge)}'>${edge(b.edge)}</td><td>${money(b.stake_amount)}</td><td>${b.status}</td><td class='${cls(b.result_profit)}'>${money(b.result_profit)}</td><td><button onclick="settle(${b.id},'won')">Won</button> <button onclick="settle(${b.id},'lost')">Lost</button> <button onclick="settle(${b.id},'push')">Push</button></td></tr>`).join(''):'<tr><td colspan=9 class=no>No tracked bets yet</td></tr>';
document.getElementById('valueBetsTable').innerHTML=vals.length?vals.map(p=>`<tr><td>${p.away_team||'-'} @ ${p.home_team||'-'}</td><td>${p.home_odds||'-'} / ${p.away_odds||'-'}</td><td>${pct(p.win_prob_home)}</td><td class='${cls(p.edge_home)}'>${edge(p.edge_home)}</td><td class='bet'>${p.recommendation}</td></tr>`).join(''):'<tr><td colspan=5 class=no>No value bets right now</td></tr>';
document.getElementById('predictionsTable').innerHTML=preds.map(p=>`<tr><td>${p.away_team||'-'} @ ${p.home_team||'-'}</td><td>${p.home_odds||'-'}</td><td>${p.away_odds||'-'}</td><td>${pct(p.win_prob_home)}</td><td>${pct(p.win_prob_away)}</td><td class='${cls(p.edge_home)}'>${edge(p.edge_home)}</td><td class='${p.recommendation!='NO BET'?'bet':'no'}'>${p.recommendation}</td></tr>`).join('');
document.getElementById('signalsTable').innerHTML=sigs.map(s=>`<tr><td>${s.team||'-'}</td><td>${s.player||'-'}</td><td>${s.signal_type||'-'}</td><td>${s.signal_text||'-'}</td><td class='${cls(s.impact_score)}'>${s.impact_score}</td></tr>`).join('');
}catch(e){document.getElementById('updated').textContent='Error: '+e.message}}loadData();setInterval(loadData,30000);
</script></body></html>
    """


@app.post("/news-signal")
def create_news_signal(signal: NewsSignal):
    conn = db(); cur = conn.cursor()
    cur.execute("""
        INSERT INTO news_signals (league, team, player, signal_type, signal_text, impact_score, source)
        VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id
    """, (signal.league, signal.team, signal.player, signal.signal_type, signal.signal_text, signal.impact_score, signal.source))
    signal_id = cur.fetchone()[0]
    conn.commit(); cur.close(); conn.close()
    return {"status":"saved","id":signal_id}


@app.get("/news-signals")
def get_news_signals():
    conn = db(); cur = conn.cursor()
    cur.execute("SELECT id, team, player, signal_type, signal_text, impact_score FROM news_signals ORDER BY created_at DESC LIMIT 50")
    rows = cur.fetchall(); cur.close(); conn.close()
    return [{"id":r[0],"team":r[1],"player":r[2],"signal_type":r[3],"signal_text":r[4],"impact_score":float(r[5]) if r[5] is not None else 0} for r in rows]


@app.get("/games")
def get_games():
    conn = db(); cur = conn.cursor()
    cur.execute("SELECT id, home_team, away_team, game_time, home_odds, away_odds FROM games ORDER BY id DESC LIMIT 100")
    rows = cur.fetchall(); cur.close(); conn.close()
    return [{"id":r[0],"home_team":r[1],"away_team":r[2],"game_time":str(r[3]) if r[3] else None,"home_odds":float(r[4]) if r[4] is not None else None,"away_odds":float(r[5]) if r[5] is not None else None} for r in rows]


@app.get("/predictions")
def get_predictions():
    conn = db(); cur = conn.cursor()
    cur.execute("""
        SELECT p.id,p.game_id,g.home_team,g.away_team,g.game_time,g.home_odds,g.away_odds,p.win_prob_home,p.win_prob_away,p.edge_home,p.recommendation
        FROM predictions p LEFT JOIN games g ON g.id=p.game_id ORDER BY p.id DESC LIMIT 100
    """)
    rows = cur.fetchall(); cur.close(); conn.close()
    return [{"id":r[0],"game_id":r[1],"home_team":r[2],"away_team":r[3],"game_time":str(r[4]) if r[4] else None,"home_odds":float(r[5]) if r[5] is not None else None,"away_odds":float(r[6]) if r[6] is not None else None,"win_prob_home":float(r[7]) if r[7] is not None else None,"win_prob_away":float(r[8]) if r[8] is not None else None,"edge_home":float(r[9]) if r[9] is not None else None,"recommendation":r[10]} for r in rows]


@app.get("/value-bets")
def get_value_bets():
    conn = db(); cur = conn.cursor()
    cur.execute("""
        SELECT p.id,p.game_id,g.home_team,g.away_team,g.game_time,g.home_odds,g.away_odds,p.win_prob_home,p.win_prob_away,p.edge_home,p.recommendation
        FROM predictions p LEFT JOIN games g ON g.id=p.game_id
        WHERE p.recommendation IS NOT NULL AND p.recommendation!='NO BET'
        ORDER BY p.edge_home DESC LIMIT 50
    """)
    rows = cur.fetchall(); cur.close(); conn.close()
    return [{"id":r[0],"game_id":r[1],"home_team":r[2],"away_team":r[3],"game_time":str(r[4]) if r[4] else None,"home_odds":float(r[5]) if r[5] is not None else None,"away_odds":float(r[6]) if r[6] is not None else None,"win_prob_home":float(r[7]) if r[7] is not None else None,"win_prob_away":float(r[8]) if r[8] is not None else None,"edge_home":float(r[9]) if r[9] is not None else None,"recommendation":r[10]} for r in rows]


@app.get("/bets")
def get_bets():
    conn = db(); cur = conn.cursor(); ensure_tracking_tables(cur); conn.commit()
    cur.execute("""
        SELECT b.id,b.game_id,g.home_team,g.away_team,b.selected_team,b.recommendation,b.selected_odds,b.model_probability,b.edge,b.stake_amount,b.status,b.result_profit,b.created_at
        FROM bet_recommendations b LEFT JOIN games g ON g.id=b.game_id
        ORDER BY b.id DESC LIMIT 100
    """)
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
    elif status in ["push", "void", "open"]: profit = 0
    cur.execute("""
        UPDATE bet_recommendations SET status=%s, result_profit=%s, settled_at=CASE WHEN %s='open' THEN NULL ELSE NOW() END WHERE id=%s
    """, (status, round(profit,2), status, bet_id))
    conn.commit(); cur.close(); conn.close()
    return {"status":"updated","bet_id":bet_id,"result":status,"profit":round(profit,2)}


@app.get("/performance")
def performance():
    conn = db(); cur = conn.cursor(); ensure_tracking_tables(cur); conn.commit()
    cur.execute("""
        SELECT
          COUNT(*) FILTER (WHERE status='open') AS open_bets,
          COUNT(*) FILTER (WHERE status!='open') AS settled_bets,
          COUNT(*) FILTER (WHERE status='won') AS wins,
          COUNT(*) FILTER (WHERE status='lost') AS losses,
          COALESCE(SUM(stake_amount) FILTER (WHERE status!='open'),0) AS risked,
          COALESCE(SUM(result_profit),0) AS profit
        FROM bet_recommendations
    """)
    r = cur.fetchone(); cur.close(); conn.close()
    open_bets, settled, wins, losses, risked, profit = int(r[0]), int(r[1]), int(r[2]), int(r[3]), float(r[4] or 0), float(r[5] or 0)
    roi = (profit / risked * 100) if risked else 0
    win_rate = (wins / (wins + losses) * 100) if (wins + losses) else 0
    return {"open_bets":open_bets,"settled_bets":settled,"wins":wins,"losses":losses,"risked":round(risked,2),"profit":round(profit,2),"roi":round(roi,2),"win_rate":round(win_rate,2)}
