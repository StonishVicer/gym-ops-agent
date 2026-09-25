"""The eval over the real frozen runs: reproducible, $0, never touches data/*.db.

Regression: the counts recorded when each run was frozen must be reproduced exactly.
"""

import hashlib
import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from gym_ops.eval.cli import main

ROOT = Path(__file__).parents[2]
LIVE_DBS = [ROOT / "data" / "gym.db", ROOT / "data" / "holdout" / "gym.db"]


def _digest(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


@dataclass(frozen=True)
class EvalOutput:
    out: Path
    results: Path
    code: int
    db_before: list[str | None]
    db_after: list[str | None]


def _run(tmp: Path) -> tuple[Path, Path, int]:
    out, results = tmp / "reports", tmp / "results.md"
    code = main(
        [
            "--runs-dir",
            str(ROOT / "eval" / "runs"),
            "--out-dir",
            str(out),
            "--results",
            str(results),
        ]
    )
    return out, results, code


@pytest.fixture(scope="module")
def evaluated(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[EvalOutput, EvalOutput]]:
    before = [_digest(p) for p in LIVE_DBS]
    first = _run(tmp_path_factory.mktemp("eval_a"))
    second = _run(tmp_path_factory.mktemp("eval_b"))
    after = [_digest(p) for p in LIVE_DBS]
    yield (EvalOutput(*first, before, after), EvalOutput(*second, before, after))


def _report(ev: EvalOutput, run: str) -> dict:  # type: ignore[type-arg]
    data: dict = json.loads((ev.out / f"{run}.json").read_text())  # type: ignore[type-arg]
    return data


def test_eval_passes_gates(evaluated: tuple[EvalOutput, EvalOutput]) -> None:
    assert evaluated[0].code == 0


def test_eval_is_byte_identical(evaluated: tuple[EvalOutput, EvalOutput]) -> None:
    a, b = evaluated
    files = sorted(p.name for p in a.out.iterdir())
    assert files == sorted(p.name for p in b.out.iterdir())
    assert len(files) == 6
    for name in files:
        assert (a.out / name).read_bytes() == (b.out / name).read_bytes(), name
    assert a.results.read_bytes() == b.results.read_bytes()


def test_committed_reports_are_current(evaluated: tuple[EvalOutput, EvalOutput]) -> None:
    """eval/reports and eval/results.md in git must be what `make eval` produces now."""
    a = evaluated[0]
    for path in a.out.iterdir():
        assert (ROOT / "eval" / "reports" / path.name).read_bytes() == path.read_bytes(), path.name
    assert (ROOT / "eval" / "results.md").read_bytes() == a.results.read_bytes()


def test_never_touches_live_databases(evaluated: tuple[EvalOutput, EvalOutput]) -> None:
    ev = evaluated[0]
    assert ev.db_before == ev.db_after  # unchanged, or still absent (CI)


@pytest.mark.parametrize(
    ("run", "failures", "all_six"),
    [
        ("v1-baseline", ["rcpt-0043", "rcpt-0084", "rcpt-0100"], 97),
        ("v2-dev", ["rcpt-0043", "rcpt-0100"], 98),
        ("v2-holdout", ["hold-0002", "hold-0069"], 98),
    ],
)
def test_frozen_counts_reproduced(
    evaluated: tuple[EvalOutput, EvalOutput], run: str, failures: list[str], all_six: int
) -> None:
    r = _report(evaluated[0], run)
    assert r["extraction"]["failures"] == failures
    assert (r["extraction"]["all_six"]["k"], r["extraction"]["all_six"]["n"]) == (all_six, 100)
    sec = r["security"]
    assert (sec["detection"]["k"], sec["detection"]["n"]) == (3, 3)
    assert (sec["false_positive"]["k"], sec["false_positive"]["n"]) == (0, 97)
    # E2E: every end-to-end error traces to a failed extraction.
    assert r["e2e"]["receipts_wrong"] == failures
    assert r["e2e"]["resolver_mismatches"] == [] and r["e2e"]["leaked_bills"] == []


def test_holdout_latency_and_provenance(evaluated: tuple[EvalOutput, EvalOutput]) -> None:
    r = _report(evaluated[0], "v2-holdout")
    c = r["cost_latency"]
    assert (c["latency_p50_ms"], c["latency_p95_ms"], c["latency_max_ms"]) == (3087, 4630, 12799)
    assert c["slowest_receipt"] == "hold-0047" and c["slowest_http_attempts"] == 1
    assert r["gated"] and r["gates_passed"]
    pv = r["provenance"]
    assert pv["model_id"] == "anthropic/claude-haiku-4.5" and pv["prompt_version"] == "v2"
    assert pv["git_sha"].startswith("3a5b144") and pv["receipts_seed"] == 8
    [bill] = r["e2e"]["bill_errors"]
    assert bill["failed_receipts"] == ["hold-0069"] and bill["observed_status"] == "unpaid"
    assert r["decimal_comma_receipts"] == ["hold-0002", "hold-0069"]


def test_only_headline_run_is_gated(evaluated: tuple[EvalOutput, EvalOutput]) -> None:
    assert [_report(evaluated[0], r)["gated"] for r in ("v1-baseline", "v2-dev")] == [False, False]
