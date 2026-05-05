# Basketball Analytics Private MVP

Private basketball betting analytics engine for personal use. The system is designed to run on a VPS/server and receive game odds, results, and news from an OpenClaw bot.

This is not a public SaaS version. No user accounts are required right now.

## Architecture

```text
OpenClaw bot on server
        ↓
collects games / odds / news / final results
        ↓
sends JSON to FastAPI backend
        ↓
PostgreSQL stores all data
        ↓
Dashboard shows analysis, top bets, confidence, ROI, anomalies
```

## Main Features

- Private API backend with FastAPI
- PostgreSQL storage
- Dashboard at `/dashboard`
- Game import endpoint for OpenClaw
- Result import endpoint for OpenClaw
- News signal endpoint for OpenClaw
- Recalculation endpoint
- Edge calculation
- Confidence score
- No-bet / PASS filter
- Recent team form analysis
- News impact adjustment
- Model learning from graded results
- Anomaly detection for bad odds or bad parsed data

## Required `.env`

Create `.env` in the project root:

```env
DATABASE_URL=postgresql://postgres:postgres@db:5432/basketball
API_KEY=CHANGE_THIS_TO_A_LONG_SECRET
ALLOWED_ORIGINS=http://localhost:8000
DEFAULT_BANKROLL=1000
MAX_STAKE_PCT=0.03
MIN_EDGE_TO_BET=0.025
STRONG_EDGE=0.06
MIN_CONFIDENCE_TO_BET=0.60
```

Use a long private value for `API_KEY`. OpenClaw will send this key in the `X-API-Key` header.

## Docker Compose

Recommended `docker-compose.yml`:

```yaml
services:
  api:
    build: ./backend
    container_name: basketball_api
    ports:
      - "8000:8000"
    env_file:
      - .env
    restart: always
    depends_on:
      - db
      - redis

  db:
    image: postgres:15
    container_name: basketball_db
    environment:
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: postgres
      POSTGRES_DB: basketball
    ports:
      - "5432:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data
    restart: always

  redis:
    image: redis:7
    container_name: basketball_redis
    restart: always
    ports:
      - "6379:6379"

volumes:
  pgdata:
```

## Start on server

```bash
git clone https://github.com/OlegDik2007/basketball-mvp-ready-files.git
cd basketball-mvp-ready-files
nano .env
docker compose up --build -d
```

Check containers:

```bash
docker ps
docker logs basketball_api --tail 100
```

Open dashboard:

```text
http://SERVER_IP:8000/dashboard
```

Health check:

```bash
curl http://SERVER_IP:8000/health
```

## OpenClaw Integration

OpenClaw should collect basketball information and send clean JSON to the backend.

### Required HTTP header

```text
X-API-Key: YOUR_SECRET_API_KEY
Content-Type: application/json
```

## Endpoint 1: Import Games + Odds

```text
POST /games/import
```

Example:

```bash
curl -X POST http://SERVER_IP:8000/games/import \
  -H "Content-Type: application/json" \
  -H "X-API-Key: YOUR_SECRET_API_KEY" \
  -d '{
    "games": [
      {
        "home_team": "Chicago Bulls",
        "away_team": "Miami Heat",
        "game_time": "2026-05-05T19:00:00",
        "home_odds": 1.91,
        "away_odds": 1.95,
        "status": "scheduled",
        "source": "openclaw"
      }
    ]
  }'
```

Important:

- Odds must be decimal odds.
- American odds like `+110` or `-130` must be converted before sending.
- If OpenClaw cannot find clean odds, do not guess. Send only verified values.

## Endpoint 2: Import Final Results

```text
POST /results/import
```

Example:

```bash
curl -X POST http://SERVER_IP:8000/results/import \
  -H "Content-Type: application/json" \
  -H "X-API-Key: YOUR_SECRET_API_KEY" \
  -d '{
    "results": [
      {
        "home_team": "Chicago Bulls",
        "away_team": "Miami Heat",
        "game_time": "2026-05-05T19:00:00",
        "home_score": 108,
        "away_score": 101,
        "status": "final",
        "source": "openclaw"
      }
    ]
  }'
```

After final results are imported, the system updates learning buckets automatically.

## Endpoint 3: Import News / Injury Signal

```text
POST /news-signal
```

Example negative signal:

```bash
curl -X POST http://SERVER_IP:8000/news-signal \
  -H "Content-Type: application/json" \
  -H "X-API-Key: YOUR_SECRET_API_KEY" \
  -d '{
    "league": "NBA",
    "team": "Chicago Bulls",
    "player": "Key Player",
    "signal_type": "injury",
    "signal_text": "Key player listed out tonight.",
    "impact_score": -6,
    "source": "openclaw"
  }'
```

Example positive signal:

```bash
curl -X POST http://SERVER_IP:8000/news-signal \
  -H "Content-Type: application/json" \
  -H "X-API-Key: YOUR_SECRET_API_KEY" \
  -d '{
    "league": "NBA",
    "team": "Miami Heat",
    "player": "Starter",
    "signal_type": "lineup",
    "signal_text": "Starter expected to return tonight.",
    "impact_score": 4,
    "source": "openclaw"
  }'
```

Impact score scale:

```text
-10 = very bad for team
-6  = important negative news
-3  = small negative news
 0  = neutral
+3  = small positive news
+6  = important positive news
+10 = very strong positive news
```

## Endpoint 4: Recalculate Analysis

After adding news signals, call:

```text
POST /recalculate
```

Example:

```bash
curl -X POST http://SERVER_IP:8000/recalculate \
  -H "X-API-Key: YOUR_SECRET_API_KEY"
```

This updates recommendations using latest odds, team form, news, and learning penalties.

## Read Endpoints

```text
GET /dashboard
GET /analysis
GET /games
GET /bets
GET /top-bets
GET /anomalies
GET /model-learning
GET /performance
```

## Recommended OpenClaw Task Prompt

Use this as the daily/hourly OpenClaw instruction:

```text
You are the data collection bot for my private Basketball Analytics backend.

Every run:
1. Find today's NBA basketball games.
2. Collect home team, away team, scheduled game time, and decimal moneyline odds for both teams.
3. Collect important injury, lineup, rest, and team news.
4. If final results are available, collect final home_score and away_score.
5. Validate data before sending. Do not guess missing odds or scores.
6. Convert American odds to decimal odds before sending.
7. Send game odds to POST http://SERVER_IP:8000/games/import.
8. Send news items one by one to POST http://SERVER_IP:8000/news-signal.
9. Send final scores to POST http://SERVER_IP:8000/results/import.
10. After news and odds are sent, call POST http://SERVER_IP:8000/recalculate.

Always use headers:
Content-Type: application/json
X-API-Key: YOUR_SECRET_API_KEY

Data quality rules:
- Do not send duplicate games if the same home_team, away_team, and game_time already exist.
- Do not send odds if they look like American odds.
- Decimal odds should usually be between 1.01 and 15.
- If a team name is unclear, skip that game and log it.
- If a source conflicts with another source, prefer official league/team reports and major sportsbooks.
```

## OpenClaw JSON Templates

Games:

```json
{
  "games": [
    {
      "home_team": "Home Team",
      "away_team": "Away Team",
      "game_time": "2026-05-05T19:00:00",
      "home_odds": 1.91,
      "away_odds": 1.95,
      "status": "scheduled",
      "source": "openclaw"
    }
  ]
}
```

News:

```json
{
  "league": "NBA",
  "team": "Team Name",
  "player": "Player Name or null",
  "signal_type": "injury | lineup | rest | travel | form | coaching | other",
  "signal_text": "Short explanation of the news",
  "impact_score": -6,
  "source": "openclaw"
}
```

Results:

```json
{
  "results": [
    {
      "home_team": "Home Team",
      "away_team": "Away Team",
      "game_time": "2026-05-05T19:00:00",
      "home_score": 108,
      "away_score": 101,
      "status": "final",
      "source": "openclaw"
    }
  ]
}
```

## Betting Logic Summary

The system does not blindly bet every edge. It combines:

- market implied probability
- recent team strength
- home court adjustment
- news impact
- historical learning penalties
- confidence score
- no-bet PASS filter

Signal levels:

```text
STRONG BET = stronger edge + higher confidence
MEDIUM BET = acceptable edge + acceptable confidence
PASS       = not enough value or confidence
```

## Important Reminder

This project is for analysis and tracking. Betting always has risk. A strong signal is not a guaranteed win.
