-- 002_poll_events.sql
-- Lightweight tracking of poll execution attempts, latencies, and failures

CREATE TABLE IF NOT EXISTS poll_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    attempted_at_utc TEXT NOT NULL,
    success INTEGER NOT NULL,
    duration_ms INTEGER,
    error_category TEXT,
    error_message TEXT,
    meter_id INTEGER
);

CREATE INDEX IF NOT EXISTS idx_poll_events_attempted ON poll_events(attempted_at_utc);
CREATE INDEX IF NOT EXISTS idx_poll_events_success ON poll_events(success, attempted_at_utc);
