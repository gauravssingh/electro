"""Regression tests for SQLite database path resolution, directory permissions, and fail-fast behavior."""

import os
import sys
import stat
from pathlib import Path
import pytest
import sqlite3

from config import Config, PROJECT_ROOT, get_default_db_path
from storage.db import get_db_connection


def test_db_path_resolution_defaults():
    """Verify default database path when ELECTRO_DB_PATH is empty or unset."""
    resolved = Config.resolve_db_path("")
    expected = get_default_db_path()
    assert resolved == str(expected)
    assert Path(resolved).is_absolute()

    if sys.platform == "darwin":
        assert "Library/Application Support/Electro/electro.db" in resolved


def test_db_path_resolution_anchors_relative_paths(tmp_path, monkeypatch):
    """Verify relative database paths are anchored to PROJECT_ROOT regardless of working directory."""
    original_cwd = os.getcwd()
    try:
        # Change current working directory to a completely different folder
        os.chdir(str(tmp_path))

        resolved = Config.resolve_db_path("my_custom.db")
        expected = PROJECT_ROOT / "my_custom.db"
        assert resolved == str(expected)
        assert Path(resolved).is_absolute()
        assert not resolved.startswith(str(tmp_path))
    finally:
        os.chdir(original_cwd)


def test_db_path_resolution_expands_user(monkeypatch):
    """Verify ~ expansion works properly."""
    resolved = Config.resolve_db_path("~/custom_dir/test.db")
    expected = Path.home() / "custom_dir" / "test.db"
    assert resolved == str(expected)
    assert Path(resolved).is_absolute()


def test_db_directory_created_with_0700_permissions(tmp_path):
    """Parent directory for database must be created with 0700 (owner only) permissions."""
    db_dir = tmp_path / "secure_sub"
    db_file = db_dir / "test.db"

    assert not db_dir.exists()

    with get_db_connection(str(db_file)) as conn:
        conn.execute("CREATE TABLE t (id INT);")

    assert db_file.exists()
    assert db_dir.exists()

    mode = stat.S_IMODE(os.stat(str(db_dir)).st_mode)
    assert mode == 0o700


def test_db_connection_fails_fast_without_fallback(tmp_path):
    """Connection errors must raise immediately without creating or falling back to another DB."""
    # Create a non-writable path to simulate connection failure
    unwritable_file = tmp_path / "not_a_dir"
    unwritable_file.write_text("regular file", encoding="utf-8")
    
    # Attempting to use a path where a parent directory cannot be created
    invalid_db_path = unwritable_file / "sub" / "db.sqlite"

    with pytest.raises((sqlite3.OperationalError, NotADirectoryError, OSError)):
        with get_db_connection(str(invalid_db_path)) as conn:
            conn.execute("SELECT 1;")
