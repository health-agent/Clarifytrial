from itertools import product
import math

import pytest

from clarifytrial.interactive.paper_benchmark import PublicAcquisitionModel, ScalarRule, LogicalRule
from clarifytrial.interactive.risk_aware_acquisition import RiskAwareAcquisition
from clarifytrial.interactive.selective_calibration import (CalibrationCandidate,
    binomial_lower_tail, clopper_pearson_upper, calibrate)


def model(logic=None, rules=None):
    return PublicAcquisitionModel(("a", "b"), ((0., 1., 2.), (0., 1., 2.)),
        ((.2, .3, .5), (.2, .3, .5)),
        rules or ((ScalarRule(0, "gte", 1., False), ScalarRule(1, "gte", 1., False)),),
        logic or ())


def test_high_prior_without_an_observed_witness_cannot_release_a_decision():
    planner = RiskAwareAcquisition(model(), risk_cutoff=.49)
    decision, = planner.decisions((-1,-1), planner.empty_state())
    assert decision.label == decision.witness_label == -1
    assert decision.evidence_facts == ()


@pytest.mark.parametrize("state", [(2,-2,2,-2),(2,2,2,2),(0,-2,2,-2),(0,2,2,2)])
def test_root_probabilities_match_full_latent_and_source_enumeration(state):
    m = model()
    planner = RiskAwareAcquisition(m, risk_cutoff=.1)
    ps, _, valid = planner.belief((-1,-1), state)
    for row, (rho, _) in zip(ps, valid):
        total = positive = 0.
        for truth in product(range(3), repeat=2):
            weight = math.prod(m.probabilities[f][t] for f,t in enumerate(truth))
            for f,t in enumerate(truth):
                reports = [x for x in state[2*f:2*f+2] if x >= 0]
                likelihood = lambda y: .95 if t == y else .025
                if len(reports) == 1: weight *= likelihood(reports[0])
                elif len(reports) == 2:
                    a,b = reports
                    weight *= (1-rho)*likelihood(a)*likelihood(b)+rho*likelihood(a)*(a == b)
            total += weight
            positive += weight*int(all(t >= 1 for t in truth))
        assert row[0] == pytest.approx(positive/total)


def test_repeated_fact_bounds_are_not_treated_as_independent_probabilities():
    rules = ((ScalarRule(0,"gte",1.,False), ScalarRule(0,"lt",2.,False)),)
    planner = RiskAwareAcquisition(model(rules=rules))
    probabilities, _, _ = planner.belief((-1,-1), planner.empty_state())
    assert all(p[0] == pytest.approx(.3) for p in probabilities)


def test_shared_error_agreement_does_not_gain_spurious_independent_confidence():
    p = RiskAwareAcquisition(model(), coupling=(1.,), risk_cutoff=.1)
    first = p.belief((-1,2), (1,-2,-2,-2))[0][0][0]
    paired = p.belief((-1,2), (1,1,-2,-2))[0][0][0]
    assert first == pytest.approx(paired)
    independent = RiskAwareAcquisition(model(), coupling=(0.,))
    assert independent.belief((-1,2),(1,1,-2,-2))[0][0][0] > paired


def test_copied_source_is_one_coupled_observation_and_conflict_is_not_repaired():
    p = RiskAwareAcquisition(model(), same_source_family=True)
    assert p.coupling == (1.,)
    with pytest.raises(ValueError, match="zero probability"):
        p.belief((-1,2),(0,2,-2,-2))


def test_predictive_branches_are_normalized_and_obey_the_available_route():
    p = RiskAwareAcquisition(model())
    fixed = (-1,2)
    state = (1,-2,-2,-2)
    branches = p.branches(fixed,state,0,1)
    assert sum(prob for prob,_ in branches) == pytest.approx(1.)
    assert all(updated[0] == 1 and updated[2:] == (-2,-2) for _,updated in branches)
    assert p.actions(fixed,state,0) == ()
    assert p.actions(fixed,state,1) == ((0,1),)


def test_posterior_predictive_tower_law_holds_with_a_shared_error_model_mixture():
    p=RiskAwareAcquisition(model())
    fixed=(-1,-1);state=(1,-2,2,-2)
    def mean_probability(s):
        probabilities,weights,_=p.belief(fixed,s)
        return sum(w*row[0] for w,row in zip(weights,probabilities))
    before=mean_probability(state)
    after=sum(probability*mean_probability(updated)
        for probability,updated in p.branches(fixed,state,0,1))
    assert after==pytest.approx(before)


def test_two_step_expected_value_matches_explicit_policy_tree_enumeration():
    p = RiskAwareAcquisition(model(), coupling=(0.,), quality=(1.,1.), risk_cutoff=.1)
    fixed = (-1,-1); state = p.empty_state()
    def brute(s,v,b):
        best = p.reward(fixed,s)
        if b:
            for f,r in p.actions(fixed,s,v):
                best = max(best,sum(prob*brute(next_s,v-r,b-1)
                    for prob,next_s in p.branches(fixed,s,f,r)))
        return best
    value,action = p.solve(fixed,state,1,2)
    assert value[0] == pytest.approx(brute(state,1,2))
    assert action in p.actions(fixed,state,1)


def test_calibration_is_exact_binomial_not_repeated_trial_pseudoreplication():
    assert binomial_lower_tail(0,100,.05) == pytest.approx(.95**100)
    assert clopper_pearson_upper(0,100) == pytest.approx(1-.05**.01)
    assert clopper_pearson_upper(100,100) == 1.
    assert clopper_pearson_upper(0,0) == 1.
    a = CalibrationCandidate("good",200,0,300,80)
    b = CalibrationCandidate("bad",200,30,400,90)
    result = calibrate([a,b],family_size=4)
    assert result["chosen"]["name"] == "good"
    assert result["candidates"][0]["risk_upper_simultaneous"] > clopper_pearson_upper(0,200)
    assert calibrate([b])["chosen"] is None


def test_stopping_when_every_subset_has_an_observed_accepted_witness():
    p=RiskAwareAcquisition(model(), risk_cutoff=.49)
    assert p.choose((2,2),p.empty_state(),1,3) is None


def test_new_protocol_fixture_is_disjoint_and_preserves_canonical_measurement_units():
    import json
    from pathlib import Path
    from clarifytrial.interactive.paper_benchmark import VALUE_GRIDS
    path=Path(__file__).resolve().parents[1]/"data/paper_risk_method_v1/protocol_subsets.json"
    data=json.loads(path.read_text(encoding="utf-8"))
    trials=[t for g in data["groups"] for t in g["trials"]]
    ids={t["nct_id"] for t in trials}
    assert len(trials)==len(ids)==24
    assert not ids.intersection(data["prior_trial_ids"])
    expected={"absolute_neutrophil_count_10_9_l":"10^9/L","platelet_count_10_9_l":"10^9/L","hemoglobin_g_dl":"g/dL"}
    for trial in trials:
        assert len(trial["criteria"])>=2
        for row in trial["criteria"]:
            assert row["fact_code"] in VALUE_GRIDS
            if row["fact_code"] in expected:assert row["unit"]==expected[row["fact_code"]]
            if row.get("original_unit") in {"/uL","/mm3"}:
                assert row["threshold"]==pytest.approx(row["original_threshold"]/1000)
