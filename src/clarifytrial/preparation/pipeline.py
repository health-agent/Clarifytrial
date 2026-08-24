"""Search, structure, and run one natural-language screening request."""

from __future__ import annotations

from typing import Protocol

from ..contracts import ContractModel, TrialSearchRank
from ..trace import TraceRecorder
from ..workflow import (
    PatientScreeningCase,
    PatientScreeningResult,
    PatientScreeningRunner,
)
from ..workflow.patient_screening_contracts import InformationTools
from .candidate_search import CandidateSearch
from .contracts import (
    NaturalScreeningRequest,
    NaturalScreeningUsage,
    PreparedScreeningCase,
    RoleTokenUsage,
)
from .patient_record import PatientRecordStructurerAgent, structure_patient_record
from .trial_protocol import (
    TrialProtocolStructurerAgent,
    build_acquisition_options,
    declared_information_needs,
    merge_information_requests,
    structure_trial_protocol,
)


class InformationToolFactory(Protocol):
    """Build tools after deterministic missing-fact IDs have been assigned."""

    def __call__(self, prepared: PreparedScreeningCase) -> InformationTools: ...


class NaturalScreeningResult(ContractModel):
    """Natural-source preparation and the connected screening result."""

    prepared: PreparedScreeningCase
    screening: PatientScreeningResult
    usage: NaturalScreeningUsage


_USAGE_FIELDS = (
    "input_tokens",
    "output_tokens",
    "thinking_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
)


def _usage_count(raw: dict, field: str) -> int:
    value = raw.get(field)
    return value if isinstance(value, int) and value >= 0 else 0


def summarize_model_usage(trace: TraceRecorder) -> NaturalScreeningUsage:
    """Sum observable provider counters without estimating missing counters."""

    grouped: dict[str, list[dict]] = {}
    for event in trace.events:
        if event.event != "structured_model_completed" or event.usage is None:
            continue
        grouped.setdefault(event.actor, []).append(event.usage)

    by_role: dict[str, RoleTokenUsage] = {}
    for role, rows in sorted(grouped.items()):
        counts = {
            field: sum(_usage_count(row, field) for row in rows)
            for field in _USAGE_FIELDS
        }
        provider_totals = [
            row["total_tokens"]
            for row in rows
            if isinstance(row.get("total_tokens"), int)
            and row["total_tokens"] >= 0
        ]
        total_tokens = sum(
            row["total_tokens"]
            if isinstance(row.get("total_tokens"), int)
            and row["total_tokens"] >= 0
            else _usage_count(row, "input_tokens")
            + _usage_count(row, "output_tokens")
            for row in rows
        )
        by_role[role] = RoleTokenUsage(
            call_count=len(rows),
            **counts,
            total_tokens=total_tokens,
            calls_with_provider_total=len(provider_totals),
        )

    return NaturalScreeningUsage(
        call_count=sum(item.call_count for item in by_role.values()),
        input_tokens=sum(item.input_tokens for item in by_role.values()),
        output_tokens=sum(item.output_tokens for item in by_role.values()),
        thinking_tokens=sum(item.thinking_tokens for item in by_role.values()),
        cache_creation_input_tokens=sum(
            item.cache_creation_input_tokens for item in by_role.values()
        ),
        cache_read_input_tokens=sum(
            item.cache_read_input_tokens for item in by_role.values()
        ),
        total_tokens=sum(item.total_tokens for item in by_role.values()),
        calls_with_provider_total=sum(
            item.calls_with_provider_total for item in by_role.values()
        ),
        by_role=by_role,
    )


class NaturalScreeningPipeline:
    """Connect natural sources to the existing structured patient workflow."""

    def __init__(
        self,
        *,
        patient_structurer: PatientRecordStructurerAgent,
        trial_structurer: TrialProtocolStructurerAgent,
        candidate_search: CandidateSearch,
        screening_runner: PatientScreeningRunner,
    ) -> None:
        self._patient_structurer = patient_structurer
        self._trial_structurer = trial_structurer
        self._candidate_search = candidate_search
        self._screening_runner = screening_runner

    def prepare(
        self,
        request: NaturalScreeningRequest,
        *,
        trace: TraceRecorder | None = None,
    ) -> PreparedScreeningCase:
        """Prepare a fully cited structured case without running information tools."""

        recorder = trace or TraceRecorder(request.case_id)
        patient_state, search_conditions = structure_patient_record(
            request.patient_record,
            self._patient_structurer,
            trace=recorder,
        )
        candidate_hits = self._candidate_search.search(
            search_conditions,
            top_k=request.candidate_count,
        )
        if not candidate_hits:
            raise ValueError("candidate search returned no trial with a positive match")
        recorder.record(
            cycle=0,
            actor="candidate_trial_search",
            event="candidate_trials_selected",
            input_refs=[request.patient_record.source_id],
            output={
                "search_conditions": search_conditions,
                "trial_ids": [item.source.trial_id for item in candidate_hits],
                "scores": [item.score for item in candidate_hits],
                "methods": sorted(
                    {item.retrieval_method for item in candidate_hits}
                ),
            },
        )
        known_needs = declared_information_needs(request.acquisition_paths)
        prepared_trials = [
            structure_trial_protocol(
                item.source,
                self._trial_structurer,
                known_needs=known_needs,
                trace=recorder,
            )
            for item in candidate_hits
        ]
        evidence_requests, fact_id_by_key = merge_information_requests(
            prepared_trials
        )
        acquisition_options = build_acquisition_options(
            request.acquisition_paths,
            fact_id_by_key=fact_id_by_key,
            requests=evidence_requests,
        )
        screening_case = PatientScreeningCase(
            case_id=request.case_id,
            disease_group=" / ".join(search_conditions),
            trials=[item.trial for item in prepared_trials],
            initial_patient_state=patient_state,
            evidence_requests=evidence_requests,
            acquisition_options=acquisition_options,
            patient_burden_input=request.patient_burden_input,
            candidate_ranking=[
                TrialSearchRank(
                    trial_id=item.source.trial_id,
                    rank=item.rank,
                    score=item.score,
                    retrieval_method=item.retrieval_method,
                )
                for item in candidate_hits
            ],
        )
        recorder.record(
            cycle=0,
            actor="screening_case_preparation",
            event="structured_case_completed",
            input_refs=[item.source.trial_id for item in candidate_hits],
            output={
                "trial_count": len(screening_case.trials),
                "criterion_count": sum(
                    len(item.criteria) for item in screening_case.trials
                ),
                "missing_fact_count": len(evidence_requests),
                "acquisition_option_count": len(acquisition_options),
            },
        )
        return PreparedScreeningCase(
            request_case_id=request.case_id,
            patient_state=patient_state,
            search_conditions=search_conditions,
            candidate_hits=candidate_hits,
            fact_id_by_key=fact_id_by_key,
            screening_case=screening_case,
        )

    def run(
        self,
        request: NaturalScreeningRequest,
        tool_factory: InformationToolFactory,
        *,
        trace: TraceRecorder | None = None,
    ) -> NaturalScreeningResult:
        """Run candidate search through final recommendations in one call."""

        recorder = trace or TraceRecorder(request.case_id)
        prepared = self.prepare(request, trace=recorder)
        tools = tool_factory(prepared)
        screening = self._screening_runner.run(
            prepared.screening_case,
            tools,
            trace=recorder,
        )
        return NaturalScreeningResult(
            prepared=prepared,
            screening=screening,
            usage=summarize_model_usage(recorder),
        )
