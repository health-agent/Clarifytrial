"""Local command-line entry points for reproducible ClarifyTrial examples."""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel

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
    TrialDecision,
)
from .environment import (
    HiddenFactAnswer,
    HiddenPatientEnvironment,
    PublicFactRequest,
    PublicQuestionCatalog,
    SyntheticInformationTools,
)
from .evaluation import DecisionGold, score_decision
from .llm import ScriptedStructuredModel
from .settings import EpisodeSettings
from .trace import TraceRecorder
from .workflow import EpisodeAgents, EpisodeCase, EpisodeResult, EpisodeRunner


_DISCLAIMER_FALLBACK = (
    "ClarifyTrial은 연구용 시제품입니다. 이 결과만으로 임상시험 참가 가능성을 "
    "확정할 수 없습니다. 자격을 판단할 때는 의료 전문가와 해당 임상시험 연구진이 "
    "최신 공식 계획서와 전체 환자 기록을 다시 확인해야 합니다."
)


def _read_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def _read_disclaimer() -> str:
    candidates = (
        Path.cwd() / "MEDICAL_DISCLAIMER.md",
        Path(__file__).resolve().parents[2] / "MEDICAL_DISCLAIMER.md",
    )
    for path in candidates:
        if path.is_file():
            lines = [
                line.strip()
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip() and not line.lstrip().startswith("#")
            ]
            if lines:
                return " ".join(lines)
    return _DISCLAIMER_FALLBACK


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
    )
    paths: list[Path] = []
    for name, model in models:
        path = destination / name
        _write_json(path, model.model_json_schema())
        paths.append(path)
    return paths


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

    schemas = commands.add_parser(
        "export-schemas",
        help="export the JSON contracts used by the workflow",
    )
    schemas.add_argument("--output", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "run-example":
        result_path = run_example(args.case, args.output)
        print(f"result: {result_path}")
        print(f"trace: {args.output / 'trace.jsonl'}")
        return 0
    if args.command == "export-schemas":
        for path in export_schemas(args.output):
            print(path)
        return 0
    raise AssertionError(f"unhandled command: {args.command}")
