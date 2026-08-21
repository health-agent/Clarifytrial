"""Public evaluation dataset adapters."""

from .clinicaltrials_gov import (
    CLARIFYTRIAL_V5_NCT_IDS,
    fetch_clinicaltrials_v5_sources,
)
from .trialgpt import (
    TrialGPTCriterionRow,
    TrialGPTPair,
    TrialGPTPatientSplit,
    TrialGPTTrialMetadata,
    fetch_trialgpt_dataset,
    group_patient_trial_pairs,
    load_sigir_trial_metadata,
    load_trialgpt_rows,
    select_pilot_pairs,
    select_full_trialgpt_pairs,
    split_trialgpt_pairs_by_patient,
    summarize_trialgpt_rows,
)

__all__ = [
    "CLARIFYTRIAL_V5_NCT_IDS",
    "TrialGPTCriterionRow",
    "TrialGPTPair",
    "TrialGPTPatientSplit",
    "TrialGPTTrialMetadata",
    "fetch_trialgpt_dataset",
    "fetch_clinicaltrials_v5_sources",
    "group_patient_trial_pairs",
    "load_sigir_trial_metadata",
    "load_trialgpt_rows",
    "select_pilot_pairs",
    "select_full_trialgpt_pairs",
    "split_trialgpt_pairs_by_patient",
    "summarize_trialgpt_rows",
]
