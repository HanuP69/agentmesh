"""Text Agent Service — handles text modality retrieval.
Exposes /retrieve endpoint called by supervisor-service."""
import logging
import os
import requests as http_requests

from fastapi import FastAPI
from pydantic import BaseModel
from typing import Optional

from shared.config import settings
from shared.hashing import ModalityHashRings
from shared.hybrid_retrieval import HybridRetriever
from shared.vector_store import create_vector_store
from shared.reranker import Reranker
from shared.embeddings import embed_query, configure_embedder
from shared.fusion import AgentResult

logger = logging.getLogger(__name__)
app = FastAPI(title="AgentMesh Text Agent Service")

# --- Redis ---
redis_client = None
if settings.USE_REDIS:
    try:
        import redis
        redis_client = redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)
        redis_client.ping()
    except Exception:
        redis_client = None

# --- Infrastructure ---
rings = ModalityHashRings(vnodes=settings.VNODES)
for i in range(settings.SHARD_NODES):
    rings.add_node("text", f"shard-{i}")

store = create_vector_store(settings.VECTOR_BACKEND, settings.PGVECTOR_DSN)
hybrid = HybridRetriever(redis_client=redis_client)

# Wire real embeddings (Ollama/Gemini) instead of hash fallback
configure_embedder(provider=settings.LLM_PROVIDER,
                   ollama_base_url=settings.OLLAMA_BASE_URL)

SYNTHESIZER_URL = os.getenv("SYNTHESIZER_URL", "http://nginx-internal:8080/synthesizer")


class _SynthesizerProxy:
    """Proxy that calls synthesizer-service over HTTP instead of direct LLM import."""
    def chat(self, agent_type, prompt, max_tokens=None):
        try:
            resp = http_requests.post(f"{SYNTHESIZER_URL}/chat", json={"agent_type": agent_type, "prompt": prompt, "max_tokens": max_tokens}, timeout=30)
            resp.raise_for_status()
            return resp.json()["response"]
        except Exception as e:
            logger.warning(f"Synthesizer proxy call failed: {e}")
            return ""


llm_proxy = _SynthesizerProxy()
reranker = Reranker(llm_client=llm_proxy)


# --- Agent Logic ---
class TextAgent:
    modality = "text"

    def __init__(self, rings, store, hybrid, reranker):
        self.rings = rings
        self.store = store
        self.hybrid = hybrid
        self.reranker = reranker

    def _shards(self):
        return self.rings.rings[self.modality].nodes

    def retrieve(self, query, top_k=5, use_llm_rerank=False, embed_query_text=None):
        qvec = embed_query(embed_query_text or query)
        dense_hits, sparse_hits = [], []
        for shard in self._shards():
            dense_hits.extend(self.store.search(self.modality, shard, qvec, top_k * 2))
            sparse_hits.extend(self.hybrid.sparse_search(self.modality, shard, query, top_k * 2))
        dense_hits.sort(key=lambda x: x["score"], reverse=True)
        sparse_hits.sort(key=lambda x: x["score"], reverse=True)
        fused = self.hybrid.hybrid_search(dense_hits, sparse_hits, top_k=top_k * 2)
        reranked = self.reranker.rerank(query, fused, top_k=top_k, use_llm=use_llm_rerank)
        return reranked

    def run(self, query, top_k=5, use_llm_rerank=False, embed_query_text=None):
        hits = self.retrieve(query, top_k, use_llm_rerank=use_llm_rerank, embed_query_text=embed_query_text)
        results = []
        for h in hits:
            confidence = min(1.0, 0.4 + 0.3 * h.get("rerank_score", 0) + 0.3 * min(1.0, h["score"] * 60.0))
            results.append(AgentResult(
                modality=self.modality,
                content=h["metadata"].get("content", ""),
                source=h["metadata"].get("source", "unknown"),
                raw_score=h.get("rerank_score", h["score"]),
                confidence=confidence,
            ))
        return results


agent = TextAgent(rings, store, hybrid, reranker)


# --- Request/Response ---
class RetrieveReq(BaseModel):
    query: str
    top_k: int = 5
    use_llm_rerank: bool = False
    embed_query: Optional[str] = None


# --- Routes ---
@app.get("/health")
def health():
    return {"status": "ok", "modality": "text", "shards": len(agent._shards()), "shard_nodes": agent._shards()}


@app.post("/retrieve")
def retrieve(req: RetrieveReq):
    results = agent.run(req.query, req.top_k, req.use_llm_rerank, embed_query_text=req.embed_query)
    return {"results": [r.__dict__ for r in results]}
