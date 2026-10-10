"""Unit tests for services/polling.py."""

from unittest.mock import MagicMock
import pytest

from elnet.client import ELNetClient
from elnet.schemas import ErrorCategory, ElnetAuthError, ElnetNetworkError
from services.polling import PollingService
from storage.repository import SnapshotRepository


@pytest.fixture
def repo(tmp_path):
    db_file = tmp_path / "test_polling.db"
    repository = SnapshotRepository(str(db_file))
    repository.init_db()
    return repository


def test_polling_fetch_success(repo):
    mock_client = MagicMock(spec=ELNetClient)
    mock_client.is_authenticated = False
    mock_client.meter_id = 1042

    def mock_login(*args, **kwargs):
        mock_client.is_authenticated = True
        return {"success": True, "flat_id": 1042}

    mock_client.login.side_effect = mock_login
    mock_client.get_home_data.return_value = {
        "DeviceId": 1042,
        "MeterBal": 550.0,
        "RelStatus": "ON",
        "kWhRecToday": 10.5,
        "kWhRecMonth": 120.0,
        "CurrentDay_EB": 45.0,
        "CurrentMonth_EB": 450.0,
    }

    poller = PollingService(
        client=mock_client,
        repository=repo,
        username="test_user",
        password="test_password",
    )

    success, snapshot, err = poller.fetch_once()

    assert success is True
    assert err is None
    assert snapshot is not None
    assert snapshot["meter_balance"] == 550.0
    assert poller.status == "connected"
    assert poller.last_success_at_utc is not None
    assert repo.count_snapshots() == 1


def test_polling_missing_credentials_halts_polling(repo):
    mock_client = MagicMock(spec=ELNetClient)
    mock_client.is_authenticated = False

    poller = PollingService(
        client=mock_client,
        repository=repo,
        username="",
        password="",
    )

    success, snapshot, err = poller.fetch_once()

    assert success is False
    assert poller.status == "authentication_required"
    assert poller.polling_enabled is False
    assert poller.last_error_category == "configuration_error"


def test_polling_auth_failure_schedules_backoff_retry(repo):
    mock_client = MagicMock(spec=ELNetClient)
    mock_client.is_authenticated = False
    mock_client.meter_id = 1042
    mock_client.login.side_effect = ElnetAuthError("Invalid username or password")

    poller = PollingService(
        client=mock_client,
        repository=repo,
        username="wrong_user",
        password="wrong_password",
        poll_interval_seconds=15,
    )

    success, snapshot, err = poller.fetch_once()

    assert success is False
    assert err == "Invalid username or password"
    assert poller.status == "authentication_required"
    # Polling remains enabled to allow recovery with backoff
    assert poller.polling_enabled is True
    assert poller.consecutive_failures == 1
    assert poller.next_retry_at_utc is not None
    assert poller.last_duration_ms is not None

    # Verify poll event was recorded
    events = repo.get_recent_poll_events(10)
    assert len(events) == 1
    assert events[0]["success"] == 0
    assert events[0]["error_category"] == "authentication_error"


def test_polling_transient_failure_marks_stale_and_preserves_last(repo):
    # First insert a valid snapshot into repo
    repo.insert_snapshot({
        "observed_at_utc": "2026-10-10T10:00:00Z",
        "meter_id": 1042,
        "device_id": "1042",
        "meter_balance": 400.0,
        "relay_status": "ON",
        "kwh_rec_today": 8.0,
        "kvah_rec_today": 8.0,
        "kwh_del_today": 0.0,
        "solar_today": 0.0,
        "kvah_del_today": 0.0,
        "kwh_rec_month": 80.0,
        "kvah_rec_month": 80.0,
        "kwh_del_month": 0.0,
        "solar_month": 0.0,
        "kvah_del_month": 0.0,
        "current_day_eb": 40.0,
        "current_day_dg": 0.0,
        "current_day_solar": 0.0,
        "fixed_charges_today": 0.0,
        "current_month_eb": 400.0,
        "current_month_dg": 0.0,
        "current_month_solar": 0.0,
        "fixed_charges_month": 0.0,
        "is_eb": True,
        "is_dg": False,
        "is_solar": False,
        "is_fixed_charge": False,
        "is_coil_enable": False,
        "eb_dg_status": None,
        "kwh_kvah_status": None,
        "meter_sn": None,
    })

    mock_client = MagicMock(spec=ELNetClient)
    mock_client.is_authenticated = True
    mock_client.meter_id = 1042
    mock_client.get_home_data.side_effect = ElnetNetworkError("Connection timed out")

    poller = PollingService(
        client=mock_client,
        repository=repo,
        username="user",
        password="password",
    )

    success, snapshot, err = poller.fetch_once()

    assert success is False
    assert poller.status == "stale"
    assert snapshot is not None
    assert snapshot["meter_balance"] == 400.0  # Preserved last known snapshot


def test_polling_lock_prevents_overlap(repo):
    mock_client = MagicMock(spec=ELNetClient)
    poller = PollingService(client=mock_client, repository=repo)

    # Manually acquire the lock to simulate a running fetch
    assert poller._lock.acquire(blocking=False) is True
    try:
        success, snapshot, err = poller.fetch_once()
        assert success is False
        assert "Another refresh is already in progress" in str(err)
    finally:
        poller._lock.release()

