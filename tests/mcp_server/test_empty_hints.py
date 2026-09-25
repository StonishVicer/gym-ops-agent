"""`available_range` hint: present only when a date-filtered tool finds nothing."""

import json
import sqlite3
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from mcp import Client
from mcp_types import TextContent

from gym_ops.mcp_server.models import DateRange
from gym_ops.mcp_server.server import (
    build_server,
    get_class_occupancy,
    list_unpaid_members,
    reconcile_payments,
)

from .conftest import FixtureDb

D = date.fromisoformat
HINTED_TOOLS = {"get_class_occupancy", "list_unpaid_members", "reconcile_payments"}


SLOT_SPAN = "SELECT MIN(substr(starts_at, 1, 10)), MAX(substr(starts_at, 1, 10)) FROM class_slots"
BILL_SPAN = "SELECT MIN(due_date), MAX(due_date) FROM expected_payments"


def _span(path: Path, sql: str) -> tuple[str, str]:
    conn = sqlite3.connect(path)
    try:
        low, high = conn.execute(sql).fetchone()
        return low, high
    finally:
        conn.close()


# --- get_class_occupancy ---------------------------------------------------------


def test_occupancy_empty_range_returns_hint(seeded_db: Path) -> None:
    result = get_class_occupancy(D("2025-01-01"), D("2025-01-31"))

    assert result.total_slots == 0
    assert result.slots == []
    assert result.available_range is not None
    expected = _span(seeded_db, SLOT_SPAN)
    assert result.model_dump(mode="json")["available_range"] == {
        "from": expected[0],
        "to": expected[1],
    }
    assert expected == ("2026-06-29", "2026-09-19")  # the deterministic seed's span


def test_occupancy_empty_class_filter_returns_hint(fixture_db: FixtureDb) -> None:
    fixture_db.slot("Yoga", "2026-09-07T07:00:00")
    fixture_db.slot("Yoga", "2026-09-12T09:00:00")

    result = get_class_occupancy(D("2026-09-01"), D("2026-09-30"), class_name="HIIT")

    assert result.total_slots == 0
    assert result.available_range == DateRange(from_=D("2026-09-07"), to=D("2026-09-12"))


def test_occupancy_with_results_has_no_hint(seeded_db: Path) -> None:
    result = get_class_occupancy(D("2026-09-01"), D("2026-09-20"))

    assert result.total_slots > 0
    assert result.available_range is None
    assert "available_range" not in result.model_dump()
    assert "available_range" not in result.model_dump_json()


def test_occupancy_no_hint_when_no_slots_exist(fixture_db: FixtureDb) -> None:
    result = get_class_occupancy(D("2026-09-01"), D("2026-09-30"))
    assert result.total_slots == 0
    assert result.available_range is None  # nothing to point at


# --- list_unpaid_members --------------------------------------------------------


def test_unpaid_month_without_bills_returns_hint(seeded_db: Path) -> None:
    result = list_unpaid_members("2025-01")

    assert result.bills == []
    low, high = _span(seeded_db, BILL_SPAN)
    assert result.available_range == DateRange(from_=D(low), to=D(high))


def test_unpaid_month_fully_paid_has_no_hint(fixture_db: FixtureDb) -> None:
    member = fixture_db.member("Paid Up")
    fixture_db.membership(member)
    fixture_db.bill(member, "2026-09-10", "GYM-1")
    fixture_db.transfer("r1", 5000, "2026-09-10", reference="GYM-1")

    result = list_unpaid_members("2026-09")

    assert result.bills == []  # nobody owes: a real answer, not missing data
    assert result.available_range is None
    assert "available_range" not in result.model_dump()


def test_unpaid_with_results_has_no_hint(seeded_db: Path) -> None:
    result = list_unpaid_members("2026-09")
    assert result.bills
    assert "available_range" not in result.model_dump()


# --- reconcile_payments ---------------------------------------------------------


def test_reconcile_empty_period_returns_hint(seeded_db: Path) -> None:
    result = reconcile_payments(D("2025-01-01"), D("2025-03-31"))

    assert (result.bills, result.unidentified) == ([], [])
    assert result.totals.bills == 0
    low, high = _span(seeded_db, BILL_SPAN)
    assert result.available_range == DateRange(from_=D(low), to=D(high))


def test_reconcile_only_unidentified_has_no_hint(fixture_db: FixtureDb) -> None:
    member = fixture_db.member("Someone")
    fixture_db.membership(member)
    fixture_db.bill(member, "2026-09-10", "GYM-1")
    fixture_db.transfer("r1", 5000, "2026-10-15", member_id=None)

    result = reconcile_payments(D("2026-10-01"), D("2026-10-31"))

    assert result.bills == []
    assert len(result.unidentified) == 1
    assert result.available_range is None


def test_reconcile_with_results_has_no_hint(seeded_db: Path) -> None:
    result = reconcile_payments(D("2026-09-01"), D("2026-09-30"))
    assert result.bills
    assert "available_range" not in result.model_dump()


# --- over MCP ---------------------------------------------------------------------


@pytest.mark.anyio
async def test_hint_on_the_wire(seeded_db: Path) -> None:
    async with Client(build_server()) as client:
        empty = await client.call_tool(
            "get_class_occupancy", {"start_date": "2025-01-01", "end_date": "2025-01-31"}
        )
        full = await client.call_tool(
            "get_class_occupancy", {"start_date": "2026-09-01", "end_date": "2026-09-20"}
        )

    assert empty.structured_content is not None
    assert empty.structured_content["available_range"] == {
        "from": "2026-06-29",
        "to": "2026-09-19",
    }
    [block] = empty.content
    assert isinstance(block, TextContent)
    assert json.loads(block.text)["available_range"] == {"from": "2026-06-29", "to": "2026-09-19"}

    assert full.structured_content is not None
    assert "available_range" not in full.structured_content
    [block] = full.content
    assert isinstance(block, TextContent)
    assert "available_range" not in block.text


@pytest.mark.anyio
async def test_hint_documented_for_the_model() -> None:
    async with Client(build_server()) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
    for name in HINTED_TOOLS:
        tool = tools[name]
        assert tool.description is not None
        assert "available_range" in tool.description
        assert tool.output_schema is not None
        props: dict[str, Any] = tool.output_schema["properties"]
        assert "available_range" in props
        assert "available_range" not in tool.output_schema.get("required", [])
    assert "available_range" not in (tools["find_members"].description or "")
