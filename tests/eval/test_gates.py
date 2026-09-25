"""FR-17: gates on the measured value; breaches are named; thresholds are SPEC §4."""

from gym_ops.eval.gates import (
    FIELD_ACCURACY_MIN,
    MEAN_COST_MAX_USD,
    P50_MAX_MS,
    Gate,
    breached,
    check_gates,
    exit_code,
)
from gym_ops.eval.metrics import FIELDS


def _gates(
    *,
    mean_cost_usd: float = 0.0035,
    max_cost_usd: float = 0.0036,
    p50_ms: int = 3087,
    fields: dict[str, float] | None = None,
) -> list[Gate]:
    accuracy = {f: 0.98 for f in FIELDS} | (fields or {})
    return check_gates(
        mean_cost_usd=mean_cost_usd,
        max_cost_usd=max_cost_usd,
        p50_ms=p50_ms,
        field_accuracy=accuracy,
    )


def test_thresholds_are_the_spec_values() -> None:
    assert (MEAN_COST_MAX_USD, P50_MAX_MS, FIELD_ACCURACY_MIN) == (0.005, 4000, 0.95)


def test_passing_set_returns_zero() -> None:
    gates = _gates()
    assert exit_code(gates) == 0 and breached(gates) == []
    assert len(gates) == 3 + len(FIELDS)


def test_latency_gate() -> None:
    gates = _gates(p50_ms=4100)
    assert exit_code(gates) == 1
    assert breached(gates) == ["NFR-2 latency p50: 4100 ms (threshold < 4000 ms)"]


def test_cost_gate() -> None:
    gates = _gates(mean_cost_usd=0.0051)
    assert exit_code(gates) == 1
    [line] = breached(gates)
    assert line.startswith("NFR-1 mean cost per extraction: $0.005100")


def test_accuracy_gate() -> None:
    gates = _gates(fields={"reference": 0.94})
    assert exit_code(gates) == 1
    [line] = breached(gates)
    assert line.startswith("NFR-3 reference accuracy: 94.0%")


def test_boundaries() -> None:
    assert exit_code(_gates(fields={"payer_name": 0.95})) == 0  # >= 95% is inclusive
    assert exit_code(_gates(p50_ms=4000)) == 1  # < 4.0 s is strict
    assert exit_code(_gates(mean_cost_usd=0.005)) == 1  # < $0.005 is strict
    assert exit_code(_gates(max_cost_usd=0.01)) == 1
