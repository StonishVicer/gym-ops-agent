import hashlib
import re
import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from gym_ops.db import seed
from gym_ops.db.seed import (
    AMBIGUOUS_MEMBERS,
    SeedConfig,
    database_checksum,
    generate,
    main,
    seed_database,
)

from .conftest import DEFAULT_CONFIG, REFERENCE_DATE


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_seed_is_deterministic(tmp_path: Path) -> None:
    first = seed_database(tmp_path / "a.db", DEFAULT_CONFIG)
    second = seed_database(tmp_path / "b.db", DEFAULT_CONFIG)

    assert first.row_counts == second.row_counts
    assert first.table_checksums == second.table_checksums
    assert first.checksum == second.checksum
    assert _file_sha256(tmp_path / "a.db") == _file_sha256(tmp_path / "b.db")


def test_different_seed_changes_data(tmp_path: Path) -> None:
    base = seed_database(tmp_path / "a.db", DEFAULT_CONFIG)
    other = seed_database(tmp_path / "b.db", DEFAULT_CONFIG.model_copy(update={"seed": 7}))
    assert base.checksum != other.checksum


def test_reseed_replaces_existing_file(tmp_path: Path) -> None:
    path = tmp_path / "gym.db"
    path.write_bytes(b"stale")
    summary = seed_database(path, DEFAULT_CONFIG)
    with sqlite3.connect(path) as conn:
        assert database_checksum(conn) == summary.checksum
    assert not (tmp_path / "gym.db.tmp").exists()


def test_row_counts(seeded_conn: sqlite3.Connection) -> None:
    def count(sql: str) -> int:
        result: int = seeded_conn.execute(sql).fetchone()[0]
        return result

    assert count("SELECT COUNT(*) FROM members") == 300
    assert count("SELECT COUNT(*) FROM memberships") >= 300
    assert count("SELECT COUNT(*) FROM class_slots") == 12 * 40
    assert count("SELECT COUNT(*) FROM extracted_payments") == 0
    assert count("SELECT COUNT(*) FROM checkins") > 5000


def test_foreign_key_check_clean(seeded_conn: sqlite3.Connection) -> None:
    assert seeded_conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_one_bill_per_covered_month_for_non_frozen_memberships() -> None:
    data = generate(DEFAULT_CONFIG)
    frozen = {m.member_id for m in data.members if m.status == "frozen"}
    bills = {(p.membership_id, p.due_date) for p in data.expected_payments}
    expected = set()
    for ms in data.memberships:
        if ms.member_id in frozen:
            continue
        for month in (7, 8, 9):
            due = date(2026, month, ms.billing_day)
            if ms.covers(due):
                expected.add((ms.membership_id, due))
    assert bills == expected
    assert len(bills) == len(data.expected_payments)


def test_about_ten_percent_expire_within_14_days(seeded_conn: sqlite3.Connection) -> None:
    (expiring,) = seeded_conn.execute(
        "SELECT COUNT(*) FROM (SELECT MAX(end_date) AS end_date FROM memberships "
        "GROUP BY member_id) WHERE end_date > ? AND end_date <= ?",
        (REFERENCE_DATE.isoformat(), (REFERENCE_DATE + timedelta(days=14)).isoformat()),
    ).fetchone()
    assert expiring == 30


def test_due_dates_spread_across_month(seeded_conn: sqlite3.Connection) -> None:
    days = {int(d[8:10]) for (d,) in seeded_conn.execute("SELECT due_date FROM expected_payments")}
    assert days == set(range(1, 29))


def test_members_with_multiple_bills_in_match_window(seeded_conn: sqlite3.Connection) -> None:
    rows = seeded_conn.execute(
        "SELECT DISTINCT a.member_id FROM expected_payments a "
        "JOIN expected_payments b ON a.member_id = b.member_id "
        "AND a.expected_payment_id < b.expected_payment_id "
        "AND abs(julianday(a.due_date) - julianday(b.due_date)) <= ?",
        (5,),
    ).fetchall()
    assert len(rows) >= 2
    assert len(rows) == AMBIGUOUS_MEMBERS


def test_references_follow_format(seeded_conn: sqlite3.Connection) -> None:
    pattern = re.compile(r"GYM-\d{6}-\d{4}-\d{2}")
    for ref, due in seeded_conn.execute("SELECT reference, due_date FROM expected_payments"):
        assert pattern.fullmatch(ref)
        assert ref.endswith(due[:7])


def test_class_slots_schedule(seeded_conn: sqlite3.Connection) -> None:
    rows = seeded_conn.execute(
        "SELECT DISTINCT strftime('%w', starts_at), strftime('%H:%M', starts_at), "
        "strftime('%H:%M', starts_at, '+' || duration_min || ' minutes'), capacity "
        "FROM class_slots"
    ).fetchall()
    for weekday, start, end, capacity in rows:
        assert weekday != "0"  # no Sundays
        assert start >= "06:00" and end <= "21:00"
        assert capacity == 20
    (max_starts,) = seeded_conn.execute("SELECT MAX(starts_at) FROM class_slots").fetchone()
    assert max_starts < REFERENCE_DATE.isoformat()


def test_checkins_within_capacity(seeded_conn: sqlite3.Connection) -> None:
    over = seeded_conn.execute(
        "SELECT s.slot_id FROM class_slots s JOIN checkins c ON c.slot_id = s.slot_id "
        "GROUP BY s.slot_id HAVING COUNT(*) > s.capacity"
    ).fetchall()
    assert over == []


def test_checkins_peak_morning_and_evening(seeded_conn: sqlite3.Connection) -> None:
    per_hour = dict(
        seeded_conn.execute(
            "SELECT CAST(strftime('%H', s.starts_at) AS INTEGER), AVG(n) FROM class_slots s "
            "JOIN (SELECT slot_id, COUNT(*) AS n FROM checkins GROUP BY slot_id) c "
            "ON c.slot_id = s.slot_id GROUP BY 1"
        ).fetchall()
    )
    peak = [per_hour[h] for h in (6, 7, 18, 19)]
    off_peak = [v for h, v in per_hour.items() if h not in (6, 7, 18, 19)]
    assert min(peak) > max(off_peak) + 3


def test_checkins_only_by_covered_non_frozen_members(seeded_conn: sqlite3.Connection) -> None:
    bad = seeded_conn.execute(
        "SELECT c.checkin_id FROM checkins c JOIN members m ON m.member_id = c.member_id "
        "JOIN class_slots s ON s.slot_id = c.slot_id "
        "WHERE m.status = 'frozen' OR NOT EXISTS (SELECT 1 FROM memberships ms "
        "WHERE ms.member_id = c.member_id "
        "AND date(s.starts_at) BETWEEN ms.start_date AND ms.end_date)"
    ).fetchall()
    assert bad == []


def test_seed_module_never_reads_wall_clock() -> None:
    source = Path(seed.__file__).read_text(encoding="utf-8")
    assert not re.search(r"\b(now|today|utcnow)\(|time\.time\(", source)


def test_seed_config_rejects_tiny_member_count() -> None:
    with pytest.raises(ValidationError):
        SeedConfig(seed=1, members=5, reference_date=REFERENCE_DATE)


def test_cli(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = tmp_path / "cli.db"
    args = ["--db-path", str(db), "--seed", "42", "--members", "300"]
    assert main([*args, "--reference-date", "2026-09-25"]) == 0
    out = capsys.readouterr().out
    assert "members" in out and "checksum" in out
    with sqlite3.connect(db) as conn:
        expected = seed_database(tmp_path / "ref.db", DEFAULT_CONFIG).checksum
        assert database_checksum(conn) == expected
