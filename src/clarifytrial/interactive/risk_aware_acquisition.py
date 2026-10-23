"""Evidence-grounded selective decisions and targeted verification.

Research API, not a replacement for deterministic clinical confirmation.
Uses Bayesian experimental design / decision-region determination and a reject
option, not a new general Bayesian algorithm. A finite source-coupling model set
is integrated for acquisition and enveloped for decision scores. Scores are not
frequentist guarantees; independent episode-level calibration is separate.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import math

from .paper_benchmark import LogicalRule, PublicAcquisitionModel


UNTRIED = -2
FAILED = -1


@dataclass(frozen=True)
class SelectiveSubsetDecision:
    trial: int
    label: int  # -1 abstain, 0 subset violation, 1 subset satisfaction
    witness_label: int
    model_error_upper: float
    probability_by_model: tuple[float, ...]
    evidence_facts: tuple[int, ...]
    reason: str


class RiskAwareAcquisition:
    """Bounded Bayesian lookahead with observed logical witnesses.

    Each state has two report slots per fact: -2 untried, -1 unavailable, or a
    public domain index. Trusted initial facts are fixed and never reacquired.
    A non-abstaining output requires an actual source-based logical witness;
    a high prior or imputation alone cannot authorize an output.
    """

    def __init__(self, model: PublicAcquisitionModel, *, error_probability=.05,
                 coupling=(0., .5, 1.), quality=(.70, .95), risk_cutoff=.03,
                 visit_penalty=0., same_source_family=False):
        if not 0 < error_probability < .5:
            raise ValueError("content error probability must be between zero and one half")
        if not coupling or any(not 0 <= x <= 1 for x in coupling):
            raise ValueError("finite coupling model set required")
        if len(quality) != 2 or any(not 0 < x <= 1 for x in quality):
            raise ValueError("two bounded route success probabilities required")
        if not 0 < risk_cutoff < .5 or visit_penalty < 0:
            raise ValueError("valid rejection cutoff and nonnegative visit penalty required")
        self.model = model
        self.error_probability = error_probability
        self.coupling = (1.,) if same_source_family else tuple(coupling)
        self.quality = tuple(quality)
        self.risk_cutoff = risk_cutoff
        self.visit_penalty = visit_penalty
        self.logic = model.logic or tuple(
            LogicalRule("all", children=tuple(LogicalRule("criterion", j)
                for j in range(len(rules)))) for rules in model.rules)
        # Compile each root into a shared decision DAG. A fact is integrated
        # once even if it occurs in multiple bounds, alternatives or exceptions.
        self.nodes: list[tuple[int, tuple[int, ...]] | None] = [None, None]
        intern = {}
        roots = []
        for index, rules in enumerate(model.rules):
            @lru_cache(maxsize=None)
            def build(leaves):
                status = self.logic[index].evaluate(leaves)
                if status != -1:
                    return status
                remaining = self.logic[index].unresolved_leaves(leaves)
                fact = min(rules[j].fact for j in remaining)
                children = []
                for value in model.domains[fact]:
                    updated = tuple(int(rule.supports(value)) if rule.fact == fact
                                    else leaves[j] for j, rule in enumerate(rules))
                    children.append(build(updated))
                if len(set(children)) == 1:
                    return children[0]
                key = fact, tuple(children)
                if key not in intern:
                    intern[key] = len(self.nodes)
                    self.nodes.append(key)
                return intern[key]
            roots.append(build((-1,) * len(rules)))
        self.roots = tuple(roots)

    def empty_state(self):
        return (UNTRIED,) * (2 * len(self.model.codes))

    def _likelihood(self, truth, report, size):
        return 1-self.error_probability if truth == report else self.error_probability/(size-1)

    @lru_cache(maxsize=40000)
    def _fact_posterior(self, fact, first, second, rho):
        reports = [x for x in (first, second) if x >= 0]
        prior = self.model.probabilities[fact]
        size = len(prior)
        weights = []
        for truth, probability in enumerate(prior):
            if not reports:
                likelihood = 1.
            elif len(reports) == 1:
                likelihood = self._likelihood(truth, reports[0], size)
            else:
                a, b = reports
                likelihood = ((1-rho)*self._likelihood(truth, a, size)*self._likelihood(truth, b, size)
                              + rho*self._likelihood(truth, a, size)*int(a == b))
            weights.append(probability * likelihood)
        mass = sum(weights)
        return (tuple(x/mass for x in weights) if mass else ()), mass

    @lru_cache(maxsize=30000)
    def belief(self, fixed, state):
        probabilities, log_masses, valid_models = [], [], []
        for rho in self.coupling:
            marginals = []
            log_mass = 0.
            for fact, known in enumerate(fixed):
                if known >= 0:
                    marginals.append(tuple(float(j == known) for j in range(len(self.model.domains[fact]))))
                    continue
                posterior, mass = self._fact_posterior(fact, state[2*fact], state[2*fact+1], rho)
                if not mass:
                    break
                marginals.append(posterior)
                log_mass += math.log(mass)
            if len(marginals) != len(fixed):
                continue
            values = [0., 1.]
            for node in self.nodes[2:]:
                fact, children = node
                values.append(sum(p*values[child] for p, child in zip(marginals[fact], children)))
            probabilities.append(tuple(values[root] for root in self.roots))
            log_masses.append(log_mass)
            valid_models.append((rho, tuple(marginals)))
        if not valid_models:
            raise ValueError("observations have zero probability under every source model")
        maximum = max(log_masses)
        masses = [math.exp(x-maximum) for x in log_masses]
        total = sum(masses)
        return tuple(probabilities), tuple(x/total for x in masses), tuple(valid_models)

    @lru_cache(maxsize=30000)
    def decisions(self, fixed, state):
        probabilities, _, _ = self.belief(fixed, state)
        decisions = []
        for index, rules in enumerate(self.model.rules):
            leaves, evidence_facts = [], set()
            for rule in rules:
                reports = ([fixed[rule.fact]] if fixed[rule.fact] >= 0 else
                           [x for x in state[2*rule.fact:2*rule.fact+2] if x >= 0])
                if reports:
                    evidence_facts.add(rule.fact)
                outcomes = {int(rule.supports(self.model.domains[rule.fact][x])) for x in reports}
                leaves.append(next(iter(outcomes)) if len(outcomes) == 1 else -1)
            witness = self.logic[index].evaluate(tuple(leaves))
            ps = tuple(row[index] for row in probabilities)
            risk = (max(1-p for p in ps) if witness == 1 else max(ps) if witness == 0 else 1.)
            label = witness if witness != -1 and risk <= self.risk_cutoff + 1e-12 else -1
            reason = ("observed witness and bounded model score" if label >= 0 else
                      "source meaning/value witness missing or conflicting" if witness < 0 else
                      "observed witness needs targeted verification")
            decisions.append(SelectiveSubsetDecision(index, label, witness, risk, ps,
                tuple(sorted(evidence_facts)), reason))
        return tuple(decisions)

    def reward(self, fixed, state):
        return float(sum(x.label >= 0 for x in self.decisions(fixed, state)))

    def actions(self, fixed, state, visits):
        # Rechecking a fact can matter after its logical witness exists. Do not
        # prune merely because the raw one-report formula is already decisive.
        relevant = {r.fact for rules in self.model.rules for r in rules}
        return tuple((f, r) for f in sorted(relevant) if fixed[f] < 0
                     for r in range(2) if state[2*f+r] == UNTRIED and r <= visits)

    @lru_cache(maxsize=60000)
    def branches(self, fixed, state, fact, route):
        _, weights, models = self.belief(fixed, state)
        first, second = state[2*fact:2*fact+2]
        distribution = [0.] * len(self.model.domains[fact])
        for model_weight, (rho, _) in zip(weights, models):
            _, denominator = self._fact_posterior(fact, first, second, rho)
            for report in range(len(distribution)):
                candidate = [first, second]
                candidate[route] = report
                _, numerator = self._fact_posterior(fact, *candidate, rho)
                distribution[report] += model_weight*numerator/denominator
        q = self.quality[route]
        outcomes = [(FAILED, 1-q)] + [(y, q*p) for y, p in enumerate(distribution)]
        result = []
        for report, probability in outcomes:
            if probability <= 1e-15:
                continue
            updated = state[:2*fact+route] + (report,) + state[2*fact+route+1:]
            result.append((probability, updated))
        return tuple(result)

    def uncertainty(self, fixed, state, kind):
        ps, weights, _ = self.belief(fixed, state)
        result = 0.
        for j in range(len(self.model.rules)):
            p = sum(w*row[j] for w, row in zip(weights, ps))
            if kind == "bayes_error":
                result += min(p, 1-p)
            elif kind == "entropy":
                result -= sum(x*math.log(x) for x in (p, 1-p) if x > 0)
            else:
                raise ValueError("unknown uncertainty comparator")
        return result

    @lru_cache(maxsize=60000)
    def solve(self, fixed, state, visits, depth):
        best, action = (self.reward(fixed, state), 0., 0.), None
        if not depth:
            return best, action
        if not 0 < depth <= 3:
            raise ValueError("bounded research depth must be zero through three")
        for fact, route in self.actions(fixed, state, visits):
            value = [-self.visit_penalty*route, -float(route), -1.]
            for probability, updated in self.branches(fixed, state, fact, route):
                following, _ = self.solve(fixed, updated, visits-route, depth-1)
                for j in range(3):
                    value[j] += probability*following[j]
            candidate = tuple(round(x, 12) for x in value)
            if candidate > best:
                best, action = candidate, (fact, route)
        return best, action

    def choose(self, fixed, state, visits, remaining, *, policy="targeted", horizon=2):
        if remaining <= 0:
            return None
        if all(x.label >= 0 for x in self.decisions(fixed, state)):
            return None
        if policy == "targeted":
            _, action = self.solve(fixed, state, visits, min(remaining, horizon))
            if action is not None:
                return action
            # An incomplete multi-fact witness can have zero one/two-step gain.
            # Information-gain fallback is declared, shared and tested, rather
            # than pretending this approximation has an adaptive-submodular bound.
            policy = "entropy"
        if policy not in {"entropy", "bayes_error"}:
            raise ValueError("unknown acquisition policy")
        current = self.uncertainty(fixed, state, policy)
        best, action = (0., 0., 0.), None
        for fact, route in self.actions(fixed, state, visits):
            future = sum(p*self.uncertainty(fixed, updated, policy)
                         for p, updated in self.branches(fixed, state, fact, route))
            value = (round(current-future-self.visit_penalty*route, 12), -float(route), -1.)
            if value > best:
                best, action = value, (fact, route)
        return action

    def clear_caches(self):
        for method in (self._fact_posterior, self.belief, self.decisions, self.branches, self.solve):
            method.cache_clear()
