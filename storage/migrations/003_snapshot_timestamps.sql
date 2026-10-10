-- 003_snapshot_timestamps.sql
-- Add explicit attempted_at_utc, received_at_utc, and persisted_at_utc timestamps
-- to snapshots table for fine-grained telemetry tracking.

ALTER TABLE snapshots ADD COLUMN attempted_at_utc TEXT;
ALTER TABLE snapshots ADD COLUMN received_at_utc TEXT;
ALTER TABLE snapshots ADD COLUMN persisted_at_utc TEXT;
