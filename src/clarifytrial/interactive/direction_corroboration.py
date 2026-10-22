"""Research-only criterion-direction agreement and independent-channel planning.

Completed pairs encode both reports, not a chosen numeric truth. The acquisition
objective is resolved subset decisions; shared-error settings deliberately violate
the planner's independent-error assumption. No clinical error rates are inferred.
"""
from __future__ import annotations

from functools import lru_cache

from .paper_benchmark import PublicAcquisitionModel, erroneous_value_index


def report_channel(size: int, probability: float, *, kind="uniform_wrong", shift=2):
    """P(report | latent value), with errors always changing the latent value."""
    if size < 2 or not 0 <= probability < 1 or kind not in {"uniform_wrong", "cyclic"}:
        raise ValueError("invalid finite report channel")
    return tuple(tuple((1-probability if y == t else
        probability/(size-1) if kind == "uniform_wrong" else
        probability if y == erroneous_value_index(t, size, shift=shift) else 0.)
        for y in range(size)) for t in range(size))


class SourcePairModel:
    """Lift scalar rules to two reports without merging their numeric values."""

    def __init__(self, base: PublicAcquisitionModel, *, mode="criterion_direction"):
        if mode not in {"exact_value", "criterion_direction"}:
            raise ValueError("unsupported agreement mode")
        self.base, self.mode = base, mode
        self.codes, self.domains, self.probabilities = base.codes, base.domains, base.probabilities
        self.rules, self.logic = base.rules, base.logic

    def encode(self, fact, first, second):
        size=len(self.domains[fact])
        if not 0 <= first < size or not 0 <= second < size:
            raise ValueError("report index outside the declared domain")
        if first == second:
            return first
        low, high=sorted((first, second))
        return size + low*size + high

    def decode(self, fact, value):
        size=len(self.domains[fact])
        if value < 0:
            return ()
        return (value, value) if value < size else divmod(value-size, size)

    def leaf_statuses(self, trial, observed):
        values=[]
        for rule in trial:
            pair=self.decode(rule.fact, observed[rule.fact])
            if not pair or (self.mode == "exact_value" and pair[0] != pair[1]):
                values.append(-1)
                continue
            directions={int(rule.supports(self.domains[rule.fact][index])) for index in pair}
            values.append(next(iter(directions)) if len(directions) == 1 else -1)
        return tuple(values)

    def statuses(self, observed):
        result=[]
        for i, trial in enumerate(self.rules):
            leaves=self.leaf_statuses(trial, observed)
            result.append(self.logic[i].evaluate(leaves) if self.logic else
                          0 if 0 in leaves else -1 if -1 in leaves else 1)
        return tuple(result)

    def information_needs(self, observed):
        result=[]
        for i, trial in enumerate(self.rules):
            leaves=self.leaf_statuses(trial, observed)
            if self.logic:
                result.append({trial[j].fact for j in self.logic[i].unresolved_leaves(leaves)})
            else:
                result.append(set() if 0 in leaves else {r.fact for r, v in zip(trial, leaves) if v < 0})
        return tuple(result)


class SourcePairPlanner:
    """Budget-three dynamic program integrating latent values and both reports.

    Each first report updates the posterior over latent values under the declared
    independent source channel. Completed disagreeing pairs can resolve some
    predicates and leave other predicates on that same fact unknown.
    """

    def __init__(self, model: SourcePairModel, *, quality=(.70, .95),
                 error_probability=.05, channel="uniform_wrong", error_shift=2):
        if len(quality) != 2 or any(not 0 <= q <= 1 for q in quality):
            raise ValueError("two bounded route probabilities required")
        self.model, self.quality = model, tuple(quality)
        self.reports, self.second = [], []
        for domain, prior in zip(model.domains, model.probabilities):
            likelihood=report_channel(len(domain), error_probability, kind=channel, shift=error_shift)
            marginal=tuple(sum(prior[t]*likelihood[t][y] for t in range(len(domain))) for y in range(len(domain)))
            conditional=tuple(tuple(sum(prior[t]*likelihood[t][y]*likelihood[t][z] for t in range(len(domain)))/marginal[y]
                if marginal[y] else 0. for z in range(len(domain))) for y in range(len(domain)))
            self.reports.append(tuple(enumerate(marginal)))
            self.second.append(conditional)

    @lru_cache(maxsize=80000)
    def solve(self, observed, pending, attempted, visits, depth):
        if not 0 <= depth <= 3:
            raise ValueError("research horizons are zero to three")
        best, action=(float(sum(v != -1 for v in self.model.statuses(observed))), 0., 0.), None
        if not depth:
            return best, action
        active=set().union(*self.model.information_needs(observed))
        for fact in sorted(active):
            used=(attempted >> (2*fact)) & 3
            if used == 3 or (used and pending[fact] < 0):
                continue
            if pending[fact] < 0 and (depth < 2 or visits < 1):
                continue
            for route in range(2):
                bit=1 << (2*fact+route)
                if attempted & bit or route > visits:
                    continue
                q=self.quality[route]
                value=[0., -float(route), -1.]

                def add(probability, obs, pend):
                    if probability:
                        reward, _=self.solve(obs, pend, attempted | bit, visits-route, depth-1)
                        for i in range(3):
                            value[i] += probability*reward[i]

                add(1-q, observed, pending)
                if pending[fact] >= 0:
                    first=pending[fact]
                    cleared=pending[:fact]+(-1,)+pending[fact+1:]
                    for reported, probability in enumerate(self.second[fact][first]):
                        pair=self.model.encode(fact, first, reported)
                        add(q*probability, observed[:fact]+(pair,)+observed[fact+1:], cleared)
                else:
                    for reported, probability in self.reports[fact]:
                        add(q*probability, observed, pending[:fact]+(reported,)+pending[fact+1:])
                candidate=tuple(round(v, 12) for v in value)
                if candidate > best:
                    best, action=candidate, (fact, route)
        return best, action
