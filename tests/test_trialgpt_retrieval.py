from __future__ import annotations

import json
import gzip

import pytest

from clarifytrial.retrieval import trialgpt

from clarifytrial.retrieval.trialgpt import (
    TrialGPTRetrievalConfig,
    evaluate_rankings,
    inspect_corpus,
    load_qrels,
    load_query_conditions,
    reciprocal_rank_fusion,
)


class _FakeBM25:
    def __init__(self, corpus):
        self.corpus = corpus


def test_trialgpt_config_requires_one_retriever() -> None:
    with pytest.raises(ValueError, match="at least one retriever"):
        TrialGPTRetrievalConfig(
            corpus_name="trec_2021",
            bm25_weight=0,
            medcpt_weight=0,
        )


def test_inspect_corpus_rejects_duplicate_trials(tmp_path) -> None:
    path = tmp_path / "corpus.jsonl"
    row = {"_id": "NCT1", "title": "A", "text": "B", "metadata": {}}
    path.write_text(
        json.dumps(row) + "\n" + json.dumps(row) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="not unique"):
        inspect_corpus(path)


def test_query_conditions_match_trialgpt_shape(tmp_path) -> None:
    path = tmp_path / "id2queries.json"
    path.write_text(
        json.dumps(
            {
                "q1": {
                    "raw": "raw note",
                    "gpt-4-turbo": {"conditions": ["first", "second"]},
                }
            }
        ),
        encoding="utf-8",
    )

    assert load_query_conditions(path, "raw") == {"q1": ("raw note",)}
    assert load_query_conditions(path, "gpt-4-turbo") == {
        "q1": ("first", "second")
    }


def test_load_qrels_requires_official_header(tmp_path) -> None:
    path = tmp_path / "test.tsv"
    path.write_text(
        "query-id\tcorpus-id\tscore\nq1\tNCT1\t2\nq1\tNCT2\t0\n",
        encoding="utf-8",
    )

    assert load_qrels(path) == {"q1": {"NCT1": 2, "NCT2": 0}}


def test_reciprocal_rank_fusion_preserves_official_condition_weighting() -> None:
    fused = reciprocal_rank_fusion(
        bm25_rankings=(("A", "B"), ("C", "A")),
        medcpt_rankings=(("B", "C"), ("A", "C")),
        fusion_k=20,
    )

    assert fused == ["A", "B", "C"]


def test_retrieval_metrics_keep_weighted_and_binary_recall_separate() -> None:
    metrics = evaluate_rankings(
        {"q1": ["eligible", "irrelevant", "excluded"]},
        {"q1": {"eligible": 2, "excluded": 1, "irrelevant": 0}},
        depths=(1, 3),
    )

    first, third = metrics
    assert first.weighted_recall == pytest.approx(2 / 3)
    assert first.binary_recall == pytest.approx(1 / 2)
    assert first.eligible_recall == 1
    assert first.precision == 1
    assert first.ndcg == 1
    assert third.weighted_recall == 1
    assert third.binary_recall == 1
    assert third.eligible_recall == 1
    assert third.precision == pytest.approx(2 / 3)
    assert third.ndcg < 1


def test_metrics_reject_missing_judged_query() -> None:
    with pytest.raises(ValueError, match="missing judged queries"):
        evaluate_rankings({}, {"q1": {"NCT1": 1}}, depths=(10,))


def test_bm25_cache_is_inert_json_and_corrupt_content_is_rebuilt(
    tmp_path, monkeypatch
) -> None:
    corpus = tmp_path / "corpus.jsonl"
    corpus.write_text(
        json.dumps(
            {
                "_id": "NCT1",
                "title": "Lung cancer study",
                "text": "Adults with lung cancer",
                "metadata": {"diseases_list": ["lung cancer"]},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    cache = tmp_path / "bm25-tokenized.json.gz"
    monkeypatch.setattr(
        trialgpt,
        "_optional_bm25_imports",
        lambda: (lambda text: text.split(), _FakeBM25),
    )

    first, first_ids = trialgpt.build_bm25(corpus, cache)
    with gzip.open(cache, "rt", encoding="utf-8") as stream:
        stored = json.load(stream)

    assert stored["format"] == trialgpt.BM25_CACHE_FORMAT
    assert first_ids == ("NCT1",)
    assert first.corpus

    cache.write_bytes(b"not executable and not valid gzip")
    second, second_ids = trialgpt.build_bm25(corpus, cache)
    assert second_ids == ("NCT1",)
    assert second.corpus == first.corpus
