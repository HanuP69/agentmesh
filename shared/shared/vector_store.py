"""Per-modality vector index abstraction. Supports pgvector or Qdrant via a
common interface; falls back to an in-memory cosine index for local/dev/test
runs without infra."""
import math
from typing import List, Tuple

try:
    import numpy as np
    _HAS_NUMPY = True
except ImportError:
    _HAS_NUMPY = False


class InMemoryVectorIndex:
    def __init__(self):
        self._items: List[Tuple[str, list, dict]] = []  # Fallback store
        self._ids: List[str] = []
        self._vectors: List[list] = []
        self._metadata: List[dict] = []
        self._matrix = None
        self._norms = None

    def upsert(self, doc_id: str, vector: list, metadata: dict) -> None:
        if _HAS_NUMPY:
            self._ids.append(doc_id)
            self._vectors.append(vector)
            self._metadata.append(metadata)
            self._matrix = None  # Invalidate cached matrix
        else:
            self._items.append((doc_id, vector, metadata))

    @staticmethod
    def _cosine(a: list, b: list) -> float:
        if not a or not b or len(a) != len(b):
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a))
        nb = math.sqrt(sum(y * y for y in b))
        return dot / (na * nb) if na and nb else 0.0

    def _ensure_matrix(self):
        if self._matrix is None and self._vectors:
            self._matrix = np.array(self._vectors, dtype=np.float32)
            self._norms = np.linalg.norm(self._matrix, axis=1)
            self._norms[self._norms == 0] = 1e-10

    def search(self, query_vector: list, top_k: int = 5) -> List[dict]:
        if _HAS_NUMPY:
            if not self._vectors:
                return []
            self._ensure_matrix()
            q = np.array(query_vector, dtype=np.float32)
            q_norm = np.linalg.norm(q)
            if q_norm == 0:
                q_norm = 1e-10
            scores = np.dot(self._matrix, q) / (self._norms * q_norm)
            top_indices = np.argsort(scores)[::-1][:top_k]
            
            results = []
            for idx in top_indices:
                results.append({
                    "id": self._ids[idx],
                    "score": float(scores[idx]),
                    "metadata": self._metadata[idx]
                })
            return results
        else:
            scored = [
                {"id": i, "score": self._cosine(query_vector, v), "metadata": m}
                for i, v, m in self._items
            ]
            scored.sort(key=lambda x: x["score"], reverse=True)
            return scored[:top_k]


class MultiModalVectorStore:
    """One index per modality (text / table / image), keyed by shard id so
    it composes with the consistent-hash routing layer."""

    def __init__(self):
        self._indices = {"text": {}, "table": {}, "image": {}}

    def _shard_index(self, modality: str, shard: str) -> InMemoryVectorIndex:
        return self._indices[modality].setdefault(shard, InMemoryVectorIndex())

    def upsert(self, modality: str, shard: str, doc_id: str, vector: list, metadata: dict) -> None:
        self._shard_index(modality, shard).upsert(doc_id, vector, metadata)

    def search(self, modality: str, shard: str, query_vector: list, top_k: int = 5) -> List[dict]:
        return self._shard_index(modality, shard).search(query_vector, top_k)


def create_vector_store(backend: str, dsn: str):
    if backend == "pgvector":
        from shared.pgvector_store import PGVectorMultiModalStore
        return PGVectorMultiModalStore(dsn)
    return MultiModalVectorStore()

