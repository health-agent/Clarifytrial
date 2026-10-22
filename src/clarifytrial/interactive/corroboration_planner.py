"""Finite-budget acquisition for two-source agreement, with no patient truth.

The objective counts source-corroborated subset decisions, not guaranteed true
decisions. The model assumes independent source errors and feature marginals;
shared errors are evaluated externally as misspecification.
"""
from __future__ import annotations

from functools import lru_cache

from .paper_benchmark import PublicAcquisitionModel, erroneous_value_index


class CorroborationPlanner:
    def __init__(self, model: PublicAcquisitionModel, *, quality=(.70, .95), error_probability=.05,
                 distinct_source_groups=True, error_shift=2):
        if len(quality) != 2 or any(not 0 <= q <= 1 for q in quality):
            raise ValueError("two route probabilities are required")
        if not 0 <= error_probability < 1:
            raise ValueError("content-error prior must be in [0,1)")
        self.model = model
        self.quality = tuple(quality)
        self.distinct_source_groups = distinct_source_groups
        p = error_probability
        reports, agreements = [], []
        for prior in model.probabilities:
            inverse={erroneous_value_index(i,len(prior),shift=error_shift):i for i in range(len(prior))}
            marginal = tuple((1-p)*prior[y] + p*prior[inverse[y]] for y in range(len(prior)))
            agree = tuple(((1-p)**2*prior[y] + p*p*prior[inverse[y]])/marginal[y]
                          if marginal[y] else 0. for y in range(len(prior)))
            reports.append(tuple(enumerate(marginal)))
            agreements.append(agree)
        self.reports = tuple(reports)
        self.agreements = tuple(agreements)

    @lru_cache(maxsize=80000)
    def solve(self, observed: tuple[int, ...], pending: tuple[int, ...], attempted: int, visits: int, depth: int):
        if not 0 <= depth <= 3:
            raise ValueError("research horizons are zero to three")
        base = sum(value != -1 for value in self.model.statuses(observed))
        best, action = (float(base), 0., 0.), None
        if not depth or not self.distinct_source_groups:
            return best, action
        active = set().union(*self.model.information_needs(observed))
        for fact in sorted(active):
            used = (attempted >> (2*fact)) & 3
            if used == 3 or (used and pending[fact] < 0):
                continue  # Only two sources exist; an unusable route cannot form a quorum.
            if pending[fact] < 0 and (depth < 2 or visits < 1):
                continue
            for route in range(2):
                bit = 1 << (2*fact+route)
                if attempted & bit or route > visits:
                    continue
                attempted_next = attempted | bit
                q = self.quality[route]
                value = [0., -float(route), -1.]

                def add(probability, obs, pend):
                    if probability:
                        reward, _ = self.solve(obs, pend, attempted_next, visits-route, depth-1)
                        for i in range(3):
                            value[i] += probability*reward[i]

                add(1-q, observed, pending)
                if pending[fact] >= 0:
                    reported = pending[fact]
                    cleared = pending[:fact]+(-1,)+pending[fact+1:]
                    agree = self.agreements[fact][reported]
                    add(q*agree, observed[:fact]+(reported,)+observed[fact+1:], cleared)
                    # Every disagreement exhausts both sources and has the same
                    # unusable future state. No source value is treated as truth.
                    add(q*(1-agree), observed, cleared)
                else:
                    for reported, probability in self.reports[fact]:
                        add(q*probability, observed, pending[:fact]+(reported,)+pending[fact+1:])
                candidate = tuple(round(v, 12) for v in value)
                if candidate > best:
                    best, action = candidate, (fact, route)
        return best, action
