from clarifytrial.datasets.natural_policy_evaluation import _decision_metrics


def test_policy_metrics_keep_candidate_and_confirmation_separate():
    current = [
        {
            "trial_id": "NCT1",
            "candidate_status": "retain",
            "confirmation_status": "not_confirmed",
        }
    ]
    target = [
        {
            "trial_id": "NCT1",
            "candidate_status": "retain",
            "confirmation_status": "confirmed",
        }
    ]

    result = _decision_metrics(current, target)

    assert result["candidate_status_recovery"] == 1
    assert result["confirmation_status_recovery"] == 0
    assert result["trial_status_recovery"] == 0
