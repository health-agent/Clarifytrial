from __future__ import annotations

from pathlib import Path
from urllib.parse import parse_qs, urlparse

from clarifytrial.preparation import ClinicalTrialsGovCandidateSearch


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


def test_official_search_keeps_api_synonyms_and_removes_late_word_overlap(
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
    assert first[0].source.eligibility_text.startswith(
        "Sex: ALL\nMinimum Age: 18 Years\n"
    )
    assert first[0].source.source_location.endswith("/NCT-GASTRIC-1")


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
