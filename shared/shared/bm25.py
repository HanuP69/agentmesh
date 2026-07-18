"""BM25 sparse retrieval, implemented from scratch (Okapi BM25). Used as the
sparse leg of hybrid retrieval, fused with dense cosine search via RRF."""
import math
import re
from collections import Counter
from typing import Dict, List, Tuple

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> List[str]:
    return _TOKEN_RE.findall(text.lower())


class BM25Index:
    """Okapi BM25 with standard params (k1=1.5, b=0.75)."""

    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.doc_ids: List[str] = []
        self.doc_tokens: List[List[str]] = []
        self.doc_freqs: List[Counter] = []
        self.df: Dict[str, int] = {}
        self.doc_len: List[int] = []
        self.avgdl: float = 0.0
        self._metadata: Dict[str, dict] = {}
        self._inverted_index = None

    def add(self, doc_id: str, text: str, metadata: dict = None) -> None:
        tokens = tokenize(text)
        self.doc_ids.append(doc_id)
        self.doc_tokens.append(tokens)
        freqs = Counter(tokens)
        self.doc_freqs.append(freqs)
        self.doc_len.append(len(tokens))
        for term in freqs:
            self.df[term] = self.df.get(term, 0) + 1
        self._metadata[doc_id] = metadata or {}
        self.avgdl = sum(self.doc_len) / len(self.doc_len)
        self._inverted_index = None

    def _idf(self, term: str) -> float:
        n = len(self.doc_ids)
        df = self.df.get(term, 0)
        # BM25 idf with +1 smoothing to keep it non-negative
        return math.log((n - df + 0.5) / (df + 0.5) + 1)

    def search(self, query: str, top_k: int = 10) -> List[Tuple[str, float, dict]]:
        if not self.doc_ids:
            return []
        
        # Build inverted index on demand to speed up search (O(N) -> O(posting list length))
        if not hasattr(self, "_inverted_index") or self._inverted_index is None:
            self._inverted_index = {}
            for i, freqs in enumerate(self.doc_freqs):
                for term, f in freqs.items():
                    self._inverted_index.setdefault(term, []).append((i, f))

        q_tokens = tokenize(query)
        scores = {}
        for term in q_tokens:
            if term not in self.df:
                continue
            idf = self._idf(term)
            if idf < 0.2:  # Skip extremely common stop words to keep search lightning fast
                continue
            postings = self._inverted_index.get(term, [])
            for i, f in postings:
                dl = self.doc_len[i]
                denom = f + self.k1 * (1 - self.b + self.b * dl / self.avgdl)
                scores[i] = scores.get(i, 0.0) + idf * (f * (self.k1 + 1)) / denom
        
        # Only process matched docs instead of looping over all N docs
        matching = []
        for i, score in scores.items():
            if score > 0:
                doc_id = self.doc_ids[i]
                matching.append((doc_id, score, self._metadata.get(doc_id, {})))
                
        ranked = sorted(matching, key=lambda x: x[1], reverse=True)
        return ranked[:top_k]


class RedisBM25Index:
    """BM25 index backed by Redis, enabling cross-process sparse retrieval.
    Ingestion-service writes, agent-services read — all via shared Redis keys.

    Key schema per (modality, shard):
        bm25:{m}:{s}:N          — int, total document count
        bm25:{m}:{s}:totlen     — int, total token count across all docs
        bm25:{m}:{s}:df         — hash, term -> document frequency
        bm25:{m}:{s}:tf:{docid} — hash, term -> term frequency in this doc
        bm25:{m}:{s}:dl:{docid} — string, document token length
        bm25:{m}:{s}:meta:{docid} — string, JSON metadata
        bm25:{m}:{s}:docs       — set, all doc_ids
    """
    import json as _json

    def __init__(self, redis_client, modality: str, shard: str,
                 k1: float = 1.5, b: float = 0.75):
        self.r = redis_client
        self.prefix = f"bm25:{modality}:{shard}"
        self.k1 = k1
        self.b = b

    def _key(self, suffix: str) -> str:
        return f"{self.prefix}:{suffix}"

    def add(self, doc_id: str, text: str, metadata: dict = None) -> None:
        import json
        tokens = tokenize(text)
        freqs = Counter(tokens)
        pipe = self.r.pipeline()
        # Store term frequencies for this doc
        tf_key = self._key(f"tf:{doc_id}")
        pipe.delete(tf_key)
        if freqs:
            pipe.hset(tf_key, mapping={t: str(c) for t, c in freqs.items()})
        # Store doc length
        pipe.set(self._key(f"dl:{doc_id}"), str(len(tokens)))
        # Store metadata
        pipe.set(self._key(f"meta:{doc_id}"), json.dumps(metadata or {}))
        # Track doc in set
        pipe.sadd(self._key("docs"), doc_id)
        pipe.execute()

        # Update corpus-level stats atomically
        # Increment df for each unique term in this doc
        df_key = self._key("df")
        pipe2 = self.r.pipeline()
        for term in freqs:
            pipe2.hincrby(df_key, term, 1)
        pipe2.incr(self._key("N"))
        pipe2.incrby(self._key("totlen"), len(tokens))
        pipe2.execute()

    def search(self, query: str, top_k: int = 10) -> List[Tuple[str, float, dict]]:
        import json
        n_str = self.r.get(self._key("N"))
        if not n_str:
            return []
        n = int(n_str)
        if n == 0:
            return []

        totlen_str = self.r.get(self._key("totlen"))
        avgdl = int(totlen_str) / n if totlen_str else 1.0

        q_tokens = tokenize(query)
        if not q_tokens:
            return []

        # Get df for query terms
        df_key = self._key("df")
        df_vals = self.r.hmget(df_key, *q_tokens)
        term_df = {}
        relevant_terms = []
        for term, df_val in zip(q_tokens, df_vals):
            if df_val is not None:
                term_df[term] = int(df_val)
                relevant_terms.append(term)

        if not relevant_terms:
            return []

        # Get all doc_ids
        doc_ids = list(self.r.smembers(self._key("docs")))
        if not doc_ids:
            return []

        # Score each doc
        scores = {}
        for doc_id in doc_ids:
            tf_key = self._key(f"tf:{doc_id}")
            dl_str = self.r.get(self._key(f"dl:{doc_id}"))
            if dl_str is None:
                continue
            dl = int(dl_str)

            # Get tf for relevant terms in this doc
            tf_vals = self.r.hmget(tf_key, *relevant_terms)
            score = 0.0
            for term, tf_val in zip(relevant_terms, tf_vals):
                f = int(tf_val) if tf_val else 0
                if f == 0:
                    continue
                df = term_df[term]
                idf = math.log((n - df + 0.5) / (df + 0.5) + 1)
                denom = f + self.k1 * (1 - self.b + self.b * dl / avgdl)
                score += idf * (f * (self.k1 + 1)) / denom

            if score > 0:
                scores[doc_id] = score

        if not scores:
            return []

        # Sort and return top_k
        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:top_k]
        results = []
        for doc_id, score in ranked:
            meta_raw = self.r.get(self._key(f"meta:{doc_id}"))
            meta = json.loads(meta_raw) if meta_raw else {}
            results.append((doc_id, score, meta))
        return results
