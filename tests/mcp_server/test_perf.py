"""NFR-8: p95 tool latency < 100 ms on the seeded DB.

Each tool is called in-process (validation, read-only open, queries, model build)
over the whole seeded window, which is its heaviest realistic input. Wall-clock
timing depends on the machine, so this is marked `perf`: `make test` runs it,
`make test-ci` skips it so a slow shared runner cannot fail the build at random.
"""

import math
import time
from collections.abc import Callable
from datetime import date
from pathlib import Path

import pytest

from gym_ops.mcp_server.server import (
    find_members,
    get_class_occupancy,
    list_unpaid_members,
    reconcile_payments,
)

from .conftest import PointDb

CALLS = 20
P95_LIMIT_MS = 100.0

D = date.fromisoformat

# The seed covers 12 weeks of classes and 3 billing months up to 2026-09-25.
TOOL_CALLS: dict[str, Callable[[], object]] = {
    "get_class_occupancy": lambda: get_class_occupancy(D("2026-06-01"), D("2026-09-30")),
    "find_members": lambda: find_members(name_query="a", limit=50),
    "list_unpaid_members": lambda: list_unpaid_members("2026-09"),
    "reconcile_payments": lambda: reconcile_payments(D("2026-07-01"), D("2026-09-30")),
}


def _p95(samples_ms: list[float]) -> float:
    """Nearest-rank p95: the 19th of 20 sorted samples."""
    ordered = sorted(samples_ms)
    return ordered[math.ceil(0.95 * len(ordered)) - 1]


def test_p95_nearest_rank() -> None:
    assert _p95([float(i) for i in range(1, 21)]) == 19.0


@pytest.mark.perf
@pytest.mark.parametrize("tool", sorted(TOOL_CALLS))
def test_tool_p95_latency(paid_db_path: Path, point_db: PointDb, tool: str) -> None:
    point_db(paid_db_path)
    call = TOOL_CALLS[tool]
    call()  # warm-up: imports, page cache, settings cache
    samples: list[float] = []
    for _ in range(CALLS):
        start = time.perf_counter()
        call()
        samples.append((time.perf_counter() - start) * 1000)
    p95 = _p95(samples)
    assert p95 < P95_LIMIT_MS, f"{tool}: p95 {p95:.1f} ms over {CALLS} calls: {sorted(samples)}"
