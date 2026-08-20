from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from clarifytrial.agents import (
    CoordinatorAgent,
    CoordinatorDecision,
    CriterionAssessmentBatch,
    MatcherJudgeAgent,
    NextEvidenceAgent,
    ReviewDecision,
    SelectiveReviewerAgent,
)
from clarifytrial.contracts import AgentAction, CriterionAssessment
from clarifytrial.llm import ScriptedStructuredModel
from clarifytrial.trace import TraceRecorder


def test_agents_use_separate_calls_contracts_and_trace_names() -> None:
    received: dict[str, dict[str, Any]] = {}

    def scripted(role: str, response: Mapping[str, Any]):
        def handler(payload: Mapping[str, Any]) -> Mapping[str, Any]:
            received[role] = dict(payload)
            return response

        return handler

    model = ScriptedStructuredModel(
        {
            "coordinator": scripted(
                "coordinator",
                {
                    "route": "MATCHER_JUDGE",
                    "target_ids": ["criterion-1"],
                    "reason_code": "new_case",
                    "reason": "The criterion has not been assessed yet.",
                },
            ),
            "matcher_judge": scripted(
                "matcher_judge",
                {
                    "assessments": [
                        {
                            "criterion_id": "criterion-1",
                            "criterion_source_location": "trial-1#criterion-1",
                            "clinical_status": "supports",
                            "evidence_sufficiency": "sufficient",
                            "evidence_ids": ["evidence-1"],
                            "missing_information_ids": [],
                            "rationale": "The supplied fact directly supports the criterion.",
                            "review_flags": [],
                        },
                        {
                            "criterion_id": "criterion-2",
                            "criterion_source_location": "trial-1#criterion-2",
                            "clinical_status": "unknown",
                            "evidence_sufficiency": "insufficient",
                            "evidence_ids": [],
                            "missing_information_ids": ["fact-medication"],
                            "rationale": "The supplied facts do not establish medication use.",
                            "review_flags": ["missing_evidence"],
                        },
                    ]
                },
            ),
            "next_evidence": scripted(
                "next_evidence",
                {
                    "action": "ASK_PATIENT",
                    "target_fact_id": "fact-medication",
                    "related_criterion_ids": ["criterion-1"],
                    "reason": "The patient can report current medication use.",
                    "message": "Are you currently taking the listed medication?",
                },
            ),
            "selective_reviewer": scripted(
                "selective_reviewer",
                {
                    "conclusion_id": "assessment-1",
                    "decision": "approve",
                    "patient_evidence_ids": ["evidence-1"],
                    "trial_evidence_ids": ["criterion-1"],
                    "affected_condition_ids": ["criterion-1"],
                    "missing_fact_ids": [],
                    "reason_code": "sources_agree",
                    "reason": "The conclusion agrees with both supplied sources.",
                },
            ),
        }
    )
    agents = [
        CoordinatorAgent(model),
        MatcherJudgeAgent(model),
        NextEvidenceAgent(model),
        SelectiveReviewerAgent(model),
    ]
    payloads = {
        "coordinator": {"state_summary": "new", "remaining_actions": 2},
        "matcher_judge": {"criterion_id": "criterion-1", "evidence_ids": ["evidence-1"]},
        "next_evidence": {"request_ids": ["fact-medication"], "remaining_actions": 2},
        "selective_reviewer": {"conclusion_id": "assessment-1", "flag": "explicit"},
    }
    trace = TraceRecorder("synthetic-case-1")

    results = [
        agent.run(
            payloads[agent.agent_name],
            trace=trace,
            cycle=index,
            input_refs=[f"input-{index}"],
        )
        for index, agent in enumerate(agents)
    ]

    assert isinstance(results[0].output, CoordinatorDecision)
    assert isinstance(results[1].output, CriterionAssessmentBatch)
    assert all(
        isinstance(assessment, CriterionAssessment)
        for assessment in results[1].output.assessments
    )
    assert isinstance(results[2].output, AgentAction)
    assert isinstance(results[3].output, ReviewDecision)
    assert model.call_count == {
        "coordinator": 1,
        "matcher_judge": 1,
        "next_evidence": 1,
        "selective_reviewer": 1,
    }

    # Each role receives only its explicit payload, never another role's history.
    assert received == payloads

    assert [event.actor for event in trace.events] == [
        "coordinator",
        "matcher_judge",
        "next_evidence",
        "selective_reviewer",
    ]
    assert [event.output["prompt_id"] for event in trace.events] == [
        "prompts/coordinator.md",
        "prompts/matcher_judge.md",
        "prompts/next_evidence.md",
        "prompts/selective_reviewer.md",
    ]
    assert [event.output["response_model"] for event in trace.events] == [
        "CoordinatorDecision",
        "CriterionAssessmentBatch",
        "AgentAction",
        "ReviewDecision",
    ]


def test_agent_metadata_is_unique_and_points_to_prompt_files() -> None:
    agent_types = [
        CoordinatorAgent,
        MatcherJudgeAgent,
        NextEvidenceAgent,
        SelectiveReviewerAgent,
    ]

    assert len({agent.agent_name for agent in agent_types}) == 4
    assert len({agent.prompt_id for agent in agent_types}) == 4
    assert all(agent.prompt_id.startswith("prompts/") for agent in agent_types)
    assert all(agent.prompt_id.endswith(".md") for agent in agent_types)
    assert all(Path(agent.prompt_id).is_file() for agent in agent_types)


def test_matcher_batch_rejects_duplicate_criterion_ids() -> None:
    assessment = {
        "criterion_id": "criterion-1",
        "criterion_source_location": "trial-1#criterion-1",
        "clinical_status": "unknown",
        "evidence_sufficiency": "insufficient",
        "evidence_ids": [],
        "missing_information_ids": ["fact-1"],
        "rationale": "The required fact is not present.",
        "review_flags": ["missing_evidence"],
    }

    with pytest.raises(ValidationError):
        CriterionAssessmentBatch.model_validate(
            {"assessments": [assessment, assessment]}
        )
