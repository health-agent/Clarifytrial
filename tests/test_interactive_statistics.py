from clarifytrial.interactive.statistics import (
    exact_sign_test,
    stratified_bootstrap_mean,
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
