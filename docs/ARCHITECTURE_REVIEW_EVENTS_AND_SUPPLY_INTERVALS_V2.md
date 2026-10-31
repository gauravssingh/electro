# Electro — Architecture Review (Revision 2): Event Persistence, Supply Intervals, Uncertainty Modelling & Implementation Readiness

**Document Type:** Technical Architecture Review & Implementation Specification (Revision 2)  
**Target Repository:** `gauravssingh/electro`  
**Working Branch & Commit:** `main` (`935d51057879d330f436ea3b1f3abc6c18d76267`)  
**Status:** Architecture Specification — Approved Baseline for PR 1  

---

## 1. Executive Summary & Architectural Verdict

Electro is a private, single-user, self-hosted macOS monitoring system for an ELNet prepaid electricity meter. The service runs as a persistent LaunchAgent daemon executing a Python/Flask application with SQLite in Write-Ahead Logging (WAL) mode, paired with a vanilla JS/Tailwind web dashboard and a native Rust CLI (`electro`).

### Architectural Verdict
The original architecture review (V1) correctly identified the core design flaw in Electro: **neither events nor supply intervals exist as persistent entities in the database**. Instead, `/api/events` scans up to 10,000 raw rows in Python memory on every query, while `/api/history` reconstructs durations ephemerally using an arbitrary $dt/2$ split heuristic.

However, the V1 proposal contained critical flaws and internal contradictions that would have compromised data integrity if implemented:
1. **False Precision in Event Occurrence**: V1 proposed calculating the event occurrence timestamp as the arithmetic midpoint between two observations. Midpoint is an unobserved heuristic. Storing it as `occurred_at_utc` presents an estimate as a physical measurement.
2. **Schema Contradiction in Supply Intervals**: V1 specified `ended_at_utc TEXT NOT NULL` while simultaneously specifying that the active interval has `status = 'OPEN'`. An open interval has not ended; enforcing `NOT NULL` is contradictory.
3. **Overlapping Uncertainty Fields**: V1 proposed `status = 'UNCERTAIN'` alongside `source = 'UNKNOWN'`, conflating interval lifecycle with source attribution.
4. **Vague Duration Definitions**: V1 included `coverage_seconds` and `uncertainty_seconds` without exact mathematical formulas or accounting for gaps.
5. **Coupled Notifications**: V1 tied desktop notifications directly into event processing, risking duplicate alerts on restart and failing transactions if `osascript` hung.

### Core Architectural Decisions in V2
1. **Three-Layer Decoupled Model**:
   - **Layer 1 (Raw Telemetry)**: `snapshots` and `poll_events` remain the immutable, read-only foundation.
   - **Layer 2 (Events)**: Persistent, discrete state changes with bounded uncertainty windows (`earliest_possible_at_utc`, `latest_possible_at_utc`). `occurred_at_utc` is strictly `NULL` for polled events.
   - **Layer 3 (Supply Intervals)**: Contiguous, persistent state periods (`GRID`, `DG`, `UNKNOWN`). Unmonitored gaps exceeding the threshold are materialized as explicit `UNKNOWN` intervals.
2. **Deterministic State Machine**: Strict 2-phase pipeline where raw telemetry is committed in Transaction 1, and derived events/intervals are processed and checkpointed atomically in Transaction 2.
3. **Atomic Rebuild Capability**: Historical data can be safely rebuilt via staging tables and atomic table swaps without service interruption or raw telemetry corruption.
4. **Phased Delivery**: PR 1 delivers the persistent Events layer and checkpointing; PR 2 delivers Supply Intervals; PR 3 migrates analytics.

---

## 2. Current Repository Findings & Differences from V1

A complete inspection of the working repository at commit `935d510` confirms the following facts:

### 1. Git & Database Status
- **Branch**: `main` (clean working tree, HEAD at `935d51057879d330f436ea3b1f3abc6c18d76267`).
- **Database**: Local `electro.db` in WAL mode containing:
  - `snapshots`: 1,206 rows spanning `2026-10-10T17:07:22Z` to `2026-10-11T13:00:06Z`. All 1,206 snapshots currently report `eb_dg_status = 0` (Grid).
  - `poll_events`: 1,154 rows (1,149 successful, 5 upstream network failures).
- **Existing Migrations**:
  - `001_initial_schema.sql`: Initial `snapshots` table.
  - `002_poll_events.sql`: Polling health and latency tracking.
  - `003_snapshot_timestamps.sql`: Lifecycle timestamps (`attempted_at_utc`, `received_at_utc`, `persisted_at_utc`).

### 2. Discovered Bug in Existing Test Suite
Running the existing pytest suite revealed an important finding:
- **Test Failures**: 2 tests out of 69 failed:
  - `tests/test_routes.py::test_api_history_valid_and_invalid`
  - `tests/test_routes.py::test_api_export_csv_success`
- **Root Cause**: These unit tests hardcoded `observed_at_utc = "2026-10-10T10:00:00Z"` and queried with `range=24h`. At test execution time on the evening of October 11 (`2026-10-11T18:33:00Z`), more than 32 hours had elapsed since the hardcoded timestamp. Because `SnapshotRepository.get_history()` filters dynamically by `datetime.now(timezone.utc) - timedelta(hours=24)`, the query returned 0 rows.
- **Architectural Lesson**: Hardcoding static timestamps in tests that query relative time ranges (`24h`) causes time-rot test failures. All test fixtures must generate dynamic timestamps relative to the current mock execution time.

### 3. Summary of Differences Between V1 and V2
| Dimension | V1 Proposal | V2 Revised Specification | Rationale |
|---|---|---|---|
| **Event Timestamp** | `occurred_at_utc` set to midpoint ($t_1 + t_2)/2$ | `occurred_at_utc` is `NULL`. Events store `earliest_possible_at_utc` and `latest_possible_at_utc`. | Midpoint is an unobserved heuristic. Storing it as occurrence is false precision. |
| **Supply Interval Status** | `status IN ('OPEN', 'CLOSED', 'UNCERTAIN')` | `status IN ('OPEN', 'CLOSED')`. Attribution uncertainty is `source = 'UNKNOWN'`. | Eliminates conflation between interval lifecycle and source attribution. |
| **Interval End Timestamp** | `ended_at_utc TEXT NOT NULL` | `ended_at_utc TEXT NULL` (enforced NULL when `OPEN`, NOT NULL when `CLOSED`). | Resolves the internal contradiction of open intervals. |
| **Open Interval Constraint** | None | SQLite partial unique index: `CREATE UNIQUE INDEX idx_supply_intervals_single_open ON supply_intervals(meter_id) WHERE status = 'OPEN'`. | Enforces at most one active interval per meter at the database engine level. |
| **Gap Representation** | Vague gap seconds | Model A: Explicit contiguous `UNKNOWN` intervals created for gaps $> \tau_{\text{gap}}$. | Guarantees continuous time accounting without gaps or timeline holes. |
| **Notifications** | Invoked inside event processor | Decoupled consumer tracking `last_dispatched_event_id` in SQLite. | Notification failure cannot block or abort database transactions. |
| **Rebuild Strategy** | Incremental re-execution | Atomic staging table swap (`events_rebuild` $\to$ `events`). | Guarantees zero downtime and complete rollback if a rebuild fails midway. |

---

## 3. Existing Data Model Assessment

### Layer 1: Raw Telemetry (Source of Truth)
The existing raw telemetry model consists of two tables:

#### 1. `snapshots`
Stores successful meter telemetry responses normalized from ELNet `HomeData`:
- **Primary Key**: `id INTEGER PRIMARY KEY AUTOINCREMENT`.
- **Temporal Anchor**: `observed_at_utc TEXT NOT NULL` (ISO 8601 UTC string).
- **Lifecycle Timestamps**: `attempted_at_utc`, `received_at_utc`, `persisted_at_utc`.
- **Meter Identifiers**: `meter_id INTEGER NOT NULL`, `device_id TEXT`, `meter_sn TEXT`.
- **Power & Relay State**: `eb_dg_status INTEGER` (0 = Grid, 1 = DG, NULL = Invalid), `relay_status TEXT` ('ON', 'OFF', 'DISCONNECTED').
- **Billing & Energy**: `meter_balance REAL`, `kwh_rec_today REAL`, `kwh_rec_month REAL`, `current_day_eb REAL`, `current_day_dg REAL`.

#### 2. `poll_events`
Stores every upstream polling attempt (successes, latency, and categorized failures):
- **Primary Key**: `id INTEGER PRIMARY KEY AUTOINCREMENT`.
- **Timestamp**: `attempted_at_utc TEXT NOT NULL`.
- **Health Indicators**: `success INTEGER NOT NULL`, `duration_ms INTEGER`.
- **Error Tracking**: `error_category TEXT`, `error_message TEXT`.

**Assessment**: The existing raw telemetry layer is rock-solid and completely sufficient. It must remain strictly read-only for all derived processing.

---

## 4. Final Events Schema (Complete SQLite DDL)

The `events` table records discrete state transitions and operational milestones. Every column has a clear, justified purpose.

```sql
-- Migration: storage/migrations/004_events.sql

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    
    -- Event Classification
    event_type TEXT NOT NULL,
    category TEXT NOT NULL CHECK (category IN ('Power', 'Billing', 'Network', 'Relay')),
    severity TEXT NOT NULL CHECK (severity IN ('info', 'warning', 'alert')),
    
    -- Temporal Semantics (Bounded Uncertainty)
    earliest_possible_at_utc TEXT NOT NULL,
    latest_possible_at_utc TEXT NOT NULL,
    occurred_at_utc TEXT, -- NULL for polled events; populated only if hardware clock certifies exact time
    detected_at_utc TEXT NOT NULL,
    
    -- Meter & Telemetry Lineage
    meter_id INTEGER,
    source_snapshot_id INTEGER REFERENCES snapshots(id) ON DELETE SET NULL,
    previous_snapshot_id INTEGER REFERENCES snapshots(id) ON DELETE SET NULL,
    poll_event_id INTEGER REFERENCES poll_events(id) ON DELETE SET NULL,
    
    -- Value Movement
    previous_value TEXT,
    current_value TEXT,
    
    -- Presentation (Preserved for zero-cost API reads and CLI output)
    title TEXT NOT NULL,
    details TEXT NOT NULL,
    metadata_json TEXT,
    
    -- Idempotency and Audit
    dedupe_key TEXT NOT NULL UNIQUE,
    created_at_utc TEXT NOT NULL
);

-- Indexes for performance
CREATE INDEX IF NOT EXISTS idx_events_latest_possible ON events(latest_possible_at_utc DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_events_category ON events(category, latest_possible_at_utc DESC);
CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type, latest_possible_at_utc DESC);
CREATE INDEX IF NOT EXISTS idx_events_meter ON events(meter_id, latest_possible_at_utc DESC);
CREATE INDEX IF NOT EXISTS idx_events_source_snapshot ON events(source_snapshot_id);
```

### Column Justification
- `event_type`: Machine-readable discriminator (e.g. `source_transition_grid_to_dg`).
- `category`: Matches existing frontend tabs and filter buttons (`Power`, `Billing`, `Network`, `Relay`).
- `severity`: Matches existing UI styling and alert badges (`info`, `warning`, `alert`).
- `earliest_possible_at_utc` & `latest_possible_at_utc`: Strict observation boundaries bounding when the state change occurred.
- `occurred_at_utc`: Explicitly nullable. Remains `NULL` when transition occurred during an unmonitored polling interval.
- `source_snapshot_id` & `previous_snapshot_id`: Direct foreign keys to raw evidence rows.
- `title` & `details`: Materialized human-readable descriptions. Eliminates Python template rendering overhead on `/api/events` reads.
- `dedupe_key`: Unique string hash preventing duplicate insertions during retries or replays.

---

## 5. Event Taxonomy & Exact Generation Rules

| Event Type | Category | Severity | Trigger Condition | Dedupe Key Format |
|---|---|---|---|---|
| `source_transition_grid_to_dg` | `Power` | `alert` | `prev.eb_dg_status == 0` and `curr.eb_dg_status == 1` | `src_change:{meter_id}:{prev_id}:{curr_id}` |
| `source_transition_dg_to_grid` | `Power` | `info` | `prev.eb_dg_status == 1` and `curr.eb_dg_status == 0` | `src_change:{meter_id}:{prev_id}:{curr_id}` |
| `source_became_unknown` | `Power` | `warning` | `prev.eb_dg_status IN (0,1)` and `curr.eb_dg_status NOT IN (0,1)` | `src_change:{meter_id}:{prev_id}:{curr_id}` |
| `relay_opened` | `Relay` | `alert` | `prev.relay_status == 'ON'` and `curr.relay_status == 'OFF'` | `relay_change:{meter_id}:{prev_id}:{curr_id}` |
| `relay_closed` | `Relay` | `info` | `prev.relay_status == 'OFF'` and `curr.relay_status == 'ON'` | `relay_change:{meter_id}:{prev_id}:{curr_id}` |
| `low_balance_warning` | `Billing` | `warning` | `prev.balance >= 500.0` and `curr.balance < 500.0` | `low_bal:{meter_id}:{prev_id}:{curr_id}` |
| `balance_recharge_detected` | `Billing` | `info` | `curr.balance - prev.balance >= 20.0` | `recharge:{meter_id}:{prev_id}:{curr_id}` |
| `telemetry_connection_dropped` | `Network` | `warning` | `prev_poll.success == 1` and `curr_poll.success == 0` | `poll_drop:{meter_id}:{curr_poll_id}` |
| `telemetry_connection_restored` | `Network` | `info` | `prev_poll.success == 0` and `curr_poll.success == 1` | `poll_recov:{meter_id}:{curr_poll_id}` |

---

## 6. Final Temporal Semantics

### Option A vs Option B Verdict: Option A with Bounded Windows
- **Decision**: Adopt **Option A**. Store exact observation bounds and leave `occurred_at_utc = NULL`.
- **Rationale**:
  - Setting `occurred_at_utc = (t_1 + t_2) / 2` creates false precision. If polling interval is 15s, a power outage reported at 10:00:15 could have occurred at 10:00:01 or 10:00:14. Assigning 10:00:07.5 claims a level of temporal knowledge the meter never provided.
  - Furthermore, if the system was asleep or disconnected for 30 minutes, setting `occurred_at_utc` to the midpoint (+15m) would attribute power outages to random times when nobody was monitoring the meter.
- **Fields Semantics**:
  1. `earliest_possible_at_utc`: The observation timestamp of the prior snapshot confirming the previous state.
  2. `latest_possible_at_utc`: The observation timestamp of the new snapshot confirming the changed state.
  3. `detected_at_utc`: Wall-clock UTC timestamp when the state processor detected the change.
  4. `occurred_at_utc`: Strictly `NULL` for polling-detected events. (Reserved for future smart meter logs that report internal hardware clock timestamps).

### Deterministic Event Ordering
When querying `/api/events`, records must be sorted deterministically:
```sql
ORDER BY latest_possible_at_utc DESC, id DESC
```
If multiple events share identical confirmation timestamps (e.g., a power switch accompanied by a relay disconnect), the tie is broken deterministically by `id DESC`.

---

## 7. Final Supply Intervals Schema (Complete SQLite DDL)

Supply intervals represent continuous state periods. The V2 schema resolves all previous contradictions regarding open intervals and uncertainty fields.

```sql
-- Migration: storage/migrations/005_supply_intervals.sql

CREATE TABLE IF NOT EXISTS supply_intervals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    meter_id INTEGER NOT NULL,
    
    -- State Attribution
    source TEXT NOT NULL CHECK (source IN ('GRID', 'DG', 'UNKNOWN')),
    status TEXT NOT NULL CHECK (status IN ('OPEN', 'CLOSED')),
    
    -- Temporal Boundaries (UTC ISO-8601)
    started_at_utc TEXT NOT NULL,
    ended_at_utc TEXT, -- Strictly NULL when status = 'OPEN', NOT NULL when status = 'CLOSED'
    
    -- Duration Metrics (in seconds)
    duration_seconds REAL,    -- Immutable for CLOSED; dynamically computed when querying OPEN
    supported_seconds REAL,   -- Sum of intervals with delta <= max_gap
    uncertain_seconds REAL,   -- Sum of intervals with delta > max_gap or UNKNOWN source
    
    -- Telemetry Lineage
    start_snapshot_id INTEGER NOT NULL REFERENCES snapshots(id) ON DELETE RESTRICT,
    end_snapshot_id INTEGER REFERENCES snapshots(id) ON DELETE RESTRICT, -- NULL when OPEN
    snapshot_count INTEGER NOT NULL DEFAULT 1,
    
    -- Calculation Audit & Traceability
    max_gap_seconds_applied REAL NOT NULL,
    calculation_version INTEGER NOT NULL DEFAULT 1,
    
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL,
    
    -- Table Constraints
    CONSTRAINT chk_interval_ended_status CHECK (
        (status = 'OPEN' AND ended_at_utc IS NULL AND end_snapshot_id IS NULL) OR
        (status = 'CLOSED' AND ended_at_utc IS NOT NULL AND end_snapshot_id IS NOT NULL AND ended_at_utc >= started_at_utc)
    )
);

-- Performance Indexes
CREATE INDEX IF NOT EXISTS idx_intervals_timeline ON supply_intervals(meter_id, started_at_utc ASC);
CREATE INDEX IF NOT EXISTS idx_intervals_range ON supply_intervals(meter_id, started_at_utc, ended_at_utc);
CREATE INDEX IF NOT EXISTS idx_intervals_source ON supply_intervals(source, started_at_utc);

-- Database-Level Guarantee: At most ONE OPEN interval per meter
CREATE UNIQUE INDEX IF NOT EXISTS idx_supply_intervals_single_open 
ON supply_intervals(meter_id) 
WHERE status = 'OPEN';
```

---

## 8. Exact Duration, Coverage & Uncertainty Definitions

### 1. Mathematical Definitions
- **Observed Boundary ($t_i$)**: A discrete observation timestamp reported in `snapshots.observed_at_utc`.
- **Observation Step ($\Delta t_i$)**: $\Delta t_i = t_{i+1} - t_i$.
- **Max Inference Gap ($\tau_{\text{gap}}$)**: Configured threshold (default $2.5 \times \text{poll\_interval}$, e.g. 37.5s, floor 45.0s).
- **Elapsed Duration**:
  $$\Delta T = \text{strftime}('\%s', t_{\text{end}}) - \text{strftime}('\%s', t_{\text{start}})$$
- **Supported Duration**: Elapsed time where consecutive readings satisfy $\Delta t_i \le \tau_{\text{gap}}$ under a confirmed source (`GRID` or `DG`).
- **Uncertain Duration**: Elapsed time where $\Delta t_i > \tau_{\text{gap}}$ or source is `UNKNOWN`.
- **Telemetry Coverage**:
  $$\text{Coverage \%} = \frac{\sum D_{\text{supported}}}{\Delta T_{\text{total\_span}}} \times 100\%$$

### 2. Walkthrough of the Two Explicit Examples

#### Example 1: Same Source with a Gap
```text
09:00:00 GRID
09:01:00 GRID
09:20:00 GRID
(Max inference gap = 5 minutes = 300s)
```
1. **At 09:00:00**: Snapshot 1 arrives. Open Interval 1 (`GRID`, `OPEN`) is created at `09:00:00`.
2. **At 09:01:00**: Snapshot 2 arrives ($\Delta t = 60\text{s} \le 300\text{s}$). Source matches. Interval 1 is extended. Supported seconds = 60.
3. **At 09:20:00**: Snapshot 3 arrives ($\Delta t = 19\text{m} = 1140\text{s} > 300\text{s}$). The gap exceeds $\tau_{\text{gap}}$.
   - **Step A**: Close Interval 1 at `09:01:00`.
     - `source = 'GRID'`, `status = 'CLOSED'`, `started_at = '09:00'`, `ended_at = '09:01'`, `duration_seconds = 60.0`, `supported_seconds = 60.0`, `uncertain_seconds = 0.0`.
   - **Step B**: Create and close an explicit Interval 2 (`UNKNOWN`):
     - `source = 'UNKNOWN'`, `status = 'CLOSED'`, `started_at = '09:01'`, `ended_at = '09:20'`, `duration_seconds = 1140.0`, `supported_seconds = 0.0`, `uncertain_seconds = 1140.0`.
   - **Step C**: Open Interval 3 (`GRID`, `OPEN`):
     - `source = 'GRID'`, `status = 'OPEN'`, `started_at = '09:20'`, `ended_at = NULL`.
   - **Verdict**: Grid runtime is credited with **60 seconds**, NOT 20 minutes. The 19-minute gap is explicitly accounted for as `UNKNOWN`.

#### Example 2: Source Change Across a Gap
```text
09:00:00 GRID
09:01:00 GRID
09:20:00 DG
(Max inference gap = 5 minutes = 300s)
```
1. **09:00 to 09:01**: Same as Example 1.
2. **At 09:20:00**: Snapshot 3 arrives with `source = DG` across a 19-minute gap.
   - **Step A**: Close Interval 1 (`GRID`) at `09:01:00` (duration 60s).
   - **Step B**: Create and close Interval 2 (`UNKNOWN`) from `09:01:00` to `09:20:00` (duration 1140s).
   - **Step C**: Open Interval 3 (`DG`, `OPEN`) at `09:20:00`.
   - **Step D**: Emit event `source_transition_grid_to_dg`:
     - `earliest_possible_at_utc = '09:01:00'`
     - `latest_possible_at_utc = '09:20:00'`
     - `occurred_at_utc = NULL`
     - `metadata_json = {"detection_window_seconds": 1140, "is_gap": true}`
   - **Verdict**: Neither Grid nor DG receives credit for the 19 unmonitored minutes. The uncertainty window is explicitly recorded on both the event and the interval timeline.

---

## 9. Unknown-Period Representation Decision

### Model A (Explicit UNKNOWN Intervals) vs Model B (Gaps Only)
- **Decision**: **Model A (Explicit UNKNOWN Intervals)** is chosen.
- **Why Model A is superior for Electro**:
  1. **Continuous Time Invariant**: Every second of the monitored timeline is accounted for by exactly one interval row.
  2. **Trivial Range Queries**: To calculate period summaries for any window $[T_{\text{start}}, T_{\text{end}}]$, SQL simply slices overlapping intervals. There is no need for recursive CTEs or complicated gap-detection logic in client code.
  3. **Zero Ambiguity in Totals**:
     $$\text{Total Time} = \text{Grid Time} + \text{DG Time} + \text{Unknown Time}$$
  4. **Elimination of `status = 'UNCERTAIN'`**: In Model A, `status` only tracks lifecycle (`OPEN` vs `CLOSED`). Attribution uncertainty is represented cleanly by `source = 'UNKNOWN'`.

---

## 10. Maximum Inference Gap Policy

1. **Configurable Parameter**: Defined in `config.py` as `ELECTRO_MAX_INFERENCE_GAP_SECONDS`.
2. **Formula & Default**:
   $$\tau_{\text{gap}} = \max(2.5 \times \text{poll\_interval\_seconds}, 45.0)$$
   For Electro's default 15s polling cadence, $\tau_{\text{gap}} = 45.0\text{s}$.
3. **Justification**:
   - Upstream ELNet queries may experience occasional transient HTTP retries or network lag taking 20–30s. Setting $\tau_{\text{gap}} = 45.0\text{s}$ accommodates normal network variance without falsely fragmenting intervals.
   - Missing 3 consecutive polls (45s) reliably indicates an outage, system sleep, or service restart, requiring gap classification.
4. **Scope**: Global single-user configuration, stamped onto each derived interval row in `max_gap_seconds_applied` and `calculation_version`.

---

## 11. Complete State-Transition Matrix

The state machine processes the next chronological snapshot $S_{\text{curr}}$ against the currently active open interval $I_{\text{open}}$ (started by prior snapshot $S_{\text{prev}}$).

| Case | $I_{\text{open}}\text{.source}$ | $S_{\text{curr}}\text{.source}$ | $\Delta t \le \tau_{\text{gap}}$ | Action on $I_{\text{open}}$ | New Interval Created? | Events Emitted |
|---|---|---|---|---|---|---|
| **1** | `GRID` | `GRID` | Yes | Extend $I_{\text{open}}$ (`snapshot_count++`, `updated_at = now`) | None | None |
| **2** | `GRID` | `GRID` | No (Gap) | Close $I_{\text{open}}$ at $S_{\text{prev}}$ | 1. Insert closed `UNKNOWN` ($S_{\text{prev}} \to S_{\text{curr}}$)<br>2. Open new `GRID` ($S_{\text{curr}}$) | None |
| **3** | `GRID` | `DG` | Yes | Close $I_{\text{open}}$ at $S_{\text{prev}}$ | Open new `DG` at $S_{\text{curr}}$ | `source_transition_grid_to_dg` |
| **4** | `GRID` | `DG` | No (Gap) | Close $I_{\text{open}}$ at $S_{\text{prev}}$ | 1. Insert closed `UNKNOWN` ($S_{\text{prev}} \to S_{\text{curr}}$)<br>2. Open new `DG` ($S_{\text{curr}}$) | `source_transition_grid_to_dg` (`is_gap = true`) |
| **5** | `DG` | `GRID` | Yes | Close $I_{\text{open}}$ at $S_{\text{prev}}$ | Open new `GRID` at $S_{\text{curr}}$ | `source_transition_dg_to_grid` |
| **6** | `DG` | `GRID` | No (Gap) | Close $I_{\text{open}}$ at $S_{\text{prev}}$ | 1. Insert closed `UNKNOWN` ($S_{\text{prev}} \to S_{\text{curr}}$)<br>2. Open new `GRID` ($S_{\text{curr}}$) | `source_transition_dg_to_grid` (`is_gap = true`) |
| **7** | `DG` | `DG` | Yes | Extend $I_{\text{open}}$ (`snapshot_count++`, `updated_at = now`) | None | None |
| **8** | `DG` | `DG` | No (Gap) | Close $I_{\text{open}}$ at $S_{\text{prev}}$ | 1. Insert closed `UNKNOWN` ($S_{\text{prev}} \to S_{\text{curr}}$)<br>2. Open new `DG` ($S_{\text{curr}}$) | None |
| **9** | `GRID` | `UNKNOWN` | Yes/No | Close $I_{\text{open}}$ at $S_{\text{prev}}$ | Open new `UNKNOWN` at $S_{\text{curr}}$ | `source_became_unknown` |
| **10** | `DG` | `UNKNOWN` | Yes/No | Close $I_{\text{open}}$ at $S_{\text{prev}}$ | Open new `UNKNOWN` at $S_{\text{curr}}$ | `source_became_unknown` |
| **11** | `UNKNOWN` | `GRID` | Yes/No | Close $I_{\text{open}}$ at $S_{\text{prev}}$ | Open new `GRID` at $S_{\text{curr}}$ | None (or source restored) |
| **12** | `UNKNOWN` | `DG` | Yes/No | Close $I_{\text{open}}$ at $S_{\text{prev}}$ | Open new `DG` at $S_{\text{curr}}$ | None |
| **13** | `UNKNOWN` | `UNKNOWN` | Yes | Extend $I_{\text{open}}$ | None | None |
| **14** | `UNKNOWN` | `UNKNOWN` | No (Gap) | Close $I_{\text{open}}$ at $S_{\text{prev}}$ | 1. Insert closed `UNKNOWN`<br>2. Open new `UNKNOWN` | None |
| **15** | *None* (Startup) | Any | N/A | None | Open initial interval matching $S_{\text{curr}}$ | None |

### Special Edge Cases
- **Duplicate Timestamps** ($t_{\text{curr}} = t_{\text{prev}}$): If duplicate timestamps occur, ignore duration increment, update `snapshot_count++`.
- **Out of Order Timestamps** ($t_{\text{curr}} < t_{\text{prev}}$): Anomaly logged; skipped in incremental streaming processor; handled deterministically during chronological rebuild (`ORDER BY observed_at_utc ASC, id ASC`).
- **Daemon Restart**: On restart, processor queries `WHERE status = 'OPEN'`. If the time elapsed between restart and now exceeds $\tau_{\text{gap}}$, the open interval is closed at its last snapshot, an `UNKNOWN` interval is inserted across the downtime, and a new interval begins.

---

## 12. Event and Interval Processing Pseudocode

```python
class StateProcessor:
    def __init__(self, db_path: str, poll_interval_seconds: int = 15):
        self.db_path = db_path
        self.max_gap = max(poll_interval_seconds * 2.5, 45.0)

    def process_pending_snapshots(self, batch_size: int = 100) -> int:
        """Process unprocessed snapshots in a single ACID transaction."""
        with get_db_connection(self.db_path) as conn:
            # 1. Fetch current checkpoint
            row = conn.execute(
                "SELECT last_processed_snapshot_id FROM processor_checkpoints WHERE processor_name = 'primary';"
            ).fetchone()
            last_id = row["last_processed_snapshot_id"] if row else 0

            # 2. Fetch pending chronological snapshots
            pending = conn.execute(
                """
                SELECT * FROM snapshots 
                WHERE id > ? 
                ORDER BY observed_at_utc ASC, id ASC 
                LIMIT ?;
                """,
                (last_id, batch_size),
            ).fetchall()

            if not pending:
                return 0

            # 3. Fetch active open interval
            open_interval = conn.execute(
                "SELECT * FROM supply_intervals WHERE meter_id = ? AND status = 'OPEN';",
                (pending[0]["meter_id"],),
            ).fetchone()

            # 4. Fetch previous snapshot for transition evaluation
            prev_snap = conn.execute(
                "SELECT * FROM snapshots WHERE id = ?;",
                (last_id,),
            ).fetchone()

            for curr_snap in pending:
                # Evaluate and persist discrete events
                self._evaluate_events(conn, prev_snap, curr_snap)

                # Evaluate and update supply intervals
                open_interval = self._evaluate_interval(conn, open_interval, prev_snap, curr_snap)

                prev_snap = curr_snap
                last_id = curr_snap["id"]

            # 5. Advance checkpoint atomically with all changes
            conn.execute(
                """
                INSERT INTO processor_checkpoints (processor_name, last_processed_snapshot_id, updated_at_utc)
                VALUES ('primary', ?, datetime('now'))
                ON CONFLICT(processor_name) DO UPDATE SET
                    last_processed_snapshot_id = excluded.last_processed_snapshot_id,
                    updated_at_utc = excluded.updated_at_utc;
                """,
                (last_id,),
            )
            # Transaction commits here automatically
            return len(pending)
```

---

## 13. Transaction and Checkpoint Recovery Design

```
┌────────────────────────────────────────────────────────────────────────┐
│                   Step 1: Raw Persistence (Transaction 1)              │
│   Client Fetch ──► INSERT INTO snapshots / poll_events ──► COMMIT     │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │ Uncoupled Trigger / Polling Tick
┌───────────────────────────────────▼────────────────────────────────────┐
│               Step 2: Derived State Pipeline (Transaction 2)           │
│   BEGIN TRANSACTION                                                    │
│     1. Read Checkpoint (last_processed_snapshot_id)                    │
│     2. Fetch Unprocessed Snapshots (ORDER BY observed_at_utc ASC)      │
│     3. State Machine:                                                  │
│        - INSERT INTO events (ON CONFLICT DO NOTHING via dedupe_key)    │
│        - UPDATE / INSERT supply_intervals                              │
│     4. UPDATE processor_checkpoints (last_processed_snapshot_id)       │
│   COMMIT                                                               │
└────────────────────────────────────────────────────────────────────────┘
```

### Resolution of All 5 Failure Scenarios
1. **Scenario A (Crash after raw snapshot commit)**:
   - Snapshot 100 committed. Checkpoint is at 99.
   - On restart, processor discovers `WHERE id > 99`, processing snapshot 100 normally.
2. **Scenario B (Crash after event insert but before checkpoint commit)**:
   - Both event insertion and checkpoint advance occur within **Transaction 2**.
   - SQLite WAL crash recovery rolls back the uncommitted transaction completely.
   - On retry, `dedupe_key` guarantees that even if a partial state persisted, duplicate events cannot be inserted.
3. **Scenario C (Crash during interval extension)**:
   - Uncommitted duration updates roll back automatically with Transaction 2. On restart, the interval is extended cleanly once.
4. **Scenario D (Crash during source transition)**:
   - Closing the old interval, inserting the new interval, inserting the transition event, and updating the checkpoint occur in the **same transaction**. ACID prevents partial application.
5. **Scenario E (Duplicate processing)**:
   - The unique constraint on `events.dedupe_key` rejects duplicate events. The interval state machine verifies `curr_snap['id'] > open_interval['end_snapshot_id']` before mutating intervals.

---

## 14. Notification Separation

### Architecture
1. **Producer-Consumer Decoupling**:
   - `StateProcessor` only writes to SQLite. It has zero knowledge of macOS `osascript` or desktop alerts.
   - `NotificationService` queries newly committed events from the database:
     ```sql
     SELECT * FROM events WHERE id > ? AND category IN ('Power', 'Billing') ORDER BY id ASC;
     ```
2. **Persistent Dispatch Latch**:
   - Stores `last_dispatched_event_id` in a dedicated table `notification_dispatch_log`.
   - Eliminates in-memory latches (`_last_source`, `_low_balance_fired`).
   - Even if the LaunchAgent service restarts 50 times, no duplicate notifications are ever fired.
3. **Resilience**:
   - If desktop notification delivery fails (e.g. system sleep, script timeout), the database transaction is unaffected.

---

## 15. Historical Backfill and Rebuild Design

To support reprocessing when calculation policies change, rebuilds must be atomic and non-destructive.

### Atomic Staging Table Rebuild Algorithm
```bash
# Executed via CLI or maintenance script:
electro derived rebuild --all
```
1. **Create Staging Tables**:
   - `CREATE TABLE events_rebuild AS SELECT * FROM events WHERE 0;`
   - `CREATE TABLE supply_intervals_rebuild AS SELECT * FROM supply_intervals WHERE 0;`
2. **Deterministic Historical Replay**:
   - Stream raw snapshots:
     ```sql
     SELECT * FROM snapshots ORDER BY observed_at_utc ASC, id ASC;
     ```
   - Stream raw `poll_events` to rebuild connection drop/recovery events.
   - Run the state machine sequentially into `events_rebuild` and `supply_intervals_rebuild`.
3. **Atomic Swap Transaction**:
   ```sql
   BEGIN TRANSACTION;
   ALTER TABLE events RENAME TO events_backup;
   ALTER TABLE events_rebuild RENAME TO events;
   DROP TABLE events_backup;

   ALTER TABLE supply_intervals RENAME TO supply_intervals_backup;
   ALTER TABLE supply_intervals_rebuild RENAME TO supply_intervals;
   DROP TABLE supply_intervals_backup;

   UPDATE processor_checkpoints 
   SET last_processed_snapshot_id = (SELECT MAX(id) FROM snapshots)
   WHERE processor_name = 'primary';
   COMMIT;
   ```
4. **Safety Guarantee**: Live queries continue without interruption. If the rebuild encounters an error, the staging tables are dropped and production data remains untouched.

---

## 16. API and CLI Compatibility

The existing consumers (`dashboard.js`, `main.rs`, `/api/events`, `/api/history`) must not break.

### 1. `/api/events` Compatibility (PR 1)
Existing contract:
```json
{
  "count": 2,
  "limit": 50,
  "events": [
    {
      "id": "ev-104",
      "timestamp": "2026-10-11T12:00:00Z",
      "type": "source_change",
      "category": "Power",
      "title": "Switched to Backup Generator (DG)",
      "details": "Outage detected on main grid; DG backup power active.",
      "severity": "alert"
    }
  ]
}
```
- In PR 1, `SnapshotRepository.get_unified_events()` is refactored to read from the persistent `events` table:
  - `timestamp` is mapped directly from `latest_possible_at_utc`.
  - `type` is mapped from `event_type`.
  - `category`, `title`, `details`, `severity` match existing string formats 1:1.
- **Zero frontend or CLI changes required.**

### 2. `/api/history` Compatibility (PR 3)
- Retains all existing response fields (`range`, `points`, `transitions`, `analytics`).
- `analytics.grid_seconds` and `analytics.dg_seconds` are populated from `supply_intervals` instead of pairwise Python loop slices.

---

## 17. Detailed Test Matrix with Expected Outcomes

| Category | Test Name | Scenario / Setup | Expected Database Result | Observable Behavior |
|---|---|---|---|---|
| **Event** | `test_grid_to_dg_event` | Snapshot 1: Grid; Snapshot 2: DG ($\Delta t = 15\text{s}$) | 1 row in `events` (`event_type = 'source_transition_grid_to_dg'`, `category = 'Power'`, `severity = 'alert'`) | API `/api/events` returns outage alert |
| **Event** | `test_dg_to_grid_event` | Snapshot 1: DG; Snapshot 2: Grid ($\Delta t = 15\text{s}$) | 1 row in `events` (`event_type = 'source_transition_dg_to_grid'`, `severity = 'info'`) | API `/api/events` returns power restored |
| **Event** | `test_low_balance_event` | Balance moves from ₹520 to ₹480 | 1 row in `events` (`event_type = 'low_balance_warning'`, `severity = 'warning'`) | Low balance event visible in UI |
| **Event** | `test_connection_recovery_event` | Poll event 1: Failure; Poll event 2: Success | 1 row in `events` (`event_type = 'telemetry_connection_restored'`) | Network recovery logged |
| **Idempotency** | `test_duplicate_snapshot_replay` | Process snapshot 100 twice through state processor | Checkpoint advances; 0 duplicate rows in `events` | Querying `/api/events` yields exactly 1 event |
| **Idempotency** | `test_crash_before_checkpoint` | Simulate exception right before checkpoint update | Transaction rolls back; 0 partial events committed | Processor retries cleanly on next tick |
| **Interval** | `test_continuous_grid_interval` | 5 snapshots with Grid ($\Delta t = 15\text{s}$) | 1 `supply_intervals` row (`source = 'GRID'`, `status = 'OPEN'`, `snapshot_count = 5`) | Runtime = 60s, 100% Grid |
| **Interval** | `test_interval_gap_creates_unknown` | Snapshot 1: Grid at 09:00; Snapshot 2: Grid at 09:20 | 1 closed Grid (60s), 1 closed Unknown (19m), 1 open Grid | Unknown duration accounted for; Grid runtime is 60s |
| **Interval** | `test_single_open_constraint` | Attempt inserting two `status = 'OPEN'` intervals | SQLite throws `IntegrityError` (`idx_supply_intervals_single_open`) | Database rejects corrupt state |
| **Rebuild** | `test_clean_historical_rebuild` | Run rebuild across 1,206 snapshots in staging table | Rebuilt counts match expected; live table swapped cleanly | Zero data loss; zero downtime |

---

## 18. SQLite Performance Considerations

1. **WAL Mode**: Fully preserved. Concurrent reads from the web dashboard and CLI never block collector writes.
2. **Busy Timeout**: Maintained at 5,000ms.
3. **Transaction Batching**: State processor processes in chunks of 100 snapshots, keeping transaction lock duration under 5ms.
4. **Index Sizing**: `idx_events_latest_possible` guarantees $O(\log N)$ query speed for `/api/events`, completely eliminating the current full-table scan.
5. **No Rolled-Up Aggregates Needed**: With supply intervals, querying 30 days of data requires inspecting $\sim 50\text{–}100$ interval rows instead of 172,800 raw snapshots. Additional minute/hour rollup tables are completely unnecessary.

---

## 19. Revised Implementation Roadmap

```
┌────────────────────────────────────────────────────────┐
│  Phase 0: Architecture Review Revision (Current Task)  │
│  - Deliver and approve V2 specification                │
└───────────────────────────┬────────────────────────────┘
                            │
┌───────────────────────────▼────────────────────────────┐
│  PR 1: Persistent Events Layer Foundation              │
│  - Migration 004_events.sql & checkpoint table         │
│  - StateProcessor (Events only) & backfill script      │
│  - Refactor /api/events to read indexed events         │
└───────────────────────────┬────────────────────────────┘
                            │
┌───────────────────────────▼────────────────────────────┐
│  PR 2: Persistent Supply Intervals Layer Foundation    │
│  - Migration 005_supply_intervals.sql                  │
│  - StateProcessor interval state machine & gaps        │
│  - Atomic rebuild CLI & interval tests                 │
└───────────────────────────┬────────────────────────────┘
                            │
┌───────────────────────────▼────────────────────────────┐
│  PR 3: Analytics Cutover & Decoupled Notifications     │
│  - /api/history reads from supply_intervals            │
│  - NotificationService reads from events table         │
└────────────────────────────────────────────────────────┘
```

---

## 20. PR 1 Scope and Acceptance Criteria

### PR 1 In-Scope Deliverables
1. **Migration `004_events.sql`**:
   - Table `events` and table `processor_checkpoints`.
2. **Event Processing (`services/state_processor.py`)**:
   - Deterministic event emission with `dedupe_key`.
   - Atomic transaction management and checkpointing.
3. **Deterministic Backfill Script (`scripts/backfill_events.py`)**:
   - Replays existing 1,206 snapshots and 1,154 poll events.
4. **Repository Refactor (`storage/repository.py`)**:
   - `get_unified_events()` queries `events` table with index.
5. **Unit Tests (`tests/test_events.py`)**:
   - Full coverage for event taxonomy, idempotency, and recovery.

### PR 1 Acceptance Criteria
1. Raw telemetry collection (`snapshots`, `poll_events`) continues completely unchanged.
2. Events are persisted to SQLite with correct lineage (`source_snapshot_id`, `poll_event_id`).
3. Re-running the processor or replaying historical data produces zero duplicate events.
4. `/api/events` response format matches the existing contract 1:1.
5. Dashboard "Events" tab and CLI `electro events` display historical events seamlessly.
6. All existing and new tests pass.

---

## 21. Remaining Product-Owner Decisions

1. **Historical Event Backfill Horizon**:
   - *Recommendation*: Backfill all 1,206 existing snapshots in `electro.db` (takes $< 0.2\text{s}$).
2. **Low Balance Alert Threshold**:
   - *Recommendation*: Default remains ₹500.00, configurable via `ELECTRO_LOW_BALANCE_THRESHOLD`.
3. **Retention Policy**:
   - *Recommendation*: Keep events indefinitely. At ~10–20 transitions per day, a full year of events occupies $< 5\text{MB}$ in SQLite.

---

## 22. Explicit Answers to the 18 Mandatory Questions

1. **Is the existing `snapshots` table sufficient as the raw source of truth?**  
   **Yes.** It preserves raw observations, power source (`eb_dg_status`), relay state, balances, and lifecycle timestamps. It must remain strictly read-only.
2. **Should events and supply intervals remain separate tables?**  
   **Yes.** Events represent discrete point-in-time state changes; supply intervals represent continuous durations. Conflating them destroys clean query mechanics.
3. **What is the final event timestamp model?**  
   `earliest_possible_at_utc` and `latest_possible_at_utc` store the observation boundary. `occurred_at_utc` is strictly `NULL`. Events are ordered by `latest_possible_at_utc DESC, id DESC`.
4. **Should midpoint timestamps be avoided or stored as estimates?**  
   **Avoided.** Midpoints introduce false precision and distort analytics over large gaps.
5. **What exactly constitutes supported runtime?**  
   Elapsed time between consecutive readings where the confirmed source did not change and $\Delta t \le \tau_{\text{gap}}$.
6. **How should unknown periods be represented?**  
   **Model A.** As explicit contiguous `supply_intervals` records with `source = 'UNKNOWN'` and `status = 'CLOSED'`.
7. **Should `UNCERTAIN` be an interval status, a source, or neither?**  
   **Neither.** Uncertainty of attribution is represented by `source = 'UNKNOWN'`. `status` is strictly limited to `'OPEN'` and `'CLOSED'`.
8. **What is the final maximum inference gap policy?**  
   $\tau_{\text{gap}} = \max(2.5 \times \text{poll\_interval\_seconds}, 45.0)$. (45.0s default).
9. **Should the threshold be versioned with derived calculations?**  
   **Yes.** Stored on each interval row as `max_gap_seconds_applied` and `calculation_version`.
10. **What should be stored versus calculated when querying intervals?**  
    For `CLOSED` intervals, `duration_seconds` is stored. For the single `OPEN` interval, elapsed duration is dynamically calculated relative to `now()`.
11. **What is the transaction boundary for derived processing?**  
    Transaction 1 commits raw telemetry. Transaction 2 processes derived records, updates intervals, and advances the checkpoint in a single atomic ACID commit.
12. **How does processing recover from every identified crash scenario?**  
    SQLite WAL ensures atomic commits. If a crash occurs before the checkpoint commits, the transaction rolls back, and retry is made idempotent via `dedupe_key`.
13. **How is the checkpoint reconciled against persisted records?**  
    On startup, the processor verifies that `last_processed_snapshot_id` matches the latest processed evidence, advancing sequentially from the checkpoint.
14. **How are notifications kept independent?**  
    `NotificationService` is an asynchronous consumer reading committed events from SQLite, tracking `last_dispatched_event_id` to prevent duplicates.
15. **What is the final event deduplication key?**  
    `{event_type_prefix}:{meter_id}:{prev_evidence_id}:{curr_evidence_id}`.
16. **How are historical rebuilds made safe and deterministic?**  
    Via staging tables (`events_rebuild`, `supply_intervals_rebuild`) and an atomic table swap transaction.
17. **What are the exact PR 1 boundaries?**  
    Schema `004_events.sql`, `StateProcessor` (events only), backfill script, refactored `/api/events`, and event test suite.
18. **What must be excluded from PR 1?**  
    Supply intervals, interval migrations, analytics modifications, frontend redesigns, and consumption analytics.
