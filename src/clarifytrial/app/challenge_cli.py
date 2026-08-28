"""Command-line registration and provider setup for ``run-challenge``."""

from __future__ import annotations

import argparse
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from ..llm import (
    ALLOWED_CODEX_EFFORTS,
    AnthropicStructuredModel,
    CodexSubscriptionStructuredModel,
    DEFAULT_CODEX_EFFORT,
    DEFAULT_CODEX_MODEL,
)
from ..preparation import (
    CandidateSearch,
    ClinicalTrialsGovCandidateSearch,
    DEFAULT_ENROLLING_STATUSES,
    InMemoryCandidateSearch,
    TeamTrialCandidateSearch,
    TrialProtocolSource,
)
from ..settings import EpisodeSettings
from .challenge_contracts import ChallengeRunOptions
from .challenge_runner import run_challenge_screening


@dataclass(frozen=True, slots=True)
class ChallengeCliDependencies:
    """Shared CLI helpers supplied by the repository's main command module."""

    read_json: Callable[[Path], Any]
    read_disclaimer: Callable[[], str]
    read_env_value: Callable[[Path, str], str]
    build_trialgpt_candidate_search: Callable[
        [argparse.Namespace, argparse.ArgumentParser], CandidateSearch
    ]


def add_challenge_parser(commands: Any) -> argparse.ArgumentParser:
    """Register the public options for ``run-challenge``."""

    challenge = commands.add_parser(
        "run-challenge",
        help=(
            "read topics[num, title], find candidate trials, ask for missing "
            "information, and update the ranked result"
        ),
    )
    challenge.add_argument("--topics", required=True, type=Path)
    challenge.add_argument(
        "--topic-settings",
        type=Path,
        help=(
            "optional JSON with patient limits and available confirmation "
            "routes keyed by topic num"
        ),
    )
    selection = challenge.add_mutually_exclusive_group(required=True)
    selection.add_argument(
        "--topic-id",
        action="append",
        default=[],
        help="topic num to run; repeat to select several",
    )
    selection.add_argument("--all-topics", action="store_true")
    challenge.add_argument("--candidate-count", type=int, default=10)
    challenge.add_argument(
        "--trial-protocol-cache",
        type=Path,
        default=Path("runs") / "trial-protocol-cache",
        help="directory that reuses unchanged structured trial criteria",
    )
    challenge.add_argument(
        "--as-of",
        help="decision time in ISO format; defaults to the current local time",
    )
    challenge.add_argument("--output", required=True, type=Path)
    challenge.add_argument(
        "--candidate-search",
        choices=("clinicaltrials", "team-jsonl", "trialgpt", "local-bm25"),
        default="clinicaltrials",
    )
    challenge.add_argument(
        "--clinicaltrials-cache",
        type=Path,
        default=Path("runs") / "clinicaltrials-search-cache",
        help="directory that reuses unchanged ClinicalTrials.gov search responses",
    )
    challenge.add_argument(
        "--refresh-trial-search",
        action="store_true",
        help="ignore saved ClinicalTrials.gov search responses for this run",
    )
    challenge.add_argument("--trial-sources", type=Path)
    challenge.add_argument(
        "--team-trials",
        type=Path,
        help="team trials.jsonl snapshot used by team-jsonl search",
    )
    challenge.add_argument(
        "--trial-status",
        action="append",
        default=[],
        help=(
            "recruitment status admitted by ClinicalTrials.gov or team-jsonl "
            "search; repeat to add statuses, or omit to use enrolling statuses"
        ),
    )
    challenge.add_argument("--trialgpt-corpus", type=Path)
    challenge.add_argument("--trialgpt-cache", type=Path)
    challenge.add_argument(
        "--trialgpt-corpus-name",
        choices=("trec_2021", "trec_2022"),
        default="trec_2022",
    )
    challenge.add_argument("--retrieval-device", default="cuda")
    challenge.add_argument("--bm25-only", action="store_true")
    challenge.add_argument("--resume", type=Path)
    challenge.add_argument("--retry-unavailable", action="store_true")
    challenge.add_argument("--approve-patient-choice", action="store_true")
    challenge.add_argument("--authorize-clinician", action="store_true")
    challenge.add_argument(
        "--provider",
        choices=("codex-subscription", "anthropic"),
        default="codex-subscription",
    )
    challenge.add_argument("--model")
    challenge.add_argument(
        "--effort",
        choices=sorted(ALLOWED_CODEX_EFFORTS),
        default=DEFAULT_CODEX_EFFORT,
    )
    challenge.add_argument("--api-key-env-file", type=Path)
    challenge.add_argument("--api-key-name", default="ANTHROPIC_API_KEY")
    challenge.add_argument("--max-output-tokens", type=int, default=8_192)
    challenge.add_argument("--timeout-seconds", type=float, default=300)
    challenge.add_argument("--max-external-actions", type=int, default=3)
    challenge.add_argument("--max-selective-reviews", type=int, default=1)
    challenge.add_argument("--max-cycles", type=int, default=12)
    challenge.add_argument(
        "--question-policy",
        choices=("clarifytrial", "fixed_order"),
        default="clarifytrial",
    )
    challenge.add_argument("--use-model-coordinator", action="store_true")
    challenge.add_argument("--no-batch-judgments", action="store_true")
    challenge.add_argument("--confirm-model-run", action="store_true")
    return challenge


def _validate_args(
    args: argparse.Namespace,
    parser: argparse.ArgumentParser,
) -> datetime:
    if not args.confirm_model_run:
        parser.error(
            "run-challenge requires --confirm-model-run because it makes "
            "live model calls"
        )
    if args.all_topics and args.resume is not None:
        parser.error("--resume can be used with one --topic-id only")
    if (
        args.retry_unavailable
        or args.approve_patient_choice
        or args.authorize_clinician
    ) and args.resume is None:
        parser.error("retry and approval flags require --resume with a saved session")
    try:
        return (
            datetime.fromisoformat(args.as_of)
            if args.as_of
            else datetime.now().astimezone()
        )
    except ValueError:
        parser.error("--as-of must use ISO date or date-time format")


def _candidate_search(
    args: argparse.Namespace,
    parser: argparse.ArgumentParser,
    dependencies: ChallengeCliDependencies,
) -> CandidateSearch | None:
    if args.resume is not None:
        return None
    if args.candidate_search == "trialgpt":
        return dependencies.build_trialgpt_candidate_search(args, parser)
    if args.candidate_search == "clinicaltrials":
        statuses = args.trial_status or sorted(DEFAULT_ENROLLING_STATUSES)
        return ClinicalTrialsGovCandidateSearch(
            args.clinicaltrials_cache,
            included_statuses=statuses,
            timeout_seconds=args.timeout_seconds,
            force_refresh=args.refresh_trial_search,
        )
    if args.candidate_search == "team-jsonl":
        if args.team_trials is None:
            parser.error("team-jsonl requires --team-trials")
        statuses = args.trial_status or sorted(DEFAULT_ENROLLING_STATUSES)
        return TeamTrialCandidateSearch(
            args.team_trials,
            included_statuses=statuses,
        )
    if args.trial_sources is None:
        parser.error("local-bm25 requires --trial-sources")
    source_rows = dependencies.read_json(args.trial_sources)
    if not isinstance(source_rows, list):
        parser.error("--trial-sources must contain a JSON list")
    return InMemoryCandidateSearch(
        [TrialProtocolSource.model_validate(item) for item in source_rows]
    )


def run_challenge_command(
    args: argparse.Namespace,
    parser: argparse.ArgumentParser,
    *,
    dependencies: ChallengeCliDependencies,
) -> int:
    """Validate CLI input, create the selected provider, and run the workflow."""

    as_of = _validate_args(args, parser)
    settings = EpisodeSettings(
        max_external_actions=args.max_external_actions,
        max_selective_reviews=args.max_selective_reviews,
        max_cycles=args.max_cycles,
        use_model_coordinator=args.use_model_coordinator,
        batch_trial_judgments=not args.no_batch_judgments,
        question_policy=args.question_policy,
    )
    candidate_search = _candidate_search(args, parser, dependencies)
    options = ChallengeRunOptions(
        topics_path=args.topics,
        output_dir=args.output,
        topic_ids=tuple(args.topic_id),
        all_topics=args.all_topics,
        as_of=as_of,
        candidate_count=args.candidate_count,
        settings=settings,
        trial_protocol_cache_dir=args.trial_protocol_cache,
        topic_settings_path=args.topic_settings,
        resume_path=args.resume,
        retry_unavailable=args.retry_unavailable,
        approve_patient_choice=args.approve_patient_choice,
        authorize_clinician=args.authorize_clinician,
    )
    medical_disclaimer = dependencies.read_disclaimer()
    if args.provider == "codex-subscription":
        model_id = args.model or DEFAULT_CODEX_MODEL
        with CodexSubscriptionStructuredModel(
            model_id=model_id,
            effort=args.effort,
            timeout_seconds=args.timeout_seconds,
        ) as model:
            outcome = run_challenge_screening(
                options=options,
                model=model,
                model_label=f"{model_id} / {args.effort}",
                candidate_search=candidate_search,
                medical_disclaimer=medical_disclaimer,
            )
    else:
        if args.api_key_env_file is None:
            parser.error("anthropic provider requires --api-key-env-file")
        model_id = args.model or "claude-sonnet-5"
        model = AnthropicStructuredModel(
            api_key=dependencies.read_env_value(
                args.api_key_env_file,
                args.api_key_name,
            ),
            model_id=model_id,
            max_output_tokens=args.max_output_tokens,
            timeout_seconds=args.timeout_seconds,
        )
        outcome = run_challenge_screening(
            options=options,
            model=model,
            model_label=model_id,
            candidate_search=candidate_search,
            medical_disclaimer=medical_disclaimer,
        )
    return 0 if outcome.runs else 2


__all__ = [
    "ChallengeCliDependencies",
    "add_challenge_parser",
    "run_challenge_command",
]
