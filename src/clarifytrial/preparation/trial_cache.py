"""Persistent cache for validated trial-criterion structures."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from uuid import uuid4

from pydantic import Field

from ..contracts import ContractModel, NextAction
from ..llm.prompts import repository_prompt_loader
from ..trace import TraceRecorder
from ..workflow import ScreeningTrial
from .contracts import TrialProtocolSource
from .trial_protocol import (
    DeclaredInformationNeed,
    PreparedInformationNeed,
    PreparedTrial,
    TrialProtocolStructurerAgent,
)


_CACHE_FORMAT_VERSION = 1
_VALIDATION_VERSION = "trial-protocol-source-validation-v1"


class CachedInformationNeed(ContractModel):
    fact_key: str = Field(min_length=1)
    description: str = Field(min_length=1)
    acceptable_actions: list[NextAction] = Field(min_length=1)
    criterion_id: str = Field(min_length=1)


class TrialProtocolCacheEntry(ContractModel):
    format_version: int = _CACHE_FORMAT_VERSION
    cache_key: str = Field(min_length=64, max_length=64)
    trial_id: str = Field(min_length=1)
    source_sha256: str = Field(min_length=64, max_length=64)
    prompt_sha256: str = Field(min_length=64, max_length=64)
    prepared_sha256: str = Field(min_length=64, max_length=64)
    model_label: str = Field(min_length=1)
    trial: ScreeningTrial
    needs: list[CachedInformationNeed] = Field(default_factory=list)


class TrialProtocolCacheStats(ContractModel):
    reused_trial_count: int = Field(default=0, ge=0)
    newly_structured_trial_count: int = Field(default=0, ge=0)
    saved_trial_count: int = Field(default=0, ge=0)
    invalid_cache_file_count: int = Field(default=0, ge=0)
    cache_write_failure_count: int = Field(default=0, ge=0)


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _known_needs_payload(
    known_needs: dict[str, DeclaredInformationNeed] | None,
) -> list[dict[str, object]]:
    return [
        {
            "fact_key": item.fact_key,
            "description": item.description,
            "acceptable_actions": [
                action.value for action in item.acceptable_actions
            ],
        }
        for item in sorted((known_needs or {}).values(), key=lambda value: value.fact_key)
    ]


def _prepared_payload(
    trial: ScreeningTrial,
    needs: list[CachedInformationNeed],
) -> dict[str, object]:
    return {
        "trial": trial.model_dump(mode="json"),
        "needs": [item.model_dump(mode="json") for item in needs],
    }


class TrialProtocolCache:
    """Reuse one validated criterion structure while its inputs stay unchanged."""

    def __init__(self, directory: str | Path, *, model_label: str) -> None:
        self.directory = Path(directory)
        self.model_label = model_label
        prompt_text = repository_prompt_loader()(
            TrialProtocolStructurerAgent.prompt_id
        )
        self.prompt_sha256 = _sha256_text(prompt_text)
        self._reused = 0
        self._new = 0
        self._saved = 0
        self._invalid = 0
        self._write_failures = 0

    @property
    def stats(self) -> TrialProtocolCacheStats:
        return TrialProtocolCacheStats(
            reused_trial_count=self._reused,
            newly_structured_trial_count=self._new,
            saved_trial_count=self._saved,
            invalid_cache_file_count=self._invalid,
            cache_write_failure_count=self._write_failures,
        )

    def _source_sha256(self, source: TrialProtocolSource) -> str:
        return _sha256_text(_canonical_json(source.model_dump(mode="json")))

    def _cache_key(
        self,
        source: TrialProtocolSource,
        known_needs: dict[str, DeclaredInformationNeed] | None,
    ) -> tuple[str, str]:
        source_sha256 = self._source_sha256(source)
        key = _sha256_text(
            _canonical_json(
                {
                    "format_version": _CACHE_FORMAT_VERSION,
                    "validation_version": _VALIDATION_VERSION,
                    "source_sha256": source_sha256,
                    "known_information_needs": _known_needs_payload(known_needs),
                    "prompt_sha256": self.prompt_sha256,
                    "model_label": self.model_label,
                }
            )
        )
        return key, source_sha256

    def _path(self, source: TrialProtocolSource, cache_key: str) -> Path:
        safe_trial_id = "".join(
            char if char.isalnum() or char in "-_" else "-"
            for char in source.trial_id
        )[:48]
        return self.directory / f"{safe_trial_id}-{cache_key[:24]}.json"

    @staticmethod
    def _prepared(entry: TrialProtocolCacheEntry) -> PreparedTrial:
        return PreparedTrial(
            trial=entry.trial,
            needs=tuple(
                PreparedInformationNeed(
                    fact_key=item.fact_key,
                    description=item.description,
                    acceptable_actions=tuple(item.acceptable_actions),
                    criterion_id=item.criterion_id,
                )
                for item in entry.needs
            ),
        )

    def _read(
        self,
        *,
        path: Path,
        cache_key: str,
        source_sha256: str,
        trial_id: str,
    ) -> TrialProtocolCacheEntry | None:
        if not path.is_file():
            return None
        try:
            entry = TrialProtocolCacheEntry.model_validate_json(
                path.read_text(encoding="utf-8")
            )
        except (OSError, ValueError):
            self._invalid += 1
            return None
        if (
            entry.format_version != _CACHE_FORMAT_VERSION
            or entry.cache_key != cache_key
            or entry.source_sha256 != source_sha256
            or entry.prompt_sha256 != self.prompt_sha256
            or entry.model_label != self.model_label
            or entry.trial_id != trial_id
            or entry.trial.trial_id != trial_id
            or entry.prepared_sha256
            != _sha256_text(
                _canonical_json(_prepared_payload(entry.trial, entry.needs))
            )
        ):
            self._invalid += 1
            return None
        return entry

    def _write(self, path: Path, entry: TrialProtocolCacheEntry) -> bool:
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(
                entry.model_dump_json(indent=2) + "\n",
                encoding="utf-8",
            )
            temporary.replace(path)
        except OSError:
            self._write_failures += 1
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            return False
        self._saved += 1
        return True

    def get_or_structure(
        self,
        source: TrialProtocolSource,
        *,
        known_needs: dict[str, DeclaredInformationNeed] | None,
        trace: TraceRecorder,
        build: Callable[[], PreparedTrial],
    ) -> PreparedTrial:
        """Return a validated saved structure or call ``build`` exactly once."""

        cache_key, source_sha256 = self._cache_key(source, known_needs)
        path = self._path(source, cache_key)
        entry = self._read(
            path=path,
            cache_key=cache_key,
            source_sha256=source_sha256,
            trial_id=source.trial_id,
        )
        if entry is not None:
            self._reused += 1
            trace.record(
                cycle=0,
                actor="trial_protocol_cache",
                event="trial_protocol_reused",
                input_refs=[source.trial_id, source.source_location],
                output={
                    "cache_file": str(path),
                    "criterion_count": len(entry.trial.criteria),
                },
            )
            return self._prepared(entry)

        self._new += 1
        prepared = build()
        cached_needs = [
            CachedInformationNeed(
                fact_key=item.fact_key,
                description=item.description,
                acceptable_actions=list(item.acceptable_actions),
                criterion_id=item.criterion_id,
            )
            for item in prepared.needs
        ]
        entry = TrialProtocolCacheEntry(
            cache_key=cache_key,
            trial_id=source.trial_id,
            source_sha256=source_sha256,
            prompt_sha256=self.prompt_sha256,
            prepared_sha256=_sha256_text(
                _canonical_json(
                    _prepared_payload(prepared.trial, cached_needs)
                )
            ),
            model_label=self.model_label,
            trial=prepared.trial,
            needs=cached_needs,
        )
        saved = self._write(path, entry)
        trace.record(
            cycle=0,
            actor="trial_protocol_cache",
            event=(
                "trial_protocol_saved"
                if saved
                else "trial_protocol_cache_write_failed"
            ),
            input_refs=[source.trial_id, source.source_location],
            output={
                "cache_file": str(path),
                "criterion_count": len(prepared.trial.criteria),
            },
        )
        return prepared


__all__ = [
    "TrialProtocolCache",
    "TrialProtocolCacheEntry",
    "TrialProtocolCacheStats",
]
