"""
AgentMesh Test Bench — benchmarks every core service/feature and produces
numbers suitable for a resume/interview ("concrete metrics to cite").

Run: python -m benchmarks.testbench
Run with real Ollama embeddings (recommended for real retrieval numbers):
    LLM_PROVIDER=ollama python -m benchmarks.testbench
Outputs: printed report + benchmarks/report.json
"""
import json
import math
import random
import statistics
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.config import settings
from shared.bm25 import BM25Index
from shared.cache import InProcessLRU, ModalityAwareCache
from shared.circuit_breaker import CircuitBreaker, CircuitOpenError
from shared.fusion import AgentResult, fuse
from shared.hashing import ConsistentHashRing
from shared.hybrid_retrieval import reciprocal_rank_fusion
from shared.naive_hash import NaiveModHash
from shared.rate_limiter import BucketConfig, TokenBucketRateLimiter
from shared.vector_store import InMemoryVectorIndex, MultiModalVectorStore
from shared import embeddings as embeddings_module
from shared.embeddings import embed_text, embed_query
from shared.reranker import Reranker
import re as _re


def detect_contradictions(fused):
    num_re = _re.compile(r"-?\d+(?:\.\d+)?")
    by_modality = {}
    for item in fused:
        nums = set(num_re.findall(item.get("content", "")))
        if nums:
            by_modality.setdefault(item["modality"], set()).update(nums)
    flags = []
    if "table" in by_modality and "text" in by_modality:
        if by_modality["table"] and by_modality["text"] and by_modality["table"].isdisjoint(by_modality["text"]):
            flags.append(f"Possible contradiction: table numbers {by_modality['table']} differ from text numbers {by_modality['text']}")
    return flags

EMBEDDING_BACKEND = "hash-fallback (offline)"
REDIS_CLIENT = None
REDIS_BACKEND = "in-memory (no Redis)"


def _connect_real_redis():
    """Tries a real Redis connection so rate-limiter/queue benchmarks below
    exercise the actual WATCH/MULTI/EXEC + sorted-set code paths instead of
    silently testing the in-memory fallback. Without this, TokenBucketRateLimiter()
    and PriorityTaskQueue() constructed with no args NEVER touch Redis, no
    matter what USE_REDIS is set to -- that env var only wires main.py's
    server, not this standalone script."""
    global REDIS_CLIENT, REDIS_BACKEND
    import os
    # settings.REDIS_URL defaults to the docker-compose service name
    # ("redis://redis:6379/0"), which won't resolve when running this script
    # standalone on your machine -- default to localhost here instead, still
    # overridable via REDIS_URL env if you're pointing at something else.
    redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    try:
        import redis as redis_lib
        client = redis_lib.from_url(redis_url, decode_responses=True, socket_connect_timeout=2)
        client.ping()
        REDIS_CLIENT = client
        REDIS_BACKEND = f"redis (live, {redis_url})"
    except Exception as e:
        print(f"  [warn] Redis unreachable ({redis_url}) -- rate limiter/queue benchmarks "
              f"will use the in-memory fallback instead of real Redis. ({e})")
        print(f"  [hint] docker run -d --name redis-bench -p 6379:6379 redis:7-alpine")
        REDIS_CLIENT = None
        REDIS_BACKEND = "in-memory (no Redis)"


def _configure_real_embedder():
    """Wires the actual embedder main.py would use, based on LLM_PROVIDER.
    Without this, embed_text() silently stays on the deterministic hash
    fallback even if Ollama/Gemini is reachable — this is what makes the
    retrieval numbers below 'real' instead of just exercising the harness."""
    global EMBEDDING_BACKEND
    if embeddings_module.is_real_embedder():
        EMBEDDING_BACKEND = f"ml-service ({settings.ML_SERVICE_URL}, live)"
    else:
        print(f"  [warn] ml-service unreachable at {settings.ML_SERVICE_URL} — embeddings will error.")
        EMBEDDING_BACKEND = f"ml-service unreachable ({settings.ML_SERVICE_URL})"

REPORT = {}


def section(name):
    print(f"\n=== {name} ===")


# ---------------------------------------------------------------------------
# 1. Consistent hashing: reshard % vs naive mod hashing
# ---------------------------------------------------------------------------
def bench_consistent_hashing():
    section("1. Consistent Hashing (virtual nodes) vs Naive Mod Hashing")
    n_keys, n_nodes = 20_000, 8
    keys = [f"doc-{i}" for i in range(n_keys)]
    nodes = [f"node-{i}" for i in range(n_nodes)]

    def remap_pct(before, after):
        b = {k: before.get_node(k) for k in keys}
        a = {k: after.get_node(k) for k in keys}
        return 100.0 * sum(1 for k in keys if a[k] != b[k]) / len(keys)

    ch_before = ConsistentHashRing(nodes=nodes, vnodes=150)
    ch_after = ConsistentHashRing(nodes=nodes, vnodes=150)
    ch_after.add_node("node-new")
    ch_add_pct = remap_pct(ch_before, ch_after)

    nh_before = NaiveModHash(nodes=nodes)
    nh_after = NaiveModHash(nodes=nodes)
    nh_after.add_node("node-new")
    nh_add_pct = remap_pct(nh_before, nh_after)

    result = {
        "n_keys": n_keys,
        "n_nodes": n_nodes,
        "consistent_hash_remap_pct_on_add": round(ch_add_pct, 2),
        "naive_hash_remap_pct_on_add": round(nh_add_pct, 2),
        "theoretical_optimal_pct": round(100 / (n_nodes + 1), 2),
        "improvement_factor": round(nh_add_pct / ch_add_pct, 1),
    }
    print(f"  consistent hashing remap on add: {result['consistent_hash_remap_pct_on_add']}% "
          f"(theoretical optimum {result['theoretical_optimal_pct']}%)")
    print(f"  naive mod-hash remap on add:     {result['naive_hash_remap_pct_on_add']}%")
    print(f"  -> consistent hashing reduces reshard churn {result['improvement_factor']}x")
    REPORT["consistent_hashing"] = result


# ---------------------------------------------------------------------------
# 2. Rate limiter: burst handling + sustained throughput
# ---------------------------------------------------------------------------
def bench_rate_limiter():
    section(f"2. Token Bucket Rate Limiter (backend: {REDIS_BACKEND})")
    rl = TokenBucketRateLimiter(redis_client=REDIS_CLIENT, configs={"agent": BucketConfig(capacity=50, refill_rate=100)})
    burst_n = 200
    allowed = sum(1 for _ in range(burst_n) if rl.allow("agent"))
    rejected = burst_n - allowed

    # sustained throughput at the refill rate
    rl2 = TokenBucketRateLimiter(redis_client=REDIS_CLIENT, configs={"agent": BucketConfig(capacity=20, refill_rate=200)})
    start = time.time()
    n_ok = 0
    while time.time() - start < 0.5:
        if rl2.allow("agent"):
            n_ok += 1
    elapsed = time.time() - start
    throughput = n_ok / elapsed

    result = {
        "backend": REDIS_BACKEND,
        "burst_requests": burst_n,
        "burst_allowed": allowed,
        "burst_rejected": rejected,
        "burst_rejection_rate_pct": round(100 * rejected / burst_n, 1),
        "sustained_throughput_req_per_sec": round(throughput, 1),
        "configured_refill_rate": 200,
    }
    print(f"  burst of {burst_n}: {allowed} allowed, {rejected} rejected "
          f"({result['burst_rejection_rate_pct']}% shed under overload)")
    print(f"  sustained throughput: {result['sustained_throughput_req_per_sec']} req/s "
          f"(configured refill 200/s)")
    REPORT["rate_limiter"] = result


# ---------------------------------------------------------------------------
# 3. Circuit breaker: trip + recovery timing
# ---------------------------------------------------------------------------
def bench_circuit_breaker():
    section("3. Circuit Breaker (trip + recovery)")
    base_delay = 0.05
    cb = CircuitBreaker("bench", failure_threshold=3, base_delay=base_delay, cap_delay=1.0)

    trip_start = time.time()
    calls_to_trip = 0
    for _ in range(10):
        calls_to_trip += 1
        try:
            cb.call(lambda: (_ for _ in ()).throw(ValueError()))
        except ValueError:
            pass
        except CircuitOpenError:
            break
    trip_time = time.time() - trip_start

    # measure recovery: keep probing until half-open call succeeds
    recovery_start = time.time()
    recovered = False
    for _ in range(50):
        try:
            cb.call(lambda: "ok")
            recovered = True
            break
        except CircuitOpenError:
            time.sleep(0.02)
    recovery_time = time.time() - recovery_start

    result = {
        "failure_threshold": 3,
        "calls_to_trip": calls_to_trip,
        "time_to_trip_sec": round(trip_time, 4),
        "recovered": recovered,
        "recovery_time_sec": round(recovery_time, 4),
        "base_backoff_sec": base_delay,
    }
    print(f"  tripped after {calls_to_trip} calls ({result['time_to_trip_sec']}s)")
    print(f"  recovered: {recovered} in {result['recovery_time_sec']}s (base backoff {base_delay}s)")
    REPORT["circuit_breaker"] = result


# ---------------------------------------------------------------------------
# 4. LRU cache: hit ratio under Zipfian access pattern
# ---------------------------------------------------------------------------
def bench_lru_cache():
    section("4. LRU Cache (custom hashmap + doubly linked list)")
    n_keys, n_accesses, capacity = 500, 20_000, 100

    # Zipfian-ish distribution: popular keys accessed disproportionately
    weights = [1 / (i + 1) for i in range(n_keys)]
    total = sum(weights)
    probs = [w / total for w in weights]
    keys = list(range(n_keys))

    lru = InProcessLRU(capacity=capacity)
    hits = 0
    t0 = time.time()
    for _ in range(n_accesses):
        k = random.choices(keys, weights=probs, k=1)[0]
        if lru.get(k) is not None:
            hits += 1
        else:
            lru.put(k, f"value-{k}")
    elapsed = time.time() - t0

    result = {
        "n_keys": n_keys,
        "cache_capacity": capacity,
        "n_accesses": n_accesses,
        "hit_ratio_pct": round(100 * hits / n_accesses, 2),
        "ops_per_sec": round(n_accesses / elapsed, 0),
    }
    print(f"  {result['n_accesses']} accesses over {n_keys} keys, cache size {capacity}: "
          f"{result['hit_ratio_pct']}% hit ratio")
    print(f"  throughput: {result['ops_per_sec']:.0f} get/put ops/sec (O(1) per op)")
    REPORT["lru_cache"] = result


# ---------------------------------------------------------------------------
# 6. Retrieval quality: Dense-only vs BM25-only vs Hybrid+RRF
# ---------------------------------------------------------------------------
CORPUS = [
    ("d1", "Python is a dynamically typed interpreted programming language popular for scripting and ML."),
    ("d2", "Rust is a systems programming language focused on memory safety without garbage collection."),
    ("d3", "The Eiffel Tower is a wrought-iron lattice tower in Paris, France, built in 1889."),
    ("d4", "The Great Wall of China stretches over 13000 miles across northern China."),
    ("d5", "Lions are large cats native to Africa and India, living in prides."),
    ("d6", "Cheetahs are the fastest land animals, capable of speeds up to 70 mph."),
    ("d7", "The FastAPI framework is used to build high-performance Python web APIs."),
    ("d8", "Redis is an in-memory data store used for caching, queues, and pub/sub."),
    ("d9", "Consistent hashing minimizes key remapping when nodes are added or removed."),
    ("d10", "A circuit breaker pattern prevents cascading failures in distributed systems."),
    ("d11", "The Amazon rainforest is the largest tropical rainforest, spanning South America."),
    ("d12", "Mount Everest is the tallest mountain above sea level, located in the Himalayas."),
    ("d13", "PostgreSQL is a relational database supporting extensions like pgvector."),
    ("d14", "BM25 is a bag-of-words ranking function used for lexical document retrieval."),
    ("d15", "Reciprocal rank fusion combines multiple ranked lists without score normalization."),
    ("d16", "Elephants are the largest land animals and are known for their long trunks."),
    ("d17", "The Nile River is the longest river in Africa, flowing through Egypt."),
    ("d18", "Docker Compose orchestrates multi-container applications for local development."),
    ("d19", "Kubernetes automates deployment, scaling, and management of containerized apps."),
    ("d20", "Golden retrievers are a friendly dog breed originally bred for retrieving game."),
]

QUERIES = [
    ("fast programming language for systems with memory safety", ["d2"]),
    ("python web api framework", ["d7", "d1"]),
    ("famous tower in paris france", ["d3"]),
    ("fastest animal on land", ["d6"]),
    ("database for storing vectors", ["d13"]),
    ("preventing cascading failures distributed systems", ["d10"]),
    ("caching and queueing with redis", ["d8"]),
    ("tallest mountain in the world", ["d12"]),
    ("lexical ranking function for search", ["d14"]),
    ("combining ranked lists from multiple retrievers", ["d15"]),
    ("container orchestration kubernetes docker", ["d18", "d19"]),
    ("large animals with trunks", ["d16"]),
]


def _recall_precision_mrr(ranked_ids, relevant, k=5):
    top_k = ranked_ids[:k]
    hits = [d for d in top_k if d in relevant]
    recall = len(hits) / len(relevant) if relevant else 0.0
    precision = len(hits) / k
    rr = 0.0
    for i, d in enumerate(ranked_ids, start=1):
        if d in relevant:
            rr = 1.0 / i
            break
    return recall, precision, rr


def bench_retrieval_quality():
    section("6. Retrieval Quality: Dense-only vs BM25-only vs Hybrid+RRF")

    dense_index = InMemoryVectorIndex()
    bm25_index = BM25Index()
    for doc_id, text in CORPUS:
        dense_index.upsert(doc_id, embed_text(text), {"content": text})
        bm25_index.add(doc_id, text, {"content": text})

    results = {"dense": [], "bm25": [], "hybrid_rrf": []}
    k = 5
    for query, relevant in QUERIES:
        qvec = embed_query(query)

        dense_hits = dense_index.search(qvec, top_k=k * 2)
        dense_ids = [h["id"] for h in dense_hits]
        r, p, rr = _recall_precision_mrr(dense_ids, relevant, k)
        results["dense"].append((r, p, rr))

        bm25_hits = bm25_index.search(query, top_k=k * 2)
        bm25_ids = [h[0] for h in bm25_hits]
        r, p, rr = _recall_precision_mrr(bm25_ids, relevant, k)
        results["bm25"].append((r, p, rr))

        fused = reciprocal_rank_fusion([dense_ids, bm25_ids], weights=[0.5, 0.5])
        fused_ids = [doc_id for doc_id, _ in fused]
        r, p, rr = _recall_precision_mrr(fused_ids, relevant, k)
        results["hybrid_rrf"].append((r, p, rr))

    summary = {}
    for method, vals in results.items():
        recalls, precisions, rrs = zip(*vals)
        summary[method] = {
            f"recall@{k}": round(statistics.mean(recalls), 3),
            f"precision@{k}": round(statistics.mean(precisions), 3),
            "mrr": round(statistics.mean(rrs), 3),
        }

    for method, m in summary.items():
        print(f"  {method:12s}: recall@{k}={m[f'recall@{k}']}  precision@{k}={m[f'precision@{k}']}  MRR={m['mrr']}")

    improvement = None
    if summary["dense"]["mrr"] > 0:
        improvement = round((summary["hybrid_rrf"]["mrr"] - max(summary["dense"]["mrr"], summary["bm25"]["mrr"]))
                             / max(summary["dense"]["mrr"], summary["bm25"]["mrr"], 1e-9) * 100, 1)
    print(f"  -> hybrid RRF MRR change vs best single method: {improvement}%")
    print(f"  (embedding backend this run: {EMBEDDING_BACKEND})")

    summary["n_queries"] = len(QUERIES)
    summary["n_corpus_docs"] = len(CORPUS)
    summary["embedding_backend"] = EMBEDDING_BACKEND
    REPORT["retrieval_quality"] = summary


# ---------------------------------------------------------------------------
# 7. Contradiction detection: precision/recall on labeled pairs
# ---------------------------------------------------------------------------
CONTRADICTION_TEST_SET = [
    # (text_content, table_content, is_contradiction_label)
    ("The tower is 330 meters tall.", "metric,value\nheight_m,330", False),
    ("The tower is 300 meters tall.", "metric,value\nheight_m,330", True),
    ("Built in 1889.", "metric,value\nyear_built,1889", False),
    ("Built in 1887.", "metric,value\nyear_built,1889", True),
    ("Population is roughly 2 million.", "metric,value\npopulation,2000000", False),
    ("Population is roughly 5 million.", "metric,value\npopulation,2000000", True),
    ("The company has 500 employees.", "metric,value\nemployees,500", False),
    ("The company has 450 employees.", "metric,value\nemployees,500", True),
    ("Revenue grew by 12 percent.", "metric,value\ngrowth_pct,12", False),
    ("Revenue grew by 8 percent.", "metric,value\ngrowth_pct,12", True),
]


def bench_contradiction_detection():
    section("7. Contradiction Detection (table vs prose)")
    tp = fp = tn = fn = 0
    for text, table, label in CONTRADICTION_TEST_SET:
        fused = [
            {"modality": "text", "content": text},
            {"modality": "table", "content": table},
        ]
        flags = detect_contradictions(fused)
        predicted = len(flags) > 0
        if predicted and label:
            tp += 1
        elif predicted and not label:
            fp += 1
        elif not predicted and not label:
            tn += 1
        else:
            fn += 1

    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    accuracy = (tp + tn) / len(CONTRADICTION_TEST_SET)

    result = {
        "n_test_pairs": len(CONTRADICTION_TEST_SET),
        "tp": tp, "fp": fp, "tn": tn, "fn": fn,
        "precision": round(precision, 3),
        "recall": round(recall, 3),
        "f1": round(f1, 3),
        "accuracy": round(accuracy, 3),
    }
    print(f"  n={result['n_test_pairs']}  precision={result['precision']}  recall={result['recall']}  "
          f"f1={result['f1']}  accuracy={result['accuracy']}")
    REPORT["contradiction_detection"] = result


# ---------------------------------------------------------------------------
# 8. End-to-end query latency (offline stub mode)
# ---------------------------------------------------------------------------
def bench_e2e_latency():
    section(f"8. End-to-End Retrieval Latency (embedding backend: {EMBEDDING_BACKEND}, no LLM synthesis call)")
    from shared.hashing import ModalityHashRings
    from shared.hybrid_retrieval import HybridRetriever

    rings = ModalityHashRings(vnodes=50)
    for i in range(3):
        rings.add_node("text", f"text-shard-{i}")
    mstore = MultiModalVectorStore()
    hybrid = HybridRetriever()
    reranker = Reranker()

    def ingest(doc_id_prefix, text, source):
        doc_id = f"{doc_id_prefix}:text:0"
        vec = embed_text(text)
        shard = rings.route("text", doc_id) or "text-shard-0"
        mstore.upsert("text", shard, doc_id, vec, {"content": text, "source": source})
        hybrid.index_doc("text", shard, doc_id, text, {"content": text, "source": source})

    for doc_id, text in CORPUS:
        ingest(doc_id, text, source=doc_id)

    def run_query(query, top_k=5):
        qvec = embed_query(query)
        dense_hits, sparse_hits = [], []
        for shard in rings.rings["text"].nodes:
            dense_hits.extend(mstore.search("text", shard, qvec, top_k * 2))
            sparse_hits.extend(hybrid.sparse_search("text", shard, query, top_k * 2))
        dense_hits.sort(key=lambda x: x["score"], reverse=True)
        sparse_hits.sort(key=lambda x: x["score"], reverse=True)
        fused = hybrid.hybrid_search(dense_hits, sparse_hits, top_k=top_k * 2)
        return reranker.rerank(query, fused, top_k=top_k)

    latencies = []
    for query, _ in QUERIES:
        t0 = time.time()
        run_query(query, top_k=5)
        latencies.append((time.time() - t0) * 1000)

    latencies.sort()
    result = {
        "n_queries": len(latencies),
        "p50_ms": round(latencies[len(latencies) // 2], 2),
        "p95_ms": round(latencies[int(len(latencies) * 0.95) - 1], 2),
        "max_ms": round(max(latencies), 2),
        "embedding_backend": EMBEDDING_BACKEND,
    }
    print(f"  p50={result['p50_ms']}ms  p95={result['p95_ms']}ms  max={result['max_ms']}ms "
          f"(hybrid retrieval, in-memory index, {len(CORPUS)} docs, embed backend: {EMBEDDING_BACKEND})")
    REPORT["e2e_retrieval_latency"] = result


def main():
    random.seed(42)
    _configure_real_embedder()
    _connect_real_redis()
    print(f"Embedding backend: {EMBEDDING_BACKEND}")
    print(f"Redis backend: {REDIS_BACKEND}\n")
    bench_consistent_hashing()
    bench_rate_limiter()
    bench_circuit_breaker()
    bench_lru_cache()
    bench_retrieval_quality()
    bench_contradiction_detection()
    bench_e2e_latency()

    out_path = Path(__file__).parent / "report.json"
    with open(out_path, "w") as f:
        json.dump(REPORT, f, indent=2)
    print(f"\nFull report written to {out_path}")


if __name__ == "__main__":
    main()
