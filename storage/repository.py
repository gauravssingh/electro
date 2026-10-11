"""Repository for snapshot storage and retrieval."""

from typing import Optional, List, Dict, Any
from datetime import datetime, timezone, timedelta

from storage.db import get_db_connection, run_migrations


class SnapshotRepository:
    """Manages SQLite storage for meter data snapshots."""

    ALLOWED_RANGES = {
        "24h": timedelta(hours=24),
        "7d": timedelta(days=7),
        "30d": timedelta(days=30),
        "all": None,
    }

    def __init__(self, db_path: str):
        self.db_path = db_path

    def init_db(self) -> None:
        """Run database migrations and initialize schema."""
        run_migrations(self.db_path)

    def insert_snapshot(self, snapshot: Dict[str, Any]) -> int:
        """Insert a normalized snapshot and return its generated ID.
        
        Timestamp lifecycle semantics:
        - attempted_at_utc: When upstream HTTP query cycle was initiated.
        - received_at_utc: When upstream payload was received and parsed.
        - persisted_at_utc: When this repository record was inserted into SQLite.
        - observed_at_utc: Primary observation timestamp (corresponds to received_at_utc).
        
        Note for legacy rows: snapshots prior to migration 003 have NULL in
        attempted_at_utc, received_at_utc, and persisted_at_utc columns.
        """
        persisted_now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        params = dict(snapshot)

        # Ensure timestamp lifecycle values are populated
        if "persisted_at_utc" not in params or not params["persisted_at_utc"]:
            params["persisted_at_utc"] = persisted_now
        if "attempted_at_utc" not in params or not params["attempted_at_utc"]:
            params["attempted_at_utc"] = params.get("observed_at_utc", persisted_now)
        if "received_at_utc" not in params or not params["received_at_utc"]:
            params["received_at_utc"] = params.get("observed_at_utc", persisted_now)

        sql = """
            INSERT INTO snapshots (
                observed_at_utc, attempted_at_utc, received_at_utc, persisted_at_utc,
                meter_id, device_id, meter_balance, relay_status,
                kwh_rec_today, kvah_rec_today, kwh_del_today, solar_today, kvah_del_today,
                kwh_rec_month, kvah_rec_month, kwh_del_month, solar_month, kvah_del_month,
                current_day_eb, current_day_dg, current_day_solar, fixed_charges_today,
                current_month_eb, current_month_dg, current_month_solar, fixed_charges_month,
                is_eb, is_dg, is_solar, is_fixed_charge, is_coil_enable,
                eb_dg_status, kwh_kvah_status, meter_sn
            ) VALUES (
                :observed_at_utc, :attempted_at_utc, :received_at_utc, :persisted_at_utc,
                :meter_id, :device_id, :meter_balance, :relay_status,
                :kwh_rec_today, :kvah_rec_today, :kwh_del_today, :solar_today, :kvah_del_today,
                :kwh_rec_month, :kvah_rec_month, :kwh_del_month, :solar_month, :kvah_del_month,
                :current_day_eb, :current_day_dg, :current_day_solar, :fixed_charges_today,
                :current_month_eb, :current_month_dg, :current_month_solar, :fixed_charges_month,
                :is_eb, :is_dg, :is_solar, :is_fixed_charge, :is_coil_enable,
                :eb_dg_status, :kwh_kvah_status, :meter_sn
            );
        """
        # Convert bools to int for SQLite
        for bool_key in ("is_eb", "is_dg", "is_solar", "is_fixed_charge", "is_coil_enable"):
            val = params.get(bool_key)
            params[bool_key] = int(val) if val is not None else None

        for col in (
            "device_id", "meter_balance", "relay_status",
            "kwh_rec_today", "kvah_rec_today", "kwh_del_today", "solar_today", "kvah_del_today",
            "kwh_rec_month", "kvah_rec_month", "kwh_del_month", "solar_month", "kvah_del_month",
            "current_day_eb", "current_day_dg", "current_day_solar", "fixed_charges_today",
            "current_month_eb", "current_month_dg", "current_month_solar", "fixed_charges_month",
            "eb_dg_status", "kwh_kvah_status", "meter_sn",
        ):
            params.setdefault(col, None)

        with get_db_connection(self.db_path) as conn:
            cursor = conn.execute(sql, params)
            return cursor.lastrowid

    def get_latest_snapshot(self, meter_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
        """Retrieve the most recent snapshot."""
        sql = "SELECT * FROM snapshots"
        params: List[Any] = []
        if meter_id is not None:
            try:
                params.append(int(meter_id))
                sql += " WHERE meter_id = ?"
            except (ValueError, TypeError):
                pass
        sql += " ORDER BY observed_at_utc DESC, id DESC LIMIT 1;"

        with get_db_connection(self.db_path) as conn:
            row = conn.execute(sql, params).fetchone()
            if not row:
                return None
            return self._row_to_dict(row)

    def get_history(
        self,
        meter_id: Optional[int] = None,
        range_str: str = "24h",
    ) -> List[Dict[str, Any]]:
        """Retrieve historical snapshots within the specified time range."""
        if range_str not in self.ALLOWED_RANGES:
            raise ValueError(f"Invalid range '{range_str}'. Allowed values: {', '.join(self.ALLOWED_RANGES.keys())}")

        delta = self.ALLOWED_RANGES[range_str]
        params: List[Any] = []

        if delta is not None:
            cutoff = (datetime.now(timezone.utc) - delta).strftime("%Y-%m-%dT%H:%M:%SZ")
            sql = "SELECT * FROM snapshots WHERE observed_at_utc >= ?"
            params.append(cutoff)
        else:
            sql = "SELECT * FROM snapshots WHERE 1=1"

        if meter_id is not None:
            try:
                params.append(int(meter_id))
                sql += " AND meter_id = ?"
            except (ValueError, TypeError):
                pass
        sql += " ORDER BY observed_at_utc ASC, id ASC;"

        with get_db_connection(self.db_path) as conn:
            rows = conn.execute(sql, params).fetchall()
            return [self._row_to_dict(r) for r in rows]

    def export_snapshots_csv(
        self,
        meter_id: Optional[int] = None,
        range_str: str = "24h",
    ) -> str:
        """Export historical snapshots in standard CSV format for data portability."""
        import csv
        import io

        snapshots = self.get_history(meter_id=meter_id, range_str=range_str)

        output = io.StringIO()
        fieldnames = [
            "observed_at_utc",
            "meter_id",
            "power_source",
            "eb_dg_status",
            "relay_status",
            "meter_balance",
            "kwh_rec_today",
            "kwh_rec_month",
            "current_day_eb",
            "current_day_dg",
            "current_day_solar",
            "fixed_charges_today",
            "current_month_eb",
            "current_month_dg",
            "current_month_solar",
            "fixed_charges_month",
            "is_eb",
            "is_dg",
            "attempted_at_utc",
            "received_at_utc",
            "persisted_at_utc",
        ]
        writer = csv.DictWriter(output, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()

        for s in snapshots:
            eb_dg = s.get("eb_dg_status")
            power_source = "Grid" if eb_dg == 0 else "DG" if eb_dg == 1 else "Unknown"
            row = {
                "observed_at_utc": s.get("observed_at_utc"),
                "meter_id": s.get("meter_id"),
                "power_source": power_source,
                "eb_dg_status": eb_dg,
                "relay_status": s.get("relay_status"),
                "meter_balance": s.get("meter_balance"),
                "kwh_rec_today": s.get("kwh_rec_today"),
                "kwh_rec_month": s.get("kwh_rec_month"),
                "current_day_eb": s.get("current_day_eb"),
                "current_day_dg": s.get("current_day_dg"),
                "current_day_solar": s.get("current_day_solar"),
                "fixed_charges_today": s.get("fixed_charges_today"),
                "current_month_eb": s.get("current_month_eb"),
                "current_month_dg": s.get("current_month_dg"),
                "current_month_solar": s.get("current_month_solar"),
                "fixed_charges_month": s.get("fixed_charges_month"),
                "is_eb": s.get("is_eb"),
                "is_dg": s.get("is_dg"),
                "attempted_at_utc": s.get("attempted_at_utc"),
                "received_at_utc": s.get("received_at_utc"),
                "persisted_at_utc": s.get("persisted_at_utc"),
            }
            writer.writerow(row)

        return output.getvalue()

    def count_snapshots(self, meter_id: Optional[int] = None) -> int:
        """Count total snapshot records."""
        sql = "SELECT COUNT(*) AS cnt FROM snapshots"
        params: List[Any] = []
        if meter_id is not None:
            try:
                params.append(int(meter_id))
                sql += " WHERE meter_id = ?"
            except (ValueError, TypeError):
                pass
        with get_db_connection(self.db_path) as conn:
            row = conn.execute(sql, params).fetchone()
            return row["cnt"] if row else 0

    def record_poll_event(
        self,
        attempted_at_utc: str,
        success: bool,
        duration_ms: Optional[int] = None,
        error_category: Optional[str] = None,
        error_message: Optional[str] = None,
        meter_id: Optional[int] = None,
    ) -> int:
        """Record an attempt to query the upstream ELNet service."""
        sql = """
            INSERT INTO poll_events (
                attempted_at_utc, success, duration_ms, error_category, error_message, meter_id
            ) VALUES (?, ?, ?, ?, ?, ?);
        """
        clean_meter_id = None
        if meter_id is not None:
            try:
                clean_meter_id = int(meter_id)
            except (ValueError, TypeError):
                pass

        params = (
            attempted_at_utc,
            1 if success else 0,
            duration_ms,
            error_category,
            error_message,
            clean_meter_id,
        )
        with get_db_connection(self.db_path) as conn:
            cursor = conn.execute(sql, params)
            return cursor.lastrowid

    def get_recent_poll_events(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Retrieve recent poll attempts (successes and failures)."""
        sql = "SELECT * FROM poll_events ORDER BY attempted_at_utc DESC, id DESC LIMIT ?;"
        with get_db_connection(self.db_path) as conn:
            rows = conn.execute(sql, (limit,)).fetchall()
            return [dict(r) for r in rows]

    def get_unified_events(
        self,
        limit: int = 50,
        meter_id: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Extract a chronological timeline of significant system events.

        Detects and returns:
        - Power source transitions (Grid <-> DG)
        - Relay state changes (ON <-> OFF)
        - Upstream connection drops and recoveries
        - Prepaid balance recharges and low-balance warnings
        """
        events: List[Dict[str, Any]] = []

        with get_db_connection(self.db_path) as conn:
            # 1. State transitions from snapshots
            snap_sql = """
                SELECT id, observed_at_utc, meter_id, eb_dg_status, relay_status, meter_balance
                FROM snapshots
            """
            params: List[Any] = []
            if meter_id is not None:
                try:
                    params.append(int(meter_id))
                    snap_sql += " WHERE meter_id = ?"
                except (ValueError, TypeError):
                    pass
            snap_sql += " ORDER BY id DESC LIMIT 5000;"

            snap_rows = conn.execute(snap_sql, params).fetchall()
            snapshots = list(reversed(snap_rows))

            prev_eb_dg = None
            prev_relay = None
            prev_balance = None

            for s in snapshots:
                ts = s["observed_at_utc"]
                eb_dg = s["eb_dg_status"]
                relay = s["relay_status"]
                balance = s["meter_balance"]

                # Source transition
                if eb_dg is not None and prev_eb_dg is not None and eb_dg != prev_eb_dg:
                    if eb_dg == 1:
                        events.append({
                            "id": f"src-{s['id']}",
                            "timestamp": ts,
                            "type": "source_change",
                            "category": "Power",
                            "title": "Switched to Backup Generator (DG)",
                            "details": "Outage detected on main grid; DG backup power active.",
                            "severity": "alert",
                        })
                    elif eb_dg == 0:
                        events.append({
                            "id": f"src-{s['id']}",
                            "timestamp": ts,
                            "type": "source_change",
                            "category": "Power",
                            "title": "Power Restored (Main Grid)",
                            "details": "Grid (EB) electricity supply restored.",
                            "severity": "info",
                        })

                # Relay transition
                if relay is not None and prev_relay is not None and relay != prev_relay:
                    if str(relay).upper() in ("OFF", "0", "DISCONNECTED"):
                        events.append({
                            "id": f"rel-{s['id']}",
                            "timestamp": ts,
                            "type": "relay_change",
                            "category": "Relay",
                            "title": "Relay Disconnected",
                            "details": f"Relay status changed from {prev_relay} to {relay}.",
                            "severity": "alert",
                        })
                    else:
                        events.append({
                            "id": f"rel-{s['id']}",
                            "timestamp": ts,
                            "type": "relay_change",
                            "category": "Relay",
                            "title": "Relay Reconnected",
                            "details": f"Relay status changed from {prev_relay} to {relay}.",
                            "severity": "info",
                        })

                # Balance recharge & low balance
                if balance is not None and prev_balance is not None:
                    try:
                        b_curr = float(balance)
                        b_prev = float(prev_balance)
                        if b_curr - b_prev >= 20.0:
                            events.append({
                                "id": f"bal-{s['id']}",
                                "timestamp": ts,
                                "type": "balance_recharge",
                                "category": "Billing",
                                "title": f"Balance Recharged (+₹{b_curr - b_prev:.2f})",
                                "details": f"Prepaid credit increased from ₹{b_prev:.2f} to ₹{b_curr:.2f}.",
                                "severity": "info",
                            })
                        elif b_prev >= 500.0 and b_curr < 500.0:
                            events.append({
                                "id": f"bal-{s['id']}",
                                "timestamp": ts,
                                "type": "low_balance",
                                "category": "Billing",
                                "title": "Low Balance Threshold Reached",
                                "details": f"Prepaid balance dipped to ₹{b_curr:.2f} (below ₹500 threshold).",
                                "severity": "warning",
                            })
                    except (ValueError, TypeError):
                        pass

                prev_eb_dg = eb_dg
                prev_relay = relay
                prev_balance = balance

            # 2. Connection drops & recoveries from poll_events
            poll_sql = """
                SELECT id, attempted_at_utc, success, duration_ms, error_category, error_message
                FROM poll_events
                ORDER BY id DESC LIMIT 5000;
            """
            poll_rows = conn.execute(poll_sql).fetchall()
            poll_events = list(reversed(poll_rows))

            prev_success = None
            in_failure_streak = False

            for p in poll_events:
                ts = p["attempted_at_utc"]
                succ = bool(p["success"])
                err_msg = p["error_message"] or "Unable to reach meter telemetry."
                err_cat = p["error_category"] or "network_error"

                if prev_success is not None:
                    # Connection dropped
                    if prev_success and not succ:
                        in_failure_streak = True
                        events.append({
                            "id": f"poll-{p['id']}",
                            "timestamp": ts,
                            "type": "connection_drop",
                            "category": "Network",
                            "title": "Connection Dropped",
                            "details": f"Upstream poll failed: {err_msg} ({err_cat}).",
                            "severity": "warning",
                        })
                    # Connection recovered
                    elif not prev_success and succ and in_failure_streak:
                        in_failure_streak = False
                        events.append({
                            "id": f"poll-{p['id']}",
                            "timestamp": ts,
                            "type": "connection_recovery",
                            "category": "Network",
                            "title": "Connection Restored",
                            "details": "Telemetry communication restored successfully.",
                            "severity": "info",
                        })

                prev_success = succ

        # Sort all events chronologically newest first
        events.sort(key=lambda x: x["timestamp"], reverse=True)
        return events[:max(1, limit)]

    def get_poll_health_stats(self, hours: int = 24) -> Dict[str, Any]:
        """Compute poll reliability metrics over the specified recent window."""
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
        with get_db_connection(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT
                    COUNT(*) AS total_attempts,
                    SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) AS successes,
                    SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END) AS failures,
                    AVG(CASE WHEN success = 1 THEN duration_ms ELSE NULL END) AS avg_duration_ms
                FROM poll_events
                WHERE attempted_at_utc >= ?;
                """,
                (cutoff,),
            ).fetchone()

            total = row["total_attempts"] if row and row["total_attempts"] else 0
            successes = row["successes"] if row and row["successes"] else 0
            failures = row["failures"] if row and row["failures"] else 0
            avg_ms = round(row["avg_duration_ms"], 1) if row and row["avg_duration_ms"] else 0.0

            success_rate_pct = round((successes / total) * 100, 1) if total > 0 else 100.0

            last_failure = conn.execute(
                """
                SELECT attempted_at_utc, error_category, error_message
                FROM poll_events
                WHERE success = 0
                ORDER BY attempted_at_utc DESC, id DESC
                LIMIT 1;
                """
            ).fetchone()

            return {
                "window_hours": hours,
                "total_attempts": total,
                "successes": successes,
                "failures": failures,
                "success_rate_pct": success_rate_pct,
                "avg_duration_ms": avg_ms,
                "last_failure": dict(last_failure) if last_failure else None,
            }

    @staticmethod
    def _row_to_dict(row: Any) -> Dict[str, Any]:
        """Convert a sqlite3.Row to standard dictionary with native types."""
        d = dict(row)
        for bool_key in ("is_eb", "is_dg", "is_solar", "is_fixed_charge", "is_coil_enable"):
            if d.get(bool_key) is not None:
                d[bool_key] = bool(d[bool_key])
        return d

