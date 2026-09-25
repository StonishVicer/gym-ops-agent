from datetime import date, timedelta
from pathlib import Path
from typing import get_args

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from gym_ops.db.seed import CLASS_NAMES
from gym_ops.mcp_server.models import ClassName
from gym_ops.mcp_server.server import get_class_occupancy

from .conftest import FixtureDb

D = date.fromisoformat


def test_slot_occupancy_pct(fixture_db: FixtureDb) -> None:
    slot = fixture_db.slot("HIIT", "2026-09-07T18:00:00", capacity=20)
    fixture_db.checkins(slot, 15)

    result = get_class_occupancy(D("2026-09-07"), D("2026-09-07"))

    assert len(result.slots) == 1
    row = result.slots[0]
    assert (row.capacity, row.checkins, row.occupancy_pct) == (20, 15, 75.0)
    assert row.weekday == "Monday"
    assert (result.total_checkins, result.occupancy_pct) == (15, 75.0)
    assert result.by_weekday_hour[0].weekday == "Monday"
    assert result.by_weekday_hour[0].hour == 18
    assert result.truncated is False


def test_empty_slot_counts_zero(fixture_db: FixtureDb) -> None:
    fixture_db.slot("Yoga", "2026-09-08T07:00:00")
    result = get_class_occupancy(D("2026-09-08"), D("2026-09-08"))
    assert result.slots[0].checkins == 0
    assert result.slots[0].occupancy_pct == 0.0


def test_date_bounds_are_inclusive(fixture_db: FixtureDb) -> None:
    fixture_db.slot("Spin", "2026-09-01T06:00:00")
    fixture_db.slot("Spin", "2026-09-30T20:00:00")
    fixture_db.slot("Spin", "2026-10-01T06:00:00")
    fixture_db.slot("Spin", "2026-08-31T20:00:00")

    result = get_class_occupancy(D("2026-09-01"), D("2026-09-30"))

    assert [s.starts_at for s in result.slots] == ["2026-09-01T06:00:00", "2026-09-30T20:00:00"]


def test_class_filter(fixture_db: FixtureDb) -> None:
    fixture_db.slot("Yoga", "2026-09-07T07:00:00")
    fixture_db.slot("HIIT", "2026-09-07T08:00:00")

    result = get_class_occupancy(D("2026-09-07"), D("2026-09-07"), class_name="Yoga")

    assert [s.class_name for s in result.slots] == ["Yoga"]
    assert [a.class_name for a in result.by_class] == ["Yoga"]


def test_slots_truncated_at_200(fixture_db: FixtureDb) -> None:
    start = date(2026, 1, 1)
    for i in range(201):
        fixture_db.slot("Boxing", f"{(start + timedelta(days=i)).isoformat()}T12:00:00")

    result = get_class_occupancy(D("2026-01-01"), D("2026-12-31"))

    assert len(result.slots) == 200
    assert result.truncated is True
    assert result.total_slots == 201  # aggregates cover every slot


def test_seeded_peak_hours(seeded_db: Path) -> None:
    result = get_class_occupancy(D("2026-07-01"), D("2026-09-20"))
    top_hours = {a.hour for a in result.by_weekday_hour[:10]}
    assert top_hours <= {6, 7, 18, 19}  # seed peaks: 06-08 and 18-20


def test_max_range_boundary(fixture_db: FixtureDb) -> None:
    get_class_occupancy(D("2026-01-01"), D("2027-01-01"))  # 366 days inclusive: allowed
    with pytest.raises(ToolError, match="spans 367 days; the maximum is 366"):
        get_class_occupancy(D("2026-01-01"), D("2027-01-02"))


def test_single_day_range_allowed(fixture_db: FixtureDb) -> None:
    assert get_class_occupancy(D("2026-09-07"), D("2026-09-07")).total_slots == 0


def test_end_before_start_rejected(fixture_db: FixtureDb) -> None:
    with pytest.raises(ToolError, match=r"end_date \(2026-09-01\) must be on or after start_date"):
        get_class_occupancy(D("2026-09-02"), D("2026-09-01"))


@pytest.mark.parametrize(
    "bad", ["2026-9-1", "2026-02-30", "2026-09-01T00:00:00", "yesterday", "", 1_780_000_000]
)
def test_bad_dates_rejected(fixture_db: FixtureDb, bad: object) -> None:
    with pytest.raises(ToolError, match="start_date: must be a calendar date in YYYY-MM-DD"):
        get_class_occupancy(bad, D("2026-09-01"))  # type: ignore[arg-type]


def test_unknown_class_rejected(fixture_db: FixtureDb) -> None:
    with pytest.raises(ToolError, match="class_name"):
        get_class_occupancy(D("2026-09-01"), D("2026-09-02"), class_name="Zumba")  # type: ignore[arg-type]


def test_class_enum_matches_seed() -> None:
    assert set(get_args(ClassName)) == set(CLASS_NAMES)
