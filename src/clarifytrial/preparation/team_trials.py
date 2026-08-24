"""Load the team's ClinicalTrials.gov JSONL snapshot for candidate search."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Iterable, Iterator
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import Request, urlopen

from pydantic import Field

from ..contracts import ContractModel
from .candidate_search import InMemoryCandidateSearch
from .contracts import TrialProtocolSource


TEAM_TRIALS_COMMIT = "01b3dd9ee6ca65acd485fa686edda0a08eecc50e"
TEAM_TRIALS_URL = (
    "https://raw.githubusercontent.com/Seohvvan/Healthcare/"
    f"{TEAM_TRIALS_COMMIT}/data/trials.jsonl"
)
TEAM_TRIALS_SHA256 = (
    "95e2551741b82db9e0729e8081af85fe0de559c9cad850b41d849f315bbf889c"
)

# These states may still accept a new participant. ACTIVE_NOT_RECRUITING is
# deliberately excluded because the study is active but no longer enrolling.
DEFAULT_ENROLLING_STATUSES = frozenset(
    {
        "RECRUITING",
        "NOT_YET_RECRUITING",
        "ENROLLING_BY_INVITATION",
    }
)


class TeamTrialRecord(ContractModel):
    """One row in the public team snapshot."""

    nct_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    conditions: list[str] = Field(min_length=1)
    brief_summary: str = ""
    eligibility_text: str = Field(min_length=1)
    sex: str | None = None
    minimum_age: str | None = None
    maximum_age: str | None = None
    overall_status: str = Field(min_length=1)
    phase: list[str] = Field(default_factory=list)


class TeamTrialCorpusSummary(ContractModel):
    """Counts needed to show exactly which trials entered candidate search."""

    source_path: str
    source_sha256: str
    row_count: int = Field(ge=0)
    status_counts: dict[str, int]
    included_statuses: list[str]
    included_trial_count: int = Field(ge=0)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def iter_team_trial_records(path: str | Path) -> Iterator[TeamTrialRecord]:
    """Yield validated rows and reject repeated NCT identifiers."""

    source = Path(path)
    seen: set[str] = set()
    with source.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                record = TeamTrialRecord.model_validate_json(line)
            except Exception as error:
                raise ValueError(
                    f"invalid team trial row at line {line_number}: {error}"
                ) from error
            if record.nct_id in seen:
                raise ValueError(
                    f"team trial corpus repeats {record.nct_id!r} at line "
                    f"{line_number}"
                )
            seen.add(record.nct_id)
            yield record


def inspect_team_trial_corpus(
    path: str | Path,
    *,
    included_statuses: Iterable[str] = DEFAULT_ENROLLING_STATUSES,
) -> TeamTrialCorpusSummary:
    """Validate a snapshot and count rows before and after status filtering."""

    source = Path(path)
    statuses = frozenset(str(item).strip().upper() for item in included_statuses)
    if not statuses:
        raise ValueError("included_statuses must not be empty")
    records = list(iter_team_trial_records(source))
    status_counts = Counter(item.overall_status.upper() for item in records)
    return TeamTrialCorpusSummary(
        source_path=str(source.resolve()),
        source_sha256=_sha256(source),
        row_count=len(records),
        status_counts=dict(sorted(status_counts.items())),
        included_statuses=sorted(statuses),
        included_trial_count=sum(
            item.overall_status.upper() in statuses for item in records
        ),
    )


def team_trial_sources(
    path: str | Path,
    *,
    included_statuses: Iterable[str] = DEFAULT_ENROLLING_STATUSES,
) -> tuple[TrialProtocolSource, ...]:
    """Convert eligible rows to the common raw-protocol search contract."""

    source = Path(path)
    statuses = frozenset(str(item).strip().upper() for item in included_statuses)
    if not statuses:
        raise ValueError("included_statuses must not be empty")
    converted = []
    for item in iter_team_trial_records(source):
        if item.overall_status.upper() not in statuses:
            continue
        converted.append(
            TrialProtocolSource(
                trial_id=item.nct_id,
                title=item.title,
                conditions=item.conditions,
                summary=item.brief_summary,
                eligibility_text=item.eligibility_text,
                source_location=f"{source.resolve()}#nct_id={item.nct_id}",
            )
        )
    if not converted:
        raise ValueError("no trials remain after recruitment-status filtering")
    return tuple(converted)


class TeamTrialCandidateSearch(InMemoryCandidateSearch):
    """Dependency-free BM25 search over the validated team snapshot."""

    retrieval_method = "team-jsonl-bm25"

    def __init__(
        self,
        path: str | Path,
        *,
        included_statuses: Iterable[str] = DEFAULT_ENROLLING_STATUSES,
    ) -> None:
        self.summary = inspect_team_trial_corpus(
            path,
            included_statuses=included_statuses,
        )
        super().__init__(
            team_trial_sources(path, included_statuses=included_statuses)
        )


def prepare_team_trial_corpus(
    destination: str | Path,
    *,
    force: bool = False,
) -> tuple[Path, Path]:
    """Download the pinned public snapshot and write inspectable metadata."""

    target = Path(destination)
    metadata_path = target.with_name(target.stem + "-metadata.json")
    if force or not target.is_file():
        request = Request(
            TEAM_TRIALS_URL,
            headers={"User-Agent": "ClarifyTrial-research/0.1"},
        )
        with urlopen(request, timeout=120) as response:
            payload = response.read()
        digest = hashlib.sha256(payload).hexdigest()
        if digest != TEAM_TRIALS_SHA256:
            raise ValueError(
                "downloaded team trial snapshot does not match the pinned SHA256"
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(target.suffix + ".part")
        temporary.write_bytes(payload)
        temporary.replace(target)

    summary = inspect_team_trial_corpus(target)
    if summary.source_sha256 != TEAM_TRIALS_SHA256:
        raise ValueError("local team trial snapshot does not match the pinned SHA256")
    metadata = {
        "source_url": TEAM_TRIALS_URL,
        "source_commit": TEAM_TRIALS_COMMIT,
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        **summary.model_dump(mode="json"),
    }
    temporary_metadata = metadata_path.with_suffix(metadata_path.suffix + ".part")
    temporary_metadata.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary_metadata.replace(metadata_path)
    return target, metadata_path


__all__ = [
    "DEFAULT_ENROLLING_STATUSES",
    "TEAM_TRIALS_COMMIT",
    "TEAM_TRIALS_SHA256",
    "TEAM_TRIALS_URL",
    "TeamTrialCandidateSearch",
    "TeamTrialCorpusSummary",
    "TeamTrialRecord",
    "inspect_team_trial_corpus",
    "iter_team_trial_records",
    "prepare_team_trial_corpus",
    "team_trial_sources",
]
