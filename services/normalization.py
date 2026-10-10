"""Data normalization service.

Converts raw upstream ELNet HomeData responses into clean, strongly-typed internal models.
"""

from typing import Optional, Dict, Any
from datetime import datetime, timezone


def _to_float(val: Any) -> Optional[float]:
    if val is None or val == "":
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _to_int(val: Any) -> Optional[int]:
    if val is None or val == "":
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None


def _to_bool(val: Any) -> Optional[bool]:
    if val is None:
        return None
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return bool(val)
    if isinstance(val, str):
        v = val.strip().lower()
        if v in ("true", "1", "yes"):
            return True
        if v in ("false", "0", "no"):
            return False
    return None


def normalize_home_data(
    raw: Dict[str, Any],
    meter_id: int,
    observed_at_utc: Optional[str] = None,
) -> Dict[str, Any]:
    """Normalize raw HomeData dictionary into internal database snapshot format."""
    if not observed_at_utc:
        observed_at_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    device_id = raw.get("DeviceId")
    device_id_str = str(device_id) if device_id is not None else None

    relay_status = raw.get("RelStatus")
    relay_status_str = str(relay_status).strip() if relay_status is not None else None

    return {
        "observed_at_utc": observed_at_utc,
        "meter_id": int(meter_id),
        "device_id": device_id_str,
        "meter_balance": _to_float(raw.get("MeterBal")),
        "relay_status": relay_status_str,
        # Energy today
        "kwh_rec_today": _to_float(raw.get("kWhRecToday")),
        "kvah_rec_today": _to_float(raw.get("kvahRecToday")),
        "kwh_del_today": _to_float(raw.get("kWhDelToday")),
        "solar_today": _to_float(raw.get("SolarToday")),
        "kvah_del_today": _to_float(raw.get("kvahDelToday")),
        # Energy month
        "kwh_rec_month": _to_float(raw.get("kWhRecMonth")),
        "kvah_rec_month": _to_float(raw.get("kvahRecMonth")),
        "kwh_del_month": _to_float(raw.get("kWhDelMonth")),
        "solar_month": _to_float(raw.get("SolarMonth")),
        "kvah_del_month": _to_float(raw.get("kvahDelMonth")),
        # Cost today
        "current_day_eb": _to_float(raw.get("CurrentDay_EB")),
        "current_day_dg": _to_float(raw.get("CurrentDay_DG")),
        "current_day_solar": _to_float(raw.get("CurrentDay_Solar")),
        "fixed_charges_today": _to_float(raw.get("FixedChargesToday")),
        # Cost month
        "current_month_eb": _to_float(raw.get("CurrentMonth_EB")),
        "current_month_dg": _to_float(raw.get("CurrentMonth_DG")),
        "current_month_solar": _to_float(raw.get("CurrentMonth_Solar")),
        "fixed_charges_month": _to_float(raw.get("FixedChargesMonth")),
        # Source flags
        "is_eb": _to_bool(raw.get("IsEB")),
        "is_dg": _to_bool(raw.get("IsDG")),
        "is_solar": _to_bool(raw.get("IsSolar")),
        "is_fixed_charge": _to_bool(raw.get("IsFixedCharge")),
        "is_coil_enable": _to_bool(raw.get("IsCoilEnable")),
        # Status codes
        "eb_dg_status": _to_int(raw.get("EBDGStatus")),
        "kwh_kvah_status": _to_int(raw.get("kwh_kvah_Status")),
        # Meter serial number is excluded / kept null to minimize sensitive identifiers
        "meter_sn": None,
    }


def map_power_source(eb_dg_status: Optional[int]) -> Dict[str, Any]:
    """Map EBDGStatus code to power source description verified from HomeUserFragment.java.

    - EBDGStatus == 0: Main Source / Grid (EB)
    - EBDGStatus == 1: Generator (DG)
    - EBDGStatus == null or any other value: Unknown / Meter status unavailable
    """
    if eb_dg_status == 0:
        return {
            "code": 0,
            "source": "GRID",
            "name": "Main Source (Grid / EB)",
            "short_name": "Grid (EB)",
            "message": "Running on Main Source",
        }
    elif eb_dg_status == 1:
        return {
            "code": 1,
            "source": "DG",
            "name": "Generator (DG)",
            "short_name": "Generator (DG)",
            "message": "Running on DG",
        }
    else:
        return {
            "code": eb_dg_status,
            "source": "UNKNOWN",
            "name": "Unknown / Unavailable",
            "short_name": "Unknown",
            "message": "Meter status unavailable",
        }


def map_relay_status(rel_status: Optional[str]) -> Dict[str, Any]:
    """Map RelStatus code independently from power source.

    - "ON": Relay ON
    - "OFF": Relay OFF
    - Other/null: Unknown
    """
    if not rel_status:
        return {"code": None, "status": "UNKNOWN", "label": "Unknown"}
    s = str(rel_status).strip().upper()
    if s == "ON":
        return {"code": "ON", "status": "ON", "label": "Relay ON"}
    elif s == "OFF":
        return {"code": "OFF", "status": "OFF", "label": "Relay OFF"}
    else:
        return {"code": s, "status": "UNKNOWN", "label": f"Relay {s}"}

