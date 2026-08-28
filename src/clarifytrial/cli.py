"""Local command-line entry points for reproducible ClarifyTrial examples."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from .app import (
    ChallengeTopicsInput,
    GeneralPatientInput,
    GeneralRunOptions,
    ScreeningSession,
    StructuredTrialSource,
    run_full_workflow_evaluation,
    run_general_screening,
)
from .app.challenge_cli import (
    ChallengeCliDependencies,
    add_challenge_parser,
    run_challenge_command,
)
from .agents import (
    CoordinatorAgent,
    MatcherJudgeAgent,
    NextEvidenceAgent,
    SelectiveReviewerAgent,
)
from .contracts import (
    EvidenceSourceType,
    NextAction,
    PatientState,
    RecommendationViews,
    TrialDecision,
)
from .datasets import (
    audit_natural_evaluation_records,
    audit_natural_evaluation_patient_pairs,
    build_natural_evaluation_records,
    build_natural_evaluation_trial_set,
    build_natural_evaluation_patient_pairs,
    compare_natural_evaluation_reviews,
    fetch_clinicaltrials_v5_sources,
    fetch_trialgpt_dataset,
    group_patient_trial_pairs,
    load_sigir_trial_metadata,
    load_trialgpt_rows,
    materialize_natural_evaluation_reserve_sources,
    prepare_natural_evaluation_sources,
    run_natural_policy_evaluation,
    run_natural_record_structure_evaluation,
    select_full_trialgpt_pairs,
    select_pilot_pairs,
    split_trialgpt_pairs_by_patient,
    summarize_trialgpt_rows,
)
from .datasets.team_expansion import select_team_evaluation_trials
from .datasets.broad_rescue import (
    audit_broad_rescue_dataset,
    build_broad_rescue_dataset,
)
from .disclaimer import read_medical_disclaimer
from .datasets.source_benchmark import (
    audit_source_benchmark,
    build_source_benchmark,
)
from .datasets.natural_ai_review import (
    build_conservative_natural_ai_gold,
    run_natural_evaluation_ai_review,
    run_natural_evaluation_max_resolution,
)
from .environment import (
    HiddenFactAnswer,
    HiddenPatientEnvironment,
    PublicFactRequest,
    PublicQuestionCatalog,
    SyntheticInformationTools,
)
from .evaluation import DecisionGold, score_decision
from .experiment_tracking import ExperimentStage
from .llm import (
    ALLOWED_CODEX_EFFORTS,
    AnthropicStructuredModel,
    CodexSubscriptionModelPool,
    CodexSubscriptionStructuredModel,
    DEFAULT_CODEX_EFFORT,
    DEFAULT_CODEX_MODEL,
    DeterministicWorkflowModel,
    ScriptedStructuredModel,
    StructuredModel,
)
from .interactive import (
    AcquisitionOption,
    GuidanceOutput,
    PatientBurdenProfile,
    run_interactive_pilot,
    run_interactive_stress,
    run_public_burden_benchmark,
    run_public_grid_stress,
    run_public_interactive_benchmark,
)
from .pilots import (
    ArchitectureExperimentPaused,
    StrongReviewExperimentIncomplete,
    run_subscription_architecture_stage,
    run_subscription_strong_review_stage,
    run_trialgpt_pilot,
)
from .retrieval import (
    TrialGPTRetrievalConfig,
    TrialGPTRuntimeSearch,
    run_trialgpt_retrieval,
)
from .reporting import (
    build_architecture_comparison,
    build_budget_frontier,
    build_final_evaluation_readiness,
    build_research_report,
)
from .preparation import (
    CandidateSearch,
    InMemoryCandidateSearch,
    NaturalHiddenFactAnswer,
    NaturalScreeningPipeline,
    NaturalScreeningRequest,
    NaturalScreeningResult,
    TeamTrialCandidateSearch,
    TrialGPTCandidateSearch,
    TrialProtocolSource,
    build_synthetic_information_tools,
    inspect_team_trial_corpus,
    prepare_team_trial_corpus,
)
from .preparation.patient_record import PatientRecordStructurerAgent
from .preparation.trial_protocol import TrialProtocolStructurerAgent
from .settings import EpisodeSettings
from .terminal_ui import run_natural_text_demo
from .trace import TraceRecorder
from .ui import build_integrated_ui_fixture, run_integrated_terminal_ui
from .workflow import (
    EpisodeAgents,
    EpisodeCase,
    EpisodeResult,
    EpisodeRunner,
    PatientScreeningCase,
    PatientScreeningResult,
    PatientScreeningRunner,
)


def _configure_utf8_stream(stream: Any) -> None:
    """Keep Korean terminal input and output readable across Windows code pages."""

    encoding = str(getattr(stream, "encoding", "") or "").replace("-", "").lower()
    reconfigure = getattr(stream, "reconfigure", None)
    if encoding in {"utf8", "utf8sig"} or not callable(reconfigure):
        return
    try:
        reconfigure(encoding="utf-8", errors="replace")
    except (OSError, ValueError):
        return


def _configure_utf8_stdio() -> None:
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        _configure_utf8_stream(stream)


_TRIALGPT_VARIANTS: dict[str, tuple[str, str | None]] = {
    "current": ("prompts/trialgpt_criterion_judge.md", None),
    "current-review": (
        "prompts/trialgpt_criterion_judge.md",
        "prompts/trialgpt_criterion_reviewer.md",
    ),
    "faithful": ("prompts/trialgpt_criterion_judge_faithful.md", None),
    "calibrated": ("prompts/trialgpt_criterion_judge_calibrated.md", None),
    "balanced": ("prompts/trialgpt_criterion_judge_balanced.md", None),
    "calibrated-review": (
        "prompts/trialgpt_criterion_judge_calibrated.md",
        "prompts/trialgpt_criterion_reviewer.md",
    ),
}


def _read_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def _read_disclaimer() -> str:
    return read_medical_disclaimer()


def _build_trialgpt_candidate_search(
    args: argparse.Namespace,
    parser: argparse.ArgumentParser,
) -> CandidateSearch:
    if args.trialgpt_corpus is None or args.trialgpt_cache is None:
        parser.error(
            "trialgpt candidate search requires --trialgpt-corpus and "
            "--trialgpt-cache"
        )
    runtime_search = TrialGPTRuntimeSearch(
        args.trialgpt_corpus,
        args.trialgpt_cache,
        TrialGPTRetrievalConfig(
            corpus_name=args.trialgpt_corpus_name,
            bm25_weight=1,
            medcpt_weight=0 if args.bm25_only else 1,
            device=args.retrieval_device,
        ),
        progress=print,
    )
    return TrialGPTCandidateSearch(runtime_search)


def _read_env_value(path: Path, name: str) -> str:
    """Read one named value without copying the credential into project files."""

    with path.open(encoding="utf-8-sig") as stream:
        for line in stream:
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            if key.strip() != name:
                continue
            resolved = value.strip().strip('"').strip("'")
            if not resolved:
                break
            return resolved
    raise ValueError(f"credential file does not define a non-empty {name}")


class _StaleLabScript:
    """Deterministic stand-in for model calls in the stale-lab plumbing test."""

    def __init__(self, case: EpisodeCase) -> None:
        if len(case.criteria) != 1 or len(case.evidence_requests) != 1:
            raise ValueError(
                "the bundled stale-lab script requires one criterion and one request"
            )
        self._criterion = case.criteria[0]
        self._request = case.evidence_requests[0]
        self._initial_evidence_ids = {
            fact.evidence_id for fact in case.initial_patient_state.facts
        }

    @staticmethod
    def coordinate(payload: Mapping[str, Any]) -> Mapping[str, Any]:
        allowed_routes = payload["allowed_routes"]
        if not isinstance(allowed_routes, list) or len(allowed_routes) != 1:
            raise ValueError("the coordinator needs exactly one permitted route")
        return {
            "route": allowed_routes[0],
            "target_ids": payload.get("dirty_criterion_ids", []),
            "reason_code": "permitted_state_transition",
            "reason": "현재 상태에서 실행할 수 있는 다음 단계로 진행한다.",
        }

    def match(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        patient_facts = payload["patient_facts"]
        if not isinstance(patient_facts, list):
            raise ValueError("patient_facts must be a list")
        new_official_facts = [
            fact
            for fact in patient_facts
            if fact["evidence_id"] not in self._initial_evidence_ids
            and fact["source_type"] == EvidenceSourceType.OFFICIAL_VERIFICATION.value
        ]
        if new_official_facts:
            evidence_id = new_official_facts[0]["evidence_id"]
            assessment = {
                "criterion_id": self._criterion.criterion_id,
                "criterion_source_location": self._criterion.source_location,
                "clinical_status": "supports",
                "evidence_sufficiency": "sufficient",
                "evidence_ids": [evidence_id],
                "missing_information_ids": [],
                "rationale": (
                    "새로 받은 공식 결과는 요구 기간 안에 있으며 혈소판 기준을 "
                    "충족한다."
                ),
                "review_flags": [],
            }
        else:
            visible_ids = [fact["evidence_id"] for fact in patient_facts]
            assessment = {
                "criterion_id": self._criterion.criterion_id,
                "criterion_source_location": self._criterion.source_location,
                "clinical_status": "supports",
                "evidence_sufficiency": "insufficient",
                "evidence_ids": visible_ids,
                "missing_information_ids": [self._request.fact_id],
                "rationale": (
                    "오래된 결과는 후보를 남길 근거가 되지만 시험에서 요구한 "
                    "14일 범위를 벗어난다."
                ),
                "review_flags": [],
            }
        return {"assessments": [assessment]}

    @staticmethod
    def choose_evidence(payload: Mapping[str, Any]) -> Mapping[str, Any]:
        pending = payload["pending_information"]
        if not isinstance(pending, list) or not pending:
            raise ValueError("the evidence agent needs a pending request")
        request = pending[0]
        action = request["acceptable_actions"][0]
        message = None
        if action == NextAction.REQUEST_VERIFICATION.value:
            message = "최근 14일 안에 받은 공식 혈소판 검사 결과를 확인해 주세요."
        elif action == NextAction.ASK_PATIENT.value:
            message = request["description"]
        return {
            "action": action,
            "target_fact_id": request["fact_id"],
            "related_criterion_ids": request["related_criterion_ids"],
            "reason": request["reason"],
            "message": message,
        }

    @staticmethod
    def unexpected_review(_: Mapping[str, Any]) -> Mapping[str, Any]:
        raise RuntimeError("the stale-lab example has no structural review trigger")


def _build_agents(case: EpisodeCase) -> tuple[EpisodeAgents, ScriptedStructuredModel]:
    script = _StaleLabScript(case)
    model = ScriptedStructuredModel(
        {
            "coordinator": script.coordinate,
            "matcher_judge": script.match,
            "next_evidence": script.choose_evidence,
            "selective_reviewer": script.unexpected_review,
        }
    )
    return (
        EpisodeAgents(
            coordinator=CoordinatorAgent(model),
            matcher_judge=MatcherJudgeAgent(model),
            next_evidence=NextEvidenceAgent(model),
            selective_reviewer=SelectiveReviewerAgent(model),
        ),
        model,
    )


def _load_tools(case_dir: Path) -> SyntheticInformationTools:
    public_items = [
        PublicFactRequest.model_validate(item)
        for item in _read_json(case_dir / "public_questions.json")
    ]
    hidden_items = [
        HiddenFactAnswer.model_validate(item)
        for item in _read_json(case_dir / "hidden_answers.json")
    ]
    return SyntheticInformationTools(
        PublicQuestionCatalog(public_items),
        HiddenPatientEnvironment(hidden_items),
    )


def _initial_action_decision(result: EpisodeResult) -> TrialDecision:
    for decision in result.decision_history:
        if decision.next_action.action is not NextAction.NONE:
            return decision
    return result.decision_history[0]


def run_example(case_dir: str | Path, output_dir: str | Path) -> Path:
    """Run one local scripted case through the production episode state machine."""

    source = Path(case_dir)
    destination = Path(output_dir)
    case = EpisodeCase.model_validate(_read_json(source / "system_input.json"))
    tools = _load_tools(source)
    agents, model = _build_agents(case)
    trace = TraceRecorder(case.case_id)
    result = EpisodeRunner(
        agents,
        EpisodeSettings(
            max_external_actions=3,
            max_selective_reviews=1,
            max_cycles=6,
        ),
    ).run(case, tools, trace=trace)

    # Gold labels are deliberately opened only after the episode has finished.
    gold = DecisionGold.model_validate(_read_json(source / "gold_initial.json"))
    initial_score = score_decision(_initial_action_decision(result), gold)

    result_path = destination / "result.json"
    _write_json(
        result_path,
        {
            "run_mode": "scripted_local_dry_run",
            "external_api_calls": 0,
            "disclaimer": _read_disclaimer(),
            "input_files": {
                "visible_system_input": str(source / "system_input.json"),
                "visible_question_catalog": str(source / "public_questions.json"),
                "hidden_environment_answers": str(source / "hidden_answers.json"),
                "post_run_gold_labels": str(source / "gold_initial.json"),
            },
            "model_calls_by_role": dict(model.call_count),
            "episode": result.model_dump(mode="json"),
            "initial_state_score": initial_score.model_dump(mode="json"),
        },
    )
    trace.write_jsonl(destination / "trace.jsonl")
    return result_path


def run_natural_screening_from_files(
    *,
    request_path: str | Path,
    hidden_answers_path: str | Path,
    output_dir: str | Path,
    model: StructuredModel,
    candidate_search: CandidateSearch,
    episode_settings: EpisodeSettings,
) -> Path:
    """Run the natural-text connection with a separate synthetic answer file."""

    request = NaturalScreeningRequest.model_validate(_read_json(Path(request_path)))
    answers_raw = _read_json(Path(hidden_answers_path))
    if not isinstance(answers_raw, list):
        raise ValueError("hidden answers file must contain a JSON list")
    answers = [NaturalHiddenFactAnswer.model_validate(item) for item in answers_raw]
    agents = EpisodeAgents(
        coordinator=CoordinatorAgent(model),
        matcher_judge=MatcherJudgeAgent(model),
        next_evidence=NextEvidenceAgent(model),
        selective_reviewer=SelectiveReviewerAgent(model),
    )
    pipeline = NaturalScreeningPipeline(
        patient_structurer=PatientRecordStructurerAgent(model),
        trial_structurer=TrialProtocolStructurerAgent(model),
        candidate_search=candidate_search,
        screening_runner=PatientScreeningRunner(agents, episode_settings),
    )
    trace = TraceRecorder(request.case_id)
    result = pipeline.run(
        request,
        lambda prepared: build_synthetic_information_tools(prepared, answers),
        trace=trace,
    )
    destination = Path(output_dir)
    result_path = destination / "result.json"
    _write_json(
        result_path,
        {
            "run_mode": "natural_text_synthetic_information_environment",
            "medical_disclaimer": _read_disclaimer(),
            "result": result.model_dump(mode="json"),
        },
    )
    trace.write_jsonl(destination / "trace.jsonl")
    return result_path


def export_schemas(output_dir: str | Path) -> list[Path]:
    """Write the public input, output, environment, and gold-label contracts."""

    destination = Path(output_dir)
    models: Sequence[tuple[str, type[BaseModel]]] = (
        ("episode-case.schema.json", EpisodeCase),
        ("episode-result.schema.json", EpisodeResult),
        ("patient-state.schema.json", PatientState),
        ("trial-decision.schema.json", TrialDecision),
        ("public-fact-request.schema.json", PublicFactRequest),
        ("hidden-fact-answer.schema.json", HiddenFactAnswer),
        ("decision-gold.schema.json", DecisionGold),
        ("acquisition-option.schema.json", AcquisitionOption),
        ("patient-burden-profile.schema.json", PatientBurdenProfile),
        ("guidance-output.schema.json", GuidanceOutput),
        ("recommendation-views.schema.json", RecommendationViews),
        ("patient-screening-case.schema.json", PatientScreeningCase),
        ("patient-screening-result.schema.json", PatientScreeningResult),
        ("natural-screening-request.schema.json", NaturalScreeningRequest),
        ("natural-screening-result.schema.json", NaturalScreeningResult),
        ("general-patient-input.schema.json", GeneralPatientInput),
        ("structured-trial-source.schema.json", StructuredTrialSource),
        ("screening-session.schema.json", ScreeningSession),
        ("challenge-topics-input.schema.json", ChallengeTopicsInput),
    )
    paths: list[Path] = []
    for name, model in models:
        path = destination / name
        _write_json(path, model.model_json_schema())
        paths.append(path)
    return paths


def prepare_trialgpt(cache_dir: str | Path, *, force: bool = False) -> dict[str, Any]:
    """Download and validate the public criterion annotations."""

    raw_path, metadata_path = fetch_trialgpt_dataset(cache_dir, force=force)
    rows = load_trialgpt_rows(raw_path)
    return {
        "raw_jsonl": str(raw_path),
        "source_metadata": str(metadata_path),
        "statistics": summarize_trialgpt_rows(rows),
    }


def run_live_trialgpt_pilot(
    *,
    raw_jsonl: Path,
    sigir_corpus: Path,
    output_dir: Path,
    api_key_env_file: Path,
    api_key_name: str,
    limit: int,
    seed: int,
    model_id: str,
    max_output_tokens: int,
    timeout_seconds: float,
) -> Path:
    """Run a bounded Sonnet pilot on the pinned, stratified TrialGPT pairs."""

    if not 1 <= limit <= 20:
        raise ValueError("limit must be between 1 and 20")
    rows = load_trialgpt_rows(raw_jsonl)
    metadata = load_sigir_trial_metadata(sigir_corpus)
    pairs = group_patient_trial_pairs(rows, metadata)
    selected = select_pilot_pairs(pairs, seed=seed)[:limit]
    if any(pair.metadata is None for pair in selected):
        raise ValueError("selected TrialGPT pair is missing SIGIR trial metadata")

    model = AnthropicStructuredModel(
        api_key=_read_env_value(api_key_env_file, api_key_name),
        model_id=model_id,
        max_output_tokens=max_output_tokens,
        timeout_seconds=timeout_seconds,
    )
    run_trialgpt_pilot(
        selected,
        model,
        output_dir,
        configured_model_id=model_id,
        effort="medium",
        selection_seed=seed,
    )
    return output_dir / "summary.json"


def run_live_trialgpt_experiment(
    *,
    raw_jsonl: Path,
    sigir_corpus: Path,
    output_dir: Path,
    api_key_env_file: Path,
    api_key_name: str,
    variant: str,
    split_name: str,
    seed: int,
    limit: int | None,
    model_id: str,
    max_output_tokens: int,
    timeout_seconds: float,
) -> Path:
    """Run one declared prompt policy on a patient-separated data partition."""

    if variant not in _TRIALGPT_VARIANTS:
        raise ValueError(f"unknown TrialGPT experiment variant: {variant}")
    if limit is not None and limit <= 0:
        raise ValueError("limit must be positive when supplied")

    rows = load_trialgpt_rows(raw_jsonl)
    metadata = load_sigir_trial_metadata(sigir_corpus)
    pairs = group_patient_trial_pairs(rows, metadata)
    patient_split = split_trialgpt_pairs_by_patient(pairs, seed=seed)
    if split_name == "development":
        selected = list(patient_split.development_pairs)
    elif split_name == "heldout":
        selected = list(patient_split.held_out_pairs)
    elif split_name == "overlap":
        selected = list(patient_split.overlap_patient_pairs)
    elif split_name == "all":
        selected = select_full_trialgpt_pairs(pairs)
    else:
        raise ValueError(f"unknown TrialGPT experiment split: {split_name}")
    if limit is not None:
        selected = selected[:limit]
    if not selected:
        raise ValueError("selected TrialGPT experiment partition is empty")
    if any(pair.metadata is None for pair in selected):
        raise ValueError("selected TrialGPT pair is missing SIGIR trial metadata")

    prompt_id, review_prompt_id = _TRIALGPT_VARIANTS[variant]
    model = AnthropicStructuredModel(
        api_key=_read_env_value(api_key_env_file, api_key_name),
        model_id=model_id,
        max_output_tokens=max_output_tokens,
        timeout_seconds=timeout_seconds,
    )
    run_trialgpt_pilot(
        selected,
        model,
        output_dir,
        configured_model_id=model_id,
        effort="medium",
        selection_seed=seed,
        variant=variant,
        prompt_id=prompt_id,
        review_prompt_id=review_prompt_id,
    )
    return output_dir / "summary.json"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="clarifytrial",
        description="Run inspectable ClarifyTrial research workflows.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser(
        "run-example",
        help="run a synthetic example without an external API",
    )
    run.add_argument("--case", required=True, type=Path)
    run.add_argument("--output", required=True, type=Path)

    general = commands.add_parser(
        "run-screening",
        help=(
            "search a supplied structured trial pool, ask for missing facts, "
            "and save resumable results"
        ),
    )
    general.add_argument("--patient", required=True, type=Path)
    general.add_argument("--trials", required=True, type=Path)
    general.add_argument("--output", required=True, type=Path)
    general.add_argument(
        "--candidate-search",
        choices=("trialgpt", "local-bm25"),
        default="local-bm25",
    )
    general.add_argument("--trialgpt-corpus", type=Path)
    general.add_argument("--trialgpt-cache", type=Path)
    general.add_argument(
        "--trialgpt-corpus-name",
        choices=("trec_2021", "trec_2022"),
        default="trec_2022",
    )
    general.add_argument("--retrieval-device", default="cuda")
    general.add_argument("--bm25-only", action="store_true")
    general.add_argument("--retrieval-search-depth", type=int, default=500)
    general.add_argument(
        "--answers",
        type=Path,
        help="optional deterministic answer file; omit to type answers",
    )
    general.add_argument(
        "--resume",
        type=Path,
        help="resume from a previously saved session.json",
    )
    general.add_argument(
        "--retry-unavailable",
        action="store_true",
        help="try facts again even if an earlier session could not obtain them",
    )
    general.add_argument(
        "--approve-patient-choice",
        action="store_true",
        help="record patient approval for the option waiting in --resume",
    )
    general.add_argument(
        "--authorize-clinician",
        action="store_true",
        help="record clinician authorization for the option waiting in --resume",
    )
    general.add_argument(
        "--provider",
        choices=("deterministic", "codex-subscription", "anthropic"),
        default="deterministic",
    )
    general.add_argument("--model")
    general.add_argument(
        "--effort",
        choices=sorted(ALLOWED_CODEX_EFFORTS),
        default=DEFAULT_CODEX_EFFORT,
    )
    general.add_argument("--api-key-env-file", type=Path)
    general.add_argument("--api-key-name", default="ANTHROPIC_API_KEY")
    general.add_argument("--max-output-tokens", type=int, default=8_192)
    general.add_argument("--timeout-seconds", type=float, default=300)
    general.add_argument("--max-external-actions", type=int, default=3)
    general.add_argument("--max-selective-reviews", type=int, default=1)
    general.add_argument("--max-cycles", type=int, default=12)
    general.add_argument(
        "--question-policy",
        choices=("clarifytrial", "fixed_order"),
        default="clarifytrial",
    )
    general.add_argument("--use-model-coordinator", action="store_true")
    general.add_argument("--no-batch-judgments", action="store_true")
    general.add_argument("--confirm-model-run", action="store_true")

    natural = commands.add_parser(
        "run-natural-screening",
        help=(
            "search and structure natural text, then run the multi-trial workflow "
            "with a separate synthetic answer file"
        ),
    )
    natural.add_argument("--request", required=True, type=Path)
    natural.add_argument(
        "--candidate-search",
        choices=("trialgpt", "local-bm25"),
        default="trialgpt",
    )
    natural.add_argument("--trial-sources", type=Path)
    natural.add_argument("--trialgpt-corpus", type=Path)
    natural.add_argument("--trialgpt-cache", type=Path)
    natural.add_argument(
        "--trialgpt-corpus-name",
        choices=("trec_2021", "trec_2022"),
        default="trec_2022",
    )
    natural.add_argument("--retrieval-device", default="cuda")
    natural.add_argument("--bm25-only", action="store_true")
    natural.add_argument("--hidden-answers", required=True, type=Path)
    natural.add_argument("--output", required=True, type=Path)
    natural.add_argument(
        "--provider",
        choices=("codex-subscription", "anthropic"),
        default="codex-subscription",
    )
    natural.add_argument("--api-key-env-file", type=Path)
    natural.add_argument("--api-key-name", default="ANTHROPIC_API_KEY")
    natural.add_argument("--model-id", default="claude-sonnet-5")
    natural.add_argument("--max-output-tokens", type=int, default=8_192)
    natural.add_argument("--timeout-seconds", type=float, default=180)
    natural.add_argument("--max-external-actions", type=int, default=3)
    natural.add_argument("--max-selective-reviews", type=int, default=1)
    natural.add_argument("--max-cycles", type=int, default=12)
    natural.add_argument("--confirm-model-run", action="store_true")

    add_challenge_parser(commands)

    full_ui = commands.add_parser(
        "run-full-ui",
        help=(
            "show patient input, search across supplied public trials, role calls, "
            "questions, reassessment, and final results in one terminal view"
        ),
    )
    full_ui.add_argument(
        "--trial-set",
        type=Path,
        default=Path("data")
        / "public_protocol_benchmark_v1"
        / "trial_set.json",
    )
    full_ui.add_argument(
        "--patient-pairs",
        type=Path,
        default=Path("data")
        / "public_protocol_benchmark_v1"
        / "patient_pairs.json",
    )
    full_ui.add_argument(
        "--generation-config",
        type=Path,
        default=Path("configs") / "natural_evaluation_patient_generation_v2.json",
    )
    full_ui.add_argument(
        "--patient-id",
        default="source-chronic_pancreatitis-04",
    )
    full_ui.add_argument(
        "--broad-corpus",
        type=Path,
        help=(
            "optional public trial JSONL; when supplied, the screen starts "
            "with the currently enrolling subset of this larger search pool"
        ),
    )
    full_ui.add_argument("--broad-search-top-k", type=int, default=200)
    full_ui.add_argument(
        "--output",
        type=Path,
        default=Path("runs") / "full-ui",
    )
    full_ui.add_argument(
        "--provider",
        choices=("deterministic", "codex-subscription", "anthropic"),
        default="deterministic",
    )
    full_ui.add_argument("--model")
    full_ui.add_argument(
        "--effort",
        choices=sorted(ALLOWED_CODEX_EFFORTS),
        default=DEFAULT_CODEX_EFFORT,
    )
    full_ui.add_argument("--api-key-env-file", type=Path)
    full_ui.add_argument("--api-key-name", default="ANTHROPIC_API_KEY")
    full_ui.add_argument("--max-output-tokens", type=int, default=8_192)
    full_ui.add_argument("--timeout-seconds", type=float, default=300)
    full_ui.add_argument("--max-external-actions", type=int, default=3)
    full_ui.add_argument("--max-selective-reviews", type=int, default=1)
    full_ui.add_argument("--max-cycles", type=int, default=12)
    full_ui.add_argument(
        "--auto",
        action="store_true",
        help="apply each prepared synthetic answer without waiting for Enter",
    )
    full_ui.add_argument("--confirm-model-run", action="store_true")

    workflow_evaluation = commands.add_parser(
        "run-workflow-evaluation",
        help=(
            "run no-question, fixed-order, immediate-coverage, and "
            "ClarifyTrial arms through the same connected workflow"
        ),
    )
    workflow_evaluation.add_argument(
        "--trial-set",
        type=Path,
        default=Path("data")
        / "natural_evaluation_v1"
        / "preliminary_trial_set.json",
    )
    workflow_evaluation.add_argument(
        "--patient-pairs",
        type=Path,
        default=Path("data")
        / "natural_evaluation_v2"
        / "preliminary_patient_pairs.json",
    )
    workflow_evaluation.add_argument(
        "--generation-config",
        type=Path,
        default=Path("configs") / "natural_evaluation_patient_generation_v2.json",
    )
    workflow_evaluation.add_argument("--output", required=True, type=Path)
    workflow_evaluation.add_argument(
        "--split",
        choices=("development", "heldout"),
        default="heldout",
    )
    workflow_evaluation.add_argument("--patient-id", action="append", default=[])
    workflow_evaluation.add_argument("--limit", type=int)
    workflow_evaluation.add_argument(
        "--provider",
        choices=("deterministic", "codex-subscription", "anthropic"),
        default="deterministic",
    )
    workflow_evaluation.add_argument("--model")
    workflow_evaluation.add_argument(
        "--effort",
        choices=sorted(ALLOWED_CODEX_EFFORTS),
        default=DEFAULT_CODEX_EFFORT,
    )
    workflow_evaluation.add_argument("--api-key-env-file", type=Path)
    workflow_evaluation.add_argument("--api-key-name", default="ANTHROPIC_API_KEY")
    workflow_evaluation.add_argument("--max-output-tokens", type=int, default=8_192)
    workflow_evaluation.add_argument("--timeout-seconds", type=float, default=300)
    workflow_evaluation.add_argument("--action-budget", type=int, default=3)
    workflow_evaluation.add_argument(
        "--arm",
        action="append",
        choices=("no_questions", "fixed_order", "immediate_coverage", "clarifytrial"),
        default=[],
        help="run only the named comparison arm; repeat to select several",
    )
    workflow_evaluation.add_argument(
        "--budget-sweep",
        action="store_true",
        help="run the same evaluation for every information budget from zero to five",
    )
    workflow_evaluation.add_argument(
        "--broad-corpus",
        type=Path,
        help=(
            "optional 1,931-trial team snapshot; checks whether predeclared "
            "target trials remain connected through the enrolling subset"
        ),
    )
    workflow_evaluation.add_argument(
        "--broad-search-top-k",
        type=int,
        default=200,
        help="search depth used for the predeclared-target connectivity check",
    )
    workflow_evaluation.add_argument("--max-selective-reviews", type=int, default=1)
    workflow_evaluation.add_argument("--max-cycles", type=int, default=12)
    workflow_evaluation.add_argument(
        "--agent-architecture",
        choices=(
            "rules_only",
            "single_judge",
            "code_routed_agents",
            "full_agents_no_reviewer",
            "full_agents",
        ),
        default="rules_only",
    )
    workflow_evaluation.add_argument("--concurrency", type=int, default=1)
    workflow_evaluation.add_argument(
        "--include-unavailable-scenario",
        action="store_true",
        help=(
            "also hide one declared answer per patient and verify that the "
            "workflow moves on without repeating the failed request"
        ),
    )
    workflow_evaluation.add_argument(
        "--approve-synthetic-actions",
        action="store_true",
        help=(
            "approve patient-choice and clinician-authorization gates that "
            "are declared inside a fully synthetic evaluation dataset"
        ),
    )
    workflow_evaluation.add_argument(
        "--include-patient-choice-scenario",
        action="store_true",
        help=(
            "also run the same synthetic cases with new tests and additional "
            "visits declined"
        ),
    )
    workflow_evaluation.add_argument(
        "--resume",
        action="store_true",
        help="reuse completed patient and comparison runs in the output directory",
    )
    workflow_evaluation.add_argument("--confirm-model-run", action="store_true")

    schemas = commands.add_parser(
        "export-schemas",
        help="export the JSON contracts used by the workflow",
    )
    schemas.add_argument("--output", required=True, type=Path)

    prepare = commands.add_parser(
        "prepare-trialgpt",
        help="download and validate the public TrialGPT criterion annotations",
    )
    prepare.add_argument(
        "--cache",
        type=Path,
        default=Path(".research-cache") / "trialgpt",
    )
    prepare.add_argument("--force", action="store_true")

    prepare_public = commands.add_parser(
        "prepare-clinicaltrials-v5",
        help="download the 15 declared ClinicalTrials.gov source records",
    )
    prepare_public.add_argument(
        "--cache",
        type=Path,
        default=Path(".research-cache") / "clinicaltrials-v5",
    )
    prepare_public.add_argument("--force", action="store_true")

    prepare_team = commands.add_parser(
        "prepare-team-trials",
        help=(
            "download and validate the pinned 1,931-trial team snapshot, "
            "then report the currently enrolling subset"
        ),
    )
    prepare_team.add_argument(
        "--output",
        type=Path,
        default=Path(".research-cache") / "team-trials" / "trials.jsonl",
    )
    prepare_team.add_argument("--force", action="store_true")

    select_team_trials = commands.add_parser(
        "select-team-evaluation-trials",
        help=(
            "select 50 currently enrolling trials across ten disease groups "
            "for later detailed evaluation"
        ),
    )
    select_team_trials.add_argument("--trials", required=True, type=Path)
    select_team_trials.add_argument(
        "--config",
        type=Path,
        default=Path("configs") / "team_trial_expansion_v1.json",
    )
    select_team_trials.add_argument("--output", required=True, type=Path)

    prepare_natural_evaluation = commands.add_parser(
        "prepare-natural-evaluation-sources",
        help=(
            "select and freeze new ClinicalTrials.gov studies for two-person "
            "natural-input evaluation review"
        ),
    )
    prepare_natural_evaluation.add_argument(
        "--config",
        type=Path,
        default=Path("configs") / "natural_evaluation_source_selection_v1.json",
    )
    prepare_natural_evaluation.add_argument(
        "--cache",
        type=Path,
        default=Path(".research-cache") / "clinicaltrials-natural-evaluation-v1",
    )
    prepare_natural_evaluation.add_argument(
        "--review-output",
        type=Path,
        default=Path("data")
        / "natural_evaluation_v1"
        / "criterion_review.json",
    )
    prepare_natural_evaluation.add_argument("--force", action="store_true")
    prepare_natural_evaluation.add_argument(
        "--overwrite-review-output",
        action="store_true",
        help="replace blank review files; never use after human review has begun",
    )

    materialize_reserves = commands.add_parser(
        "materialize-natural-evaluation-reserves",
        help="rebuild reserve review rows from the already frozen source records",
    )
    materialize_reserves.add_argument(
        "--source",
        type=Path,
        default=Path("data") / "natural_evaluation_v1" / "criterion_review.json",
    )
    materialize_reserves.add_argument(
        "--cache",
        type=Path,
        default=Path(".research-cache") / "clinicaltrials-natural-evaluation-v1",
    )
    materialize_reserves.add_argument(
        "--selection-config",
        type=Path,
        default=Path("configs") / "natural_evaluation_source_selection_v1.json",
    )
    materialize_reserves.add_argument(
        "--output",
        type=Path,
        default=Path("data")
        / "natural_evaluation_v1"
        / "reserve_criterion_review.json",
    )
    materialize_reserves.add_argument(
        "--group",
        dest="group_ids",
        action="append",
        help="repeat to materialize only selected disease groups",
    )

    compare_natural_reviews = commands.add_parser(
        "compare-natural-evaluation-reviews",
        help="compare two independent criterion review sheets without resolving them",
    )
    compare_natural_reviews.add_argument(
        "--source",
        type=Path,
        default=Path("data")
        / "natural_evaluation_v1"
        / "criterion_review.json",
    )
    compare_natural_reviews.add_argument(
        "--reviewer-1",
        type=Path,
        default=Path("data") / "natural_evaluation_v1" / "reviewer_1.csv",
    )
    compare_natural_reviews.add_argument(
        "--reviewer-2",
        type=Path,
        default=Path("data") / "natural_evaluation_v1" / "reviewer_2.csv",
    )
    compare_natural_reviews.add_argument(
        "--output",
        type=Path,
        default=Path("data")
        / "natural_evaluation_v1"
        / "review_comparison.json",
    )

    ai_natural_review = commands.add_parser(
        "run-natural-evaluation-ai-review",
        help="make a two-pass preliminary AI review of the frozen source lines",
    )
    ai_natural_review.add_argument(
        "--source",
        type=Path,
        default=Path("data")
        / "natural_evaluation_v1"
        / "criterion_review.json",
    )
    ai_natural_review.add_argument(
        "--review-output",
        type=Path,
        default=Path("data")
        / "natural_evaluation_v1"
        / "ai_preliminary_review.json",
    )
    ai_natural_review.add_argument(
        "--gold-output",
        type=Path,
        default=Path("data")
        / "natural_evaluation_v1"
        / "ai_preliminary_gold.json",
    )
    ai_natural_review.add_argument(
        "--checkpoint-dir",
        type=Path,
        default=Path("runs") / "natural-evaluation-ai-review",
    )
    ai_natural_review.add_argument("--model", default="gpt-5.6-sol")
    ai_natural_review.add_argument(
        "--source-section",
        choices=("trials", "reserve_trials"),
        default="trials",
    )
    ai_natural_review.add_argument(
        "--group",
        dest="group_ids",
        action="append",
        help="repeat to review only selected disease groups",
    )
    ai_natural_review.add_argument(
        "--effort",
        choices=sorted(ALLOWED_CODEX_EFFORTS),
        default="max",
    )
    ai_natural_review.add_argument(
        "--concurrency", type=int, choices=(1, 2, 3), default=3
    )
    ai_natural_review.add_argument("--chunk-size", type=int, default=6)
    ai_natural_review.add_argument("--timeout-seconds", type=float, default=600)
    ai_natural_review.add_argument("--confirm-subscription-run", action="store_true")

    resolve_ai_natural_review = commands.add_parser(
        "resolve-natural-evaluation-ai-review",
        help="use maximum reasoning on a selected preliminary review subset",
    )
    resolve_ai_natural_review.add_argument(
        "--source",
        type=Path,
        default=Path("data")
        / "natural_evaluation_v1"
        / "criterion_review.json",
    )
    resolve_ai_natural_review.add_argument(
        "--base-review",
        type=Path,
        default=Path("data")
        / "natural_evaluation_v1"
        / "ai_preliminary_review.json",
    )
    resolve_ai_natural_review.add_argument(
        "--review-output",
        type=Path,
        default=Path("data")
        / "natural_evaluation_v1"
        / "ai_preliminary_review_max.json",
    )
    resolve_ai_natural_review.add_argument(
        "--gold-output",
        type=Path,
        default=Path("data")
        / "natural_evaluation_v1"
        / "ai_preliminary_gold_max.json",
    )
    resolve_ai_natural_review.add_argument(
        "--checkpoint-dir",
        type=Path,
        default=Path("runs") / "natural-evaluation-ai-review-max-resolution",
    )
    resolve_ai_natural_review.add_argument("--model", default="gpt-5.6-sol")
    resolve_ai_natural_review.add_argument(
        "--source-section",
        choices=("trials", "reserve_trials"),
        default=None,
    )
    resolve_ai_natural_review.add_argument(
        "--group",
        dest="group_ids",
        action="append",
        help="repeat to match the base review disease groups",
    )
    resolve_ai_natural_review.add_argument(
        "--effort",
        choices=sorted(ALLOWED_CODEX_EFFORTS),
        default="max",
    )
    resolve_ai_natural_review.add_argument(
        "--concurrency", type=int, choices=(1, 2, 3), default=3
    )
    resolve_ai_natural_review.add_argument("--chunk-size", type=int, default=3)
    resolve_ai_natural_review.add_argument(
        "--selection-mode",
        choices=("uncertain_or_medium", "included"),
        default="uncertain_or_medium",
        help="which source lines receive the maximum-effort audit",
    )
    resolve_ai_natural_review.add_argument(
        "--timeout-seconds", type=float, default=600
    )
    resolve_ai_natural_review.add_argument(
        "--max-retries", type=int, choices=(0, 1), default=1
    )
    resolve_ai_natural_review.add_argument(
        "--confirm-subscription-run", action="store_true"
    )

    conservative_ai_gold = commands.add_parser(
        "build-natural-evaluation-conservative-gold",
        help="keep only high-confidence, source-validated AI criterion labels",
    )
    conservative_ai_gold.add_argument(
        "--source",
        type=Path,
        default=Path("data")
        / "natural_evaluation_v1"
        / "criterion_review.json",
    )
    conservative_ai_gold.add_argument(
        "--tiered-review",
        type=Path,
        default=Path("data")
        / "natural_evaluation_v1"
        / "ai_preliminary_review_polarity_audited.json",
    )
    conservative_ai_gold.add_argument(
        "--selection-config",
        type=Path,
        default=Path("configs")
        / "natural_evaluation_source_selection_v1.json",
    )
    conservative_ai_gold.add_argument(
        "--source-section",
        choices=("trials", "reserve_trials"),
        default=None,
    )
    conservative_ai_gold.add_argument(
        "--group",
        dest="group_ids",
        action="append",
        help="repeat to match the reviewed disease groups",
    )
    conservative_ai_gold.add_argument(
        "--output",
        type=Path,
        default=Path("data")
        / "natural_evaluation_v1"
        / "ai_preliminary_gold_conservative.json",
    )

    final_natural_trial_set = commands.add_parser(
        "build-natural-evaluation-trial-set",
        help="replace low-coverage primaries with qualifying frozen reserves",
    )
    final_natural_trial_set.add_argument(
        "--primary-source",
        type=Path,
        default=Path("data") / "natural_evaluation_v1" / "criterion_review.json",
    )
    final_natural_trial_set.add_argument(
        "--reserve-source",
        type=Path,
        default=Path("data")
        / "natural_evaluation_v1"
        / "reserve_criterion_review.json",
    )
    final_natural_trial_set.add_argument(
        "--primary-gold",
        type=Path,
        default=Path("data")
        / "natural_evaluation_v1"
        / "ai_preliminary_gold_conservative.json",
    )
    final_natural_trial_set.add_argument(
        "--reserve-gold",
        type=Path,
        default=Path("data")
        / "natural_evaluation_v1"
        / "ai_preliminary_reserve_gold_conservative.json",
    )
    final_natural_trial_set.add_argument(
        "--selection-config",
        type=Path,
        default=Path("configs") / "natural_evaluation_source_selection_v1.json",
    )
    final_natural_trial_set.add_argument(
        "--output",
        type=Path,
        default=Path("data")
        / "natural_evaluation_v1"
        / "preliminary_trial_set.json",
    )

    natural_patient_pairs = commands.add_parser(
        "build-natural-evaluation-patient-pairs",
        help="generate paired synthetic patients from the frozen trial set",
    )
    natural_patient_pairs.add_argument(
        "--trial-set",
        type=Path,
        default=Path("data")
        / "natural_evaluation_v1"
        / "preliminary_trial_set.json",
    )
    natural_patient_pairs.add_argument(
        "--generation-config",
        type=Path,
        default=Path("configs")
        / "natural_evaluation_patient_generation_v1.json",
    )
    natural_patient_pairs.add_argument(
        "--output",
        type=Path,
        default=Path("data")
        / "natural_evaluation_v1"
        / "preliminary_patient_pairs.json",
    )

    audit_natural_pairs = commands.add_parser(
        "audit-natural-evaluation-patient-pairs",
        help="recompute every paired synthetic evaluation episode",
    )
    audit_natural_pairs.add_argument(
        "--trial-set",
        type=Path,
        default=Path("data")
        / "natural_evaluation_v1"
        / "preliminary_trial_set.json",
    )
    audit_natural_pairs.add_argument(
        "--generation-config",
        type=Path,
        default=Path("configs")
        / "natural_evaluation_patient_generation_v1.json",
    )
    audit_natural_pairs.add_argument(
        "--patient-pairs",
        type=Path,
        default=Path("data")
        / "natural_evaluation_v1"
        / "preliminary_patient_pairs.json",
    )

    broad_rescue = commands.add_parser(
        "build-broad-rescue-dataset",
        help=(
            "build ten-disease synthetic patients with both recoverable and "
            "ineligible outcomes for workflow maturity testing"
        ),
    )
    broad_rescue.add_argument(
        "--config",
        type=Path,
        default=Path("configs") / "broad_rescue_maturity_v1.json",
    )
    broad_rescue.add_argument(
        "--output",
        type=Path,
        default=Path("data") / "broad_rescue_maturity_v1",
    )

    audit_broad_rescue = commands.add_parser(
        "audit-broad-rescue-dataset",
        help="rebuild and compare every declared broad synthetic case",
    )
    audit_broad_rescue.add_argument(
        "--config",
        type=Path,
        default=Path("configs") / "broad_rescue_maturity_v1.json",
    )
    audit_broad_rescue.add_argument(
        "--trial-set",
        type=Path,
        default=Path("data") / "broad_rescue_maturity_v1" / "trial_set.json",
    )
    audit_broad_rescue.add_argument(
        "--patient-pairs",
        type=Path,
        default=Path("data") / "broad_rescue_maturity_v1" / "patient_pairs.json",
    )

    source_benchmark = commands.add_parser(
        "build-public-protocol-benchmark",
        help=(
            "build synthetic patient episodes against a conservative subset "
            "of 50 selected public trial protocols"
        ),
    )
    source_benchmark.add_argument(
        "--config",
        type=Path,
        default=Path("configs") / "public_protocol_benchmark_v1.json",
    )
    source_benchmark.add_argument(
        "--selection",
        type=Path,
        default=Path("runs") / "team-trial-expansion" / "selection.json",
    )
    source_benchmark.add_argument(
        "--trials",
        type=Path,
        default=Path(".research-cache") / "team-trials" / "trials.jsonl",
    )
    source_benchmark.add_argument(
        "--output",
        type=Path,
        default=Path("data") / "public_protocol_benchmark_v1",
    )

    audit_source = commands.add_parser(
        "audit-public-protocol-benchmark",
        help="rebuild and compare the public-protocol subset and synthetic patients",
    )
    audit_source.add_argument(
        "--config",
        type=Path,
        default=Path("configs") / "public_protocol_benchmark_v1.json",
    )
    audit_source.add_argument(
        "--selection",
        type=Path,
        default=Path("runs") / "team-trial-expansion" / "selection.json",
    )
    audit_source.add_argument(
        "--trials",
        type=Path,
        default=Path(".research-cache") / "team-trials" / "trials.jsonl",
    )
    audit_source.add_argument(
        "--trial-set",
        type=Path,
        default=Path("data") / "public_protocol_benchmark_v1" / "trial_set.json",
    )
    audit_source.add_argument(
        "--patient-pairs",
        type=Path,
        default=Path("data")
        / "public_protocol_benchmark_v1"
        / "patient_pairs.json",
    )

    natural_records = commands.add_parser(
        "build-natural-evaluation-records",
        help="render the paired facts as synthetic record entries and prose",
    )
    natural_records.add_argument(
        "--patient-pairs",
        type=Path,
        default=Path("data")
        / "natural_evaluation_v1"
        / "preliminary_patient_pairs.json",
    )
    natural_records.add_argument(
        "--output",
        type=Path,
        default=Path("data")
        / "natural_evaluation_v1"
        / "preliminary_natural_records.json",
    )

    audit_natural_records = commands.add_parser(
        "audit-natural-evaluation-records",
        help="verify that rendered records preserve every paired clinical value",
    )
    audit_natural_records.add_argument(
        "--patient-pairs",
        type=Path,
        default=Path("data")
        / "natural_evaluation_v1"
        / "preliminary_patient_pairs.json",
    )
    audit_natural_records.add_argument(
        "--records",
        type=Path,
        default=Path("data")
        / "natural_evaluation_v1"
        / "preliminary_natural_records.json",
    )

    structure_natural_records = commands.add_parser(
        "run-natural-record-structure-evaluation",
        help="measure whether Sol reads values and evidence state from synthetic records",
    )
    structure_natural_records.add_argument(
        "--records",
        type=Path,
        default=Path("data")
        / "natural_evaluation_v1"
        / "preliminary_natural_records.json",
    )
    structure_natural_records.add_argument("--output", type=Path, required=True)
    structure_natural_records.add_argument(
        "--split", choices=("development", "heldout", "all"), default="all"
    )
    structure_natural_records.add_argument(
        "--evidence-state",
        choices=("sufficient", "insufficient", "all"),
        default="all",
    )
    structure_natural_records.add_argument("--model", default="gpt-5.6-sol")
    structure_natural_records.add_argument(
        "--effort", choices=sorted(ALLOWED_CODEX_EFFORTS), default="medium"
    )
    structure_natural_records.add_argument(
        "--concurrency", type=int, choices=(1, 2, 3), default=3
    )
    structure_natural_records.add_argument("--timeout-seconds", type=float, default=300)
    structure_natural_records.add_argument(
        "--confirm-subscription-run", action="store_true"
    )

    natural_policy = commands.add_parser(
        "run-json-question-policy-evaluation",
        aliases=["run-natural-question-policy-evaluation"],
        help=(
            "compare question order on standardized JSON; natural-record "
            "structure results are optional"
        ),
    )
    natural_policy.add_argument(
        "--trial-set",
        type=Path,
        default=Path("data")
        / "natural_evaluation_v1"
        / "preliminary_trial_set.json",
    )
    natural_policy.add_argument(
        "--generation-config",
        type=Path,
        default=Path("configs") / "natural_evaluation_patient_generation_v2.json",
    )
    natural_policy.add_argument(
        "--patient-pairs",
        type=Path,
        default=Path("data")
        / "natural_evaluation_v2"
        / "preliminary_patient_pairs.json",
    )
    natural_policy.add_argument(
        "--records",
        type=Path,
        default=Path("data")
        / "natural_evaluation_v2"
        / "preliminary_natural_records.json",
    )
    natural_policy.add_argument(
        "--structure-result",
        type=Path,
        action="append",
        default=[],
        help="optional natural-record extraction result; omit for JSON-only evaluation",
    )
    natural_policy.add_argument(
        "--split",
        choices=("development", "heldout"),
        action="append",
        help="evaluate only the selected split; may be repeated",
    )
    natural_policy.add_argument(
        "--patient-id",
        action="append",
        help="evaluate only the selected patient ID; may be repeated",
    )
    natural_policy.add_argument("--output", type=Path, required=True)
    natural_policy.add_argument("--action-budget", type=int, default=3)
    natural_policy.add_argument(
        "--budget-sweep",
        action="store_true",
        help="also evaluate every question budget from zero through five",
    )
    natural_policy.add_argument(
        "--include-fully-missing",
        action="store_true",
        help="also remove all five pivotal values from the initial patient input",
    )

    text_demo = commands.add_parser(
        "run-text-demo",
        help="show one synthetic question and re-assessment process in the terminal",
    )
    text_demo.add_argument(
        "--trial-set",
        type=Path,
        default=Path("data")
        / "natural_evaluation_v1"
        / "preliminary_trial_set.json",
    )
    text_demo.add_argument(
        "--generation-config",
        type=Path,
        default=Path("configs") / "natural_evaluation_patient_generation_v2.json",
    )
    text_demo.add_argument(
        "--patient-pairs",
        type=Path,
        default=Path("data")
        / "natural_evaluation_v2"
        / "preliminary_patient_pairs.json",
    )
    text_demo.add_argument(
        "--records",
        type=Path,
        default=Path("data")
        / "natural_evaluation_v2"
        / "preliminary_natural_records.json",
    )
    text_demo.add_argument(
        "--patient-id",
        default="natural-breast_cancer-11",
    )
    text_demo.add_argument(
        "--input-state",
        choices=("partly-known", "fully-missing"),
        default="fully-missing",
    )
    text_demo.add_argument("--action-budget", type=int, default=3)
    text_demo.add_argument(
        "--output",
        type=Path,
        default=Path("runs") / "text-ui-demo.json",
    )
    text_demo.add_argument(
        "--auto",
        action="store_true",
        help="show every step without waiting for Enter",
    )

    pilot = commands.add_parser(
        "run-trialgpt-pilot",
        help="run a bounded live Sonnet cost pilot on TrialGPT pairs",
    )
    pilot.add_argument("--raw-jsonl", required=True, type=Path)
    pilot.add_argument("--sigir-corpus", required=True, type=Path)
    pilot.add_argument("--output", required=True, type=Path)
    pilot.add_argument("--api-key-env-file", required=True, type=Path)
    pilot.add_argument("--api-key-name", default="API_KEY")
    pilot.add_argument("--limit", type=int, default=20)
    pilot.add_argument("--seed", type=int, default=20_260_820)
    pilot.add_argument("--model", default="claude-sonnet-5")
    pilot.add_argument("--max-output-tokens", type=int, default=8_192)
    pilot.add_argument("--timeout-seconds", type=float, default=120)
    pilot.add_argument("--confirm-live-api", action="store_true")

    experiment = commands.add_parser(
        "run-trialgpt-experiment",
        help="compare one declared TrialGPT prompt policy on a fixed data split",
    )
    experiment.add_argument("--raw-jsonl", required=True, type=Path)
    experiment.add_argument("--sigir-corpus", required=True, type=Path)
    experiment.add_argument("--output", required=True, type=Path)
    experiment.add_argument("--api-key-env-file", required=True, type=Path)
    experiment.add_argument("--api-key-name", default="API_KEY")
    experiment.add_argument(
        "--variant",
        choices=sorted(_TRIALGPT_VARIANTS),
        required=True,
    )
    experiment.add_argument(
        "--split",
        dest="split_name",
        choices=("development", "heldout", "overlap", "all"),
        default="development",
    )
    experiment.add_argument("--seed", type=int, default=20_260_820)
    experiment.add_argument("--limit", type=int)
    experiment.add_argument("--model", default="claude-sonnet-5")
    experiment.add_argument("--max-output-tokens", type=int, default=8_192)
    experiment.add_argument("--timeout-seconds", type=float, default=120)
    experiment.add_argument("--confirm-live-api", action="store_true")

    architecture = commands.add_parser(
        "run-trialgpt-architecture",
        help="compare S1, M1, and M2 with ChatGPT subscription Sol medium",
    )
    architecture.add_argument("--raw-jsonl", required=True, type=Path)
    architecture.add_argument("--sigir-corpus", required=True, type=Path)
    architecture.add_argument("--output", required=True, type=Path)
    architecture.add_argument(
        "--stage",
        choices=("smoke", "dev", "main", "overlap"),
        required=True,
    )
    architecture.add_argument("--experiment-id")
    architecture.add_argument("--split-seed", type=int, default=20_260_820)
    architecture.add_argument("--order-seed", type=int, default=20_260_821)
    architecture.add_argument("--retrieval-top-k", type=int, default=5)
    architecture.add_argument("--pause-at-percent", type=float, default=80.0)
    architecture.add_argument("--timeout-seconds", type=float, default=180.0)
    architecture.add_argument(
        "--case-concurrency",
        type=int,
        choices=(1, 2, 3),
        default=3,
        help="number of patient-trial cases to run concurrently (max 3)",
    )
    architecture.add_argument("--confirm-subscription-run", action="store_true")

    strong_review = commands.add_parser(
        "run-trialgpt-strong-review",
        help="compare strong single judgment with no-web and web review",
    )
    strong_review.add_argument("--raw-jsonl", required=True, type=Path)
    strong_review.add_argument("--sigir-corpus", required=True, type=Path)
    strong_review.add_argument("--output", required=True, type=Path)
    strong_review.add_argument(
        "--stage",
        choices=("development", "heldout", "overlap"),
        default="development",
    )
    strong_review.add_argument("--limit", type=int)
    strong_review.add_argument("--retrieval-top-k", type=int, default=5)
    strong_review.add_argument("--timeout-seconds", type=float, default=240.0)
    strong_review.add_argument(
        "--case-concurrency", type=int, choices=(1, 2, 3), default=3
    )
    strong_review.add_argument("--confirm-subscription-run", action="store_true")

    retrieval = commands.add_parser(
        "run-trialgpt-retrieval",
        help="reproduce TrialGPT BM25 and MedCPT candidate retrieval",
    )
    retrieval.add_argument("--dataset", required=True, type=Path)
    retrieval.add_argument("--cache", required=True, type=Path)
    retrieval.add_argument("--output", required=True, type=Path)
    retrieval.add_argument(
        "--corpus",
        choices=("trec_2021", "trec_2022"),
        required=True,
    )
    retrieval.add_argument("--query-type", default="gpt-4-turbo")
    retrieval.add_argument("--fusion-k", type=int, default=20)
    retrieval.add_argument("--search-depth", type=int, default=2_000)
    retrieval.add_argument("--bm25-weight", type=float, default=1.0)
    retrieval.add_argument("--medcpt-weight", type=float, default=1.0)
    retrieval.add_argument("--batch-size", type=int, default=16)
    retrieval.add_argument("--device", default="cuda")

    interactive = commands.add_parser(
        "run-interactive-pilot",
        help="run the 12-patient question and reassessment pilot",
    )
    interactive.add_argument("--output", required=True, type=Path)
    interactive.add_argument("--with-subscription-model", action="store_true")
    interactive.add_argument("--confirm-subscription-run", action="store_true")
    interactive.add_argument(
        "--case-concurrency", type=int, choices=(1, 2, 3), default=3
    )
    interactive.add_argument("--timeout-seconds", type=float, default=180.0)

    stress = commands.add_parser(
        "run-interactive-stress",
        help="compare clarification policies on many synthetic graph structures",
    )
    stress.add_argument("--output", required=True, type=Path)
    stress.add_argument("--structures-per-topology", type=int, default=100)
    stress.add_argument("--seed", type=int, default=20_260_821)

    public_benchmark = commands.add_parser(
        "run-public-interactive-benchmark",
        help="run the 30-patient public-criterion synthetic benchmark",
    )
    public_benchmark.add_argument(
        "--config",
        type=Path,
        default=Path("configs") / "interactive_public_benchmark_v1.json",
    )
    public_benchmark.add_argument("--source-cache", required=True, type=Path)
    public_benchmark.add_argument("--output", required=True, type=Path)
    public_benchmark.add_argument("--seed", type=int, default=20_260_821)
    public_benchmark.add_argument(
        "--action-budget", type=int, choices=(1, 2, 3), default=3
    )

    public_grid = commands.add_parser(
        "run-public-grid-stress",
        help="exhaust the declared public-criterion value grid",
    )
    public_grid.add_argument(
        "--config",
        type=Path,
        default=Path("configs") / "interactive_public_benchmark_v1.json",
    )
    public_grid.add_argument("--source-cache", required=True, type=Path)
    public_grid.add_argument("--output", required=True, type=Path)
    public_grid.add_argument(
        "--action-budget", type=int, choices=(1, 2, 3), default=3
    )

    burden = commands.add_parser(
        "run-public-burden-benchmark",
        help="compare patient-specific acquisition burden on 360 settings",
    )
    burden.add_argument(
        "--config",
        type=Path,
        default=Path("configs") / "interactive_public_benchmark_v1.json",
    )
    burden.add_argument("--source-cache", required=True, type=Path)
    burden.add_argument("--output", required=True, type=Path)
    burden.add_argument(
        "--action-budget", type=int, choices=(3,), default=3
    )

    report = commands.add_parser(
        "build-report",
        help="build Markdown, CSV, and SVG figures from evaluation JSON",
    )
    report.add_argument("--question-policy", type=Path)
    report.add_argument("--burden", type=Path)
    report.add_argument("--workflow", type=Path)
    report.add_argument("--budget-frontier", type=Path)
    report.add_argument("--retrieval", type=Path, action="append", default=[])
    report.add_argument("--output", type=Path, required=True)
    report.add_argument("--split", default="heldout")
    report.add_argument("--input-state", default="fully_missing")
    report.add_argument("--action-budget", type=int, default=3)

    architecture_comparison = commands.add_parser(
        "compare-agent-architectures",
        help="compare model-call structures on the same patients and trials",
    )
    architecture_comparison.add_argument(
        "--workflow", type=Path, action="append", required=True
    )
    architecture_comparison.add_argument(
        "--arm",
        choices=("no_questions", "fixed_order", "immediate_coverage", "clarifytrial"),
        default="clarifytrial",
    )
    architecture_comparison.add_argument("--output", type=Path, required=True)

    readiness = commands.add_parser(
        "audit-final-evaluation-readiness",
        aliases=["audit-external-evaluation-readiness"],
        help=(
            "check synthetic breadth, connected rescue behavior, failure "
            "fallback, public criteria, broad search, and model execution"
        ),
    )
    readiness.add_argument("--trial-set", required=True, type=Path)
    readiness.add_argument("--patient-pairs", required=True, type=Path)
    readiness.add_argument("--workflow", required=True, type=Path)
    readiness.add_argument("--output", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    _configure_utf8_stdio()
    parser = _parser()
    args = parser.parse_args(argv)
    if args.command == "run-example":
        result_path = run_example(args.case, args.output)
        print(f"result: {result_path}")
        print(f"trace: {args.output / 'trace.jsonl'}")
        return 0
    if args.command == "run-screening":
        if (
            args.retry_unavailable
            or args.approve_patient_choice
            or args.authorize_clinician
        ) and args.resume is None:
            parser.error(
                "retry and approval flags require --resume with a saved session"
            )
        live_provider = args.provider != "deterministic"
        if live_provider and not args.confirm_model_run:
            parser.error(
                "live run-screening providers require --confirm-model-run"
            )
        settings = EpisodeSettings(
            max_external_actions=args.max_external_actions,
            max_selective_reviews=args.max_selective_reviews,
            max_cycles=args.max_cycles,
            use_model_coordinator=args.use_model_coordinator,
            batch_trial_judgments=not args.no_batch_judgments,
            question_policy=args.question_policy,
        )
        candidate_search = None
        if args.candidate_search == "trialgpt" and args.resume is None:
            candidate_search = _build_trialgpt_candidate_search(args, parser)
        options = GeneralRunOptions(
            patient_path=args.patient,
            trials_path=args.trials,
            output_dir=args.output,
            settings=settings,
            answers_path=args.answers,
            resume_path=args.resume,
            retry_unavailable=args.retry_unavailable,
            approve_patient_choice=args.approve_patient_choice,
            authorize_clinician=args.authorize_clinician,
            candidate_search=candidate_search,
            candidate_search_depth=args.retrieval_search_depth,
        )
        if args.provider == "deterministic":
            outcome = run_general_screening(
                options=options,
                model=DeterministicWorkflowModel(),
                model_label="deterministic-workflow",
                medical_disclaimer=_read_disclaimer(),
            )
        elif args.provider == "codex-subscription":
            model_id = args.model or DEFAULT_CODEX_MODEL
            with CodexSubscriptionStructuredModel(
                model_id=model_id,
                effort=args.effort,
                timeout_seconds=args.timeout_seconds,
            ) as model:
                outcome = run_general_screening(
                    options=options,
                    model=model,
                    model_label=f"{model_id} / {args.effort}",
                    medical_disclaimer=_read_disclaimer(),
                )
        else:
            if args.api_key_env_file is None:
                parser.error("anthropic provider requires --api-key-env-file")
            model_id = args.model or "claude-sonnet-5"
            model = AnthropicStructuredModel(
                api_key=_read_env_value(args.api_key_env_file, args.api_key_name),
                model_id=model_id,
                max_output_tokens=args.max_output_tokens,
                timeout_seconds=args.timeout_seconds,
            )
            outcome = run_general_screening(
                options=options,
                model=model,
                model_label=model_id,
                medical_disclaimer=_read_disclaimer(),
            )
        return 0 if outcome.result_path is not None or outcome.paused else 2
    if args.command == "run-natural-screening":
        if not args.confirm_model_run:
            parser.error(
                "run-natural-screening requires --confirm-model-run because it "
                "makes live model calls"
            )
        settings = EpisodeSettings(
            max_external_actions=args.max_external_actions,
            max_selective_reviews=args.max_selective_reviews,
            max_cycles=args.max_cycles,
        )
        if args.candidate_search == "local-bm25":
            if args.trial_sources is None:
                parser.error("local-bm25 requires --trial-sources")
            source_rows = _read_json(args.trial_sources)
            if not isinstance(source_rows, list):
                parser.error("--trial-sources must contain a JSON list")
            candidate_search: CandidateSearch = InMemoryCandidateSearch(
                [TrialProtocolSource.model_validate(item) for item in source_rows]
            )
        else:
            candidate_search = _build_trialgpt_candidate_search(args, parser)
        if args.provider == "codex-subscription":
            with CodexSubscriptionStructuredModel(
                timeout_seconds=args.timeout_seconds,
            ) as model:
                result_path = run_natural_screening_from_files(
                    request_path=args.request,
                    hidden_answers_path=args.hidden_answers,
                    output_dir=args.output,
                    model=model,
                    candidate_search=candidate_search,
                    episode_settings=settings,
                )
        else:
            if args.api_key_env_file is None:
                parser.error("anthropic provider requires --api-key-env-file")
            model = AnthropicStructuredModel(
                api_key=_read_env_value(
                    args.api_key_env_file,
                    args.api_key_name,
                ),
                model_id=args.model_id,
                max_output_tokens=args.max_output_tokens,
                timeout_seconds=args.timeout_seconds,
            )
            result_path = run_natural_screening_from_files(
                request_path=args.request,
                hidden_answers_path=args.hidden_answers,
                output_dir=args.output,
                model=model,
                candidate_search=candidate_search,
                episode_settings=settings,
            )
        print(f"result: {result_path}")
        print(f"trace: {args.output / 'trace.jsonl'}")
        return 0
    if args.command == "run-challenge":
        return run_challenge_command(
            args,
            parser,
            dependencies=ChallengeCliDependencies(
                read_json=_read_json,
                read_disclaimer=_read_disclaimer,
                read_env_value=_read_env_value,
                build_trialgpt_candidate_search=(
                    _build_trialgpt_candidate_search
                ),
            ),
        )
    if args.command == "run-full-ui":
        if args.provider != "deterministic" and not args.confirm_model_run:
            parser.error(
                "live run-full-ui providers require --confirm-model-run"
            )
        fixture = build_integrated_ui_fixture(
            trial_set_path=args.trial_set,
            patient_pairs_path=args.patient_pairs,
            generation_config_path=args.generation_config,
            patient_id=args.patient_id,
            broad_corpus_path=args.broad_corpus,
            broad_search_top_k=args.broad_search_top_k,
        )
        settings = EpisodeSettings(
            max_external_actions=args.max_external_actions,
            max_selective_reviews=args.max_selective_reviews,
            max_cycles=args.max_cycles,
        )
        if args.provider == "deterministic":
            run_integrated_terminal_ui(
                fixture=fixture,
                model=DeterministicWorkflowModel(),
                model_label="deterministic-workflow",
                settings=settings,
                output_dir=args.output,
                medical_disclaimer=_read_disclaimer(),
                auto_advance=args.auto,
            )
        elif args.provider == "codex-subscription":
            model_id = args.model or DEFAULT_CODEX_MODEL
            with CodexSubscriptionStructuredModel(
                model_id=model_id,
                effort=args.effort,
                timeout_seconds=args.timeout_seconds,
            ) as model:
                run_integrated_terminal_ui(
                    fixture=fixture,
                    model=model,
                    model_label=f"{model_id} / {args.effort}",
                    settings=settings,
                    output_dir=args.output,
                    medical_disclaimer=_read_disclaimer(),
                    auto_advance=args.auto,
                )
        else:
            if args.api_key_env_file is None:
                parser.error("anthropic provider requires --api-key-env-file")
            model_id = args.model or "claude-sonnet-5"
            model = AnthropicStructuredModel(
                api_key=_read_env_value(
                    args.api_key_env_file,
                    args.api_key_name,
                ),
                model_id=model_id,
                max_output_tokens=args.max_output_tokens,
                timeout_seconds=args.timeout_seconds,
            )
            run_integrated_terminal_ui(
                fixture=fixture,
                model=model,
                model_label=model_id,
                settings=settings,
                output_dir=args.output,
                medical_disclaimer=_read_disclaimer(),
                auto_advance=args.auto,
            )
        return 0
    if args.command == "run-workflow-evaluation":
        live_provider = args.provider != "deterministic"
        if live_provider and not args.confirm_model_run:
            parser.error(
                "live run-workflow-evaluation providers require "
                "--confirm-model-run"
            )
        run_kwargs = {
            "trial_set_path": args.trial_set,
            "patient_pairs_path": args.patient_pairs,
            "generation_config_path": args.generation_config,
            "split": args.split,
            "patient_ids": args.patient_id,
            "limit": args.limit,
            "arms": args.arm or (
                "no_questions",
                "fixed_order",
                "immediate_coverage",
                "clarifytrial",
            ),
            "max_selective_reviews": args.max_selective_reviews,
            "max_cycles": args.max_cycles,
            "agent_architecture": args.agent_architecture,
            "concurrency": args.concurrency,
            "include_unavailable_scenario": args.include_unavailable_scenario,
            "include_patient_choice_scenario": (
                args.include_patient_choice_scenario
            ),
            "approve_synthetic_actions": args.approve_synthetic_actions,
            "broad_corpus_path": args.broad_corpus,
            "broad_search_top_k": args.broad_search_top_k,
            "resume": args.resume,
        }
        def run_declared_budgets(model: StructuredModel, model_label: str) -> dict[str, Any]:
            if not args.budget_sweep:
                return run_full_workflow_evaluation(
                    **run_kwargs,
                    destination=args.output,
                    action_budget=args.action_budget,
                    model=model,
                    model_label=model_label,
                )
            summary_paths = []
            latest = None
            for budget in range(6):
                budget_output = args.output / f"budget-{budget}"
                latest = run_full_workflow_evaluation(
                    **run_kwargs,
                    destination=budget_output,
                    action_budget=budget,
                    model=model,
                    model_label=model_label,
                )
                summary_paths.append(budget_output / "summary.json")
            frontier = build_budget_frontier(
                workflow_summary_paths=summary_paths,
                output_dir=args.output / "frontier",
            )
            assert latest is not None
            return {**latest, "budget_frontier": frontier}

        if args.provider == "deterministic":
            summary = run_declared_budgets(
                DeterministicWorkflowModel(),
                "deterministic-workflow",
            )
        elif args.provider == "codex-subscription":
            model_id = args.model or DEFAULT_CODEX_MODEL
            with CodexSubscriptionModelPool(
                size=args.concurrency,
                worker_factory=lambda: CodexSubscriptionStructuredModel(
                    model_id=model_id,
                    effort=args.effort,
                    timeout_seconds=args.timeout_seconds,
                ),
            ) as model:
                summary = run_declared_budgets(
                    model,
                    f"{model_id} / {args.effort}",
                )
        else:
            if args.concurrency != 1:
                parser.error("anthropic workflow evaluation currently uses concurrency=1")
            if args.api_key_env_file is None:
                parser.error("anthropic provider requires --api-key-env-file")
            model_id = args.model or "claude-sonnet-5"
            model = AnthropicStructuredModel(
                api_key=_read_env_value(args.api_key_env_file, args.api_key_name),
                model_id=model_id,
                max_output_tokens=args.max_output_tokens,
                timeout_seconds=args.timeout_seconds,
            )
            summary = run_declared_budgets(model, model_id)
        if args.budget_sweep:
            print(f"frontier: {args.output / 'frontier' / 'frontier.md'}")
        else:
            print(f"summary: {args.output / 'summary.json'}")
        paired = summary.get("paired_clarifytrial_vs_fixed")
        print(
            f"patients={summary['patient_count']} "
            f"paired={0 if paired is None else paired['patient_count']}"
        )
        return 0
    if args.command == "export-schemas":
        for path in export_schemas(args.output):
            print(path)
        return 0
    if args.command == "prepare-trialgpt":
        prepared = prepare_trialgpt(args.cache, force=args.force)
        print(f"raw: {prepared['raw_jsonl']}")
        print(f"metadata: {prepared['source_metadata']}")
        print(json.dumps(prepared["statistics"], ensure_ascii=False))
        return 0
    if args.command == "prepare-clinicaltrials-v5":
        metadata = fetch_clinicaltrials_v5_sources(args.cache, force=args.force)
        print(f"metadata: {args.cache / 'source_metadata.json'}")
        print(
            f"studies: {metadata['study_count']} "
            f"data_timestamp={metadata['data_timestamp']}"
        )
        return 0
    if args.command == "prepare-team-trials":
        corpus_path, metadata_path = prepare_team_trial_corpus(
            args.output,
            force=args.force,
        )
        summary = inspect_team_trial_corpus(corpus_path)
        print(f"trials: {corpus_path}")
        print(f"metadata: {metadata_path}")
        print(
            f"all_trials={summary.row_count} "
            f"currently_enrolling={summary.included_trial_count}"
        )
        return 0
    if args.command == "select-team-evaluation-trials":
        result = select_team_evaluation_trials(
            corpus_path=args.trials,
            config_path=args.config,
            destination=args.output,
        )
        print(f"selection: {args.output}")
        print(
            f"groups={result['group_count']} "
            f"trials={result['selected_trial_count']}"
        )
        return 0
    if args.command == "prepare-natural-evaluation-sources":
        prepared = prepare_natural_evaluation_sources(
            args.config,
            args.cache,
            args.review_output,
            force=args.force,
            overwrite_review=args.overwrite_review_output,
        )
        print(f"metadata: {prepared['metadata_path']}")
        print(f"review: {prepared['review_output_path']}")
        print(f"reviewer_1: {prepared['reviewer_1_path']}")
        print(f"reviewer_2: {prepared['reviewer_2_path']}")
        print(
            "studies: "
            f"primary={prepared['primary_study_count']} "
            f"reserve={prepared['reserve_study_count']} "
            "objective_candidates="
            f"{prepared['primary_objective_candidate_count']} "
            "review_rows="
            f"{prepared['primary_review_candidate_count']}"
        )
        print(f"source_audit_passed: {prepared['audit']['passed']}")
        return 0
    if args.command == "materialize-natural-evaluation-reserves":
        result = materialize_natural_evaluation_reserve_sources(
            review_path=args.source,
            cache_dir=args.cache,
            selection_config_path=args.selection_config,
            output_path=args.output,
            group_ids=args.group_ids,
        )
        print(f"reserve_review_source: {result['output']}")
        print(f"reserve_reviewer_1: {result['reviewer_1']}")
        print(f"reserve_reviewer_2: {result['reviewer_2']}")
        print(
            f"studies={result['reserve_study_count']} "
            f"review_rows={result['review_candidate_count']}"
        )
        return 0
    if args.command == "compare-natural-evaluation-reviews":
        comparison = compare_natural_evaluation_reviews(
            args.source,
            args.reviewer_1,
            args.reviewer_2,
            args.output,
        )
        print(f"comparison: {args.output}")
        print(
            f"status={comparison['status']} "
            f"agreed={comparison['agreement_count']} "
            f"disagreed={comparison['disagreement_count']} "
            f"incomplete={comparison['incomplete_count']}"
        )
        return 0
    if args.command == "run-natural-evaluation-ai-review":
        if not args.confirm_subscription_run:
            parser.error(
                "run-natural-evaluation-ai-review requires "
                "--confirm-subscription-run"
            )
        with CodexSubscriptionModelPool(
            size=args.concurrency,
            worker_factory=lambda: CodexSubscriptionStructuredModel(
                model_id=args.model,
                effort=args.effort,
                timeout_seconds=args.timeout_seconds,
            ),
        ) as model:
            result = run_natural_evaluation_ai_review(
                source_path=args.source,
                review_output_path=args.review_output,
                gold_output_path=args.gold_output,
                checkpoint_dir=args.checkpoint_dir,
                model=model,
                model_id=args.model,
                effort=args.effort,
                source_section=args.source_section,
                group_ids=args.group_ids,
                concurrency=args.concurrency,
                chunk_size=args.chunk_size,
                progress=print,
            )
        print(f"review: {result['review_output']}")
        print(f"preliminary_gold: {result['gold_output']}")
        print(
            f"source_lines={result['source_line_count']} "
            f"criteria={result['criterion_count']} "
            f"audit_changes={result['changed_after_audit_count']}"
        )
        print(
            "tokens: "
            f"input={result['usage']['input_tokens']} "
            f"output={result['usage']['output_tokens']} "
            f"reasoning={result['usage']['thinking_tokens']} "
            f"total={result['usage']['total_tokens']}"
        )
        return 0
    if args.command == "resolve-natural-evaluation-ai-review":
        if not args.confirm_subscription_run:
            parser.error(
                "resolve-natural-evaluation-ai-review requires "
                "--confirm-subscription-run"
            )
        with CodexSubscriptionModelPool(
            size=args.concurrency,
            worker_factory=lambda: CodexSubscriptionStructuredModel(
                model_id=args.model,
                effort=args.effort,
                timeout_seconds=args.timeout_seconds,
                max_retries=args.max_retries,
            ),
        ) as model:
            result = run_natural_evaluation_max_resolution(
                source_path=args.source,
                base_review_path=args.base_review,
                review_output_path=args.review_output,
                gold_output_path=args.gold_output,
                checkpoint_dir=args.checkpoint_dir,
                model=model,
                model_id=args.model,
                effort=args.effort,
                source_section=args.source_section,
                group_ids=args.group_ids,
                concurrency=args.concurrency,
                chunk_size=args.chunk_size,
                selection_mode=args.selection_mode,
                progress=print,
            )
        print(f"review: {result['review_output']}")
        print(f"preliminary_gold: {result['gold_output']}")
        print(
            f"source_lines={result['source_line_count']} "
            f"max_reviewed={result['maximum_review_line_count']} "
            f"criteria={result['criterion_count']} "
            f"remaining_uncertain={result['remaining_uncertain_count']}"
        )
        print(
            "max_tokens: "
            f"input={result['usage']['input_tokens']} "
            f"output={result['usage']['output_tokens']} "
            f"reasoning={result['usage']['thinking_tokens']} "
            f"total={result['usage']['total_tokens']}"
        )
        return 0
    if args.command == "build-natural-evaluation-conservative-gold":
        result = build_conservative_natural_ai_gold(
            source_path=args.source,
            tiered_review_path=args.tiered_review,
            selection_config_path=args.selection_config,
            output_path=args.output,
            source_section=args.source_section,
            group_ids=args.group_ids,
        )
        print(f"conservative_preliminary_gold: {result['output']}")
        print(
            f"source_lines={result['source_line_count']} "
            f"high_confidence_lines={result['high_confidence_source_line_count']} "
            f"accepted_lines={result['accepted_source_line_count']} "
            f"criteria={result['criterion_count']} "
            f"deferred_complex_lines={result['deferred_complex_source_line_count']}"
        )
        print(
            "low_coverage_trials="
            + ",".join(result["low_coverage_trial_ids"])
        )
        return 0
    if args.command == "build-natural-evaluation-trial-set":
        result = build_natural_evaluation_trial_set(
            primary_source_path=args.primary_source,
            reserve_source_path=args.reserve_source,
            primary_gold_path=args.primary_gold,
            reserve_gold_path=args.reserve_gold,
            selection_config_path=args.selection_config,
            output_path=args.output,
        )
        print(f"preliminary_trial_set: {result['output']}")
        print(
            f"trials={result['trial_count']} "
            f"criteria={result['criterion_count']} "
            f"replacements={result['replacement_count']}"
        )
        return 0
    if args.command == "build-natural-evaluation-patient-pairs":
        result = build_natural_evaluation_patient_pairs(
            trial_set_path=args.trial_set,
            generation_config_path=args.generation_config,
            output_path=args.output,
        )
        print(f"preliminary_patient_pairs: {result['output']}")
        print(
            f"patients={result['patient_count']} "
            f"episodes={result['episode_count']} "
            f"development={result['development_patient_count']} "
            f"heldout={result['heldout_patient_count']} "
            "changed_confirmations="
            f"{result['paired_confirmation_change_count']}"
        )
        return 0
    if args.command == "audit-natural-evaluation-patient-pairs":
        result = audit_natural_evaluation_patient_pairs(
            trial_set_path=args.trial_set,
            generation_config_path=args.generation_config,
            patient_pairs_path=args.patient_pairs,
        )
        print(
            f"passed={result['passed']} "
            f"patients={result['patient_count']} "
            f"episodes={result['episode_count']} "
            "candidate_mismatches="
            f"{result['candidate_status_mismatch_count']} "
            "recovery_mismatches="
            f"{result['verification_recovery_mismatch_count']}"
        )
        return 0
    if args.command == "build-broad-rescue-dataset":
        result = build_broad_rescue_dataset(
            config_path=args.config,
            output_dir=args.output,
        )
        print(f"trial_set: {result['trial_set']}")
        print(f"patient_pairs: {result['patient_pairs']}")
        print(
            f"groups={result['group_count']} trials={result['trial_count']} "
            f"patients={result['patient_count']} "
            f"patient_trial_pairs={result['patient_trial_pair_count']} "
            f"confirmed={result['complete_confirmed_candidate_count']} "
            f"ineligible={result['complete_ineligible_count']}"
        )
        return 0
    if args.command == "audit-broad-rescue-dataset":
        result = audit_broad_rescue_dataset(
            config_path=args.config,
            trial_set_path=args.trial_set,
            patient_pairs_path=args.patient_pairs,
        )
        print(
            f"passed={result['passed']} groups={result['group_count']} "
            f"trials={result['trial_count']} patients={result['patient_count']} "
            f"patient_trial_pairs={result['patient_trial_pair_count']}"
        )
        return 0
    if args.command == "build-public-protocol-benchmark":
        result = build_source_benchmark(
            config_path=args.config,
            selection_path=args.selection,
            corpus_path=args.trials,
            output_dir=args.output,
        )
        print(f"trial_set: {result['trial_set']}")
        print(f"patient_pairs: {result['patient_pairs']}")
        print(
            f"groups={result['group_count']} trials={result['trial_count']} "
            f"criteria={result['criterion_count']} "
            f"patients={result['patient_count']} "
            f"confirmed={result['complete_confirmed_candidate_count']} "
            f"ineligible={result['complete_ineligible_count']}"
        )
        return 0
    if args.command == "audit-public-protocol-benchmark":
        result = audit_source_benchmark(
            config_path=args.config,
            selection_path=args.selection,
            corpus_path=args.trials,
            trial_set_path=args.trial_set,
            patient_pairs_path=args.patient_pairs,
        )
        print(
            f"passed={result['passed']} groups={result['group_count']} "
            f"trials={result['trial_count']} criteria={result['criterion_count']} "
            f"patients={result['patient_count']}"
        )
        return 0
    if args.command == "build-natural-evaluation-records":
        result = build_natural_evaluation_records(
            patient_pairs_path=args.patient_pairs,
            destination=args.output,
        )
        print(f"natural_records: {result['output']}")
        print(
            f"records={result['record_count']} "
            f"development={result['development_record_count']} "
            f"heldout={result['heldout_record_count']}"
        )
        return 0
    if args.command == "audit-natural-evaluation-records":
        result = audit_natural_evaluation_records(
            patient_pairs_path=args.patient_pairs,
            records_path=args.records,
        )
        print(
            f"passed={result['passed']} records={result['record_count']} "
            f"pair_value_mismatches={result['pair_value_mismatch_count']}"
        )
        return 0
    if args.command == "run-natural-record-structure-evaluation":
        if not args.confirm_subscription_run:
            parser.error(
                "run-natural-record-structure-evaluation requires "
                "--confirm-subscription-run"
            )
        with CodexSubscriptionModelPool(
            size=args.concurrency,
            worker_factory=lambda: CodexSubscriptionStructuredModel(
                model_id=args.model,
                effort=args.effort,
                timeout_seconds=args.timeout_seconds,
            ),
        ) as model:
            result = run_natural_record_structure_evaluation(
                records_path=args.records,
                destination=args.output,
                model=model,
                split=args.split,
                evidence_state=args.evidence_state,
                max_workers=args.concurrency,
            )
        print(
            f"completed={result['completed_record_count']}/"
            f"{result['requested_record_count']} "
            f"critical_accuracy={result['critical_fully_correct_rate']:.4f} "
            f"unknown={result['unknown_measurement_count']} "
            f"tokens={result['token_usage']['total_tokens']}"
        )
        return 0 if result["failed_record_count"] == 0 else 2
    if args.command == "compare-agent-architectures":
        result = build_architecture_comparison(
            workflow_summary_paths=args.workflow,
            output_dir=args.output,
            arm=args.arm,
        )
        print(f"architectures={len(result['rows'])}")
        print(f"report: {args.output / 'report.md'}")
        return 0
    if args.command in {
        "run-natural-question-policy-evaluation",
        "run-json-question-policy-evaluation",
    }:
        result = run_natural_policy_evaluation(
            trial_set_path=args.trial_set,
            generation_config_path=args.generation_config,
            patient_pairs_path=args.patient_pairs,
            records_path=args.records,
            structure_result_paths=args.structure_result,
            destination=args.output,
            action_budget=args.action_budget,
            action_budgets=range(6) if args.budget_sweep else None,
            splits=args.split,
            patient_ids=args.patient_id,
            include_fully_missing=args.include_fully_missing,
        )
        print(
            f"patients={result['patient_count']} runs={result['run_count']} "
            f"output={result['output']}"
        )
        return 0
    if args.command == "run-text-demo":
        run_natural_text_demo(
            trial_set_path=args.trial_set,
            generation_config_path=args.generation_config,
            patient_pairs_path=args.patient_pairs,
            records_path=args.records,
            destination=args.output,
            patient_id=args.patient_id,
            action_budget=args.action_budget,
            input_state=args.input_state,
            auto_advance=args.auto,
        )
        print(f"\n상세 실행 기록: {args.output}")
        return 0
    if args.command == "build-report":
        result = build_research_report(
            destination=args.output,
            question_policy_path=args.question_policy,
            burden_path=args.burden,
            workflow_path=args.workflow,
            budget_frontier_path=args.budget_frontier,
            retrieval_paths=args.retrieval,
            split=args.split,
            input_state=args.input_state,
            action_budget=args.action_budget,
        )
        print(f"report: {result['report']}")
        print(f"metrics: {result['metric_count']}")
        return 0
    if args.command in {
        "audit-final-evaluation-readiness",
        "audit-external-evaluation-readiness",
    }:
        result = build_final_evaluation_readiness(
            trial_set_path=args.trial_set,
            patient_pairs_path=args.patient_pairs,
            workflow_summary_path=args.workflow,
            output_dir=args.output,
        )
        print(
            "external_model_evaluation_ready="
            f"{result['software_ready_for_external_model_evaluation']} "
            "independent_performance_ready="
            f"{result['independent_performance_claim_ready']}"
        )
        print(f"report: {args.output / 'readiness.md'}")
        return 0
    if args.command == "run-trialgpt-pilot":
        if not args.confirm_live_api:
            parser.error("run-trialgpt-pilot requires --confirm-live-api")
        summary_path = run_live_trialgpt_pilot(
            raw_jsonl=args.raw_jsonl,
            sigir_corpus=args.sigir_corpus,
            output_dir=args.output,
            api_key_env_file=args.api_key_env_file,
            api_key_name=args.api_key_name,
            limit=args.limit,
            seed=args.seed,
            model_id=args.model,
            max_output_tokens=args.max_output_tokens,
            timeout_seconds=args.timeout_seconds,
        )
        summary = _read_json(summary_path)
        print(f"summary: {summary_path}")
        print(f"calls: {summary['completed_calls']}/{summary['expected_calls']}")
        print(f"cost_usd: {summary['usage']['total_cost_usd']:.6f}")
        return 0 if summary["failed_calls"] == 0 else 2
    if args.command == "run-trialgpt-experiment":
        if not args.confirm_live_api:
            parser.error("run-trialgpt-experiment requires --confirm-live-api")
        summary_path = run_live_trialgpt_experiment(
            raw_jsonl=args.raw_jsonl,
            sigir_corpus=args.sigir_corpus,
            output_dir=args.output,
            api_key_env_file=args.api_key_env_file,
            api_key_name=args.api_key_name,
            variant=args.variant,
            split_name=args.split_name,
            seed=args.seed,
            limit=args.limit,
            model_id=args.model,
            max_output_tokens=args.max_output_tokens,
            timeout_seconds=args.timeout_seconds,
        )
        summary = _read_json(summary_path)
        print(f"summary: {summary_path}")
        print(f"variant: {summary['variant']}")
        print(f"calls: {summary['completed_calls']}/{summary['expected_calls']}")
        print(f"accuracy: {summary['sonnet_vs_expert']['label_accuracy']:.6f}")
        print(f"cost_usd: {summary['usage']['total_cost_usd']:.6f}")
        return 0 if summary["failed_calls"] == 0 else 2
    if args.command == "run-trialgpt-architecture":
        if not args.confirm_subscription_run:
            parser.error(
                "run-trialgpt-architecture requires --confirm-subscription-run"
            )
        experiment_id = args.experiment_id or (
            f"trialgpt-architecture-sol-medium-{args.stage}"
        )
        try:
            benchmark_path = run_subscription_architecture_stage(
                raw_jsonl=args.raw_jsonl,
                sigir_corpus=args.sigir_corpus,
                output_dir=args.output,
                stage=ExperimentStage(args.stage),
                experiment_id=experiment_id,
                split_seed=args.split_seed,
                order_seed=args.order_seed,
                retrieval_top_k=args.retrieval_top_k,
                pause_threshold_percent=args.pause_at_percent,
                timeout_seconds=args.timeout_seconds,
                case_concurrency=args.case_concurrency,
                progress=print,
            )
        except ArchitectureExperimentPaused as exc:
            print(f"paused: {exc}")
            print(f"resume with the same output and experiment id: {args.output}")
            return 3
        benchmark = _read_json(benchmark_path)
        status = _read_json(args.output / "run-status.json")
        print(f"benchmark: {benchmark_path}")
        for arm in ("S1", "M1", "M2"):
            metrics = benchmark["arm_metrics"][arm]
            print(
                f"{arm}: criterion_accuracy={metrics['criterion_accuracy']:.6f} "
                f"trial_status_accuracy={metrics['trial_status_accuracy']:.6f}"
            )
        usage = status["usage_summary"]["totals"]
        print(
            "tokens: "
            f"input={usage['input_tokens']} output={usage['output_tokens']} "
            f"reasoning={usage['reasoning_tokens']} total={usage['total_tokens']}"
        )
        return 0 if status["failed_arms"] == 0 else 2
    if args.command == "run-trialgpt-strong-review":
        if not args.confirm_subscription_run:
            parser.error(
                "run-trialgpt-strong-review requires --confirm-subscription-run"
            )
        try:
            benchmark_path = run_subscription_strong_review_stage(
                raw_jsonl=args.raw_jsonl,
                sigir_corpus=args.sigir_corpus,
                output_dir=args.output,
                stage=args.stage,
                limit=args.limit,
                retrieval_top_k=args.retrieval_top_k,
                timeout_seconds=args.timeout_seconds,
                case_concurrency=args.case_concurrency,
                progress=print,
            )
        except StrongReviewExperimentIncomplete as exc:
            print(f"incomplete: {exc}")
            print(f"resume with the same output: {args.output}")
            return 3
        benchmark = _read_json(benchmark_path)
        print(f"benchmark: {benchmark_path}")
        for arm in ("S1-R", "S1-RV", "S1-RW"):
            metrics = benchmark["arm_metrics"][arm]
            print(
                f"{arm}: criterion_accuracy={metrics['criterion_accuracy']:.6f} "
                f"trial_status_accuracy={metrics['trial_status_accuracy']:.6f} "
                f"tokens={metrics['system_total_tokens']}"
            )
        print(
            f"executed_total_tokens={benchmark['executed_total_tokens']} "
            f"web_search_actions={benchmark['web_search_actions']}"
        )
        return 0
    if args.command == "run-trialgpt-retrieval":
        config = TrialGPTRetrievalConfig(
            corpus_name=args.corpus,
            query_type=args.query_type,
            fusion_k=args.fusion_k,
            search_depth=args.search_depth,
            bm25_weight=args.bm25_weight,
            medcpt_weight=args.medcpt_weight,
            batch_size=args.batch_size,
            device=args.device,
        )
        summary_path = run_trialgpt_retrieval(
            args.dataset,
            args.cache,
            args.output,
            config,
            progress=print,
        )
        summary = _read_json(summary_path)
        print(f"summary: {summary_path}")
        for row in summary["metric_rows"]:
            print(
                f"@{row['depth']}: weighted_recall={row['weighted_recall']:.6f} "
                f"binary_recall={row['binary_recall']:.6f} "
                f"ndcg={row['ndcg']:.6f} precision={row['precision']:.6f}"
            )
        return 0
    if args.command == "run-interactive-pilot":
        if args.with_subscription_model and not args.confirm_subscription_run:
            parser.error(
                "--with-subscription-model requires --confirm-subscription-run"
            )
        summary_path = run_interactive_pilot(
            args.output,
            include_subscription_model=args.with_subscription_model,
            case_concurrency=args.case_concurrency,
            timeout_seconds=args.timeout_seconds,
            progress=print,
        )
        summary = _read_json(summary_path)
        print(f"summary: {summary_path}")
        for row in summary["summaries"]:
            print(
                f"{row['policy_id']}: "
                f"trial_recovery={row['mean_trial_status_recovery']:.3f} "
                f"necessary_recall={row['mean_necessary_fact_recall']:.3f} "
                f"actions={row['total_actions']}"
            )
        return 0
    if args.command == "run-interactive-stress":
        summary_path = run_interactive_stress(
            args.output,
            structures_per_topology=args.structures_per_topology,
            seed=args.seed,
            progress=print,
        )
        summary = _read_json(summary_path)
        print(f"summary: {summary_path}")
        for row in summary["policy_metrics"]:
            print(
                f"{row['evaluation_distribution']} {row['policy_id']}: "
                f"recovery={row['expected_trial_recovery']:.3f} "
                f"worst={row['worst_trial_recovery']:.3f} "
                f"actions={row['expected_actions']:.3f}"
            )
        return 0
    if args.command == "run-public-interactive-benchmark":
        summary_path = run_public_interactive_benchmark(
            args.config,
            args.source_cache,
            args.output,
            seed=args.seed,
            action_budget=args.action_budget,
            progress=lambda message: print(message, flush=True),
        )
        summary = _read_json(summary_path)
        print(f"summary: {summary_path}")
        print(
            "primary_gate_passed: "
            f"{summary['paired_heldout']['primary_gate_passed']}"
        )
        for row in summary["policy_metrics"]:
            print(
                f"{row['split']} {row['policy_id']}: "
                f"recovery={row['mean_trial_recovery']:.3f} "
                f"actions={row['mean_actions']:.3f} "
                f"route_cost={row['mean_route_cost']:.3f}"
            )
        return 0
    if args.command == "run-public-grid-stress":
        summary_path = run_public_grid_stress(
            args.config,
            args.source_cache,
            args.output,
            action_budget=args.action_budget,
            progress=lambda message: print(message, flush=True),
        )
        summary = _read_json(summary_path)
        print(f"summary: {summary_path}")
        for row in summary["policy_metrics"]:
            print(
                f"{row['evaluation_distribution']} {row['policy_id']}: "
                f"recovery={row['mean_trial_recovery']:.3f} "
                f"actions={row['mean_actions']:.3f} "
                f"route_cost={row['mean_route_cost']:.3f}"
            )
        return 0
    if args.command == "run-public-burden-benchmark":
        summary_path = run_public_burden_benchmark(
            args.config,
            args.source_cache,
            args.output,
            action_budget=args.action_budget,
            progress=lambda message: print(message, flush=True),
        )
        summary = _read_json(summary_path)
        comparison = summary["adoption_comparison"]
        print(f"summary: {summary_path}")
        print(f"patient_settings: {summary['patient_setting_count']}")
        print(f"policy_runs: {summary['policy_run_count']}")
        print(f"adoption_gate_passed: {comparison['adoption_gate_passed']}")
        print(
            "heldout: "
            f"adaptive_full_recovery={comparison['heldout']['candidate_recovery']:.3f} "
            f"baseline_full_recovery={comparison['heldout']['baseline_recovery']:.3f} "
            f"adaptive_feasible_recovery="
            f"{comparison['heldout']['candidate_burden_feasible_recovery']:.3f} "
            f"baseline_feasible_recovery="
            f"{comparison['heldout']['baseline_burden_feasible_recovery']:.3f} "
            f"burden_reduction={comparison['heldout']['constrained_burden_reduction']:.3f}"
        )
        return 0
    raise AssertionError(f"unhandled command: {args.command}")
