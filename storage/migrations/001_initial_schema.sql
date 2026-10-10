-- 001_initial_schema.sql
-- Snapshot storage for ELNet electricity meter readings

CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    observed_at_utc TEXT NOT NULL,
    meter_id INTEGER NOT NULL,
    device_id TEXT,
    meter_balance REAL,
    relay_status TEXT,
    kwh_rec_today REAL,
    kvah_rec_today REAL,
    kwh_del_today REAL,
    solar_today REAL,
    kvah_del_today REAL,
    kwh_rec_month REAL,
    kvah_rec_month REAL,
    kwh_del_month REAL,
    solar_month REAL,
    kvah_del_month REAL,
    current_day_eb REAL,
    current_day_dg REAL,
    current_day_solar REAL,
    fixed_charges_today REAL,
    current_month_eb REAL,
    current_month_dg REAL,
    current_month_solar REAL,
    fixed_charges_month REAL,
    is_eb INTEGER,
    is_dg INTEGER,
    is_solar INTEGER,
    is_fixed_charge INTEGER,
    is_coil_enable INTEGER,
    eb_dg_status INTEGER,
    kwh_kvah_status INTEGER,
    meter_sn TEXT
);

CREATE INDEX IF NOT EXISTS idx_snapshots_observed ON snapshots(observed_at_utc);
CREATE INDEX IF NOT EXISTS idx_snapshots_meter_observed ON snapshots(meter_id, observed_at_utc);
