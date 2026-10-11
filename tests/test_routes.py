"""Integration tests for web routes in app.py."""

import json
from unittest.mock import MagicMock
import pytest

from app import create_app
from storage.repository import SnapshotRepository
from services.polling import PollingService


@pytest.fixture
def app_and_repo(tmp_path):
    db_file = tmp_path / "test_routes.db"
    repo = SnapshotRepository(str(db_file))
    repo.init_db()

    mock_poller = MagicMock(spec=PollingService)
    mock_poller.client = MagicMock()
    mock_poller.client.meter_id = 1042
    mock_poller.status = "connected"
    mock_poller.is_collector_owner = True
    mock_poller.last_success_at_utc = "2026-10-10T12:00:00Z"
    mock_poller.is_fetching = False
    mock_poller.get_status_info.return_value = {
        "status": "connected",
        "stale": False,
        "last_attempt_at": "2026-10-10T12:00:00Z",
        "last_success_at": "2026-10-10T12:00:00Z",
        "last_error_category": None,
        "last_error_message": None,
        "consecutive_failures": 0,
        "polling_enabled": True,
        "is_fetching": False,
    }

    test_app = create_app(
        test_config={
            "TESTING": True,
            "ELECTRO_DB_PATH": str(db_file),
            "POLLING_SERVICE": mock_poller,
        },
        start_poller=False,
    )
    return test_app, repo, mock_poller


@pytest.fixture
def client(app_and_repo):
    test_app, _, _ = app_and_repo
    return test_app.test_client()


def test_index_route(client):
    res = client.get("/")
    assert res.status_code == 200
    assert b"Electro" in res.data
    assert b"Personal Meter Dashboard" in res.data


def test_healthz_route(client):
    res = client.get("/healthz")
    assert res.status_code == 200
    data = res.get_json()
    assert data["status"] == "ok"
    assert data["app"] == "electro"
    assert "token" not in str(data).lower()
    assert "password" not in str(data).lower()


def test_api_dashboard_sanitized(client, app_and_repo):
    _, repo, _ = app_and_repo

    # Insert snapshot with test data
    repo.insert_snapshot({
        "observed_at_utc": "2026-10-10T12:00:00Z",
        "meter_id": 1042,
        "device_id": "1042",
        "meter_balance": 750.25,
        "relay_status": "ON",
        "kwh_rec_today": 14.2,
        "kvah_rec_today": 15.0,
        "kwh_del_today": 0.0,
        "solar_today": 0.0,
        "kvah_del_today": 0.0,
        "kwh_rec_month": 120.0,
        "kvah_rec_month": 125.0,
        "kwh_del_month": 0.0,
        "solar_month": 0.0,
        "kvah_del_month": 0.0,
        "current_day_eb": 70.0,
        "current_day_dg": 0.0,
        "current_day_solar": 0.0,
        "fixed_charges_today": 10.0,
        "current_month_eb": 700.0,
        "current_month_dg": 0.0,
        "current_month_solar": 0.0,
        "fixed_charges_month": 100.0,
        "is_eb": True,
        "is_dg": True,
        "is_solar": False,
        "is_fixed_charge": True,
        "is_coil_enable": True,
        "eb_dg_status": 1,
        "kwh_kvah_status": 1,
        "meter_sn": "SECRET_SERIAL",
    })

    res = client.get("/api/dashboard")
    assert res.status_code == 200
    data = res.get_json()

    assert data["status"] == "connected"
    assert data["data"]["meter_balance"] == 750.25
    assert data["data"]["kwh_rec_today"] == 14.2
    assert data["data"]["eb_dg_status"] == 1
    assert data["data"]["power_source"]["source"] == "DG"
    assert data["data"]["relay_info"]["status"] == "ON"

    # Verify sensitive fields are NOT in output
    raw_str = res.get_data(as_text=True)
    assert "SECRET_SERIAL" not in raw_str
    assert "token" not in raw_str.lower()
    assert "password" not in raw_str.lower()
    assert "skey" not in raw_str.lower()


def test_api_history_valid_and_invalid(client, app_and_repo):
    _, repo, _ = app_and_repo

    # Insert two snapshots with a power source transition from Grid (0) to DG (1)
    base_snapshot = {
        "meter_id": 1042,
        "device_id": "1042",
        "meter_balance": 700.0,
        "relay_status": "ON",
        "kwh_rec_today": 10.0,
        "kvah_rec_today": 10.0,
        "kwh_del_today": 0.0,
        "solar_today": 0.0,
        "kvah_del_today": 0.0,
        "kwh_rec_month": 100.0,
        "kvah_rec_month": 100.0,
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
        "is_dg": True,
        "is_solar": False,
        "is_fixed_charge": True,
        "is_coil_enable": True,
        "kwh_kvah_status": 1,
        "meter_sn": None,
    }

    # Point 1: Grid (0)
    p1 = dict(base_snapshot)
    p1["observed_at_utc"] = "2026-10-10T10:00:00Z"
    p1["eb_dg_status"] = 0
    repo.insert_snapshot(p1)

    # Point 2: Generator (1)
    p2 = dict(base_snapshot)
    p2["observed_at_utc"] = "2026-10-10T10:05:00Z"
    p2["eb_dg_status"] = 1
    repo.insert_snapshot(p2)

    # Valid range 24h
    res = client.get("/api/history?range=24h")
    assert res.status_code == 200
    data = res.get_json()
    assert data["range"] == "24h"
    assert "points" in data
    assert len(data["points"]) >= 2
    assert "transitions" in data
    assert len(data["transitions"]) == 1
    transition = data["transitions"][0]
    assert transition["from_code"] == 0
    assert transition["to_code"] == 1
    assert transition["from_source"]["source"] == "GRID"
    assert transition["to_source"]["source"] == "DG"

    # Invalid range parameter
    res_bad = client.get("/api/history?range=999y")
    assert res_bad.status_code == 400
    assert "error" in res_bad.get_json()



def test_api_refresh_success(client, app_and_repo):
    _, _, mock_poller = app_and_repo
    mock_poller.is_fetching = False
    mock_poller.fetch_once.return_value = (True, {"meter_balance": 800.0}, None)

    res = client.post("/api/refresh")
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    mock_poller.fetch_once.assert_called_once_with(manual=True)


def test_api_refresh_already_in_progress(client, app_and_repo):
    _, _, mock_poller = app_and_repo
    mock_poller.is_fetching = True

    res = client.post("/api/refresh")
    assert res.status_code == 409
    data = res.get_json()
    assert data["success"] is False
    assert "already in progress" in data["error"]


def test_api_health_endpoint(client, app_and_repo):
    res = client.get("/api/health")
    assert res.status_code == 200
    data = res.get_json()
    assert data["status"] in ("healthy", "degraded")
    assert data["app"] == "electro"
    assert "telemetry" in data
    assert "poll_stats_24h" in data
    assert "database" in data
    assert data["database"]["ok"] is True


def test_api_export_csv_success(client, app_and_repo):
    _, repo, _ = app_and_repo
    repo.insert_snapshot({
        "observed_at_utc": "2026-10-10T12:00:00Z",
        "meter_id": 1042,
        "device_id": "1042",
        "meter_balance": 880.0,
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
        "eb_dg_status": 0,
        "kwh_kvah_status": 1,
        "meter_sn": None,
    })

    res = client.get("/api/export/csv?range=24h")
    assert res.status_code == 200
    assert "text/csv" in res.content_type
    assert "attachment; filename=" in res.headers.get("Content-Disposition", "")
    assert b"observed_at_utc,meter_id,power_source" in res.data
    assert b"1042,Grid" in res.data


def test_api_export_csv_invalid_range(client):
    res = client.get("/api/export/csv?range=bad_range")
    assert res.status_code == 400
    data = res.get_json()
    assert "error" in data


def test_api_token_auth_enforced(tmp_path):
    """Verify that when ELECTRO_API_TOKEN is configured, endpoints require authentication."""
    db_file = tmp_path / "test_auth.db"
    repo = SnapshotRepository(str(db_file))
    repo.init_db()

    app_with_auth = create_app(
        test_config={
            "TESTING": True,
            "ELECTRO_DB_PATH": str(db_file),
            "ELECTRO_API_TOKEN": "secret-test-token-12345",
        },
        start_poller=False,
    )
    auth_client = app_with_auth.test_client()

    # 1. Unauthenticated request to /api/dashboard should fail with 401
    res = auth_client.get("/api/dashboard")
    assert res.status_code == 401
    assert res.get_json()["error"] == "unauthorized"

    # 2. Public /healthz probe remains accessible without token
    res_healthz = auth_client.get("/healthz")
    assert res_healthz.status_code == 200

    # 3. Bad token should fail with 401
    res_bad = auth_client.get(
        "/api/dashboard",
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert res_bad.status_code == 401

    # 4. Valid Authorization: Bearer <token> succeeds
    res_bearer = auth_client.get(
        "/api/dashboard",
        headers={"Authorization": "Bearer secret-test-token-12345"},
    )
    assert res_bearer.status_code == 200

    # 5. Valid X-Electro-Token succeeds
    res_header = auth_client.get(
        "/api/dashboard",
        headers={"X-Electro-Token": "secret-test-token-12345"},
    )
    assert res_header.status_code == 200

    # 6. Valid ?token= query parameter succeeds
    res_query = auth_client.get(
        "/api/dashboard?token=secret-test-token-12345"
    )
    assert res_query.status_code == 200


def test_api_events(client, app_and_repo):
    _, repo, _ = app_and_repo
    repo.insert_snapshot({
        "observed_at_utc": "2026-10-10T10:00:00Z",
        "meter_id": 1042,
        "eb_dg_status": 0,
        "relay_status": "ON",
        "meter_balance": 1000.0,
    })
    repo.insert_snapshot({
        "observed_at_utc": "2026-10-10T10:15:00Z",
        "meter_id": 1042,
        "eb_dg_status": 1,
        "relay_status": "ON",
        "meter_balance": 980.0,
    })

    res = client.get("/api/events?limit=25")
    assert res.status_code == 200
    data = res.get_json()
    assert "events" in data
    assert "count" in data
    assert data["limit"] == 25
    assert len(data["events"]) == 1
    assert data["events"][0]["type"] == "source_change"
    assert data["events"][0]["severity"] == "alert"


def test_api_forecast(client, app_and_repo):
    from datetime import datetime, timezone, timedelta
    _, repo, _ = app_and_repo
    now = datetime.now(timezone.utc)
    t1 = (now - timedelta(hours=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
    t2 = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    repo.insert_snapshot({
        "observed_at_utc": t1,
        "meter_id": 1042,
        "meter_balance": 2000.0,
    })
    repo.insert_snapshot({
        "observed_at_utc": t2,
        "meter_id": 1042,
        "meter_balance": 1950.0,
    })

    res = client.get("/api/forecast?range=24h&threshold=500")
    assert res.status_code == 200
    data = res.get_json()
    assert data["status"] == "ok"
    assert data["current_balance"] == 1950.0
    assert data["hourly_burn_rate"] == 5.0
    assert data["daily_burn_rate"] == 120.0
    assert data["runway_hours"] == 390.0
    assert data["runway_days"] == 16.2
    assert data["low_balance_threshold"] == 500.0

