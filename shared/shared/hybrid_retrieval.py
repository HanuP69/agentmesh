"""Reciprocal Rank Fusion (RRF) — combines ranked lists from dense (cosine)
and sparse (BM25) retrieval into one ranking without needing to normalize
raw scores across the two systems. Standard formula:
    RRF(d) = sum over rankers r of 1 / (k + rank_r(d))
"""
from typing import Dict, List, Tuple

from shared.config import settings


def reciprocal_rank_fusion(
    ranked_lists: List[List[str]], k: int = None, weights: List[float] = None
) -> List[Tuple[str, float]]:
    k = k or settings.RRF_K
    weights = weights or [1.0] * len(ranked_lists)
    scores: Dict[str, float] = {}
    for w, ranked in zip(weights, ranked_lists):
        for rank, doc_id in enumerate(ranked, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + w * (1.0 / (k + rank))
    return sorted(scores.items(), key=lambda x: x[1], reverse=True)


def score_normalized_fusion(
    scored_lists: List[List[Tuple[str, float]]], weights: List[float] = None
) -> List[Tuple[str, float]]:
    """Alternative to RRF that fuses actual retrieval scores (min-max
    normalized to [0,1] per list) instead of just rank position.

    RRF only ever "sees" rank, not how strong a match was — so a doc a
    ranker is barely confident about but that both rankers surface can
    outscore a doc one ranker was extremely confident about but the other
    missed entirely (a doc absent from a list contributes 0, but a doc at
    rank 30 in a 30-length list contributes almost the same as at rank 25).
    Normalizing and summing actual scores preserves "how strong", not just
    "was it there".

    scored_lists: e.g. [[(doc_id, cosine_score), ...], [(doc_id, bm25_score), ...]]
    """
    weights = weights or [1.0] * len(scored_lists)
    scores: Dict[str, float] = {}
    for w, scored in zip(weights, scored_lists):
        if not scored:
            continue
        vals = [s for _, s in scored]
        lo, hi = min(vals), max(vals)
        span = (hi - lo) or 1e-9
        for doc_id, s in scored:
            norm = (s - lo) / span
            scores[doc_id] = scores.get(doc_id, 0.0) + w * norm
    return sorted(scores.items(), key=lambda x: x[1], reverse=True)


class HybridRetriever:
    """Combines a per-shard dense vector index with a per-shard BM25 index,
    fused via RRF. One instance per modality."""

    def __init__(self, redis_client=None):
        self._redis = redis_client
        # modality -> shard -> BM25Index (or RedisBM25Index)
        self.bm25: Dict[str, Dict[str, "BM25Index"]] = {"text": {}, "table": {}, "image": {}}

    def index_doc(self, modality: str, shard: str, doc_id: str, text: str, metadata: dict):
        from shared.bm25 import BM25Index, RedisBM25Index

        if self._redis is not None:
            idx = self.bm25[modality].get(shard)
            if idx is None:
                idx = RedisBM25Index(self._redis, modality, shard)
                self.bm25[modality][shard] = idx
        else:
            idx = self.bm25[modality].setdefault(shard, BM25Index())
        idx.add(doc_id, text, metadata)

    def sparse_search(self, modality: str, shard: str, query: str, top_k: int) -> List[dict]:
        if self._redis is not None and shard not in self.bm25[modality]:
            from shared.bm25 import RedisBM25Index
            self.bm25[modality][shard] = RedisBM25Index(self._redis, modality, shard)
        idx = self.bm25[modality].get(shard)
        if idx is None:
            return []
        hits = idx.search(query, top_k)
        return [{"id": doc_id, "score": score, "metadata": meta} for doc_id, score, meta in hits]

    def hybrid_search(
        self,
        dense_hits: List[dict],
        sparse_hits: List[dict],
        top_k: int = 5,
    ) -> List[dict]:
        """dense_hits / sparse_hits: [{"id","score","metadata"}, ...] already
        sorted best-first. Returns RRF-fused, reranked-by-metadata list."""
        dense_ids = [h["id"] for h in dense_hits]
        sparse_ids = [h["id"] for h in sparse_hits]
        
        from shared.embeddings import is_real_embedder
        if is_real_embedder():
            weights = [settings.HYBRID_DENSE_WEIGHT, settings.HYBRID_SPARSE_WEIGHT]
        else:
            weights = [0.0, 1.0]  # ignore random/stub dense results completely

        fused = reciprocal_rank_fusion(
            [dense_ids, sparse_ids],
            weights=weights,
        )
        meta_by_id = {h["id"]: h["metadata"] for h in dense_hits + sparse_hits}
        return [
            {"id": doc_id, "score": score, "metadata": meta_by_id.get(doc_id, {})}
            for doc_id, score in fused[:top_k]
        ]
