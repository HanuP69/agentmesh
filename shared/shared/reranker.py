import os
import requests
import socket
from typing import List

ML_SERVICE_URL = os.getenv("ML_SERVICE_URL")
if not ML_SERVICE_URL or (os.name == "nt" and "nginx-internal" in ML_SERVICE_URL):
    ML_SERVICE_URL = "http://localhost:8099"
else:
    try:
        socket.getaddrinfo("nginx-internal", 8080)
        ML_SERVICE_URL = "http://nginx-internal:8080/ml"
    except socket.gaierror:
        ML_SERVICE_URL = "http://localhost:8099"


class Reranker:
    def __init__(self, llm_client=None, agent_type: str = "synthesizer"):
        pass

    def rerank(self, query: str, candidates: List[dict], top_k: int = 5, use_llm: bool = False,
               blend_weight: float = 0.7) -> List[dict]:
        """blend_weight: how much the final order trusts the cross-encoder
        vs. the incoming fused (RRF/normalized) score. Previously this
        discarded the fused score entirely and sorted purely by cross-encoder
        score — on fact/exact-match-heavy queries where BM25 already had the
        right doc ranked #1, that let a mediocre cross-encoder call override
        a strong upstream signal. Blending keeps the reranker useful for
        semantic reordering without letting it fully override a confident
        fused ranking."""
        if not candidates:
            return []
        texts = [c.get("metadata", {}).get("content", "") for c in candidates]
        try:
            r = requests.post(f"{ML_SERVICE_URL}/rerank", json={"query": query, "candidates": texts, "top_k": top_k}, timeout=120)
            r.raise_for_status()
            ranked = r.json()["ranked"]
        except Exception:
            return candidates[:top_k]

        if not ranked:
            return candidates[:top_k]

        rerank_vals = [score for _, score in ranked]
        r_lo, r_hi = min(rerank_vals), max(rerank_vals)
        r_span = (r_hi - r_lo) or 1e-9

        fused_vals = [c.get("score", 0.0) for c in candidates]
        f_lo, f_hi = min(fused_vals), max(fused_vals)
        f_span = (f_hi - f_lo) or 1e-9

        out = []
        for idx, score in ranked:
            item = dict(candidates[idx])
            item["rerank_score"] = score
            rerank_norm = (score - r_lo) / r_span
            fused_norm = (item.get("score", 0.0) - f_lo) / f_span
            item["blended_score"] = blend_weight * rerank_norm + (1 - blend_weight) * fused_norm
            out.append(item)
        out.sort(key=lambda x: x["blended_score"], reverse=True)
        return out[:top_k]
