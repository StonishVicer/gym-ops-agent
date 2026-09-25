"""Security properties a reviewer should not have to take on trust (NFR-4, NFR-7)."""

import ast
import re
import sqlite3
from collections.abc import Callable
from datetime import date
from pathlib import Path

import pytest
from mcp import Client
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import TextContent

import gym_ops.mcp_server as mcp_pkg
from gym_ops.mcp_server import queries
from gym_ops.mcp_server.server import (
    _open_db,
    build_server,
    find_members,
    get_class_occupancy,
    list_unpaid_members,
    reconcile_payments,
)

INJECTION = "2026-09'; DROP TABLE members;--"
SERVER_SOURCES = sorted(Path(mcp_pkg.__file__).parent.glob("*.py"))
WRITE_STATEMENTS = [
    "INSERT INTO members (full_name, email, phone, status, joined_on) "
    "VALUES ('X', 'x@example.com', '1', 'active', '2026-01-01')",
    "UPDATE members SET status = 'frozen'",
    "DELETE FROM members",
    "DROP TABLE members",
    "CREATE TABLE sneaky (id INTEGER)",
    "ATTACH DATABASE ':memory:' AS other",
    "PRAGMA query_only = OFF",
]


D = date.fromisoformat


def _member_count(path: Path) -> int:
    conn = sqlite3.connect(path)
    try:
        return int(conn.execute("SELECT COUNT(*) FROM members").fetchone()[0])
    finally:
        conn.close()


@pytest.mark.parametrize("sql", WRITE_STATEMENTS)
def test_writes_raise(seeded_db: Path, sql: str) -> None:
    with pytest.raises(ToolError) as info, _open_db() as conn:
        conn.execute(sql)
        # Even if `PRAGMA query_only = OFF` is accepted, the mode=ro open still refuses this.
        conn.execute("DELETE FROM members")
    assert isinstance(info.value.__cause__, sqlite3.OperationalError)
    assert _member_count(seeded_db) == 300


@pytest.mark.parametrize(
    "call",
    [
        lambda: list_unpaid_members(INJECTION),
        lambda: get_class_occupancy(INJECTION, D("2026-09-30")),  # type: ignore[arg-type]
        lambda: get_class_occupancy(D("2026-09-01"), INJECTION),  # type: ignore[arg-type]
        lambda: reconcile_payments(INJECTION, D("2026-09-30")),  # type: ignore[arg-type]
        lambda: find_members(status=INJECTION),  # type: ignore[arg-type]
        lambda: get_class_occupancy(D("2026-09-01"), D("2026-09-30"), class_name=INJECTION),  # type: ignore[arg-type]
    ],
    ids=["month", "start_date", "end_date", "period_start", "status", "class_name"],
)
def test_injection_rejected_by_validation(
    seeded_db: Path,
    table_exists: Callable[[Path, str], bool],
    call: Callable[[], object],
) -> None:
    with pytest.raises(ToolError, match="Invalid arguments"):
        call()
    assert table_exists(seeded_db, "members")
    assert _member_count(seeded_db) == 300


def test_injection_in_free_text_is_inert(
    seeded_db: Path, table_exists: Callable[[Path, str], bool]
) -> None:
    # name_query is free text by design: it is bound as a parameter, never parsed as SQL.
    assert find_members(name_query=INJECTION).members == []
    assert find_members(name_query="' OR '1'='1").members == []
    assert table_exists(seeded_db, "members")
    assert _member_count(seeded_db) == 300


@pytest.mark.anyio
async def test_injection_over_mcp(
    seeded_db: Path, table_exists: Callable[[Path, str], bool]
) -> None:
    async with Client(build_server()) as client:
        result = await client.call_tool("list_unpaid_members", {"month": INJECTION})
    assert result.is_error is True
    [block] = result.content
    assert isinstance(block, TextContent)
    assert "month: must be a month in YYYY-MM format" in block.text
    assert INJECTION not in block.text  # rejected input is not echoed back
    assert table_exists(seeded_db, "members")


def _string_constants(path: Path) -> list[str]:
    return [
        node.value
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]


@pytest.mark.parametrize("path", SERVER_SOURCES, ids=lambda p: p.name)
def test_no_write_sql_literals(path: Path) -> None:
    pattern = re.compile(
        r"\b(INSERT|UPDATE|DELETE|DROP|CREATE|ALTER|ATTACH|DETACH|REPLACE|VACUUM)\b"
    )
    offenders = [s for s in _string_constants(path) if pattern.search(s)]
    assert offenders == []


@pytest.mark.parametrize("path", SERVER_SOURCES, ids=lambda p: p.name)
def test_sql_is_never_built_dynamically(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in {"execute", "executemany", "executescript"}
        ):
            sql = node.args[0]
            # Only a named constant (queries.X) or a pass-through parameter reaches execute.
            assert isinstance(sql, ast.Attribute | ast.Name), ast.unparse(node)


def test_queries_are_plain_literals() -> None:
    tree = ast.parse(Path(queries.__file__).read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            assert isinstance(node.value, ast.Constant), ast.unparse(node)
            assert isinstance(node.value.value, str)
            assert "?" in node.value.value


@pytest.mark.parametrize("path", SERVER_SOURCES, ids=lambda p: p.name)
def test_only_readonly_connection_factory(path: Path) -> None:
    source = path.read_text(encoding="utf-8")
    assert "get_write_connection" not in source
    assert "sqlite3.connect" not in source
