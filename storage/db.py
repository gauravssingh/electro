"""Database connection and migration manager."""

import sqlite3
from pathlib import Path
from contextlib import contextmanager
from typing import Generator
import logging

logger = logging.getLogger("electro.db")

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


@contextmanager
def get_db_connection(db_path: str) -> Generator[sqlite3.Connection, None, None]:
    """Provide a thread-safe SQLite connection with WAL mode and row factory."""
    db_file = Path(db_path)
    parent_dir = db_file.parent
    if not parent_dir.exists():
        try:
            parent_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            try:
                parent_dir.chmod(0o700)
            except OSError:
                pass
        except Exception as e:
            logger.error("Failed to create database directory %s: %s", parent_dir, e)
            raise

    try:
        conn = sqlite3.connect(db_path, timeout=10.0, check_same_thread=False)
    except Exception as e:
        logger.error("Failed to connect to SQLite database at %s: %s", db_path, e)
        raise

    # Ensure the SQLite database file itself is restricted to owner read/write
    if db_file.is_file():
        try:
            db_file.chmod(0o600)
        except OSError:
            pass

    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA busy_timeout = 5000;")
        conn.execute("PRAGMA foreign_keys = ON;")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def run_migrations(db_path: str) -> None:
    """Run pending SQL migrations."""
    with get_db_connection(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version TEXT PRIMARY KEY,
                applied_at_utc TEXT NOT NULL
            );
            """
        )
        applied = {row["version"] for row in conn.execute("SELECT version FROM schema_migrations").fetchall()}

        # Read migration files in sorted order
        migration_files = sorted(MIGRATIONS_DIR.glob("*.sql"))
        for m_file in migration_files:
            version = m_file.stem
            if version not in applied:
                logger.info("Applying migration: %s", version)
                sql = m_file.read_text(encoding="utf-8")
                conn.executescript(sql)
                conn.execute(
                    "INSERT INTO schema_migrations (version, applied_at_utc) VALUES (?, datetime('now'));",
                    (version,),
                )
                logger.info("Applied migration: %s", version)
