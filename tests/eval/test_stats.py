"""Wilson intervals and nearest-rank percentiles against hand-computed values."""

import pytest

from gym_ops.eval.stats import (
    n_for_wilson_lower_bound,
    nearest_rank,
    proportion,
    wilson,
)


def test_percentiles() -> None:
    values = [10, 1, 9, 2, 8, 3, 7, 4, 6, 5]
    assert nearest_rank(values, 50) == 5  # ceil(0.5 * 10) = 5th smallest
    assert nearest_rank(values, 90) == 9
    assert nearest_rank(values, 95) == 10  # ceil(9.5) = 10th
    assert nearest_rank(values, 100) == 10
    assert nearest_rank([7], 50) == 7
    with pytest.raises(ValueError):
        nearest_rank([], 50)
    with pytest.raises(ValueError):
        nearest_rank([1], 0)


@pytest.mark.parametrize(
    ("k", "n", "low", "high"),
    [
        # 98/100 by hand: p=.98, z=1.96, denom=1.038416, center=.962249, margin=.032262
        (98, 100, 0.92999, 0.99450),
        (0, 10, 0.0, 0.27754),  # textbook: Wilson upper bound for 0/10
        (10, 10, 0.72246, 1.0),
        (3, 3, 0.43849, 1.0),
        (0, 97, 0.0, 0.03810),
        (50, 100, 0.40383, 0.59617),
    ],
)
def test_wilson_known_values(k: int, n: int, low: float, high: float) -> None:
    got_low, got_high = wilson(k, n)
    assert got_low == pytest.approx(low, abs=1e-4)
    assert got_high == pytest.approx(high, abs=1e-4)


def test_wilson_rejects_bad_input() -> None:
    with pytest.raises(ValueError):
        wilson(1, 0)
    with pytest.raises(ValueError):
        wilson(5, 4)


def test_proportion_and_format() -> None:
    p = proportion(98, 100)
    assert (p.k, p.n, p.rate) == (98, 100, 0.98)
    assert p.fmt() == "98.0% [93.0, 99.4] (98/100)"
    empty = proportion(0, 0)
    assert empty.rate is None and empty.fmt() == "n/a (0/0)"


def test_sample_size_for_lower_bound() -> None:
    n = n_for_wilson_lower_bound(0.98, 0.95)
    assert n == 203
    assert wilson(0.98 * n, n)[0] >= 0.95 > wilson(0.98 * (n - 1), n - 1)[0]
    with pytest.raises(ValueError):
        n_for_wilson_lower_bound(0.94, 0.95, n_max=500)
