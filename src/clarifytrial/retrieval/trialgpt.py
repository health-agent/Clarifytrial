"""Faithful, inspectable reproduction of TrialGPT candidate retrieval."""

from __future__ import annotations

import hashlib
import gzip
import importlib.metadata
import json
import math
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


ARTICLE_MODEL_ID = "ncbi/MedCPT-Article-Encoder"
QUERY_MODEL_ID = "ncbi/MedCPT-Query-Encoder"
DEFAULT_QUERY_TYPE = "gpt-4-turbo"
DEFAULT_FUSION_K = 20
DEFAULT_SEARCH_DEPTH = 2_000
DEFAULT_METRIC_DEPTHS = (10, 50, 100, 500, 1_000, 2_000)
BM25_CACHE_FORMAT = "clarifytrial-bm25-token-cache-v1"


class TrialGPTCorpusEntry(BaseModel):
    model_config = ConfigDict(extra="allow", frozen=True)

    trial_id: str = Field(alias="_id", min_length=1)
    title: str
    text: str
    metadata: dict[str, Any]


class TrialGPTRetrievalConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    corpus_name: str = Field(pattern=r"^(trec_2021|trec_2022)$")
    query_type: str = DEFAULT_QUERY_TYPE
    fusion_k: int = Field(default=DEFAULT_FUSION_K, ge=1)
    search_depth: int = Field(default=DEFAULT_SEARCH_DEPTH, ge=1)
    bm25_weight: float = Field(default=1.0, ge=0)
    medcpt_weight: float = Field(default=1.0, ge=0)
    article_model_id: str = ARTICLE_MODEL_ID
    query_model_id: str = QUERY_MODEL_ID
    article_max_length: int = Field(default=512, ge=1)
    query_max_length: int = Field(default=256, ge=1)
    batch_size: int = Field(default=16, ge=1)
    device: str = "cuda"

    @model_validator(mode="after")
    def at_least_one_retriever(self) -> TrialGPTRetrievalConfig:
        if self.bm25_weight == 0 and self.medcpt_weight == 0:
            raise ValueError("at least one retriever weight must be positive")
        return self


class RetrievalMetricRow(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    depth: int = Field(ge=1)
    weighted_recall: float = Field(ge=0, le=1)
    binary_recall: float = Field(ge=0, le=1)
    eligible_recall: float = Field(ge=0, le=1)
    precision: float = Field(ge=0, le=1)
    ndcg: float = Field(ge=0, le=1)


class TrialGPTRetrievalSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    implementation: str = "clarifytrial-trialgpt-retrieval-v1"
    config: TrialGPTRetrievalConfig
    corpus_path: str
    corpus_sha256: str
    corpus_documents: int
    query_count: int
    judged_query_count: int
    qrel_rows: int
    metric_rows: tuple[RetrievalMetricRow, ...]
    ranking_path: str
    embedding_path: str | None
    runtime: dict[str, str]
    faithfulness_notes: tuple[str, ...]
    elapsed_seconds: float = Field(ge=0)


@dataclass(frozen=True)
class CorpusManifest:
    ids: tuple[str, ...]
    sha256: str
    rows: int


def _read_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def retrieval_runtime() -> dict[str, str]:
    packages = ("faiss-cpu", "nltk", "numpy", "rank-bm25", "torch", "transformers")
    return {name: importlib.metadata.version(name) for name in packages}


def iter_corpus(path: Path) -> Iterable[TrialGPTCorpusEntry]:
    seen: set[str] = set()
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            entry = TrialGPTCorpusEntry.model_validate_json(line)
            if entry.trial_id in seen:
                raise ValueError(
                    f"corpus repeats trial_id {entry.trial_id!r} at line {line_number}"
                )
            seen.add(entry.trial_id)
            yield entry


def inspect_corpus(path: Path) -> CorpusManifest:
    digest = hashlib.sha256()
    ids: list[str] = []
    with path.open("rb") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            digest.update(line)
            try:
                raw = json.loads(line)
                trial_id = str(raw["_id"])
            except (KeyError, TypeError, json.JSONDecodeError) as exc:
                raise ValueError(f"invalid corpus JSON at line {line_number}") from exc
            ids.append(trial_id)
    if len(ids) != len(set(ids)):
        raise ValueError("corpus trial IDs are not unique")
    return CorpusManifest(tuple(ids), digest.hexdigest(), len(ids))


def load_queries(path: Path) -> dict[str, str]:
    rows: dict[str, str] = {}
    with path.open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                raw = json.loads(line)
                query_id = str(raw["_id"])
                text = str(raw["text"])
            except (KeyError, TypeError, json.JSONDecodeError) as exc:
                raise ValueError(f"invalid query JSON at line {line_number}") from exc
            if query_id in rows:
                raise ValueError(f"queries repeat query ID {query_id!r}")
            rows[query_id] = text
    return rows


def load_qrels(path: Path) -> dict[str, dict[str, int]]:
    qrels: dict[str, dict[str, int]] = {}
    with path.open(encoding="utf-8-sig") as stream:
        header = stream.readline().rstrip("\r\n").split("\t")
        expected = ["query-id", "corpus-id", "score"]
        if header != expected:
            raise ValueError(f"unexpected qrels header: {header!r}")
        for line_number, line in enumerate(stream, start=2):
            if not line.strip():
                continue
            fields = line.rstrip("\r\n").split("\t")
            if len(fields) != 3:
                raise ValueError(f"invalid qrel at line {line_number}")
            query_id, trial_id, raw_score = fields
            score = int(raw_score)
            if score < 0:
                raise ValueError("qrel scores must be non-negative")
            query_rows = qrels.setdefault(query_id, {})
            if trial_id in query_rows:
                raise ValueError(
                    f"qrels repeat ({query_id!r}, {trial_id!r}) at line {line_number}"
                )
            query_rows[trial_id] = score
    return qrels


def load_query_conditions(path: Path, query_type: str) -> dict[str, tuple[str, ...]]:
    raw = _read_json(path)
    if not isinstance(raw, dict):
        raise ValueError("id2queries must be a JSON object")
    output: dict[str, tuple[str, ...]] = {}
    for query_id, query_variants in raw.items():
        if not isinstance(query_variants, dict) or query_type not in query_variants:
            raise ValueError(f"query {query_id!r} has no {query_type!r} variant")
        selected = query_variants[query_type]
        if query_type in {"raw", "human_summary"}:
            conditions = (str(selected),)
        elif isinstance(selected, dict) and isinstance(selected.get("conditions"), list):
            conditions = tuple(str(item) for item in selected["conditions"] if str(item))
        elif isinstance(selected, list):
            conditions = tuple(str(item) for item in selected if str(item))
        else:
            raise ValueError(
                f"query {query_id!r} has an invalid {query_type!r} variant"
            )
        output[str(query_id)] = conditions
    return output


def reciprocal_rank_fusion(
    bm25_rankings: Sequence[Sequence[str]],
    medcpt_rankings: Sequence[Sequence[str]],
    *,
    fusion_k: int,
    bm25_weight: float = 1.0,
    medcpt_weight: float = 1.0,
) -> list[str]:
    return [
        trial_id
        for trial_id, _ in reciprocal_rank_fusion_with_scores(
            bm25_rankings,
            medcpt_rankings,
            fusion_k=fusion_k,
            bm25_weight=bm25_weight,
            medcpt_weight=medcpt_weight,
        )
    ]


def reciprocal_rank_fusion_with_scores(
    bm25_rankings: Sequence[Sequence[str]],
    medcpt_rankings: Sequence[Sequence[str]],
    *,
    fusion_k: int,
    bm25_weight: float = 1.0,
    medcpt_weight: float = 1.0,
) -> list[tuple[str, float]]:
    """Return the public rank-fusion order together with inspectable scores."""

    if fusion_k < 1:
        raise ValueError("fusion_k must be positive")
    if len(bm25_rankings) != len(medcpt_rankings):
        raise ValueError("BM25 and MedCPT must have one ranking per condition")
    scores: dict[str, float] = {}
    for condition_index, (bm25_rows, medcpt_rows) in enumerate(
        zip(bm25_rankings, medcpt_rankings, strict=True)
    ):
        condition_weight = 1.0 / (condition_index + 1)
        if bm25_weight > 0:
            for rank, trial_id in enumerate(bm25_rows):
                scores[trial_id] = scores.get(trial_id, 0.0) + (
                    bm25_weight * condition_weight / (rank + fusion_k)
                )
        if medcpt_weight > 0:
            for rank, trial_id in enumerate(medcpt_rows):
                scores[trial_id] = scores.get(trial_id, 0.0) + (
                    medcpt_weight * condition_weight / (rank + fusion_k)
                )
    # Preserve the original TrialGPT reproduction's stable insertion-order
    # tie handling. Adding a secondary trial-ID sort would subtly change the
    # reproduced ranking when scores are equal.
    return sorted(scores.items(), key=lambda item: -item[1])


def evaluate_rankings(
    rankings: Mapping[str, Sequence[str]],
    qrels: Mapping[str, Mapping[str, int]],
    *,
    depths: Sequence[int] = DEFAULT_METRIC_DEPTHS,
) -> tuple[RetrievalMetricRow, ...]:
    if not qrels:
        raise ValueError("qrels are empty")
    missing = sorted(set(qrels) - set(rankings))
    if missing:
        raise ValueError(f"rankings are missing judged queries: {missing[:3]!r}")

    rows: list[RetrievalMetricRow] = []
    for depth in depths:
        if depth < 1:
            raise ValueError("metric depths must be positive")
        weighted_recalls: list[float] = []
        binary_recalls: list[float] = []
        eligible_recalls: list[float] = []
        precisions: list[float] = []
        ndcgs: list[float] = []
        for query_id, judgments in qrels.items():
            ranking = list(rankings[query_id][:depth])
            positive = {trial_id: score for trial_id, score in judgments.items() if score > 0}
            eligible = {trial_id for trial_id, score in judgments.items() if score >= 2}
            total_gain = sum(positive.values())
            weighted_recalls.append(
                sum(positive.get(trial_id, 0) for trial_id in ranking) / total_gain
                if total_gain
                else 0.0
            )
            binary_recalls.append(
                sum(trial_id in positive for trial_id in ranking) / len(positive)
                if positive
                else 0.0
            )
            eligible_recalls.append(
                sum(trial_id in eligible for trial_id in ranking) / len(eligible)
                if eligible
                else 0.0
            )
            precisions.append(sum(trial_id in positive for trial_id in ranking) / depth)
            gains = [positive.get(trial_id, 0) for trial_id in ranking]
            dcg = sum(
                (2**gain - 1) / math.log2(rank + 2)
                for rank, gain in enumerate(gains)
            )
            ideal = sorted(positive.values(), reverse=True)[:depth]
            idcg = sum(
                (2**gain - 1) / math.log2(rank + 2)
                for rank, gain in enumerate(ideal)
            )
            ndcgs.append(dcg / idcg if idcg else 0.0)
        rows.append(
            RetrievalMetricRow(
                depth=depth,
                weighted_recall=sum(weighted_recalls) / len(weighted_recalls),
                binary_recall=sum(binary_recalls) / len(binary_recalls),
                eligible_recall=sum(eligible_recalls) / len(eligible_recalls),
                precision=sum(precisions) / len(precisions),
                ndcg=sum(ndcgs) / len(ndcgs),
            )
        )
    return tuple(rows)


def _optional_bm25_imports() -> tuple[Any, Any]:
    try:
        from nltk import word_tokenize
        from rank_bm25 import BM25Okapi
    except ImportError as exc:
        raise RuntimeError(
            "TrialGPT BM25 dependencies are missing; install .[retrieval-bm25]"
        ) from exc
    return word_tokenize, BM25Okapi


def _optional_dense_imports() -> tuple[Any, Any, Any, Any, Any]:
    try:
        import faiss
        import numpy as np
        import torch
        from transformers import AutoModel, AutoTokenizer
    except ImportError as exc:
        raise RuntimeError(
            "TrialGPT retrieval dependencies are missing; install .[retrieval]"
        ) from exc
    return faiss, np, torch, AutoModel, AutoTokenizer


def _tokenizer(word_tokenize: Callable[..., list[str]], text: str) -> list[str]:
    try:
        return word_tokenize(text.lower())
    except LookupError as exc:
        raise RuntimeError(
            "NLTK punkt data is missing; run python -m nltk.downloader punkt"
        ) from exc


def _read_bm25_cache(
    cache_path: Path,
    *,
    manifest: CorpusManifest,
) -> list[list[str]] | None:
    """Read inert JSON tokens and reject stale or malformed cache content."""

    try:
        with gzip.open(cache_path, "rt", encoding="utf-8") as stream:
            cached = json.load(stream)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError):
        return None
    if not isinstance(cached, dict):
        return None
    if cached.get("format") != BM25_CACHE_FORMAT:
        return None
    if cached.get("corpus_sha256") != manifest.sha256:
        return None
    trial_ids = cached.get("trial_ids")
    if not isinstance(trial_ids, list) or tuple(trial_ids) != manifest.ids:
        return None
    tokenized = cached.get("tokenized_corpus")
    if not isinstance(tokenized, list) or len(tokenized) != manifest.rows:
        return None
    if any(
        not isinstance(document, list)
        or any(not isinstance(token, str) for token in document)
        for document in tokenized
    ):
        return None
    return tokenized


def _write_bm25_cache(
    cache_path: Path,
    *,
    manifest: CorpusManifest,
    tokenized_corpus: list[list[str]],
) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = cache_path.with_suffix(cache_path.suffix + ".part")
    with gzip.open(temporary, "wt", encoding="utf-8", newline="\n") as stream:
        json.dump(
            {
                "format": BM25_CACHE_FORMAT,
                "corpus_sha256": manifest.sha256,
                "trial_ids": list(manifest.ids),
                "tokenized_corpus": tokenized_corpus,
            },
            stream,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        stream.write("\n")
    temporary.replace(cache_path)


def build_bm25(
    corpus_path: Path,
    cache_path: Path,
) -> tuple[Any, tuple[str, ...]]:
    word_tokenize, BM25Okapi = _optional_bm25_imports()
    manifest = inspect_corpus(corpus_path)
    if cache_path.is_file():
        tokenized = _read_bm25_cache(cache_path, manifest=manifest)
        if tokenized is not None:
            return BM25Okapi(tokenized), manifest.ids

    tokenized_corpus: list[list[str]] = []
    for entry in iter_corpus(corpus_path):
        tokens = _tokenizer(word_tokenize, entry.title) * 3
        diseases = entry.metadata.get("diseases_list", [])
        if not isinstance(diseases, list):
            raise ValueError(f"trial {entry.trial_id!r} has invalid diseases_list")
        for disease in diseases:
            tokens.extend(_tokenizer(word_tokenize, str(disease)) * 2)
        tokens.extend(_tokenizer(word_tokenize, entry.text))
        tokenized_corpus.append(tokens)

    _write_bm25_cache(
        cache_path,
        manifest=manifest,
        tokenized_corpus=tokenized_corpus,
    )
    return BM25Okapi(tokenized_corpus), manifest.ids


def build_medcpt_embeddings(
    corpus_path: Path,
    embedding_path: Path,
    ids_path: Path,
    state_path: Path,
    *,
    model_id: str = ARTICLE_MODEL_ID,
    device: str = "cuda",
    batch_size: int = 16,
    max_length: int = 512,
    progress: Callable[[str], None] | None = None,
) -> CorpusManifest:
    _, np, torch, AutoModel, AutoTokenizer = _optional_dense_imports()
    manifest = inspect_corpus(corpus_path)
    if embedding_path.is_file() and ids_path.is_file():
        stored_ids = tuple(_read_json(ids_path))
        embeddings = np.load(embedding_path, mmap_mode="r")
        if stored_ids == manifest.ids and embeddings.shape == (manifest.rows, 768):
            return manifest

    model = AutoModel.from_pretrained(model_id).to(device)
    model.eval()
    tokenizer = AutoTokenizer.from_pretrained(model_id)

    part_path = embedding_path.with_suffix(embedding_path.suffix + ".part")
    next_index = 0
    if part_path.is_file() and state_path.is_file():
        state = _read_json(state_path)
        if (
            state.get("corpus_sha256") == manifest.sha256
            and state.get("model_id") == model_id
            and state.get("rows") == manifest.rows
        ):
            next_index = int(state.get("next_index", 0))
            embeds = np.lib.format.open_memmap(part_path, mode="r+")
        else:
            raise ValueError("partial embedding state does not match this corpus")
    else:
        embedding_path.parent.mkdir(parents=True, exist_ok=True)
        embeds = np.lib.format.open_memmap(
            part_path,
            mode="w+",
            dtype="float32",
            shape=(manifest.rows, 768),
        )

    batch_titles: list[str] = []
    batch_texts: list[str] = []
    batch_start = next_index

    def flush() -> None:
        nonlocal batch_start
        if not batch_titles:
            return
        encoded = tokenizer(
            batch_titles,
            batch_texts,
            truncation=True,
            padding=True,
            return_tensors="pt",
            max_length=max_length,
        ).to(device)
        with torch.inference_mode():
            values = model(**encoded).last_hidden_state[:, 0, :]
        end = batch_start + len(batch_titles)
        embeds[batch_start:end] = values.detach().cpu().numpy()
        embeds.flush()
        batch_start = end
        _write_json(
            state_path,
            {
                "corpus_sha256": manifest.sha256,
                "model_id": model_id,
                "rows": manifest.rows,
                "next_index": end,
            },
        )
        if progress is not None and (end == manifest.rows or end % (batch_size * 50) == 0):
            progress(f"encoded {end}/{manifest.rows}")
        batch_titles.clear()
        batch_texts.clear()

    for index, entry in enumerate(iter_corpus(corpus_path)):
        if index < next_index:
            continue
        if not batch_titles:
            batch_start = index
        batch_titles.append(entry.title)
        batch_texts.append(entry.text)
        if len(batch_titles) >= batch_size:
            flush()
    flush()
    del embeds
    part_path.replace(embedding_path)
    _write_json(ids_path, list(manifest.ids))
    if state_path.exists():
        state_path.unlink()
    return manifest


def _load_query_encoder(config: TrialGPTRetrievalConfig) -> tuple[Any, Any, Any]:
    _, _, torch, AutoModel, AutoTokenizer = _optional_dense_imports()
    model = AutoModel.from_pretrained(config.query_model_id).to(config.device)
    model.eval()
    tokenizer = AutoTokenizer.from_pretrained(config.query_model_id)
    return torch, model, tokenizer


class TrialGPTRuntimeHit(BaseModel):
    """One candidate returned by the reusable TrialGPT search index."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    rank: int = Field(ge=1)
    score: float
    entry: TrialGPTCorpusEntry


class TrialGPTRuntimeSearch:
    """Reuse the reproduced BM25 and MedCPT index for individual patients."""

    def __init__(
        self,
        corpus_path: str | Path,
        cache_dir: str | Path,
        config: TrialGPTRetrievalConfig,
        *,
        progress: Callable[[str], None] | None = None,
    ) -> None:
        self.corpus_path = Path(corpus_path)
        self.config = config
        self._entries = {item.trial_id: item for item in iter_corpus(self.corpus_path)}
        manifest = inspect_corpus(self.corpus_path)
        if manifest.rows == 0:
            raise ValueError("trial corpus must contain at least one document")
        if len(self._entries) != manifest.rows:
            raise ValueError("trial corpus IDs are not unique")
        self._depth = min(config.search_depth, manifest.rows)
        cache = Path(cache_dir) / config.corpus_name

        self._bm25 = None
        self._bm25_ids: tuple[str, ...] = manifest.ids
        self._word_tokenize = None
        if config.bm25_weight > 0:
            self._bm25, self._bm25_ids = build_bm25(
                self.corpus_path,
                cache / "bm25-tokenized.json.gz",
            )
            self._word_tokenize, _ = _optional_bm25_imports()

        self._dense_index = None
        self._dense_ids: tuple[str, ...] = manifest.ids
        self._torch = self._query_model = self._query_tokenizer = None
        if config.medcpt_weight > 0:
            embedding_path = cache / "medcpt-article.npy"
            ids_path = cache / "medcpt-trial-ids.json"
            state_path = cache / "medcpt-build-state.json"
            build_medcpt_embeddings(
                self.corpus_path,
                embedding_path,
                ids_path,
                state_path,
                model_id=config.article_model_id,
                device=config.device,
                batch_size=config.batch_size,
                max_length=config.article_max_length,
                progress=progress,
            )
            faiss, np, _, _, _ = _optional_dense_imports()
            self._dense_ids = tuple(_read_json(ids_path))
            embeddings = np.load(embedding_path)
            if embeddings.shape != (manifest.rows, 768):
                raise ValueError(
                    f"unexpected MedCPT embedding shape: {embeddings.shape!r}"
                )
            self._dense_index = faiss.IndexFlatIP(768)
            self._dense_index.add(embeddings)
            (
                self._torch,
                self._query_model,
                self._query_tokenizer,
            ) = _load_query_encoder(config)

    def search(
        self,
        search_conditions: Sequence[str],
        *,
        top_k: int,
    ) -> list[TrialGPTRuntimeHit]:
        """Search one patient's stated conditions without using any gold labels."""

        if top_k < 1:
            raise ValueError("top_k must be at least one")
        conditions = [item.strip() for item in search_conditions if item.strip()]
        if not conditions:
            raise ValueError("at least one non-empty search condition is required")

        if self._bm25 is not None and self._word_tokenize is not None:
            bm25_rankings = [
                self._bm25.get_top_n(
                    _tokenizer(self._word_tokenize, condition),
                    self._bm25_ids,
                    n=self._depth,
                )
                for condition in conditions
            ]
        else:
            bm25_rankings = [[] for _ in conditions]

        dense_ready = all(
            item is not None
            for item in (
                self._dense_index,
                self._torch,
                self._query_model,
                self._query_tokenizer,
            )
        )
        if dense_ready:
            encoded = self._query_tokenizer(
                conditions,
                truncation=True,
                padding=True,
                return_tensors="pt",
                max_length=self.config.query_max_length,
            ).to(self.config.device)
            with self._torch.inference_mode():
                query_embeddings = (
                    self._query_model(**encoded)
                    .last_hidden_state[:, 0, :]
                    .detach()
                    .cpu()
                    .numpy()
                )
            _, indices = self._dense_index.search(
                query_embeddings,
                k=self._depth,
            )
            medcpt_rankings = [
                [self._dense_ids[index] for index in row] for row in indices
            ]
        else:
            medcpt_rankings = [[] for _ in conditions]

        fused = reciprocal_rank_fusion_with_scores(
            bm25_rankings,
            medcpt_rankings,
            fusion_k=self.config.fusion_k,
            bm25_weight=self.config.bm25_weight,
            medcpt_weight=self.config.medcpt_weight,
        )
        return [
            TrialGPTRuntimeHit(
                rank=rank,
                score=score,
                entry=self._entries[trial_id],
            )
            for rank, (trial_id, score) in enumerate(fused[:top_k], start=1)
        ]


def run_trialgpt_retrieval(
    dataset_dir: Path,
    cache_dir: Path,
    output_dir: Path,
    config: TrialGPTRetrievalConfig,
    *,
    progress: Callable[[str], None] | None = None,
) -> Path:
    started = time.perf_counter()
    corpus_path = dataset_dir / "corpus.jsonl"
    queries_path = dataset_dir / "queries.jsonl"
    query_variants_path = dataset_dir / "id2queries.json"
    qrels_path = dataset_dir / "qrels" / "test.tsv"
    for required in (corpus_path, queries_path, query_variants_path, qrels_path):
        if not required.is_file():
            raise FileNotFoundError(required)

    manifest = inspect_corpus(corpus_path)
    queries = load_queries(queries_path)
    qrels = load_qrels(qrels_path)
    query_conditions = load_query_conditions(query_variants_path, config.query_type)
    judged_query_ids = [query_id for query_id in queries if query_id in qrels]
    missing_variants = sorted(set(judged_query_ids) - set(query_conditions))
    if missing_variants:
        raise ValueError(f"missing query variants: {missing_variants[:3]!r}")

    corpus_cache = cache_dir / config.corpus_name
    bm25 = None
    bm25_trial_ids: tuple[str, ...] = manifest.ids
    word_tokenize = None
    if config.bm25_weight > 0:
        bm25, bm25_trial_ids = build_bm25(
            corpus_path,
            corpus_cache / "bm25-tokenized.json.gz",
        )
        word_tokenize, _ = _optional_bm25_imports()

    embedding_path: Path | None = None
    medcpt_trial_ids: tuple[str, ...] = manifest.ids
    dense_index = None
    torch = query_model = query_tokenizer = None
    if config.medcpt_weight > 0:
        embedding_path = corpus_cache / "medcpt-article.npy"
        ids_path = corpus_cache / "medcpt-trial-ids.json"
        state_path = corpus_cache / "medcpt-build-state.json"
        build_medcpt_embeddings(
            corpus_path,
            embedding_path,
            ids_path,
            state_path,
            model_id=config.article_model_id,
            device=config.device,
            batch_size=config.batch_size,
            max_length=config.article_max_length,
            progress=progress,
        )
        faiss, np, _, _, _ = _optional_dense_imports()
        medcpt_trial_ids = tuple(_read_json(ids_path))
        embeddings = np.load(embedding_path)
        if embeddings.shape != (manifest.rows, 768):
            raise ValueError(f"unexpected MedCPT embedding shape: {embeddings.shape!r}")
        dense_index = faiss.IndexFlatIP(768)
        dense_index.add(embeddings)
        torch, query_model, query_tokenizer = _load_query_encoder(config)

    rankings: dict[str, list[str]] = {}
    depth = min(config.search_depth, manifest.rows)
    for query_offset, query_id in enumerate(judged_query_ids, start=1):
        conditions = query_conditions[query_id]
        if not conditions:
            rankings[query_id] = []
            continue
        if bm25 is not None and word_tokenize is not None:
            bm25_rankings = []
            for condition in conditions:
                tokens = _tokenizer(word_tokenize, condition)
                bm25_rankings.append(
                    bm25.get_top_n(tokens, bm25_trial_ids, n=depth)
                )
        else:
            bm25_rankings = [[] for _ in conditions]

        if all(
            item is not None
            for item in (dense_index, torch, query_model, query_tokenizer)
        ):
            encoded = query_tokenizer(
                list(conditions),
                truncation=True,
                padding=True,
                return_tensors="pt",
                max_length=config.query_max_length,
            ).to(config.device)
            with torch.inference_mode():
                query_embeddings = (
                    query_model(**encoded)
                    .last_hidden_state[:, 0, :]
                    .detach()
                    .cpu()
                    .numpy()
                )
            _, indices = dense_index.search(query_embeddings, k=depth)
            medcpt_rankings = [
                [medcpt_trial_ids[index] for index in row]
                for row in indices
            ]
        else:
            medcpt_rankings = [[] for _ in conditions]
        fused = reciprocal_rank_fusion(
            bm25_rankings,
            medcpt_rankings,
            fusion_k=config.fusion_k,
            bm25_weight=config.bm25_weight,
            medcpt_weight=config.medcpt_weight,
        )
        rankings[query_id] = fused[:depth]
        if progress is not None:
            progress(f"retrieved query {query_offset}/{len(judged_query_ids)}")

    output_dir.mkdir(parents=True, exist_ok=True)
    ranking_path = output_dir / "rankings.json"
    _write_json(ranking_path, rankings)
    metrics = evaluate_rankings(rankings, qrels)
    summary = TrialGPTRetrievalSummary(
        config=config,
        corpus_path=str(corpus_path),
        corpus_sha256=manifest.sha256,
        corpus_documents=manifest.rows,
        query_count=len(queries),
        judged_query_count=len(judged_query_ids),
        qrel_rows=sum(len(rows) for rows in qrels.values()),
        metric_rows=metrics,
        ranking_path=str(ranking_path),
        embedding_path=str(embedding_path) if embedding_path is not None else None,
        runtime=retrieval_runtime(),
        faithfulness_notes=(
            "TrialGPT public GPT-4 Turbo query conditions are used without regeneration.",
            "BM25 field weighting, MedCPT encoders, maximum lengths, search depth, "
            "condition weighting, and reciprocal-rank fusion follow the public code.",
            "Article embeddings are computed in deterministic batches instead of the "
            "public code's one-document loop; model.eval() is set explicitly.",
        ),
        elapsed_seconds=time.perf_counter() - started,
    )
    summary_path = output_dir / "summary.json"
    _write_json(summary_path, summary.model_dump(mode="json"))
    return summary_path
