import pytest
from mcp.server.mcpserver.exceptions import ToolError

from gym_ops.mcp_server.models import MemberSummary
from gym_ops.mcp_server.server import find_members

from .conftest import FixtureDb


@pytest.fixture
def people(fixture_db: FixtureDb) -> FixtureDb:
    for name, status in [
        ("Dana Smith", "active"),
        ("Diana Reed", "frozen"),
        ("Bob Stone", "cancelled"),
        ("Ann 100% Real", "active"),
        ("Under_Score", "active"),
    ]:
        fixture_db.member(name, status)
    return fixture_db


def test_substring_case_insensitive(people: FixtureDb) -> None:
    names = [m.full_name for m in find_members(name_query="ANA").members]
    assert names == ["Dana Smith", "Diana Reed"]


@pytest.mark.parametrize(("query", "expected"), [("%", ["Ann 100% Real"]), ("_", ["Under_Score"])])
def test_like_wildcards_escaped(people: FixtureDb, query: str, expected: list[str]) -> None:
    assert [m.full_name for m in find_members(name_query=query).members] == expected


def test_percent_matches_nothing_without_literal(fixture_db: FixtureDb) -> None:
    fixture_db.member("Dana Smith")
    assert find_members(name_query="%").members == []


def test_status_filter(people: FixtureDb) -> None:
    assert [m.full_name for m in find_members(status="frozen").members] == ["Diana Reed"]


def test_current_membership_is_latest_ending(fixture_db: FixtureDb) -> None:
    member = fixture_db.member("Dana Smith")
    fixture_db.membership(member, plan="annual", start="2025-01-01", end="2025-12-31")
    fixture_db.membership(member, plan="student", start="2026-01-01", end="2026-12-31")

    [row] = find_members(name_query="dana").members

    assert row.plan == "student"
    assert row.membership_end_date is not None
    assert row.membership_end_date.isoformat() == "2026-12-31"


def test_member_without_membership(people: FixtureDb) -> None:
    [row] = find_members(name_query="bob").members
    assert (row.plan, row.membership_end_date) == (None, None)


def test_limit_boundaries(people: FixtureDb) -> None:
    one = find_members(limit=1)
    assert len(one.members) == 1
    assert one.truncated is True
    fifty = find_members(limit=50)
    assert len(fifty.members) == 5
    assert fifty.truncated is False


@pytest.mark.parametrize("limit", [0, 51, 500, -1, True, 5.0, "10"])
def test_limit_bounds(people: FixtureDb, limit: object) -> None:
    with pytest.raises(ToolError, match="limit"):
        find_members(limit=limit)  # type: ignore[arg-type]


@pytest.mark.parametrize("query", ["", "   ", "a" * 101, "bad\x00byte", "tab\tchar"])
def test_name_query_rejected(people: FixtureDb, query: str) -> None:
    with pytest.raises(ToolError, match="name_query"):
        find_members(name_query=query)


def test_unknown_status_rejected(people: FixtureDb) -> None:
    with pytest.raises(ToolError, match="status"):
        find_members(status="deleted")  # type: ignore[arg-type]


def test_no_contact_pii_in_output() -> None:
    assert not {"email", "phone"} & set(MemberSummary.model_fields)
