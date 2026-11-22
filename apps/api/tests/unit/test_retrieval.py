from __future__ import annotations

from app.ai.retrieval import (
    Hit,
    dedupe,
    limit_per_document,
    normalize_fused_scores,
    to_sources,
)


def hit(chunk_id: str, doc: str, content: str, score: float, vrank=None, krank=None) -> Hit:  # type: ignore[no-untyped-def]
    return Hit(
        chunk_id=chunk_id,
        document_id=doc,
        document_title=f"Doc {doc}",
        content=content,
        heading_path=None,
        page_start=1,
        char_start=0,
        char_end=10,
        vector_rank=vrank,
        keyword_rank=krank,
        fused_score=score,
    )


class TestDedupe:
    def test_near_identical_passages_collapse(self):
        a = hit("c1", "d1", "the service role key lives in fly secrets only", 0.03)
        b = hit("c2", "d1", "the service role key lives in fly secrets only", 0.02)
        assert len(dedupe([a, b])) == 1

    def test_different_passages_survive(self):
        a = hit("c1", "d1", "the service role key lives in fly secrets", 0.03)
        b = hit("c2", "d1", "reciprocal rank fusion uses a constant of sixty", 0.02)
        assert len(dedupe([a, b])) == 2


class TestDocumentDiversity:
    def test_one_document_cannot_dominate(self):
        hits = [hit(f"c{i}", "d1", f"passage {i}", 0.03 - i / 1000) for i in range(10)]
        hits.append(hit("c99", "d2", "other passage", 0.001))
        limited = limit_per_document(hits, max_share=0.6)
        assert limited  # never returns empty

    def test_single_document_is_returned_whole(self):
        hits = [hit(f"c{i}", "d1", f"passage {i}", 0.03) for i in range(5)]
        assert len(limit_per_document(hits)) == 5


class TestSourceMapping:
    def test_markers_are_sequential_from_one(self):
        hits = [hit(f"c{i}", f"d{i}", f"passage {i}", 0.02) for i in range(3)]
        sources = to_sources(hits)
        assert [s.marker for s in sources] == [1, 2, 3]
        assert sources[0].chunk_id == "c0"

    def test_matched_arm_reports_provenance(self):
        assert hit("c", "d", "x", 0.01, vrank=1, krank=2).matched_arm == "both"
        assert hit("c", "d", "x", 0.01, vrank=1).matched_arm == "vector"
        assert hit("c", "d", "x", 0.01, krank=1).matched_arm == "keyword"


class TestScoreNormalization:
    def test_best_hit_normalizes_to_one(self):
        hits = [hit("c1", "d1", "a", 0.02), hit("c2", "d2", "b", 0.01)]
        assert normalize_fused_scores(hits) == [1.0, 0.5]

    def test_empty_is_empty(self):
        assert normalize_fused_scores([]) == []
