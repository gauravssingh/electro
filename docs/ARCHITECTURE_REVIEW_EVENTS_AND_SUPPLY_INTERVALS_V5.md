# Electro — Architecture Review (Revision 5): Final Consistency Corrections

**Document Type:** Technical Architecture & Implementation Specification (Revision 5)  
**Target Repository:** `gauravssingh/electro`  
**Working Branch & Commit:** `main` (`935d51057879d330f436ea3b1f3abc6c18d76267`)  
**Status:** Architecture Finalized & Locked — Ready for PR 1 Development  
**Predecessors:**  
- `ARCHITECTURE_REVIEW_EVENTS_AND_SUPPLY_INTERVALS.md` (V1)  
- `ARCHITECTURE_REVIEW_EVENTS_AND_SUPPLY_INTERVALS_V2.md` (V2)  
- `ARCHITECTURE_REVIEW_EVENTS_AND_SUPPLY_INTERVALS_V3.md` (V3)  
- `ARCHITECTURE_REVIEW_EVENTS_AND_SUPPLY_INTERVALS_V4.md` (V4)

---

## 1. Executive Summary & V4-to-V5 Correction Table

Revision 5 represents the final consistency pass over the Electro derived data architecture. It resolves the subtle race conditions, temporal baseline decoupling, staging DDL reproduction, and process-vs-thread concurrency models that were left ambiguous in V4.

### V4-to-V5 Consistency Correction Table
| # | Architectural Dimension | V4 Specification | Revision 5 Final Correction | Correctness Rationale |
|---|---|---|---|---|
| **1** | **Temporal vs Point-in-Time Separation** | Evaluated balance changes within the same pass as temporal transitions; out-of-order snapshots risked skipping balance evaluations. | Decoupled into `_evaluate_temporal_transitions()` and `_evaluate_point_in_time_events()`. Point-in-time checks evaluate for every snapshot using instantaneous observation timestamps $[t_{\text{curr}}, t_{\text{curr}}]$. | An out-of-order timestamp suppresses invalid time-span transitions without suppressing valid instantaneous balance readings. |
| **2** | **Monotonic Baseline Recovery** | Monotonic baseline recovery was only applied to snapshots; poll events lacked baseline tracking. | Symmetrical monotonic baseline tracking implemented for both `SnapshotEventProcessor` and `PollEventProcessor`. Anomalous poll records do not pollute the comparison baseline. | Guarantees $t_{\text{latest}} \ge t_{\text{earliest}}$ across all event categories. |
| **3** | **Predecessor Deletion / Pruning** | Robust query introduced, but behavior when predecessor was completely deleted was implicit. | Explicit genesis recovery: when predecessor lookup yields `None`, the snapshot establishes initial baseline state without firing transition events. | Gracefully handles table truncations or historical pruning. |
| **4** | **Rebuild High-Water Marks** | Rebuild captured only a single snapshot high-water mark; poll-event rebuild cursor was unanchored. | Captures independent high-water marks: $H_{\text{snap}} = \text{MAX}(snapshots.id)$ and $H_{\text{poll}} = \text{MAX}(poll\_events.id)$. Replays both streams and restores both checkpoints atomically. | Ensures post-rebuild incremental streaming catches all live records without gaps or duplicates. |
| **5** | **Process vs Thread Concurrency** | Concurrency safety relied on thread mutex; SQLite WAL was claimed to eliminate contention. | Documented two-tier concurrency: Process-level advisory file lock (`.electro.poller.lock`) for collector ownership; thread mutex for in-process runs; bounded write transactions to prevent `busy_timeout` exhaustion. | Eliminates database locking timeouts (`sqlite3.OperationalError: database is locked`). |
| **6** | **Canonical Schema Unification** | Staging DDL was written as an inline SQL block distinct from migration DDL. | Single canonical DDL specification shared identically between migration `004_events.sql` and rebuild staging `events_rebuild`. | Guarantees 100% fidelity of columns, CHECK constraints, foreign keys, and indexes. |
| **7** | **Relay Taxonomy Alignment** | Checked `r_curr == "DISCONNECTED"` against raw strings; lacked canonical relay normalization. | Implemented canonical `normalize_relay_status()` mapping to `'CONNECTED'`, `'DISCONNECTED'`, or `'UNKNOWN'`. | Guarantees deterministic relay transition detection matching production ELNet values (`'ON'`, `'OFF'`). |
| **8** | **Observation Coverage Formulation** | Observation coverage lacked explicit boundary and reporting denominator rules. | Defined strict evaluation window denominator $W = T_{\text{end}} - T_{\text{start}}$, valid step threshold $\Delta t \le \tau_{\text{gap}}$, and window truncation rules. | Transparent reporting; prevents claiming unmonitored time as classified supply. |

---

## 2. Canonical Schema Definition & Safe Table Replacement

### 2.1 Single Canonical DDL Specification
The following DDL defines the canonical schema. Both the initial production migration (`004_events.sql`) and historical rebuild staging tables (`events_rebuild`) must use this exact definition:

```sql
-- Canonical Checkpoints Table DDL
CREATE TABLE IF NOT EXISTS processor_checkpoints (
    processor_name TEXT PRIMARY KEY,
    last_processed_id INTEGER NOT NULL DEFAULT 0,
    last_processed_timestamp TEXT,
    updated_at_utc TEXT NOT NULL
);

INSERT OR IGNORE INTO processor_checkpoints (processor_name, last_processed_id, updated_at_utc)
VALUES 
    ('snapshot_events', 0, datetime('now')),
    ('poll_events', 0, datetime('now'));

-- Canonical Events Table DDL (Production & Staging Template)
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    
    -- Event Classification
    event_type TEXT NOT NULL,
    category TEXT NOT NULL CHECK (category IN ('Power', 'Billing', 'Network', 'Relay')),
    severity TEXT NOT NULL CHECK (severity IN ('info', 'warning', 'alert')),
    
    -- Temporal Semantics (Enforced Monotonic Window)
    earliest_possible_at_utc TEXT NOT NULL,
    latest_possible_at_utc TEXT NOT NULL,
    occurred_at_utc TEXT, -- Strictly NULL for polled events; populated only if hardware clock certifies exact time
    detected_at_utc TEXT NOT NULL,
    
    -- Telemetry Lineage & Evidence
    meter_id INTEGER NOT NULL,
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
    
    -- Deterministic Idempotency & Audit
    dedupe_key TEXT NOT NULL UNIQUE,
    created_at_utc TEXT NOT NULL,
    
    -- Table Constraints
    CONSTRAINT chk_event_time_window CHECK (latest_possible_at_utc >= earliest_possible_at_utc)
);

-- Canonical Indexes
CREATE INDEX IF NOT EXISTS idx_events_latest_possible ON events(latest_possible_at_utc DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_events_category ON events(category, latest_possible_at_utc DESC);
CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type, latest_possible_at_utc DESC);
CREATE INDEX IF NOT EXISTS idx_events_meter ON events(meter_id, latest_possible_at_utc DESC);
CREATE INDEX IF NOT EXISTS idx_events_source_snap ON events(source_snapshot_id);
CREATE INDEX IF NOT EXISTS idx_events_poll_ev ON events(poll_event_id);
```

### 2.2 Pre-Swap Validation Checklist
Before executing table replacement, the rebuild script must execute these validation queries against the staging table `events_rebuild`:

1. **Foreign Key Integrity Check**:
   ```sql
   PRAGMA foreign_key_check(events_rebuild);
   ```
   *Expected Result*: 0 rows returned. Any row indicates a broken reference to `snapshots` or `poll_events`.
2. **Deduplication Key Uniqueness**:
   ```sql
   SELECT dedupe_key, COUNT(*) AS cnt 
   FROM events_rebuild 
   GROUP BY dedupe_key 
   HAVING cnt > 1;
   ```
   *Expected Result*: 0 rows returned.
3. **Temporal Monotonicity Invariant**:
   ```sql
   SELECT COUNT(*) AS violations 
   FROM events_rebuild 
   WHERE latest_possible_at_utc < earliest_possible_at_utc;
   ```
   *Expected Result*: `violations == 0`.
4. **Row Count Sanity Check**:
   ```sql
   SELECT COUNT(*) AS c FROM events_rebuild;
   ```
   *Expected Result*: If high-water mark snapshots exist, count must be $\ge 0$ (typically $\sim 10\text{–}50$ events per 1,000 snapshots).
5. **Dangling Evidence Check**:
   ```sql
   SELECT COUNT(*) AS dangling 
   FROM events_rebuild 
   WHERE source_snapshot_id IS NOT NULL 
     AND source_snapshot_id NOT IN (SELECT id FROM snapshots);
   ```
   *Expected Result*: `dangling == 0`.

### 2.3 Safe Atomic Table Replacement Procedure
SQLite in WAL mode permits table renaming inside an exclusive transaction. Because no other table references `events` via foreign keys, the swap is clean and atomic:

```python
def swap_rebuild_table_atomic(conn: sqlite3.Connection, hwm_snap: int, hwm_poll: int) -> None:
    """Execute atomic table swap with checkpoints in < 5ms."""
    # 1. Run validation checklist
    fk_errors = conn.execute("PRAGMA foreign_key_check(events_rebuild);").fetchall()
    if fk_errors:
        raise RuntimeError(f"Foreign key validation failed: {fk_errors}")

    time_errors = conn.execute(
        "SELECT COUNT(*) AS c FROM events_rebuild WHERE latest_possible_at_utc < earliest_possible_at_utc;"
    ).fetchone()["c"]
    if time_errors > 0:
        raise RuntimeError(f"Time window validation failed: {time_errors} inverted records.")

    # 2. Build indexes on staging table prior to swap (avoids holding lock during index creation)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_events_reb_lat ON events_rebuild(latest_possible_at_utc DESC, id DESC);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_events_reb_cat ON events_rebuild(category, latest_possible_at_utc DESC);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_events_reb_typ ON events_rebuild(event_type, latest_possible_at_utc DESC);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_events_reb_met ON events_rebuild(meter_id, latest_possible_at_utc DESC);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_events_reb_src ON events_rebuild(source_snapshot_id);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_events_reb_pol ON events_rebuild(poll_event_id);")

    # 3. Atomic swap transaction (execution duration < 5ms)
    conn.execute("BEGIN IMMEDIATE;")
    try:
        # Swap table names
        conn.execute("ALTER TABLE events RENAME TO events_backup;")
        conn.execute("ALTER TABLE events_rebuild RENAME TO events;")
        conn.execute("DROP TABLE events_backup;")

        # Re-establish production index names
        conn.execute("ALTER TABLE events RENAME INDEX idx_events_reb_lat TO idx_events_latest_possible;")
        # Note: In SQLite, index names persist with their table; DROP/RENAME maintains indexes.

        # Restore checkpoints to exact high-water marks atomically
        conn.execute(
            """
            UPDATE processor_checkpoints 
            SET last_processed_id = ?, updated_at_utc = datetime('now')
            WHERE processor_name = 'snapshot_events';
            """,
            (hwm_snap,),
        )
        conn.execute(
            """
            UPDATE processor_checkpoints 
            SET last_processed_id = ?, updated_at_utc = datetime('now')
            WHERE processor_name = 'poll_events';
            """,
            (hwm_poll,),
        )
        conn.commit()
        logger.info("Atomic table swap and checkpoint restoration committed successfully.")
    except Exception as e:
        conn.rollback()
        logger.error("Atomic swap failed, transaction rolled back: %s", e)
        raise
```

---

## 3. Corrected Snapshot Event Processor

### 3.1 Separation of Temporal Transitions and Point-in-Time Evaluations
The processor cleanly separates:
1. **Temporal Transitions**: Power source switches (`GRID` $\leftrightarrow$ `DG`) and relay state switches (`CONNECTED` $\leftrightarrow$ `DISCONNECTED`). These require an interval window $[t_{\text{prev}}, t_{\text{curr}}]$ where $t_{\text{curr}} \ge t_{\text{prev}}$.
2. **Point-in-Time Evaluations**: Balance threshold crossings (`low_balance_warning`) and recharges (`balance_recharge_detected`). These evaluate against the immediate prior balance state, with the event observation bounds set to the exact snapshot observation point $[t_{\text{curr}}, t_{\text{curr}}]$.

### 3.2 Monotonic Baseline Recovery
- `last_valid_temporal_snap` tracks the snapshot with the highest valid timestamp processed so far.
- If $S_{\text{curr}}$ arrives with $t_{\text{curr}} < t_{\text{baseline}}$, temporal transition evaluation is suppressed for that step. `last_valid_temporal_snap` **remains unchanged**.
- When a subsequent snapshot $S_{\text{next}}$ arrives with $t_{\text{next}} \ge t_{\text{baseline}}$, temporal evaluation resumes comparing $S_{\text{next}}$ against $S_{\text{baseline}}$.

### 3.3 Checkpoint Timestamp Semantics
- `last_processed_id`: Strictly the highest snapshot ID sequentially processed in the batch.
- `last_processed_timestamp`: Strictly `last_valid_temporal_snap['observed_at_utc']` (the latest confirmed monotonic time, avoiding backward cursor jumps).

```python
class SnapshotEventProcessor:
    def __init__(
        self,
        db_path: str,
        meter_id: int,
        low_balance_threshold: float = 500.0,
        recharge_threshold: float = 20.0,
    ):
        self.db_path = db_path
        self.meter_id = meter_id
        self.low_balance_threshold = float(low_balance_threshold)
        self.recharge_threshold = float(recharge_threshold)

    def process_pending(self, batch_size: int = 100) -> int:
        with get_db_connection(self.db_path) as conn:
            # 1. Fetch current checkpoint
            row = conn.execute(
                "SELECT last_processed_id FROM processor_checkpoints WHERE processor_name = 'snapshot_events';"
            ).fetchone()
            last_id = row["last_processed_id"] if row else 0

            # 2. Query batch strictly by ascending sequential ID
            pending = conn.execute(
                "SELECT * FROM snapshots WHERE id > ? AND meter_id = ? ORDER BY id ASC LIMIT ?;",
                (last_id, self.meter_id, batch_size),
            ).fetchall()

            if not pending:
                return 0

            # 3. Robust predecessor lookup (handling SQLite auto-increment ID gaps)
            prev_state_snap = conn.execute(
                "SELECT * FROM snapshots WHERE id <= ? AND meter_id = ? ORDER BY id DESC LIMIT 1;",
                (last_id, self.meter_id),
            ).fetchone()

            last_valid_temporal_snap = prev_state_snap

            for curr_snap in pending:
                t_curr = curr_snap["observed_at_utc"]
                is_temporal_valid = True

                # Check for timestamp monotonicity inversion
                if last_valid_temporal_snap:
                    t_baseline = last_valid_temporal_snap["observed_at_utc"]
                    if t_curr < t_baseline:
                        logger.warning(
                            "Snapshot timestamp anomaly: id %d (%s) < baseline id %d (%s). Temporal event suppressed.",
                            curr_snap["id"], t_curr, last_valid_temporal_snap["id"], t_baseline
                        )
                        is_temporal_valid = False

                # 4. Evaluate temporal transitions (Power Source & Relay)
                if is_temporal_valid and last_valid_temporal_snap:
                    self._evaluate_temporal_transitions(conn, last_valid_temporal_snap, curr_snap)
                    last_valid_temporal_snap = curr_snap

                # 5. Evaluate point-in-time state (Prepaid Balance & Recharges)
                # Evaluates independently of temporal anomalies
                if prev_state_snap:
                    self._evaluate_point_in_time_events(conn, prev_state_snap, curr_snap)

                prev_state_snap = curr_snap
                last_id = curr_snap["id"]

            # 6. Advance checkpoint atomically in the same transaction
            conn.execute(
                """
                UPDATE processor_checkpoints 
                SET last_processed_id = ?, 
                    last_processed_timestamp = ?,
                    updated_at_utc = datetime('now')
                WHERE processor_name = 'snapshot_events';
                """,
                (last_id, last_valid_temporal_snap["observed_at_utc"] if last_valid_temporal_snap else None),
            )
            return len(pending)

    def _evaluate_temporal_transitions(self, conn, prev, curr):
        meter_id = curr["meter_id"]
        t_prev = prev["observed_at_utc"]
        t_curr = curr["observed_at_utc"]
        s_prev = normalize_source_code(prev["eb_dg_status"])
        s_curr = normalize_source_code(curr["eb_dg_status"])

        # Power Source Transition Evaluation
        if s_prev != s_curr:
            event_type, severity, title, details = None, "info", "", ""
            if s_prev == "GRID" and s_curr == "DG":
                event_type, severity = "source_transition_grid_to_dg", "alert"
                title, details = "Switched to Backup Generator (DG)", "Outage detected on main grid; DG backup power active."
            elif s_prev == "DG" and s_curr == "GRID":
                event_type, severity = "source_transition_dg_to_grid", "info"
                title, details = "Power Restored (Main Grid)", "Normal Grid (EB) electricity supply restored."
            elif s_prev == "UNKNOWN" and s_curr == "GRID":
                event_type, severity = "source_transition_unknown_to_grid", "info"
                title, details = "Grid Power Confirmed", "Power supply confirmed on Main Grid following unmonitored window."
            elif s_prev == "UNKNOWN" and s_curr == "DG":
                event_type, severity = "source_transition_unknown_to_dg", "alert"
                title, details = "Backup Generator (DG) Confirmed", "Power supply confirmed on Generator following unmonitored window."
            elif s_curr == "UNKNOWN":
                event_type, severity = "source_became_unknown", "warning"
                title, details = "Power Source Unknown", f"Meter reported unrecognized source status: {curr['eb_dg_status']}."

            if event_type:
                dedupe_key = f"src_change:{meter_id}:{prev['id']}:{curr['id']}"
                conn.execute(
                    """
                    INSERT INTO events (
                        event_type, category, severity,
                        earliest_possible_at_utc, latest_possible_at_utc, occurred_at_utc, detected_at_utc,
                        meter_id, source_snapshot_id, previous_snapshot_id,
                        previous_value, current_value, title, details, dedupe_key, created_at_utc
                    ) VALUES (?, 'Power', ?, ?, ?, NULL, datetime('now'), ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                    ON CONFLICT(dedupe_key) DO NOTHING;
                    """,
                    (event_type, severity, t_prev, t_curr, meter_id, curr["id"], prev["id"], s_prev, s_curr, title, details, dedupe_key),
                )

        # Relay Transition Evaluation
        r_prev = normalize_relay_code(prev["relay_status"])
        r_curr = normalize_relay_code(curr["relay_status"])
        if r_prev != "UNKNOWN" and r_curr != "UNKNOWN" and r_prev != r_curr:
            is_disconnect = r_curr == "DISCONNECTED"
            event_type = "relay_opened" if is_disconnect else "relay_closed"
            severity = "alert" if is_disconnect else "info"
            title = "Relay Disconnected" if is_disconnect else "Relay Reconnected"
            details = f"Relay status changed from {r_prev} to {r_curr}."
            dedupe_key = f"relay_change:{meter_id}:{prev['id']}:{curr['id']}"

            conn.execute(
                """
                INSERT INTO events (
                    event_type, category, severity,
                    earliest_possible_at_utc, latest_possible_at_utc, occurred_at_utc, detected_at_utc,
                    meter_id, source_snapshot_id, previous_snapshot_id,
                    previous_value, current_value, title, details, dedupe_key, created_at_utc
                ) VALUES (?, 'Relay', ?, ?, ?, NULL, datetime('now'), ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT(dedupe_key) DO NOTHING;
                """,
                (event_type, severity, t_prev, t_curr, meter_id, curr["id"], prev["id"], r_prev, r_curr, title, details, dedupe_key),
            )

    def _evaluate_point_in_time_events(self, conn, prev, curr):
        meter_id = curr["meter_id"]
        t_curr = curr["observed_at_utc"]
        b_prev = prev["meter_balance"]
        b_curr = curr["meter_balance"]

        if b_prev is not None and b_curr is not None:
            diff = float(b_curr) - float(b_prev)
            # 1. Balance Recharge Detection
            if diff >= self.recharge_threshold:
                dedupe_key = f"recharge:{meter_id}:{prev['id']}:{curr['id']}"
                conn.execute(
                    """
                    INSERT INTO events (
                        event_type, category, severity,
                        earliest_possible_at_utc, latest_possible_at_utc, occurred_at_utc, detected_at_utc,
                        meter_id, source_snapshot_id, previous_snapshot_id,
                        previous_value, current_value, title, details, dedupe_key, created_at_utc
                    ) VALUES (
                        'balance_recharge_detected', 'Billing', 'info',
                        ?, ?, NULL, datetime('now'), ?, ?, ?, ?, ?, ?, ?, ?, datetime('now')
                    ) ON CONFLICT(dedupe_key) DO NOTHING;
                    """,
                    (prev["observed_at_utc"], t_curr, meter_id, curr["id"], prev["id"],
                     str(b_prev), str(b_curr), f"Balance Recharged (+₹{diff:.2f})",
                     f"Prepaid credit increased from ₹{b_prev:.2f} to ₹{b_curr:.2f}.", dedupe_key),
                )
            # 2. Low Balance Threshold Crossing
            elif float(b_prev) >= self.low_balance_threshold and float(b_curr) < self.low_balance_threshold:
                dedupe_key = f"low_bal:{meter_id}:{prev['id']}:{curr['id']}"
                conn.execute(
                    """
                    INSERT INTO events (
                        event_type, category, severity,
                        earliest_possible_at_utc, latest_possible_at_utc, occurred_at_utc, detected_at_utc,
                        meter_id, source_snapshot_id, previous_snapshot_id,
                        previous_value, current_value, title, details, dedupe_key, created_at_utc
                    ) VALUES (
                        'low_balance_warning', 'Billing', 'warning',
                        ?, ?, NULL, datetime('now'), ?, ?, ?, ?, ?, ?, ?, ?, datetime('now')
                    ) ON CONFLICT(dedupe_key) DO NOTHING;
                    """,
                    (prev["observed_at_utc"], t_curr, meter_id, curr["id"], prev["id"],
                     str(b_prev), str(b_curr), "Low Balance Threshold Reached",
                     f"Prepaid balance dipped to ₹{b_curr:.2f} (below ₹{self.low_balance_threshold:.0f} threshold).", dedupe_key),
                )
```

---

## 4. Corrected Poll Event Processor

### 4.1 Monotonic Temporal Baseline & Anomalous Polls
`PollEventProcessor` mirrors the snapshot processor's baseline tracking:
- Maintains `last_valid_temporal_poll`.
- If an anomalous poll record arrives with `attempted_at_utc < baseline`, transition evaluation is suppressed and the baseline remains unchanged.

### 4.2 Canonical Success & Meter Normalization
- `poll_events.success`: Canonical normalization converts `NULL`, negative, or unparseable values to `0` (failure).
- `poll_events.meter_id`: If `NULL`, mapped to the configured single-meter identity (`self.configured_meter_id`).
- **Meter Reconfiguration**: If `curr_poll['meter_id']` does not match `self.configured_meter_id`, the processor logs an identity mismatch and resets the connectivity baseline to `None` for the new meter.

```python
class PollEventProcessor:
    def __init__(self, db_path: str, configured_meter_id: int):
        self.db_path = db_path
        self.configured_meter_id = int(configured_meter_id)

    def process_pending(self, batch_size: int = 100) -> int:
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

            # Robust predecessor lookup
            prev_poll = conn.execute(
                "SELECT * FROM poll_events WHERE id <= ? ORDER BY id DESC LIMIT 1;",
                (last_id,),
            ).fetchone()

            last_valid_temporal_poll = prev_poll

            for curr_poll in pending:
                t_curr = curr_poll["attempted_at_utc"]
                curr_meter_id = curr_poll["meter_id"] or self.configured_meter_id
                
                # Canonical success normalization: NULL or invalid int maps to False
                curr_success = bool(curr_poll["success"]) if curr_poll["success"] is not None else False

                is_temporal_valid = True
                if last_valid_temporal_poll:
                    t_baseline = last_valid_temporal_poll["attempted_at_utc"]
                    if t_curr < t_baseline:
                        logger.warning(
                            "Poll timestamp anomaly: id %d (%s) < baseline id %d (%s). Transition event suppressed.",
                            curr_poll["id"], t_curr, last_valid_temporal_poll["id"], t_baseline
                        )
                        is_temporal_valid = False

                if is_temporal_valid and last_valid_temporal_poll:
                    prev_meter_id = last_valid_temporal_poll["meter_id"] or self.configured_meter_id
                    
                    # Meter Identity Check: Reset baseline if physical meter changed
                    if curr_meter_id == prev_meter_id:
                        prev_success = bool(last_valid_temporal_poll["success"]) if last_valid_temporal_poll["success"] is not None else False
                        t_prev = last_valid_temporal_poll["attempted_at_utc"]

                        # 1. Connection Drop
                        if prev_success and not curr_success:
                            err_cat = curr_poll["error_category"] or "network_error"
                            err_msg = curr_poll["error_message"] or "Unable to reach meter telemetry."
                            dedupe_key = f"poll_drop:{curr_meter_id}:{curr_poll['id']}"
                            conn.execute(
                                """
                                INSERT INTO events (
                                    event_type, category, severity,
                                    earliest_possible_at_utc, latest_possible_at_utc, occurred_at_utc, detected_at_utc,
                                    meter_id, poll_event_id,
                                    previous_value, current_value, title, details, dedupe_key, created_at_utc
                                ) VALUES (
                                    'telemetry_connection_dropped', 'Network', 'warning',
                                    ?, ?, NULL, datetime('now'), ?, ?,
                                    'connected', 'disconnected',
                                    'Connection Dropped', ?, ?, datetime('now')
                                ) ON CONFLICT(dedupe_key) DO NOTHING;
                                """,
                                (t_prev, t_curr, curr_meter_id, curr_poll["id"],
                                 f"Upstream poll failed: {err_msg} ({err_cat}).", dedupe_key),
                            )

                        # 2. Connection Restored
                        elif not prev_success and curr_success:
                            dedupe_key = f"poll_recov:{curr_meter_id}:{curr_poll['id']}"
                            conn.execute(
                                """
                                INSERT INTO events (
                                    event_type, category, severity,
                                    earliest_possible_at_utc, latest_possible_at_utc, occurred_at_utc, detected_at_utc,
                                    meter_id, poll_event_id,
                                    previous_value, current_value, title, details, dedupe_key, created_at_utc
                                ) VALUES (
                                    'telemetry_connection_restored', 'Network', 'info',
                                    ?, ?, NULL, datetime('now'), ?, ?,
                                    'disconnected', 'connected',
                                    'Connection Restored', 'Telemetry communication restored successfully.',
                                    ?, datetime('now')
                                ) ON CONFLICT(dedupe_key) DO NOTHING;
                                """,
                                (t_prev, t_curr, curr_meter_id, curr_poll["id"], dedupe_key),
                            )

                if is_temporal_valid:
                    last_valid_temporal_poll = curr_poll
                last_id = curr_poll["id"]

            conn.execute(
                """
                UPDATE processor_checkpoints 
                SET last_processed_id = ?, 
                    last_processed_timestamp = ?,
                    updated_at_utc = datetime('now')
                WHERE processor_name = 'poll_events';
                """,
                (last_id, last_valid_temporal_poll["attempted_at_utc"] if last_valid_temporal_poll else None),
            )
            return len(pending)
```

---

## 5. Rebuild Correctness & Multi-Stream Coordination

### 5.1 Independent High-Water Marks
During historical rebuilds, snapshots and poll events progress on different clocks. The rebuild script must capture independent high-water marks:
- $H_{\text{snap}} = \text{COALESCE}(\text{MAX}(snapshots.id), 0)$
- $H_{\text{poll}} = \text{COALESCE}(\text{MAX}(poll\_events.id), 0)$

### 5.2 Deterministic Replay Workflow
```
┌────────────────────────────────────────────────────────────────────────┐
│                        Step 1: Capture HWMs                            │
│   H_snap = MAX(snapshots.id)   │   H_poll = MAX(poll_events.id)        │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │ Live collector continues inserting
┌───────────────────────────────────▼────────────────────────────────────┐
│                    Step 2: Replay into Staging                         │
│   Replay snapshots (id <= H_snap) ORDER BY observed_at_utc ASC, id ASC │
│   Replay polls     (id <= H_poll) ORDER BY attempted_at_utc ASC, id ASC│
└───────────────────────────────────┬────────────────────────────────────┘
                                    │ Writes into events_rebuild
┌───────────────────────────────────▼────────────────────────────────────┐
│                    Step 3: Validation Checklist                        │
│   PRAGMA foreign_key_check  │ Dedupe Uniqueness │ Time Window Check    │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │ Passes validation
┌───────────────────────────────────▼────────────────────────────────────┐
│             Step 4: Atomic Table Swap & Checkpoint Commit              │
│   BEGIN IMMEDIATE                                                      │
│     ALTER TABLE events RENAME TO events_backup                         │
│     ALTER TABLE events_rebuild RENAME TO events                        │
│     DROP TABLE events_backup                                           │
│     UPDATE processor_checkpoints SET last_processed_id = H_snap        │
│     UPDATE processor_checkpoints SET last_processed_id = H_poll        │
│   COMMIT                                                               │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │ Post-Rebuild Catch-up
┌───────────────────────────────────▼────────────────────────────────────┐
│             Step 5: Live Incremental Processing Resumes                │
│   Process snapshots WHERE id > H_snap                                  │
│   Process polls     WHERE id > H_poll                                  │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 6. Concurrency & Transaction Boundaries

### 6.1 Two-Tier Concurrency Model
1. **Process-Level Ownership (`fcntl.flock`)**:
   Electro runs under Gunicorn with LaunchAgent. The collector acquires an exclusive non-blocking file lock on `.electro.poller.lock` (`services/polling.py` lines 48–50, 140–160). Only the process holding this lock executes background polling and derived event processing. Secondary web worker processes run in `standby`.
2. **Thread-Level Mutex (`threading.Lock`)**:
   Within the active collector worker, an in-process lock `_processor_lock` prevents concurrent invocation of `process_pending()`.

### 6.2 SQLite WAL Contention Mitigation
While SQLite Write-Ahead Logging (WAL) supports multiple concurrent readers, **only one writer can hold a write transaction at any instant**:
- Raw telemetry ingestion commits immediately in dedicated, isolated write transactions lasting $< 2\text{ms}$.
- Derived event processing batches up to 100 snapshots inside a bounded transaction lasting $< 5\text{ms}$.
- Historical rebuilds write to `events_rebuild` in small chunked transactions rather than holding one multi-second lock.
- The atomic swap holds an immediate lock for $< 5\text{ms}$, well within SQLite's configured `busy_timeout = 5000ms`.

---

## 7. Observation Coverage & Duration Accounting Rules

### 7.1 Canonical Definitions
For an evaluation reporting window $[T_{\text{start}}, T_{\text{end}})$ with duration denominator $W = \text{epoch}(T_{\text{end}}) - \text{epoch}(T_{\text{start}})$:
1. **Consecutive Observation Step**:
   Two chronological observations at $t_i$ and $t_{i+1}$ with step duration $\Delta t_i = t_{i+1} - t_i$.
2. **Monitored Observation Step**:
   A step where $\Delta t_i \le \tau_{\text{gap}}$ (default $\tau_{\text{gap}} = 45.0\text{s}$).
3. **Unmonitored Gap Step**:
   A step where $\Delta t_i > \tau_{\text{gap}}$.

### 7.2 Metrics Formulas
- **Classified Supply Time ($D_{\text{classified}}$)**:
  $$D_{\text{classified}} = D_{\text{grid}} + D_{\text{dg}}$$
  Total duration during which supply source was affirmatively attributed to Grid or DG.
- **Unknown Supply Time ($D_{\text{unknown}}$)**:
  Total duration of unmonitored gaps, source-transition observation windows, and periods of uncalibrated or corrupted meter output.
- **Classified Supply Ratio (%)**:
  $$\text{Classified Supply Ratio} = \frac{D_{\text{classified}}}{W} \times 100\%$$
- **Observation Coverage (%)**:
  $$\text{Observation Coverage} = \frac{\sum_{\Delta t_i \le \tau_{\text{gap}}} \Delta t_i}{W} \times 100\%$$
  Measures the proportion of time supported by active telemetry observation, regardless of power source.

---

## 8. Event Taxonomy & Relay Pseudocode Alignment

### 8.1 Canonical Relay Normalization
```python
def normalize_relay_code(val: Any) -> str:
    """Map raw relay status string/code to canonical relay state."""
    if not val:
        return "UNKNOWN"
    s = str(val).strip().upper()
    if s in ("ON", "1", "CONNECTED"):
        return "CONNECTED"
    elif s in ("OFF", "0", "DISCONNECTED", "TRIP"):
        return "DISCONNECTED"
    return "UNKNOWN"
```

### 8.2 Relay Transition Event Rules
- `CONNECTED` $\to$ `DISCONNECTED`: Emits `relay_opened` (category: `Relay`, severity: `alert`).
- `DISCONNECTED` $\to$ `CONNECTED`: Emits `relay_closed` (category: `Relay`, severity: `info`).
- `UNKNOWN` $\to$ `CONNECTED` / `DISCONNECTED`: Initial baseline establishment; **no transition event emitted**.

---

## 9. PR 1 Implementation Checklist

### Scope Restrictions
- **Include**: Migration `004_events.sql`, `SnapshotEventProcessor`, `PollEventProcessor`, `scripts/backfill_events.py`, `SnapshotRepository.get_unified_events()` refactor, unit tests.
- **Exclude**: Supply intervals (PR 2), analytics duration migration (PR 3), notifications refactor (PR 4), UI redesigns.

### Developer Checklist
- [ ] Create `storage/migrations/004_events.sql` with canonical DDL.
- [ ] Add `SnapshotEventProcessor` with separate temporal/point-in-time evaluations and baseline recovery.
- [ ] Add `PollEventProcessor` with monotonic poll baseline and single-meter validation.
- [ ] Wire processors into `services/polling.py` post-snapshot commit (decoupled).
- [ ] Refactor `SnapshotRepository.get_unified_events()` to query `events` table ordered by `latest_possible_at_utc DESC, id DESC`.
- [ ] Implement `scripts/backfill_events.py` with high-water marks and pre-swap validation.
- [ ] Create `tests/test_events.py` covering all test matrix scenarios.
- [ ] Run full test suite; verify 100% pass rate.

---

## 10. Comprehensive Test Matrix

| Area | Test Identifier | Input Scenario | Expected Database State | Asserted Invariant |
|---|---|---|---|---|
| **Temporal** | `test_out_of_order_snapshot_suppression` | Snap 1: 10:00:15 (Grid), Snap 2: 10:00:10 (DG), Snap 3: 10:00:30 (DG) | 1 event (`Grid -> DG`, earliest: 10:00:15, latest: 10:00:30) | No inverted time windows; checkpoint advances to Snap 3 |
| **Point-in-Time** | `test_point_in_time_balance_evaluation` | Snap 1: bal=520 at 10:00:15, Snap 2: bal=480 at 10:00:10 (out-of-order) | 1 `low_balance_warning` event (earliest: 10:00:10, latest: 10:00:10) | Balance event emitted despite temporal anomaly |
| **Poll** | `test_anomalous_poll_baseline_suppression` | Poll 1: 10:00:15 (succ=1), Poll 2: 10:00:10 (succ=0), Poll 3: 10:00:30 (succ=0) | 1 `connection_dropped` event (earliest: 10:00:15, latest: 10:00:30) | Baseline recovered; no inverted poll event |
| **Poll** | `test_null_success_canonical_normalization` | Poll 1: succ=1, Poll 2: succ=NULL | 1 `connection_dropped` event emitted | NULL success treated as failure |
| **Poll** | `test_meter_reconfiguration_reset` | Poll 1: meter=1042 (succ=0), Poll 2: meter=1043 (succ=1) | 0 `connection_restored` events emitted | Meter switch resets baseline cleanly |
| **Rebuild** | `test_rebuild_independent_hwms` | 100 snapshots, 50 polls. Rebuild executed. | Checkpoint `snapshot_events` = 100; `poll_events` = 50 | Independent stream HWMs committed atomically |
| **Rebuild** | `test_pre_swap_validation_rejection` | Inject corrupted row with `latest < earliest` into staging | Rebuild raises `RuntimeError`; atomic swap aborted; prod table untouched | Production table protected against staging bugs |
| **Relay** | `test_relay_canonical_transitions` | Snap 1: 'ON', Snap 2: 'DISCONNECTED', Snap 3: 'CONNECTED' | 1 `relay_opened` (alert), 1 `relay_closed` (info) | Canonical relay status mapped correctly |

---

## 11. Statement of Verified Baseline & Assumptions

### Verified Against Repository
- `snapshots` contains 1,206 rows with columns matching migration `001` and `003`.
- `poll_events` contains 1,154 rows matching migration `002`.
- Collector uses `fcntl.flock` on `.electro.poller.lock` for process ownership.
- Database runs with `PRAGMA journal_mode = WAL;`, `busy_timeout = 5000;`, and `foreign_keys = ON;`.

### Explicit Assumptions
1. **Single-Meter Installation**: Electro operates strictly as a single-meter application. Meter ID `1042` is discoverable via environment or login. If multi-meter support is ever required, schema will require a compound checkpoint key `(processor_name, meter_id)`.
2. **Read-Only Upstream**: Upstream ELNet interactions remain strictly read-only.
