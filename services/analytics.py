"""Power source duration analytics, transition timelines, and gap detection.

Computes duration from elapsed intervals between adjacent observations
rather than multiplying snapshot counts by a static interval.
"""

from typing import List, Dict, Any, Optional
from datetime import datetime, timezone

from services.normalization import map_power_source


def parse_timestamp_utc(ts_str: str) -> Optional[datetime]:
    """Parse ISO UTC timestamp into datetime object."""
    if not ts_str:
        return None
    try:
        clean = ts_str.replace("Z", "+00:00")
        return datetime.fromisoformat(clean).astimezone(timezone.utc)
    except Exception:
        return None


def compute_source_duration_analytics(
    points: List[Dict[str, Any]],
    poll_interval_seconds: int = 15,
    max_gap_seconds: Optional[float] = None,
) -> Dict[str, Any]:
    """Calculate elapsed source durations, transitions, and missing-data coverage.

    Rules:
    - Calculates duration from adjacent chronological observations.
    - If delta between adjacent points exceeds `max_gap_seconds`, it is classified
      as an unobserved gap (e.g. system sleep, network outage, or service restart).
    - When source changes within a normal polling interval, the interval is split
      equally between the previous and new source.
    - Unknown or unexpected EBDGStatus values are classified as unknown, not Grid or DG.
    - Coverage is reported as monitored time vs total time span.
    """
    if max_gap_seconds is None:
        max_gap_seconds = max(poll_interval_seconds * 2.5, 45.0)

    if not points:
        return {
            "total_snapshots": 0,
            "grid_snapshots": 0,
            "dg_snapshots": 0,
            "unknown_snapshots": 0,
            "grid_seconds": 0.0,
            "dg_seconds": 0.0,
            "unknown_seconds": 0.0,
            "gap_seconds": 0.0,
            "estimated_grid_minutes": 0.0,
            "estimated_dg_minutes": 0.0,
            "gap_minutes": 0.0,
            "unknown_minutes": 0.0,
            "telemetry_coverage_pct": 100.0,
            "grid_availability_pct": 100.0,
            "total_transitions": 0,
            "poll_interval_seconds": poll_interval_seconds,
            "max_gap_seconds": max_gap_seconds,
            "transitions": [],
        }

    # Count snapshot source classifications
    grid_count = sum(1 for p in points if p.get("eb_dg_status") == 0)
    dg_count = sum(1 for p in points if p.get("eb_dg_status") == 1)
    unknown_count = len(points) - grid_count - dg_count

    grid_seconds = 0.0
    dg_seconds = 0.0
    unknown_seconds = 0.0
    gap_seconds = 0.0
    transitions: List[Dict[str, Any]] = []

    for i in range(len(points) - 1):
        curr_pt = points[i]
        next_pt = points[i + 1]

        t_curr = parse_timestamp_utc(curr_pt.get("observed_at_utc", ""))
        t_next = parse_timestamp_utc(next_pt.get("observed_at_utc", ""))

        if not t_curr or not t_next:
            continue

        dt = (t_next - t_curr).total_seconds()
        if dt <= 0:
            continue

        s_curr = curr_pt.get("eb_dg_status")
        s_next = next_pt.get("eb_dg_status")

        # Check if source transitioned
        if s_curr is not None and s_next is not None and s_curr != s_next:
            transitions.append({
                "observed_at_utc": next_pt.get("observed_at_utc"),
                "from_code": s_curr,
                "to_code": s_next,
                "detection_window_seconds": round(dt, 1),
                "is_gap": dt > max_gap_seconds,
                "from_source": map_power_source(s_curr),
                "to_source": map_power_source(s_next),
            })

        # Evaluate elapsed time
        if dt > max_gap_seconds:
            # Missing data gap
            gap_seconds += dt
        else:
            # Monitored interval
            if s_curr == s_next:
                if s_curr == 0:
                    grid_seconds += dt
                elif s_curr == 1:
                    dg_seconds += dt
                else:
                    unknown_seconds += dt
            else:
                # Transition occurred within monitored interval: split equally
                half = dt / 2.0
                for s in (s_curr, s_next):
                    if s == 0:
                        grid_seconds += half
                    elif s == 1:
                        dg_seconds += half
                    else:
                        unknown_seconds += half

    monitored_seconds = grid_seconds + dg_seconds + unknown_seconds
    total_span_seconds = monitored_seconds + gap_seconds

    if total_span_seconds > 0:
        coverage_pct = round((monitored_seconds / total_span_seconds) * 100, 1)
    else:
        coverage_pct = 100.0

    confirmed_sources_seconds = grid_seconds + dg_seconds
    if confirmed_sources_seconds > 0:
        grid_availability_pct = round((grid_seconds / confirmed_sources_seconds) * 100, 1)
    else:
        grid_availability_pct = 100.0

    # Balance and consumption delta calculations across the window
    balance_start = None
    balance_end = None
    balance_change = None
    kwh_start = None
    kwh_end = None
    consumption_kwh = None

    if points:
        first_bal = points[0].get("meter_balance")
        last_bal = points[-1].get("meter_balance")
        if first_bal is not None and last_bal is not None:
            balance_start = round(float(first_bal), 2)
            balance_end = round(float(last_bal), 2)
            balance_change = round(balance_end - balance_start, 2)

        first_kwh = points[0].get("kwh_rec_month")
        last_kwh = points[-1].get("kwh_rec_month")
        if first_kwh is not None and last_kwh is not None:
            kwh_start = round(float(first_kwh), 2)
            kwh_end = round(float(last_kwh), 2)
            if kwh_end >= kwh_start:
                consumption_kwh = round(kwh_end - kwh_start, 2)

    return {
        "total_snapshots": len(points),
        "grid_snapshots": grid_count,
        "dg_snapshots": dg_count,
        "unknown_snapshots": unknown_count,
        "grid_seconds": round(grid_seconds, 1),
        "dg_seconds": round(dg_seconds, 1),
        "unknown_seconds": round(unknown_seconds, 1),
        "gap_seconds": round(gap_seconds, 1),
        "estimated_grid_minutes": round(grid_seconds / 60.0, 1),
        "estimated_dg_minutes": round(dg_seconds / 60.0, 1),
        "gap_minutes": round(gap_seconds / 60.0, 1),
        "unknown_minutes": round(unknown_seconds / 60.0, 1),
        "telemetry_coverage_pct": coverage_pct,
        "grid_availability_pct": grid_availability_pct,
        "total_transitions": len(transitions),
        "poll_interval_seconds": poll_interval_seconds,
        "max_gap_seconds": max_gap_seconds,
        "transitions": transitions,
        "balance_start": balance_start,
        "balance_end": balance_end,
        "balance_change": balance_change,
        "kwh_start": kwh_start,
        "kwh_end": kwh_end,
        "consumption_kwh": consumption_kwh,
    }


def compute_balance_forecast(
    points: List[Dict[str, Any]],
    low_balance_threshold: float = 500.0,
) -> Dict[str, Any]:
    """Calculate hourly/daily burn rate, projected runway, and low-balance horizon.

    Recharge-resilient: Ignores upward balance jumps (top-ups) so burn rate reflects
    actual power consumption rate rather than net delta.
    """
    from datetime import timedelta

    thresh = float(low_balance_threshold)

    valid_points = []
    for p in points:
        b = p.get("meter_balance")
        ts = p.get("observed_at_utc")
        if b is not None and ts:
            try:
                valid_points.append({
                    "balance": float(b),
                    "timestamp": parse_timestamp_utc(ts),
                    "iso": ts,
                })
            except (ValueError, TypeError):
                pass

    if len(valid_points) < 2:
        curr_bal = valid_points[-1]["balance"] if valid_points else None
        return {
            "status": "insufficient_data",
            "current_balance": curr_bal,
            "hourly_burn_rate": 0.0,
            "daily_burn_rate": 0.0,
            "runway_hours": None,
            "runway_days": None,
            "projected_depletion_utc": None,
            "projected_threshold_utc": None,
            "days_to_threshold": None,
            "hours_to_threshold": None,
            "low_balance_threshold": thresh,
            "sample_hours": 0.0,
            "sample_points": len(valid_points),
        }

    t_first = valid_points[0]["timestamp"]
    t_last = valid_points[-1]["timestamp"]
    total_hours = (t_last - t_first).total_seconds() / 3600.0

    if total_hours < 0.05:  # Less than 3 minutes
        return {
            "status": "insufficient_data",
            "current_balance": valid_points[-1]["balance"],
            "hourly_burn_rate": 0.0,
            "daily_burn_rate": 0.0,
            "runway_hours": None,
            "runway_days": None,
            "projected_depletion_utc": None,
            "projected_threshold_utc": None,
            "days_to_threshold": None,
            "hours_to_threshold": None,
            "low_balance_threshold": thresh,
            "sample_hours": round(total_hours, 2),
            "sample_points": len(valid_points),
        }

    total_consumed = 0.0
    valid_hours = 0.0

    for i in range(len(valid_points) - 1):
        p1 = valid_points[i]
        p2 = valid_points[i + 1]
        dt_hr = (p2["timestamp"] - p1["timestamp"]).total_seconds() / 3600.0
        if dt_hr <= 0:
            continue

        valid_hours += dt_hr
        b1 = p1["balance"]
        b2 = p2["balance"]

        if b1 > b2:
            drop = b1 - b2
            if drop < 10000.0:  # Exclude corrupt spikes
                total_consumed += drop

    if valid_hours > 0 and total_consumed > 0:
        hourly_burn = round(total_consumed / valid_hours, 3)
        daily_burn = round(hourly_burn * 24.0, 2)
    else:
        hourly_burn = 0.0
        daily_burn = 0.0

    curr_bal = valid_points[-1]["balance"]
    runway_hours = None
    runway_days = None
    projected_depletion = None
    hours_to_thresh = None
    days_to_thresh = None
    projected_thresh = None

    if hourly_burn > 0:
        status = "ok"
        runway_hours = round(curr_bal / hourly_burn, 1)
        runway_days = round(runway_hours / 24.0, 1)
        depletion_dt = t_last + timedelta(hours=runway_hours)
        projected_depletion = depletion_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

        if curr_bal <= thresh:
            hours_to_thresh = 0.0
            days_to_thresh = 0.0
            projected_thresh = valid_points[-1]["iso"]
        else:
            hours_to_thresh = round((curr_bal - thresh) / hourly_burn, 1)
            days_to_thresh = round(hours_to_thresh / 24.0, 1)
            thresh_dt = t_last + timedelta(hours=hours_to_thresh)
            projected_thresh = thresh_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    else:
        status = "zero_burn"

    return {
        "status": status,
        "current_balance": curr_bal,
        "hourly_burn_rate": hourly_burn,
        "daily_burn_rate": daily_burn,
        "runway_hours": runway_hours,
        "runway_days": runway_days,
        "projected_depletion_utc": projected_depletion,
        "projected_threshold_utc": projected_thresh,
        "days_to_threshold": days_to_thresh,
        "hours_to_threshold": hours_to_thresh,
        "low_balance_threshold": thresh,
        "sample_hours": round(total_hours, 2),
        "sample_points": len(valid_points),
    }
