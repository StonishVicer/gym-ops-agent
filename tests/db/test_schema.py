import sqlite3
from typing import Any

import pytest

MEMBER_SQL = (
    "INSERT INTO members (member_id, full_name, email, phone, status, joined_on) "
    "VALUES (?, ?, ?, ?, ?, ?)"
)
MEMBERSHIP_SQL = (
    "INSERT INTO memberships (membership_id, member_id, plan, price_cents, start_date, end_date) "
    "VALUES (?, ?, ?, ?, ?, ?)"
)
BILL_SQL = (
    "INSERT INTO expected_payments "
    "(membership_id, member_id, due_date, amount_cents, reference) VALUES (?, ?, ?, ?, ?)"
)
EXTRACTED_SQL = (
    "INSERT INTO extracted_payments (receipt_id, member_id, payer_name, amount_cents, currency, "
    "transfer_date, reference, bank_name, model_id, input_tokens, output_tokens, "
    "cost_usd_micros, latency_ms, extracted_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)


def _member(member_id: int = 1, **overrides: Any) -> tuple[Any, ...]:
    row: dict[str, Any] = {
        "member_id": member_id,
        "full_name": "Dana Smith",
        "email": f"dana{member_id}@example.com",
        "phone": "(555) 555-0100",
        "status": "active",
        "joined_on": "2025-01-15",
    }
    row.update(overrides)
    return tuple(row.values())


def _extracted(**overrides: Any) -> tuple[Any, ...]:
    row: dict[str, Any] = {
        "receipt_id": "r-001",
        "member_id": None,
        "payer_name": None,
        "amount_cents": 5000,
        "currency": "USD",
        "transfer_date": "2026-09-01",
        "reference": None,
        "bank_name": "Banco Demo",
        "model_id": "anthropic/claude-haiku-4.5",
        "input_tokens": 1500,
        "output_tokens": 120,
        "cost_usd_micros": 2100,
        "latency_ms": 900,
        "extracted_at": "2026-09-25T10:00:00Z",
    }
    row.update(overrides)
    return tuple(row.values())


@pytest.fixture
def with_member_and_membership(empty_db: sqlite3.Connection) -> sqlite3.Connection:
    empty_db.execute(MEMBER_SQL, _member(1))
    empty_db.execute(MEMBER_SQL, _member(2))
    empty_db.execute(MEMBERSHIP_SQL, (1, 1, "monthly", 5000, "2026-01-01", "2026-12-31"))
    return empty_db


def test_fk_enforced_on_checkin(empty_db: sqlite3.Connection) -> None:
    empty_db.execute(
        "INSERT INTO class_slots (slot_id, class_name, coach_name, starts_at, duration_min, "
        "capacity) VALUES (1, 'HIIT', 'Coach', '2026-09-01T06:00:00', 45, 20)"
    )
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        empty_db.execute(
            "INSERT INTO checkins (member_id, slot_id, checked_in_at) VALUES (?, ?, ?)",
            (999, 1, "2026-09-01T05:55:00"),
        )


def test_bill_member_must_match_membership(with_member_and_membership: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        with_member_and_membership.execute(
            BILL_SQL, (1, 2, "2026-09-10", 5000, "GYM-000001-2026-09")
        )


@pytest.mark.parametrize("amount", [12.5, "12.50", -100, 0])
def test_bill_amount_must_be_positive_integer_cents(
    with_member_and_membership: sqlite3.Connection, amount: object
) -> None:
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        with_member_and_membership.execute(
            BILL_SQL, (1, 1, "2026-09-10", amount, "GYM-000001-2026-09")
        )


@pytest.mark.parametrize("bad_date", ["2026-9-10", "10/09/2026", "2026-02-30", "2026-09-10 00:00"])
def test_iso_date_check(with_member_and_membership: sqlite3.Connection, bad_date: str) -> None:
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        with_member_and_membership.execute(BILL_SQL, (1, 1, bad_date, 5000, "GYM-000001-2026-09"))


def test_bill_reference_unique(with_member_and_membership: sqlite3.Connection) -> None:
    conn = with_member_and_membership
    conn.execute(BILL_SQL, (1, 1, "2026-09-10", 5000, "GYM-000001-2026-09"))
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
        conn.execute(BILL_SQL, (1, 1, "2026-10-10", 5000, "GYM-000001-2026-09"))


@pytest.mark.parametrize(
    "overrides",
    [{"status": "deleted"}, {"status": "ACTIVE"}, {"joined_on": "yesterday"}, {"full_name": " "}],
)
def test_member_checks(empty_db: sqlite3.Connection, overrides: dict[str, Any]) -> None:
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        empty_db.execute(MEMBER_SQL, _member(1, **overrides))


def test_membership_plan_and_date_order(with_member_and_membership: sqlite3.Connection) -> None:
    conn = with_member_and_membership
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        conn.execute(MEMBERSHIP_SQL, (2, 1, "lifetime", 5000, "2026-01-01", "2026-12-31"))
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        conn.execute(MEMBERSHIP_SQL, (2, 1, "monthly", 5000, "2026-12-31", "2026-01-01"))


def test_extracted_payment_allows_null_reference_and_payer(empty_db: sqlite3.Connection) -> None:
    empty_db.execute(EXTRACTED_SQL, _extracted())
    row = empty_db.execute("SELECT reference, payer_name, member_id FROM extracted_payments")
    assert row.fetchone() == (None, None, None)


@pytest.mark.parametrize(
    "overrides",
    [
        {"amount_cents": 0},
        {"amount_cents": 49.99},
        {"currency": "usd"},
        {"currency": "US"},
        {"transfer_date": "2026-13-01"},
        {"input_tokens": -1},
        {"extracted_at": "2026-09-25 10:00:00"},
    ],
)
def test_extracted_payment_checks(empty_db: sqlite3.Connection, overrides: dict[str, Any]) -> None:
    with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
        empty_db.execute(EXTRACTED_SQL, _extracted(**overrides))


def test_extracted_payment_fk_and_unique_receipt(empty_db: sqlite3.Connection) -> None:
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        empty_db.execute(EXTRACTED_SQL, _extracted(member_id=42))
    empty_db.execute(EXTRACTED_SQL, _extracted())
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
        empty_db.execute(EXTRACTED_SQL, _extracted())


def test_no_derived_link_table(empty_db: sqlite3.Connection) -> None:
    tables = {
        name
        for (name,) in empty_db.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    }
    assert tables == {
        "members",
        "memberships",
        "class_slots",
        "checkins",
        "expected_payments",
        "extracted_payments",
    }


def test_documented_indexes_exist(empty_db: sqlite3.Connection) -> None:
    indexes = {
        name
        for (name,) in empty_db.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND name LIKE 'idx_%'"
        )
    }
    assert indexes == {
        "idx_members_status",
        "idx_memberships_member",
        "idx_class_slots_starts_at",
        "idx_class_slots_name_starts",
        "idx_checkins_slot",
        "idx_expected_due",
        "idx_expected_member_due",
        "idx_extracted_date",
        "idx_extracted_reference",
        "idx_extracted_member_date",
    }
