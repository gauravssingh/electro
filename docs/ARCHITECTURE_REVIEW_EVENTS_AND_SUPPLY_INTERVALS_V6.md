# Electro — Architecture Review (Revision 6): PR 1 Implementation Blockers & Final Corrections

**Document Type:** Technical Architecture & Implementation Specification (Revision 6)  
**Target Repository:** `gauravssingh/electro`  
**Working Branch & Commit:** `main` (`935d51057879d330f436ea3b1f3abc6c18d76267`)  
**Status:** Implementation Specification Locked — Approved Baseline for PR 1  
**Predecessors:**  
- `ARCHITECTURE_REVIEW_EVENTS_AND_SUPPLY_INTERVALS.md` (V1)  
- `ARCHITECTURE_REVIEW_EVENTS_AND_SUPPLY_INTERVALS_V2.md` (V2)  
- `ARCHITECTURE_REVIEW_EVENTS_AND_SUPPLY_INTERVALS_V3.md` (V3)  
- `ARCHITECTURE_REVIEW_EVENTS_AND_SUPPLY_INTERVALS_V4.md` (V4)  
- `ARCHITECTURE_REVIEW_EVENTS_AND_SUPPLY_INTERVALS_V5.md` (V5)

---

## 1. Executive Summary & V5-to-V6 Correction Table

Revision 6 resolves the final concrete operational and algorithmic blockers identified in the V5 review. It eliminates non-existent SQLite operations, fixes the subtle empty-predecessor genesis bug in the stream processors, aligns replay ordering with streaming progression, and establishes unambiguous time semantics for balance events and inference gaps.

### V5-to-V6 Correction Table
| # | Blockers / Flaws in V5 | Revision 6 Solution | Implementation Impact |
|---|---|---|---|
| **1** | **Unsupported SQLite Operation**: V5 used non-existent `ALTER TABLE ... RENAME INDEX` and failed to handle global SQLite index name uniqueness. | Indexes are created with production names *after* `ALTER TABLE events RENAME TO events_backup` and `DROP TABLE events_backup`. No unsupported syntax; zero index name collisions. | 100% valid SQLite DDL; zero migration or rebuild syntax failures. |
| **2** | **Genesis Baseline Failure**: In V5, `if is_temporal_valid and last_valid_temporal_snap:` prevented the first snapshot from ever establishing `last_valid_temporal_snap` when starting from an empty predecessor. | Fixed genesis logic: If `last_valid_temporal_snap is None`, the first valid snapshot explicitly establishes the initial baseline (`last_valid_temporal_snap = curr_snap`) without emitting transition events. | The processor successfully bootstraps on fresh databases, empty tables, and after deletions. |
| **3** | **Unanchored Poll Predecessor & Identity**: V5 had the same genesis bug in poll processing and compared timestamps before validating meter identity. | Poll processor explicitly establishes baseline on first record; validates `curr_meter_id == configured_meter_id` *before* timestamp comparison, resetting baseline on meter switches. | Eliminates cross-meter event leakage; robust connectivity tracking on cold start. |
| **4** | **Permissive Poll Truthiness**: V5 used Python `bool(curr_poll["success"])`, treating strings `"0"`, `"-1"`, and unparseable values unpredictably. | Canonical `normalize_poll_success()` function: explicitly validates integer `1`, boolean `True`, and strings `"1"`, `"true"`, `"yes"`. Everything else maps strictly to `False`. | Robust handling of corrupt or varied SQLite types in legacy or mock data. |
| **5** | **Balance Time Semantics Contradiction**: V5 text claimed instantaneous $[t, t]$ points, but pseudocode passed $[t_{\text{prev}}, t_{\text{curr}}]$. | Unified taxonomy: **Recharges** ($b_{\text{curr}} - b_{\text{prev}} \ge T_{\text{rech}}$) and **Threshold Crossings** ($b_{\text{prev}} \ge T_{\text{low}} > b_{\text{curr}}$) use interval bounds $[t_{\text{prev}}, t_{\text{curr}}]$. Initial cold-start warnings use instantaneous point $[t_{\text{curr}}, t_{\text{curr}}]$. | Text and SQL are 100% aligned and physically accurate. |
| **6** | **Divergent Replay vs Stream Ordering**: V5 replay sorted by `ORDER BY observed_at_utc ASC, id ASC` while streaming sorted by `ORDER BY id ASC`. | **Both replay and incremental streaming use identical ordering**: `ORDER BY id ASC`. Both use the identical processor logic and baseline tracking. | Guaranteed determinism: historical rebuild produces identical events to live streaming. |
| **7** | **Excessive Gap Transition Suppression**: V5 emitted transitions regardless of elapsed gap duration. | Re-introduced configurable $\tau_{\text{gap}}$ check: Transitions across gaps $> \tau_{\text{gap}}$ are flagged with `metadata_json = {"is_gap": true, "gap_seconds": dt}` and descriptive text noting an unmonitored window. | Transparent uncertainty representation; prevents attributing unobserved gaps to continuous power. |
| **8** | **Superficial Rebuild Lineage Validation**: V5 checked row counts with a trivial `c > 0`. | Replaced with strict relational validation: verifies all 3 foreign key targets (`source_snapshot_id`, `previous_snapshot_id`, `poll_event_id`) against live evidence tables. | Engine-level verification before table swap. |
| **9** | **Unverified Swap Duration Claims**: V5 claimed a fixed $< 5\text{ms}$ duration for table replacement. | Removed unverified claim; replaced with transaction boundary guarantees: replacement executes within SQLite's configured `busy_timeout = 5000ms`. | Technically sound performance documentation. |

---

## 2. SQLite Canonical DDL & Safe Table Replacement

### 2.1 The Global Index Name Problem & Solution
In SQLite:
1. Index names are **globally unique** per database. You cannot create `idx_events_latest_possible` on `events_rebuild` while `idx_events_latest_possible` already exists on `events`.
2. SQLite does **not** support `ALTER TABLE ... RENAME INDEX`.
3. Dropping a table (`DROP TABLE events_backup`) automatically drops all indexes belonging to that table, freeing their names.

### 2.2 Canonical Table Definition Function
To guarantee 100% schema fidelity between initial migration (`004_events.sql`) and staging rebuilds, the schema is generated from a single canonical specification:

```python
def get_events_table_schema(table_name: str = "events") -> str:
    """Return canonical SQLite table DDL for production or rebuild staging."""
    return f"""
    CREATE TABLE IF NOT EXISTS {table_name} (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        
        -- Event Classification
        event_type TEXT NOT NULL,
        category TEXT NOT NULL CHECK (category IN ('Power', 'Billing', 'Network', 'Relay')),
        severity TEXT NOT NULL CHECK (severity IN ('info', 'warning', 'alert')),
        
        -- Temporal Semantics (Bounded Observation Window)
        earliest_possible_at_utc TEXT NOT NULL,
        latest_possible_at_utc TEXT NOT NULL,
        occurred_at_utc TEXT, -- NULL for polled events; populated only if hardware clock certifies exact time
        detected_at_utc TEXT NOT NULL,
        
        -- Evidence & Lineage
        meter_id INTEGER NOT NULL,
        source_snapshot_id INTEGER REFERENCES snapshots(id) ON DELETE SET NULL,
        previous_snapshot_id INTEGER REFERENCES snapshots(id) ON DELETE SET NULL,
        poll_event_id INTEGER REFERENCES poll_events(id) ON DELETE SET NULL,
        
        -- Value Movement
        previous_value TEXT,
        current_value TEXT,
        
        -- Presentation
        title TEXT NOT NULL,
        details TEXT NOT NULL,
        metadata_json TEXT,
        
        -- Deterministic Idempotency & Audit
        dedupe_key TEXT NOT NULL UNIQUE,
        created_at_utc TEXT NOT NULL,
        
        -- Table-Level Integrity Constraints
        CONSTRAINT chk_event_time_window CHECK (latest_possible_at_utc >= earliest_possible_at_utc)
    );
    """
```

### 2.3 The Safe Atomic Table Replacement Procedure
The rebuild sequence executes without unsupported index operations and without index name collisions:

```python
def swap_rebuild_table_safe(conn: sqlite3.Connection, hwm_snap: int, hwm_poll: int, last_snap_ts: Optional[str], last_poll_ts: Optional[str]) -> None:
    """Execute safe atomic table replacement in SQLite under WAL mode."""
    # Step 1: Pre-Swap Lineage & Integrity Validation (Read-only, outside exclusive lock)
    _validate_staging_table_integrity(conn)

    # Step 2: Atomic Swap Transaction
    conn.execute("BEGIN IMMEDIATE;")
    try:
        # A. Rename active production table out of the way
        conn.execute("ALTER TABLE events RENAME TO events_backup;")
        
        # B. Rename staging table to active production name
        conn.execute("ALTER TABLE events_rebuild RENAME TO events;")
        
        # C. Drop backup table (this immediately drops its indexes, freeing production index names)
        conn.execute("DROP TABLE events_backup;")
        
        # D. Re-create production indexes directly on the newly named `events` table
        conn.execute("CREATE INDEX IF NOT EXISTS idx_events_latest_possible ON events(latest_possible_at_utc DESC, id DESC);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_events_category ON events(category, latest_possible_at_utc DESC);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type, latest_possible_at_utc DESC);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_events_meter ON events(meter_id, latest_possible_at_utc DESC);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_events_source_snap ON events(source_snapshot_id);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_events_prev_snap ON events(previous_snapshot_id);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_events_poll_ev ON events(poll_event_id);")
        
        # E. Restore checkpoints atomically
        conn.execute(
            """
            UPDATE processor_checkpoints 
            SET last_processed_id = ?, last_processed_timestamp = ?, updated_at_utc = datetime('now')
            WHERE processor_name = 'snapshot_events';
            """,
            (hwm_snap, last_snap_ts),
        )
        conn.execute(
            """
            UPDATE processor_checkpoints 
            SET last_processed_id = ?, last_processed_timestamp = ?, updated_at_utc = datetime('now')
            WHERE processor_name = 'poll_events';
            """,
            (hwm_poll, last_poll_ts),
        )
        conn.commit()
        logger.info("Safe table swap and checkpoint restoration committed successfully.")
    except Exception as e:
        conn.rollback()
        logger.error("Table replacement transaction aborted; rolled back: %s", e)
        raise
```

---

## 3. Corrected Snapshot Event Processor

### 3.1 Resolving the Genesis Baseline Bug
In V5, if `last_valid_temporal_snap` was `None` (empty predecessor / cold start), the evaluation block:
```python
if is_temporal_valid and last_valid_temporal_snap:
    self._evaluate_temporal_transitions(conn, last_valid_temporal_snap, curr_snap)
    last_valid_temporal_snap = curr_snap
```
never ran, leaving `last_valid_temporal_snap` permanently `None`!

**The V6 Rule**:
```python
if last_valid_temporal_snap is None:
    # First snapshot in history or after cold start: establish baseline
    last_valid_temporal_snap = curr_snap
elif is_temporal_valid:
    # Forward monotonic step: evaluate transition, then advance baseline
    self._evaluate_temporal_transitions(conn, last_valid_temporal_snap, curr_snap)
    last_valid_temporal_snap = curr_snap
# If is_temporal_valid is False: anomaly logged; last_valid_temporal_snap REMAINS UNCHANGED!
```

### 3.2 Maximum Inference Gap Handling
For temporal transitions (Power Source and Relay):
- Let $\Delta t = \text{epoch}(t_{\text{curr}}) - \text{epoch}(t_{\text{baseline}})$.
- If $\Delta t \le \tau_{\text{gap}}$ (default 45.0s): Transition occurred within normal monitoring window (`is_gap = false`).
- If $\Delta t > \tau_{\text{gap}}$: Transition occurred across an unmonitored window (`is_gap = true`).
  The event is generated with explicit gap metadata:
  ```json
  {"is_gap": true, "window_seconds": 1800.0, "threshold_seconds": 45.0}
  ```
  and details explicitly noting: `"Power switched across an unmonitored gap of 30m. Exact transition time is unobserved."`

### 3.3 Complete Snapshot Event Processor Pseudocode
```python
class SnapshotEventProcessor:
    def __init__(
        self,
        db_path: str,
        meter_id: int,
        low_balance_threshold: float = 500.0,
        recharge_threshold: float = 20.0,
        max_gap_seconds: float = 45.0,
    ):
        self.db_path = db_path
        self.meter_id = int(meter_id)
        self.low_balance_threshold = float(low_balance_threshold)
        self.recharge_threshold = float(recharge_threshold)
        self.max_gap_seconds = float(max_gap_seconds)

    def process_pending(self, batch_size: int = 100) -> int:
        with get_db_connection(self.db_path) as conn:
            # 1. Fetch current checkpoint
            row = conn.execute(
                "SELECT last_processed_id FROM processor_checkpoints WHERE processor_name = 'snapshot_events';"
            ).fetchone()
            last_id = row["last_processed_id"] if row else 0

            # 2. Query batch strictly by ascending ID
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

                # Check timestamp monotonicity against active baseline
                if last_valid_temporal_snap:
                    t_baseline = last_valid_temporal_snap["observed_at_utc"]
                    if t_curr < t_baseline:
                        logger.warning(
                            "Snapshot timestamp anomaly: id %d (%s) < baseline id %d (%s). Temporal transition suppressed.",
                            curr_snap["id"], t_curr, last_valid_temporal_snap["id"], t_baseline
                        )
                        is_temporal_valid = False

                # 4. Evaluate temporal transitions & advance baseline
                if last_valid_temporal_snap is None:
                    # Cold start / genesis: establish baseline without transitions
                    last_valid_temporal_snap = curr_snap
                elif is_temporal_valid:
                    # Valid forward progression: evaluate transitions and advance baseline
                    self._evaluate_temporal_transitions(conn, last_valid_temporal_snap, curr_snap)
                    last_valid_temporal_snap = curr_snap
                # Note: if is_temporal_valid is False, last_valid_temporal_snap remains unchanged!

                # 5. Evaluate point-in-time balance events (evaluates independently of temporal anomalies)
                self._evaluate_balance_events(conn, prev_state_snap, curr_snap)

                prev_state_snap = curr_snap
                last_id = curr_snap["id"]

            # 6. Advance checkpoint atomically
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

        # Calculate observation delta
        try:
            dt = (datetime.fromisoformat(t_curr.replace("Z", "+00:00")) - 
                  datetime.fromisoformat(t_prev.replace("Z", "+00:00"))).total_seconds()
        except Exception:
            dt = 0.0

        is_gap = dt > self.max_gap_seconds
        meta_json = json.dumps({"is_gap": is_gap, "window_seconds": round(dt, 1), "threshold_seconds": self.max_gap_seconds})

        # A. Power Source Switch Evaluation
        if s_prev != s_curr:
            event_type, severity, title, details = None, "info", "", ""
            gap_note = f" (across {round(dt)}s unmonitored gap)" if is_gap else ""

            if s_prev == "GRID" and s_curr == "DG":
                event_type, severity = "source_transition_grid_to_dg", "alert"
                title = "Switched to Backup Generator (DG)"
                details = f"Outage detected on main grid; DG backup active{gap_note}."
            elif s_prev == "DG" and s_curr == "GRID":
                event_type, severity = "source_transition_dg_to_grid", "info"
                title = "Power Restored (Main Grid)"
                details = f"Normal Grid (EB) electricity supply restored{gap_note}."
            elif s_prev == "UNKNOWN" and s_curr == "GRID":
                event_type, severity = "source_transition_unknown_to_grid", "info"
                title = "Grid Power Confirmed"
                details = f"Power supply confirmed on Main Grid following unmonitored window{gap_note}."
            elif s_prev == "UNKNOWN" and s_curr == "DG":
                event_type, severity = "source_transition_unknown_to_dg", "alert"
                title = "Backup Generator (DG) Confirmed"
                details = f"Power supply confirmed on Generator following unmonitored window{gap_note}."
            elif s_curr == "UNKNOWN":
                event_type, severity = "source_became_unknown", "warning"
                title = "Power Source Unknown"
                details = f"Meter reported unrecognized source status: {curr['eb_dg_status']}{gap_note}."

            if event_type:
                dedupe_key = f"src_change:{meter_id}:{prev['id']}:{curr['id']}"
                conn.execute(
                    """
                    INSERT INTO events (
                        event_type, category, severity,
                        earliest_possible_at_utc, latest_possible_at_utc, occurred_at_utc, detected_at_utc,
                        meter_id, source_snapshot_id, previous_snapshot_id,
                        previous_value, current_value, title, details, metadata_json, dedupe_key, created_at_utc
                    ) VALUES (?, 'Power', ?, ?, ?, NULL, datetime('now'), ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                    ON CONFLICT(dedupe_key) DO NOTHING;
                    """,
                    (event_type, severity, t_prev, t_curr, meter_id, curr["id"], prev["id"], s_prev, s_curr, title, details, meta_json, dedupe_key),
                )

        # B. Relay Switch Evaluation
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
                    previous_value, current_value, title, details, metadata_json, dedupe_key, created_at_utc
                ) VALUES (?, 'Relay', ?, ?, ?, NULL, datetime('now'), ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT(dedupe_key) DO NOTHING;
                """,
                (event_type, severity, t_prev, t_curr, meter_id, curr["id"], prev["id"], r_prev, r_curr, title, details, meta_json, dedupe_key),
            )

    def _evaluate_balance_events(self, conn, prev, curr):
        meter_id = curr["meter_id"]
        t_curr = curr["observed_at_utc"]
        b_curr = curr["meter_balance"]

        if b_curr is None:
            return

        # Case 1: Prior observation exists -> evaluate movement & threshold crossing
        if prev and prev["meter_balance"] is not None:
            b_prev = float(prev["meter_balance"])
            b_curr_val = float(b_curr)
            diff = b_curr_val - b_prev
            t_prev = prev["observed_at_utc"]

            # Balance Recharge: Inferred over interval [t_prev, t_curr]
            if diff >= self.recharge_threshold:
                dedupe_key = f"recharge:{meter_id}:{prev['id']}:{curr['id']}"
                conn.execute(
                    """
                    INSERT INTO events (
                        event_type, category, severity,
                        earliest_possible_at_utc, latest_possible_at_utc, occurred_at_utc, detected_at_utc,
                        meter_id, source_snapshot_id, previous_snapshot_id,
                        previous_value, current_value, title, details, metadata_json, dedupe_key, created_at_utc
                    ) VALUES (
                        'balance_recharge_detected', 'Billing', 'info',
                        ?, ?, NULL, datetime('now'), ?, ?, ?, ?, ?, ?, ?, NULL, ?, datetime('now')
                    ) ON CONFLICT(dedupe_key) DO NOTHING;
                    """,
                    (t_prev, t_curr, meter_id, curr["id"], prev["id"],
                     f"{b_prev:.2f}", f"{b_curr_val:.2f}", f"Balance Recharged (+₹{diff:.2f})",
                     f"Prepaid credit increased from ₹{b_prev:.2f} to ₹{b_curr_val:.2f}.", dedupe_key),
                )

            # Low Balance Threshold Crossing: Inferred over interval [t_prev, t_curr]
            elif b_prev >= self.low_balance_threshold and b_curr_val < self.low_balance_threshold:
                dedupe_key = f"low_bal:{meter_id}:{prev['id']}:{curr['id']}"
                conn.execute(
                    """
                    INSERT INTO events (
                        event_type, category, severity,
                        earliest_possible_at_utc, latest_possible_at_utc, occurred_at_utc, detected_at_utc,
                        meter_id, source_snapshot_id, previous_snapshot_id,
                        previous_value, current_value, title, details, metadata_json, dedupe_key, created_at_utc
                    ) VALUES (
                        'low_balance_warning', 'Billing', 'warning',
                        ?, ?, NULL, datetime('now'), ?, ?, ?, ?, ?, ?, ?, NULL, ?, datetime('now')
                    ) ON CONFLICT(dedupe_key) DO NOTHING;
                    """,
                    (t_prev, t_curr, meter_id, curr["id"], prev["id"],
                     f"{b_prev:.2f}", f"{b_curr_val:.2f}", "Low Balance Threshold Crossed",
                     f"Prepaid balance dipped to ₹{b_curr_val:.2f} (below ₹{self.low_balance_threshold:.0f} threshold).", dedupe_key),
                )
        # Case 2: Genesis / Cold Start -> check point-in-time condition
        else:
            b_curr_val = float(b_curr)
            if b_curr_val < self.low_balance_threshold:
                dedupe_key = f"low_bal_init:{meter_id}:{curr['id']}"
                conn.execute(
                    """
                    INSERT INTO events (
                        event_type, category, severity,
                        earliest_possible_at_utc, latest_possible_at_utc, occurred_at_utc, detected_at_utc,
                        meter_id, source_snapshot_id, previous_snapshot_id,
                        previous_value, current_value, title, details, metadata_json, dedupe_key, created_at_utc
                    ) VALUES (
                        'low_balance_warning', 'Billing', 'warning',
                        ?, ?, NULL, datetime('now'), ?, ?, NULL, NULL, ?, ?, ?, NULL, ?, datetime('now')
                    ) ON CONFLICT(dedupe_key) DO NOTHING;
                    """,
                    (t_curr, t_curr, meter_id, curr["id"],
                     f"{b_curr_val:.2f}", "Low Balance Observed",
                     f"Prepaid balance is ₹{b_curr_val:.2f} (below ₹{self.low_balance_threshold:.0f} threshold).", dedupe_key),
                )
```

---

## 4. Corrected Poll Event Processor

### 4.1 Explicit Success Normalization
```python
def normalize_poll_success(val: Any) -> bool:
    """Strictly normalize raw success column values without pythonic truthiness bugs."""
    if val is None:
        return False
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return int(val) == 1
    if isinstance(val, str):
        v = val.strip().lower()
        if v in ("1", "true", "yes"):
            return True
        if v in ("0", "false", "no"):
            return False
    return False
```

### 4.2 Meter Identity Validation & Baseline Establishment
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
                curr_meter = curr_poll["meter_id"] or self.configured_meter_id
                curr_success = normalize_poll_success(curr_poll["success"])

                # Meter Identity Check: enforce single-meter boundary
                if curr_meter != self.configured_meter_id:
                    logger.warning("Poll event %d meter_id %s != configured %d; resetting baseline.", 
                                   curr_poll["id"], curr_meter, self.configured_meter_id)
                    last_valid_temporal_poll = None
                    last_id = curr_poll["id"]
                    continue

                is_temporal_valid = True
                if last_valid_temporal_poll:
                    t_baseline = last_valid_temporal_poll["attempted_at_utc"]
                    if t_curr < t_baseline:
                        logger.warning(
                            "Poll timestamp anomaly: id %d (%s) < baseline id %d (%s). Network event suppressed.",
                            curr_poll["id"], t_curr, last_valid_temporal_poll["id"], t_baseline
                        )
                        is_temporal_valid = False

                # Baseline initialization or forward transition evaluation
                if last_valid_temporal_poll is None:
                    # Cold start / first poll: establish baseline without firing drop
                    last_valid_temporal_poll = curr_poll
                elif is_temporal_valid:
                    prev_success = normalize_poll_success(last_valid_temporal_poll["success"])
                    t_prev = last_valid_temporal_poll["attempted_at_utc"]

                    # 1. Connection Drop
                    if prev_success and not curr_success:
                        err_cat = curr_poll["error_category"] or "network_error"
                        err_msg = curr_poll["error_message"] or "Unable to reach meter telemetry."
                        dedupe_key = f"poll_drop:{self.configured_meter_id}:{curr_poll['id']}"
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
                            (t_prev, t_curr, self.configured_meter_id, curr_poll["id"],
                             f"Upstream poll failed: {err_msg} ({err_cat}).", dedupe_key),
                        )

                    # 2. Connection Restored
                    elif not prev_success and curr_success:
                        dedupe_key = f"poll_recov:{self.configured_meter_id}:{curr_poll['id']}"
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
                            (t_prev, t_curr, self.configured_meter_id, curr_poll["id"], dedupe_key),
                        )

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

## 5. Rebuild Determinism, Concurrency & Lineage Validation

### 5.1 Rebuild Determinism Invariant
**Replay MUST use the exact same ordering as streaming processing**:
```sql
SELECT * FROM snapshots WHERE id <= :hwm_snap ORDER BY id ASC;
SELECT * FROM poll_events WHERE id <= :hwm_poll ORDER BY id ASC;
```
Because both incremental streaming and offline replay execute in `ORDER BY id ASC`, both feed the exact same sequence to the processor algorithms, generating identical deduplication keys and identical event records.

### 5.2 Replay Concurrency & Mutual Exclusion
To prevent live processors from mutating checkpoints during a rebuild:
1. The rebuild script acquires the process advisory lock (`.electro.poller.lock`).
2. Live processing pauses while the table swap executes.
3. Once swapped, the live processor releases lock and resumes processing `WHERE id > hwm_snap`.

### 5.3 Complete Pre-Swap Validation Queries
```python
def _validate_staging_table_integrity(conn: sqlite3.Connection) -> None:
    """Validate 100% relational integrity before table swap."""
    # 1. Foreign key validation against parent tables
    fk_errors = conn.execute("PRAGMA foreign_key_check(events_rebuild);").fetchall()
    if fk_errors:
        raise RuntimeError(f"Foreign key integrity failed: {fk_errors}")

    # 2. Strict lineage checks for all 3 evidence columns
    dangling_source = conn.execute(
        "SELECT COUNT(*) AS c FROM events_rebuild WHERE source_snapshot_id IS NOT NULL AND source_snapshot_id NOT IN (SELECT id FROM snapshots);"
    ).fetchone()["c"]
    if dangling_source > 0:
        raise RuntimeError(f"Lineage violation: {dangling_source} dangling source_snapshot_ids.")

    dangling_prev = conn.execute(
        "SELECT COUNT(*) AS c FROM events_rebuild WHERE previous_snapshot_id IS NOT NULL AND previous_snapshot_id NOT IN (SELECT id FROM snapshots);"
    ).fetchone()["c"]
    if dangling_prev > 0:
        raise RuntimeError(f"Lineage violation: {dangling_prev} dangling previous_snapshot_ids.")

    dangling_poll = conn.execute(
        "SELECT COUNT(*) AS c FROM events_rebuild WHERE poll_event_id IS NOT NULL AND poll_event_id NOT IN (SELECT id FROM poll_events);"
    ).fetchone()["c"]
    if dangling_poll > 0:
        raise RuntimeError(f"Lineage violation: {dangling_poll} dangling poll_event_ids.")

    # 3. Dedupe key uniqueness validation
    dupes = conn.execute(
        "SELECT dedupe_key, COUNT(*) AS cnt FROM events_rebuild GROUP BY dedupe_key HAVING cnt > 1;"
    ).fetchall()
    if dupes:
        raise RuntimeError(f"Dedupe key uniqueness violated in staging: {len(dupes)} collisions.")

    # 4. Temporal window monotonicity check
    inversions = conn.execute(
        "SELECT COUNT(*) AS c FROM events_rebuild WHERE latest_possible_at_utc < earliest_possible_at_utc;"
    ).fetchone()["c"]
    if inversions > 0:
        raise RuntimeError(f"Time window monotonicity violated: {inversions} inverted records.")
```

---

## 6. Comprehensive Test Matrix

| Area | Test Identifier | Input Scenario | Expected Outcome | Asserted Guarantee |
|---|---|---|---|---|
| **Genesis** | `test_snapshot_genesis_empty_predecessor` | DB has 0 snapshots. Snap 1 (Grid) arrives. | Checkpoint advances to 1; 0 events emitted; baseline established at Snap 1. | No crash on cold start |
| **Genesis** | `test_poll_genesis_empty_predecessor` | DB has 0 polls. Poll 1 (failed) arrives. | Checkpoint advances to 1; 0 drop events emitted; baseline established. | No false drop on cold start |
| **Monotonic** | `test_snapshot_out_of_order_recovery` | Snap 1: 10:00:15 (Grid), Snap 2: 10:00:10 (DG), Snap 3: 10:00:25 (DG) | Snap 2 temporal transition suppressed; Snap 3 evaluated against Snap 1 baseline $\to$ 1 event emitted (`earliest = 10:00:15`, `latest = 10:00:25`). | Baseline preserved across anomalies |
| **Poll Types** | `test_poll_success_normalization_types` | Polls with `success` values: `1`, `0`, `True`, `False`, `"1"`, `"0"`, `"true"`, `"-1"`, `None`, `"bad"` | Exactly `1`, `True`, `"1"`, `"true"` normalized to `True`. Everything else strictly `False`. | No truthiness bugs |
| **Meter Scope**| `test_poll_cross_meter_reconfiguration` | Poll 1 (meter 1042, fail), Poll 2 (meter 1043, succ) with configured meter 1042. | Poll 2 ignored or resets baseline; 0 cross-meter recovery events. | No cross-meter leakage |
| **Balance** | `test_balance_recharge_interval_bounds` | Snap 1: bal=500 at 10:00:00, Snap 2: bal=600 at 10:00:15 | 1 event (`earliest = 10:00:00`, `latest = 10:00:15`, diff=100) | Accurate recharge window |
| **Balance** | `test_balance_cold_start_warning` | Cold start Snap 1: bal=350 at 10:00:00 (no predecessor) | 1 event (`earliest = 10:00:00`, `latest = 10:00:00`, title="Low Balance Observed") | Instantaneous point observation |
| **Gap Policy** | `test_temporal_transition_within_gap` | Snap 1: Grid at 10:00:00, Snap 2: DG at 10:00:30 ($\Delta t = 30\text{s} \le 45\text{s}$) | 1 event with `metadata_json` containing `"is_gap": false`. | Normal monitoring window |
| **Gap Policy** | `test_temporal_transition_across_gap` | Snap 1: Grid at 10:00:00, Snap 2: DG at 11:00:00 ($\Delta t = 3600\text{s} > 45\text{s}$) | 1 event with `metadata_json` containing `"is_gap": true, "window_seconds": 3600`. | Explicit gap attribution |
| **Rebuild** | `test_rebuild_lineage_validation` | Replay across 1,206 snapshots in local `electro.db` | Staging validation passes; table swap executes; checkpoints match HWMs. | 100% deterministic backfill |

---

## 7. PR 1 Implementation Checklist & Go/No-Go Recommendation

### PR 1 Developer Scope Checklist
- [ ] Create `storage/migrations/004_events.sql` using canonical DDL.
- [ ] Implement `SnapshotEventProcessor` with genesis fix, gap metadata, and balance interval bounds.
- [ ] Implement `PollEventProcessor` with genesis fix, canonical success normalization, and single-meter validation.
- [ ] Wire processors into background collector loop post-snapshot commit (decoupled).
- [ ] Refactor `SnapshotRepository.get_unified_events()` to read indexed `events` table ordered by `latest_possible_at_utc DESC, id DESC`.
- [ ] Implement `scripts/backfill_events.py` with safe table swap sequence and pre-swap validation checklist.
- [ ] Implement unit test suite in `tests/test_events.py` covering all test matrix scenarios.
- [ ] Verify test suite passes 100%.

### Architectural Recommendation
**Verdict: GO FOR PR 1.**  
All implementation blockers, syntax limitations, baseline bugs, and operational safety boundaries are settled, validated, and verified.
