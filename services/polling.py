"""Background polling and status tracking service.

Ensures non-overlapping upstream queries, manages periodic fetching,
and tracks connection health without logging sensitive information.
"""

from typing import Optional, Dict, Any, Tuple
from datetime import datetime, timezone
import logging
import threading
import time

from elnet.client import ELNetClient
from elnet.schemas import ErrorCategory, ElnetError
from services.normalization import normalize_home_data
from storage.repository import SnapshotRepository

logger = logging.getLogger("electro.polling")


class PollingService:
    """Manages scheduled and on-demand polling with concurrency locking."""

    def __init__(
        self,
        client: ELNetClient,
        repository: SnapshotRepository,
        username: str = "",
        password: str = "",
        fcm_token: str = "",
        poll_interval_seconds: int = 60,
    ):
        self.client = client
        self.repository = repository
        self.username = username
        self.password = password
        self.fcm_token = fcm_token
        self.poll_interval_seconds = poll_interval_seconds

        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

        # State tracking
        self.status = "offline"  # connected | stale | authentication_required | offline
        self.last_attempt_at_utc: Optional[str] = None
        self.last_success_at_utc: Optional[str] = None
        self.last_error_category: Optional[str] = None
        self.last_error_message: Optional[str] = None
        self.consecutive_failures = 0
        self.polling_enabled = True
        self.is_fetching = False

    def get_status_info(self) -> Dict[str, Any]:
        """Return public status summary (safe for frontend)."""
        return {
            "status": self.status,
            "stale": self.status in ("stale", "offline"),
            "last_attempt_at": self.last_attempt_at_utc,
            "last_success_at": self.last_success_at_utc,
            "last_error_category": self.last_error_category,
            "last_error_message": self.last_error_message,
            "consecutive_failures": self.consecutive_failures,
            "polling_enabled": self.polling_enabled,
            "is_fetching": self.is_fetching,
        }

    def fetch_once(self, manual: bool = False) -> Tuple[bool, Optional[Dict[str, Any]], Optional[str]]:
        """Perform a single fetch cycle protected by a non-blocking lock.
        
        Returns (success_bool, normalized_snapshot_or_latest, error_msg_if_failed).
        """
        # Attempt to acquire lock without blocking indefinitely
        acquired = self._lock.acquire(blocking=False)
        if not acquired:
            logger.info("Fetch cycle skipped: another fetch is currently in progress")
            latest = self.repository.get_latest_snapshot(self.client.meter_id)
            return False, latest, "Another refresh is already in progress"

        self.is_fetching = True
        now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.last_attempt_at_utc = now_utc

        try:
            # Step 1: Ensure authenticated
            if not self.client.is_authenticated:
                if not self.username or not self.password:
                    self.status = "authentication_required"
                    self.last_error_category = ErrorCategory.CONFIGURATION_ERROR
                    self.last_error_message = "Credentials not configured in environment"
                    self.polling_enabled = False
                    return False, None, self.last_error_message

                logger.info("Client unauthenticated. Initiating upstream login...")
                self.client.login(self.username, self.password, self.fcm_token)

            # Step 2: Fetch HomeData
            target_meter_id = self.client.meter_id
            if target_meter_id is None:
                raise ElnetError("MeterID not discovered during login", category=ErrorCategory.CONFIGURATION_ERROR)

            raw_data = self.client.get_home_data(meter_id=target_meter_id)

            # Step 3: Normalize and persist snapshot
            snapshot = normalize_home_data(raw_data, meter_id=target_meter_id, observed_at_utc=now_utc)
            self.repository.insert_snapshot(snapshot)

            # Step 4: Update status
            self.last_success_at_utc = now_utc
            self.consecutive_failures = 0
            self.last_error_category = None
            self.last_error_message = None
            self.status = "connected"
            logger.info("Successfully fetched and stored snapshot for meter %s", target_meter_id)
            return True, snapshot, None

        except ElnetError as e:
            self.consecutive_failures += 1
            self.last_error_category = e.category
            self.last_error_message = e.message
            if e.category in (ErrorCategory.AUTHENTICATION_ERROR, ErrorCategory.CONFIGURATION_ERROR):
                self.status = "authentication_required"
                self.polling_enabled = False  # Avoid hammering auth endpoint
                logger.error("Authentication or configuration error; polling halted: %s", e.message)
            else:
                self.status = "stale"
                logger.warning("Upstream fetch failed (%s): %s", e.category, e.message)

            latest = self.repository.get_latest_snapshot(self.client.meter_id)
            return False, latest, e.message

        except Exception as e:
            self.consecutive_failures += 1
            self.last_error_category = ErrorCategory.UPSTREAM_APPLICATION_ERROR
            self.last_error_message = "Unexpected application error"
            self.status = "stale"
            logger.exception("Unexpected error during fetch cycle")
            latest = self.repository.get_latest_snapshot(self.client.meter_id)
            return False, latest, "Unexpected error"

        finally:
            self.is_fetching = False
            self._lock.release()

    def start(self) -> None:
        """Start the background polling worker thread."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, name="ELNetPoller", daemon=True)
        self._thread.start()
        logger.info("Background polling worker started (interval: %ds)", self.poll_interval_seconds)

    def stop(self) -> None:
        """Signal worker thread to terminate and wait."""
        self._stop_event.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=2.0)
            self._thread = None
        logger.info("Background polling worker stopped")

    def _run_loop(self) -> None:
        """Background worker loop."""
        # Initial fetch immediately upon startup
        if self.polling_enabled:
            self.fetch_once()

        while not self._stop_event.is_set():
            # Wait for interval or stop event
            if self._stop_event.wait(timeout=self.poll_interval_seconds):
                break
            if self.polling_enabled:
                self.fetch_once()
