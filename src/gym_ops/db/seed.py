"""Deterministic synthetic seed for the gym ops database.

    uv run python -m gym_ops.db.seed --seed 42 --members 300

Everything is generated from one `random.Random(seed)` and one seeded Faker
instance, relative to a fixed `reference_date` — never the wall clock — so the
same arguments always produce a byte-identical database (SPEC FR-1).

What the data is shaped to exercise:

* ~10% of members hold a membership that ends within 14 days of the reference date.
* Class check-ins peak at 06:00-08:00 and 18:00-20:00 on weekdays (`find_peak_hours`).
* Bills are due on each member's billing anniversary, spread across days 1-28 of
  the month, so the reconcile +/-MATCH_WINDOW_DAYS window is exercised.
* A few members hold a second, concurrent membership billed 2 days after the
  first: two open bills in the same window, needed for the `ambiguous` case (ADR-0005).
* `extracted_payments` is left empty; the extractor fills it.
"""

import argparse
import calendar
import hashlib
import os
import random
import sqlite3
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path

from faker import Faker
from pydantic import BaseModel, ConfigDict, Field

from gym_ops.config import get_settings
from gym_ops.db.connection import apply_schema, get_write_connection

# SPEC A-1 / Q-1: USD amounts, English names.
FAKER_LOCALE = "en_US"
EMAIL_DOMAIN = "example.com"  # RFC 2606 reserved domain

CANCELLED_SHARE = 0.12
FROZEN_SHARE = 0.06
EXPIRING_SHARE = 0.10
EXPIRING_WITHIN_DAYS = 14
AMBIGUOUS_MEMBERS = 3
ADDON_BILLING_OFFSET_DAYS = 2
MAX_BILLING_DAY = 28  # every month has a day 28

# Monthly fee per plan; longer commitments are cheaper per month.
PLAN_PRICES_CENTS: dict[str, int] = {
    "monthly": 5000,
    "quarterly": 4500,
    "annual": 4000,
    "student": 3500,
}
PLAN_WEIGHTS: tuple[int, ...] = (45, 20, 15, 20)

CLASS_CAPACITY = 20
CLASS_NAMES: tuple[str, ...] = ("HIIT", "Spin", "Yoga", "Pilates", "Boxing", "Strength")
CLASS_DURATION_MIN: dict[str, int] = {"Yoga": 60, "Pilates": 60}
DEFAULT_DURATION_MIN = 45
WEEKDAY_HOURS: tuple[int, ...] = (6, 7, 8, 12, 18, 19, 20)  # Mon-Fri: 7 slots/day
SATURDAY_HOURS: tuple[int, ...] = (8, 9, 10, 11, 12)  # 5 slots -> 40 slots/week
# Mean share of capacity filled, by start hour. Peaks: 06-08 and 18-20.
ATTENDANCE_RATE: dict[int, float] = {
    6: 0.85,
    7: 0.95,
    8: 0.55,
    9: 0.60,
    10: 0.55,
    11: 0.45,
    12: 0.40,
    18: 0.95,
    19: 0.90,
    20: 0.45,
}
ATTENDANCE_STDDEV = 1.5
MAX_EARLY_CHECKIN_MIN = 15

DATETIME_FMT = "%Y-%m-%dT%H:%M:%S"

INSERT_MEMBER = (
    "INSERT INTO members (member_id, full_name, email, phone, status, joined_on) "
    "VALUES (?, ?, ?, ?, ?, ?)"
)
INSERT_MEMBERSHIP = (
    "INSERT INTO memberships "
    "(membership_id, member_id, plan, price_cents, start_date, end_date) "
    "VALUES (?, ?, ?, ?, ?, ?)"
)
INSERT_CLASS_SLOT = (
    "INSERT INTO class_slots "
    "(slot_id, class_name, coach_name, starts_at, duration_min, capacity) "
    "VALUES (?, ?, ?, ?, ?, ?)"
)
INSERT_CHECKIN = (
    "INSERT INTO checkins (checkin_id, member_id, slot_id, checked_in_at) VALUES (?, ?, ?, ?)"
)
INSERT_EXPECTED_PAYMENT = (
    "INSERT INTO expected_payments "
    "(expected_payment_id, membership_id, member_id, due_date, amount_cents, reference) "
    "VALUES (?, ?, ?, ?, ?, ?)"
)

# Static per-table dumps, ordered by primary key, for content checksums.
TABLE_DUMP_SQL: dict[str, str] = {
    "members": "SELECT * FROM members ORDER BY member_id",
    "memberships": "SELECT * FROM memberships ORDER BY membership_id",
    "class_slots": "SELECT * FROM class_slots ORDER BY slot_id",
    "checkins": "SELECT * FROM checkins ORDER BY checkin_id",
    "expected_payments": "SELECT * FROM expected_payments ORDER BY expected_payment_id",
    "extracted_payments": "SELECT * FROM extracted_payments ORDER BY extracted_payment_id",
}
TABLE_COUNT_SQL: dict[str, str] = {
    "members": "SELECT COUNT(*) FROM members",
    "memberships": "SELECT COUNT(*) FROM memberships",
    "class_slots": "SELECT COUNT(*) FROM class_slots",
    "checkins": "SELECT COUNT(*) FROM checkins",
    "expected_payments": "SELECT COUNT(*) FROM expected_payments",
    "extracted_payments": "SELECT COUNT(*) FROM extracted_payments",
}


class SeedConfig(BaseModel):
    """Inputs that fully determine the seeded data."""

    model_config = ConfigDict(frozen=True)

    seed: int
    members: int = Field(ge=20, le=100_000)
    reference_date: date
    weeks: int = Field(default=12, ge=1, le=52)
    billing_months: int = Field(default=3, ge=1, le=24)


class SeedSummary(BaseModel):
    """Result of a seed run: row counts and content checksums."""

    db_path: str
    row_counts: dict[str, int]
    table_checksums: dict[str, str]
    checksum: str


@dataclass(frozen=True)
class Member:
    member_id: int
    full_name: str
    email: str
    phone: str
    status: str
    joined_on: date


@dataclass(frozen=True)
class Membership:
    membership_id: int
    member_id: int
    plan: str
    price_cents: int
    start_date: date
    end_date: date
    billing_day: int  # generation-only; not stored

    def covers(self, day: date) -> bool:
        return self.start_date <= day <= self.end_date


@dataclass(frozen=True)
class ClassSlot:
    slot_id: int
    class_name: str
    coach_name: str
    starts_at: datetime
    duration_min: int
    capacity: int


@dataclass(frozen=True)
class Checkin:
    checkin_id: int
    member_id: int
    slot_id: int
    checked_in_at: datetime


@dataclass(frozen=True)
class ExpectedPayment:
    expected_payment_id: int
    membership_id: int
    member_id: int
    due_date: date
    amount_cents: int
    reference: str


@dataclass
class SeedData:
    members: list[Member] = field(default_factory=list)
    memberships: list[Membership] = field(default_factory=list)
    class_slots: list[ClassSlot] = field(default_factory=list)
    checkins: list[Checkin] = field(default_factory=list)
    expected_payments: list[ExpectedPayment] = field(default_factory=list)


def payment_reference(membership_id: int, due_date: date) -> str:
    """Return the bill reference members put on their transfer, e.g. `GYM-000123-2026-09`."""
    return f"GYM-{membership_id:06d}-{due_date.year:04d}-{due_date.month:02d}"


def generate(config: SeedConfig) -> SeedData:
    """Build all seed rows in memory. Pure: same config, same rows."""
    rng = random.Random(config.seed)  # noqa: S311 -- reproducible synthetic data, not crypto
    fake = Faker(FAKER_LOCALE)
    fake.seed_instance(config.seed)

    data = SeedData()
    data.members = _generate_members(config, rng, fake)
    months = _billing_months(config.reference_date, config.billing_months)
    data.memberships = _generate_memberships(config, rng, data.members, months[0])
    data.class_slots = _generate_class_slots(config, fake)
    data.checkins = _generate_checkins(rng, data.members, data.memberships, data.class_slots)
    data.expected_payments = _generate_expected_payments(data.members, data.memberships, months)
    return data


def _rand_date(rng: random.Random, lower: date, upper: date) -> date:
    return lower + timedelta(days=rng.randint(0, (upper - lower).days))


def _slug(text: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return "".join(ch for ch in ascii_text.lower() if ch.isalnum())


def _generate_members(config: SeedConfig, rng: random.Random, fake: Faker) -> list[Member]:
    n = config.members
    ref = config.reference_date
    shuffled = list(range(1, n + 1))
    rng.shuffle(shuffled)
    n_cancelled = round(n * CANCELLED_SHARE)
    n_frozen = round(n * FROZEN_SHARE)
    cancelled = set(shuffled[:n_cancelled])
    frozen = set(shuffled[n_cancelled : n_cancelled + n_frozen])

    members: list[Member] = []
    for member_id in range(1, n + 1):
        first, last = fake.first_name(), fake.last_name()
        if member_id in cancelled:
            status, min_tenure_days = "cancelled", 200
        elif member_id in frozen:
            status, min_tenure_days = "frozen", 20
        else:
            status, min_tenure_days = "active", 20
        members.append(
            Member(
                member_id=member_id,
                full_name=f"{first} {last}",
                email=f"{_slug(first)}.{_slug(last)}.{member_id}@{EMAIL_DOMAIN}",
                phone=fake.numerify("(###) 555-01##"),  # 555-01xx: fictional range
                status=status,
                joined_on=ref - timedelta(days=rng.randint(min_tenure_days, 3 * 365)),
            )
        )
    return members


def _generate_memberships(
    config: SeedConfig, rng: random.Random, members: Sequence[Member], window_start: date
) -> list[Membership]:
    ref = config.reference_date
    active = [m for m in members if m.status == "active"]
    n_expiring = min(round(config.members * EXPIRING_SHARE), len(active))
    expiring = {m.member_id for m in rng.sample(active, n_expiring)}
    plans = list(PLAN_PRICES_CENTS)

    memberships: list[Membership] = []
    current_by_member: dict[int, Membership] = {}

    def add(member: Member, plan: str, start: date, end: date, billing_day: int) -> Membership:
        ms = Membership(
            membership_id=len(memberships) + 1,
            member_id=member.member_id,
            plan=plan,
            price_cents=PLAN_PRICES_CENTS[plan],
            start_date=start,
            end_date=end,
            billing_day=billing_day,
        )
        memberships.append(ms)
        return ms

    for member in members:
        if member.status == "cancelled":
            end = ref - timedelta(days=rng.randint(1, 150))
        elif member.member_id in expiring:
            end = ref + timedelta(days=rng.randint(1, EXPIRING_WITHIN_DAYS))
        else:
            end = ref + timedelta(days=rng.randint(EXPIRING_WITHIN_DAYS + 1, 365))
        upper = min(end, ref)
        start = _rand_date(rng, max(member.joined_on, upper - timedelta(days=400)), upper)
        # Billing anniversary is per member, so consecutive memberships share it and
        # never produce two bills in one month.
        billing_day = min(member.joined_on.day, MAX_BILLING_DAY)
        if (start - member.joined_on).days >= 60:
            prior_plan = rng.choices(plans, weights=PLAN_WEIGHTS)[0]
            add(member, prior_plan, member.joined_on, start - timedelta(days=1), billing_day)
        plan = rng.choices(plans, weights=PLAN_WEIGHTS)[0]
        current_by_member[member.member_id] = add(member, plan, start, end, billing_day)

    # Concurrent add-on memberships -> two open bills 2 days apart (ambiguous case).
    candidates = [
        m
        for m in active
        if m.member_id not in expiring
        and current_by_member[m.member_id].start_date < window_start
        and current_by_member[m.member_id].billing_day
        <= MAX_BILLING_DAY - ADDON_BILLING_OFFSET_DAYS
    ]
    for member in rng.sample(candidates, min(AMBIGUOUS_MEMBERS, len(candidates))):
        primary = current_by_member[member.member_id]
        add(
            member,
            "monthly",
            primary.start_date,
            primary.end_date,
            primary.billing_day + ADDON_BILLING_OFFSET_DAYS,
        )
    return memberships


def _weekly_timetable() -> list[tuple[int, int, str]]:
    """(weekday, start hour, class name) for one week, Mon=0 .. Sat=5."""
    timetable: list[tuple[int, int, str]] = []
    for weekday in range(6):
        hours = WEEKDAY_HOURS if weekday < 5 else SATURDAY_HOURS
        for i, hour in enumerate(hours):
            timetable.append((weekday, hour, CLASS_NAMES[(weekday + i) % len(CLASS_NAMES)]))
    return timetable


def _generate_class_slots(config: SeedConfig, fake: Faker) -> list[ClassSlot]:
    coaches = {name: fake.name() for name in CLASS_NAMES}
    ref = config.reference_date
    # The `weeks` full weeks (Mon-Sat) before the week containing the reference date.
    first_monday = ref - timedelta(days=ref.weekday(), weeks=config.weeks)
    slots: list[ClassSlot] = []
    for week in range(config.weeks):
        for weekday, hour, class_name in _weekly_timetable():
            day = first_monday + timedelta(weeks=week, days=weekday)
            slots.append(
                ClassSlot(
                    slot_id=len(slots) + 1,
                    class_name=class_name,
                    coach_name=coaches[class_name],
                    starts_at=datetime.combine(day, time(hour)),
                    duration_min=CLASS_DURATION_MIN.get(class_name, DEFAULT_DURATION_MIN),
                    capacity=CLASS_CAPACITY,
                )
            )
    return slots


def _generate_checkins(
    rng: random.Random,
    members: Sequence[Member],
    memberships: Sequence[Membership],
    slots: Sequence[ClassSlot],
) -> list[Checkin]:
    frozen = {m.member_id for m in members if m.status == "frozen"}
    by_member: dict[int, list[Membership]] = {}
    for ms in memberships:
        by_member.setdefault(ms.member_id, []).append(ms)

    checkins: list[Checkin] = []
    for slot in slots:
        day = slot.starts_at.date()
        eligible = sorted(
            member_id
            for member_id, mss in by_member.items()
            if member_id not in frozen and any(ms.covers(day) for ms in mss)
        )
        mean = ATTENDANCE_RATE[slot.starts_at.hour] * slot.capacity
        n = max(0, min(round(rng.gauss(mean, ATTENDANCE_STDDEV)), slot.capacity, len(eligible)))
        for member_id in sorted(rng.sample(eligible, n)):
            early = timedelta(minutes=rng.randint(0, MAX_EARLY_CHECKIN_MIN))
            checkins.append(
                Checkin(
                    checkin_id=len(checkins) + 1,
                    member_id=member_id,
                    slot_id=slot.slot_id,
                    checked_in_at=slot.starts_at - early,
                )
            )
    return checkins


def _billing_months(reference_date: date, count: int) -> list[date]:
    """First day of each of the `count` calendar months ending with the reference month."""
    months: list[date] = []
    year, month = reference_date.year, reference_date.month
    for _ in range(count):
        months.append(date(year, month, 1))
        year, month = (year, month - 1) if month > 1 else (year - 1, 12)
    return sorted(months)


def _generate_expected_payments(
    members: Sequence[Member], memberships: Sequence[Membership], months: Sequence[date]
) -> list[ExpectedPayment]:
    frozen = {m.member_id for m in members if m.status == "frozen"}
    payments: list[ExpectedPayment] = []
    for ms in memberships:
        if ms.member_id in frozen:
            continue  # frozen for the whole seeded window: not billed
        for month_start in months:
            last_day = calendar.monthrange(month_start.year, month_start.month)[1]
            due = month_start.replace(day=min(ms.billing_day, last_day))
            if not ms.covers(due):
                continue
            payments.append(
                ExpectedPayment(
                    expected_payment_id=len(payments) + 1,
                    membership_id=ms.membership_id,
                    member_id=ms.member_id,
                    due_date=due,
                    amount_cents=ms.price_cents,
                    reference=payment_reference(ms.membership_id, due),
                )
            )
    return payments


def write(conn: sqlite3.Connection, data: SeedData) -> None:
    """Insert all seed rows in one transaction (parametrized statements only)."""
    with conn:
        conn.executemany(
            INSERT_MEMBER,
            [
                (m.member_id, m.full_name, m.email, m.phone, m.status, m.joined_on.isoformat())
                for m in data.members
            ],
        )
        conn.executemany(
            INSERT_MEMBERSHIP,
            [
                (
                    ms.membership_id,
                    ms.member_id,
                    ms.plan,
                    ms.price_cents,
                    ms.start_date.isoformat(),
                    ms.end_date.isoformat(),
                )
                for ms in data.memberships
            ],
        )
        conn.executemany(
            INSERT_CLASS_SLOT,
            [
                (
                    s.slot_id,
                    s.class_name,
                    s.coach_name,
                    s.starts_at.strftime(DATETIME_FMT),
                    s.duration_min,
                    s.capacity,
                )
                for s in data.class_slots
            ],
        )
        conn.executemany(
            INSERT_CHECKIN,
            [
                (c.checkin_id, c.member_id, c.slot_id, c.checked_in_at.strftime(DATETIME_FMT))
                for c in data.checkins
            ],
        )
        conn.executemany(
            INSERT_EXPECTED_PAYMENT,
            [
                (
                    p.expected_payment_id,
                    p.membership_id,
                    p.member_id,
                    p.due_date.isoformat(),
                    p.amount_cents,
                    p.reference,
                )
                for p in data.expected_payments
            ],
        )


def table_checksums(conn: sqlite3.Connection) -> dict[str, str]:
    """SHA-256 of each table's rows, dumped in primary-key order."""
    checksums = {}
    for table, sql in TABLE_DUMP_SQL.items():
        digest = hashlib.sha256()
        for row in conn.execute(sql):
            digest.update(repr(row).encode("utf-8"))
            digest.update(b"\n")
        checksums[table] = digest.hexdigest()
    return checksums


def database_checksum(conn: sqlite3.Connection) -> str:
    """Single SHA-256 over all table checksums; equal iff every table's content is equal."""
    digest = hashlib.sha256()
    for table, table_digest in sorted(table_checksums(conn).items()):
        digest.update(f"{table}:{table_digest}\n".encode())
    return digest.hexdigest()


def row_counts(conn: sqlite3.Connection) -> dict[str, int]:
    """Row count per table."""
    return {table: conn.execute(sql).fetchone()[0] for table, sql in TABLE_COUNT_SQL.items()}


def seed_database(db_path: str | Path, config: SeedConfig) -> SeedSummary:
    """Rebuild the database at `db_path` from scratch.

    Builds into a sibling temp file and atomically replaces the target, so a
    failed run never leaves a half-seeded database behind.
    """
    target = Path(db_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    tmp.unlink(missing_ok=True)

    data = generate(config)
    conn = get_write_connection(tmp)
    try:
        apply_schema(conn)
        write(conn, data)
        violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError(f"foreign key violations after seeding: {violations[:5]}")
        summary = SeedSummary(
            db_path=str(target),
            row_counts=row_counts(conn),
            table_checksums=table_checksums(conn),
            checksum=database_checksum(conn),
        )
    except BaseException:
        conn.close()
        tmp.unlink(missing_ok=True)
        raise
    conn.close()
    os.replace(tmp, target)
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point: seed the database and print row counts and checksum."""
    settings = get_settings()
    parser = argparse.ArgumentParser(description="Build the synthetic gym ops database.")
    parser.add_argument("--db-path", default=settings.DB_PATH)
    parser.add_argument("--seed", type=int, default=settings.SEED)
    parser.add_argument("--members", type=int, default=settings.SEED_MEMBERS)
    parser.add_argument(
        "--reference-date",
        type=date.fromisoformat,
        default=settings.REFERENCE_DATE,
        help="Fixed 'today' the data is generated relative to (YYYY-MM-DD).",
    )
    args = parser.parse_args(argv)
    config = SeedConfig(seed=args.seed, members=args.members, reference_date=args.reference_date)

    summary = seed_database(args.db_path, config)
    print(f"Seeded {summary.db_path} (seed={config.seed}, reference={config.reference_date})")
    for table, count in summary.row_counts.items():
        print(f"  {table:<20} {count:>6}")
    print(f"  checksum {summary.checksum}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
