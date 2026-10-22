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


def crossed_stratified_bootstrap_mean(
    matrices_by_stratum: Mapping[str, Sequence[Sequence[float]]], *,
    seed: int = 20_261_023, resamples: int = 5_000,
) -> dict[str, Any]:
    """Separately resample patient rows and protocol columns within each stratum.

    A stratified pigeonhole-style sensitivity for the listed crossed arrays,
    not proof of population coverage for a deliberately selected trial corpus.
    """
    from collections import Counter
    matrices={str(k):tuple(tuple(float(v) for v in row) for row in rows)
              for k,rows in sorted(matrices_by_stratum.items())}
    if not matrices or resamples<1:
        raise ValueError("nonempty matrices and positive resamples required")
    for matrix in matrices.values():
        if not matrix or not matrix[0] or any(len(row)!=len(matrix[0]) for row in matrix):
            raise ValueError("each crossed stratum must be a nonempty rectangular matrix")
    total=sum(len(m)*len(m[0]) for m in matrices.values())
    estimate=sum(sum(sum(row) for row in m) for m in matrices.values())/total
    rng=random.Random(seed)
    samples=[]
    for _ in range(resamples):
        value=0.
        for matrix in matrices.values():
            patients=len(matrix)
            protocols=len(matrix[0])
            row_counts=Counter(rng.choices(range(patients),k=patients))
            col_counts=Counter(rng.choices(range(protocols),k=protocols))
            value+=sum(row_count*col_count*matrix[i][j]
                       for i,row_count in row_counts.items() for j,col_count in col_counts.items())
        samples.append(value/total)
    return {"mean_difference":estimate,"bootstrap_seed":seed,"bootstrap_resamples":resamples,
            "resampling_units":"patient rows and trial columns, independently within query strata; masks averaged first",
            "patient_count":sum(len(m) for m in matrices.values()),
            "trial_count":sum(len(m[0]) for m in matrices.values()),
            "percentile_95_range":{"lower":_percentile(samples,.025),"upper":_percentile(samples,.975)},
            "scope":"Crossed resampling sensitivity on the listed arrays; selected protocols are not a random clinical-trial population sample."}


__all__ = ["exact_sign_test", "stratified_bootstrap_mean", "crossed_stratified_bootstrap_mean"]
