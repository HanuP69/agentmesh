import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.cache import InProcessLRU, ModalityAwareCache
from shared.fusion import AgentResult, fuse


def test_lru_eviction_order():
    lru = InProcessLRU(capacity=2)
    lru.put("a", 1)
    lru.put("b", 2)
    lru.get("a")  # a is now MRU
    lru.put("c", 3)  # evicts b (LRU)
    assert lru.get("b") is None
    assert lru.get("a") == 1
    assert lru.get("c") == 3


def test_modality_cache_hit_ratio():
    cache = ModalityAwareCache()
    cache.get("q1", "text")  # miss
    cache.put("q1", "text", ["result"])
    cache.get("q1", "text")  # hit
    assert cache.hit_ratio("text") == 0.5


def test_fusion_ranks_by_weighted_score():
    results = [
        AgentResult("image", "img content", "s1", raw_score=0.9, confidence=0.9),
        AgentResult("text", "text content", "s2", raw_score=0.5, confidence=0.5),
    ]
    fused = fuse(results)
    assert fused[0]["modality"] == "image"
    assert fused[0]["fused_score"] > fused[1]["fused_score"]
