"""NFR gates (SPEC FR-17). Thresholds come from SPEC §4 and are never tuned here.

A gate compares the *measured* value with its threshold, as the SPEC defines it; the
Wilson interval is reported next to every accuracy gate so a pass on n = 100 is not
mistaken for a statistically established rate.
"""

from collections.abc import Mapping
from typing import Final

from pydantic import BaseModel, ConfigDict

from gym_ops.eval.metrics import FIELDS, CostLatency, ExtractionMetrics
from gym_ops.eval.stats import Proportion

MEAN_COST_MAX_USD: Final = 0.005  # NFR-1: mean < $0.005
MAX_COST_MAX_USD: Final = 0.01  # NFR-1: max < $0.01
P50_MAX_MS: Final = 4000  # NFR-2: p50 < 4.0 s (p95 reported, not gated: SPEC Q-6)
FIELD_ACCURACY_MIN: Final = 0.95  # NFR-3: >= 95% for each of the six fields


class Gate(BaseModel):
    model_config = ConfigDict(frozen=True)

    nfr: str
    metric: str
    threshold: str
    measured: str
    passed: bool
    interval: str | None = None  # Wilson 95% for accuracy gates


def check_gates(
    *,
    mean_cost_usd: float,
    max_cost_usd: float,
    p50_ms: int,
    field_accuracy: Mapping[str, float],
    field_detail: Mapping[str, Proportion] | None = None,
) -> list[Gate]:
    """Evaluate every NFR-1..3 gate from plain numbers (FR-17 AC feeds synthetic ones)."""
    gates = [
        Gate(
            nfr="NFR-1",
            metric="mean cost per extraction",
            threshold=f"< ${MEAN_COST_MAX_USD}",
            measured=f"${mean_cost_usd:.6f}",
            passed=mean_cost_usd < MEAN_COST_MAX_USD,
        ),
        Gate(
            nfr="NFR-1",
            metric="max cost per extraction",
            threshold=f"< ${MAX_COST_MAX_USD}",
            measured=f"${max_cost_usd:.6f}",
            passed=max_cost_usd < MAX_COST_MAX_USD,
        ),
        Gate(
            nfr="NFR-2",
            metric="latency p50",
            threshold=f"< {P50_MAX_MS} ms",
            measured=f"{p50_ms} ms",
            passed=p50_ms < P50_MAX_MS,
        ),
    ]
    for field in FIELDS:
        acc = field_accuracy[field]
        detail = field_detail.get(field) if field_detail else None
        gates.append(
            Gate(
                nfr="NFR-3",
                metric=f"{field} accuracy",
                threshold=f">= {FIELD_ACCURACY_MIN:.0%}",
                measured=f"{acc:.1%}" + (f" ({detail.k}/{detail.n})" if detail else ""),
                passed=acc >= FIELD_ACCURACY_MIN,
                interval=(
                    f"[{detail.low * 100:.1f}%, {detail.high * 100:.1f}%]"
                    if detail and detail.low is not None and detail.high is not None
                    else None
                ),
            )
        )
    return gates


def gates_for(cost: CostLatency, extraction: ExtractionMetrics) -> list[Gate]:
    return check_gates(
        mean_cost_usd=cost.mean_cost_usd,
        max_cost_usd=cost.max_cost_usd,
        p50_ms=cost.latency_p50_ms,
        field_accuracy={f: p.rate or 0.0 for f, p in extraction.per_field.items()},
        field_detail=extraction.per_field,
    )


def exit_code(gates: list[Gate]) -> int:
    return 0 if all(g.passed for g in gates) else 1


def breached(gates: list[Gate]) -> list[str]:
    return [
        f"{g.nfr} {g.metric}: {g.measured} (threshold {g.threshold})" for g in gates if not g.passed
    ]
