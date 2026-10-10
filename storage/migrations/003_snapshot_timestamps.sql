-- 003_snapshot_timestamps.sql
-- Add explicit attempted_at_utc, received_at_utc, and persisted_at_utc timestamps
-- to snapshots table for fine-grained telemetry tracking.
--
-- Field semantics:
-- - attempted_at_utc: UTC timestamp when upstream HTTP request was initiated.
-- - received_at_utc: UTC timestamp when upstream response was received and decoded.
-- - persisted_at_utc: UTC timestamp when record was written to local SQLite database.
--
-- Legacy row policy:
-- For historical rows captured before this migration, these three columns remain NULL.
-- They are intentionally NOT backfilled because exact upstream network timing and local
-- insertion timestamps cannot be reconstructed accurately or honestly.
-- All queries continue to support observed_at_utc as the primary observation timestamp.

ALTER TABLE snapshots ADD COLUMN attempted_at_utc TEXT;
ALTER TABLE snapshots ADD COLUMN received_at_utc TEXT;
ALTER TABLE snapshots ADD COLUMN persisted_at_utc TEXT;
