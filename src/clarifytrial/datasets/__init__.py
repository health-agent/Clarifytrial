"""Public evaluation dataset adapters."""

from .clinicaltrials_gov import (
    CLARIFYTRIAL_V5_NCT_IDS,
    fetch_clinicaltrials_v5_sources,
)
from .natural_evaluation import (
    NaturalEvaluationSelectionConfig,
    audit_natural_evaluation_review,
    load_natural_evaluation_selection_config,
    materialize_natural_evaluation_reserve_sources,
    objective_criterion_candidates,
    prepare_natural_evaluation_sources,
)
from .natural_benchmark import build_natural_evaluation_trial_set
from .natural_patient_pairs import (
    NaturalPatientGenerationConfig,
    audit_natural_evaluation_patient_pairs,
    build_natural_evaluation_patient_pairs,
    load_natural_patient_generation_config,
)
from .natural_policy_evaluation import run_natural_policy_evaluation
from .natural_records import (
    audit_natural_evaluation_records,
    build_natural_evaluation_records,
)
from .natural_review import compare_natural_evaluation_reviews
from .natural_structure_evaluation import run_natural_record_structure_evaluation
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
    "NaturalEvaluationSelectionConfig",
    "NaturalPatientGenerationConfig",
    "TrialGPTCriterionRow",
    "TrialGPTPair",
    "TrialGPTPatientSplit",
    "TrialGPTTrialMetadata",
    "fetch_trialgpt_dataset",
    "compare_natural_evaluation_reviews",
    "build_natural_evaluation_trial_set",
    "build_natural_evaluation_patient_pairs",
    "build_natural_evaluation_records",
    "audit_natural_evaluation_review",
    "audit_natural_evaluation_patient_pairs",
    "audit_natural_evaluation_records",
    "fetch_clinicaltrials_v5_sources",
    "group_patient_trial_pairs",
    "load_sigir_trial_metadata",
    "load_natural_evaluation_selection_config",
    "load_natural_patient_generation_config",
    "materialize_natural_evaluation_reserve_sources",
    "load_trialgpt_rows",
    "select_pilot_pairs",
    "select_full_trialgpt_pairs",
    "objective_criterion_candidates",
    "prepare_natural_evaluation_sources",
    "run_natural_policy_evaluation",
    "run_natural_record_structure_evaluation",
    "split_trialgpt_pairs_by_patient",
    "summarize_trialgpt_rows",
]
