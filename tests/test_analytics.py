"""Tests for source duration analytics, transitions, and gap detection."""

from services.analytics import compute_source_duration_analytics


def test_analytics_empty_points():
    res = compute_source_duration_analytics([], poll_interval_seconds=15)
    assert res["total_snapshots"] == 0
    assert res["grid_seconds"] == 0.0
    assert res["dg_seconds"] == 0.0
    assert res["gap_seconds"] == 0.0
    assert res["telemetry_coverage_pct"] == 100.0
    assert res["total_transitions"] == 0


def test_analytics_continuous_grid_readings():
    # 5 snapshots at 15-second intervals on Grid (eb_dg_status=0)
    # Total elapsed time = 4 * 15s = 60s
    points = [
        {"observed_at_utc": "2026-10-10T12:00:00Z", "eb_dg_status": 0},
        {"observed_at_utc": "2026-10-10T12:00:15Z", "eb_dg_status": 0},
        {"observed_at_utc": "2026-10-10T12:00:30Z", "eb_dg_status": 0},
        {"observed_at_utc": "2026-10-10T12:00:45Z", "eb_dg_status": 0},
        {"observed_at_utc": "2026-10-10T12:01:00Z", "eb_dg_status": 0},
    ]

    res = compute_source_duration_analytics(points, poll_interval_seconds=15, max_gap_seconds=45.0)

    assert res["total_snapshots"] == 5
    assert res["grid_snapshots"] == 5
    assert res["dg_snapshots"] == 0
    assert res["grid_seconds"] == 60.0
    assert res["dg_seconds"] == 0.0
    assert res["gap_seconds"] == 0.0
    assert res["estimated_grid_minutes"] == 1.0
    assert res["estimated_dg_minutes"] == 0.0
    assert res["telemetry_coverage_pct"] == 100.0
    assert res["grid_availability_pct"] == 100.0
    assert res["total_transitions"] == 0


def test_analytics_source_transitions_and_split():
    # Grid -> DG transition at 12:00:30 (interval 12:00:15 to 12:00:30 is 15s)
    # DG remains until 12:00:45 (15s)
    points = [
        {"observed_at_utc": "2026-10-10T12:00:00Z", "eb_dg_status": 0},
        {"observed_at_utc": "2026-10-10T12:00:15Z", "eb_dg_status": 0},
        {"observed_at_utc": "2026-10-10T12:00:30Z", "eb_dg_status": 1},
        {"observed_at_utc": "2026-10-10T12:00:45Z", "eb_dg_status": 1},
    ]

    res = compute_source_duration_analytics(points, poll_interval_seconds=15, max_gap_seconds=45.0)

    assert res["total_snapshots"] == 4
    assert res["total_transitions"] == 1
    trans = res["transitions"][0]
    assert trans["from_code"] == 0
    assert trans["to_code"] == 1
    assert trans["detection_window_seconds"] == 15.0

    # 0 to 15s: 15s Grid
    # 15 to 30s: 7.5s Grid + 7.5s DG (transition split)
    # 30 to 45s: 15s DG
    # Total Grid = 22.5s, Total DG = 22.5s
    assert res["grid_seconds"] == 22.5
    assert res["dg_seconds"] == 22.5
    assert res["grid_availability_pct"] == 50.0
    assert res["gap_seconds"] == 0.0


def test_analytics_gap_detection_does_not_overcount():
    # Two snapshots separated by 3600 seconds (1 hour outage/sleep)
    # The 1 hour gap must NOT be attributed to Grid or DG!
    points = [
        {"observed_at_utc": "2026-10-10T10:00:00Z", "eb_dg_status": 1},
        {"observed_at_utc": "2026-10-10T10:00:15Z", "eb_dg_status": 1},
        # 1 hour gap
        {"observed_at_utc": "2026-10-10T11:00:15Z", "eb_dg_status": 1},
        {"observed_at_utc": "2026-10-10T11:00:30Z", "eb_dg_status": 1},
    ]

    res = compute_source_duration_analytics(points, poll_interval_seconds=15, max_gap_seconds=45.0)

    # 15s before gap + 15s after gap = 30s DG
    assert res["dg_seconds"] == 30.0
    assert res["grid_seconds"] == 0.0
    assert res["gap_seconds"] == 3600.0
    assert res["gap_minutes"] == 60.0
    # Coverage should show only ~0.8% coverage
    assert res["telemetry_coverage_pct"] < 2.0


def test_analytics_unknown_ebdg_status():
    points = [
        {"observed_at_utc": "2026-10-10T12:00:00Z", "eb_dg_status": None},
        {"observed_at_utc": "2026-10-10T12:00:15Z", "eb_dg_status": None},
        {"observed_at_utc": "2026-10-10T12:00:30Z", "eb_dg_status": 99},
    ]

    res = compute_source_duration_analytics(points, poll_interval_seconds=15, max_gap_seconds=45.0)

    assert res["grid_seconds"] == 0.0
    assert res["dg_seconds"] == 0.0
    assert res["unknown_seconds"] == 30.0
    assert res["unknown_snapshots"] == 3


def test_analytics_balance_and_consumption_deltas():
    points = [
        {
            "observed_at_utc": "2026-10-10T12:00:00Z",
            "eb_dg_status": 0,
            "meter_balance": 1500.0,
            "kwh_rec_month": 120.5,
        },
        {
            "observed_at_utc": "2026-10-10T12:00:30Z",
            "eb_dg_status": 0,
            "meter_balance": 1490.0,
            "kwh_rec_month": 122.0,
        },
    ]

    res = compute_source_duration_analytics(points, poll_interval_seconds=15)

    assert res["balance_start"] == 1500.0
    assert res["balance_end"] == 1490.0
    assert res["balance_change"] == -10.0
    assert res["kwh_start"] == 120.5
    assert res["kwh_end"] == 122.0
    assert res["consumption_kwh"] == 1.5


def test_balance_forecast_insufficient_data():
    from services.analytics import compute_balance_forecast
    assert compute_balance_forecast([])["status"] == "insufficient_data"
    assert compute_balance_forecast([{"observed_at_utc": "2026-10-10T12:00:00Z", "meter_balance": 1000.0}])["status"] == "insufficient_data"


def test_balance_forecast_normal_consumption():
    from services.analytics import compute_balance_forecast
    points = [
        {"observed_at_utc": "2026-10-10T00:00:00Z", "meter_balance": 2000.0},
        {"observed_at_utc": "2026-10-10T10:00:00Z", "meter_balance": 1950.0},
    ]
    res = compute_balance_forecast(points, low_balance_threshold=500.0)

    assert res["status"] == "ok"
    assert res["current_balance"] == 1950.0
    assert res["hourly_burn_rate"] == 5.0
    assert res["daily_burn_rate"] == 120.0
    assert res["runway_hours"] == 390.0
    assert res["runway_days"] == 16.2
    assert res["hours_to_threshold"] == 290.0
    assert res["days_to_threshold"] == 12.1
    assert res["projected_depletion_utc"] == "2026-10-26T16:00:00Z"


def test_balance_forecast_recharge_resilience():
    from services.analytics import compute_balance_forecast
    points = [
        {"observed_at_utc": "2026-10-10T00:00:00Z", "meter_balance": 1000.0},
        {"observed_at_utc": "2026-10-10T05:00:00Z", "meter_balance": 975.0},    # 25 spent over 5h
        {"observed_at_utc": "2026-10-10T05:01:00Z", "meter_balance": 2000.0},   # +1025 recharge
        {"observed_at_utc": "2026-10-10T10:00:00Z", "meter_balance": 1975.0},   # 25 spent over ~5h
    ]
    res = compute_balance_forecast(points, low_balance_threshold=500.0)

    assert res["status"] == "ok"
    assert res["current_balance"] == 1975.0
    assert round(res["hourly_burn_rate"], 1) == 5.0  # 50 total spent over 10h
    assert res["daily_burn_rate"] == 120.0
    assert res["runway_hours"] == 395.0


def test_balance_forecast_already_below_threshold():
    from services.analytics import compute_balance_forecast
    points = [
        {"observed_at_utc": "2026-10-10T00:00:00Z", "meter_balance": 450.0},
        {"observed_at_utc": "2026-10-10T05:00:00Z", "meter_balance": 400.0},
    ]
    res = compute_balance_forecast(points, low_balance_threshold=500.0)

    assert res["status"] == "ok"
    assert res["current_balance"] == 400.0
    assert res["hourly_burn_rate"] == 10.0
    assert res["hours_to_threshold"] == 0.0
    assert res["days_to_threshold"] == 0.0
    assert res["projected_threshold_utc"] == "2026-10-10T05:00:00Z"
