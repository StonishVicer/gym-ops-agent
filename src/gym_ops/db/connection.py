"""SQLite connection factories.

Two ways to open the database, and only two (ADR-0003):

* `get_readonly_connection` — the MCP server's only entry point. Read-only is
  enforced by the engine (`mode=ro` URI) plus `PRAGMA query_only`.
* `get_write_connection` — used only by the seeder and the extractor.
"""

import sqlite3
from importlib.resources import files
from pathlib import Path

BUSY_TIMEOUT_S = 5.0


def get_readonly_connection(path: str | Path) -> sqlite3.Connection:
    """Open an existing database read-only.

    Any INSERT/UPDATE/DELETE/CREATE on the returned connection raises
    `sqlite3.OperationalError`.

    Raises:
        FileNotFoundError: if `path` does not exist (a `mode=ro` open never creates it).
    """
    resolved = Path(path).resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"database not found: {resolved}")
    # as_uri() yields `file:///abs/path` with reserved characters percent-encoded,
    # so a `?` or `#` in the path cannot inject extra URI parameters.
    conn = sqlite3.connect(f"{resolved.as_uri()}?mode=ro", uri=True, timeout=BUSY_TIMEOUT_S)
    conn.execute("PRAGMA query_only = ON")
    return conn


def get_write_connection(path: str | Path) -> sqlite3.Connection:
    """Open (or create) a database read-write with foreign keys enforced.

    For the seeder and the extractor only; the MCP server must never call this.
    """
    conn = sqlite3.connect(Path(path), timeout=BUSY_TIMEOUT_S)
    conn.execute("PRAGMA foreign_keys = ON")
    if conn.execute("PRAGMA foreign_keys").fetchone() != (1,):
        conn.close()
        raise RuntimeError("SQLite build does not support foreign key enforcement")
    return conn


def load_schema_sql() -> str:
    """Return the DDL from the packaged `schema.sql`."""
    return files("gym_ops.db").joinpath("schema.sql").read_text(encoding="utf-8")


def apply_schema(conn: sqlite3.Connection) -> None:
    """Create all tables and indexes on an empty database."""
    conn.executescript(load_schema_sql())
