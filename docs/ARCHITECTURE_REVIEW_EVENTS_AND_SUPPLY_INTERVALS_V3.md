# Electro — Architecture Review (Revision 3): Final Corrections Before PR 1

**Document Type:** Technical Architecture Specification (Revision 3)  
**Target Repository:** `gauravssingh/electro`  
**Working Branch & Commit:** `main` (`935d51057879d330f436ea3b1f3abc6c18d76267`)  
**Status:** Architecture Approved — Implementation Specification for PR 1 & PR 2  
**Predecessors:** `ARCHITECTURE_REVIEW_EVENTS_AND_SUPPLY_INTERVALS.md` (V1), `ARCHITECTURE_REVIEW_EVENTS_AND_SUPPLY_INTERVALS_V2.md` (V2)

---

## 1. Revised Architectural Verdict

Electro is a private, single-user, self-hosted electricity monitoring application collecting telemetry from an ELNet meter. It runs as a persistent LaunchAgent daemon executing a Python/Flask application with SQLite (WAL mode), paired with a vanilla JS/Tailwind dashboard and a standalone native Rust CLI (`electro`).

### Final Architectural Verdict
The core three-layer decoupled architecture established in V1 and refined in V2 remains valid:
- **Layer 1: Raw Telemetry (`snapshots`, `poll_events`)**: Immutable, read-only ground truth.
- **Layer 2: Persistent Events (`events`)**: Persistent, discrete state changes with bounded uncertainty windows.
- **Layer 3: Persistent Supply Intervals (`supply_intervals`)**: Persistent, contiguous state periods (`GRID`, `DG`, `UNKNOWN`).

However, V2 contained critical correctness and operational flaws that must be fixed before any code is written:
1. **The Transition-Window Accounting Void**: In V2, when the source transitioned from `GRID` (at 09:00:00) to `DG` (at 09:00:15), the `GRID` interval was closed at 09:00:00 and the `DG` interval was opened at 09:00:15. This left the 15-second observation window $[09:00:00, 09:00:15]$ completely unrepresented in any database record. In Revision 3, **all source-transition observation windows are explicitly materialized as contiguous `UNKNOWN` intervals**, ensuring 100% time coverage without falsely attributing transition time to either Grid or DG.
2. **Streaming Sorting vs Checkpoint Flaw**: In V2, incremental snapshot processing queried `WHERE id > ? ORDER BY observed_at_utc ASC, id ASC`. This mixed stream progression with timestamp ordering. If timestamps jittered or arrived out of order, rows could be skipped or checkpoint semantics violated. In Revision 3, **incremental processing strictly follows monotonic ascending `id` (`ORDER BY id ASC`)**, detecting out-of-order timestamps as non-fatal anomalies, while historical rebuilds use global chronological ordering (`ORDER BY observed_at_utc ASC, id ASC`).
3. **Missing Poll-Event Processing Architecture**: V2 designed event taxonomy for connection drops and recoveries but only provided snapshot processing pseudocode. In Revision 3, **an independent poll-event processor with its own checkpoint cursor (`poll_events`) is fully specified**.
4. **Unrealistic Exactly-Once Notification Claims**: V2 proposed decoupling desktop notifications but implied guaranteed exactly-once delivery. Revision 3 explicitly documents the crash-window reality of desktop notifications (at-least-once vs at-most-once) and **formally defers notification modifications until PR 4**.
5. **Flawed Rebuild Mechanism**: V2 suggested `CREATE TABLE AS SELECT`, which strips primary keys, constraints, and defaults in SQLite. Revision 3 specifies exact DDL for staging tables and an atomic table-swap transaction.

---

## 2. Explicit List of V2 Corrections

| # | Flaw Identified in V2 | Revision 3 Correction | Architectural Impact |
|---|---|---|---|
| **1** | **Transition Window Void**: Closing `GRID` at $t_1$ and opening `DG` at $t_2$ leaves $[t_1, t_2]$ unrepresented in the database. | Materialize $[t_1, t_2]$ as an explicit, closed `UNKNOWN` interval across the transition window. | 100% contiguous timeline. Neither source receives false credit during the transition. |
| **2** | **Open Interval Over-Credit**: Credited unobserved time between latest observation and $now()$ as supported runtime. | Supported runtime of an `OPEN` interval is strictly bounded by its latest confirmed observation: $\text{supported} = t_{\text{last\_obs}} - t_{\text{start}}$. Time after $t_{\text{last\_obs}}$ is unconfirmed. | Eliminates false precision and phantom runtime in live queries. |
| **3** | **Mixed Checkpoint Ordering**: Combining `WHERE id > ?` with `ORDER BY observed_at_utc ASC` risks skipping rows during streaming. | Incremental processing queries `WHERE id > ? ORDER BY id ASC`. Out-of-order timestamps are recorded as anomalies without disrupting cursor progression. | Monotonic checkpointing guaranteed; zero skipped snapshots. |
| **4** | **Unspecified Poll-Event Processing**: Pseudocode only covered snapshots; poll events were omitted. | Independent poll-event processor with dedicated checkpoint (`poll_events`) and drop/recovery state machine. | Robust network event persistence completely decoupled from snapshot cadence. |
| **5** | **Implicit Source Normalization**: Incomplete handling of NULL or arbitrary integer values. | Canonical normalization function: `0` $\to$ `GRID`, `1` $\to$ `DG`, NULL/other $\to$ `UNKNOWN`. | Robust handling of corrupted upstream responses or uncalibrated meters. |
| **6** | **Flawed Rebuild DDL**: Used `CREATE TABLE AS SELECT` which drops SQLite constraints and types. | Explicit staging DDL reproducing exact constraints and indexes, followed by an atomic table rename. | Safe, verified, zero-downtime historical rebuilds. |
| **7** | **Notification Crash Window Ignored**: Claimed exactly-once desktop notification delivery. | Acknowledged delivery crash window; notification refactor formally deferred to PR 4. | Clean PR 1 focus on data layer reliability. |

---

## 3. Final Event Schema & Checkpoint Schema (Complete SQLite DDL)

### Migration `storage/migrations/004_events.sql`
```sql
-- Migration: 004_events.sql
-- Creates persistent events table and decoupled processor checkpoints table.

CREATE TABLE IF NOT EXISTS processor_checkpoints (
    processor_name TEXT PRIMARY KEY,
    last_processed_id INTEGER NOT NULL DEFAULT 0,
    last_processed_timestamp TEXT,
    updated_at_utc TEXT NOT NULL
);

-- Seed checkpoints for independent streaming pipelines
INSERT OR IGNORE INTO processor_checkpoints (processor_name, last_processed_id, updated_at_utc)
VALUES 
    ('snapshot_events', 0, datetime('now')),
    ('poll_events', 0, datetime('now'));

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    
    -- Event Classification
    event_type TEXT NOT NULL,
    category TEXT NOT NULL CHECK (category IN ('Power', 'Billing', 'Network', 'Relay')),
    severity TEXT NOT NULL CHECK (severity IN ('info', 'warning', 'alert')),
    
    -- Temporal Semantics (Bounded Uncertainty Window)
    earliest_possible_at_utc TEXT NOT NULL,
    latest_possible_at_utc TEXT NOT NULL,
    occurred_at_utc TEXT, -- Strictly NULL for polled events; populated only if hardware clock certifies exact time
    detected_at_utc TEXT NOT NULL,
    
    -- Evidence & Lineage
    meter_id INTEGER NOT NULL,
    source_snapshot_id INTEGER REFERENCES snapshots(id) ON DELETE SET NULL,
    previous_snapshot_id INTEGER REFERENCES snapshots(id) ON DELETE SET NULL,
    poll_event_id INTEGER REFERENCES poll_events(id) ON DELETE SET NULL,
    
    -- Value Movement
    previous_value TEXT,
    current_value TEXT,
    
    -- Presentation (Materialized for zero-cost API reads and CLI output)
    title TEXT NOT NULL,
    details TEXT NOT NULL,
    metadata_json TEXT,
    
    -- Deterministic Idempotency & Audit
    dedupe_key TEXT NOT NULL UNIQUE,
    created_at_utc TEXT NOT NULL
);

-- Query Performance Indexes
CREATE INDEX IF NOT EXISTS idx_events_latest_possible ON events(latest_possible_at_utc DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_events_category ON events(category, latest_possible_at_utc DESC);
CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type, latest_possible_at_utc DESC);
CREATE INDEX IF NOT EXISTS idx_events_meter ON events(meter_id, latest_possible_at_utc DESC);
CREATE INDEX IF NOT EXISTS idx_events_source_snap ON events(source_snapshot_id);
CREATE INDEX IF NOT EXISTS idx_events_poll_ev ON events(poll_event_id);
```

---

## 4. Final Supply Intervals Schema (PR 2 Specification)

Supply intervals are **strictly deferred to PR 2**. The schema below represents the final specification to be implemented in `005_supply_intervals.sql`.

### Migration `storage/migrations/005_supply_intervals.sql`
```sql
-- Migration: 005_supply_intervals.sql (PR 2 Deliverable)
-- Creates continuous, contiguous supply intervals table.

CREATE TABLE IF NOT EXISTS supply_intervals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    meter_id INTEGER NOT NULL,
    
    -- State Attribution & Lifecycle
    source TEXT NOT NULL CHECK (source IN ('GRID', 'DG', 'UNKNOWN')),
    status TEXT NOT NULL CHECK (status IN ('OPEN', 'CLOSED')),
    
    -- Strict Contiguous Temporal Boundaries (ISO 8601 UTC)
    started_at_utc TEXT NOT NULL,
    ended_at_utc TEXT, -- Strictly NULL when status = 'OPEN', NOT NULL when status = 'CLOSED'
    
    -- Duration Accounting (in seconds)
    duration_seconds REAL,   -- Exactly (ended_at - started_at) for CLOSED; NULL for OPEN
    supported_seconds REAL,  -- Confirmed observation runtime (0.0 for UNKNOWN intervals)
    uncertain_seconds REAL,  -- Unconfirmed runtime (0.0 for GRID/DG; equal to duration for UNKNOWN)
    
    -- Telemetry Lineage
    start_snapshot_id INTEGER NOT NULL REFERENCES snapshots(id) ON DELETE RESTRICT,
    end_snapshot_id INTEGER REFERENCES snapshots(id) ON DELETE RESTRICT, -- NULL when OPEN
    last_confirmed_snapshot_id INTEGER NOT NULL REFERENCES snapshots(id) ON DELETE RESTRICT,
    last_confirmed_at_utc TEXT NOT NULL, -- Bounds the supported runtime of an OPEN interval
    snapshot_count INTEGER NOT NULL DEFAULT 1,
    
    -- Inference Metadata & Versioning
    max_gap_seconds_applied REAL NOT NULL,
    calculation_version INTEGER NOT NULL DEFAULT 1,
    
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL,
    
    -- Strict State Lifecycle Invariants
    CONSTRAINT chk_interval_lifecycle CHECK (
        (status = 'OPEN' AND ended_at_utc IS NULL AND end_snapshot_id IS NULL) OR
        (status = 'CLOSED' AND ended_at_utc IS NOT NULL AND end_snapshot_id IS NOT NULL AND ended_at_utc >= started_at_utc)
    )
);

-- Timeline and Slicing Indexes
CREATE INDEX IF NOT EXISTS idx_intervals_timeline ON supply_intervals(meter_id, started_at_utc ASC);
CREATE INDEX IF NOT EXISTS idx_intervals_range ON supply_intervals(meter_id, started_at_utc, ended_at_utc);
CREATE INDEX IF NOT EXISTS idx_intervals_source ON supply_intervals(source, started_at_utc);

-- Engine-level Guarantee: Exactly ONE OPEN interval per meter
CREATE UNIQUE INDEX IF NOT EXISTS idx_supply_intervals_single_open 
ON supply_intervals(meter_id) 
WHERE status = 'OPEN';
```

---

## 5. Complete Transition Matrix (Contiguous Timeline)

Let $S_{\text{prev}}$ be the prior snapshot at $t_1$, and $S_{\text{curr}}$ be the incoming snapshot at $t_2$.  
Let $I_{\text{open}}$ be the currently open interval started at $t_{\text{start}}$ with source $s_{\text{open}}$.  
Let $\tau_{\text{gap}}$ be the maximum inference gap (default 45.0s).  
Let $\Delta t = t_2 - t_1$.

### The Contiguous Transition Matrix
| # | $I_{\text{open}}\text{.source}$ | $S_{\text{curr}}\text{.norm\_source}$ | Gap Condition | Actions Applied to Supply Intervals | Resulting Interval Records | Events Emitted |
|---|---|---|---|---|---|---|
| **1** | `GRID` | `GRID` | $\Delta t \le \tau_{\text{gap}}$ | Extend $I_{\text{open}}$: update `last_confirmed_at_utc = t2`, `snapshot_count++`. | $I_{\text{open}}$ remains `OPEN` ($t_{\text{start}} \to t_2$) | None |
| **2** | `GRID` | `GRID` | $\Delta t > \tau_{\text{gap}}$ | 1. Close $I_{\text{open}}$ at $t_1$.<br>2. Insert closed `UNKNOWN`: $[t_1, t_2]$.<br>3. Open new `GRID`: at $t_2$. | 1. `GRID` ($t_{\text{start}} \to t_1$, CLOSED)<br>2. `UNKNOWN` ($t_1 \to t_2$, CLOSED)<br>3. `GRID` (at $t_2$, OPEN) | None |
| **3** | `GRID` | `DG` | $\Delta t \le \tau_{\text{gap}}$ | 1. Close $I_{\text{open}}$ at $t_1$.<br>2. Insert closed `UNKNOWN`: $[t_1, t_2]$ (transition window).<br>3. Open new `DG`: at $t_2$. | 1. `GRID` ($t_{\text{start}} \to t_1$, CLOSED)<br>2. `UNKNOWN` ($t_1 \to t_2$, CLOSED)<br>3. `DG` (at $t_2$, OPEN) | `source_transition_grid_to_dg`<br>(window $[t_1, t_2]$, `is_gap = false`) |
| **4** | `GRID` | `DG` | $\Delta t > \tau_{\text{gap}}$ | 1. Close $I_{\text{open}}$ at $t_1$.<br>2. Insert closed `UNKNOWN`: $[t_1, t_2]$ (excessive gap).<br>3. Open new `DG`: at $t_2$. | 1. `GRID` ($t_{\text{start}} \to t_1$, CLOSED)<br>2. `UNKNOWN` ($t_1 \to t_2$, CLOSED)<br>3. `DG` (at $t_2$, OPEN) | `source_transition_grid_to_dg`<br>(window $[t_1, t_2]$, `is_gap = true`) |
| **5** | `DG` | `GRID` | $\Delta t \le \tau_{\text{gap}}$ | 1. Close $I_{\text{open}}$ at $t_1$.<br>2. Insert closed `UNKNOWN`: $[t_1, t_2]$ (transition window).<br>3. Open new `GRID`: at $t_2$. | 1. `DG` ($t_{\text{start}} \to t_1$, CLOSED)<br>2. `UNKNOWN` ($t_1 \to t_2$, CLOSED)<br>3. `GRID` (at $t_2$, OPEN) | `source_transition_dg_to_grid`<br>(window $[t_1, t_2]$, `is_gap = false`) |
| **6** | `DG` | `GRID` | $\Delta t > \tau_{\text{gap}}$ | 1. Close $I_{\text{open}}$ at $t_1$.<br>2. Insert closed `UNKNOWN`: $[t_1, t_2]$.<br>3. Open new `GRID`: at $t_2$. | 1. `DG` ($t_{\text{start}} \to t_1$, CLOSED)<br>2. `UNKNOWN` ($t_1 \to t_2$, CLOSED)<br>3. `GRID` (at $t_2$, OPEN) | `source_transition_dg_to_grid`<br>(window $[t_1, t_2]$, `is_gap = true`) |
| **7** | `DG` | `DG` | $\Delta t \le \tau_{\text{gap}}$ | Extend $I_{\text{open}}$: update `last_confirmed_at_utc = t2`, `snapshot_count++`. | $I_{\text{open}}$ remains `OPEN` ($t_{\text{start}} \to t_2$) | None |
| **8** | `DG` | `DG` | $\Delta t > \tau_{\text{gap}}$ | 1. Close $I_{\text{open}}$ at $t_1$.<br>2. Insert closed `UNKNOWN`: $[t_1, t_2]$.<br>3. Open new `DG`: at $t_2$. | 1. `DG` ($t_{\text{start}} \to t_1$, CLOSED)<br>2. `UNKNOWN` ($t_1 \to t_2$, CLOSED)<br>3. `DG` (at $t_2$, OPEN) | None |
| **9** | `GRID` | `UNKNOWN` | Any | 1. Close $I_{\text{open}}$ at $t_1$.<br>2. Open new `UNKNOWN`: started at $t_1$. | 1. `GRID` ($t_{\text{start}} \to t_1$, CLOSED)<br>2. `UNKNOWN` (started at $t_1$, OPEN) | `source_became_unknown`<br>(window $[t_1, t_2]$) |
| **10** | `DG` | `UNKNOWN` | Any | 1. Close $I_{\text{open}}$ at $t_1$.<br>2. Open new `UNKNOWN`: started at $t_1$. | 1. `DG` ($t_{\text{start}} \to t_1$, CLOSED)<br>2. `UNKNOWN` (started at $t_1$, OPEN) | `source_became_unknown`<br>(window $[t_1, t_2]$) |
| **11** | `UNKNOWN` | `GRID` | Any | 1. Close $I_{\text{open}}$ at $t_2$.<br>2. Open new `GRID`: started at $t_2$. | 1. `UNKNOWN` ($t_{\text{start}} \to t_2$, CLOSED)<br>2. `GRID` (started at $t_2$, OPEN) | None (telemetry recovered) |
| **12** | `UNKNOWN` | `DG` | Any | 1. Close $I_{\text{open}}$ at $t_2$.<br>2. Open new `DG`: started at $t_2$. | 1. `UNKNOWN` ($t_{\text{start}} \to t_2$, CLOSED)<br>2. `DG` (started at $t_2$, OPEN) | None |
| **13** | `UNKNOWN` | `UNKNOWN` | $\Delta t \le \tau_{\text{gap}}$ | Extend $I_{\text{open}}$ to $t_2$. | $I_{\text{open}}$ remains `OPEN` ($t_{\text{start}} \to t_2$) | None |
| **14** | `UNKNOWN` | `UNKNOWN` | $\Delta t > \tau_{\text{gap}}$ | Extend $I_{\text{open}}$ across the gap (all unmonitored time is UNKNOWN). | $I_{\text{open}}$ remains `OPEN` ($t_{\text{start}} \to t_2$) | None |
| **15** | *Genesis* | Any | N/A | Open initial interval of source $S_{\text{curr}}$ at $t_0$. | Initial interval (started at $t_0$, OPEN) | None |

### Key Properties of This State Machine
1. **Zero Temporal Gaps**: For every pair of adjacent intervals $I_k$ and $I_{k+1}$, $I_k\text{.ended\_at\_utc} == I_{k+1}\text{.started\_at\_utc}$.
2. **Strict Source Attribution**: If power was Grid at 09:00:00 and DG at 09:00:15, the 15-second transition window is `UNKNOWN`. Neither Grid nor DG receives credit for time when the source state was in flux.
3. **No Overlaps**: Adjacent intervals touch at a point boundary without overlapping durations.

---

## 6. Exact Temporal and Duration Semantics

### 1. Canonical Source Normalization
All raw values must pass through canonical normalization before evaluation:
```python
def normalize_source_code(val: Any) -> str:
    """Map raw eb_dg_status to canonical source state string."""
    if val is None:
        return "UNKNOWN"
    try:
        ival = int(val)
        if ival == 0:
            return "GRID"
        elif ival == 1:
            return "DG"
        return "UNKNOWN"
    except (ValueError, TypeError):
        return "UNKNOWN"
```

### 2. Duration & Accounting Formulas
For any **CLOSED** interval $I = [t_{\text{start}}, t_{\text{end}}]$:
$$\Delta T = \text{epoch\_seconds}(t_{\text{end}}) - \text{epoch\_seconds}(t_{\text{start}})$$
- `duration_seconds` $= \Delta T$
- If `source == 'GRID'`:
  - `supported_seconds` $= \Delta T$
  - `uncertain_seconds` $= 0.0$
- If `source == 'DG'`:
  - `supported_seconds` $= \Delta T$
  - `uncertain_seconds` $= 0.0$
- If `source == 'UNKNOWN'`:
  - `supported_seconds` $= 0.0$
  - `uncertain_seconds` $= \Delta T$

### 3. Open Interval Semantics (No False Credit)
For an **OPEN** interval $I_{\text{open}}$ with start boundary $t_{\text{start}}$ and latest confirmed observation $t_{\text{last\_obs}}$:
- **Supported Runtime**: Bounded strictly by the latest observation:
  $$\text{supported\_seconds} = \text{epoch\_seconds}(t_{\text{last\_obs}}) - \text{epoch\_seconds}(t_{\text{start}})$$
- **In-Flight / Unconfirmed Elapsed Time**:
  $$\text{unconfirmed\_seconds} = \max(0, \text{epoch\_seconds}(now()) - \text{epoch\_seconds}(t_{\text{last\_obs}}))$$
- **Rule**: Live dashboards must **never credit unobserved time after $t_{\text{last\_obs}}$ as supported runtime**. If the poller stops running, the reported supported runtime stops advancing immediately.

### 4. Mathematical Timeline Invariant
Across any historical evaluation window $[T_0, T_1]$:
$$\text{Total Represented Time} = \text{Grid Time} + \text{DG Time} + \text{Unknown Time}$$
$$\text{Telemetry Coverage \%} = \frac{\text{Grid Time} + \text{DG Time}}{\text{Total Represented Time}} \times 100\%$$

---

## 7. Incremental Snapshot Processor Pseudocode

### The Stream Progression Rule
- Incremental processing queries strictly by **ascending snapshot ID**:
  ```sql
  SELECT * FROM snapshots WHERE id > ? ORDER BY id ASC LIMIT ?;
  ```
- The checkpoint advances strictly to the **highest sequentially processed snapshot ID**.
- Out-of-order timestamps ($t_{\text{curr}} < t_{\text{prev}}$) are handled as follows:
  - Logged as a temporal anomaly.
  - The snapshot's non-temporal events (e.g. balance threshold) are evaluated.
  - Duration is incremented by 0 to prevent negative time intervals.
  - Checkpoint advances normally. No rows are skipped.

```python
class SnapshotEventProcessor:
    def __init__(self, db_path: str, poll_interval_seconds: int = 15):
        self.db_path = db_path
        self.max_gap = max(poll_interval_seconds * 2.5, 45.0)

    def process_pending(self, batch_size: int = 100) -> int:
        """Process unprocessed snapshots in ascending ID order in a single transaction."""
        with get_db_connection(self.db_path) as conn:
            # 1. Fetch checkpoint
            row = conn.execute(
                "SELECT last_processed_id FROM processor_checkpoints WHERE processor_name = 'snapshot_events';"
            ).fetchone()
            last_id = row["last_processed_id"] if row else 0

            # 2. Query strictly by ascending ID
            pending = conn.execute(
                "SELECT * FROM snapshots WHERE id > ? ORDER BY id ASC LIMIT ?;",
                (last_id, batch_size),
            ).fetchall()

            if not pending:
                return 0

            # 3. Fetch preceding snapshot for transition context
            prev_snap = conn.execute(
                "SELECT * FROM snapshots WHERE id = ?;",
                (last_id,),
            ).fetchone()

            for curr_snap in pending:
                # Detect and handle out-of-order timestamp anomaly
                if prev_snap and curr_snap["observed_at_utc"] < prev_snap["observed_at_utc"]:
                    logger.warning(
                        "Timestamp anomaly detected: snapshot %d (%s) < snapshot %d (%s)",
                        curr_snap["id"], curr_snap["observed_at_utc"],
                        prev_snap["id"], prev_snap["observed_at_utc"]
                    )
                    # Non-fatal: process balance/relay events, ignore negative time delta

                # Generate discrete events into 'events' table
                self._evaluate_snapshot_events(conn, prev_snap, curr_snap)

                prev_snap = curr_snap
                last_id = curr_snap["id"]

            # 4. Advance checkpoint atomically
            conn.execute(
                """
                UPDATE processor_checkpoints 
                SET last_processed_id = ?, 
                    last_processed_timestamp = ?,
                    updated_at_utc = datetime('now')
                WHERE processor_name = 'snapshot_events';
                """,
                (last_id, prev_snap["observed_at_utc"] if prev_snap else None),
            )
            # Transaction commits here
            return len(pending)

    def _evaluate_snapshot_events(self, conn, prev, curr):
        if not prev:
            return

        meter_id = curr["meter_id"]
        t_prev = prev["observed_at_utc"]
        t_curr = curr["observed_at_utc"]
        s_prev = normalize_source_code(prev["eb_dg_status"])
        s_curr = normalize_source_code(curr["eb_dg_status"])

        # 1. Power Source Transitions
        if s_prev != s_curr:
            event_type = None
            category = "Power"
            severity = "info"
            title = ""
            details = ""

            if s_prev == "GRID" and s_curr == "DG":
                event_type = "source_transition_grid_to_dg"
                severity = "alert"
                title = "Switched to Backup Generator (DG)"
                details = f"Outage detected on main grid; DG backup power active."
            elif s_prev == "DG" and s_curr == "GRID":
                event_type = "source_transition_dg_to_grid"
                severity = "info"
                title = "Power Restored (Main Grid)"
                details = f"Grid (EB) electricity supply restored."
            elif s_curr == "UNKNOWN":
                event_type = "source_became_unknown"
                severity = "warning"
                title = "Power Source Unknown"
                details = f"Meter reported unrecognized source status: {curr['eb_dg_status']}."

            if event_type:
                dedupe_key = f"src_change:{meter_id}:{prev['id']}:{curr['id']}"
                conn.execute(
                    """
                    INSERT OR IGNORE INTO events (
                        event_type, category, severity,
                        earliest_possible_at_utc, latest_possible_at_utc, occurred_at_utc, detected_at_utc,
                        meter_id, source_snapshot_id, previous_snapshot_id,
                        previous_value, current_value, title, details, metadata_json, dedupe_key, created_at_utc
                    ) VALUES (
                        ?, ?, ?,
                        ?, ?, NULL, datetime('now'),
                        ?, ?, ?,
                        ?, ?, ?, ?, NULL, ?, datetime('now')
                    );
                    """,
                    (event_type, category, severity, t_prev, t_curr, meter_id, curr["id"], prev["id"], s_prev, s_curr, title, details, dedupe_key),
                )

        # 2. Relay Status Changes
        rel_prev = str(prev["relay_status"]).strip().upper() if prev["relay_status"] else "UNKNOWN"
        rel_curr = str(curr["relay_status"]).strip().upper() if curr["relay_status"] else "UNKNOWN"
        if rel_prev != "UNKNOWN" and rel_curr != "UNKNOWN" and rel_prev != rel_curr:
            is_disconnect = rel_curr in ("OFF", "DISCONNECTED", "0")
            event_type = "relay_opened" if is_disconnect else "relay_closed"
            severity = "alert" if is_disconnect else "info"
            title = "Relay Disconnected" if is_disconnect else "Relay Reconnected"
            details = f"Relay status changed from {rel_prev} to {rel_curr}."
            dedupe_key = f"relay_change:{meter_id}:{prev['id']}:{curr['id']}"

            conn.execute(
                """
                INSERT OR IGNORE INTO events (
                    event_type, category, severity,
                    earliest_possible_at_utc, latest_possible_at_utc, occurred_at_utc, detected_at_utc,
                    meter_id, source_snapshot_id, previous_snapshot_id,
                    previous_value, current_value, title, details, metadata_json, dedupe_key, created_at_utc
                ) VALUES (
                    ?, 'Relay', ?,
                    ?, ?, NULL, datetime('now'),
                    ?, ?, ?,
                    ?, ?, ?, ?, NULL, ?, datetime('now')
                );
                """,
                (event_type, severity, t_prev, t_curr, meter_id, curr["id"], prev["id"], rel_prev, rel_curr, title, details, dedupe_key),
            )

        # 3. Prepaid Balance Warnings & Recharges
        b_prev = prev["meter_balance"]
        b_curr = curr["meter_balance"]
        if b_prev is not None and b_curr is not None:
            diff = float(b_curr) - float(b_prev)
            if diff >= 20.0:  # Configurable recharge threshold
                conn.execute(
                    """
                    INSERT OR IGNORE INTO events (
                        event_type, category, severity,
                        earliest_possible_at_utc, latest_possible_at_utc, occurred_at_utc, detected_at_utc,
                        meter_id, source_snapshot_id, previous_snapshot_id,
                        previous_value, current_value, title, details, dedupe_key, created_at_utc
                    ) VALUES (
                        'balance_recharge_detected', 'Billing', 'info',
                        ?, ?, NULL, datetime('now'),
                        ?, ?, ?,
                        ?, ?, ?, ?, ?, datetime('now')
                    );
                    """,
                    (t_prev, t_curr, meter_id, curr["id"], prev["id"], str(b_prev), str(b_curr),
                     f"Balance Recharged (+₹{diff:.2f})", f"Prepaid credit increased from ₹{b_prev:.2f} to ₹{b_curr:.2f}.",
                     f"recharge:{meter_id}:{prev['id']}:{curr['id']}"),
                )
            elif float(b_prev) >= 500.0 and float(b_curr) < 500.0:  # Configurable low balance threshold
                conn.execute(
                    """
                    INSERT OR IGNORE INTO events (
                        event_type, category, severity,
                        earliest_possible_at_utc, latest_possible_at_utc, occurred_at_utc, detected_at_utc,
                        meter_id, source_snapshot_id, previous_snapshot_id,
                        previous_value, current_value, title, details, dedupe_key, created_at_utc
                    ) VALUES (
                        'low_balance_warning', 'Billing', 'warning',
                        ?, ?, NULL, datetime('now'),
                        ?, ?, ?,
                        ?, ?, ?, ?, ?, datetime('now')
                    );
                    """,
                    (t_prev, t_curr, meter_id, curr["id"], prev["id"], str(b_prev), str(b_curr),
                     "Low Balance Threshold Reached", f"Prepaid balance dipped to ₹{b_curr:.2f} (below ₹500 threshold).",
                     f"low_bal:{meter_id}:{prev['id']}:{curr['id']}"),
                )
```

---

## 8. Independent Poll-Event Processor Pseudocode

```python
class PollEventProcessor:
    """Processes network telemetry health and connection transitions independently."""

    def __init__(self, db_path: str):
        self.db_path = db_path

    def process_pending(self, batch_size: int = 100) -> int:
        """Process poll_events stream in ascending ID order."""
        with get_db_connection(self.db_path) as conn:
            row = conn.execute(
                "SELECT last_processed_id FROM processor_checkpoints WHERE processor_name = 'poll_events';"
            ).fetchone()
            last_id = row["last_processed_id"] if row else 0

            pending = conn.execute(
                "SELECT * FROM poll_events WHERE id > ? ORDER BY id ASC LIMIT ?;",
                (last_id, batch_size),
            ).fetchall()

            if not pending:
                return 0

            # Fetch preceding poll_event to know previous success status
            prev_poll = conn.execute(
                "SELECT * FROM poll_events WHERE id = ?;",
                (last_id,),
            ).fetchone()

            for curr_poll in pending:
                if prev_poll:
                    prev_success = bool(prev_poll["success"])
                    curr_success = bool(curr_poll["success"])

                    # 1. Connection Drop
                    if prev_success and not curr_success:
                        err_cat = curr_poll["error_category"] or "network_error"
                        err_msg = curr_poll["error_message"] or "Unable to reach meter telemetry."
                        dedupe_key = f"poll_drop:{curr_poll['meter_id'] or 0}:{curr_poll['id']}"
                        
                        conn.execute(
                            """
                            INSERT OR IGNORE INTO events (
                                event_type, category, severity,
                                earliest_possible_at_utc, latest_possible_at_utc, occurred_at_utc, detected_at_utc,
                                meter_id, poll_event_id,
                                previous_value, current_value, title, details, dedupe_key, created_at_utc
                            ) VALUES (
                                'telemetry_connection_dropped', 'Network', 'warning',
                                ?, ?, NULL, datetime('now'),
                                ?, ?,
                                'connected', 'disconnected',
                                'Connection Dropped', ?, ?, datetime('now')
                            );
                            """,
                            (prev_poll["attempted_at_utc"], curr_poll["attempted_at_utc"],
                             curr_poll["meter_id"] or 0, curr_poll["id"],
                             f"Upstream poll failed: {err_msg} ({err_cat}).", dedupe_key),
                        )

                    # 2. Connection Recovery
                    elif not prev_success and curr_success:
                        dedupe_key = f"poll_recov:{curr_poll['meter_id'] or 0}:{curr_poll['id']}"
                        conn.execute(
                            """
                            INSERT OR IGNORE INTO events (
                                event_type, category, severity,
                                earliest_possible_at_utc, latest_possible_at_utc, occurred_at_utc, detected_at_utc,
                                meter_id, poll_event_id,
                                previous_value, current_value, title, details, dedupe_key, created_at_utc
                            ) VALUES (
                                'telemetry_connection_restored', 'Network', 'info',
                                ?, ?, NULL, datetime('now'),
                                ?, ?,
                                'disconnected', 'connected',
                                'Connection Restored', 'Telemetry communication restored successfully.',
                                ?, datetime('now')
                            );
                            """,
                            (prev_poll["attempted_at_utc"], curr_poll["attempted_at_utc"],
                             curr_poll["meter_id"] or 0, curr_poll["id"], dedupe_key),
                        )

                prev_poll = curr_poll
                last_id = curr_poll["id"]

            # Advance poll_events checkpoint atomically
            conn.execute(
                """
                UPDATE processor_checkpoints 
                SET last_processed_id = ?, 
                    last_processed_timestamp = ?,
                    updated_at_utc = datetime('now')
                WHERE processor_name = 'poll_events';
                """,
                (last_id, prev_poll["attempted_at_utc"] if prev_poll else None),
            )
            return len(pending)
```

---

## 9. Historical Rebuild Procedure (SQLite-Safe)

To rebuild events or supply intervals safely without `CREATE TABLE AS SELECT` flaws:

### Rebuild Script Workflow (`scripts/rebuild_derived_data.py`)
1. **Explicit Staging DDL**:
   ```sql
   DROP TABLE IF EXISTS events_rebuild;
   CREATE TABLE events_rebuild (
       id INTEGER PRIMARY KEY AUTOINCREMENT,
       event_type TEXT NOT NULL,
       category TEXT NOT NULL CHECK (category IN ('Power', 'Billing', 'Network', 'Relay')),
       severity TEXT NOT NULL CHECK (severity IN ('info', 'warning', 'alert')),
       earliest_possible_at_utc TEXT NOT NULL,
       latest_possible_at_utc TEXT NOT NULL,
       occurred_at_utc TEXT,
       detected_at_utc TEXT NOT NULL,
       meter_id INTEGER NOT NULL,
       source_snapshot_id INTEGER,
       previous_snapshot_id INTEGER,
       poll_event_id INTEGER,
       previous_value TEXT,
       current_value TEXT,
       title TEXT NOT NULL,
       details TEXT NOT NULL,
       metadata_json TEXT,
       dedupe_key TEXT NOT NULL UNIQUE,
       created_at_utc TEXT NOT NULL
   );
   ```
2. **Replay Snapshots in Strict Chronological Order**:
   ```sql
   SELECT * FROM snapshots ORDER BY observed_at_utc ASC, id ASC;
   ```
   All events generated are written into `events_rebuild`.
3. **Replay Poll Events**:
   ```sql
   SELECT * FROM poll_events ORDER BY attempted_at_utc ASC, id ASC;
   ```
   All network drop/recovery events written into `events_rebuild`.
4. **Validation Check**:
   Verify `COUNT(*) > 0` and that no duplicate dedupe keys were skipped erroneously.
5. **Atomic Swap Transaction**:
   ```sql
   BEGIN TRANSACTION;
   -- Recreate indexes on staging table
   CREATE INDEX idx_events_rebuild_latest ON events_rebuild(latest_possible_at_utc DESC, id DESC);
   CREATE INDEX idx_events_rebuild_cat ON events_rebuild(category, latest_possible_at_utc DESC);
   
   -- Swap tables
   ALTER TABLE events RENAME TO events_old;
   ALTER TABLE events_rebuild RENAME TO events;
   DROP TABLE events_old;
   
   -- Update checkpoint
   UPDATE processor_checkpoints 
   SET last_processed_id = (SELECT MAX(id) FROM snapshots),
       updated_at_utc = datetime('now')
   WHERE processor_name = 'snapshot_events';
   
   UPDATE processor_checkpoints 
   SET last_processed_id = (SELECT MAX(id) FROM poll_events),
       updated_at_utc = datetime('now')
   WHERE processor_name = 'poll_events';
   COMMIT;
   ```

---

## 10. Updated Test Matrix with Expected Outcomes

| Area | Test Identifier | Scenario & Input Setup | Expected Database State | Observable Output |
|---|---|---|---|---|
| **Events** | `test_grid_to_dg_event` | Snap 1: Grid (10:00:00), Snap 2: DG (10:00:15) | 1 row in `events` (`event_type = 'source_transition_grid_to_dg'`, `category = 'Power'`, `severity = 'alert'`, `earliest = 10:00:00`, `latest = 10:00:15`, `occurred = NULL`) | `/api/events` returns outage alert |
| **Events** | `test_null_source_normalization` | Snap 1: Grid (10:00:00), Snap 2: `eb_dg_status = NULL` | 1 row in `events` (`event_type = 'source_became_unknown'`, `severity = 'warning'`) | UI displays unknown source event |
| **Events** | `test_poll_connection_drop_and_recovery` | Poll 1: success=1, Poll 2: success=0, Poll 3: success=1 | 2 rows in `events`: 1 `telemetry_connection_dropped`, 1 `telemetry_connection_restored` | Network tab shows drop & recovery |
| **Idempotency** | `test_snapshot_processor_replay` | Process snapshots 1–10, then reset cursor and reprocess | `events` count unchanged; 0 duplicates inserted | API response unchanged |
| **Idempotency** | `test_out_of_order_timestamp_handling` | Snap 10 (10:00:15), Snap 11 (10:00:10), Snap 12 (10:00:30) | Checkpoint advances 10 $\to$ 11 $\to$ 12; warning logged; no crash | All snapshots processed |
| **Intervals** | `test_transition_window_is_unknown` | Snap 1: Grid (09:00:00), Snap 2: DG (09:00:15) | 1 closed Grid (ended 09:00:00), 1 closed Unknown (09:00:00 $\to$ 09:00:15, 15s), 1 open DG (started 09:00:15) | Grid time = 0s credit for transition |
| **Intervals** | `test_open_interval_no_future_credit` | Interval opened at 10:00:00, last snapshot at 10:05:00, current time is 10:20:00 | Database `last_confirmed_at_utc = 10:05:00`; supported runtime query returns 300s (not 1200s) | No phantom runtime |
| **Intervals** | `test_single_open_index_enforcement` | Attempt inserting second row with `status = 'OPEN'` for same meter | SQLite throws `IntegrityError` (`idx_supply_intervals_single_open`) | Corrupt open intervals rejected |
| **Rebuild** | `test_safe_historical_rebuild` | Run historical backfill across existing 1,206 snapshots | Staging table swapped; `processor_checkpoints` = 1206; WAL valid | Zero data loss; zero downtime |

---

## 11. Implementation Boundaries and Acceptance Criteria

### PR 1: Persistent Events Foundation
**Scope**:
- Migration `004_events.sql` (`events` and `processor_checkpoints`).
- Snapshot event processing (`services/state_processor.py`).
- Poll-event processing (`services/poll_processor.py` or integrated).
- Deterministic backfill script (`scripts/backfill_events.py`).
- Refactor `SnapshotRepository.get_unified_events()` to query `events` table with index.
- Unit tests (`tests/test_events.py`).

**Explicitly Excluded from PR 1**:
- Supply intervals schema and processing (PR 2).
- Duration analytics and `/api/history` modifications (PR 3).
- Notification service modifications (PR 4).
- Frontend UI redesign.

**PR 1 Acceptance Criteria**:
1. Raw telemetry collection (`snapshots`, `poll_events`) continues completely uninterrupted.
2. Events are persisted to SQLite with exact observation boundaries (`earliest_possible_at_utc`, `latest_possible_at_utc`, `occurred_at_utc = NULL`).
3. Replaying snapshots or poll events produces zero duplicate events (`dedupe_key` uniqueness).
4. Out-of-order timestamps in `snapshots` are detected without breaking checkpoint advancement.
5. `/api/events` response format matches the existing JSON schema 1:1.
6. Rust CLI `electro events` displays historical events seamlessly.
7. All existing unit tests pass.

---

### PR 2: Supply Intervals Foundation
**Scope**:
- Migration `005_supply_intervals.sql`.
- Interval state machine enforcing transition-window `UNKNOWN` intervals.
- Single open interval enforcement via SQLite partial index.
- Historical intervals rebuild script.
- Unit tests for contiguous intervals and gap handling.

---

### PR 3: Analytics Cutover
**Scope**:
- Refactor `/api/history` and `/api/supply/summary` to query pre-aggregated `supply_intervals`.
- Replace Python $dt/2$ transition loop with exact interval slicing.

---

### PR 4: Decoupled Notifications
**Scope**:
- Event-driven alerts consuming committed events from SQLite.
- Dispatch state tracking in `notification_dispatch_log`.

---

## 12. Remaining Product-Owner Decisions

1. **Backfill Horizon for PR 1**:
   - *Recommendation*: Backfill all 1,206 existing snapshots and 1,154 poll events in `electro.db` (execution time $< 0.2\text{s}$).
2. **Notification Behavior During PR 1 & PR 2**:
   - *Recommendation*: Leave the current `NotificationService` behavior completely untouched. Address decoupled notifications in PR 4 once derived tables are fully stable.
3. **Transition Window Display in UI**:
   - *Recommendation*: The UI in PR 3 can highlight transition-window `UNKNOWN` intervals with a subtle transition badge ("Switching... 15s") rather than presenting them as an unexplained telemetry loss.
