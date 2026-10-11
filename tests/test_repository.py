"""Unit tests for storage/repository.py."""

from datetime import datetime, timezone, timedelta
import pytest

from storage.repository import SnapshotRepository


@pytest.fixture
def repo(tmp_path):
    db_file = tmp_path / "test_electro.db"
    repository = SnapshotRepository(str(db_file))
    repository.init_db()
    return repository


def test_insert_and_get_latest(repo):
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    snapshot = {
        "observed_at_utc": now_str,
        "meter_id": 1042,
        "device_id": "1042",
        "meter_balance": 500.0,
        "relay_status": "ON",
        "kwh_rec_today": 10.0,
        "kvah_rec_today": 11.0,
        "kwh_del_today": 0.0,
        "solar_today": 0.0,
        "kvah_del_today": 0.0,
        "kwh_rec_month": 100.0,
        "kvah_rec_month": 110.0,
        "kwh_del_month": 0.0,
        "solar_month": 0.0,
        "kvah_del_month": 0.0,
        "current_day_eb": 50.0,
        "current_day_dg": 0.0,
        "current_day_solar": 0.0,
        "fixed_charges_today": 5.0,
        "current_month_eb": 500.0,
        "current_month_dg": 0.0,
        "current_month_solar": 0.0,
        "fixed_charges_month": 50.0,
        "is_eb": True,
        "is_dg": False,
        "is_solar": False,
        "is_fixed_charge": True,
        "is_coil_enable": True,
        "eb_dg_status": 1,
        "kwh_kvah_status": 1,
        "meter_sn": None,
    }

    inserted_id = repo.insert_snapshot(snapshot)
    assert inserted_id == 1

    latest = repo.get_latest_snapshot(meter_id=1042)
    assert latest is not None
    assert latest["meter_balance"] == 500.0
    assert latest["is_eb"] is True
    assert latest["is_dg"] is False
    assert latest["relay_status"] == "ON"


def test_get_history_and_range_validation(repo):
    base_time = datetime.now(timezone.utc)
    # Insert snapshots with different timestamps
    times = [
        (base_time - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        (base_time - timedelta(hours=5)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        (base_time - timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ"),
    ]

    for idx, t in enumerate(times):
        repo.insert_snapshot({
            "observed_at_utc": t,
            "meter_id": 1042,
            "device_id": "1042",
            "meter_balance": 100.0 + idx,
            "relay_status": "ON",
            "kwh_rec_today": 5.0,
            "kvah_rec_today": 5.0,
            "kwh_del_today": 0.0,
            "solar_today": 0.0,
            "kvah_del_today": 0.0,
            "kwh_rec_month": 50.0,
            "kvah_rec_month": 50.0,
            "kwh_del_month": 0.0,
            "solar_month": 0.0,
            "kvah_del_month": 0.0,
            "current_day_eb": 20.0,
            "current_day_dg": 0.0,
            "current_day_solar": 0.0,
            "fixed_charges_today": 2.0,
            "current_month_eb": 200.0,
            "current_month_dg": 0.0,
            "current_month_solar": 0.0,
            "fixed_charges_month": 20.0,
            "is_eb": True,
            "is_dg": False,
            "is_solar": False,
            "is_fixed_charge": False,
            "is_coil_enable": False,
            "eb_dg_status": None,
            "kwh_kvah_status": None,
            "meter_sn": None,
        })

    # Test 24h filter (should include only the last two)
    history_24h = repo.get_history(meter_id=1042, range_str="24h")
    assert len(history_24h) == 2

    # Test 7d filter (should include all three)
    history_7d = repo.get_history(meter_id=1042, range_str="7d")
    assert len(history_7d) == 3

    # Invalid range should raise ValueError
    with pytest.raises(ValueError, match="Invalid range"):
        repo.get_history(meter_id=1042, range_str="invalid_range")


def test_record_poll_events_and_health_stats(repo):
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    
    # Record 2 successes, 1 failure
    repo.record_poll_event(now_str, success=True, duration_ms=120, meter_id=1042)
    repo.record_poll_event(now_str, success=True, duration_ms=180, meter_id=1042)
    repo.record_poll_event(
        now_str,
        success=False,
        duration_ms=50,
        error_category="network_error",
        error_message="Connection timed out",
        meter_id=1042,
    )

    events = repo.get_recent_poll_events(limit=10)
    assert len(events) == 3

    stats = repo.get_poll_health_stats(hours=24)
    assert stats["total_attempts"] == 3
    assert stats["successes"] == 2
    assert stats["failures"] == 1
    assert stats["success_rate_pct"] == 66.7
    assert stats["avg_duration_ms"] == 150.0
    assert stats["last_failure"] is not None
    assert stats["last_failure"]["error_category"] == "network_error"


def test_export_snapshots_csv(repo):
    import csv
    import io

    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    repo.insert_snapshot({
        "observed_at_utc": now_str,
        "meter_id": 1042,
        "device_id": "1042",
        "meter_balance": 999.5,
        "relay_status": "ON",
        "kwh_rec_today": 12.5,
        "kvah_rec_today": 13.0,
        "kwh_del_today": 0.0,
        "solar_today": 0.0,
        "kvah_del_today": 0.0,
        "kwh_rec_month": 150.0,
        "kvah_rec_month": 160.0,
        "kwh_del_month": 0.0,
        "solar_month": 0.0,
        "kvah_del_month": 0.0,
        "current_day_eb": 45.0,
        "current_day_dg": 0.0,
        "current_day_solar": 0.0,
        "fixed_charges_today": 5.0,
        "current_month_eb": 450.0,
        "current_month_dg": 0.0,
        "current_month_solar": 0.0,
        "fixed_charges_month": 50.0,
        "is_eb": True,
        "is_dg": False,
        "is_solar": False,
        "is_fixed_charge": True,
        "is_coil_enable": True,
        "eb_dg_status": 0,
        "kwh_kvah_status": 1,
        "meter_sn": None,
    })

    # Test export for 24h
    csv_str = repo.export_snapshots_csv(meter_id=1042, range_str="24h")
    assert csv_str is not None
    reader = csv.DictReader(io.StringIO(csv_str))
    rows = list(reader)
    assert len(rows) == 1
    assert rows[0]["meter_id"] == "1042"
    assert rows[0]["meter_balance"] == "999.5"
    assert rows[0]["power_source"] == "Grid"
    assert rows[0]["eb_dg_status"] == "0"
    assert rows[0]["relay_status"] == "ON"
    assert rows[0]["kwh_rec_today"] == "12.5"

    # Test export for 'all' range
    all_csv_str = repo.export_snapshots_csv(meter_id=1042, range_str="all")
    all_rows = list(csv.DictReader(io.StringIO(all_csv_str)))
    assert len(all_rows) == 1


def test_get_unified_events(repo):
    # Snapshot 1: EB, Relay ON, Balance 1000
    repo.insert_snapshot({
        "observed_at_utc": "2026-10-10T10:00:00Z",
        "meter_id": 1042,
        "eb_dg_status": 0,
        "relay_status": "ON",
        "meter_balance": 1000.0,
    })
    # Snapshot 2: Outage switch to DG (1), Balance 980
    repo.insert_snapshot({
        "observed_at_utc": "2026-10-10T10:15:00Z",
        "meter_id": 1042,
        "eb_dg_status": 1,
        "relay_status": "ON",
        "meter_balance": 980.0,
    })
    # Snapshot 3: Restoration to EB (0), Relay tripped OFF, Balance dip to 450 (below 500)
    repo.insert_snapshot({
        "observed_at_utc": "2026-10-10T10:30:00Z",
        "meter_id": 1042,
        "eb_dg_status": 0,
        "relay_status": "OFF",
        "meter_balance": 450.0,
    })
    # Snapshot 4: Recharge (+550) to 1000
    repo.insert_snapshot({
        "observed_at_utc": "2026-10-10T10:45:00Z",
        "meter_id": 1042,
        "eb_dg_status": 0,
        "relay_status": "ON",
        "meter_balance": 1000.0,
    })

    # Poll events: success -> failure -> success (drop and recovery)
    repo.record_poll_event("2026-10-10T10:00:00Z", success=True, duration_ms=250)
    repo.record_poll_event("2026-10-10T10:05:00Z", success=False, duration_ms=5000, error_category="network_error", error_message="Timeout")
    repo.record_poll_event("2026-10-10T10:10:00Z", success=True, duration_ms=300)

    events = repo.get_unified_events(limit=50)

    # Check that events contain source changes, relay changes, balance changes, drops, and recoveries
    types = [e["type"] for e in events]
    assert "source_change" in types
    assert "relay_change" in types
    assert "balance_recharge" in types
    assert "low_balance" in types
    assert "connection_drop" in types
    assert "connection_recovery" in types

    # Chronological sort: newest timestamp should be first
    timestamps = [e["timestamp"] for e in events]
    assert timestamps == sorted(timestamps, reverse=True)

