from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from clarifytrial.agents import CandidateRelevanceAgent
from clarifytrial.llm import ScriptedStructuredModel
from clarifytrial.preparation import (
    CandidateRelevanceProtocolError,
    ClinicalTrialsGovCandidateSearch,
    review_candidate_relevance,
)
from clarifytrial.trace import TraceRecorder


def _study(
    trial_id: str,
    *,
    title: str,
    conditions: list[str],
    eligibility: str = "Adults may participate.",
) -> dict:
    return {
        "protocolSection": {
            "identificationModule": {
                "nctId": trial_id,
                "briefTitle": title,
            },
            "statusModule": {"overallStatus": "RECRUITING"},
            "conditionsModule": {"conditions": conditions},
            "descriptionModule": {"briefSummary": f"Study of {title}."},
            "eligibilityModule": {
                "sex": "ALL",
                "minimumAge": "18 Years",
                "eligibilityCriteria": eligibility,
            },
        }
    }


def test_official_search_keeps_top_api_results_for_the_relevance_review(
    tmp_path: Path,
) -> None:
    urls: list[str] = []

    def fetch(url: str, _: float) -> dict:
        urls.append(url)
        return {
            "studies": [
                _study(
                    "NCT-GASTRIC-1",
                    title="Gastric outlet obstruction study",
                    conditions=["Gastric Outlet Obstruction"],
                ),
                _study(
                    "NCT-GASTRIC-2",
                    title="Treatment for gastric obstruction",
                    conditions=["Gastric Outlet Obstruction"],
                ),
                _study(
                    "NCT-LYMPHOMA",
                    title="B-cell neoplasm study",
                    conditions=["Pediatric-Type Follicular Lymphoma"],
                ),
            ]
        }

    search = ClinicalTrialsGovCandidateSearch(
        tmp_path / "cache",
        fetch_json=fetch,
    )
    first = search.search(["pyloric stenosis"], top_k=10)
    second = search.search(["pyloric stenosis"], top_k=10)

    assert [item.source.trial_id for item in first] == [
        "NCT-GASTRIC-1",
        "NCT-GASTRIC-2",
    ]
    assert [item.source.trial_id for item in second] == [
        "NCT-GASTRIC-1",
        "NCT-GASTRIC-2",
    ]
    assert len(urls) == 1
    parameters = parse_qs(urlparse(urls[0]).query)
    assert parameters["query.cond"] == ["pyloric stenosis"]
    assert set(parameters["filter.overallStatus"][0].split("|")) == {
        "RECRUITING",
        "NOT_YET_RECRUITING",
        "ENROLLING_BY_INVITATION",
    }


def test_official_search_leaves_a_top_synonym_for_the_relevance_review(
    tmp_path: Path,
) -> None:
    search = ClinicalTrialsGovCandidateSearch(
        tmp_path / "cache",
        fetch_json=lambda _url, _timeout: {
            "studies": [
                _study(
                    "NCT-UROTHELIAL",
                    title="Urothelial cancer treatment",
                    conditions=["Urothelial Cancer"],
                )
            ]
        },
    )

    result = search.search(["bladder cancer"], top_k=3)

    assert [item.source.trial_id for item in result] == ["NCT-UROTHELIAL"]


def test_relevance_review_removes_gallbladder_from_a_bladder_cancer_search(
    tmp_path: Path,
) -> None:
    search = ClinicalTrialsGovCandidateSearch(
        tmp_path / "cache",
        fetch_json=lambda _url, _timeout: {
            "studies": [
                _study(
                    "NCT-GALLBLADDER",
                    title="Radiation Therapy in Unresectable Gall Bladder Cancer",
                    conditions=["Gallbladder Cancer"],
                ),
                _study(
                    "NCT-BLADDER",
                    title="Bladder tumor surgery",
                    conditions=["Bladder Cancer"],
                ),
            ]
        },
    )

    retrieved = search.search(["bladder cancer"], top_k=3)
    reviewer = CandidateRelevanceAgent(
        ScriptedStructuredModel(
            {
                "candidate_relevance_reviewer": lambda _payload: {
                    "decisions": [
                        {
                            "trial_id": "NCT-GALLBLADDER",
                            "relevant": False,
                            "reason": "담낭암은 방광암과 다른 질환이다.",
                        },
                        {
                            "trial_id": "NCT-BLADDER",
                            "relevant": True,
                            "reason": "방광암을 직접 다룬다.",
                        },
                    ]
                }
            }
        )
    )

    result = review_candidate_relevance(
        search_conditions=["bladder cancer"],
        candidate_hits=retrieved,
        reviewer=reviewer,
        requested_count=3,
        trace=TraceRecorder("bladder"),
    )

    assert [item.source.trial_id for item in result] == ["NCT-BLADDER"]
    assert result[0].rank == 1
    assert result[0].retrieval_method.endswith("disease relevance review")


def test_relevance_review_requires_every_retrieved_trial_once(
    tmp_path: Path,
) -> None:
    search = ClinicalTrialsGovCandidateSearch(
        tmp_path / "cache",
        fetch_json=lambda _url, _timeout: {
            "studies": [
                _study("NCT-1", title="Trial one", conditions=["Disease"]),
                _study("NCT-2", title="Trial two", conditions=["Disease"]),
            ]
        },
    )
    retrieved = search.search(["disease"], top_k=2)
    reviewer = CandidateRelevanceAgent(
        ScriptedStructuredModel(
            {
                "candidate_relevance_reviewer": lambda _payload: {
                    "decisions": [
                        {
                            "trial_id": "NCT-1",
                            "relevant": True,
                            "reason": "같은 질환이다.",
                        }
                    ]
                }
            }
        )
    )

    with pytest.raises(CandidateRelevanceProtocolError):
        review_candidate_relevance(
            search_conditions=["disease"],
            candidate_hits=retrieved,
            reviewer=reviewer,
            requested_count=2,
            trace=TraceRecorder("missing-id"),
        )


def test_official_search_returns_empty_when_api_has_no_usable_study(
    tmp_path: Path,
) -> None:
    search = ClinicalTrialsGovCandidateSearch(
        tmp_path / "cache",
        fetch_json=lambda _url, _timeout: {
            "studies": [
                _study(
                    "NCT-NO-CRITERIA",
                    title="No criteria",
                    conditions=["Unknown condition"],
                    eligibility="",
                )
            ]
        },
    )

    assert search.search(["unknown condition"], top_k=5) == []
