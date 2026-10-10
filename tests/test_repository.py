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

