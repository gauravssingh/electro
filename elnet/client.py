"""ELNet API Client adapter.

Handles authentication and read-only HomeData queries.
Maintains in-memory bearer token and strictly avoids logging sensitive details.
"""

from typing import Optional, Dict, Any
import logging
import time

import requests

from elnet.crypto import encrypt_payload, wrap_envelope
from elnet.schemas import (
    ErrorCategory,
    ElnetError,
    ElnetConfigError,
    ElnetAuthError,
    ElnetHttpError,
    ElnetTimeoutError,
    ElnetNetworkError,
)

logger = logging.getLogger("electro.elnet")


class ELNetClient:
    """Client for read-only communication with ELNet API."""

    def __init__(
        self,
        base_url: str,
        skey: bytes,
        ivkey: bytes,
        timeout: int = 15,
        session: Optional[requests.Session] = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.skey = skey
        self.ivkey = ivkey
        self.timeout = timeout
        self.session = session or requests.Session()

        # In-memory authentication state (never persisted to disk)
        self._auth_token: Optional[str] = None
        self._flat_id: Optional[int] = None
        self._meter_id: Optional[int] = None

    @property
    def is_authenticated(self) -> bool:
        return bool(self._auth_token)

    @property
    def flat_id(self) -> Optional[int]:
        return self._flat_id

    @property
    def meter_id(self) -> Optional[int]:
        return self._meter_id or self._flat_id

    @meter_id.setter
    def meter_id(self, value: int) -> None:
        self._meter_id = value

    def clear_session(self) -> None:
        """Clear cached in-memory token."""
        self._auth_token = None

    def _post_encrypted(
        self,
        path: str,
        payload: Dict[str, Any],
        token: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Encrypt payload, dispatch POST request, and parse response."""
        encrypted_str = encrypt_payload(payload, self.skey, self.ivkey)
        body = wrap_envelope(encrypted_str)

        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"

        url = f"{self.base_url}{path}"
        try:
            start_time = time.time()
            resp = self.session.post(
                url,
                json=body,
                headers=headers,
                timeout=self.timeout,
            )
            elapsed = time.time() - start_time
            logger.debug("Upstream POST %s completed in %.2fs with status %d", path, elapsed, resp.status_code)
        except requests.exceptions.Timeout as e:
            logger.warning("Upstream POST %s timed out after %ds", path, self.timeout)
            raise ElnetTimeoutError(f"Request to {path} timed out") from e
        except requests.exceptions.ConnectionError as e:
            logger.warning("Upstream POST %s connection failed", path)
            raise ElnetNetworkError(f"Failed to connect to upstream server at {self.base_url}") from e
        except requests.exceptions.RequestException as e:
            logger.warning("Upstream POST %s request error", path)
            raise ElnetNetworkError("Network error occurred during upstream request") from e

        if resp.status_code == 401:
            self.clear_session()
            raise ElnetError("Authentication failed or token expired", category=ErrorCategory.AUTHORIZATION_ERROR, status_code=401)

        if not resp.ok:
            raise ElnetHttpError(f"Upstream returned HTTP {resp.status_code}", status_code=resp.status_code)

        try:
            data = resp.json()
        except Exception as e:
            raise ElnetError("Invalid JSON received from upstream server", category=ErrorCategory.INVALID_RESPONSE) from e

        if not isinstance(data, dict):
            raise ElnetError("Unexpected non-dictionary response from upstream server", category=ErrorCategory.INVALID_RESPONSE)

        return data

    def login(
        self,
        username: str,
        password: str,
        fcm_token: str = "",
    ) -> Dict[str, Any]:
        """Authenticate with ELNet and store token in-memory."""
        if not username or not password:
            raise ElnetConfigError("Username and password are required for login")

        login_payload = {
            "Access": "Live",
            "FCMID": fcm_token or "",
            "Password": password,
            "UserName": username,
        }

        # Clear existing session before login attempt
        self.clear_session()

        resp_data = self._post_encrypted("/api/Dashboard/login", login_payload, token=None)

        status = str(resp_data.get("Status", "")).strip()
        message = str(resp_data.get("Message", "")).strip()
        data_block = resp_data.get("Data") or {}

        if status.lower() != "success":
            logger.warning("Upstream login failed with status '%s': %s", status, message)
            raise ElnetAuthError(f"Login failed: {message or status}")

        auth_token = data_block.get("AuthToken")
        if not auth_token or not isinstance(auth_token, str):
            logger.warning("Upstream login returned success status but missing AuthToken")
            raise ElnetAuthError("Login succeeded but no AuthToken was returned")

        # Save token strictly in memory
        self._auth_token = auth_token

        # Extract and validate FlatId
        flat_id = data_block.get("FlatId")
        if flat_id is not None:
            try:
                flat_id_int = int(flat_id)
                if flat_id_int > 0:
                    self._flat_id = flat_id_int
                    self._meter_id = flat_id_int
            except (ValueError, TypeError):
                logger.warning("Received invalid FlatId from login response")

        logger.info("ELNet login successful (FlatId: %s)", self._flat_id)
        return {
            "success": True,
            "flat_id": self._flat_id,
        }

    def get_home_data(
        self,
        meter_id: Optional[int] = None,
        max_retries: int = 2,
    ) -> Dict[str, Any]:
        """Fetch HomeData for the specified or default meter ID."""
        target_meter_id = meter_id or self.meter_id
        if target_meter_id is None:
            raise ElnetConfigError("No MeterID provided or discovered via login")

        if not self._auth_token:
            raise ElnetError("Client is not authenticated. Please login first.", category=ErrorCategory.AUTHENTICATION_ERROR)

        payload = {"MeterID": int(target_meter_id)}

        # Safe read-only retry with bounded backoff
        last_error = None
        for attempt in range(max_retries + 1):
            try:
                resp_data = self._post_encrypted(
                    "/api/Dashboard/HomeData",
                    payload,
                    token=self._auth_token,
                )

                status = str(resp_data.get("Status", "")).strip()
                if status.lower() != "success":
                    msg = str(resp_data.get("Message", "")).strip()
                    # Check if session/token expired
                    if "unauthorized" in msg.lower() or "session" in msg.lower() or "token" in msg.lower():
                        self.clear_session()
                        raise ElnetError(f"Session expired: {msg}", category=ErrorCategory.AUTHORIZATION_ERROR)
                    raise ElnetError(f"HomeData request rejected: {msg or status}", category=ErrorCategory.UPSTREAM_APPLICATION_ERROR)

                data_block = resp_data.get("Data")
                if not isinstance(data_block, dict):
                    raise ElnetError("HomeData response missing valid 'Data' object", category=ErrorCategory.INVALID_RESPONSE)

                return data_block

            except (ElnetTimeoutError, ElnetNetworkError) as e:
                last_error = e
                if attempt < max_retries:
                    backoff = 1.0 * (2 ** attempt)
                    logger.info("Transient error fetching HomeData; retrying in %.1fs (attempt %d/%d)", backoff, attempt + 1, max_retries)
                    time.sleep(backoff)
                else:
                    raise

        if last_error:
            raise last_error
        raise ElnetError("Unknown error occurred during HomeData retrieval")
