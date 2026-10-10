"""Unit tests for services/normalization.py."""

import json
from pathlib import Path
import pytest

from services.normalization import normalize_home_data

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def homedata_fixture():
    with open(FIXTURES_DIR / "homedata_success.json", "r", encoding="utf-8") as f:
        return json.load(f)["Data"]


def test_normalize_complete_data(homedata_fixture):
    normalized = normalize_home_data(homedata_fixture, meter_id=1042)

    assert normalized["meter_id"] == 1042
    assert normalized["device_id"] == "1042"
    assert normalized["meter_balance"] == 845.5
    assert normalized["relay_status"] == "ON"
    assert normalized["kwh_rec_today"] == 12.5
    assert normalized["kwh_rec_month"] == 145.2
    assert normalized["current_day_eb"] == 85.0
    assert normalized["current_month_eb"] == 950.0
    assert normalized["is_eb"] is True
    assert normalized["is_dg"] is True
    assert normalized["is_solar"] is False
    assert normalized["meter_sn"] is None  # Sensitive operational identifier omitted
    assert "observed_at_utc" in normalized


def test_normalize_empty_and_null_data():
    empty_raw = {}
    normalized = normalize_home_data(empty_raw, meter_id=999)

    assert normalized["meter_id"] == 999
    assert normalized["device_id"] is None
    assert normalized["meter_balance"] is None
    assert normalized["relay_status"] is None
    assert normalized["kwh_rec_today"] is None
    assert normalized["is_eb"] is None
    assert normalized["eb_dg_status"] is None


def test_map_power_source():
    from services.normalization import map_power_source

    # EBDGStatus == 0 -> Main Source / Grid (EB)
    grid = map_power_source(0)
    assert grid["source"] == "GRID"
    assert "Main Source" in grid["name"]
    assert grid["code"] == 0

    # EBDGStatus == 1 -> Generator (DG)
    dg = map_power_source(1)
    assert dg["source"] == "DG"
    assert "Generator" in dg["name"]
    assert dg["code"] == 1

    # EBDGStatus == null / None -> Unknown
    unknown_null = map_power_source(None)
    assert unknown_null["source"] == "UNKNOWN"

    # EBDGStatus == any other value -> Unknown
    unknown_other = map_power_source(99)
    assert unknown_other["source"] == "UNKNOWN"
    assert unknown_other["code"] == 99


def test_map_relay_status():
    from services.normalization import map_relay_status

    assert map_relay_status("ON")["status"] == "ON"
    assert map_relay_status("on")["status"] == "ON"
    assert map_relay_status("OFF")["status"] == "OFF"
    assert map_relay_status(None)["status"] == "UNKNOWN"
    assert map_relay_status("TRIP")["status"] == "UNKNOWN"

