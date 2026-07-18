"""Cross-modal confidence fusion: weighted (Bayesian-lite) combination of
per-agent confidence scores into a final ranked list."""
from dataclasses import dataclass
from typing import List

DEFAULT_MODALITY_WEIGHTS = {"text": 1.0, "table": 1.1, "image": 0.85}


@dataclass
class AgentResult:
    modality: str
    content: str
    source: str
    raw_score: float  # retrieval similarity score, 0..1
    confidence: float  # agent's self-reported confidence, 0..1
    metadata: dict = None


def fuse(results: List[AgentResult], weights: dict = None) -> List[dict]:
    weights = weights or DEFAULT_MODALITY_WEIGHTS
    fused = []
    for r in results:
        w = weights.get(r.modality, 1.0)
        # Bayesian-lite: combine prior (modality weight) with likelihoods
        # (raw retrieval score, agent confidence) via weighted geometric mean.
        combined = w * (max(0.0, r.raw_score) ** 0.5) * (max(0.0, r.confidence) ** 0.5)
        fused.append({**r.__dict__, "fused_score": combined})
    fused.sort(key=lambda x: x["fused_score"], reverse=True)
    return fused


def cross_modal_rerank(results: List[AgentResult], weights: dict = None) -> List[dict]:
    """When a query hits multiple modalities, merge into one ranked list
    instead of ranking each modality independently."""
    return fuse(results, weights)
