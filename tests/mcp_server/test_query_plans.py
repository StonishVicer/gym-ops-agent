"""FR-3: every MCP query reaches the large tables through an index, never a full scan.

EXPLAIN QUERY PLAN runs on the seeded DB with a realistic set of transfers loaded.
No ANALYZE is run (neither `make seed` nor the tests create sqlite_stat1), so these
are the plans the server actually gets.
"""

import re
from collections.abc import Iterator
from pathlib import Path

import pytest

from gym_ops.db.connection import get_readonly_connection
from gym_ops.mcp_server import queries

# Tables whose rows grow with members x time. A SCAN of any of them fails the test.
LARGE_TABLES = {"checkins", "expected_payments", "memberships", "extracted_payments", "class_slots"}
SCHEMA_TABLES = LARGE_TABLES | {"members"}

# Representative parameters for every query, in placeholder order.
PARAMS: dict[str, list[tuple[object, ...]]] = {
    "OCCUPANCY_SLOTS": [
        ("2026-09-01", "2026-09-21", None, None, 201),
        ("2026-09-01", "2026-09-21", "Yoga", "Yoga", 201),
    ],
    "OCCUPANCY_BY_CLASS": [("2026-09-01", "2026-09-21", None, None, 200)],
    "OCCUPANCY_BY_WEEKDAY_HOUR": [("2026-09-01", "2026-09-21", None, None, 168)],
    "FIND_MEMBERS": [
        ("%ana%", "%ana%", None, None, 21),
        (None, None, "active", "active", 21),
    ],
    "BILLS_DUE_BETWEEN": [("2026-08-22", "2026-10-10", 50_001)],
    "TRANSFERS_FOR_PERIOD": [("2026-08-27", "2026-10-05", "2026-09-01", "2026-09-30", 50_001)],
    "SLOT_DATE_RANGE": [()],
    "BILL_DUE_DATE_RANGE": [()],
}

# Indexes each query must use: the index-to-tool mapping in docs/architecture.md §2.
EXPECTED_INDEXES: dict[str, set[str]] = {
    "OCCUPANCY_SLOTS": {"idx_class_slots_starts_at", "idx_checkins_slot"},
    "OCCUPANCY_BY_CLASS": {"idx_class_slots_starts_at", "idx_checkins_slot"},
    "OCCUPANCY_BY_WEEKDAY_HOUR": {"idx_class_slots_starts_at", "idx_checkins_slot"},
    "FIND_MEMBERS": {"idx_memberships_member"},
    "BILLS_DUE_BETWEEN": {"idx_expected_due"},
    "TRANSFERS_FOR_PERIOD": {"idx_extracted_date", "idx_extracted_reference", "idx_expected_due"},
    # Each MIN/MAX subquery must be an index seek, not a scan of the index.
    "SLOT_DATE_RANGE": {"idx_class_slots_starts_at"},
    "BILL_DUE_DATE_RANGE": {"idx_expected_due"},
}

# Scans that are allowed, with the reason. Keyed by (query, table).
ALLOWED_SCANS: dict[tuple[str, str], str] = {
    # `name_query` is a substring match (LIKE '%x%'), which no B-tree index can serve,
    # and the status filter is optional, so the query reads members once. That table
    # is one row per member (hundreds), and each row costs one PK lookup plus one
    # idx_memberships_member search; the LIMIT caps what is returned, not what is read.
    ("FIND_MEMBERS", "members"): "substring LIKE cannot use an index",
}

_TABLE_REF = re.compile(r"\b(?:FROM|JOIN)\s+(\w+)(?:\s+AS\s+(\w+))?", flags=re.IGNORECASE)


def _aliases(sql: str) -> dict[str, str]:
    """Map every schema table name and alias in `sql` to its table (CTEs are skipped)."""
    out: dict[str, str] = {}
    for table, alias in _TABLE_REF.findall(sql):
        if table not in SCHEMA_TABLES:
            continue
        out[table] = table
        if alias:
            out[alias] = table
    return out


def _query_names() -> list[str]:
    return sorted(n for n in dir(queries) if n.isupper() and isinstance(getattr(queries, n), str))


@pytest.fixture(scope="module")
def plans(paid_db_path: Path) -> Iterator[dict[str, list[list[str]]]]:
    conn = get_readonly_connection(paid_db_path)
    try:
        yield {
            name: [
                [row[3] for row in conn.execute("EXPLAIN QUERY PLAN " + getattr(queries, name), p)]
                for p in PARAMS[name]
            ]
            for name in PARAMS
        }
    finally:
        conn.close()


def test_every_query_is_checked() -> None:
    assert set(PARAMS) == set(_query_names()) == set(EXPECTED_INDEXES)


@pytest.mark.parametrize("name", _query_names())
def test_no_full_scans(plans: dict[str, list[list[str]]], name: str) -> None:
    aliases = _aliases(getattr(queries, name))
    for plan in plans[name]:
        for step in plan:
            if not step.startswith("SCAN "):
                continue
            # Scans of CTEs / subqueries (`SCAN slot`, `SCAN scoped`, `SCAN (subquery-1)`)
            # read rows an indexed inner query already filtered.
            table = aliases.get(step.split()[1])
            if table is None:
                continue
            if (name, table) in ALLOWED_SCANS:
                continue
            assert table not in LARGE_TABLES, f"{name}: full scan of {table}: {plan}"
            pytest.fail(f"{name}: unexpected scan '{step}' with no documented reason: {plan}")


@pytest.mark.parametrize("name", _query_names())
def test_expected_indexes_used(plans: dict[str, list[list[str]]], name: str) -> None:
    for plan in plans[name]:
        used = {m for step in plan for m in re.findall(r"INDEX (\w+)", step)}
        assert EXPECTED_INDEXES[name] <= used, f"{name}: {plan}"


def test_allowed_scans_are_still_needed(plans: dict[str, list[list[str]]]) -> None:
    """An exemption that no longer matches a real scan should be deleted."""
    for query, table in ALLOWED_SCANS:
        aliases = _aliases(getattr(queries, query))
        scanned = {
            aliases.get(step.split()[1])
            for plan in plans[query]
            for step in plan
            if step.startswith("SCAN ")
        }
        assert table in scanned, (query, table)
