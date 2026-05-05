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
MIN_EDGE_TO_BET = float(os.getenv("MIN_EDGE_TO_BET", "0.025"))
STRONG_EDGE = float(os.getenv("STRONG_EDGE", "0.06"))
MIN_CONFIDENCE_TO_BET = float(os.getenv("MIN_CONFIDENCE_TO_BET", "0.60"))

app = FastAPI(title="Basketball Analytics Private MVP", version="1.1.0")
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
            confidence NUMERIC DEFAULT 0,
            team_strength_delta NUMERIC DEFAULT 0,
            news_impact NUMERIC DEFAULT 0,
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
        );
        ALTER TABLE bet_recommendations ADD COLUMN IF NOT EXISTS confidence NUMERIC DEFAULT 0;
        ALTER TABLE bet_recommendations ADD COLUMN IF NOT EXISTS team_strength_delta NUMERIC DEFAULT 0;
        ALTER TABLE bet_recommendations ADD COLUMN IF NOT EXISTS news_impact NUMERIC DEFAULT 0;
        CREATE INDEX IF NOT EXISTS idx_games_time ON games(game_time DESC);
        CREATE INDEX IF NOT EXISTS idx_games_teams ON games(lower(home_team), lower(away_team));
        CREATE INDEX IF NOT EXISTS idx_bets_status ON bet_recommendations(status);
        CREATE INDEX IF NOT EXISTS idx_news_team ON news_signals(lower(team), created_at DESC);
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


def fetch_all(sql, params=()):
    with conn() as c, c.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


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


def clamp(x, lo, hi):
    return max(lo, min(hi, x))


def team_recent_strength(cur, team: str):
    cur.execute("""
        SELECT home_team, away_team, home_score, away_score
        FROM games
        WHERE status='final'
          AND home_score IS NOT NULL AND away_score IS NOT NULL
          AND (LOWER(home_team)=LOWER(%s) OR LOWER(away_team)=LOWER(%s))
        ORDER BY game_time DESC NULLS LAST, id DESC
        LIMIT 10
    """, (team, team))
    rows = cur.fetchall()
    if not rows:
        return {"games": 0, "win_rate": 0.5, "avg_margin": 0.0, "score": 0.0}
    wins = 0
    margins = []
    for home, away, hs, aas in rows:
        is_home = home.lower() == team.lower()
        team_score = hs if is_home else aas
        opp_score = aas if is_home else hs
        margin = team_score - opp_score
        margins.append(margin)
        if margin > 0:
            wins += 1
    win_rate = wins / len(rows)
    avg_margin = sum(margins) / len(margins)
    # Strength roughly between -0.08 and +0.08 probability points.
    score = clamp(((win_rate - 0.5) * 0.10) + clamp(avg_margin / 150, -0.04, 0.04), -0.08, 0.08)
    return {"games": len(rows), "win_rate": win_rate, "avg_margin": avg_margin, "score": score}


def team_news_impact(cur, team: str):
    cur.execute("""
        SELECT COALESCE(SUM(impact_score),0), COUNT(*)
        FROM news_signals
        WHERE LOWER(team)=LOWER(%s)
          AND created_at >= NOW() - INTERVAL '5 days'
    """, (team,))
    total, count = cur.fetchone()
    raw = float(total or 0)
    # impact_score is -10..10. Convert to small probability adjustment.
    impact = clamp(raw / 250, -0.06, 0.06)
    return {"count": int(count or 0), "raw": raw, "prob_adjustment": impact}


def learning_penalty(cur, signal_bucket: str):
    cur.execute("""
        SELECT probability_penalty, edge_penalty
        FROM model_adjustments
        WHERE bucket_type='signal_level' AND bucket_name=%s
    """, (signal_bucket,))
    row = cur.fetchone()
    if not row:
        return 0.0, 0.0
    return float(row[0] or 0), float(row[1] or 0)


def decide_signal(edge: float, confidence: float):
    if edge >= STRONG_EDGE and confidence >= 0.72:
        return "STRONG BET", "MEDIUM"
    if edge >= MIN_EDGE_TO_BET and confidence >= MIN_CONFIDENCE_TO_BET:
        return "MEDIUM BET", "MEDIUM-HIGH"
    return "PASS", "HIGH"


def analyze_game(cur, home_team, away_team, home_odds, away_odds):
    h_imp, a_imp = 1 / float(home_odds), 1 / float(away_odds)
    market_total = h_imp + a_imp
    market_home = h_imp / market_total
    market_away = a_imp / market_total

    home_form = team_recent_strength(cur, home_team)
    away_form = team_recent_strength(cur, away_team)
    home_news = team_news_impact(cur, home_team)
    away_news = team_news_impact(cur, away_team)

    home_adv = 0.018
    strength_delta = home_form["score"] - away_form["score"]
    news_delta = home_news["prob_adjustment"] - away_news["prob_adjustment"]
    data_quality = min(1.0, (home_form["games"] + away_form["games"]) / 12)

    home_prob = clamp(market_home + home_adv + strength_delta + news_delta, 0.05, 0.95)
    away_prob = 1 - home_prob

    candidates = [
        {"team": home_team, "side": "home", "odds": float(home_odds), "prob": home_prob, "edge": home_prob - h_imp},
        {"team": away_team, "side": "away", "odds": float(away_odds), "prob": away_prob, "edge": away_prob - a_imp},
    ]
    best = max(candidates, key=lambda x: x["edge"])

    base_conf = 0.50 + min(abs(best["edge"]) * 2.2, 0.20) + data_quality * 0.12 + min(abs(strength_delta) * 1.5, 0.10) + min(abs(news_delta) * 1.2, 0.08)
    confidence = clamp(base_conf, 0.0, 0.95)

    signal, risk = decide_signal(best["edge"], confidence)
    prob_penalty, edge_penalty = learning_penalty(cur, signal)
    adjusted_prob = clamp(best["prob"] - prob_penalty, 0.03, 0.97)
    adjusted_edge = adjusted_prob - (1 / best["odds"]) - edge_penalty
    signal, risk = decide_signal(adjusted_edge, confidence)

    stake_pct = 0.0 if signal == "PASS" else min(MAX_STAKE_PCT, max(0.005, adjusted_edge / 3.0))
    reasons = [
        f"market implied {1/best['odds']:.1%}",
        f"model probability {adjusted_prob:.1%}",
        f"edge {adjusted_edge:.1%}",
        f"confidence {confidence:.0%}",
        f"recent form: {home_team} {home_form['win_rate']:.0%}/{home_form['avg_margin']:+.1f} margin vs {away_team} {away_form['win_rate']:.0%}/{away_form['avg_margin']:+.1f}",
    ]
    if home_news["count"] or away_news["count"]:
        reasons.append(f"news impact delta {news_delta:+.1%}")
    if signal == "PASS":
        reasons.append("no-bet filter: edge/confidence not strong enough")

    return {
        "team": best["team"],
        "side": best["side"],
        "odds": best["odds"],
        "prob": adjusted_prob,
        "fair": 1 / best["odds"],
        "edge": adjusted_edge,
        "confidence": confidence,
        "team_strength_delta": strength_delta,
        "news_impact": news_delta,
        "signal": signal,
        "risk": risk,
        "stake_pct": stake_pct,
        "stake": round(DEFAULT_BANKROLL * stake_pct, 2),
        "reason": "; ".join(reasons),
        "home_form": home_form,
        "away_form": away_form,
        "home_news": home_news,
        "away_news": away_news,
    }


def save_recommendation(cur, game_id, pick):
    cur.execute("""
        INSERT INTO bet_recommendations
        (game_id, selected_team, recommendation, selected_odds, model_probability, fair_probability, edge,
         confidence, team_strength_delta, news_impact, bankroll, stake_pct, stake_amount, signal_level, risk_level, reason)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (game_id, recommendation) DO UPDATE SET
          selected_odds=EXCLUDED.selected_odds,
          model_probability=EXCLUDED.model_probability,
          fair_probability=EXCLUDED.fair_probability,
          edge=EXCLUDED.edge,
          confidence=EXCLUDED.confidence,
          team_strength_delta=EXCLUDED.team_strength_delta,
          news_impact=EXCLUDED.news_impact,
          stake_pct=EXCLUDED.stake_pct,
          stake_amount=EXCLUDED.stake_amount,
          signal_level=EXCLUDED.signal_level,
          risk_level=EXCLUDED.risk_level,
          reason=EXCLUDED.reason
    """, (game_id, pick["team"], pick["team"], pick["odds"], pick["prob"], pick["fair"], pick["edge"], pick["confidence"], pick["team_strength_delta"], pick["news_impact"], DEFAULT_BANKROLL, pick["stake_pct"], pick["stake"], pick["signal"], pick["risk"], pick["reason"]))


def refresh_learning(cur):
    cur.execute("""
        WITH graded AS (
            SELECT b.signal_level, b.stake_amount, b.result_profit,
                   CASE WHEN g.home_score IS NULL OR g.away_score IS NULL OR g.home_score=g.away_score THEN NULL
                        WHEN LOWER(b.selected_team)=LOWER(CASE WHEN g.home_score>g.away_score THEN g.home_team ELSE g.away_team END) THEN 1 ELSE 0 END AS correct
            FROM bet_recommendations b JOIN games g ON g.id=b.game_id
            WHERE g.home_score IS NOT NULL AND g.away_score IS NOT NULL AND g.home_score!=g.away_score
        ), agg AS (
            SELECT signal_level, COUNT(*) sample_size, AVG(correct::numeric) accuracy,
                   COALESCE(SUM(result_profit) / NULLIF(SUM(stake_amount),0),0) roi
            FROM graded GROUP BY signal_level
        )
        INSERT INTO model_adjustments (bucket_type,bucket_name,sample_size,accuracy,roi,probability_penalty,edge_penalty,reason,updated_at)
        SELECT 'signal_level', signal_level, sample_size, accuracy, roi,
               CASE WHEN sample_size >= 10 AND accuracy < 0.50 THEN 0.015 ELSE 0 END,
               CASE WHEN sample_size >= 10 AND roi < 0 THEN 0.01 ELSE 0 END,
               CASE WHEN sample_size < 10 THEN 'not enough graded results yet'
                    WHEN accuracy < 0.50 OR roi < 0 THEN 'auto cautious: weak historical performance'
                    ELSE 'healthy bucket' END,
               NOW()
        FROM agg
        ON CONFLICT (bucket_type,bucket_name) DO UPDATE SET
            sample_size=EXCLUDED.sample_size, accuracy=EXCLUDED.accuracy, roi=EXCLUDED.roi,
            probability_penalty=EXCLUDED.probability_penalty, edge_penalty=EXCLUDED.edge_penalty,
            reason=EXCLUDED.reason, updated_at=NOW();
    """)


@app.get("/")
def root():
    return {"status": "ok", "dashboard": "/dashboard", "docs": "/docs", "version": "1.1.0"}

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
                pick = analyze_game(cur, g.home_team, g.away_team, g.home_odds, g.away_odds)
                save_recommendation(cur, game_id, pick)
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
        refresh_learning(cur)
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
        refresh_learning(cur)
    return {"status": "updated", "profit": round(profit, 2)}

@app.post("/recalculate", dependencies=[Depends(require_key)])
def recalculate():
    count = 0
    with conn() as c, c.cursor() as cur:
        cur.execute("SELECT id,home_team,away_team,home_odds,away_odds FROM games WHERE is_anomaly=false AND home_odds IS NOT NULL AND away_odds IS NOT NULL")
        for game_id, home, away, ho, ao in cur.fetchall():
            pick = analyze_game(cur, home, away, float(ho), float(ao))
            save_recommendation(cur, game_id, pick)
            count += 1
        refresh_learning(cur)
    return {"status": "ok", "recalculated": count}

@app.get("/games")
def games():
    data = fetch_all("SELECT id,home_team,away_team,game_time,home_odds,away_odds,status,home_score,away_score,is_anomaly,anomaly_reason FROM games ORDER BY COALESCE(game_time, created_at) DESC LIMIT 100")
    return [{"id":r[0],"home_team":r[1],"away_team":r[2],"game_time":str(r[3]) if r[3] else None,"home_odds":float(r[4]) if r[4] else None,"away_odds":float(r[5]) if r[5] else None,"status":r[6],"home_score":r[7],"away_score":r[8],"is_anomaly":r[9],"anomaly_reason":r[10]} for r in data]

@app.get("/bets")
def bets():
    data = fetch_all("SELECT b.id,g.home_team,g.away_team,b.recommendation,b.selected_odds,b.model_probability,b.edge,b.confidence,b.team_strength_delta,b.news_impact,b.stake_amount,b.signal_level,b.status,b.result_profit,b.reason FROM bet_recommendations b JOIN games g ON g.id=b.game_id ORDER BY b.id DESC LIMIT 100")
    return [{"id":r[0],"home_team":r[1],"away_team":r[2],"recommendation":r[3],"selected_odds":float(r[4]),"model_probability":float(r[5]),"edge":float(r[6]),"confidence":float(r[7] or 0),"team_strength_delta":float(r[8] or 0),"news_impact":float(r[9] or 0),"stake_amount":float(r[10]),"signal_level":r[11],"status":r[12],"result_profit":float(r[13] or 0),"reason":r[14]} for r in data]

@app.get("/top-bets")
def top_bets():
    return [b for b in bets() if b["status"] == "open" and b["signal_level"] != "PASS"][:3]

@app.get("/analysis")
def analysis():
    return {"top_bets": top_bets(), "all_bets": bets()[:25], "performance": performance(), "learning": model_learning(), "anomalies": anomalies()[:10]}

@app.get("/anomalies")
def anomalies():
    data = fetch_all("SELECT id,anomaly_type,source,home_team,away_team,reason,severity,created_at FROM data_anomalies ORDER BY id DESC LIMIT 100")
    return [{"id":r[0],"type":r[1],"source":r[2],"home_team":r[3],"away_team":r[4],"reason":r[5],"severity":r[6],"created_at":str(r[7])} for r in data]

@app.get("/model-learning")
def model_learning():
    data = fetch_all("SELECT bucket_type,bucket_name,sample_size,accuracy,roi,probability_penalty,edge_penalty,reason,updated_at FROM model_adjustments ORDER BY bucket_type,bucket_name")
    return [{"bucket_type":r[0],"bucket_name":r[1],"sample_size":r[2],"accuracy":float(r[3] or 0),"roi":float(r[4] or 0),"probability_penalty":float(r[5] or 0),"edge_penalty":float(r[6] or 0),"reason":r[7],"updated_at":str(r[8]) if r[8] else None} for r in data]

@app.get("/performance")
def performance():
    r = fetch_all("SELECT COUNT(*) FILTER (WHERE status='open'), COUNT(*) FILTER (WHERE status!='open'), COUNT(*) FILTER (WHERE status='won'), COUNT(*) FILTER (WHERE status='lost'), COALESCE(SUM(stake_amount) FILTER (WHERE status!='open'),0), COALESCE(SUM(result_profit),0) FROM bet_recommendations")[0]
    risked, profit = float(r[4]), float(r[5])
    return {"open_bets":int(r[0]),"settled_bets":int(r[1]),"wins":int(r[2]),"losses":int(r[3]),"risked":round(risked,2),"profit":round(profit,2),"roi":round((profit/risked*100) if risked else 0,2)}

@app.get("/dashboard", response_class=HTMLResponse)
def dashboard():
    return """
<!doctype html><html><head><meta name='viewport' content='width=device-width,initial-scale=1'><title>Basketball Analytics</title><style>body{font-family:Arial;background:#0f172a;color:#e5e7eb;margin:0}header{background:#111827;padding:24px}.wrap{padding:20px;max-width:1200px;margin:auto}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}.card,table{background:#111827;border:1px solid #334155;border-radius:14px;padding:14px}table{width:100%;border-collapse:collapse;margin-top:18px}th,td{padding:10px;border-bottom:1px solid #334155;text-align:left}.pos{color:#22c55e}.neg{color:#f87171}.pill{font-weight:700;color:#facc15}.pass{color:#94a3b8}@media(max-width:700px){table{display:block;overflow-x:auto;white-space:nowrap}}</style></head><body><header><h1>🏀 Private Basketball Analytics</h1><div>Edge • Confidence • Recent form • News impact • PASS filter</div></header><div class='wrap'><div class='grid'><div class='card'>Games<br><b id='games'>0</b></div><div class='card'>Top Bets<br><b id='top'>0</b></div><div class='card'>Profit<br><b id='profit'>$0</b></div><div class='card'>ROI<br><b id='roi'>0%</b></div></div><h2>Top Bets</h2><table><thead><tr><th>Game</th><th>Pick</th><th>Signal</th><th>Odds</th><th>Edge</th><th>Conf.</th><th>Stake</th><th>Reason</th></tr></thead><tbody id='rows'></tbody></table><h2>All Recommendations</h2><table><tbody id='all'></tbody></table><h2>Learning</h2><table><tbody id='learn'></tbody></table><h2>Anomalies</h2><table><tbody id='anoms'></tbody></table></div><script>const money=x=>'$'+Number(x||0).toFixed(2);const pct=x=>(Number(x||0)*100).toFixed(1)+'%';async function j(u){return (await fetch(u)).json()}async function load(){const a=await j('/analysis');games.textContent=(await j('/games')).length;top.textContent=a.top_bets.length;profit.textContent=money(a.performance.profit);roi.textContent=Number(a.performance.roi||0).toFixed(1)+'%';rows.innerHTML=a.top_bets.map(b=>`<tr><td>${b.away_team} @ ${b.home_team}</td><td>${b.recommendation}</td><td class='pill'>${b.signal_level}</td><td>${b.selected_odds}</td><td>${pct(b.edge)}</td><td>${pct(b.confidence)}</td><td>${money(b.stake_amount)}</td><td>${b.reason}</td></tr>`).join('')||'<tr><td>No current value bets</td></tr>';all.innerHTML=a.all_bets.map(b=>`<tr><td>${b.away_team} @ ${b.home_team}</td><td>${b.recommendation}</td><td class='${b.signal_level==='PASS'?'pass':'pill'}'>${b.signal_level}</td><td>${pct(b.edge)}</td><td>${pct(b.confidence)}</td><td>${b.reason}</td></tr>`).join('')||'<tr><td>No recommendations yet</td></tr>';learn.innerHTML=a.learning.map(x=>`<tr><td>${x.bucket_name}</td><td>sample ${x.sample_size}</td><td>accuracy ${pct(x.accuracy)}</td><td>roi ${pct(x.roi)}</td><td>${x.reason}</td></tr>`).join('')||'<tr><td>No learning data yet</td></tr>';anoms.innerHTML=a.anomalies.map(x=>`<tr><td>${x.away_team||'-'} @ ${x.home_team||'-'}</td><td class='neg'>${x.reason}</td><td>${x.created_at}</td></tr>`).join('')||'<tr><td>No anomalies</td></tr>'}load();setInterval(load,30000)</script></body></html>
"""
