from __future__ import annotations

import csv
import json
import pytest

from scripts import build_kosmi_poster_measurements as report
from clarifytrial.interactive.public_benchmark import (
    _actual_scenario, build_public_case, load_public_benchmark_spec,
)
from clarifytrial.interactive.policies import ClarifyTrialExactCoveragePolicy, ClarifyTrialRulePolicy
from clarifytrial.interactive.stress import _simulate, build_stress_case


def test_first_candidate_uses_one_saved_sequence_and_preserves_early_stop():
    case = build_stress_case("chain", 0, seed=20260830, action_budget=3)
    prefix = f"{case.case_id}-fact-"
    path = report._candidate_trajectory(case, [prefix + str(i) for i in (0, 4, 1)])
    assert [len(step["reference_confirmed_ids"]) for step in path] == [0, 0, 0, 1]
    short_path = report._candidate_trajectory(case, [prefix + str(i) for i in (3, 4)])
    assert [len(step["reference_confirmed_ids"]) for step in short_path] == [0, 0, 1, 1]
    assert short_path[-1]["actions_used"] == 2
    with pytest.raises(ValueError, match="repeats a fact"):
        report._candidate_trajectory(case, [prefix + "0", prefix + "0"])


def test_two_masks_remain_one_patient_and_replay_mismatch_is_rejected(tmp_path, monkeypatch):
    spec = load_public_benchmark_spec("configs/interactive_public_benchmark_v1.json")
    group = next(group for group in spec.groups if group.group_id == "major_depressive_disorder")
    profile = next(profile for profile in group.profiles if profile.profile_id == "mdd-06")
    group = group.model_copy(update={"profiles": [profile]})
    config = tmp_path / "config.json"
    config.write_text(spec.model_copy(update={"groups": [group]}).model_dump_json(), encoding="utf-8")
    policies = (ClarifyTrialRulePolicy(), ClarifyTrialExactCoveragePolicy())
    monkeypatch.setattr(report, "PROGRESS_POLICIES", tuple(p.policy_id for p in policies))
    rows = []
    for mask in group.masks:
        case = build_public_case(group, profile, mask)
        for policy in policies:
            result = _simulate(case.public_policy_view(), case.initial_patient_state(), _actual_scenario(case), policy)
            rows.append({"group_id": group.group_id, "profile_id": profile.profile_id,
                         "mask_id": mask.mask_id, "split": "heldout", "policy_id": policy.policy_id,
                         "action_budget": 3, "actions": result.actions,
                         "selected_fact_ids": list(result.selected_fact_ids),
                         "trial_recovery": result.trial_recovery,
                         "confirmed_rescue_count": result.confirmed_rescues,
                         "ineligible_cleanup_count": result.ineligible_cleanups})
    raw = tmp_path / "budget-3" / "case-results.jsonl"
    raw.parent.mkdir()
    raw.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
    result = report.build_supplement(tmp_path, tmp_path / "out", config)
    overall = [row for row in result["summary"] if row["mask_id"] == "all"]
    assert all(row["base_patient_count"] == 1 and row["masked_case_count"] == 2 for row in overall)
    with (tmp_path / "out" / "candidate_progress_patients.csv").open(encoding="utf-8-sig") as stream:
        patients = list(csv.DictReader(stream))
    assert len(patients) == 2
    assert all(row["mask_count"] == "2" for row in patients)
    rows[0]["confirmed_rescue_count"] += 1
    raw.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
    with pytest.raises(ValueError, match="replay differs"):
        report.build_supplement(tmp_path, tmp_path / "out", config)
