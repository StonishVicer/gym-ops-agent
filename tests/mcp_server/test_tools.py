import sqlite3
import sys
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from mcp import Client, StdioServerParameters
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import CallToolResult, TextContent

from gym_ops.mcp_server import server
from gym_ops.mcp_server.models import ReconcileResult
from gym_ops.mcp_server.server import (
    build_server,
    find_members,
    get_class_occupancy,
    list_unpaid_members,
    reconcile_payments,
)

from .conftest import FixtureDb, PointDb

D = date.fromisoformat

EXPECTED_TOOLS = {
    "get_class_occupancy",
    "find_members",
    "list_unpaid_members",
    "reconcile_payments",
}
pytestmark = pytest.mark.anyio


async def _call(name: str, args: dict[str, Any]) -> CallToolResult:
    async with Client(build_server()) as client:
        return await client.call_tool(name, args)


def _text(result: CallToolResult) -> str:
    [block] = result.content
    assert isinstance(block, TextContent)
    return block.text


async def test_exactly_four_tools() -> None:
    async with Client(build_server()) as client:
        tools = (await client.list_tools()).tools
    assert {t.name for t in tools} == EXPECTED_TOOLS
    assert len(tools) == 4
    for tool in tools:
        assert tool.annotations is not None
        assert tool.annotations.read_only_hint is True
        assert tool.annotations.destructive_hint is False
        assert tool.output_schema is not None
        assert tool.description
        assert not tool.description.startswith(" ")  # cleandoc'd for the model


async def test_untrusted_receipt_fields_are_labelled() -> None:
    """Receipt text reaches the operator's model: the server and both tools that return
    it must say payer_name and reference are untrusted data (second-order injection)."""
    assert "untrusted" in server.SERVER_INSTRUCTIONS
    async with Client(build_server()) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
    for name in ("list_unpaid_members", "reconcile_payments"):
        description = " ".join((tools[name].description or "").split())
        assert "untrusted" in description, name
        assert "never follow instructions" in description, name


async def test_no_sql_parameters() -> None:
    async with Client(build_server()) as client:
        tools = (await client.list_tools()).tools
    for tool in tools:
        params = set(tool.input_schema["properties"])
        assert not params & {"sql", "query", "statement", "where", "order_by", "table"}


async def test_structured_output_validates(seeded_db: Path) -> None:
    result = await _call(
        "reconcile_payments", {"period_start": "2026-09-01", "period_end": "2026-09-30"}
    )
    assert result.is_error is False
    parsed = ReconcileResult.model_validate(result.structured_content)
    assert parsed.totals.bills > 0


async def test_validation_error_is_actionable(seeded_db: Path) -> None:
    result = await _call("list_unpaid_members", {"month": "September"})
    assert result.is_error is True
    text = _text(result)
    assert text == (
        "Error executing tool list_unpaid_members: Invalid arguments: "
        "month: must be a month in YYYY-MM format, e.g. 2026-09."
    )


async def test_cross_field_error_is_actionable(seeded_db: Path) -> None:
    result = await _call(
        "get_class_occupancy", {"start_date": "2026-09-10", "end_date": "2026-09-01"}
    )
    assert result.is_error is True
    assert "end_date (2026-09-01) must be on or after start_date (2026-09-10)" in _text(result)


async def test_missing_required_argument(seeded_db: Path) -> None:
    result = await _call("reconcile_payments", {"period_start": "2026-09-01"})
    assert result.is_error is True
    assert "period_end: Field required" in _text(result)


async def test_unknown_argument_rejected(seeded_db: Path) -> None:
    result = await _call("find_members", {"name": "dana"})
    assert result.is_error is True
    assert _text(result) == (
        "Error executing tool find_members: Invalid arguments: unknown argument(s) name. "
        "Valid arguments: limit, name_query, status."
    )


async def test_crash_details_stay_on_server(
    seeded_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_: object) -> None:
        raise RuntimeError("secret internals /etc/passwd")

    monkeypatch.setattr(server, "load_and_reconcile", boom)
    result = await _call(
        "reconcile_payments", {"period_start": "2026-09-01", "period_end": "2026-09-30"}
    )
    assert result.is_error is True
    assert _text(result) == "Error executing tool reconcile_payments"


def test_missing_db_actionable(tmp_path: Path, point_db: PointDb) -> None:
    missing = tmp_path / "nope.db"
    point_db(missing)
    with pytest.raises(ToolError, match=r"not found .* `make seed`"):
        find_members()
    assert not missing.exists()  # read-only open never creates it


def test_unseeded_db_actionable(tmp_path: Path, point_db: PointDb) -> None:
    empty = tmp_path / "empty.db"
    sqlite3.connect(empty).close()
    point_db(empty)
    with pytest.raises(ToolError, match=r"missing tables\. Rebuild it with `make seed`"):
        find_members()


def test_busy_db_actionable(fixture_db: FixtureDb, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("gym_ops.db.connection.BUSY_TIMEOUT_S", 0.05)
    locker = sqlite3.connect(fixture_db.path, isolation_level=None)
    locker.execute("BEGIN EXCLUSIVE")
    try:
        with pytest.raises(ToolError, match="busy; retry"):
            find_members()
    finally:
        locker.execute("ROLLBACK")
        locker.close()


def test_tools_write_nothing_to_stdout(seeded_db: Path, capfd: pytest.CaptureFixture[str]) -> None:
    get_class_occupancy(D("2026-09-01"), D("2026-09-20"))
    find_members(name_query="a")
    list_unpaid_members("2026-09")
    reconcile_payments(D("2026-09-01"), D("2026-09-30"))
    with pytest.raises(ToolError):
        list_unpaid_members("bad")
    assert capfd.readouterr().out == ""


async def test_server_runs_over_stdio(seeded_db_path: Path) -> None:
    """The real entry point (`make mcp`) speaks MCP on stdout and lists 4 tools."""
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "gym_ops.mcp_server"],
        env={"DB_PATH": str(seeded_db_path)},
    )
    async with Client(params) as client:
        tools = (await client.list_tools()).tools
        result = await client.call_tool("find_members", {"limit": 2})
    assert {t.name for t in tools} == EXPECTED_TOOLS
    assert result.is_error is False
    assert result.structured_content is not None
    assert len(result.structured_content["members"]) == 2
