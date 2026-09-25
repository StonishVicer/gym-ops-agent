import sqlite3
from pathlib import Path

import pytest

from gym_ops.db.connection import get_readonly_connection, get_write_connection

WRITE_STATEMENTS = [
    "INSERT INTO members (full_name, email, phone, status, joined_on) "
    "VALUES ('X', 'x@example.com', '1', 'active', '2026-01-01')",
    "UPDATE members SET status = 'frozen'",
    "DELETE FROM members",
    "CREATE TABLE sneaky (id INTEGER)",
    "DROP TABLE checkins",
]


@pytest.mark.parametrize("sql", WRITE_STATEMENTS)
def test_readonly_connection_rejects_writes(seeded_db_path: Path, sql: str) -> None:
    conn = get_readonly_connection(seeded_db_path)
    try:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute(sql)
    finally:
        conn.close()


def test_readonly_connection_can_read(seeded_db_path: Path) -> None:
    conn = get_readonly_connection(seeded_db_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM members").fetchone() == (300,)
        assert conn.execute("PRAGMA query_only").fetchone() == (1,)
    finally:
        conn.close()


def test_readonly_connection_never_creates_file(tmp_path: Path) -> None:
    missing = tmp_path / "missing.db"
    with pytest.raises(FileNotFoundError):
        get_readonly_connection(missing)
    assert not missing.exists()


def test_readonly_path_with_uri_metacharacters(tmp_path: Path) -> None:
    odd_dir = tmp_path / "we?ird#dir"
    odd_dir.mkdir()
    path = odd_dir / "gym.db"
    get_write_connection(path).close()
    conn = get_readonly_connection(path)
    try:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("CREATE TABLE t (id INTEGER)")
    finally:
        conn.close()


def test_write_connection_enforces_foreign_keys(tmp_path: Path) -> None:
    conn = get_write_connection(tmp_path / "rw.db")
    try:
        assert conn.execute("PRAGMA foreign_keys").fetchone() == (1,)
    finally:
        conn.close()
