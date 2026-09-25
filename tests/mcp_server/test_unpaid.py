import pytest
from mcp.server.mcpserver.exceptions import ToolError

from gym_ops.mcp_server.server import list_unpaid_members

from .conftest import FixtureDb


@pytest.fixture
def three_bills(fixture_db: FixtureDb) -> FixtureDb:
    for i, name in enumerate(["Ann Unpaid", "Ben Paid", "Cat Partial"], start=1):
        member = fixture_db.member(name)
        fixture_db.membership(member)
        fixture_db.bill(member, f"2026-09-0{i}", f"GYM-00000{i}-2026-09")
    fixture_db.transfer("paid", 5000, "2026-09-02", reference="GYM-000002-2026-09")
    fixture_db.transfer("part", 4000, "2026-09-03", reference="GYM-000003-2026-09")
    return fixture_db


def test_outstanding_after_topup(three_bills: FixtureDb) -> None:
    result = list_unpaid_members("2026-09")
    assert [(b.member_name, b.outstanding_cents) for b in result.bills] == [
        ("Ann Unpaid", 5000),
        ("Cat Partial", 1000),
    ]
    assert (result.total_outstanding_cents, result.total_outstanding) == (6000, "$60.00")

    three_bills.transfer("topup", 1000, "2026-09-04", reference="GYM-000003-2026-09")
    assert [b.member_name for b in list_unpaid_members("2026-09").bills] == ["Ann Unpaid"]


def test_month_boundaries(fixture_db: FixtureDb) -> None:
    member = fixture_db.member("Dee Cember")
    fixture_db.membership(member)
    fixture_db.bill(member, "2026-12-31", "GYM-A")
    fixture_db.bill(member, "2027-01-01", "GYM-B")
    fixture_db.bill(member, "2026-02-28", "GYM-C")
    assert [b.reference for b in list_unpaid_members("2026-12").bills] == ["GYM-A"]
    assert [b.reference for b in list_unpaid_members("2027-01").bills] == ["GYM-B"]
    assert [b.reference for b in list_unpaid_members("2026-02").bills] == ["GYM-C"]


def test_truncation_flag(fixture_db: FixtureDb) -> None:
    for i in range(250):
        member = fixture_db.member(f"Member {i:03d}")
        fixture_db.membership(member)
        fixture_db.bill(member, "2026-09-15", f"GYM-{i:04d}")

    result = list_unpaid_members("2026-09")

    assert len(result.bills) == 200
    assert result.truncated is True
    assert result.total_outstanding_cents == 250 * 5000


@pytest.mark.parametrize(
    "month", ["2026-9", "2026-13", "2026-00", "26-09", "2026-09-01", "2026/09", "", 202609]
)
def test_bad_month_rejected(fixture_db: FixtureDb, month: object) -> None:
    with pytest.raises(ToolError, match="month: must be a month in YYYY-MM format"):
        list_unpaid_members(month)  # type: ignore[arg-type]
