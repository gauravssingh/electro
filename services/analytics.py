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
    }
