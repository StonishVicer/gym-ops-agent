"""NFR-7: no SQL is built with f-strings, `%`, `+`, or `.format()` anywhere in gym_ops."""

import ast
import tomllib
from pathlib import Path

import pytest

import gym_ops

SOURCES = sorted(Path(gym_ops.__file__).parent.rglob("*.py"))
EXECUTE_METHODS = {"execute", "executemany", "executescript"}


def _is_dynamic(node: ast.expr) -> bool:
    if isinstance(node, ast.JoinedStr | ast.BinOp):
        return True
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "format"
    )


@pytest.mark.parametrize("path", SOURCES, ids=lambda p: str(p.relative_to(p.parents[1])))
def test_execute_never_receives_built_sql(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    offenders = [
        f"{path.name}:{node.lineno}: {ast.unparse(node)}"
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in EXECUTE_METHODS
        and node.args
        and _is_dynamic(node.args[0])
    ]
    assert offenders == []


def test_ruff_sql_injection_rule_enabled() -> None:
    config = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())
    selected = config["tool"]["ruff"]["lint"]["select"]
    assert "S" in selected or "S608" in selected  # flake8-bandit S608: SQL built from strings


@pytest.mark.parametrize(
    ("snippet", "dynamic"),
    [
        ('conn.execute(f"SELECT * FROM t WHERE id = {x}")', True),
        ('conn.execute("SELECT * FROM t WHERE id = %s" % x)', True),
        ('conn.execute("SELECT * FROM t WHERE id = " + x)', True),
        ('conn.execute("SELECT * FROM t WHERE id = {}".format(x))', True),
        ('conn.execute("SELECT * FROM t WHERE id = ?", (x,))', False),
        ("conn.execute(queries.FIND_MEMBERS, params)", False),
    ],
)
def test_detector(snippet: str, dynamic: bool) -> None:
    call = ast.parse(snippet).body[0]
    assert isinstance(call, ast.Expr) and isinstance(call.value, ast.Call)
    assert _is_dynamic(call.value.args[0]) is dynamic
