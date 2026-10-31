# Electro — Architecture Review: Event Persistence & Supply Interval Model

**Document Type:** Technical Architecture Review & Implementation Proposal  
**Target Repository:** `gauravssingh/electro`  
**Working Branch & Commit:** `main` (`935d510`)  
**Status:** Under Review (Architecture Only — No code or schema modifications applied)  

---

## 1. Executive Summary

Electro is a single-user, self-hosted macOS monitoring service for an ELNet electricity meter. It runs as a persistent LaunchAgent daemon executing Python/Flask with SQLite in Write-Ahead Logging (WAL) mode, paired with a vanilla JS/Tailwind web UI and a standalone native Rust CLI (`electro`).

### The Core Architectural Problem
In the current codebase, **neither events nor supply intervals exist as persistent database entities**:
1. **Events are reconstructed dynamically on every query**: `SnapshotRepository.get_unified_events()` executes a linear scan over up to 5,000 raw rows from `snapshots` and up to 5,000 raw rows from `poll_events`, detecting transitions in Python memory on every call to `/api/events`.
2. **Supply analytics and transition timelines are computed ephemerally**: `compute_source_duration_analytics()` pairwise loops over raw snapshot slices on every `/api/history` call, recalculating durations and splitting transition windows on the fly.
3. **No historical state intervals are saved**: Queries for different time ranges (`24h`, `7d`, `30d`, `all`) re-slice raw observations without preserving open/closed state continuity across time boundaries.
4. **Desktop alerts rely on ephemeral in-memory latches**: `NotificationService` tracks state via in-memory instance variables (`_last_source`, `_low_balance_fired`, `_stale_fired`). A daemon restart resets these latches, creating a risk of duplicate alert triggers or lost transition context.

### The Proposed Two-Layer Solution
We propose introducing two distinct, append-only/state-managed derived SQLite tables behind the raw telemetry layer:

```
┌────────────────────────────────────────────────────────────────────────┐
│                   Layer 1: Raw Telemetry (Source of Truth)             │
│   snapshots (raw observations)   │   poll_events (health/telemetry)    │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │ Derived via Deterministic Processor
         ┌──────────────────────────┴──────────────────────────┐
         ▼                                                     ▼
┌─────────────────────────────────┐   ┌──────────────────────────────────┐
│      Layer 2: Events Layer      │   │  Layer 3: Supply Intervals Layer │
│  events (discrete state changes)│   │ supply_intervals (state durations│
│  - Grid ↔ DG switches           │   │ - Source: GRID, DG, UNKNOWN      │
│  - Relay ON ↔ OFF               │   │ - Status: OPEN, CLOSED, UNCERTAIN│
│  - Telemetry drops & recoveries │   │ - Coverage & uncertainty seconds │
│  - Balance recharge & warnings  │   │ - Audit foreign keys to snapshots│
└─────────────────────────────────┘   └──────────────────────────────────┘
```

* **Preservation of Raw Truth**: The existing `snapshots` and `poll_events` tables remain completely immutable, pristine sources of truth.
* **Separation of Concerns**: Events represent *instantaneous/bounded state transitions*; supply intervals represent *continuous elapsed periods under a verified supply state*.
* **Explicit Uncertainty Modeling**: When telemetry gaps exceed a maximum inference threshold, time is never arbitrarily assumed to continue on the previous source; it is explicitly tracked as `UNKNOWN` or bounded with `uncertainty_seconds`.
* **Zero Infrastructure Overhead**: Runs purely on the existing SQLite engine with transactions, requiring no Docker, Redis, queue daemons, or rollup background workers.

---

## 2. Current Implementation Findings

An exhaustive inspection of the local checkout (`commit 935d510`) reveals the following component-by-component reality:

| Area | Component in Codebase | Concrete Implementation Details |
|---|---|---|
| **1. Application Entry Point** | `app.py` | Factory function `create_app()`. Initializes `SnapshotRepository`, `ELNetClient`, `NotificationService`, and `PollingService`. Registers routes: `/`, `/api/dashboard`, `/api/history`, `/api/events`, `/api/forecast`, `/api/export/csv`, `/api/refresh`, `/healthz`, `/api/health`. Middleware enforces constant-time bearer token check via `hmac.compare_digest`. |
| **2. Background Polling Service** | `services/polling.py` | Monotonic cadence worker thread (`ELNetPoller`). Uses advisory cross-process file lock via `fcntl.flock` on `.electro.poller.lock` to ensure only one worker polls even across Gunicorn forks. Manages backoff upon failure. Calls `notifier.on_snapshot()` on success and `notifier.on_poll_failure()` on failure. |
| **3. Upstream Client & Normalization** | `elnet/client.py`, `services/normalization.py` | `ELNetClient` handles encrypted login (`/api/Dashboard/login`) and encrypted HomeData retrieval (`/api/Dashboard/HomeData`). `normalize_home_data()` strictly converts raw JSON keys into strongly typed snake_case dictionary matching SQLite column names. |
| **4. SQLite Connection & Migrations** | `storage/db.py` | `get_db_connection()` context manager sets `PRAGMA journal_mode = WAL`, `PRAGMA busy_timeout = 5000`, `PRAGMA foreign_keys = ON`. Permissions on directory and db file are set to `0700` and `0600`. `run_migrations()` sorts and applies `.sql` files recorded in `schema_migrations`. |
| **5. Database Schema & Tables** | `storage/migrations/*.sql` | Only 3 tables exist in the schema: `schema_migrations`, `snapshots`, and `poll_events`. **No tables exist for events or supply intervals.** |
| **6. Repository Layer** | `storage/repository.py` | Provides CRUD methods: `insert_snapshot()`, `get_latest_snapshot()`, `get_history()`, `count_snapshots()`, `record_poll_event()`, `get_recent_poll_events()`, `get_poll_health_stats()`, `export_snapshots_csv()`, and `get_unified_events()`. |
| **7. Event Generation** | `storage/repository.py#L261-L430` | **On-the-fly ephemeral generation**: `get_unified_events()` queries the last 5,000 snapshots and last 5,000 poll events, reconstructs transitions in Python memory, sorts them by timestamp descending, and returns the slice. **Nothing is written to an events table.** |
| **8. Source-Transition & Duration Analytics** | `services/analytics.py` | `compute_source_duration_analytics()` loops pairwise through raw snapshot dictionaries. Splits normal-cadence transition intervals equally ($dt/2$ to each source) and flags gaps exceeding `max_gap_seconds`. |
| **9. Balance Forecast Engine** | `services/analytics.py#L195-L330` | `compute_balance_forecast()` computes consumption burn rate ($\text{₹/hr}$, $\text{₹/day}$) across valid intervals, explicitly filtering out positive balance jumps (recharges) so burn rate reflects true power consumption. |
| **10. Local Notifications** | `services/notifications.py` | Native macOS notifications via `osascript`. Alerts on source transitions (Grid $\leftrightarrow$ DG), balance crossing $< \text{₹}500$, and $\ge 3$ consecutive poll failures. Dedupes via in-memory latches. |
| **11. Frontend Architecture** | `templates/dashboard.html`, `static/js/dashboard.js` | 4 distinct view tabs: Overview, Analytics, Supply, History. Connects to `/api/dashboard`, `/api/history`, `/api/forecast`, `/api/events`. History view renders the unified event stream with category pills (`all`, `Power`, `Relay`, `Network`, `Billing`). |
| **12. Standalone Native CLI** | `cli/src/` | Rust crate compiled to `~/.cargo/bin/electro`. Subcommands: `init`, `status`, `summary`, `transitions`, `health`, `refresh`, `export`, `forecast`, `events`. Communicates strictly over REST API; completely decoupled from SQLite database file. |
| **13. Daemon / LaunchAgent Lifecycle** | `scripts/launchd/` | Service `gui/501/com.gauravs.electro` runs Gunicorn with 1 worker and 4 threads. Automatically restarts on crash (`KeepAlive.Crashed = true`). Reloaded safely via `kill -HUP`. |

---

## 3. Implemented vs Missing Functionality

### Status Matrix

| Functionality | Status | Production Implementation | Test / Doc Presence | Missing / Gap |
|---|---|---|---|---|
| **Raw Telemetry Storage** | **Implemented** | `snapshots` table in SQLite with 34 columns. | `test_repository.py`, `test_normalization.py` | None. Fully functional. |
| **Poll Health Tracking** | **Implemented** | `poll_events` table tracking attempts, latency, errors. | `test_polling.py`, `test_timestamps_and_health.py` | None. Fully functional. |
| **Source Transition Detection** | **Partially Implemented** | Dynamically computed via `compute_source_duration_analytics()` and `get_unified_events()`. | Tested in `test_analytics.py` and `test_repository.py`. | **Not persisted**. Recomputed on every query. Truncated if snapshots exceed 5,000. |
| **Events Persistence** | **Missing** | None. No `events` table exists. | Tests only assert against the dynamic query method. | **Missing dedicated table, schema, indexes, and event lifecycle.** |
| **Supply Intervals Persistence** | **Missing** | None. No `supply_intervals` table exists. | Analytical heuristic exists in `services/analytics.py`. | **Missing state machine, interval schema, gap tracking, and backfill.** |
| **Relay State Change Tracking** | **Partially Implemented** | Dynamically detected in `get_unified_events()`. | Tested in `test_repository.py`. | Ephemeral. Not stored as discrete event entity. |
| **Balance Recharge Events** | **Partially Implemented** | Dynamically detected in `get_unified_events()` ($\ge \text{₹}20$ jump). | Tested in `test_repository.py`. | Rule is a hardcoded heuristic in repository query; not a validated billing event. |
| **Connection Drop/Recovery Tracking**| **Partially Implemented** | `poll_events` records raw failures; `get_unified_events()` pairs them in Python. | Tested in `test_repository.py`. | No persistent lifecycle or linking of outages to resolution events. |
| **Historical Rebuild Utility** | **Missing** | None. | None. | No mechanism to rebuild derived state if calculation logic changes. |

---

## 4. Current Telemetry Schema Assessment

### Table Definition: `snapshots`
The table is created across `001_initial_schema.sql` and `003_snapshot_timestamps.sql`:

```sql
CREATE TABLE snapshots (
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
    meter_sn TEXT,
    attempted_at_utc TEXT,
    received_at_utc TEXT,
    persisted_at_utc TEXT
);

CREATE INDEX idx_snapshots_observed ON snapshots(observed_at_utc);
CREATE INDEX idx_snapshots_meter_observed ON snapshots(meter_id, observed_at_utc);
```

### Assessment of Schema Characteristics
1. **Primary Key**: `id INTEGER PRIMARY KEY AUTOINCREMENT`. Provides an immutable, monotonically increasing integer surrogate key. **Ideal as a foreign key (`snapshot_id`) for derived layers.**
2. **Identification of Source Status**:
   * `eb_dg_status`: `0` = Grid (EB), `1` = Generator (DG), `NULL` = Unknown or invalid.
   * `is_eb` and `is_dg`: Configuration/hardware capability flags, NOT live power source status.
   * **Crucial Rule**: Live supply status must be read **exclusively from `eb_dg_status`**.
3. **Separation of Power Source and Relay State**:
   * `relay_status` is stored as `TEXT` (`"ON"`, `"OFF"`).
   * Verified fact: `RelStatus` does not indicate supply source. A relay can be `"ON"` while running on DG or Grid.
4. **Timestamp Semantics and Lifecycle**:
   * `attempted_at_utc`: UTC timestamp when upstream HTTP query was initiated. (NULL on legacy rows before migration 003).
   * `received_at_utc`: UTC timestamp when upstream JSON payload was received and parsed. (NULL on legacy rows).
   * `persisted_at_utc`: UTC timestamp when SQLite record was inserted. (NULL on legacy rows).
   * `observed_at_utc`: **The canonical observation timestamp.** In `services/normalization.py`, this equals `received_at_utc` for modern rows. For legacy rows, it is the sole populated timestamp.
   * **Architectural Rule**: All interval and event models must use `observed_at_utc` as the temporal anchor for backward-compatibility with all 1,190+ historical snapshots.
5. **Deduplication Gap**:
   * There is no unique constraint on `(meter_id, observed_at_utc)`. In practice, monotonic collector polling prevents duplicate captures, but the schema does not enforce uniqueness at the database level.

---

## 5. Proposed Events Layer Design

The Events Layer must represent **discrete, verified state transitions and operational lifecycle milestones**. It must be append-only and strictly traceable back to underlying observations.

### Concrete SQLite Schema: `events`
*(Proposal only — do not execute)*

```sql
-- Migration: 004_events.sql
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    category TEXT NOT NULL,
    severity TEXT NOT NULL,
    
    -- Temporal Boundaries (Preserving Uncertainty)
    occurred_at_utc TEXT NOT NULL,          -- Best-estimate timestamp (ISO-8601 UTC)
    detected_at_utc TEXT NOT NULL,          -- Timestamp when processor detected the event
    earliest_possible_at_utc TEXT,          -- Left boundary of observation gap (t_{n-1})
    latest_possible_at_utc TEXT,            -- Right boundary of observation gap (t_n)
    
    -- Traceability to Underlying Observations
    source_snapshot_id INTEGER,             -- Observation establishing new state (snapshots.id)
    previous_snapshot_id INTEGER,           -- Prior observation preceding transition (snapshots.id)
    poll_event_id INTEGER,                  -- Polling attempt associated with network events (poll_events.id)
    
    -- Transition State Values
    previous_value TEXT,                    -- Prior state representation (e.g. "GRID", "ON", "2250.0")
    current_value TEXT,                     -- New state representation (e.g. "DG", "OFF", "450.0")
    
    -- Contextual Details & Idempotency
    title TEXT NOT NULL,                    -- Human-readable short title
    details TEXT NOT NULL,                  -- Descriptive explanation
    metadata_json TEXT,                     -- Structured JSON (e.g. {"gap_seconds": 15.2, "delta": -50.0})
    dedupe_key TEXT NOT NULL UNIQUE,        -- Deterministic key preventing duplicate insertion
    
    created_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    
    FOREIGN KEY (source_snapshot_id) REFERENCES snapshots(id) ON DELETE SET NULL,
    FOREIGN KEY (previous_snapshot_id) REFERENCES snapshots(id) ON DELETE SET NULL,
    FOREIGN KEY (poll_event_id) REFERENCES poll_events(id) ON DELETE SET NULL
);

-- Performance & Retrieval Indexes
CREATE INDEX IF NOT EXISTS idx_events_occurred ON events(occurred_at_utc DESC);
CREATE INDEX IF NOT EXISTS idx_events_category_occurred ON events(category, occurred_at_utc DESC);
CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type);
CREATE INDEX IF NOT EXISTS idx_events_source_snapshot ON events(source_snapshot_id);
```

### Schema Justifications
* `dedupe_key TEXT UNIQUE`: Ensures strict idempotency. For a transition between snapshot 100 and 101, `dedupe_key = "trans:source:100:101"`. Re-running the processor will perform `INSERT OR IGNORE` without duplication.
* `earliest_possible_at_utc` and `latest_possible_at_utc`: Solves the physical reality that meter polling is discrete. If Grid was seen at 10:00 and DG at 10:01, the event physically occurred within $(10:00, 10:01]$.
* `FOREIGN KEY ... ON DELETE SET NULL`: Ensures referential integrity while preventing cascading destruction if a snapshot were ever archived.

---

## 6. Proposed Event Taxonomy and Processing Rules

### Classification Matrix

| Event Type | Category | Severity | Class | Trigger Condition & Evidence | Occurrence Time Determination | Deduplication Key Format | Rebuildable? |
|---|---|---|---|---|---|---|---|
| `source_transition_grid_to_dg` | `Power` | `alert` | **Observed** | Consecutive snapshots: $S_{n-1}.\text{eb\_dg\_status} = 0$ and $S_n.\text{eb\_dg\_status} = 1$. | `earliest` = $S_{n-1}.t$, `latest` = $S_n.t$, `occurred` = midpoint if $\Delta t \le \text{gap}$, else $S_n.t$. | `src:0:1:{prev_id}:{curr_id}` | **Yes** (100% from `snapshots`) |
| `source_transition_dg_to_grid` | `Power` | `info` | **Observed** | Consecutive snapshots: $S_{n-1}.\text{eb\_dg\_status} = 1$ and $S_n.\text{eb\_dg\_status} = 0$. | `earliest` = $S_{n-1}.t$, `latest` = $S_n.t$, `occurred` = midpoint if $\Delta t \le \text{gap}$, else $S_n.t$. | `src:1:0:{prev_id}:{curr_id}` | **Yes** (100% from `snapshots`) |
| `source_became_unknown` | `Power` | `warning` | **Derived** | $S_{n-1}.\text{eb\_dg\_status} \in (0,1)$ and $S_n.\text{eb\_dg\_status} \notin (0,1)$. | $S_n.\text{observed\_at\_utc}$. | `src:known:unknown:{prev_id}:{curr_id}` | **Yes** (100% from `snapshots`) |
| `relay_state_changed` | `Relay` | `alert` / `info` | **Observed** | Consecutive snapshots: $S_{n-1}.\text{relay\_status} \ne S_n.\text{relay\_status}$. | $S_n.\text{observed\_at\_utc}$. | `rel:{prev_status}:{curr_status}:{prev_id}:{curr_id}` | **Yes** (100% from `snapshots`) |
| `low_balance_threshold_crossed` | `Billing` | `warning` | **Derived** | Consecutive snapshots: $S_{n-1}.\text{balance} \ge 500.0$ and $S_n.\text{balance} < 500.0$. | $S_n.\text{observed\_at\_utc}$. | `bal:low:{prev_id}:{curr_id}` | **Yes** (100% from `snapshots`) |
| `balance_recharge_detected` | `Billing` | `info` | **Derived** | Consecutive snapshots: $S_n.\text{balance} - S_{n-1}.\text{balance} \ge 100.0$ (configurable). | $S_n.\text{observed\_at\_utc}$. | `bal:recharge:{prev_id}:{curr_id}` | **Yes** (100% from `snapshots`) |
| `telemetry_connection_dropped` | `Network` | `warning` | **Derived** | First failure after success in `poll_events`: $P_{n-1}.\text{success}=1$ and $P_n.\text{success}=0$. | $P_n.\text{attempted\_at\_utc}$. | `poll:drop:{prev_id}:{curr_id}` | **Yes** (100% from `poll_events`) |
| `telemetry_connection_restored` | `Network` | `info` | **Derived** | First success after failure streak in `poll_events`: $P_{n-1}.\text{success}=0$ and $P_n.\text{success}=1$. | $P_n.\text{attempted\_at\_utc}$. | `poll:recovery:{prev_id}:{curr_id}` | **Yes** (100% from `poll_events`) |

---

## 7. Proposed Supply Intervals Layer Design

Supply intervals model the **continuous state periods of electricity supply**. They are state-driven intervals rather than arbitrary calendar or hourly buckets.

### Concrete SQLite Schema: `supply_intervals`
*(Proposal only — do not execute)*

```sql
-- Migration: 005_supply_intervals.sql
CREATE TABLE IF NOT EXISTS supply_intervals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    meter_id INTEGER NOT NULL,
    source TEXT NOT NULL,                  -- 'GRID', 'DG', or 'UNKNOWN'
    status TEXT NOT NULL,                  -- 'OPEN', 'CLOSED', or 'UNCERTAIN'
    
    -- Temporal Spans
    started_at_utc TEXT NOT NULL,          -- Observation timestamp establishing interval
    ended_at_utc TEXT NOT NULL,            -- Observation timestamp terminating/extending interval
    
    -- Quantitative Duration & Trust Metrics
    duration_seconds REAL NOT NULL,        -- Total elapsed seconds: (ended_at - started_at)
    coverage_seconds REAL NOT NULL,        -- Sum of monitored delta time supported by observations
    uncertainty_seconds REAL NOT NULL,     -- Sum of unobserved gap time during interval
    
    -- Traceability to Snapshots & Events
    start_snapshot_id INTEGER NOT NULL,    -- First snapshot in interval (snapshots.id)
    end_snapshot_id INTEGER NOT NULL,      -- Latest snapshot confirming interval (snapshots.id)
    start_event_id INTEGER,                -- Event that initiated interval (events.id)
    end_event_id INTEGER,                  -- Event that terminated interval (events.id)
    
    snapshot_count INTEGER NOT NULL DEFAULT 1,
    calculation_version INTEGER NOT NULL DEFAULT 1,
    
    created_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    
    FOREIGN KEY (start_snapshot_id) REFERENCES snapshots(id) ON DELETE RESTRICT,
    FOREIGN KEY (end_snapshot_id) REFERENCES snapshots(id) ON DELETE RESTRICT,
    FOREIGN KEY (start_event_id) REFERENCES events(id) ON DELETE SET NULL,
    FOREIGN KEY (end_event_id) REFERENCES events(id) ON DELETE SET NULL
);

-- Optimization Indexes for Range Analytics
CREATE INDEX IF NOT EXISTS idx_intervals_meter_dates ON supply_intervals(meter_id, started_at_utc, ended_at_utc);
CREATE INDEX IF NOT EXISTS idx_intervals_source ON supply_intervals(source, started_at_utc);
CREATE INDEX IF NOT EXISTS idx_intervals_status ON supply_intervals(status);
```

---

## 8. State-Transition and Gap-Handling Rules

### State-Transition Matrix

| Previous State | New Observation | Delta Time ($\Delta t$) | Interval Action | Event Action |
|---|---|---|---|---|
| `GRID` (OPEN) | `GRID` | $\le \text{max\_gap}$ | Extend open Grid interval: `ended_at_utc = S_new.t`, `duration += \Delta t`, `coverage += \Delta t`, `end_snapshot_id = S_new.id`. | None. |
| `GRID` (OPEN) | `GRID` | $> \text{max\_gap}$ | 1. Close open Grid interval at $S_{prev}.t$.<br>2. Create `UNKNOWN` interval spanning $[S_{prev}.t, S_{new}.t]$.<br>3. Open new Grid interval starting at $S_{new}.t$. | None. |
| `GRID` (OPEN) | `DG` | $\le \text{max\_gap}$ | 1. Close Grid interval at $S_{prev}.t$ (or midpoint).<br>2. Open new `DG` interval at $S_{new}.t$ (or midpoint). | Emit `source_transition_grid_to_dg`. |
| `GRID` (OPEN) | `DG` | $> \text{max\_gap}$ | 1. Close Grid interval at $S_{prev}.t$.<br>2. Create `UNKNOWN` interval for gap $[S_{prev}.t, S_{new}.t]$.<br>3. Open new `DG` interval starting at $S_{new}.t$. | Emit `source_transition_grid_to_dg` flagged as `is_gap = true`. |
| `DG` (OPEN) | `GRID` | $\le \text{max\_gap}$ | 1. Close `DG` interval at $S_{prev}.t$ (or midpoint).<br>2. Open new `GRID` interval at $S_{new}.t$ (or midpoint). | Emit `source_transition_dg_to_grid`. |
| `DG` (OPEN) | `DG` | $\le \text{max\_gap}$ | Extend open DG interval: `ended_at_utc = S_new.t`, `duration += \Delta t`, `coverage += \Delta t`, `end_snapshot_id = S_new.id`. | None. |
| `GRID` or `DG` | `UNKNOWN` / `NULL` | Any | 1. Close active interval at $S_{prev}.t$.<br>2. Open `UNKNOWN` interval starting at $S_{new}.t$. | Emit `source_became_unknown`. |
| `UNKNOWN` (OPEN) | `GRID` | Any | 1. Close `UNKNOWN` interval at $S_{prev}.t$.<br>2. Open `GRID` interval starting at $S_{new}.t$. | Emit `source_transition_dg_to_grid` (or restoration). |
| Any | Same Source after daemon restart | $\le \text{max\_gap}$ | Read open interval from DB; continue extending it seamlessly. | None. |

---

## 9. Processing Architecture

### Component Responsibility Model

```
services/
    polling.py                 <-- Polling loop, HTTP fetch, backoff, process locks
    normalization.py           <-- Normalizes raw ELNet JSON into strongly typed dict
    state_processor.py         <-- NEW: Pure state machine for events & supply intervals
    notifications.py           <-- macOS desktop alerts (triggered by state_processor)
    analytics.py               <-- Read-only query helpers (burn rate, daily statistics)

storage/
    db.py                      <-- SQLite connection context, WAL pragma, migrations
    repository.py              <-- All SQL queries (snapshots, events, intervals)
    migrations/
        001_initial_schema.sql
        002_poll_events.sql
        003_snapshot_timestamps.sql
        004_events.sql            <-- NEW
        005_supply_intervals.sql  <-- NEW
```

---

## 10. Transaction and Idempotency Strategy

### The 2-Step Checkpointed Transaction Model
* **Step 1 (Raw Persistence)**: Insert snapshot into `snapshots` (Commit). Raw telemetry is immediately secured on disk.
* **Step 2 (Derived Processing)**: Process events and intervals for newly inserted snapshot(s) up to `snapshot_id` (Commit). Record checkpoint `last_processed_snapshot_id`.

If derived processing ever encounters an uncaught exception, raw telemetry remains safely committed on disk. On the next cycle, the processor resumes from `last_processed_snapshot_id`.

---

## 11. Historical Backfill and Rebuild Strategy

1. **Non-Destructive Execution**: Raw `snapshots` and `poll_events` are strictly read-only.
2. **Chronological Stream Processing**: Rebuild processes historical snapshots ordered by `id ASC`.
3. **Audit Assertions**: $\sum \text{duration\_seconds} + \text{gap\_seconds} \approx \text{total elapsed chronological time}$, with exactly 1 `OPEN` interval at the head of the stream.

---

## 12. Existing API and Dashboard Impact

* `GET /api/events`: Replaces dynamic 10,000-row scan with indexed `SELECT * FROM events ORDER BY occurred_at_utc DESC LIMIT ?`. Response JSON format is 100% identical.
* `GET /api/history`: Retains raw `points` array for time series chart, but sources `transitions` directly from `events` table and runtimes from `supply_intervals`.
* `GET /api/supply/summary` *(New)*: Returns exact Grid/DG runtimes, coverage %, and transition counts.
* `GET /api/supply/intervals` *(New)*: Returns historical supply interval list for deep timeline inspection.

---

## 13. Detailed Test Matrix

Tests will cover all 10 state transition matrix cases, small vs long observation gaps, daemon restarts during open intervals, out-of-order rows, and deterministic multi-pass historical rebuilds.

---

## 14. Performance and SQLite Considerations

* Normal polling volume is ~5,760 snapshots/day (~2.1M/year).
* Events and supply intervals generate only ~1–15 rows/day (< 5,000 rows/year).
* Query performance will execute in $< 2\text{ms}$ over B-Tree indexes. Pre-aggregated minute/hour/day rollup tables are unnecessary.

---

## 15. Migration Strategy

Applied via `storage/migrations/004_events.sql` and `storage/migrations/005_supply_intervals.sql`. Safe and backward compatible.

---

## 16. Phased Implementation Plan

* **Phase 0**: Baseline & Test Scaffolding
* **Phase 1**: Events Layer Persistence & `/api/events` Cutover
* **Phase 2**: Supply Intervals Layer & Historical Backfill
* **Phase 3**: Analytics Migration & Dashboard/CLI Adoption

---

## 17. Open Design Decisions Requiring Product-Owner Approval

1. **Midpoint vs. New-Observation Boundary**: Recommend Midpoint with explicit $[t_{prev}, t_{curr}]$ boundaries preserved.
2. **Recharge Detection Threshold**: Recommend raising from current $\text{₹}20$ heuristic to $\text{₹}100$ or configurable `ELECTRO_RECHARGE_MIN_DELTA`.
3. **Maximum Inference Gap**: Recommend $2.5 \times \text{poll\_interval}$.

---

## 18. Recommended First Implementation Scope

* **PR 1**: `004_events.sql`, `StateProcessor`, collector hook, `repository.get_events()`, backfill script, test suite.
* **PR 2**: `005_supply_intervals.sql`, interval state machine, gap handling, analytics migration.

---

## 19. Final Explicit Recommendations

1. **Is the current telemetry model sufficient as the raw source of truth?** **Yes.** `snapshots` contains all physical readings and must remain the immutable ground truth.
2. **Should events be persisted independently from supply intervals?** **Yes.** Events represent discrete transitions; intervals represent continuous state periods.
3. **Should supply intervals be the first analytical aggregate?** **Yes.** Intervals reflect physical supply reality without arbitrary bucket distortions.
4. **What is the simplest reliable approach to detecting transitions?** Direct comparison between consecutive confirmed observations: $S_{new}.\text{eb\_dg\_status} \ne S_{prev}.\text{eb\_dg\_status}$.
5. **How should observation gaps and uncertain transition times be represented?** Maximum inference gap threshold ($2.5\times$ cadence) with explicit `UNKNOWN` gap intervals and bounded uncertainty intervals.
6. **Should event and interval processing happen in the same transaction as snapshot persistence?** **No. Use the 2-step checkpoint model** so raw telemetry persistence is decoupled from derived processing failures.
7. **How should processing recover after a crash or restart?** Query the single `OPEN` interval; extend if $\Delta t \le \text{max\_gap}$, otherwise close and insert an `UNKNOWN` gap interval.
8. **What is the safest way to rebuild historical events and intervals?** Chronological streaming of raw snapshots (`id ASC`) through `StateProcessor` under a new `calculation_version`.
9. **What should the first implementation PR contain?** PR 1: Events schema (`004_events.sql`), `StateProcessor` event emission, deduplication, backfill, and test suite.
10. **What should explicitly be excluded from that PR?** `supply_intervals` (PR 2), energy consumption analytics, frontend UI changes, and upstream client modifications.
