"""Small dependency-free statistics for paired policy experiments."""

from __future__ import annotations

import random
from collections.abc import Mapping, Sequence
from math import comb
from statistics import mean
from typing import Any


def exact_sign_test(differences: Sequence[float], *, tolerance: float = 1e-12) -> float:
    """Return the two-sided exact sign-test p-value, excluding ties."""

    wins = sum(item > tolerance for item in differences)
    losses = sum(item < -tolerance for item in differences)
    non_ties = wins + losses
    if non_ties == 0:
        return 1.0
    smaller = min(wins, losses)
    return min(
        1.0,
        2
        * sum(comb(non_ties, index) for index in range(smaller + 1))
        / (2**non_ties),
    )


def _percentile(values: Sequence[float], probability: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("percentile requires at least one value")
    position = (len(ordered) - 1) * probability
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def stratified_bootstrap_mean(
    differences_by_stratum: Mapping[str, Sequence[float]],
    *,
    cluster_unit: str = "paired_case",
    seed: int = 20_260_830,
    resamples: int = 5_000,
) -> dict[str, Any]:
    """Bootstrap paired effects while preserving every declared stratum size."""

    groups = {
        str(key): tuple(sorted(float(item) for item in values))
        for key, values in sorted(differences_by_stratum.items())
        if values
    }
    values = [item for group in groups.values() for item in group]
    if not values:
        raise ValueError("bootstrap requires at least one paired difference")
    if resamples < 1:
        raise ValueError("bootstrap resamples must be positive")
    generator = random.Random(seed)
    samples = []
    for _ in range(resamples):
        drawn = [
            generator.choice(group)
            for group in groups.values()
            for _ in group
        ]
        samples.append(mean(drawn))
    return {
        "cluster_unit": cluster_unit,
        "strata": sorted(groups),
        "pair_count": len(values),
        "bootstrap_seed": seed,
        "bootstrap_resamples": resamples,
        "mean_difference": mean(values),
        "bootstrap_95_ci": {
            "lower": _percentile(samples, 0.025),
            "upper": _percentile(samples, 0.975),
        },
        "wins": sum(item > 1e-12 for item in values),
        "ties": sum(abs(item) <= 1e-12 for item in values),
        "losses": sum(item < -1e-12 for item in values),
        "two_sided_exact_sign_test_p": exact_sign_test(values),
    }


__all__ = ["exact_sign_test", "stratified_bootstrap_mean"]
