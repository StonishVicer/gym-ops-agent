"""Small-sample statistics for the eval: Wilson intervals and nearest-rank percentiles.

With n = 100 per set, a normal-approximation interval is wrong exactly where the
results live (rates near 1.0), so every proportion carries a Wilson score interval.
"""

import math
from collections.abc import Sequence
from typing import Final

from pydantic import BaseModel, ConfigDict

Z95: Final = 1.96  # two-sided 95%


class Proportion(BaseModel):
    """k successes out of n, with a 95% Wilson score interval."""

    model_config = ConfigDict(frozen=True)

    k: int
    n: int
    rate: float | None  # None when n == 0
    low: float | None
    high: float | None

    def fmt(self) -> str:
        """`98.0% [93.0, 99.4] (98/100)`, or `n/a (0/0)`."""
        if self.rate is None or self.low is None or self.high is None:
            return f"n/a ({self.k}/{self.n})"
        return (
            f"{self.rate * 100:.1f}% [{self.low * 100:.1f}, {self.high * 100:.1f}] "
            f"({self.k}/{self.n})"
        )


def wilson(k: float, n: int, z: float = Z95) -> tuple[float, float]:
    """95% Wilson score interval for k successes in n trials (k may be fractional)."""
    if n <= 0:
        raise ValueError("n must be positive")
    if not 0 <= k <= n:
        raise ValueError(f"k={k} outside [0, {n}]")
    p = k / n
    z2 = z * z
    denom = 1 + z2 / n
    center = (p + z2 / (2 * n)) / denom
    margin = z * math.sqrt(p * (1 - p) / n + z2 / (4 * n * n)) / denom
    return max(0.0, center - margin), min(1.0, center + margin)


def proportion(k: int, n: int) -> Proportion:
    if n == 0:
        return Proportion(k=0, n=0, rate=None, low=None, high=None)
    low, high = wilson(k, n)
    return Proportion(k=k, n=n, rate=round(k / n, 6), low=round(low, 6), high=round(high, 6))


def nearest_rank(values: Sequence[int], pct: float) -> int:
    """Nearest-rank percentile: the smallest value with at least pct% of values <= it."""
    if not values:
        raise ValueError("no values")
    if not 0 < pct <= 100:
        raise ValueError("pct must be in (0, 100]")
    ordered = sorted(values)
    return ordered[math.ceil(pct / 100 * len(ordered)) - 1]


def n_for_wilson_lower_bound(true_rate: float, target: float, n_max: int = 100_000) -> int:
    """Smallest n whose Wilson lower bound clears `target` if the observed rate is `true_rate`.

    Answers "how many receipts would it take to *establish* a >= target rate", assuming
    the measured rate holds exactly (k = true_rate * n).
    """
    for n in range(1, n_max + 1):
        if wilson(true_rate * n, n)[0] >= target:
            return n
    raise ValueError(f"not reachable within n <= {n_max}")
