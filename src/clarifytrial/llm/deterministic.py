"""Deterministic workflow model for offline runs and integration tests.

It does not interpret free clinical text.  It mirrors code-authoritative
mechanical checks, preserves declared identifiers, and writes plain request
messages so the complete structured workflow can run without an external API.
"""

from __future__ import annotations

from typing import Any

from .base import ModelCall, ModelUsage, ResponseT


class DeterministicWorkflowModel:
    """Serve the four workflow roles without network access."""

    def __init__(self) -> None:
        self.call_count: dict[str, int] = {}

    def complete(self, call: ModelCall[ResponseT]) -> tuple[ResponseT, ModelUsage]:
        self.call_count[call.role] = self.call_count.get(call.role, 0) + 1
        payload = call.payload
        if call.role == "coordinator":
            raw: dict[str, Any] = {
                "route": payload["allowed_routes"][0],
                "target_ids": payload["required_target_ids"],
                "reason_code": "single_allowed_route",
                "reason": "코드 규칙상 가능한 다음 단계를 실행한다.",
            }
        elif call.role == "matcher_judge":
            requests = payload.get("evidence_requests", [])
            assessments = []
            for criterion in payload["criteria"]:
                criterion_id = criterion["criterion_id"]
                checked = payload["mechanical_checks"][criterion_id]
                missing_ids = []
                if checked["evidence_sufficiency"] != "sufficient":
                    missing_ids = [
                        item["fact_id"]
                        for item in requests
                        if criterion_id in item["related_criterion_ids"]
                    ]
                assessments.append(
                    {
                        "criterion_id": criterion_id,
                        "criterion_source_location": criterion["source_location"],
                        "clinical_status": checked["clinical_status"],
                        "evidence_sufficiency": checked["evidence_sufficiency"],
                        "evidence_ids": checked["evidence_ids"],
                        "missing_information_ids": missing_ids,
                        "rationale": (
                            "구조화된 환자 사실과 조건의 코드 검사 결과를 적용했다."
                        ),
                        "review_flags": [],
                    }
                )
            raw = {"assessments": assessments}
        elif call.role == "next_evidence":
            required = payload["required_action"]
            pending = payload.get("pending_information", [])
            description = (
                pending[0].get("description", required["target_fact_id"])
                if pending
                else required["target_fact_id"]
            )
            raw = {
                **required,
                "reason": payload.get(
                    "selection_reason",
                    "현재 판단을 끝내는 데 필요한 정보를 확인한다.",
                ),
                "message": f"{description}을(를) 확인해 주세요.",
            }
        elif call.role == "selective_reviewer":
            raw = {
                "conclusion_id": payload["conclusion_id"],
                "decision": "approve",
                "patient_evidence_ids": [
                    item["evidence_id"] for item in payload.get("patient_facts", [])
                ],
                "trial_evidence_ids": [
                    item["criterion_id"] for item in payload.get("criteria", [])
                ],
                "affected_condition_ids": [],
                "missing_fact_ids": [],
                "reason_code": "structured_checks_consistent",
                "reason": "제공된 구조화 근거와 코드 검사 결과가 일치한다.",
            }
        else:
            raise KeyError(f"deterministic workflow model does not serve {call.role!r}")

        response = call.response_model.model_validate(raw)
        usage = ModelUsage(
            model_id="deterministic-workflow",
            effort=None,
            input_tokens=0,
            output_tokens=0,
            latency_ms=0,
            finish_reason="stop",
        )
        return response, usage


__all__ = ["DeterministicWorkflowModel"]
