-- Migration 004: Supply intervals and operational events tables
-- First-class power continuity tracking and discrete milestone logging

CREATE TABLE IF NOT EXISTS supply_intervals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    meter_id INTEGER NOT NULL,
    source TEXT NOT NULL CHECK (source IN ('GRID', 'DG', 'UNKNOWN', 'GAP')),
    started_at_utc TEXT NOT NULL,
    ended_at_utc TEXT NOT NULL,
    duration_seconds REAL NOT NULL DEFAULT 0.0,
    start_kwh REAL,
    end_kwh REAL,
    kwh_consumed REAL DEFAULT 0.0,
    start_snapshot_id INTEGER REFERENCES snapshots(id) ON DELETE SET NULL,
    end_snapshot_id INTEGER REFERENCES snapshots(id) ON DELETE SET NULL,
    is_active INTEGER NOT NULL DEFAULT 0 CHECK (is_active IN (0, 1)),
    CONSTRAINT chk_interval_valid CHECK (ended_at_utc >= started_at_utc)
);

CREATE INDEX IF NOT EXISTS idx_intervals_meter_started ON supply_intervals(meter_id, started_at_utc DESC);
CREATE INDEX IF NOT EXISTS idx_intervals_active ON supply_intervals(meter_id, is_active);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    meter_id INTEGER,
    event_type TEXT NOT NULL,
    category TEXT NOT NULL CHECK (category IN ('Power', 'Billing', 'Network', 'Relay')),
    severity TEXT NOT NULL CHECK (severity IN ('info', 'warning', 'alert')),
    occurred_at_utc TEXT NOT NULL,
    title TEXT NOT NULL,
    details TEXT NOT NULL,
    previous_value TEXT,
    current_value TEXT,
    metadata_json TEXT,
    snapshot_id INTEGER REFERENCES snapshots(id) ON DELETE SET NULL,
    poll_event_id INTEGER REFERENCES poll_events(id) ON DELETE SET NULL,
    created_at_utc TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_meter_occurred ON events(meter_id, occurred_at_utc DESC);
CREATE INDEX IF NOT EXISTS idx_events_category ON events(category, occurred_at_utc DESC);
