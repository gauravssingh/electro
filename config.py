"""Configuration module for Electro."""

from typing import Optional
import os
from pathlib import Path


def load_dotenv(dotenv_path: Optional[Path] = None) -> None:
    """Minimal local .env parser avoiding external dependencies."""
    if dotenv_path is None:
        dotenv_path = Path(__file__).resolve().parent / ".env"
    if not dotenv_path.is_file():
        return

    try:
        with open(dotenv_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, val = line.split("=", 1)
                key = key.strip()
                val = val.strip().strip("'\"")
                if key and key not in os.environ:
                    os.environ[key] = val
    except Exception:
        # Ignore errors loading local .env
        pass


# Load local .env if present
load_dotenv()


class Config:
    """Application configuration."""

    # Upstream ELNet settings
    ELNET_BASE_URL: str = os.getenv("ELNET_BASE_URL", "").rstrip("/")
    ELNET_USERNAME: str = os.getenv("ELNET_USERNAME", "")
    ELNET_PASSWORD: str = os.getenv("ELNET_PASSWORD", "")
    ELNET_SKEY: str = os.getenv("ELNET_SKEY", "")
    ELNET_IVKEY: str = os.getenv("ELNET_IVKEY", "")
    ELNET_FCM_TOKEN: str = os.getenv("ELNET_FCM_TOKEN", "")
    ELNET_POLL_INTERVAL_SECONDS: int = int(
        os.getenv("ELNET_POLL_INTERVAL_SECONDS", "15")
    )
    ELNET_REQUEST_TIMEOUT_SECONDS: int = int(
        os.getenv("ELNET_REQUEST_TIMEOUT_SECONDS", "15")
    )

    # Local application settings
    ELECTRO_DB_PATH: str = os.getenv("ELECTRO_DB_PATH", "electro.db")
    ELECTRO_HOST: str = os.getenv("ELECTRO_HOST", "127.0.0.1")
    ELECTRO_PORT: int = int(os.getenv("ELECTRO_PORT", "5000"))
    ELECTRO_LOG_LEVEL: str = os.getenv("ELECTRO_LOG_LEVEL", "INFO").upper()

    @classmethod
    def get_crypto_keys(cls) -> tuple:
        """Returns validated (skey_bytes, ivkey_bytes). Raises ValueError if missing or invalid."""
        if not cls.ELNET_SKEY or not cls.ELNET_IVKEY:
            raise ValueError("ELNET_SKEY and ELNET_IVKEY environment variables must be set.")
        skey = cls.ELNET_SKEY[:32].encode("utf-8")
        ivkey = cls.ELNET_IVKEY[:16].encode("utf-8")
        if len(skey) != 32 or len(ivkey) != 16:
            raise ValueError("Invalid AES key length (must be 32 bytes) or IV length (must be 16 bytes).")
        return skey, ivkey
