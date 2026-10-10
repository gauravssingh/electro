"""Flask web application for Electro."""

from typing import Optional, Dict, Any
import logging
import os
import sys

from flask import Flask, render_template, jsonify, request

from config import Config
from elnet.client import ELNetClient
from elnet.schemas import ErrorCategory
from storage.repository import SnapshotRepository
from services.polling import PollingService
from services.normalization import map_power_source, map_relay_status
from services.analytics import compute_source_duration_analytics


def setup_logging(log_level_name: str) -> None:
    """Configure structured, privacy-conscious logging."""
    level = getattr(logging, log_level_name, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] [%(name)s]: %(message)s",
        stream=sys.stdout,
    )


def create_app(
    test_config: Optional[Dict[str, Any]] = None,
    start_poller: bool = True,
) -> Flask:
    """Application factory for Electro."""
    app = Flask(__name__)

    # Default configuration
    app.config["ELECTRO_DB_PATH"] = Config.ELECTRO_DB_PATH
    app.config["ELECTRO_LOG_LEVEL"] = Config.ELECTRO_LOG_LEVEL
    app.config["ELNET_BASE_URL"] = Config.ELNET_BASE_URL
    app.config["ELNET_USERNAME"] = Config.ELNET_USERNAME
    app.config["ELNET_PASSWORD"] = Config.ELNET_PASSWORD
    app.config["ELNET_FCM_TOKEN"] = Config.ELNET_FCM_TOKEN
    app.config["ELNET_POLL_INTERVAL_SECONDS"] = Config.ELNET_POLL_INTERVAL_SECONDS
    app.config["ELNET_REQUEST_TIMEOUT_SECONDS"] = Config.ELNET_REQUEST_TIMEOUT_SECONDS

    if test_config:
        app.config.update(test_config)

    setup_logging(app.config["ELECTRO_LOG_LEVEL"])
    logger = logging.getLogger("electro.app")

    # Initialize repository
    repo = SnapshotRepository(app.config["ELECTRO_DB_PATH"])
    repo.init_db()
    app.repository = repo

    # Initialize client and polling service if keys configured
    poller = None
    try:
        skey, ivkey = Config.get_crypto_keys()
        client = ELNetClient(
            base_url=app.config["ELNET_BASE_URL"],
            skey=skey,
            ivkey=ivkey,
            timeout=app.config["ELNET_REQUEST_TIMEOUT_SECONDS"],
        )
        poller = PollingService(
            client=client,
            repository=repo,
            username=app.config["ELNET_USERNAME"],
            password=app.config["ELNET_PASSWORD"],
            fcm_token=app.config["ELNET_FCM_TOKEN"],
            poll_interval_seconds=app.config["ELNET_POLL_INTERVAL_SECONDS"],
        )
    except Exception as e:
        logger.warning("ELNet client/poller initialization deferred: %s", str(e))

    # Allow custom poller injection for testing
    if "POLLING_SERVICE" in app.config:
        poller = app.config["POLLING_SERVICE"]

    app.poller = poller

    if poller and start_poller and not app.config.get("TESTING", False):
        poller.start()

    # --- HTTP Routes ---

    @app.route("/")
    def index():
        """Render dashboard HTML shell."""
        return render_template("dashboard.html")

    @app.route("/api/dashboard")
    def api_dashboard():
        """Return latest normalized snapshot and polling connection status."""
        poller_status = app.poller.get_status_info() if app.poller else {
            "status": "offline",
            "stale": True,
            "last_attempt_at": None,
            "last_success_at": None,
            "last_error_category": "configuration_error",
            "last_error_message": "Cryptographic keys or client unconfigured",
            "consecutive_failures": 0,
            "polling_enabled": False,
            "is_fetching": False,
        }

        meter_id = app.poller.client.meter_id if (app.poller and app.poller.client) else None
        latest = app.repository.get_latest_snapshot(meter_id=meter_id)

        # Sanitize data: ensure no tokens, keys, passwords, or personal details
        data_clean = None
        if latest:
            eb_dg_status = latest.get("eb_dg_status")
            relay_status = latest.get("relay_status")
            data_clean = {
                "observed_at_utc": latest.get("observed_at_utc"),
                "meter_id": latest.get("meter_id"),
                "meter_balance": latest.get("meter_balance"),
                "relay_status": relay_status,
                "relay_info": map_relay_status(relay_status),
                "eb_dg_status": eb_dg_status,
                "power_source": map_power_source(eb_dg_status),
                "kwh_rec_today": latest.get("kwh_rec_today"),
                "kvah_rec_today": latest.get("kvah_rec_today"),
                "kwh_rec_month": latest.get("kwh_rec_month"),
                "kvah_rec_month": latest.get("kvah_rec_month"),
                "current_day_eb": latest.get("current_day_eb"),
                "current_day_dg": latest.get("current_day_dg"),
                "current_day_solar": latest.get("current_day_solar"),
                "fixed_charges_today": latest.get("fixed_charges_today"),
                "current_month_eb": latest.get("current_month_eb"),
                "current_month_dg": latest.get("current_month_dg"),
                "current_month_solar": latest.get("current_month_solar"),
                "fixed_charges_month": latest.get("fixed_charges_month"),
                "is_eb": latest.get("is_eb"),
                "is_dg": latest.get("is_dg"),
                "is_solar": latest.get("is_solar"),
                "is_fixed_charge": latest.get("is_fixed_charge"),
                "is_coil_enable": latest.get("is_coil_enable"),
            }

        return jsonify({
            "status": poller_status["status"],
            "stale": poller_status["stale"],
            "last_success_at": poller_status["last_success_at"],
            "last_attempt_at": poller_status["last_attempt_at"],
            "last_duration_ms": poller_status.get("last_duration_ms"),
            "age_seconds": poller_status.get("age_seconds"),
            "consecutive_failures": poller_status.get("consecutive_failures", 0),
            "next_retry_at": poller_status.get("next_retry_at"),
            "poll_interval_seconds": poller_status.get("poll_interval_seconds"),
            "last_error_category": poller_status["last_error_category"],
            "last_error_message": poller_status["last_error_message"],
            "is_fetching": poller_status["is_fetching"],
            "data": data_clean,
        })

    @app.route("/api/history")
    def api_history():
        """Return historical points for time series charting and trustworthy duration analytics."""
        range_str = request.args.get("range", "24h").lower()
        if range_str not in SnapshotRepository.ALLOWED_RANGES:
            return jsonify({
                "error": f"Invalid range parameter '{range_str}'. Allowed ranges: {list(SnapshotRepository.ALLOWED_RANGES.keys())}"
            }), 400

        meter_id = app.poller.client.meter_id if (app.poller and app.poller.client) else None
        history = app.repository.get_history(meter_id=meter_id, range_str=range_str)

        # Sanitize points
        points = []
        for row in history:
            current_status = row.get("eb_dg_status")
            relay_status = row.get("relay_status")
            timestamp = row.get("observed_at_utc")

            pt = {
                "observed_at_utc": timestamp,
                "meter_balance": row.get("meter_balance"),
                "kwh_rec_today": row.get("kwh_rec_today"),
                "kwh_rec_month": row.get("kwh_rec_month"),
                "current_day_eb": row.get("current_day_eb"),
                "current_month_eb": row.get("current_month_eb"),
                "relay_status": relay_status,
                "relay_info": map_relay_status(relay_status),
                "eb_dg_status": current_status,
                "power_source": map_power_source(current_status),
            }
            points.append(pt)

        # Compute source analytics using elapsed-time intervals and gap detection
        poll_interval = app.config.get("ELNET_POLL_INTERVAL_SECONDS", 15)
        analytics = compute_source_duration_analytics(points, poll_interval_seconds=poll_interval)

        return jsonify({
            "range": range_str,
            "count": len(points),
            "points": points,
            "transitions": analytics["transitions"],
            "analytics": analytics,
        })

    @app.route("/api/refresh", methods=["POST"])
    def api_refresh():
        """Trigger an on-demand read-only HomeData fetch."""
        if not app.poller:
            return jsonify({
                "success": False,
                "error": "Poller service is not configured"
            }), 503

        if app.poller.is_fetching:
            return jsonify({
                "success": False,
                "error": "Refresh already in progress"
            }), 409

        success, snapshot, error_msg = app.poller.fetch_once(manual=True)
        if not success:
            return jsonify({
                "success": False,
                "status": app.poller.status,
                "error": error_msg or "Failed to refresh data",
            }), 502 if app.poller.status in ("stale", "offline") else 401

        return jsonify({
            "success": True,
            "status": app.poller.status,
            "last_success_at": app.poller.last_success_at_utc,
        })

    @app.route("/healthz")
    def healthz():
        """Process liveness probe."""
        return jsonify({
            "status": "ok",
            "app": "electro",
        })

    @app.route("/api/health")
    def api_health():
        """Comprehensive service and telemetry health probe."""
        poller_info = app.poller.get_status_info() if app.poller else None
        stats_24h = app.repository.get_poll_health_stats(hours=24)
        total_snaps = app.repository.count_snapshots()

        status_label = "healthy"
        if not app.poller or not poller_info or not poller_info.get("polling_enabled"):
            status_label = "unhealthy"
        elif poller_info.get("stale") or (poller_info.get("consecutive_failures", 0) > 0):
            status_label = "degraded"

        payload = {
            "status": status_label,
            "app": "electro",
            "telemetry": poller_info,
            "poll_stats_24h": stats_24h,
            "database": {
                "ok": True,
                "snapshot_count": total_snaps,
                "db_path": app.config.get("ELECTRO_DB_PATH"),
            },
        }
        return jsonify(payload), 200 if status_label in ("healthy", "degraded") else 503

    return app


# Module-level WSGI application instance for production runners (e.g. Gunicorn / Waitress)
app = create_app(start_poller=True)


if __name__ == "__main__":
    import signal

    def handle_shutdown(signum, frame):
        logging.getLogger("electro.app").info("Received signal %s; shutting down...", signum)
        if hasattr(app, "poller") and app.poller:
            app.poller.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)

    # Bind strictly to loopback 127.0.0.1 for security
    app.run(
        host=Config.ELECTRO_HOST,
        port=Config.ELECTRO_PORT,
        debug=False,
    )
