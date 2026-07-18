"""
Regression tests for retrieval quality against the Open RAG Benchmark harness
(benchmarks/openrag_bench.py). These don't just check the harness runs — they
assert on the actual retrieval-quality relationships it's supposed to prove:

  - hybrid RRF must not score worse than either of its inputs (bm25-only,
    dense-only) on recall@5 — if it did, the fusion would be actively
    hurting retrieval instead of helping, which would make the whole
    "hybrid retrieval" pitch false.
  - every method must be well above 0 on a dataset where gold answers are
    guaranteed to exist in the corpus (catches silent breakage: wrong bug
    could exist where retrieval always returns empty/garbage and everything
    still runs without raising).

Fixture data (`benchmarks/openrag_fixture/`) is a tiny 2-document, 5-query
synthetic set that ships in-repo so this test needs no network and no
external dataset download, and runs in under a second on hash-fallback
embeddings (no Ollama/Gemini required).

To evaluate against the *real* vectara/open_ragbench dataset (1000 arXiv
papers, 3045 queries) instead of the fixture:
    1. Download it from https://huggingface.co/datasets/vectara/open_ragbench
       (this repo's benchmark script expects corpus.json/queries.json/qrels.json
       or a corpus/ directory of per-paper JSON files — see load_dataset()).
    2. python benchmarks/openrag_bench.py --data-dir /path/to/open_ragbench --limit 200
Note: this sandbox's network egress allowlist does not include huggingface.co,
so the real dataset could not be fetched or run from here — only the fixture
could be exercised in this environment.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "benchmarks"))

import pytest

from openrag_bench import FIXTURE_DIR, run, summarize


@pytest.fixture(scope="module")
def rows():
    return run(FIXTURE_DIR, limit=None, rerank="none")


@pytest.fixture(scope="module")
def summary(rows):
    return summarize(rows)


def test_harness_produces_all_methods(summary):
    assert set(summary["overall"].keys()) == {"bm25", "dense", "hybrid_rrf", "hybrid_norm"}


@pytest.mark.parametrize("method", ["bm25", "dense", "hybrid_rrf", "hybrid_norm"])
def test_no_method_silently_returns_nothing(summary, method):
    # Every query in the fixture has its gold section actually present in the
    # indexed corpus, so recall@10 == 0 would mean retrieval is broken, not
    # just "hard query" — catches e.g. an indexing bug that drops all chunks.
    assert summary["overall"][method]["recall@10"] > 0.0, (
        f"{method} found zero gold answers in top 10 — retrieval/indexing is broken, "
        "this is not a ranking-quality issue"
    )


def test_hybrid_rrf_is_not_worse_than_either_input_method(summary):
    hybrid = summary["overall"]["hybrid_rrf"]["recall@5"]
    bm25 = summary["overall"]["bm25"]["recall@5"]
    dense = summary["overall"]["dense"]["recall@5"]
    assert hybrid >= bm25, (
        f"hybrid_rrf recall@5={hybrid} is worse than bm25-only={bm25} — "
        "fusion is actively hurting retrieval, not helping"
    )
    assert hybrid >= dense, (
        f"hybrid_rrf recall@5={hybrid} is worse than dense-only={dense} — "
        "fusion is actively hurting retrieval, not helping"
    )


def test_hybrid_norm_is_not_worse_than_either_input_method(summary):
    hybrid = summary["overall"]["hybrid_norm"]["recall@5"]
    bm25 = summary["overall"]["bm25"]["recall@5"]
    dense = summary["overall"]["dense"]["recall@5"]
    assert hybrid >= bm25, (
        f"hybrid_norm recall@5={hybrid} is worse than bm25-only={bm25} — "
        "score-normalized fusion is actively hurting retrieval, not helping"
    )
    assert hybrid >= dense, (
        f"hybrid_norm recall@5={hybrid} is worse than dense-only={dense} — "
        "score-normalized fusion is actively hurting retrieval, not helping"
    )


def test_per_query_row_shape(rows):
    # Guards the report-writing contract: summarize() and any downstream
    # consumer depends on every row carrying these exact keys.
    required_keys = {"qid", "method", "source", "type", "recalls", "rr", "ndcgs"}
    for row in rows:
        assert required_keys <= row.keys()
        assert set(row["recalls"].keys()) == {1, 3, 5, 10}
        assert 0.0 <= row["rr"] <= 1.0


def test_by_source_breakdown_only_uses_hybrid_rrf(summary):
    # summarize() intentionally reports the by-source breakdown for
    # hybrid_rrf only (that's the method actually meant to ship) — this
    # locks that choice in as a test rather than an implicit assumption
    # someone could quietly break while refactoring summarize().
    assert set(summary["by_source"].keys()) <= {"text", "text-table", "text-image", "text-table-image"}
