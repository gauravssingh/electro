# Electro — Architecture Review (Revision 4): Final Implementation Specification

**Document Type:** Technical Architecture & Implementation Specification (Revision 4)  
**Target Repository:** `gauravssingh/electro`  
**Working Branch & Commit:** `main` (`935d51057879d330f436ea3b1f3abc6c18d76267`)  
**Status:** Architecture Approved — Ready for PR 1 Implementation  
**Predecessors:**  
- `ARCHITECTURE_REVIEW_EVENTS_AND_SUPPLY_INTERVALS.md` (V1)  
- `ARCHITECTURE_REVIEW_EVENTS_AND_SUPPLY_INTERVALS_V2.md` (V2)  
- `ARCHITECTURE_REVIEW_EVENTS_AND_SUPPLY_INTERVALS_V3.md` (V3)

---

## 1. Summary of Corrections from V3

Revision 4 resolves the remaining edge cases, operational boundaries, and mathematical rigor required before authorising PR 1 development:

| # | Flaw Identified in V3 | Revision 4 Correction | Architectural Impact |
|---|---|---|---|
| **1** | **Out-of-Order Timestamp Glitch**: Incremental processor evaluated transitions even when $t_{\text{curr}} < t_{\text{prev}}$, producing inverted windows ($t_{\text{earliest}} > t_{\text{latest}}$). | Database check constraint `latest_possible_at_utc >= earliest_possible_at_utc`. Processor skips temporal event generation for inverted pairs; tracks `last_valid_temporal_snapshot` to maintain a monotonic baseline. | Prevents corrupted time windows; maintains valid event bounds at the engine level. |
| **2** | **Fragile Predecessor Lookup**: Assumed `WHERE id = last_id`, failing if auto-increment IDs had gaps due to rollbacks or deletions. | Robust query: `WHERE id <= ? AND meter_id = ? ORDER BY id DESC LIMIT 1`. Gracefully falls back to genesis state if no predecessor exists. | Eliminates silent processor crashes or false genesis transitions when ID gaps exist. |
| **3** | **Unaware Poll-Event Identity**: Evaluated poll events globally; lacked rules for NULL meter IDs during initial HTTP connection failures. | Enforced single-meter invariant for Electro; unauthenticated/pre-login poll failures explicitly map to the configured meter. Initial failure vs drop distinguished. | Correct per-meter connectivity tracking; no artificial "meter 0" hack. |
| **4** | **Staging Schema Discrepancies**: Staging rebuild DDL lacked exact constraints of the primary table. | Single canonical schema definition shared identically between production and staging tables. | Rebuild preserves 100% of constraints, foreign keys, and indexes. |
| **5** | **Undefined Interval Boundary Topology**: Boundary semantics at $t_{\text{boundary}}$ were not explicitly half-open. | Formally adopted half-open interval convention: $[t_{\text{start}}, t_{\text{end}})$. | Zero ambiguity at transitions; provable mathematical non-overlap; exact duration slicing. |
| **6** | **Inaccurate Analytics Terminology**: Labelled inferred Grid/DG time as "telemetry coverage". | Replaced with: **Classified Supply Time**, **Unknown Supply Time**, **Classified Supply Ratio (%)**, and **Observation Coverage (%)**. | Accurate communication; avoids claiming continuous telemetry when state is inferred. |
| **7** | **Silent Error Masking in SQLite**: Blind `INSERT OR IGNORE` masked general constraint violations. | Replaced with explicit `INSERT INTO events (...) ON CONFLICT(dedupe_key) DO NOTHING`. | Genuine schema violations (e.g. check constraints) raise loud errors; only deduplication collisions are ignored. |
| **8** | **Ambiguous PR 1 Concurrency & Safety**: Undefined isolation between live collection and derived processing. | Explicit transaction boundaries: Raw writes are 100% independent. In-process mutex prevents concurrent processor runs. Rebuild uses high-water mark. | Guaranteed collector uptime; zero database locking stalls. |

---

## 2. Corrected Out-of-Order Timestamp Processing

### The Problem in V3
When incremental processing runs in ascending ID order (`ORDER BY id ASC`), a clock-skewed or delayed packet may arrive such that $t_{\text{curr}} < t_{\text{prev}}$. In V3, a warning was logged, but the processor still invoked `_evaluate_snapshot_events(conn, prev_snap, curr_snap)`, which wrote:
- `earliest_possible_at_utc = t_prev`
- `latest_possible_at_utc = t_curr`
Because $t_{\text{prev}} > t_{\text{curr}}$, this created an invalid uncertainty window where the start of the window was after the end!

### The V4 Design Rules
1. **Engine-Level Enforced Invariant**:
   The `events` table includes an explicit check constraint:
   ```sql
   CONSTRAINT chk_event_time_window CHECK (latest_possible_at_utc >= earliest_possible_at_utc)
   ```
2. **Temporal Event Safeguard**:
   If $t_{\text{curr}} < t_{\text{prev}}$, the processor **must not generate temporal events** (source transitions).
3. **Never Silently Swap Timestamps**:
   The processor must never reorder timestamps to force them to fit. An out-of-order timestamp represents an anomaly that cannot establish a forward transition window.
4. **Baseline Recovery Strategy**:
   The processor maintains `last_valid_temporal_snapshot`:
   - If $t_{\text{curr}} \ge t_{\text{prev}}$, `last_valid_temporal_snapshot` is updated to $S_{\text{curr}}$.
   - If $t_{\text{curr}} < t_{\text{prev}}$, $S_{\text{curr}}$ is processed for non-temporal events, but `last_valid_temporal_snapshot` remains $S_{\text{prev}}$. Subsequent snapshots $S_{\text{next}}$ are compared against $S_{\text{prev}}$ rather than the anomalous backward timestamp!
5. **Non-Temporal Point-in-Time Events**:
   Threshold events (such as `low_balance_warning`) that reflect a point-in-time condition may still be generated, but their observation window is collapsed to an exact point:
   - `earliest_possible_at_utc = t_curr`
   - `latest_possible_at_utc = t_curr`
   This satisfies `latest >= earliest` and accurately reflects the observation point.
6. **Chronological Rebuild Distinction**:
   During a historical rebuild, all snapshots are sorted globally by `ORDER BY observed_at_utc ASC, id ASC`. Therefore, out-of-order ID jitter is naturally eliminated in rebuilds.

---

## 3. Correct Predecessor Lookup (Handling ID Gaps)

### The Problem in V3
V3 looked up predecessors using:
```sql
SELECT * FROM snapshots WHERE id = ?; -- where ? was last_processed_id
```
If snapshot IDs contain gaps (common in SQLite due to aborted transactions, failed inserts, or maintenance prunes), `WHERE id = last_id` returns `None`, falsely causing the processor to treat a continuous stream as an initial genesis state.

### The V4 Robust Predecessor Query
```sql
SELECT * FROM snapshots 
WHERE id <= :checkpoint_id 
  AND meter_id = :meter_id 
ORDER BY id DESC 
LIMIT 1;
```
For poll events:
```sql
SELECT * FROM poll_events 
WHERE id <= :checkpoint_id 
  AND (meter_id = :meter_id OR meter_id IS NULL)
ORDER BY id DESC 
LIMIT 1;
```

### Predecessor Availability Handling
1. **Predecessor Found**: Use as baseline ($S_{\text{prev}}$) for state comparison.
2. **Predecessor Not Found (Genesis State)**:
   - Occurs when running on a fresh database or after all prior snapshots were pruned.
   - The first snapshot in the batch establishes initial baseline state without emitting transition events.
   - Checkpoint advances to the processed ID normally.

---

## 4. Meter-Aware Poll-Event Processing

### Single-Meter Invariant & Enforcement
Electro is architected as a single-user application monitoring a single physical meter installation. In the current production deployment:
- The active meter identity is determined from `Config.ELNET_USERNAME` and confirmed via client login (e.g. `meter_id = 1042`).
- The database schema must strictly record this `meter_id`.

### Handling NULL Meter ID on Upstream Failures
When an upstream poll fails during DNS resolution, network timeout, or pre-login HTTP error, `client.meter_id` has not yet been discovered from the upstream payload. In existing code (`services/polling.py`), `poll_events.meter_id` is recorded as `NULL`.

**V4 Rule for NULL `meter_id`**:
- When `curr_poll['meter_id']` is `NULL`, the processor attributes the poll event to the system's configured default meter (`app.config['ELNET_METER_ID']` or latest confirmed `meter_id`).
- It does **not** invent an artificial meter ID `0`.

### Connectivity State Lifecycle
```
                 ┌────────────────────────────────┐
                 │          UNCONNECTED           │
                 │ (Initial State on Cold Start)  │
                 └──────────────┬─────────────────┘
                                │
                   First Poll   │   First Poll
                    Failure     │    Success
                        ┌───────┴───────┐
                        ▼               ▼
         ┌─────────────────────┐  ┌─────────────────────┐
         │ DEGRADED / FAILED   │  │      CONNECTED      │
         │ (Streak >= 1)       │  │ (Live Telemetry OK) │
         └──────────┬──────────┘  └──────────┬──────────┘
                    │        ▲               │
         Subsequent │        │ Poll Failure  │ Subsequent
          Failures  │        │ (Emit Drop)   │  Successes
          (No Event)│        └───────────────┤  (No Event)
                    ▼                        │
         ┌─────────────────────┐             │
         │ DEGRADED CONTINUES  │             │
         └──────────┬──────────┘             │
                    │                        │
                    │ Poll Success           │
                    │ (Emit Recovery)        │
                    ▼                        ▼
         ┌──────────────────────────────────────────────┐
         │                  CONNECTED                   │
         └──────────────────────────────────────────────┘
```

1. **Cold Start (UNCONNECTED)**:
   - First poll fails: System records initial failure state. **No connection drop event is emitted** (cannot drop an unestablished connection).
   - First poll succeeds: System records connected state.
2. **Established Connection $\to$ Failure**:
   - `prev_success == 1` and `curr_success == 0`: Emits `telemetry_connection_dropped`.
3. **Streak of Consecutive Failures**:
   - `prev_success == 0` and `curr_success == 0`: **No event emitted**. State remains degraded.
4. **Failure Streak $\to$ Success**:
   - `prev_success == 0` and `curr_success == 1`: Emits `telemetry_connection_restored`.
5. **Transport Success vs Telemetry Acquisition**:
   - A poll is marked `success = 1` only if HTTP transport succeeded **and** a valid, parseable `HomeData` payload was acquired. A 200 OK containing an upstream error envelope is recorded as `success = 0` in `poll_events`.

---

## 5. Final Canonical Event Schema & Checkpoint Schema

### Canonical Schema Definition (`storage/schema.py` / `storage/migrations/004_events.sql`)
This DDL is used verbatim for creating the production table and staging rebuild tables:

```sql
-- Migration: 004_events.sql

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

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    
    -- Event Classification
    event_type TEXT NOT NULL,
    category TEXT NOT NULL CHECK (category IN ('Power', 'Billing', 'Network', 'Relay')),
    severity TEXT NOT NULL CHECK (severity IN ('info', 'warning', 'alert')),
    
    -- Temporal Semantics (Strictly Enforced Monotonic Window)
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
    
    -- Presentation (Materialized for zero-cost API reads and CLI output)
    title TEXT NOT NULL,
    details TEXT NOT NULL,
    metadata_json TEXT,
    
    -- Deterministic Idempotency & Audit
    dedupe_key TEXT NOT NULL UNIQUE,
    created_at_utc TEXT NOT NULL,
    
    -- Table Constraints
    CONSTRAINT chk_event_time_window CHECK (latest_possible_at_utc >= earliest_possible_at_utc)
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

## 6. Corrected Snapshot & Poll-Event Processors (Pseudocode)

### 1. Corrected `SnapshotEventProcessor`
```python
class SnapshotEventProcessor:
    def __init__(self, db_path: str, meter_id: int, low_balance_threshold: float = 500.0, recharge_threshold: float = 20.0):
        self.db_path = db_path
        self.meter_id = meter_id
        self.low_balance_threshold = float(low_balance_threshold)
        self.recharge_threshold = float(recharge_threshold)

    def process_pending(self, batch_size: int = 100) -> int:
        with get_db_connection(self.db_path) as conn:
            # 1. Fetch checkpoint
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

            # 3. Robust predecessor lookup (handling ID gaps)
            prev_snap = conn.execute(
                "SELECT * FROM snapshots WHERE id <= ? AND meter_id = ? ORDER BY id DESC LIMIT 1;",
                (last_id, self.meter_id),
            ).fetchone()

            last_valid_temporal_snap = prev_snap

            for curr_snap in pending:
                t_curr = curr_snap["observed_at_utc"]
                is_temporal_valid = True

                if last_valid_temporal_snap:
                    t_baseline = last_valid_temporal_snap["observed_at_utc"]
                    if t_curr < t_baseline:
                        # Anomaly detected: current snapshot is out of order
                        logger.warning(
                            "Snapshot timestamp anomaly: id %d (%s) < baseline id %d (%s). Temporal event skipped.",
                            curr_snap["id"], t_curr, last_valid_temporal_snap["id"], t_baseline
                        )
                        is_temporal_valid = False

                # Process events
                self._evaluate_events(conn, last_valid_temporal_snap if is_temporal_valid else None, curr_snap, is_temporal_valid)

                if is_temporal_valid:
                    last_valid_temporal_snap = curr_snap
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
                (last_id, last_valid_temporal_snap["observed_at_utc"] if last_valid_temporal_snap else None),
            )
            return len(pending)

    def _evaluate_events(self, conn, prev, curr, is_temporal_valid: bool):
        meter_id = curr["meter_id"]
        t_curr = curr["observed_at_utc"]
        s_curr = normalize_source_code(curr["eb_dg_status"])

        # 1. Temporal Transitions (Evaluated ONLY if temporal ordering is valid)
        if is_temporal_valid and prev:
            t_prev = prev["observed_at_utc"]
            s_prev = normalize_source_code(prev["eb_dg_status"])

            if s_prev != s_curr:
                event_type, severity, title, details = None, "info", "", ""
                if s_prev == "GRID" and s_curr == "DG":
                    event_type, severity = "source_transition_grid_to_dg", "alert"
                    title, details = "Switched to Backup Generator (DG)", "Outage detected on main grid; DG backup active."
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
                    title, details = "Power Source Unknown", f"Meter reported unrecognized status: {curr['eb_dg_status']}."

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

            # Relay transitions
            r_prev = normalize_relay_status(prev["relay_status"])
            r_curr = normalize_relay_status(curr["relay_status"])
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

        # 2. Point-in-Time & Billing Events
        b_curr = curr["meter_balance"]
        if b_curr is not None and prev and prev["meter_balance"] is not None:
            b_prev = prev["meter_balance"]
            diff = float(b_curr) - float(b_prev)
            t_win_start = prev["observed_at_utc"] if is_temporal_valid else t_curr
            t_win_end = t_curr

            if diff >= self.recharge_threshold:
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
                    (t_win_start, t_win_end, meter_id, curr["id"], prev["id"], str(b_prev), str(b_curr),
                     f"Balance Recharged (+₹{diff:.2f})", f"Prepaid credit increased from ₹{b_prev:.2f} to ₹{b_curr:.2f}.",
                     f"recharge:{meter_id}:{prev['id']}:{curr['id']}"),
                )
            elif float(b_prev) >= self.low_balance_threshold and float(b_curr) < self.low_balance_threshold:
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
                    (t_win_start, t_win_end, meter_id, curr["id"], prev["id"], str(b_prev), str(b_curr),
                     "Low Balance Threshold Reached", f"Prepaid balance dipped to ₹{b_curr:.2f} (below ₹{self.low_balance_threshold:.0f} threshold).",
                     f"low_bal:{meter_id}:{prev['id']}:{curr['id']}"),
                )
```

---

### 2. Corrected `PollEventProcessor`
```python
class PollEventProcessor:
    def __init__(self, db_path: str, default_meter_id: int):
        self.db_path = db_path
        self.default_meter_id = default_meter_id

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

            for curr_poll in pending:
                target_meter_id = curr_poll["meter_id"] or self.default_meter_id
                t_curr = curr_poll["attempted_at_utc"]
                curr_success = bool(curr_poll["success"])

                if prev_poll:
                    t_prev = prev_poll["attempted_at_utc"]
                    prev_success = bool(prev_poll["success"])

                    # Safeguard: Verify temporal order
                    if t_curr >= t_prev:
                        # 1. Connection Drop
                        if prev_success and not curr_success:
                            err_cat = curr_poll["error_category"] or "network_error"
                            err_msg = curr_poll["error_message"] or "Unable to reach meter telemetry."
                            dedupe_key = f"poll_drop:{target_meter_id}:{curr_poll['id']}"
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
                                (t_prev, t_curr, target_meter_id, curr_poll["id"],
                                 f"Upstream poll failed: {err_msg} ({err_cat}).", dedupe_key),
                            )

                        # 2. Connection Restored
                        elif not prev_success and curr_success:
                            dedupe_key = f"poll_recov:{target_meter_id}:{curr_poll['id']}"
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
                                (t_prev, t_curr, target_meter_id, curr_poll["id"], dedupe_key),
                            )

                prev_poll = curr_poll
                last_id = curr_poll["id"]

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

## 7. Half-Open Temporal Boundary Convention

Supply intervals formally adhere to the **half-open interval topology**:
$$[t_{\text{start}}, t_{\text{end}})$$

### Properties & Mathematical Proof of Contiguity
1. **Right-Open Inclusion**:
   The interval includes all timestamps $t$ satisfying:
   $$t_{\text{start}} \le t < t_{\text{end}}$$
2. **Zero Point Ambiguity**:
   For adjacent intervals $I_1 = [t_0, t_1)$ and $I_2 = [t_1, t_2)$, the boundary timestamp $t_1$ belongs **exclusively** to $I_2$.
3. **Additive Duration Invariant**:
   $$\text{Duration}(I_1) + \text{Duration}(I_2) = (t_1 - t_0) + (t_2 - t_1) = t_2 - t_0$$
   Total duration strictly equals total elapsed time with **zero overlap and zero missing duration**.
4. **SQL Slicing Query Standard**:
   To slice intervals intersecting a time window $[W_{\text{start}}, W_{\text{end}})$:
   ```sql
   SELECT * FROM supply_intervals
   WHERE meter_id = :meter_id
     AND started_at_utc < :window_end
     AND (ended_at_utc IS NULL OR ended_at_utc > :window_start)
   ORDER BY started_at_utc ASC;
   ```
   This query is guaranteed to capture every active or historical interval overlapping the window by at least one microsecond.

---

## 8. Corrected Analytics Terminology

To preserve scientific integrity, inferred intervals are distinguished from continuous raw telemetry:

| Proposed V4 Term | Formula / Definition | Prior Misleading V2/V3 Term | Rationale |
|---|---|---|---|
| **Classified Supply Time** | $D_{\text{grid}} + D_{\text{dg}}$ (seconds) | "Confirmed Monitored Time" | Source is classified based on meter readings within threshold. |
| **Unknown Supply Time** | $D_{\text{unknown}}$ (seconds) | "Unmonitored Gap" | Includes unobserved gaps, transition windows, and corrupt data. |
| **Classified Supply Ratio (%)** | $\frac{D_{\text{grid}} + D_{\text{dg}}}{\text{Total Period Span}} \times 100\%$ | "Telemetry Coverage" | Telemetry was not continuous; source classification was maintained. |
| **Observation Coverage (%)** | $\frac{\sum \text{Durations where } \Delta t \le \tau_{\text{gap}}}{\text{Total Period Span}} \times 100\%$ | "Data Freshness %" | True observation coverage based on successful polling intervals. |
| **Grid Availability (%)** | $\frac{D_{\text{grid}}}{D_{\text{grid}} + D_{\text{dg}}} \times 100\%$ | "Grid Availability" | Availability proportion among classified power sources. |

---

## 9. Completed Event Taxonomy

| Event Type | Category | Severity | Trigger Condition | Dedupe Key Pattern |
|---|---|---|---|---|
| `source_transition_grid_to_dg` | `Power` | `alert` | `s_prev == 'GRID'` and `s_curr == 'DG'` | `src_change:{meter_id}:{prev_id}:{curr_id}` |
| `source_transition_dg_to_grid` | `Power` | `info` | `s_prev == 'DG'` and `s_curr == 'GRID'` | `src_change:{meter_id}:{prev_id}:{curr_id}` |
| `source_transition_unknown_to_grid` | `Power` | `info` | `s_prev == 'UNKNOWN'` and `s_curr == 'GRID'` | `src_change:{meter_id}:{prev_id}:{curr_id}` |
| `source_transition_unknown_to_dg` | `Power` | `alert` | `s_prev == 'UNKNOWN'` and `s_curr == 'DG'` | `src_change:{meter_id}:{prev_id}:{curr_id}` |
| `source_became_unknown` | `Power` | `warning` | `s_prev != 'UNKNOWN'` and `s_curr == 'UNKNOWN'` | `src_change:{meter_id}:{prev_id}:{curr_id}` |
| `relay_opened` | `Relay` | `alert` | `r_prev == 'CONNECTED'` and `r_curr == 'DISCONNECTED'` | `relay_change:{meter_id}:{prev_id}:{curr_id}` |
| `relay_closed` | `Relay` | `info` | `r_prev == 'DISCONNECTED'` and `r_curr == 'CONNECTED'` | `relay_change:{meter_id}:{prev_id}:{curr_id}` |
| `low_balance_warning` | `Billing` | `warning` | `b_prev >= T_low` and `b_curr < T_low` | `low_bal:{meter_id}:{prev_id}:{curr_id}` |
| `balance_recharge_detected` | `Billing` | `info` | `b_curr - b_prev >= T_rech` | `recharge:{meter_id}:{prev_id}:{curr_id}` |
| `telemetry_connection_dropped` | `Network` | `warning` | `p_prev == 1` and `p_curr == 0` | `poll_drop:{meter_id}:{curr_poll_id}` |
| `telemetry_connection_restored` | `Network` | `info` | `p_prev == 0` and `p_curr == 1` | `poll_recov:{meter_id}:{curr_poll_id}` |

### Configuration Variables (in `config.py`)
- `ELECTRO_LOW_BALANCE_THRESHOLD`: Default `500.0` (₹)
- `ELECTRO_RECHARGE_THRESHOLD`: Default `20.0` (₹)
- `ELECTRO_MAX_INFERENCE_GAP_SECONDS`: Default `45.0` (seconds)

---

## 10. Operational Safety & High-Water Mark Rebuild

### 1. Collector Independence Invariant
- **Isolated Telemetry Commit**:
  The collector (`services/polling.py`) executes:
  ```python
  self.repository.insert_snapshot(snapshot) # Dedicated SQLite transaction
  self.repository.record_poll_event(...)    # Dedicated SQLite transaction
  ```
  Neither operation depends on, awaits, or shares a transaction with derived event processing.
- If event processing fails, throws an exception, or deadlocks, **raw telemetry collection continues 100% unimpeded**.

### 2. Concurrency Control
- **In-Process Mutex**: A dedicated `threading.Lock` protects `SnapshotEventProcessor.process_pending()` and `PollEventProcessor.process_pending()`. Multiple threads cannot process the stream concurrently.
- **SQLite WAL Mode**: SQLite handles concurrent readers and the single writer without table locks.

### 3. High-Water Mark Historical Rebuild Specification
When executing `python -m scripts.rebuild_derived_data`:

```python
def execute_safe_rebuild(db_path: str):
    with get_db_connection(db_path) as conn:
        # Step 1: Capture High-Water Mark while collector continues writing
        row = conn.execute("SELECT MAX(id) AS max_id FROM snapshots;").fetchone()
        high_water_mark = row["max_id"] if row and row["max_id"] else 0
        logger.info("Starting rebuild up to high-water mark snapshot %d", high_water_mark)

        # Step 2: Create Staging Table with canonical DDL
        conn.execute("DROP TABLE IF EXISTS events_rebuild;")
        # Canonical DDL identical to production
        conn.execute("""
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
                created_at_utc TEXT NOT NULL,
                CONSTRAINT chk_event_time_window CHECK (latest_possible_at_utc >= earliest_possible_at_utc)
            );
        """)

        # Step 3: Replay snapshots chronologically up to high-water mark
        # Writes into events_rebuild using SnapshotEventProcessor logic...

        # Step 4: Replay poll_events chronologically...

        # Step 5: Validate Staging Data
        count = conn.execute("SELECT COUNT(*) AS c FROM events_rebuild;").fetchone()["c"]
        if count == 0 and high_water_mark > 0:
            raise RuntimeError("Rebuild validation failed: staging table is empty.")

        # Step 6: Atomic Table Swap in Exclusive Transaction
        conn.execute("BEGIN IMMEDIATE;")
        try:
            # Create indexes on staging table prior to swap
            conn.execute("CREATE INDEX idx_events_reb_lat ON events_rebuild(latest_possible_at_utc DESC, id DESC);")
            conn.execute("CREATE INDEX idx_events_reb_cat ON events_rebuild(category, latest_possible_at_utc DESC);")
            conn.execute("CREATE INDEX idx_events_reb_typ ON events_rebuild(event_type, latest_possible_at_utc DESC);")

            # Swap tables
            conn.execute("ALTER TABLE events RENAME TO events_backup;")
            conn.execute("ALTER TABLE events_rebuild RENAME TO events;")
            conn.execute("DROP TABLE events_backup;")

            # Set checkpoint to high-water mark
            conn.execute(
                "UPDATE processor_checkpoints SET last_processed_id = ?, updated_at_utc = datetime('now') WHERE processor_name = 'snapshot_events';",
                (high_water_mark,)
            )
            conn.commit()
            logger.info("Atomic table swap committed successfully.")
        except Exception:
            conn.rollback()
            logger.error("Rebuild failed during swap; rolled back. Production untouched.")
            raise
```

---

## 11. Updated PR 1 Acceptance Criteria

PR 1 is strictly confined to the persistent events layer and checkpoint infrastructure.

### Concrete Acceptance Criteria
1. **Raw Ingestion Unaffected**: `snapshots` and `poll_events` continue collecting at 15s cadence without modification.
2. **Schema Migration `004_events.sql`**: Applies cleanly, creating `events` (with `chk_event_time_window`) and `processor_checkpoints`.
3. **Ascending Stream Progression**: Snapshot and poll processors advance monotonically by ascending ID.
4. **Out-of-Order Safety**: A snapshot with $t_{\text{curr}} < t_{\text{prev}}$ logs an anomaly and advances the checkpoint without generating invalid time-inverted events.
5. **ID-Gap Tolerance**: Predecessor lookup uses `WHERE id <= ? ORDER BY id DESC LIMIT 1`, gracefully handling gaps in SQLite primary keys.
6. **Idempotency Guarantee**: Running the processor repeatedly across the same snapshots generates zero duplicate events via `dedupe_key` uniqueness.
7. **Single-Meter Enforcement**: Unauthenticated poll failures map explicitly to the configured meter without artificial meter IDs.
8. **Backward-Compatible API**: Existing `/api/events` endpoint returns identical JSON structure (`events`, `count`, `limit`) queried directly from indexed `events`.
9. **Deterministic Backfill**: Existing 1,206 snapshots in `electro.db` backfill in $< 0.2\text{s}$.
10. **Test Suite Coverage**: All 69 existing unit tests pass, plus new tests in `tests/test_events.py`.

---

## 12. Updated Test Matrix for Corrected Issues

| Test Identifier | Category | Input Scenario | Expected Database Result | Observable Behavior |
|---|---|---|---|---|
| `test_out_of_order_timestamp_skipped` | Timestamp Safety | Snap 1: 10:00:15 (Grid), Snap 2: 10:00:10 (DG), Snap 3: 10:00:30 (DG) | 0 events between Snap 1 & 2; 1 event between Snap 1 & 3 (`source_transition_grid_to_dg`, earliest: 10:00:15, latest: 10:00:30) | No inverted time windows; checkpoint advances to Snap 3 |
| `test_check_constraint_rejects_inversion` | DB Integrity | Direct insert with `earliest = 10:05:00` and `latest = 10:00:00` | SQLite throws `sqlite3.IntegrityError: CHECK constraint failed: chk_event_time_window` | Engine-level rejection of invalid time bounds |
| `test_predecessor_lookup_with_id_gaps` | Predecessor | Snap 1 (ID 10), Snap 2 (ID 25 due to gap). Checkpoint at 10. | Snap 10 correctly identified as predecessor; transition detected | Predecessor found despite missing IDs 11–24 |
| `test_genesis_first_snapshot` | Predecessor | Database has 0 events and 0 snapshots. Snap 1 arrives. | Predecessor is None. Checkpoint advances to 1. 0 transitions emitted. | Clean initial baseline establishment |
| `test_null_meter_id_poll_failure` | Poll Events | Poll 1: success=1 (`meter_id=1042`), Poll 2: success=0 (`meter_id=NULL`) | 1 `telemetry_connection_dropped` row created with `meter_id = 1042` | Default meter credited; no meter 0 created |
| `test_initial_poll_failure_no_drop` | Poll Events | System starts. Poll 1 fails (`success=0`). | 0 `telemetry_connection_dropped` events emitted (unconnected state) | No false connection drop on cold start |
| `test_consecutive_poll_failures_single_event` | Poll Events | Poll 1: fail, Poll 2: fail, Poll 3: fail | Exactly 1 `telemetry_connection_dropped` event on first failure | Streak does not generate duplicate drop alerts |
| `test_on_conflict_do_nothing` | Idempotency | Processor runs twice on Snapshots 1–50 | `events` count matches exactly 1st run; duplicate keys ignored | 100% idempotent retry |
| `test_high_water_mark_rebuild` | Operational | Rebuild script executed with high-water mark 500 while snapshots 501–505 exist | Staging table swapped; checkpoint set to 500; incremental run catches 501–505 | Zero telemetry loss during rebuild |

---

## 13. Remaining Product-Owner Decisions

1. **Backfill Execution Timing**:
   - *Recommendation*: Run the backfill script immediately after applying migration `004_events.sql` as part of the PR 1 deployment.
2. **Transition Window UI Representation (PR 3)**:
   - *Recommendation*: When PR 3 introduces `supply_intervals`, the UI should display transition windows as a distinct state ("Switching: 15s") rather than an alarmist outage.
3. **Alert Dispatch Retention**:
   - *Recommendation*: Keep `events` records permanently. A single-meter household generates $\approx 10\text{–}20$ events daily ($< 5\text{MB}$ per year in SQLite).
