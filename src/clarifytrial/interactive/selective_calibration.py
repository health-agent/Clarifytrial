"""Learn-then-test calibration of pre-fixed episode-level selectors.

LTT is Angelopoulos et al.'s method, not a new statistical guarantee. Use a
Bonferroni family over *all* pre-fixed policies/cutoffs tested on calibration.
The binary loss is any wrong newly released subset decision in an episode,
conditional on releasing at least one. Patients, not trials/masks, are trials
of the binomial experiment. No monotonicity of adaptive policies is assumed.
"""
from __future__ import annotations

from dataclasses import dataclass
import math


def binomial_lower_tail(errors: int, selected: int, risk: float) -> float:
    if not 0 <= errors <= selected or not 0 < risk < 1:
        raise ValueError("valid binomial count and risk required")
    if not selected:
        return 1.
    logs = [math.lgamma(selected+1)-math.lgamma(j+1)-math.lgamma(selected-j+1)
            + j*math.log(risk)+(selected-j)*math.log1p(-risk)
            for j in range(errors+1)]
    largest = max(logs)
    return min(1., math.exp(largest)*sum(math.exp(x-largest) for x in logs))


def clopper_pearson_upper(errors: int, selected: int, delta=.05) -> float:
    if not 0 < delta < 1 or not 0 <= errors <= selected:
        raise ValueError("valid counts and confidence failure probability required")
    if not selected or errors == selected:
        return 1.
    if not errors:
        return -math.expm1(math.log(delta)/selected)
    low, high = errors/selected, 1.
    for _ in range(55):
        mid = (low+high)/2
        if binomial_lower_tail(errors, selected, mid) > delta:
            low = mid
        else:
            high = mid
    return high


@dataclass(frozen=True)
class CalibrationCandidate:
    name: str
    selected: int
    any_wrong: int
    correct_decisions: int
    visits: int


def calibrate(candidates, *, risk=.05, delta=.05, family_size=None):
    """Certify an arbitrary fixed finite family; selecting later remains valid.

    Empty/noncertified families return no allowed deployment. A caller choosing
    among certified policies must not use test outcomes. The guarantee requires
    IID calibration/deployment episodes from the same mixture, labels available
    independently of acquisition, and a policy fixed before calibration.
    """
    candidates = tuple(candidates)
    family_size = family_size or len(candidates)
    if family_size < len(candidates) or family_size < 1 or not 0 < risk < 1 or not 0 < delta < 1:
        raise ValueError("valid pre-fixed family size, risk and delta required")
    result = []
    for c in candidates:
        p = binomial_lower_tail(c.any_wrong, c.selected, risk)
        upper = clopper_pearson_upper(c.any_wrong, c.selected, delta/family_size)
        result.append({"name": c.name, "selected": c.selected, "any_wrong": c.any_wrong,
            "correct_decisions": c.correct_decisions, "visits": c.visits,
            "p_value": p, "risk_upper_simultaneous": upper,
            "certified": bool(c.selected and p <= delta/family_size)})
    valid = [c for c in result if c["certified"]]
    chosen = max(valid, key=lambda c: (c["correct_decisions"], -c["visits"], c["name"])) if valid else None
    return {"scope": "conditional episode-any-error risk; IID calibration/deployment only",
        "risk": risk, "delta": delta, "family_size": family_size,
        "candidates": result, "chosen": chosen}
