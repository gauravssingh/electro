"""Schema definitions, constants, and error types for ELNet communication."""

from typing import Optional, Dict, Any


class ErrorCategory:
    CONFIGURATION_ERROR = "configuration_error"
    AUTHENTICATION_ERROR = "authentication_error"
    AUTHORIZATION_ERROR = "authorization_or_expired_token"
    UPSTREAM_HTTP_ERROR = "upstream_http_error"
    UPSTREAM_APPLICATION_ERROR = "upstream_application_error"
    TIMEOUT = "timeout"
    NETWORK_ERROR = "network_error"
    INVALID_RESPONSE = "invalid_response"
    STORAGE_ERROR = "storage_error"


class ElnetError(Exception):
    """Base exception for ELNet client errors with sanitized error categories."""

    def __init__(
        self,
        message: str,
        category: str = ErrorCategory.UPSTREAM_APPLICATION_ERROR,
        status_code: Optional[int] = None,
        details: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(message)
        self.message = message
        self.category = category
        self.status_code = status_code
        self.details = details or {}

    def __str__(self) -> str:
        if self.status_code:
            return f"[{self.category}] (HTTP {self.status_code}) {self.message}"
        return f"[{self.category}] {self.message}"


class ElnetConfigError(ElnetError):
    def __init__(self, message: str):
        super().__init__(message, category=ErrorCategory.CONFIGURATION_ERROR)


class ElnetAuthError(ElnetError):
    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message, category=ErrorCategory.AUTHENTICATION_ERROR, status_code=status_code)


class ElnetHttpError(ElnetError):
    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message, category=ErrorCategory.UPSTREAM_HTTP_ERROR, status_code=status_code)


class ElnetTimeoutError(ElnetError):
    def __init__(self, message: str = "Request timed out"):
        super().__init__(message, category=ErrorCategory.TIMEOUT)


class ElnetNetworkError(ElnetError):
    def __init__(self, message: str):
        super().__init__(message, category=ErrorCategory.NETWORK_ERROR)
