from clarifytrial.interactive.statistics import (
    exact_sign_test,
    stratified_bootstrap_mean,
    crossed_stratified_bootstrap_mean,
)


def test_exact_sign_test_excludes_ties() -> None:
    assert exact_sign_test([1, 1, 1, 0, 0]) == 0.25
    assert exact_sign_test([0, 0]) == 1.0


def test_stratified_bootstrap_is_reproducible() -> None:
    values = {"group-a": [0.2, 0.4], "group-b": [-0.1, 0.3]}

    first = stratified_bootstrap_mean(
        values,
        cluster_unit="patient",
        seed=17,
        resamples=100,
    )
    second = stratified_bootstrap_mean(
        {"group-b": [0.3, -0.1], "group-a": [0.4, 0.2]},
        cluster_unit="patient",
        seed=17,
        resamples=100,
    )

    assert first == second
    assert first["cluster_unit"] == "patient"
    assert first["pair_count"] == 4
    assert first["mean_difference"] == 0.2


def test_crossed_resampling_preserves_constant_effect():
    result=crossed_stratified_bootstrap_mean({"group-a":[[.25,.25],[.25,.25]]},resamples=100)
    assert result["mean_difference"]==.25
    assert result["percentile_95_range"]=={"lower":.25,"upper":.25}
    assert result["patient_count"]==result["trial_count"]==2


def test_crossed_resampling_varies_trial_columns_even_with_one_patient():
    result=crossed_stratified_bootstrap_mean({"group-a":[[0.,1.]]},resamples=1000)
    assert result["mean_difference"]==.5
    assert result["percentile_95_range"]=={"lower":0.,"upper":1.}


def test_crossed_resampling_rejects_missing_or_ragged_cells():
    import pytest
    with pytest.raises(ValueError):
        crossed_stratified_bootstrap_mean({"group-a":[[1],[1,2]]})
