"""State machine processor for supply intervals and operational events.

Translates sequential meter telemetry snapshots and polling events into:
1. First-class supply intervals (continuous power delivery blocks).
2. Discrete operational milestone events (power switches, relay trips, recharges, network outages).
"""

import json
import sqlite3
from typing import Optional, Dict, Any, List
from datetime import datetime, timezone


def map_source_code(eb_dg_status: Optional[int]) -> str:
    """Map raw eb_dg_status integer to canonical source string."""
    if eb_dg_status == 0:
        return "GRID"
    elif eb_dg_status == 1:
        return "DG"
    return "UNKNOWN"


def parse_iso_utc(ts_str: Optional[str]) -> Optional[datetime]:
    """Parse ISO UTC timestamp into datetime object."""
    if not ts_str:
        return None
    try:
        clean = ts_str.replace("Z", "+00:00")
        return datetime.fromisoformat(clean).astimezone(timezone.utc)
    except Exception:
        return None


def process_snapshot_transition(
    conn: sqlite3.Connection,
    snapshot: Dict[str, Any],
    prev_snapshot: Optional[Dict[str, Any]] = None,
    poll_interval_seconds: int = 60,
    low_balance_threshold: float = 500.0,
) -> None:
    """Process a newly persisted snapshot to advance supply intervals and record events.

    Executes within an existing SQLite transaction.
    """
    meter_id = snapshot.get("meter_id")
    if meter_id is None:
        return

    t_curr_str = snapshot.get("observed_at_utc")
    t_curr = parse_iso_utc(t_curr_str)
    if not t_curr:
        return

    curr_source = map_source_code(snapshot.get("eb_dg_status"))
    snap_id = snapshot.get("id")
    curr_kwh = snapshot.get("kwh_rec_today")
    curr_balance = snapshot.get("meter_balance")
    curr_relay = (snapshot.get("relay_status") or "").strip().upper()
    max_gap_seconds = max(poll_interval_seconds * 3.0, 180.0)

    # 1. Fetch currently active interval for this meter
    active_row = conn.execute(
        "SELECT * FROM supply_intervals WHERE meter_id = ? AND is_active = 1 LIMIT 1;",
        (meter_id,),
    ).fetchone()

    if not active_row:
        # Genesis bootstrap: Open the initial active interval
        conn.execute(
            """
            INSERT INTO supply_intervals (
                meter_id, source, started_at_utc, ended_at_utc, duration_seconds,
                start_kwh, end_kwh, kwh_consumed, start_snapshot_id, end_snapshot_id, is_active
            ) VALUES (?, ?, ?, ?, 0.0, ?, ?, 0.0, ?, ?, 1);
            """,
            (meter_id, curr_source, t_curr_str, t_curr_str, curr_kwh, curr_kwh, snap_id, snap_id),
        )
    else:
        t_active_end = parse_iso_utc(active_row["ended_at_utc"])
        t_active_start = parse_iso_utc(active_row["started_at_utc"])
        dt = (t_curr - t_active_end).total_seconds() if t_active_end else 0.0

        # Case A: Same source and within acceptable observation window
        if active_row["source"] == curr_source and 0 <= dt <= max_gap_seconds:
            dur = (t_curr - t_active_start).total_seconds() if t_active_start else 0.0
            start_kwh = active_row["start_kwh"] if active_row["start_kwh"] is not None else curr_kwh
            kwh_consumed = max(0.0, (curr_kwh - start_kwh)) if (curr_kwh is not None and start_kwh is not None) else 0.0

            conn.execute(
                """
                UPDATE supply_intervals
                SET ended_at_utc = ?, duration_seconds = ?, end_kwh = ?, kwh_consumed = ?, end_snapshot_id = ?
                WHERE id = ?;
                """,
                (t_curr_str, dur, curr_kwh, kwh_consumed, snap_id, active_row["id"]),
            )

        # Case B: Source transition OR unmonitored gap detected
        else:
            # Close currently active interval
            conn.execute(
                "UPDATE supply_intervals SET is_active = 0 WHERE id = ?;",
                (active_row["id"],),
            )

            # If an unmonitored gap occurred, record explicit GAP interval
            if dt > max_gap_seconds:
                gap_start_str = active_row["ended_at_utc"]
                conn.execute(
                    """
                    INSERT INTO supply_intervals (
                        meter_id, source, started_at_utc, ended_at_utc, duration_seconds,
                        start_kwh, end_kwh, kwh_consumed, start_snapshot_id, end_snapshot_id, is_active
                    ) VALUES (?, 'GAP', ?, ?, ?, NULL, NULL, 0.0, ?, ?, 0);
                    """,
                    (meter_id, gap_start_str, t_curr_str, dt, active_row["end_snapshot_id"], snap_id),
                )

            # Open new active interval for current source
            conn.execute(
                """
                INSERT INTO supply_intervals (
                    meter_id, source, started_at_utc, ended_at_utc, duration_seconds,
                    start_kwh, end_kwh, kwh_consumed, start_snapshot_id, end_snapshot_id, is_active
                ) VALUES (?, ?, ?, ?, 0.0, ?, ?, 0.0, ?, ?, 1);
                """,
                (meter_id, curr_source, t_curr_str, t_curr_str, curr_kwh, curr_kwh, snap_id, snap_id),
            )

            # Emit power source transition event if source actually changed
            if active_row["source"] != curr_source:
                prev_src = active_row["source"]
                if curr_source == "DG":
                    severity = "alert"
                    title = "Switched to Backup Generator (DG)"
                    details = "Outage detected on main grid; DG backup power active."
                elif curr_source == "GRID":
                    severity = "info"
                    title = "Power Restored (Main Grid)"
                    details = "Grid (EB) electricity supply restored."
                else:
                    severity = "warning"
                    title = f"Power Switched to {curr_source}"
                    details = f"Power source transitioned from {prev_src} to {curr_source}."
                conn.execute(
                    """
                    INSERT INTO events (
                        meter_id, event_type, category, severity, occurred_at_utc,
                        title, details, previous_value, current_value, snapshot_id, created_at_utc
                    ) VALUES (?, 'source_change', 'Power', ?, ?, ?, ?, ?, ?, ?, datetime('now'));
                    """,
                    (meter_id, severity, t_curr_str, title, details, prev_src, curr_source, snap_id),
                )

    # 2. Check for Relay state change
    if prev_snapshot:
        prev_relay = (prev_snapshot.get("relay_status") or "").strip().upper()
        if curr_relay and prev_relay and curr_relay != prev_relay:
            if curr_relay in ("OFF", "0", "DISCONNECTED"):
                severity = "alert"
                title = "Relay Disconnected"
                details = f"Relay status changed from {prev_relay} to {curr_relay}."
            else:
                severity = "info"
                title = "Relay Reconnected"
                details = f"Relay status changed from {prev_relay} to {curr_relay}."
            conn.execute(
                """
                INSERT INTO events (
                    meter_id, event_type, category, severity, occurred_at_utc,
                    title, details, previous_value, current_value, snapshot_id, created_at_utc
                ) VALUES (?, 'relay_change', 'Relay', ?, ?, ?, ?, ?, ?, ?, datetime('now'));
                """,
                (meter_id, severity, t_curr_str, title, details, prev_relay, curr_relay, snap_id),
            )

    # 3. Check for Billing Balance Movements (Recharges & Low Balance)
    if prev_snapshot and curr_balance is not None:
        prev_balance = prev_snapshot.get("meter_balance")
        if prev_balance is not None:
            # A. Recharge detected (balance increased by >= ₹20)
            if (curr_balance - prev_balance) >= 20.0:
                recharge_amt = round(curr_balance - prev_balance, 2)
                title = f"Balance Recharged (+₹{recharge_amt})"
                details = f"Prepaid credit increased from ₹{round(prev_balance, 2)} to ₹{round(curr_balance, 2)}."
                conn.execute(
                    """
                    INSERT INTO events (
                        meter_id, event_type, category, severity, occurred_at_utc,
                        title, details, previous_value, current_value, metadata_json, snapshot_id, created_at_utc
                    ) VALUES (?, 'balance_recharge', 'Billing', 'info', ?, ?, ?, ?, ?, ?, ?, datetime('now'));
                    """,
                    (
                        meter_id,
                        t_curr_str,
                        title,
                        details,
                        str(prev_balance),
                        str(curr_balance),
                        json.dumps({"recharge_amount": recharge_amt}),
                        snap_id,
                    ),
                )

            # B. Low balance threshold crossing (< threshold)
            if prev_balance >= low_balance_threshold > curr_balance:
                title = f"Low Balance Threshold Reached"
                details = f"Prepaid balance dipped to ₹{round(curr_balance, 2)} (below ₹{int(low_balance_threshold)} threshold)."
                conn.execute(
                    """
                    INSERT INTO events (
                        meter_id, event_type, category, severity, occurred_at_utc,
                        title, details, previous_value, current_value, snapshot_id, created_at_utc
                    ) VALUES (?, 'low_balance', 'Billing', 'warning', ?, ?, ?, ?, ?, ?, datetime('now'));
                    """,
                    (
                        meter_id,
                        t_curr_str,
                        title,
                        details,
                        str(prev_balance),
                        str(curr_balance),
                        snap_id,
                    ),
                )
