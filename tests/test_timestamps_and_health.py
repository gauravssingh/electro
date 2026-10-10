"""Regression tests for snapshot timestamp lifecycle, freshness thresholds, and health statuses."""

from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock
import pytest

from storage.repository import SnapshotRepository
from services.polling import PollingService
from services.normalization import normalize_home_data
from elnet.client import ELNetClient


@pytest.fixture
def repo(tmp_path):
    db_file = tmp_path / "test_timestamps.db"
    repository = SnapshotRepository(str(db_file))
    repository.init_db()
    return repository


def test_snapshot_timestamp_lifecycle_fields(repo):
    """Verify attempted_at_utc, received_at_utc, persisted_at_utc lifecycle fields are stored."""
    attempt_time = "2026-10-10T14:00:00Z"
    received_time = "2026-10-10T14:00:02Z"

    raw = {
        "DeviceId": 1042,
        "MeterBal": 1200.5,
        "RelStatus": "ON",
        "kWhRecToday": 15.0,
        "EBDGStatus": 0,
    }

    normalized = normalize_home_data(
        raw,
        meter_id=1042,
        attempted_at_utc=attempt_time,
        received_at_utc=received_time,
    )

    # observed_at_utc represents received_at_utc
    assert normalized["attempted_at_utc"] == attempt_time
    assert normalized["received_at_utc"] == received_time
    assert normalized["observed_at_utc"] == received_time

    snap_id = repo.insert_snapshot(normalized)
    assert snap_id is not None

    stored = repo.get_latest_snapshot(1042)
    assert stored is not None
    assert stored["attempted_at_utc"] == attempt_time
    assert stored["received_at_utc"] == received_time
    assert stored["observed_at_utc"] == received_time
    assert stored["persisted_at_utc"] is not None
    # Persisted time should be equal to or after received_time
    assert stored["persisted_at_utc"] >= received_time


def test_staleness_calculation_against_freshness_threshold(repo):
    """Verify staleness derives from last_success age against max(interval * 2.5, 45.0)."""
    mock_client = MagicMock(spec=ELNetClient)
    poller = PollingService(
        client=mock_client,
        repository=repo,
        poll_interval_seconds=15,
    )

    # Freshman threshold for 15s poll interval: max(15 * 2.5, 45.0) = 45.0s
    status_info = poller.get_status_info()
    assert status_info["freshness_threshold_seconds"] == 45.0
    # Initial status has no successful poll, hence stale
    assert status_info["status"] == "initializing"
    assert status_info["stale"] is True

    # Case 1: Fresh poll (10 seconds ago) -> not stale
    recent_time = (datetime.now(timezone.utc) - timedelta(seconds=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
    poller.last_success_at_utc = recent_time
    poller.status = "connected"

    status_fresh = poller.get_status_info()
    assert status_fresh["stale"] is False
    assert status_fresh["age_seconds"] is not None
    assert status_fresh["age_seconds"] <= 15.0

    # Case 2: Aged poll (60 seconds ago > 45s threshold) -> stale
    old_time = (datetime.now(timezone.utc) - timedelta(seconds=60)).strftime("%Y-%m-%dT%H:%M:%SZ")
    poller.last_success_at_utc = old_time
    status_aged = poller.get_status_info()
    assert status_aged["stale"] is True
    assert status_aged["age_seconds"] >= 59.0


def test_staleness_threshold_scales_with_larger_interval(repo):
    """Verify staleness threshold scales up with larger poll intervals."""
    mock_client = MagicMock(spec=ELNetClient)
    poller = PollingService(
        client=mock_client,
        repository=repo,
        poll_interval_seconds=60,
    )
    # max(60 * 2.5, 45.0) = 150.0s
    status = poller.get_status_info()
    assert status["freshness_threshold_seconds"] == 150.0


def test_standby_process_reads_freshness_from_database(repo):
    """A standby web process reflects latest snapshot from SQLite when last_success_at_utc is None."""
    # Insert a fresh snapshot into repo
    recent_time = (datetime.now(timezone.utc) - timedelta(seconds=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    repo.insert_snapshot({
        "observed_at_utc": recent_time,
        "meter_id": 1042,
        "device_id": "1042",
        "meter_balance": 999.0,
        "relay_status": "ON",
        "kwh_rec_today": 2.0,
        "kvah_rec_today": 2.0,
        "kwh_del_today": 0.0,
        "solar_today": 0.0,
        "kvah_del_today": 0.0,
        "kwh_rec_month": 20.0,
        "kvah_rec_month": 20.0,
        "kwh_del_month": 0.0,
        "solar_month": 0.0,
        "kvah_del_month": 0.0,
        "current_day_eb": 10.0,
        "current_day_dg": 0.0,
        "current_day_solar": 0.0,
        "fixed_charges_today": 0.0,
        "current_month_eb": 100.0,
        "current_month_dg": 0.0,
        "current_month_solar": 0.0,
        "fixed_charges_month": 0.0,
        "is_eb": True,
        "is_dg": False,
        "is_solar": False,
        "is_fixed_charge": False,
        "is_coil_enable": False,
        "eb_dg_status": 0,
        "kwh_kvah_status": 1,
        "meter_sn": None,
    })

    mock_client = MagicMock(spec=ELNetClient)
    mock_client.meter_id = 1042

    poller = PollingService(
        client=mock_client,
        repository=repo,
        poll_interval_seconds=15,
    )
    poller.status = "standby"
    poller.is_collector_owner = False
    assert poller.last_success_at_utc is None

    # Standby poller should read recent snapshot timestamp from DB
    info = poller.get_status_info()
    assert info["last_success_at"] == recent_time
    assert info["stale"] is False
    assert info["is_collector_owner"] is False
    assert info["collector_running"] is False


def test_legacy_snapshots_readable_with_missing_timestamps_represented_honestly(repo):
    """Legacy snapshots without lifecycle columns remain fully readable with NULL represented as None."""
    from storage.db import get_db_connection

    legacy_time = (datetime.now(timezone.utc) - timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    # Insert row directly without lifecycle timestamps (simulating historical pre-migration data)
    with get_db_connection(repo.db_path) as conn:
        conn.execute(
            """
            INSERT INTO snapshots (
                observed_at_utc, meter_id, device_id, meter_balance, relay_status,
                kwh_rec_today, kvah_rec_today, kwh_del_today, solar_today, kvah_del_today,
                kwh_rec_month, kvah_rec_month, kwh_del_month, solar_month, kvah_del_month,
                current_day_eb, current_day_dg, current_day_solar, fixed_charges_today,
                current_month_eb, current_month_dg, current_month_solar, fixed_charges_month,
                is_eb, is_dg, is_solar, is_fixed_charge, is_coil_enable,
                eb_dg_status, kwh_kvah_status, meter_sn
            ) VALUES (
                ?, 1042, '1042', 300.0, 'ON',
                5.0, 5.0, 0.0, 0.0, 0.0,
                50.0, 50.0, 0.0, 0.0, 0.0,
                25.0, 0.0, 0.0, 0.0,
                250.0, 0.0, 0.0, 0.0,
                1, 0, 0, 0, 0,
                0, 1, 'LEGACY_METER'
            );
            """,
            (legacy_time,),
        )

    # Verify get_latest_snapshot reads the legacy row honestly
    latest = repo.get_latest_snapshot(1042)
    assert latest is not None
    assert latest["observed_at_utc"] == legacy_time
    assert latest["attempted_at_utc"] is None
    assert latest["received_at_utc"] is None
    assert latest["persisted_at_utc"] is None
    assert latest["meter_balance"] == 300.0

    # Verify get_history reads the legacy row honestly
    history = repo.get_history(1042, range_str="30d")
    assert len(history) >= 1
    legacy_point = [p for p in history if p["observed_at_utc"] == legacy_time][0]
    assert legacy_point["attempted_at_utc"] is None
    assert legacy_point["received_at_utc"] is None
    assert legacy_point["persisted_at_utc"] is None


def test_persisted_at_utc_semantics_and_ordering(repo):
    """Verify persisted_at_utc represents the SQLite write time and respects attempt/received ordering."""
    attempt_time = "2026-10-10T15:00:00Z"
    received_time = "2026-10-10T15:00:02Z"

    raw = {
        "DeviceId": 1042,
        "MeterBal": 500.0,
        "RelStatus": "ON",
        "kWhRecToday": 1.0,
    }

    normalized = normalize_home_data(
        raw,
        meter_id=1042,
        attempted_at_utc=attempt_time,
        received_at_utc=received_time,
    )

    # Before inserting, persisted_at_utc is not set by normalization
    assert normalized.get("persisted_at_utc") is None

    # Insert into repository
    snap_id = repo.insert_snapshot(normalized)
    assert snap_id is not None

    stored = repo.get_latest_snapshot(1042)
    assert stored is not None
    assert stored["persisted_at_utc"] is not None
    # Chronological invariant: attempt <= receipt <= persistence
    assert stored["attempted_at_utc"] <= stored["received_at_utc"] <= stored["persisted_at_utc"]
