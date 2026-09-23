"""Uncertainty for small evaluation sets: bootstrap intervals and paired tests.

With 19 dev items one item is worth ~5 points of recall. Saying so in prose is
a caveat; computing it is a measurement. Two tools:

* **`bootstrap_ci`** -- a percentile bootstrap of the mean of per-item scores.
  Resample the items with replacement, recompute the mean, take the 2.5th and
  97.5th percentiles. It answers "how much would this number move on a
  different draw of questions like these?"

* **`paired_comparison`** -- two runs over the *same* items. Pairing matters: a
  config that wins on the hard items and a config that wins on the easy ones
  can have identical means and very different per-item behaviour, and an
  unpaired interval on each mean throws that away. The interval is a
  bootstrap over per-item differences, and the p-value is an exact sign-flip
  (permutation) test on them: under "no difference", each item's delta is as
  likely to be negative as positive.

Everything is seeded, so a record's intervals are reproducible from the record.
No numpy: this runs in CI and in the API on lists of a few hundred floats.
"""

from __future__ import annotations

import itertools
import math
import random
from dataclasses import asdict, dataclass

DEFAULT_RESAMPLES = 10_000
DEFAULT_SEED = 0
# Exact enumeration of sign flips up to this many non-zero differences
# (2^16 = 65,536 permutations); Monte Carlo beyond it.
EXACT_PERMUTATION_LIMIT = 16


@dataclass(frozen=True)
class Interval:
    mean: float
    low: float
    high: float
    n: int

    def to_dict(self) -> dict[str, float | int]:
        return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in asdict(self).items()}


@dataclass(frozen=True)
class PairedComparison:
    """B minus A, over the items both runs scored."""

    metric: str
    n: int
    mean_a: float
    mean_b: float
    delta: float
    low: float
    high: float
    p_value: float
    wins: int  # items where B beat A
    losses: int  # items where A beat B

    @property
    def distinguishable(self) -> bool:
        """Whether the 95% interval on the difference excludes zero."""
        return self.low > 0 or self.high < 0

    def to_dict(self) -> dict[str, object]:
        out: dict[str, object] = {
            k: (round(v, 4) if isinstance(v, float) else v) for k, v in asdict(self).items()
        }
        out["distinguishable"] = self.distinguishable
        return out


def _percentile(sorted_values: list[float], q: float) -> float:
    """Linear-interpolated percentile of an already-sorted list."""
    if not sorted_values:
        return 0.0
    position = q * (len(sorted_values) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return sorted_values[lower]
    weight = position - lower
    return sorted_values[lower] * (1 - weight) + sorted_values[upper] * weight


def bootstrap_ci(
    values: list[float],
    *,
    confidence: float = 0.95,
    resamples: int = DEFAULT_RESAMPLES,
    seed: int = DEFAULT_SEED,
) -> Interval:
    """Percentile bootstrap interval for the mean of `values`."""
    n = len(values)
    if n == 0:
        return Interval(0.0, 0.0, 0.0, 0)
    mean = sum(values) / n
    if n == 1 or len(set(values)) == 1:
        # No spread to resample. Reported as a point, which is honest: the
        # data carries no information about variability.
        return Interval(mean, mean, mean, n)

    rng = random.Random(seed)
    means = sorted(sum(rng.choices(values, k=n)) / n for _ in range(resamples))
    tail = (1 - confidence) / 2
    return Interval(mean, _percentile(means, tail), _percentile(means, 1 - tail), n)


def sign_flip_p_value(
    differences: list[float], *, resamples: int = DEFAULT_RESAMPLES, seed: int = DEFAULT_SEED
) -> float:
    """Two-sided paired permutation test of `mean(differences) == 0`.

    Zero differences carry no sign and are dropped. Exact when few enough
    remain to enumerate every sign assignment; Monte Carlo otherwise.
    """
    nonzero = [d for d in differences if d != 0]
    if not nonzero:
        return 1.0
    observed = abs(sum(nonzero))
    # Floating-point slack, so an assignment equal to the observed sum counts.
    threshold = observed - 1e-12

    if len(nonzero) <= EXACT_PERMUTATION_LIMIT:
        hits = total = 0
        for signs in itertools.product((1, -1), repeat=len(nonzero)):
            total += 1
            if abs(sum(s * d for s, d in zip(signs, nonzero, strict=True))) >= threshold:
                hits += 1
        return hits / total

    rng = random.Random(seed)
    hits = sum(
        1
        for _ in range(resamples)
        if abs(sum(d if rng.random() < 0.5 else -d for d in nonzero)) >= threshold
    )
    # +1 smoothing: a Monte Carlo p-value is never exactly zero.
    return (hits + 1) / (resamples + 1)


def paired_comparison(
    metric: str,
    a: list[float],
    b: list[float],
    *,
    confidence: float = 0.95,
    resamples: int = DEFAULT_RESAMPLES,
    seed: int = DEFAULT_SEED,
) -> PairedComparison:
    """Compare two runs item by item. `a[i]` and `b[i]` must be the same item."""
    if len(a) != len(b):
        raise ValueError(f"paired lists differ in length: {len(a)} vs {len(b)}")
    n = len(a)
    if n == 0:
        return PairedComparison(metric, 0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0, 0)

    differences = [y - x for x, y in zip(a, b, strict=True)]
    interval = bootstrap_ci(differences, confidence=confidence, resamples=resamples, seed=seed)
    return PairedComparison(
        metric=metric,
        n=n,
        mean_a=sum(a) / n,
        mean_b=sum(b) / n,
        delta=interval.mean,
        low=interval.low,
        high=interval.high,
        p_value=sign_flip_p_value(differences, resamples=resamples, seed=seed),
        wins=sum(1 for d in differences if d > 0),
        losses=sum(1 for d in differences if d < 0),
    )
