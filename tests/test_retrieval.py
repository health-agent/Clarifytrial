from clarifytrial.retrieval import BM25Retriever, CriterionStore, SearchDocument


def make_document(document_id: str, text: str) -> SearchDocument:
    return SearchDocument(
        document_id=document_id,
        trial_id="NCT-DEMO",
        criterion_id=f"criterion-{document_id}",
        criterion_type="inclusion",
        raw_text=text,
        source_location=f"protocol:{document_id}",
    )


def test_bm25_returns_stable_rank_and_source_location() -> None:
    store = CriterionStore(
        [
            make_document("b", "recent platelet count is required"),
            make_document("a", "EGFR mutation confirmed by central laboratory"),
        ]
    )
    retriever = BM25Retriever(store)

    first = retriever.search("central EGFR laboratory", top_k=2)
    second = retriever.search("central EGFR laboratory", top_k=2)

    assert [hit.document.document_id for hit in first] == ["a", "b"]
    assert [hit.model_dump() for hit in first] == [hit.model_dump() for hit in second]
    assert first[0].document.source_location == "protocol:a"


def test_store_rejects_duplicate_document_ids() -> None:
    duplicate = make_document("same", "one")
    try:
        CriterionStore([duplicate, duplicate])
    except ValueError as exc:
        assert "unique" in str(exc)
    else:
        raise AssertionError("duplicate document ids must fail")
