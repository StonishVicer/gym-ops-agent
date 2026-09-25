import random
import shutil
import sqlite3
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import pytest

from gym_ops.config import get_settings
from gym_ops.db.connection import apply_schema, get_write_connection
from gym_ops.db.seed import SeedConfig, seed_database

SEED_CONFIG = SeedConfig(seed=42, members=300, reference_date=date(2026, 9, 25))

PointDb = Callable[[Path], None]


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def point_db(monkeypatch: pytest.MonkeyPatch) -> Iterator[PointDb]:
    """Point the server's Settings at a database (env wins over `.env`)."""

    def point(path: Path) -> None:
        monkeypatch.setenv("DB_PATH", str(path))
        monkeypatch.setenv("MATCH_WINDOW_DAYS", "5")
        get_settings.cache_clear()

    yield point
    get_settings.cache_clear()


@pytest.fixture(scope="session")
def seeded_db_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("mcp_seeded") / "gym.db"
    seed_database(path, SEED_CONFIG)
    return path


@pytest.fixture
def seeded_db(seeded_db_path: Path, point_db: PointDb) -> Path:
    point_db(seeded_db_path)
    return seeded_db_path


class FixtureDb:
    """A hand-built database for exact-value tests. Every insert is committed."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.conn = get_write_connection(path)
        apply_schema(self.conn)

    def _insert(self, sql: str, params: tuple[object, ...]) -> int:
        cur = self.conn.execute(sql, params)
        self.conn.commit()
        assert cur.lastrowid is not None
        return cur.lastrowid

    def member(self, full_name: str, status: str = "active") -> int:
        n = self.conn.execute("SELECT COUNT(*) FROM members").fetchone()[0]
        return self._insert(
            "INSERT INTO members (full_name, email, phone, status, joined_on) "
            "VALUES (?, ?, ?, ?, ?)",
            (full_name, f"m{n}@example.com", "555-0100", status, "2025-01-01"),
        )

    def membership(
        self,
        member_id: int,
        plan: str = "monthly",
        start: str = "2025-01-01",
        end: str = "2027-01-01",
        price_cents: int = 5000,
    ) -> int:
        return self._insert(
            "INSERT INTO memberships (member_id, plan, price_cents, start_date, end_date) "
            "VALUES (?, ?, ?, ?, ?)",
            (member_id, plan, price_cents, start, end),
        )

    def bill(self, member_id: int, due: str, reference: str, amount_cents: int = 5000) -> int:
        membership_id = self.conn.execute(
            "SELECT membership_id FROM memberships WHERE member_id = ? ORDER BY membership_id",
            (member_id,),
        ).fetchone()[0]
        return self._insert(
            "INSERT INTO expected_payments "
            "(membership_id, member_id, due_date, amount_cents, reference) VALUES (?, ?, ?, ?, ?)",
            (membership_id, member_id, due, amount_cents, reference),
        )

    def transfer(
        self,
        receipt_id: str,
        amount_cents: int,
        transfer_date: str,
        reference: str | None = None,
        member_id: int | None = None,
        payer_name: str | None = "Payer",
    ) -> int:
        return self._insert(
            "INSERT INTO extracted_payments (receipt_id, member_id, payer_name, amount_cents, "
            "currency, transfer_date, reference, bank_name, model_id, input_tokens, "
            "output_tokens, cost_usd_micros, latency_ms, extracted_at) "
            "VALUES (?, ?, ?, ?, 'USD', ?, ?, 'Test Bank', 'test', 0, 0, 0, 0, "
            "'2026-09-30T00:00:00Z')",
            (receipt_id, member_id, payer_name, amount_cents, transfer_date, reference),
        )

    def slot(self, class_name: str, starts_at: str, capacity: int = 20) -> int:
        return self._insert(
            "INSERT INTO class_slots (class_name, coach_name, starts_at, duration_min, capacity) "
            "VALUES (?, 'Coach', ?, 45, ?)",
            (class_name, starts_at, capacity),
        )

    def checkins(self, slot_id: int, count: int) -> None:
        starts_at = self.conn.execute(
            "SELECT starts_at FROM class_slots WHERE slot_id = ?", (slot_id,)
        ).fetchone()[0]
        for i in range(count):
            member_id = self.member(f"Attendee {slot_id}-{i}")
            self._insert(
                "INSERT INTO checkins (member_id, slot_id, checked_in_at) VALUES (?, ?, ?)",
                (member_id, slot_id, starts_at),
            )

    def close(self) -> None:
        self.conn.close()


@pytest.fixture
def fixture_db(tmp_path: Path, point_db: PointDb) -> Iterator[FixtureDb]:
    db = FixtureDb(tmp_path / "fixture.db")
    point_db(db.path)
    yield db
    db.close()


@pytest.fixture
def table_exists() -> Callable[[Path, str], bool]:
    def check(path: Path, table: str) -> bool:
        conn = sqlite3.connect(path)
        try:
            row = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
            ).fetchone()
            return row is not None
        finally:
            conn.close()

    return check


@dataclass(frozen=True)
class SyntheticTransfer:
    receipt_id: str
    member_id: int | None
    amount_cents: int
    transfer_date: str
    reference: str | None


def synthetic_transfers(db: Path) -> list[SyntheticTransfer]:
    """One realistic payment scenario per bill, drawn from a fixed seed."""
    rng = random.Random(7)  # noqa: S311 -- reproducible test data, not crypto
    conn = get_write_connection(db)
    bills = conn.execute(
        "SELECT member_id, due_date, amount_cents, reference FROM expected_payments "
        "ORDER BY expected_payment_id"
    ).fetchall()
    conn.close()
    out: list[SyntheticTransfer] = []

    def add(member: int | None, cents: int, day: date, ref: str | None) -> None:
        out.append(SyntheticTransfer(f"rcpt-{len(out):05d}", member, cents, day.isoformat(), ref))

    for member, due_s, cents, ref in bills:
        due = date.fromisoformat(due_s)
        near = due + timedelta(days=rng.randint(-6, 6))
        match rng.randrange(8):
            case 0:
                pass  # unpaid
            case 1:
                add(member, cents, near, ref)
            case 2:
                add(member, cents - 1000, due, ref)
                add(member, 1000, near, None)  # top-up, maybe outside window
            case 3:
                add(member, cents, near, None)
            case 4:
                add(member, cents, due, ref)
                add(member, cents, near, ref)  # duplicate
            case 5:
                add(None, cents, near, None)  # unknown payer
            case 6:
                add(member, cents, due + timedelta(days=20), ref)  # late
            case _:
                add(member, cents, near, "garbage ref")
    return out


def load_transfers(db: Path, transfers: list[SyntheticTransfer]) -> None:
    conn = get_write_connection(db)
    conn.executemany(
        "INSERT INTO extracted_payments (receipt_id, member_id, payer_name, amount_cents, "
        "currency, transfer_date, reference, bank_name, model_id, input_tokens, output_tokens, "
        "cost_usd_micros, latency_ms, extracted_at) VALUES (?, ?, 'Payer', ?, 'USD', ?, ?, "
        "'Bank', 'test', 0, 0, 0, 0, '2026-09-30T00:00:00Z')",
        [
            (t.receipt_id, t.member_id, t.amount_cents, t.transfer_date, t.reference)
            for t in transfers
        ],
    )
    conn.commit()
    conn.close()


@pytest.fixture(scope="session")
def paid_db_path(seeded_db_path: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The seeded DB plus one realistic transfer scenario per bill."""
    path = tmp_path_factory.mktemp("paid") / "gym.db"
    shutil.copy(seeded_db_path, path)
    load_transfers(path, synthetic_transfers(path))
    return path
