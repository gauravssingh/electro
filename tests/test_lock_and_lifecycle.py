"""Regression tests for process-lock ownership, shutdown races, and lifecycle idempotency."""

import time
import threading
from unittest.mock import MagicMock
import pytest
import fcntl

from services.polling import PollingService
from storage.repository import SnapshotRepository
from elnet.client import ELNetClient
from app import create_app


@pytest.fixture
def repo(tmp_path):
    db_file = tmp_path / "test_lifecycle.db"
    repository = SnapshotRepository(str(db_file))
    repository.init_db()
    return repository


def test_lock_prevents_dual_collector_startup(repo):
    """At most one process/poller may own periodic capture."""
    mock_client1 = MagicMock(spec=ELNetClient)
    mock_client1.timeout = 1

    mock_client2 = MagicMock(spec=ELNetClient)
    mock_client2.timeout = 1

    poller1 = PollingService(
        client=mock_client1,
        repository=repo,
        username="user1",
        password="pass1",
        poll_interval_seconds=60,
    )

    poller2 = PollingService(
        client=mock_client2,
        repository=repo,
        username="user2",
        password="pass2",
        poll_interval_seconds=60,
    )

    # Start poller1
    assert poller1.start() is True
    assert poller1.is_collector_owner is True
    assert poller1._thread is not None
    assert poller1._thread.is_alive() is True

    # Attempt to start poller2 pointing at same lock file
    assert poller2.start() is False
    assert poller2.is_collector_owner is False
    assert poller2.status == "standby"
    assert poller2._thread is None

    # Standby poller cannot execute fetch_once
    success, _, err = poller2.fetch_once()
    assert success is False
    assert "Another process holds the collector lock" in err

    # Stop poller1 cleanly
    assert poller1.stop(timeout=2.0) is True
    assert poller1._thread is None
    assert poller1.is_collector_owner is False

    # Now poller2 can start and acquire lock
    assert poller2.start() is True
    assert poller2.is_collector_owner is True
    assert poller2._thread is not None
    assert poller2._thread.is_alive() is True

    # Cleanup
    assert poller2.stop(timeout=2.0) is True


def test_start_and_stop_idempotent(repo):
    """Calling start() or stop() repeatedly is safe and idempotent."""
    mock_client = MagicMock(spec=ELNetClient)
    mock_client.timeout = 1

    poller = PollingService(
        client=mock_client,
        repository=repo,
        username="user",
        password="password",
        poll_interval_seconds=60,
    )

    # Calling stop() before start() is safe
    assert poller.stop() is True
    assert poller.stop() is True

    # Multiple start() calls
    assert poller.start() is True
    first_thread = poller._thread
    assert first_thread.is_alive() is True

    assert poller.start() is True
    # Thread remains the same running thread
    assert poller._thread is first_thread

    # Multiple stop() calls
    assert poller.stop(timeout=2.0) is True
    assert poller._thread is None

    assert poller.stop(timeout=2.0) is True
    assert poller._thread is None


def test_lock_held_during_slow_shutdown(repo):
    """Process lock remains held while the polling thread is still alive during stop."""
    mock_client = MagicMock(spec=ELNetClient)
    mock_client.timeout = 5
    mock_client.is_authenticated = True
    mock_client.meter_id = 1042

    in_fetch_event = threading.Event()
    continue_fetch_event = threading.Event()

    def slow_homedata(*args, **kwargs):
        in_fetch_event.set()
        # Simulate in-flight I/O that takes 0.3s
        continue_fetch_event.wait(timeout=1.0)
        return {
            "DeviceId": 1042,
            "MeterBal": 500.0,
            "RelStatus": "ON",
            "kWhRecToday": 1.0,
        }

    mock_client.get_home_data.side_effect = slow_homedata

    poller = PollingService(
        client=mock_client,
        repository=repo,
        username="u",
        password="p",
        poll_interval_seconds=60,
    )

    poller.start()

    # Wait until poller thread has entered fetch_once / get_home_data
    assert in_fetch_event.wait(timeout=2.0) is True

    # Trigger stop in a separate thread so we can inspect lock file state while stop is in-flight
    stop_finished = threading.Event()

    def run_stop():
        poller.stop(timeout=3.0)
        stop_finished.set()

    t_stop = threading.Thread(target=run_stop)
    t_stop.start()

    # While slow_homedata is still waiting, another process trying to acquire the lock must fail
    lock_path = poller._lock_path
    f_other = open(lock_path, "a+")
    with pytest.raises(BlockingIOError):
        fcntl.flock(f_other, fcntl.LOCK_EX | fcntl.LOCK_NB)
    f_other.close()

    # Now let the slow fetch finish
    continue_fetch_event.set()
    t_stop.join(timeout=3.0)
    assert stop_finished.is_set() is True
    assert poller._thread is None

    # After stop is finished, lock can be acquired
    f_other = open(lock_path, "a+")
    fcntl.flock(f_other, fcntl.LOCK_EX | fcntl.LOCK_NB)
    fcntl.flock(f_other, fcntl.LOCK_UN)
    f_other.close()


def test_shutdown_timeout_retains_ownership_and_thread(repo):
    """If stop() times out, retain ownership and thread reference to prevent replacement worker."""
    mock_client = MagicMock(spec=ELNetClient)
    mock_client.timeout = 10
    mock_client.is_authenticated = True
    mock_client.meter_id = 1042

    block_fetch_event = threading.Event()
    unblock_fetch_event = threading.Event()

    def hanging_fetch(*args, **kwargs):
        block_fetch_event.set()
        unblock_fetch_event.wait(timeout=5.0)
        return {"DeviceId": 1042, "MeterBal": 100.0}

    mock_client.get_home_data.side_effect = hanging_fetch

    poller = PollingService(
        client=mock_client,
        repository=repo,
        username="u",
        password="p",
        poll_interval_seconds=60,
    )

    poller.start()
    assert block_fetch_event.wait(timeout=2.0) is True

    # Attempt stop with tiny timeout so it times out
    stopped = poller.stop(timeout=0.05)
    assert stopped is False

    # Thread reference and lock ownership must be retained!
    assert poller._thread is not None
    assert poller._thread.is_alive() is True
    assert poller.is_collector_owner is True

    # Starting again does not launch a replacement worker
    existing_thread = poller._thread
    assert poller.start() is True
    assert poller._thread is existing_thread

    # Unblock and clean up
    unblock_fetch_event.set()
    assert poller.stop(timeout=3.0) is True
    assert poller._thread is None
    assert poller.is_collector_owner is False


def test_api_refresh_blocked_on_standby_process(repo):
    """POST /api/refresh returns 409 Conflict if this process is in standby mode."""
    mock_poller = MagicMock(spec=PollingService)
    mock_poller.client = MagicMock()
    mock_poller.client.meter_id = 1042
    mock_poller.status = "standby"
    mock_poller.is_collector_owner = False
    mock_poller.is_fetching = False

    app = create_app(
        test_config={
            "TESTING": True,
            "ELECTRO_DB_PATH": repo.db_path,
            "POLLING_SERVICE": mock_poller,
        },
        start_poller=False,
    )
    client = app.test_client()

    res = client.post("/api/refresh")
    assert res.status_code == 409
    data = res.get_json()
    assert data["success"] is False
    assert "another process owns the collector worker" in data["error"]
