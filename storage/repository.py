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
    }

    def __init__(self, db_path: str):
        self.db_path = db_path

    def init_db(self) -> None:
        """Run database migrations and initialize schema."""
        run_migrations(self.db_path)

    def insert_snapshot(self, snapshot: Dict[str, Any]) -> int:
        """Insert a normalized snapshot and return its generated ID."""
        sql = """
            INSERT INTO snapshots (
                observed_at_utc, meter_id, device_id, meter_balance, relay_status,
                kwh_rec_today, kvah_rec_today, kwh_del_today, solar_today, kvah_del_today,
                kwh_rec_month, kvah_rec_month, kwh_del_month, solar_month, kvah_del_month,
                current_day_eb, current_day_dg, current_day_solar, fixed_charges_today,
                current_month_eb, current_month_dg, current_month_solar, fixed_charges_month,
                is_eb, is_dg, is_solar, is_fixed_charge, is_coil_enable,
                eb_dg_status, kwh_kvah_status, meter_sn
            ) VALUES (
                :observed_at_utc, :meter_id, :device_id, :meter_balance, :relay_status,
                :kwh_rec_today, :kvah_rec_today, :kwh_del_today, :solar_today, :kvah_del_today,
                :kwh_rec_month, :kvah_rec_month, :kwh_del_month, :solar_month, :kvah_del_month,
                :current_day_eb, :current_day_dg, :current_day_solar, :fixed_charges_today,
                :current_month_eb, :current_month_dg, :current_month_solar, :fixed_charges_month,
                :is_eb, :is_dg, :is_solar, :is_fixed_charge, :is_coil_enable,
                :eb_dg_status, :kwh_kvah_status, :meter_sn
            );
        """
        params = dict(snapshot)
        # Convert bools to int for SQLite
        for bool_key in ("is_eb", "is_dg", "is_solar", "is_fixed_charge", "is_coil_enable"):
            val = params.get(bool_key)
            params[bool_key] = int(val) if val is not None else None

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
        cutoff = (datetime.now(timezone.utc) - delta).strftime("%Y-%m-%dT%H:%M:%SZ")

        sql = "SELECT * FROM snapshots WHERE observed_at_utc >= ?"
        params: List[Any] = [cutoff]
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

    @staticmethod
    def _row_to_dict(row: Any) -> Dict[str, Any]:
        """Convert a sqlite3.Row to standard dictionary with native types."""
        d = dict(row)
        for bool_key in ("is_eb", "is_dg", "is_solar", "is_fixed_charge", "is_coil_enable"):
            if d.get(bool_key) is not None:
                d[bool_key] = bool(d[bool_key])
        return d
