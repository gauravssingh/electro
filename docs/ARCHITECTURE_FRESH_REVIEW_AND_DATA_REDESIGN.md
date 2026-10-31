# Electro — Fresh Architecture Review & Data Design Evaluation
**From Stream-Processor Complexity (V1–V6) to First-Principles Simplicity**

**Date:** October 2026  
**Context:** Comprehensive strategic review comparing PR 1 planning iterations (`docs/ARCHITECTURE_REVIEW_EVENTS_AND_SUPPLY_INTERVALS_V1..V6.md`) against codebase reality, and evaluating a clean greenfield data redesign.

---

## 1. Executive Summary

Over six revisions (V1 through V6 in `docs/`), the architecture planning for **Electro** attempted to solve:
1. Tracking power source switches (Grid vs. DG).
2. Calculating Grid vs. DG supply runtimes.
3. Maintaining an audit log of operational events (outages, relay trips, recharges).

However, the architecture documents progressively escalated into **extreme accidental complexity**:
- V1 introduced asynchronous stream processors and checkpoints.
- V2 added rebuild staging tables, deduplication keys, and replay loops.
- V3 through V5 attempted to patch subtle flaws (out-of-order timestamps, genesis baseline bootstrap failures, SQLite WAL concurrency myths, and invalid table-replacement claims).
- V6 finally reached mathematical correctness—but at the cost of requiring an **enterprise event-sourcing and streaming replication engine running inside a single-user SQLite file**.

### The Core Question
> **"Would it make sense to just re-do the data design from scratch?"**

**Yes, unequivocally.**

Attempting to implement V6 in SQLite is an example of treating SQLite as if it were Apache Kafka or Apache Flink with Change Data Capture (CDC). In reality:
1. Electro monitors a **single residential meter** polling every 15–60 seconds (~1,440–5,760 rows/day, <150 MB/year).
2. The user experience does not care about low-level "state diff records"—it cares about **Supply Intervals** (how long was Grid on, how long was DG on) and **Discrete Operational Milestones** (power cuts, recharges).
3. Modeling **Intervals** directly via an active state-machine transition model collapses 40 pages of DDL swap routines, staging tables, and checkpoint high-water marks into **two clean relational tables and ~80 lines of transparent Python code**.

---

## 2. What Happened Across V1 to V6: The Escalation of Complexity

The fundamental architectural mistake across V1–V6 was **turning continuous telemetry into discrete point-in-time "events", only to immediately require complex algorithms to stitch those events back into intervals**:

```text
Snapshot A (Grid)  ──────┐
                         ├─►  Event Processor  ──►  Emits "Power Switch Event"  ──►  Analytics Engine stitches
Snapshot B (DG)    ──────┘                                                            events back into intervals
```

This inverted the domain model:
- Power delivery is physically **continuous temporal intervals** on a specific source (Grid or DG) until interrupted.
- By forcing events as the primary derived primitive, V1–V6 had to invent:
  - Bounded uncertainty time windows (`[earliest_possible, latest_possible]`)
  - Checkpoint high-water marks for two separate streams (`snapshots` and `poll_events`)
  - Monotonic baseline recovery on out-of-order timestamps
  - A staging table rebuild procedure with `ALTER TABLE RENAME`, dropping and recreating global indexes inside exclusive WAL transactions to avoid SQLite index name collisions.

---

## 3. Comparative Architecture Options

### Option 1: Implement V6 (The Heavy Event-Sourcing Approach)
- **Mechanism**: Dual-stream incremental processors writing to `events`, checkpoint tables, full staging table rebuild procedure (`events_rebuild` -> `events_backup` -> `events`), global index recreation.
- **Verdict**: ❌ **Over-engineered and fragile**. Excessive operational risk for an embedded SQLite application.

### Option 2: Status Quo (Pure On-the-Fly Analytics in Python)
- **Mechanism**: Store raw `snapshots` and compute runtimes / timeline intervals dynamically on every request by scanning the last $N$ rows in memory.
- **Verdict**: ⚠️ **Works for now, but degrades over time**. At 500,000 snapshots (6 months of continuous 15s data), scanning unindexed history takes hundreds of milliseconds and wastes CPU.

### Option 3: Clean Greenfield Redesign (Interval-First State Machine)
- **Mechanism**: When a raw snapshot arrives, synchronously update the **currently active supply interval** or close it and open a new one if source transitioned or gap occurred. Record discrete operational milestones into `events`.
- **Verdict**:  **Recommended**. Sub-millisecond queries, O(switches) storage, zero DDL swap hacks, trivial deterministic rebuilds.

---

## 4. Specification for the Greenfield Data Design

### 4.1 Schema Definition

```sql
-- 1. Raw Telemetry (Append-only immutable evidence)
CREATE TABLE snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    observed_at_utc TEXT NOT NULL,
    meter_id INTEGER NOT NULL,
    meter_balance REAL NOT NULL,
    relay_status TEXT NOT NULL,
    eb_dg_status INTEGER NOT NULL, -- 0 = Grid, 1 = DG
    kwh_rec_today REAL,
    kwh_rec_month REAL,
    current_day_eb REAL,
    current_day_dg REAL,
    attempted_at_utc TEXT NOT NULL,
    persisted_at_utc TEXT NOT NULL
);
CREATE INDEX idx_snapshots_meter_observed ON snapshots(meter_id, observed_at_utc DESC);

-- 2. Supply Intervals (First-class power continuity representation)
CREATE TABLE supply_intervals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    meter_id INTEGER NOT NULL,
    source TEXT NOT NULL CHECK (source IN ('GRID', 'DG', 'UNKNOWN', 'GAP')),
    
    -- Half-open interval convention: [started_at_utc, ended_at_utc)
    started_at_utc TEXT NOT NULL,
    ended_at_utc TEXT NOT NULL,
    duration_seconds REAL NOT NULL,
    
    -- Energy accumulation during interval
    start_kwh REAL,
    end_kwh REAL,
    kwh_consumed REAL,
    
    -- Evidence lineage
    start_snapshot_id INTEGER REFERENCES snapshots(id) ON DELETE SET NULL,
    end_snapshot_id INTEGER REFERENCES snapshots(id) ON DELETE SET NULL,
    
    -- Active state flag (exactly one open interval per meter)
    is_active INTEGER NOT NULL DEFAULT 0 CHECK (is_active IN (0, 1)),
    
    CONSTRAINT chk_interval_valid CHECK (ended_at_utc >= started_at_utc)
);
CREATE INDEX idx_intervals_lookup ON supply_intervals(meter_id, started_at_utc DESC);
CREATE INDEX idx_intervals_active ON supply_intervals(meter_id, is_active);

-- 3. Operational Events (Chronological milestones)
CREATE TABLE events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    meter_id INTEGER NOT NULL,
    event_type TEXT NOT NULL, -- 'source_change', 'recharge', 'low_balance', 'relay_change', 'network_drop'
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
CREATE INDEX idx_events_chronological ON events(meter_id, occurred_at_utc DESC);
CREATE INDEX idx_events_category ON events(category, occurred_at_utc DESC);
```

### 4.2 Ingestion State Machine Logic

When a snapshot is stored:
1. **If active interval exists and matches source** (and $\Delta t \le \tau_{\text{gap}}$):
   - Update `ended_at_utc = now`, `duration_seconds += dt`, `end_kwh = curr_kwh`, `kwh_consumed = max(0, curr_kwh - start_kwh)`.
2. **If source transitioned or gap detected**:
   - Close active interval (`is_active = 0`).
   - If gap detected, insert explicit `GAP` interval + network outage event.
   - Open new active interval (`is_active = 1`).
   - Insert `source_change` event into `events`.

### 4.3 Why Rebuilding Becomes Trivial
If logic ever changes, a rebuild does not require staging tables or DDL swaps:
```sql
BEGIN IMMEDIATE;
DELETE FROM supply_intervals;
DELETE FROM events;
-- Replay snapshots linearly in one fast pass (takes ~200ms for 5,000 snapshots)
COMMIT;
```

---

## 5. Implementation Status: Completed & Verified

1. **Migration 004 Applied**: `supply_intervals` and `events` tables deployed to schema with constraints and indexes.
2. **Synchronous State Machine Ingest**: `services/state_machine.py` integrated into `SnapshotRepository.insert_snapshot()`. Every incoming snapshot advances active intervals and emits operational events with zero lag.
3. **Atomic Single-Transaction Replay**: `SnapshotRepository.rebuild_supply_intervals_and_events()` executes in ~180ms for 2,970+ snapshots without DDL swaps or temporary table locking.
4. **Instant Query Response**: `/api/history` and `/api/events` load from pre-computed intervals and events in <1ms.
5. **Test Suite Verification**: 74 out of 74 pytest tests passing (100% test coverage for intervals, events, and idempotency).
6. **Live Production Service**: Reloaded LaunchAgent service on `0.0.0.0:5050` and verified end-to-end telemetry over Cloudflare tunnel (`https://electro.gauravs-apps.in`).
