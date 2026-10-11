"""Background polling and status tracking service.

Ensures non-overlapping upstream queries, manages periodic fetching using a
fixed monotonic cadence, records poll telemetry/failures to SQLite, and recovers
gracefully from expired sessions and transient network outages.
"""

from typing import Optional, Dict, Any, Tuple
from datetime import datetime, timezone, timedelta
from pathlib import Path
import logging
import threading
import time
import fcntl

from elnet.client import ELNetClient
from elnet.schemas import ErrorCategory, ElnetError
from services.normalization import normalize_home_data
from storage.repository import SnapshotRepository

logger = logging.getLogger("electro.polling")


class PollingService:
    """Manages scheduled and on-demand polling with concurrency locking and backoff."""

    def __init__(
        self,
        client: ELNetClient,
        repository: SnapshotRepository,
        username: str = "",
        password: str = "",
        fcm_token: str = "",
        poll_interval_seconds: int = 15,
        notifier: Optional[Any] = None,
    ):
        self.client = client
        self.repository = repository
        self.username = username
        self.password = password
        self.fcm_token = fcm_token
        self.poll_interval_seconds = max(5, int(poll_interval_seconds))
        self.notifier = notifier

        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock_file: Optional[Any] = None
        self._lock_path = Path(repository.db_path).parent / ".electro.poller.lock"
        self.is_collector_owner: bool = True

        # State tracking: initializing | connected | stale | authentication_required | configuration_error | standby | offline
        self.status = "initializing"
        self.last_attempt_at_utc: Optional[str] = None
        self.last_success_at_utc: Optional[str] = None
        self.last_duration_ms: Optional[int] = None
        self.last_error_category: Optional[str] = None
        self.last_error_message: Optional[str] = None
        self.consecutive_failures = 0
        self.next_retry_at_utc: Optional[str] = None
        self.next_scheduled_poll_at_utc: Optional[str] = None
        self._backoff_until_monotonic: float = 0.0

        self.polling_enabled = True
        self.is_fetching = False

    def get_status_info(self) -> Dict[str, Any]:
        """Return public status summary (safe for frontend and health checks)."""
        last_success = self.last_success_at_utc
        if not last_success and self.repository:
            try:
                latest = self.repository.get_latest_snapshot()
                if latest and latest.get("observed_at_utc"):
                    last_success = latest.get("observed_at_utc")
            except Exception:
                pass

        age_seconds: Optional[float] = None
        if last_success:
            try:
                last_dt = datetime.fromisoformat(last_success.replace("Z", "+00:00"))
                age_seconds = round((datetime.now(timezone.utc) - last_dt).total_seconds(), 1)
            except Exception:
                pass

        max_fresh_age = max(self.poll_interval_seconds * 2.5, 45.0)
        is_stale = (
            self.status in ("stale", "offline", "authentication_required", "configuration_error")
            or (age_seconds is not None and age_seconds > max_fresh_age)
            or (last_success is None)
        )

        return {
            "status": self.status,
            "stale": is_stale,
            "last_attempt_at": self.last_attempt_at_utc,
            "last_success_at": last_success,
            "last_duration_ms": self.last_duration_ms,
            "age_seconds": age_seconds,
            "freshness_threshold_seconds": max_fresh_age,
            "last_error_category": self.last_error_category,
            "last_error_message": self.last_error_message,
            "consecutive_failures": self.consecutive_failures,
            "next_retry_at": self.next_retry_at_utc,
            "next_scheduled_poll_at": self.next_scheduled_poll_at_utc,
            "polling_enabled": self.polling_enabled,
            "poll_interval_seconds": self.poll_interval_seconds,
            "is_fetching": self.is_fetching,
            "is_collector_owner": getattr(self, "is_collector_owner", False),
            "collector_running": self._thread is not None and self._thread.is_alive(),
        }

    def _calculate_backoff_seconds(self) -> float:
        """Compute exponential backoff delay based on consecutive failures."""
        if self.consecutive_failures <= 0:
            return 0.0
        # Exponent caps at 5 (multiplier up to 32x) with max backoff cap of 120s
        multiplier = 2 ** min(self.consecutive_failures - 1, 5)
        delay = float(min(self.poll_interval_seconds * multiplier, 120))
        return delay

    def fetch_once(self, manual: bool = False) -> Tuple[bool, Optional[Dict[str, Any]], Optional[str]]:
        """Perform a single fetch cycle protected by in-process and cross-process locks.

        Returns (success_bool, normalized_snapshot_or_latest, error_msg_if_failed).
        """
        if not self.is_collector_owner:
            latest = self.repository.get_latest_snapshot(self.client.meter_id if self.client else None)
            return False, latest, "Another process holds the collector lock"

        acquired = self._lock.acquire(blocking=False)
        if not acquired:
            logger.info("Fetch cycle skipped: another fetch is currently in progress")
            latest = self.repository.get_latest_snapshot(self.client.meter_id if self.client else None)
            return False, latest, "Another refresh is already in progress"

        temp_lock_file = None
        if self._lock_file is None:
            # We don't hold the long-lived process lock. Try acquiring advisory lock temporarily.
            try:
                if not self._lock_path.parent.exists():
                    self._lock_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                temp_lock_file = open(self._lock_path, "a+")
                try:
                    self._lock_path.chmod(0o600)
                except OSError:
                    pass
                fcntl.flock(temp_lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except (BlockingIOError, OSError):
                if temp_lock_file:
                    try:
                        temp_lock_file.close()
                    except Exception:
                        pass
                self._lock.release()
                self.is_collector_owner = False
                self.status = "standby"
                logger.info("Fetch skipped: another process holds the collector lock")
                latest = self.repository.get_latest_snapshot(self.client.meter_id if self.client else None)
                return False, latest, "Another process holds the collector lock"

        self.is_fetching = True
        start_monotonic = time.monotonic()
        attempted_at_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.last_attempt_at_utc = attempted_at_utc
        target_meter_id = self.client.meter_id if self.client else None

        try:
            # Step 1: Ensure authenticated
            if not self.client.is_authenticated:
                if not self.username or not self.password:
                    self.status = "configuration_error"
                    self.last_error_category = ErrorCategory.CONFIGURATION_ERROR
                    self.last_error_message = "Credentials not configured in environment"
                    self.polling_enabled = False  # Permanent configuration issue
                    self.repository.record_poll_event(
                        attempted_at_utc=attempted_at_utc,
                        success=False,
                        duration_ms=0,
                        error_category=ErrorCategory.CONFIGURATION_ERROR,
                        error_message=self.last_error_message,
                        meter_id=None,
                    )
                    return False, None, self.last_error_message

                logger.info("Client unauthenticated. Initiating upstream login...")
                self.client.login(self.username, self.password, self.fcm_token)
                target_meter_id = self.client.meter_id

            # Step 2: Fetch HomeData
            if target_meter_id is None:
                raise ElnetError("MeterID not discovered during login", category=ErrorCategory.CONFIGURATION_ERROR)

            raw_data = self.client.get_home_data(meter_id=target_meter_id)
            received_at_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

            # Step 3: Normalize and persist snapshot
            snapshot = normalize_home_data(
                raw_data,
                meter_id=target_meter_id,
                attempted_at_utc=attempted_at_utc,
                received_at_utc=received_at_utc,
            )
            self.repository.insert_snapshot(snapshot)

            # Compute request duration
            duration_ms = int((time.monotonic() - start_monotonic) * 1000)
            self.last_duration_ms = duration_ms

            # Step 4: Record success event and reset failure state
            self.repository.record_poll_event(
                attempted_at_utc=attempted_at_utc,
                success=True,
                duration_ms=duration_ms,
                error_category=None,
                error_message=None,
                meter_id=target_meter_id,
            )

            self.last_success_at_utc = received_at_utc
            self.consecutive_failures = 0
            self.last_error_category = None
            self.last_error_message = None
            self.next_retry_at_utc = None
            self._backoff_until_monotonic = 0.0
            self.status = "connected"
            if self.notifier:
                try:
                    self.notifier.on_snapshot(snapshot)
                except Exception as n_err:
                    logger.debug("Desktop notifier error on snapshot: %s", n_err)
            logger.info("Successfully captured snapshot for meter %s in %dms", target_meter_id, duration_ms)
            return True, snapshot, None

        except ElnetError as e:
            duration_ms = int((time.monotonic() - start_monotonic) * 1000)
            self.last_duration_ms = duration_ms
            self.consecutive_failures += 1
            self.last_error_category = e.category
            self.last_error_message = e.message

            # Record failure in poll_events table
            self.repository.record_poll_event(
                attempted_at_utc=attempted_at_utc,
                success=False,
                duration_ms=duration_ms,
                error_category=e.category,
                error_message=e.message,
                meter_id=target_meter_id,
            )

            if e.category == ErrorCategory.CONFIGURATION_ERROR:
                self.status = "configuration_error"
                self.polling_enabled = False  # Permanent error until config changes
                logger.error("Configuration error; periodic polling halted: %s", e.message)
            elif e.category in (ErrorCategory.AUTHENTICATION_ERROR, ErrorCategory.AUTHORIZATION_ERROR):
                # Recoverable auth/session failure
                self.status = "authentication_required"
                self.client.clear_session()  # Clear stale token for re-login on next retry
                backoff_delay = self._calculate_backoff_seconds()
                self._backoff_until_monotonic = time.monotonic() + backoff_delay
                retry_dt = datetime.now(timezone.utc) + timedelta(seconds=backoff_delay)
                self.next_retry_at_utc = retry_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
                logger.warning(
                    "Authentication failure (%s). Retry #%d scheduled in %.1fs at %s",
                    e.message,
                    self.consecutive_failures,
                    backoff_delay,
                    self.next_retry_at_utc,
                )
            else:
                # Network, timeout, or upstream server failure
                self.status = "stale"
                backoff_delay = self._calculate_backoff_seconds()
                self._backoff_until_monotonic = time.monotonic() + backoff_delay
                retry_dt = datetime.now(timezone.utc) + timedelta(seconds=backoff_delay)
                self.next_retry_at_utc = retry_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
                logger.warning(
                    "Upstream fetch failed (%s: %s). Retry #%d in %.1fs",
                    e.category,
                    e.message,
                    self.consecutive_failures,
                    backoff_delay,
                )

            if self.notifier:
                try:
                    self.notifier.on_poll_failure(self.consecutive_failures, e.message)
                except Exception as n_err:
                    logger.debug("Desktop notifier error on poll failure: %s", n_err)

            latest = self.repository.get_latest_snapshot(self.client.meter_id if self.client else None)
            return False, latest, e.message

        except Exception as e:
            duration_ms = int((time.monotonic() - start_monotonic) * 1000)
            self.last_duration_ms = duration_ms
            self.consecutive_failures += 1
            self.last_error_category = ErrorCategory.UPSTREAM_APPLICATION_ERROR
            self.last_error_message = f"Unexpected error: {str(e)}"
            self.status = "stale"

            self.repository.record_poll_event(
                attempted_at_utc=attempted_at_utc,
                success=False,
                duration_ms=duration_ms,
                error_category=ErrorCategory.UPSTREAM_APPLICATION_ERROR,
                error_message=str(e),
                meter_id=target_meter_id,
            )

            backoff_delay = self._calculate_backoff_seconds()
            self._backoff_until_monotonic = time.monotonic() + backoff_delay
            retry_dt = datetime.now(timezone.utc) + timedelta(seconds=backoff_delay)
            self.next_retry_at_utc = retry_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
            logger.exception("Unexpected exception during fetch cycle")

            if self.notifier:
                try:
                    self.notifier.on_poll_failure(self.consecutive_failures, str(e))
                except Exception as n_err:
                    logger.debug("Desktop notifier error on poll failure: %s", n_err)

            latest = self.repository.get_latest_snapshot(self.client.meter_id if self.client else None)
            return False, latest, "Unexpected application error"

        finally:
            if temp_lock_file is not None:
                try:
                    fcntl.flock(temp_lock_file, fcntl.LOCK_UN)
                    temp_lock_file.close()
                except Exception:
                    pass
            self.is_fetching = False
            self._lock.release()

    def start(self) -> bool:
        """Start the background polling worker thread.
        
        Uses an advisory process lock so that in multi-worker WSGI environments,
        only one worker process ever launches the periodic polling thread.
        Safe and idempotent.
        """
        if self._thread is not None and self._thread.is_alive():
            logger.debug("Polling worker thread is already running")
            return True

        # Attempt to acquire advisory process-level file lock if not already held
        if self._lock_file is None:
            try:
                if not self._lock_path.parent.exists():
                    self._lock_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                f = open(self._lock_path, "a+")
                try:
                    self._lock_path.chmod(0o600)
                except OSError:
                    pass
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self._lock_file = f
                self.is_collector_owner = True
            except (BlockingIOError, OSError):
                self.is_collector_owner = False
                self.status = "standby"
                logger.info("Another process holds the poller lock; running in web-only standby mode")
                return False

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, name="ELNetPoller", daemon=True)
        self._thread.start()
        logger.info("Background polling worker started (cadence: %ds)", self.poll_interval_seconds)
        return True

    def _release_process_lock(self) -> None:
        """Release the advisory process-level file lock."""
        if self._lock_file is not None:
            try:
                fcntl.flock(self._lock_file, fcntl.LOCK_UN)
                self._lock_file.close()
            except Exception:
                pass
            self._lock_file = None
            self.is_collector_owner = False

    def stop(self, timeout: Optional[float] = None) -> bool:
        """Signal worker thread to terminate cleanly and wait for it.

        Waits up to the client request timeout + buffer to ensure in-flight
        HTTP requests finish without leaving orphan threads.
        Releases the process lock only after the worker thread has completely terminated.
        Safe and idempotent.
        """
        self._stop_event.set()

        if self._thread is None:
            self._release_process_lock()
            if self.status != "configuration_error":
                self.status = "offline"
            self.next_scheduled_poll_at_utc = None
            return True

        if not self._thread.is_alive():
            self._thread = None
            self._release_process_lock()
            if self.status != "configuration_error":
                self.status = "offline"
            self.next_scheduled_poll_at_utc = None
            return True

        wait_timeout = timeout if timeout is not None else float(getattr(self.client, "timeout", 15) + 2.0)
        self._thread.join(timeout=wait_timeout)

        if self._thread.is_alive():
            logger.warning("Polling worker thread did not stop within %.1fs timeout", wait_timeout)
            # Retain lock ownership and thread reference to prevent replacement worker from starting
            return False

        self._thread = None
        self._release_process_lock()
        if self.status != "configuration_error":
            self.status = "offline"
        self.next_scheduled_poll_at_utc = None
        logger.info("Background polling worker stopped cleanly")
        return True

    def _run_loop(self) -> None:
        """Background worker loop using a fixed monotonic cadence.

        Avoids schedule drift and skips missed ticks to prevent burst requests
        after system sleeps or network delays.
        """
        next_tick = time.monotonic()
        self.next_scheduled_poll_at_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        while not self._stop_event.is_set():
            now = time.monotonic()

            # Handle backoff wait if in error state
            if self._backoff_until_monotonic > now:
                backoff_wait = min(self._backoff_until_monotonic - now, 2.0)
                if self._stop_event.wait(timeout=backoff_wait):
                    break
                continue

            # Wait until next tick or stop signal
            wait_time = max(0.0, next_tick - now)
            if wait_time > 0:
                if self._stop_event.wait(timeout=wait_time):
                    break

            if not self.polling_enabled:
                # Configuration error halted loop; idle until reconfigured
                self._stop_event.wait(timeout=5.0)
                continue

            # Execute fetch cycle
            self.fetch_once()

            # Compute next tick against monotonic clock
            now = time.monotonic()
            next_tick += self.poll_interval_seconds

            # If request took longer than interval or Mac slept, advance ticks
            # to prevent overlapping or burst catch-up requests
            if next_tick <= now:
                # Align next_tick to the nearest future cadence point
                missed_steps = int((now - next_tick) / self.poll_interval_seconds) + 1
                next_tick += missed_steps * self.poll_interval_seconds

            delay_to_next = max(0.0, next_tick - now)
            next_dt = datetime.now(timezone.utc) + timedelta(seconds=delay_to_next)
            self.next_scheduled_poll_at_utc = next_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
