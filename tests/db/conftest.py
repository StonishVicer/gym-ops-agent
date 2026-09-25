import sqlite3
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest

from gym_ops.db.connection import apply_schema, get_write_connection
from gym_ops.db.seed import SeedConfig, seed_database

REFERENCE_DATE = date(2026, 9, 25)
DEFAULT_CONFIG = SeedConfig(seed=42, members=300, reference_date=REFERENCE_DATE)


@pytest.fixture(scope="session")
def seeded_db_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("seeded") / "gym.db"
    seed_database(path, DEFAULT_CONFIG)
    return path


@pytest.fixture
def seeded_conn(seeded_db_path: Path) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(seeded_db_path)
    yield conn
    conn.close()


@pytest.fixture
def empty_db(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    conn = get_write_connection(tmp_path / "empty.db")
    apply_schema(conn)
    yield conn
    conn.close()
