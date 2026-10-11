"""Unit tests for services/notifications.py and its integration with polling."""

from unittest.mock import patch, MagicMock
import pytest

from services.notifications import NotificationService
from services.polling import PollingService
from elnet.client import ELNetClient
from elnet.schemas import ElnetNetworkError
from storage.repository import SnapshotRepository


def test_send_notification_disabled():
    notifier = NotificationService(enabled=False)
    with patch("subprocess.run") as mock_run:
        result = notifier.send_notification("Title", "Message")
        assert result is False
        mock_run.assert_not_called()


def test_send_notification_non_darwin():
    notifier = NotificationService(enabled=True)
    with patch("sys.platform", "linux"), patch("subprocess.run") as mock_run:
        result = notifier.send_notification("Title", "Message")
        assert result is False
        mock_run.assert_not_called()


def test_send_notification_darwin_success():
    notifier = NotificationService(enabled=True)
    with patch("sys.platform", "darwin"), patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        result = notifier.send_notification(
            title='Electro "Alert"',
            message='Power "Outage"',
            subtitle="DG Active",
            sound="Sosumi",
        )
        assert result is True
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "osascript"
        assert cmd[1] == "-e"
        script = cmd[2]
        assert 'display notification "Power \\"Outage\\"" with title "Electro \\"Alert\\""' in script
        assert 'subtitle "DG Active"' in script
        assert 'sound name "Sosumi"' in script


def test_source_transition_alerts():
    notifier = NotificationService(enabled=True)

    with patch.object(notifier, "send_notification") as mock_notify:
        # Initial snapshot (Grid): establishes baseline, should NOT trigger alert
        notifier.on_snapshot({"eb_dg_status": 0, "meter_balance": 1500.0})
        mock_notify.assert_not_called()

        # Same source: no alert
        notifier.on_snapshot({"eb_dg_status": 0, "meter_balance": 1490.0})
        mock_notify.assert_not_called()

        # Transition to DG (1): Outage Alert!
        notifier.on_snapshot({"eb_dg_status": 1, "meter_balance": 1480.0})
        assert mock_notify.call_count == 1
        assert "Power Source Alert" in mock_notify.call_args[1]["title"]
        assert "Backup Generator (DG)" in mock_notify.call_args[1]["message"]

        # Continued DG: no duplicate alert
        mock_notify.reset_mock()
        notifier.on_snapshot({"eb_dg_status": 1, "meter_balance": 1470.0})
        mock_notify.assert_not_called()

        # Transition back to Grid (0): Power Restored Alert!
        notifier.on_snapshot({"eb_dg_status": 0, "meter_balance": 1460.0})
        assert mock_notify.call_count == 1
        assert "Power Restored" in mock_notify.call_args[1]["title"]
        assert "Normal Grid (EB)" in mock_notify.call_args[1]["message"]


def test_low_balance_alert_and_latch():
    notifier = NotificationService(enabled=True, low_balance_threshold=500.0)

    with patch.object(notifier, "send_notification") as mock_notify:
        # Balance above threshold: no alert
        notifier.on_snapshot({"eb_dg_status": 0, "meter_balance": 750.0})
        mock_notify.assert_not_called()

        # Balance drops below 500.0: Alert fires!
        notifier.on_snapshot({"eb_dg_status": 0, "meter_balance": 450.0})
        assert mock_notify.call_count == 1
        assert "Low Balance Warning" in mock_notify.call_args[1]["title"]
        assert "below ₹500" in mock_notify.call_args[1]["message"]

        # Stays below 500.0: Latched, should NOT fire again
        mock_notify.reset_mock()
        notifier.on_snapshot({"eb_dg_status": 0, "meter_balance": 440.0})
        mock_notify.assert_not_called()

        # User recharges to 2000.0: Latch resets, no alert
        notifier.on_snapshot({"eb_dg_status": 0, "meter_balance": 2000.0})
        mock_notify.assert_not_called()

        # Balance drops below 500.0 again in the future: Alert fires again!
        notifier.on_snapshot({"eb_dg_status": 0, "meter_balance": 490.0})
        assert mock_notify.call_count == 1
        assert "Low Balance Warning" in mock_notify.call_args[1]["title"]


def test_stale_telemetry_and_recovery_flow():
    notifier = NotificationService(enabled=True, stale_failure_threshold=3)

    with patch.object(notifier, "send_notification") as mock_notify:
        # Consecutive failures 1 and 2: below threshold
        notifier.on_poll_failure(1, "Connection timeout")
        notifier.on_poll_failure(2, "Connection timeout")
        mock_notify.assert_not_called()

        # 3rd failure: threshold reached, Alert fires!
        notifier.on_poll_failure(3, "Connection timeout")
        assert mock_notify.call_count == 1
        assert "Telemetry Stale" in mock_notify.call_args[1]["title"]
        assert "3 Failed Polls" in mock_notify.call_args[1]["subtitle"]

        # 4th failure: Latched, should NOT fire again
        mock_notify.reset_mock()
        notifier.on_poll_failure(4, "Connection timeout")
        mock_notify.assert_not_called()

        # Telemetry recovers with valid snapshot: Recovery Alert fires!
        notifier.on_snapshot({"eb_dg_status": 0, "meter_balance": 1200.0})
        assert mock_notify.call_count == 1
        assert "Connection Restored" in mock_notify.call_args[1]["title"]

        # Subsequent snapshot: no duplicate recovery alert
        mock_notify.reset_mock()
        notifier.on_snapshot({"eb_dg_status": 0, "meter_balance": 1195.0})
        mock_notify.assert_not_called()


def test_polling_service_notifies_on_snapshot_and_failure(tmp_path):
    repo = SnapshotRepository(str(tmp_path / "test.db"))
    repo.init_db()

    mock_client = MagicMock(spec=ELNetClient)
    mock_client.is_authenticated = True
    mock_client.meter_id = 1042
    mock_client.get_home_data.return_value = {
        "DeviceId": 1042,
        "MeterBal": 600.0,
        "RelStatus": "ON",
        "kWhRecToday": 10.0,
        "kWhRecMonth": 100.0,
        "CurrentDay_EB": 30.0,
        "CurrentMonth_EB": 300.0,
    }

    mock_notifier = MagicMock()

    poller = PollingService(
        client=mock_client,
        repository=repo,
        username="u",
        password="p",
        notifier=mock_notifier,
    )

    # 1. Successful fetch triggers notifier.on_snapshot
    success, snapshot, err = poller.fetch_once()
    assert success is True
    mock_notifier.on_snapshot.assert_called_once()

    # 2. Network error triggers notifier.on_poll_failure
    mock_client.get_home_data.side_effect = ElnetNetworkError("Meter unreachable")
    success, snapshot, err = poller.fetch_once()
    assert success is False
    mock_notifier.on_poll_failure.assert_called_once_with(1, "Meter unreachable")
