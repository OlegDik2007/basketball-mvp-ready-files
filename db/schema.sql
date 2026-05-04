CREATE TABLE IF NOT EXISTS games (
    id SERIAL PRIMARY KEY,
    home_team TEXT,
    away_team TEXT,
    game_time TIMESTAMP,
    home_odds NUMERIC,
    away_odds NUMERIC
);

CREATE TABLE IF NOT EXISTS predictions (
    id SERIAL PRIMARY KEY,
    game_id INT,
    win_prob_home NUMERIC,
    win_prob_away NUMERIC,
    edge_home NUMERIC,
    recommendation TEXT
);

CREATE TABLE IF NOT EXISTS news_signals (
    id SERIAL PRIMARY KEY,
    team TEXT,
    player TEXT,
    signal_type TEXT,
    signal_text TEXT,
    impact_score NUMERIC,
    created_at TIMESTAMP DEFAULT NOW()
);
