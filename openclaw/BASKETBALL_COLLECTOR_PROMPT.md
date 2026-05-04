# OpenClaw Basketball Collector Prompt

Use Chrome to collect basketball betting analytics data for today's NBA games.

Your job is NOT to place bets.
Your job is to collect information, normalize it, and send structured JSON to the local backend.

Backend base URL:

```text
http://localhost:8000
```

## Sources to collect

Use public websites available in Chrome for:

1. Today's NBA schedule
2. Moneyline odds
3. Injury/news context
4. Final scores / live completed results

Preferred information:

- home_team
- away_team
- game_time
- home_odds as decimal odds
- away_odds as decimal odds
- injury/news signals
- final score when available

## Important rules

- Do not guess missing odds.
- If odds are American format, convert to decimal.
- If a player is questionable/doubtful/out, create a news signal.
- If final score is available, send it to results import.
- Return and send only clean JSON.
- Do not place any bets.
- Do not click betting buttons.
- Do not login to sportsbooks.

## American odds conversion

Positive American odds:

```text
decimal = 1 + american / 100
```

Negative American odds:

```text
decimal = 1 + 100 / abs(american)
```

Examples:

```text
+120 = 2.20
-150 = 1.67
-110 = 1.91
```

## Send games and odds

POST:

```text
http://localhost:8000/games/import
```

Payload format:

```json
{
  "games": [
    {
      "home_team": "Los Angeles Lakers",
      "away_team": "Boston Celtics",
      "game_time": "2026-05-04T19:30:00",
      "home_odds": 1.90,
      "away_odds": 2.05,
      "status": "scheduled",
      "source": "openclaw_chrome"
    }
  ]
}
```

## Send injury/news signals

POST:

```text
http://localhost:8000/news-signal
```

Payload format:

```json
{
  "league": "NBA",
  "team": "Los Angeles Lakers",
  "player": "LeBron James",
  "signal_type": "injury",
  "signal_text": "Questionable with ankle injury before tonight's game",
  "impact_score": -5,
  "source": "openclaw_chrome"
}
```

## Impact score guide

Use this scale:

```text
+8 to +10 = very positive team news
+4 to +7  = positive team news
+1 to +3  = small positive news
0         = neutral
-1 to -3  = small negative news
-4 to -6  = important negative news
-7 to -10 = major negative news / star player out
```

Examples:

```text
Star player OUT = -8
Starter doubtful = -6
Starter questionable = -4
Bench player out = -2
Key player returns = +5
Full roster / no injury concern = +2
```

## Send results

POST:

```text
http://localhost:8000/results/import
```

Payload format:

```json
{
  "results": [
    {
      "home_team": "Los Angeles Lakers",
      "away_team": "Boston Celtics",
      "home_score": 110,
      "away_score": 102,
      "status": "final",
      "source": "openclaw_chrome"
    }
  ]
}
```

## Daily workflow

Run this collection cycle several times per day:

1. Morning: collect schedule, odds, injuries
2. Afternoon: update odds and injuries
3. 30-60 minutes before games: update odds and injury status
4. After games: collect final scores

## Output goal

At the end, confirm:

```json
{
  "games_sent": 0,
  "signals_sent": 0,
  "results_sent": 0,
  "notes": "short summary"
}
```
