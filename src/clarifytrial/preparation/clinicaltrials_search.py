"""Candidate search backed by the public ClinicalTrials.gov API v2."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from ..io import atomic_write_text
from ..retrieval.bm25 import tokenize
from .contracts import CandidateSearchHit, TrialProtocolSource
from .team_trials import DEFAULT_ENROLLING_STATUSES


CLINICALTRIALS_API_ROOT = "https://clinicaltrials.gov/api/v2"
CLINICALTRIALS_STUDY_ROOT = "https://clinicaltrials.gov/study"

JsonFetcher = Callable[[str, float], Mapping[str, Any]]


def _fetch_json(url: str, timeout_seconds: float) -> Mapping[str, Any]:
    request = Request(
        url,
        headers={"User-Agent": "ClarifyTrial-research/0.1"},
    )
    with urlopen(request, timeout=timeout_seconds) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("ClinicalTrials.gov returned a non-object JSON response")
    return payload


def _study_source(study: Mapping[str, Any]) -> TrialProtocolSource | None:
    protocol = study.get("protocolSection")
    if not isinstance(protocol, Mapping):
        return None
    identification = protocol.get("identificationModule")
    status = protocol.get("statusModule")
    conditions_module = protocol.get("conditionsModule")
    description = protocol.get("descriptionModule")
    eligibility = protocol.get("eligibilityModule")
    if not isinstance(identification, Mapping) or not isinstance(eligibility, Mapping):
        return None

    trial_id = str(identification.get("nctId") or "").strip()
    title = str(
        identification.get("briefTitle")
        or identification.get("officialTitle")
        or trial_id
    ).strip()
    criteria = str(eligibility.get("eligibilityCriteria") or "").strip()
    if not trial_id or not title or not criteria:
        return None

    conditions: list[str] = []
    if isinstance(conditions_module, Mapping):
        raw_conditions = conditions_module.get("conditions", [])
        if isinstance(raw_conditions, list):
            conditions = [str(item).strip() for item in raw_conditions if str(item).strip()]

    summaries: list[str] = []
    if isinstance(description, Mapping):
        for key in ("briefSummary", "detailedDescription"):
            value = str(description.get(key) or "").strip()
            if value and value not in summaries:
                summaries.append(value)

    eligibility_lines = []
    sex = str(eligibility.get("sex") or "").strip()
    minimum_age = str(eligibility.get("minimumAge") or "").strip()
    maximum_age = str(eligibility.get("maximumAge") or "").strip()
    if sex:
        eligibility_lines.append(f"Sex: {sex}")
    if minimum_age:
        eligibility_lines.append(f"Minimum Age: {minimum_age}")
    if maximum_age:
        eligibility_lines.append(f"Maximum Age: {maximum_age}")
    eligibility_lines.append(criteria)

    overall_status = ""
    if isinstance(status, Mapping):
        overall_status = str(status.get("overallStatus") or "").strip()
    if overall_status:
        summaries.append(f"Recruitment status: {overall_status}")

    return TrialProtocolSource(
        trial_id=trial_id,
        title=title,
        conditions=conditions,
        summary="\n\n".join(summaries),
        eligibility_text="\n".join(eligibility_lines),
        source_location=f"{CLINICALTRIALS_STUDY_ROOT}/{trial_id}",
    )


def _contains_all_condition_tokens(
    condition: str,
    source: TrialProtocolSource,
) -> bool:
    query_tokens = set(tokenize(condition))
    if not query_tokens:
        return False
    searchable = "\n".join(source.conditions or [source.title])
    return query_tokens.issubset(set(tokenize(searchable)))


class ClinicalTrialsGovCandidateSearch:
    """Search current enrolling studies without requiring a local trial corpus.

    ClinicalTrials.gov supplies the relevance order but not a numeric score. The
    returned score is therefore a reciprocal-rank value used only to merge
    several condition queries. Every query word must appear in the trial's
    declared conditions. Broader disease names and synonyms must be sent as
    separate queries. This prevents an incidental title word or API synonym
    expansion from silently changing the disease being screened.
    """

    retrieval_method = "ClinicalTrials.gov condition search"

    def __init__(
        self,
        cache_dir: str | Path,
        *,
        included_statuses: Sequence[str] = tuple(sorted(DEFAULT_ENROLLING_STATUSES)),
        timeout_seconds: float = 60,
        force_refresh: bool = False,
        fetch_json: JsonFetcher | None = None,
    ) -> None:
        statuses = tuple(
            sorted({str(item).strip().upper() for item in included_statuses if str(item).strip()})
        )
        if not statuses:
            raise ValueError("included_statuses must not be empty")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than zero")
        self.cache_dir = Path(cache_dir)
        self.included_statuses = statuses
        self.timeout_seconds = timeout_seconds
        self.force_refresh = force_refresh
        self._fetch_json = fetch_json or _fetch_json

    def _query(self, condition: str, *, page_size: int) -> Mapping[str, Any]:
        parameters = {
            "query.cond": condition,
            "filter.overallStatus": "|".join(self.included_statuses),
            "pageSize": str(page_size),
            "format": "json",
        }
        url = f"{CLINICALTRIALS_API_ROOT}/studies?{urlencode(parameters)}"
        cache_key = hashlib.sha256(url.encode("utf-8")).hexdigest()
        cache_path = self.cache_dir / f"{cache_key}.json"
        if cache_path.is_file() and not self.force_refresh:
            try:
                cached = json.loads(cache_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                cached = None
            if isinstance(cached, dict):
                response = cached.get("response")
                if isinstance(response, dict):
                    return response

        response = self._fetch_json(url, self.timeout_seconds)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_text(
            cache_path,
            json.dumps(
                {
                    "retrieved_at": datetime.now(timezone.utc).isoformat(),
                    "api_url": url,
                    "response": response,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        return response

    def search(
        self,
        search_conditions: Sequence[str],
        *,
        top_k: int,
    ) -> list[CandidateSearchHit]:
        if top_k < 1:
            raise ValueError("top_k must be at least one")
        conditions = [item.strip() for item in search_conditions if item.strip()]
        if not conditions:
            raise ValueError("at least one non-empty search condition is required")

        query_depth = min(100, max(20, top_k * 5))
        scores: dict[str, float] = {}
        sources: dict[str, TrialProtocolSource] = {}
        for condition_index, condition in enumerate(conditions):
            response = self._query(condition, page_size=query_depth)
            studies = response.get("studies", [])
            if not isinstance(studies, list):
                raise ValueError("ClinicalTrials.gov response has no studies list")
            condition_weight = 1 / (condition_index + 1)
            for api_rank, study in enumerate(studies, start=1):
                if not isinstance(study, Mapping):
                    continue
                source = _study_source(study)
                if source is None:
                    continue
                if api_rank > 2 and not _contains_all_condition_tokens(
                    condition,
                    source,
                ):
                    continue
                sources[source.trial_id] = source
                scores[source.trial_id] = scores.get(source.trial_id, 0.0) + (
                    condition_weight / (20 + api_rank)
                )

        ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
        return [
            CandidateSearchHit(
                rank=rank,
                score=score,
                retrieval_method=self.retrieval_method,
                source=sources[trial_id],
            )
            for rank, (trial_id, score) in enumerate(ranked[:top_k], start=1)
        ]


__all__ = [
    "CLINICALTRIALS_API_ROOT",
    "CLINICALTRIALS_STUDY_ROOT",
    "ClinicalTrialsGovCandidateSearch",
]
