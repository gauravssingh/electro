"""Local notification service for macOS desktop alerts.

Alerts on:
- Grid <-> Generator (DG) power source transitions.
- Low prepaid meter balance below configurable threshold.
- Stale telemetry / network connection dropouts and subsequent recovery.

Ensures alert latches and deduplication so identical conditions do not fire repeatedly.
"""

from typing import Optional, Dict, Any
import logging
import subprocess
import sys

logger = logging.getLogger("electro.notifications")


class NotificationService:
    """Manages system alerts and deduplication state for Electro."""

    def __init__(
        self,
        enabled: bool = True,
        low_balance_threshold: float = 500.0,
        stale_failure_threshold: int = 3,
    ):
        self.enabled = enabled
        self.low_balance_threshold = float(low_balance_threshold)
        self.stale_failure_threshold = max(1, int(stale_failure_threshold))

        # Latches to prevent repeated notifications
        self._last_source: Optional[int] = None
        self._low_balance_fired: bool = False
        self._stale_fired: bool = False

    def send_notification(
        self,
        title: str,
        message: str,
        subtitle: Optional[str] = None,
        sound: Optional[str] = "Ping",
    ) -> bool:
        """Deliver native desktop notification via osascript on macOS."""
        if not self.enabled or sys.platform != "darwin":
            return False

        safe_title = title.replace('"', '\\"')
        safe_msg = message.replace('"', '\\"')

        script_parts = [f'display notification "{safe_msg}" with title "{safe_title}"']
        if subtitle:
            safe_sub = subtitle.replace('"', '\\"')
            script_parts.append(f'subtitle "{safe_sub}"')
        if sound:
            safe_sound = sound.replace('"', '\\"')
            script_parts.append(f'sound name "{safe_sound}"')

        script = " ".join(script_parts)

        try:
            res = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True,
                text=True,
                timeout=2.0,
                check=False,
            )
            if res.returncode == 0:
                logger.info("Delivered desktop notification: %s - %s", title, message)
                return True
            else:
                logger.warning("Failed to display notification (code %d): %s", res.returncode, res.stderr)
                return False
        except Exception as e:
            logger.debug("Desktop notification skipped: %s", str(e))
            return False

    def on_snapshot(self, snapshot: Dict[str, Any]) -> None:
        """Evaluate a freshly captured snapshot for transition and balance alerts."""
        if not snapshot:
            return

        # 1. Evaluate Power Source Switch
        current_source = snapshot.get("eb_dg_status")
        if current_source in (0, 1):
            if self._last_source is not None and current_source != self._last_source:
                if current_source == 1:
                    self.send_notification(
                        title="Electro ⚡ Power Source Alert",
                        subtitle="Outage Detected",
                        message="Power switched to Backup Generator (DG). Higher tariff may apply.",
                        sound="Sosumi",
                    )
                elif current_source == 0:
                    self.send_notification(
                        title="Electro ⚡ Power Restored",
                        subtitle="Main Grid Active",
                        message="Normal Grid (EB) power restored.",
                        sound="Hero",
                    )
            # Update last observed source latch
            self._last_source = current_source

        # 2. Evaluate Low Balance
        balance = snapshot.get("meter_balance")
        if balance is not None:
            try:
                bal_float = float(balance)
                if bal_float < self.low_balance_threshold:
                    if not self._low_balance_fired:
                        self.send_notification(
                            title="Electro ⚡ Low Balance Warning",
                            subtitle=f"Balance: ₹{bal_float:.2f}",
                            message=f"Prepaid electricity balance is below ₹{self.low_balance_threshold:.0f}. Please recharge.",
                            sound="Basso",
                        )
                        self._low_balance_fired = True
                else:
                    # Reset latch if balance is restored/recharged
                    self._low_balance_fired = False
            except (ValueError, TypeError):
                pass

        # 3. Telemetry Recovery from previous stale state
        if self._stale_fired:
            self.send_notification(
                title="Electro ⚡ Connection Restored",
                subtitle="Live Telemetry",
                message="Upstream meter connection restored successfully.",
                sound="Ping",
            )
            self._stale_fired = False

    def on_poll_failure(self, consecutive_failures: int, error_message: Optional[str] = None) -> None:
        """Evaluate consecutive failures to send a stale telemetry alert once."""
        if consecutive_failures >= self.stale_failure_threshold and not self._stale_fired:
            msg = error_message or "Unable to reach meter telemetry."
            self.send_notification(
                title="Electro ⚠️ Telemetry Stale",
                subtitle=f"{consecutive_failures} Failed Polls",
                message=f"Connection degraded: {msg}",
                sound="Basso",
            )
            self._stale_fired = True
