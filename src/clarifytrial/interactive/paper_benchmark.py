"""Source-grounded, bounded acquisition research with fallible evidence routes.

This is an experimental model of selected numeric protocol subsets. All patient
values, acquisition probabilities and costs are synthetic, not clinical estimates.
The planner receives only public rules, development marginals, and observations.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from random import Random
from typing import Sequence

from ..contracts import CriterionLogic, EvidenceFact, EvidenceRequirement, NumericConstraint, PatientState, TrialCriterion
from .contracts import InteractivePolicyView, InteractiveTrial
from .oracle import evaluate_policy_view


VALUE_GRIDS = {
    "body_mass_index_kg_m2": (18., 22., 25., 28., 32., 36., 42., 48.),
    "egfr_ml_min_1_73m2": (12., 22., 35., 48., 65., 90.),
    "egfr_ml_min": (12., 22., 35., 48., 65., 90.),
    "hba1c": (5.8, 6.4, 7.2, 8.4, 9.4, 10.2, 11.5),
    "hemoglobin_g_dl": (7., 8., 9., 10., 12., 14.),
    "bilirubin_mg_dl": (.7, 1.5, 2.8, 3., 4., 6.),
    "alt_xuln": (1., 2., 3., 4., 6.),
    "ast_xuln": (1., 2., 3., 4., 6.),
    "ecog_score": (0., 1., 2., 3.),
    "absolute_neutrophil_count_10_9_l": (.8, 1.2, 1.5, 2., 4.),
    "platelet_count_10_9_l": (70., 100., 150., 250., 350., 450.),
    "body_weight_kg": (3., 25., 32., 40., 60., 90.),
    "heart_rate_bpm": (48., 58., 65., 80., 110., 145.),
    "serum_creatinine_mg_dl": (.7, 1.2, 1.6, 2., 3., 5.),
    "uacr_mg_g": (10.,30.,50.,100.,500.,1500.,3000.),
    "creatinine_clearance_ml_min": (15.,25.,30.,40.,50.,70.,100.),
    "lvef_percent": (20.,25.,35.,40.,45.,50.,55.,65.),
    "serum_albumin_g_dl": (1.5,2.,2.5,3.,4.),
    "alkaline_phosphatase_xuln": (1.,2.,2.5,3.,5.),
    "inr_ratio": (1.,1.2,1.5,1.8,2.5),
    "bilirubin_xuln": (.8,1.,1.5,2.,3.,4.,5.,6.),
    "liver_metastases": (0.,1.),
    "gilbert_syndrome": (0.,1.),
    "dialysis": (0.,1.),
    "systolic_bp_mmhg": (85.,95.,110.,135.,140.,150.,165.),
    "diastolic_bp_mmhg": (55.,60.,75.,80.,90.,105.),
    "office_systolic_bp_mmhg": (110.,130.,140.,150.,170.),
    "office_diastolic_bp_mmhg": (65.,80.,90.,100.,110.),
    "home_systolic_bp_mmhg": (110.,125.,135.,145.,160.),
    "ntprobnp_pg_ml": (100.,200.,400.,600.,1000.,2500.),
}
AS_OF = datetime(2026, 10, 22, 12, tzinfo=timezone.utc)
QUALITY_PRIORS = (.70, .95)  # Existing record, verification requiring a visit.
POLICIES = ("coverage", "decision_myopic", "decision_budget", "quality_myopic", "quality_budget")


@dataclass(frozen=True)
class ScalarRule:
    fact: int
    operator: str
    threshold: float
    exclusion: bool

    def supports(self, value: float) -> bool:
        if self.operator == "gte":
            matches = value >= self.threshold
        elif self.operator == "gt":
            matches = value > self.threshold
        elif self.operator == "lte":
            matches = value <= self.threshold
        elif self.operator == "lt":
            matches = value < self.threshold
        elif self.operator == "eq":
            matches = value == self.threshold
        else:
            raise ValueError(f"unsupported operator: {self.operator}")
        return not matches if self.exclusion else matches


@dataclass(frozen=True)
class LogicalRule:
    """Three-valued AND/OR over eligibility-facing scalar leaves."""
    operator: str
    leaf: int | None = None
    children: tuple["LogicalRule", ...] = ()

    def evaluate(self, leaves: tuple[int, ...]) -> int:
        if self.operator == "criterion":
            return leaves[self.leaf]
        values=tuple(child.evaluate(leaves) for child in self.children)
        if self.operator == "all":
            return 0 if 0 in values else (-1 if -1 in values else 1)
        if self.operator == "any":
            return 1 if 1 in values else (-1 if -1 in values else 0)
        raise ValueError(f"unsupported logical operator: {self.operator}")

    def unresolved_leaves(self, leaves: tuple[int, ...]) -> set[int]:
        if self.evaluate(leaves) != -1:
            return set()
        if self.operator == "criterion":
            return {self.leaf}
        return {leaf for child in self.children for leaf in child.unresolved_leaves(leaves)}


@dataclass(frozen=True)
class PublicAcquisitionModel:
    """No patient truth or route outcomes may enter this object."""

    codes: tuple[str, ...]
    domains: tuple[tuple[float, ...], ...]
    probabilities: tuple[tuple[float, ...], ...]
    rules: tuple[tuple[ScalarRule, ...], ...]
    logic: tuple[LogicalRule, ...] = ()

    def leaf_statuses(self, trial: tuple[ScalarRule, ...], observed: tuple[int, ...]) -> tuple[int, ...]:
        return tuple(-1 if observed[r.fact]<0 else int(r.supports(self.domains[r.fact][observed[r.fact]])) for r in trial)

    def statuses(self, observed: tuple[int, ...]) -> tuple[int, ...]:
        # -1 unresolved, 0 excluded by a known condition, 1 all selected rules met.
        result = []
        for i, trial in enumerate(self.rules):
            if self.logic:
                result.append(self.logic[i].evaluate(self.leaf_statuses(trial,observed)))
                continue
            known = [r.supports(self.domains[r.fact][observed[r.fact]])
                     for r in trial if observed[r.fact] >= 0]
            result.append(0 if False in known else (1 if len(known) == len(trial) else -1))
        return tuple(result)

    def information_needs(self, observed: tuple[int,...]) -> tuple[set[int], ...]:
        statuses=self.statuses(observed)
        return tuple(set() if status!=-1 else
            ({trial[i].fact for i in self.logic[j].unresolved_leaves(self.leaf_statuses(trial,observed))}
             if self.logic else {r.fact for r in trial if observed[r.fact]<0})
            for j,(trial,status) in enumerate(zip(self.rules,statuses)))


def make_model(group: dict, split: str, development_count: int = 64) -> PublicAcquisitionModel:
    codes = tuple(sorted({r["fact_code"] for t in group["trials"] for r in t["criteria"]}))
    domains = tuple(VALUE_GRIDS[code] for code in codes)
    # Full synthetic development feature vectors are observable training data;
    # no heldout patient or heldout outcome is used to estimate a marginal.
    development = generate_patients(domains, development_count, seed=11000 + sum(map(ord, group["group_id"])))
    probabilities = tuple(tuple((1 + sum(row[j] == k for row in development)) /
        (development_count + len(domain)) for k in range(len(domain))) for j, domain in enumerate(domains))
    rules = tuple(tuple(ScalarRule(codes.index(r["fact_code"]), r["operator"], r["threshold"], r["kind"] == "exclusion")
                        for r in t["criteria"]) for t in group["trials"] if t["split"] == split)
    selected=[t for t in group["trials"] if t["split"]==split]
    def compile_logic(node, indices):
        return LogicalRule(node["operator"],indices.get(node.get("criterion_id")),
                           tuple(compile_logic(c,indices) for c in node.get("children",[])))
    logic=tuple(compile_logic(t["eligibility_logic"],{r["criterion_id"]:i for i,r in enumerate(t["criteria"])})
                if t.get("eligibility_logic") else LogicalRule("all",children=tuple(
                    LogicalRule("criterion",i) for i in range(len(t["criteria"])))) for t in selected)
    # Keep the original flat path exactly when no source logic was declared.
    return PublicAcquisitionModel(codes, domains, probabilities, rules,logic if any(t.get("eligibility_logic") for t in selected) else ())


def generate_patients(domains, count: int, *, seed: int, shifted=False):
    rng = Random(seed)
    rows = []
    for _ in range(count):
        # Shared latent quantile creates cross-feature dependence. The planner's
        # marginal model deliberately does not know this dependence.
        quantile = rng.random() ** (.45 if shifted else 1.5)
        rows.append(tuple(min(len(d) - 1, int(len(d) * (.55 * quantile + .45 * rng.random()))) for d in domains))
    return rows


class RoutePlanner:
    """Exact finite-horizon Bellman search, capped at three acquisition attempts.

    A failed/insufficient result consumes an action and visit, reveals no current
    value, and leaves an alternative route available. Independent failure and
    feature marginals are declared model assumptions, not empirical findings.
    """

    def __init__(self, model: PublicAcquisitionModel, *, quality: tuple[float, float], visit_penalty: float = 0., compact: bool = False):
        if any(not 0 <= p <= 1 for p in quality):
            raise ValueError("route success probability must lie in [0,1]")
        self.model, self.quality = model, quality
        if visit_penalty < 0:
            raise ValueError("visit penalty must be nonnegative")
        self.visit_penalty = visit_penalty
        aliases=[]
        outcomes=[]
        for fact,domain in enumerate(model.domains):
            relevant=[r for trial in model.rules for r in trial if r.fact==fact]
            representatives={}
            probabilities={}
            mapping=[]
            for index,value in enumerate(domain):
                signature=tuple(r.supports(value) for r in relevant) if compact else (index,)
                representative=representatives.setdefault(signature,index)
                mapping.append(representative)
                probabilities[representative]=probabilities.get(representative,0.)+model.probabilities[fact][index]
            aliases.append(tuple(mapping))
            outcomes.append(tuple(probabilities.items()))
        self.aliases=tuple(aliases)
        self.outcomes=tuple(outcomes)

    @lru_cache(maxsize=150000)
    def solve(self, observed: tuple[int, ...], attempted: int, visits: int, depth: int):
        if not 0 <= depth <= 3:
            raise ValueError("research planner supports horizons zero to three")
        canonical=tuple(self.aliases[j][v] if v>=0 else -1 for j,v in enumerate(observed))
        if canonical!=observed:
            return self.solve(canonical,attempted,visits,depth)
        statuses = self.model.statuses(observed)
        base = sum(s != -1 for s in statuses)
        best = (float(base), 0., 0.)  # resolved, negative visits, negative attempts
        best_action = None
        if depth == 0:
            return best, best_action
        active = set().union(*self.model.information_needs(observed))
        for fact in sorted(active):
            for route in range(2):
                bit = 1 << (2 * fact + route)
                if attempted & bit or route > visits:
                    continue
                attempted_next = attempted | bit
                q = self.quality[route]
                value = [-self.visit_penalty * route, -float(route), -1.]
                if q < 1:
                    failure, _ = self.solve(observed, attempted_next, visits - route, depth - 1)
                    for i in range(3):
                        value[i] += (1 - q) * failure[i]
                for index, probability in self.outcomes[fact]:
                    updated = observed[:fact] + (index,) + observed[fact + 1:]
                    success, _ = self.solve(updated, attempted_next, visits - route, depth - 1)
                    for i in range(3):
                        value[i] += q * probability * success[i]
                value = tuple(round(v, 12) for v in value)
                if value > best:
                    best, best_action = value, (fact, route)
        return best, best_action

    def coverage(self, observed, attempted, visits):
        statuses = self.model.statuses(observed)
        counts = {}
        for facts in self.model.information_needs(observed):
            for fact in facts:
                counts[fact] = counts.get(fact, 0) + 1
        choices = [(count, -route, -fact, (fact, route)) for fact, count in counts.items()
                   for route in range(2) if route <= visits and not attempted & (1 << (2 * fact + route))]
        return max(choices)[-1] if choices else None


def reference_statuses(trials: Sequence[dict], values: dict[str, float], usable: set[str]) -> tuple[int, ...]:
    """Independent direct source-row scorer, not mechanical_checks or planner rules."""
    statuses = []
    for trial in trials:
        missing = False
        fails = False
        leaf_values={}
        for row in trial["criteria"]:
            code = row["fact_code"]
            if code not in usable:
                missing = True
                leaf_values[row["criterion_id"]]=-1
                continue
            value, threshold, op = values[code], row["threshold"], row["operator"]
            condition = ((op == "gte" and value >= threshold) or (op == "gt" and value > threshold)
                         or (op == "lte" and value <= threshold) or (op == "lt" and value < threshold)
                         or (op == "eq" and value == threshold))
            if (row["kind"] == "exclusion" and condition) or (row["kind"] == "inclusion" and not condition):
                fails = True
            leaf_values[row["criterion_id"]]=int(condition if row["kind"]=="inclusion" else not condition)
        def reference_tree(node):
            if node["operator"]=="criterion":
                return leaf_values[node["criterion_id"]]
            outcomes=[reference_tree(child) for child in node["children"]]
            if node["operator"]=="all":
                if any(value==0 for value in outcomes): return 0
                return -1 if any(value<0 for value in outcomes) else 1
            if node["operator"]=="any":
                if any(value==1 for value in outcomes): return 1
                return -1 if any(value<0 for value in outcomes) else 0
            raise ValueError("unsupported reference logic")
        statuses.append(reference_tree(trial["eligibility_logic"]) if trial.get("eligibility_logic") else (0 if fails else (-1 if missing else 1)))
    return tuple(statuses)


def runtime_view(group, split) -> InteractivePolicyView:
    trials = []
    for t in group["trials"]:
        if t["split"] != split:
            continue
        criteria = [TrialCriterion(criterion_id=r["criterion_id"], trial_id=t["nct_id"], kind=r["kind"],
            statement=r["source_text"], source_location=r["source_location"],
            numeric_constraint=NumericConstraint(concept=r["fact_code"], operator=r["operator"], threshold=r["threshold"], unit=r["unit"]),
            evidence_requirement=EvidenceRequirement(max_age_days=14, allowed_source_types=["medical_record", "official_verification"],
                                                     allowed_verification_statuses=["verified"])) for r in t["criteria"]]
        trials.append(InteractiveTrial(trial_id=t["nct_id"], criteria=criteria,
            eligibility_logic=CriterionLogic.model_validate(t["eligibility_logic"]) if t.get("eligibility_logic") else None))
    return InteractivePolicyView(case_id="paper-subset", disease_group=group["group_id"], trials=trials, action_budget=3)


def evidence(code, value, unit, outcome="valid", sequence=0):
    if outcome == "no_answer":
        return None
    return EvidenceFact(evidence_id=f"synthetic:{sequence}:{code}:{outcome}", statement=f"Synthetic {code} = {value} {unit}",
        source_type="patient_report" if outcome == "wrong_source" else "medical_record",
        source_location="synthetic://paper-benchmark", event_date=None if outcome == "missing_date" else
            (AS_OF - timedelta(days=90 if outcome == "stale" else 1)).date(), recorded_date=AS_OF.date(),
        verification_status="pending" if outcome == "unverified" else "verified",
        concept=code, value=value, unit=unit)


def runtime_statuses(view, facts):
    state = PatientState(patient_id="synthetic-paper", as_of=AS_OF, facts=[f for f in facts if f is not None])
    return tuple({"confirmed":1, "ineligible":0}.get(d.confirmation_status.value, -1)
                 for d in evaluate_policy_view(view, state).decisions)
