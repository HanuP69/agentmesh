# AgentMesh — Distributed Multi-Agent Multimodal RAG System

AgentMesh is a microservices-based Retrieval-Augmented Generation platform that routes text, table, and image queries through modality-specialized agents, coordinates them over Redis, and load-balances across a 24-container Docker deployment behind Nginx.

Most RAG projects optimize retrieval quality on a single process. AgentMesh instead treats **distributed-systems infrastructure** as the core artifact: consistent hashing, atomic rate limiting, priority scheduling, and circuit breaking — wrapped around a genuinely multimodal (text + table + image) knowledge base.

## Architecture

```
Client → nginx-edge (TLS, LB) → api-gateway (×2) → supervisor-service (×2)
                                                          │
                        ┌─────────────────┬───────────────┴───────────────┬──────────────────┐
                        ▼                 ▼                               ▼                  ▼
                 text-agent (×2)   table-agent (×2)              image-agent (×2)   synthesizer (×2)
                        │                 │                               │                  │
                        └────────── ml-service (BGE-M3 / Jina-CLIP-v2 / BGE-reranker-v2-m3) ──┘
                                                          │
                                        pgvector · Redis (queue/cache/rate-limit) · MongoDB (chat)
```

- **api-gateway** (Node.js) — auth (JWT + Google OAuth), rate-limit entry point
- **supervisor-service** (Node.js) — query decomposition, modality tagging, cross-modal confidence fusion, contradiction detection (regex-based numeric diff between table/prose)
- **text / table / image-agent-service** (FastAPI) — per-modality retrieval, each routed through its own consistent-hash ring (virtual nodes) to a shard key
- **ml-service** (FastAPI + ONNX Runtime) — CPU-only inference microservice serving BGE-M3 dense embeddings, Jina-CLIP-v2 image embeddings, and BGE-reranker-v2-m3 cross-encoder reranking
- **synthesizer-service** — final answer generation (NIM / Gemini / Ollama, with clearly labeled `[stub-response]` fallback)
- **ingestion-service** — PDF/text/table/image ingestion pipelines
- **chat-service** — chat history and user sessions (MongoDB, in-memory fallback)
- **nginx-edge / nginx-internal** — TLS termination and internal load balancing across service replicas
- **frontend** (React + Vite) — live SSE dashboard: per-modality queue depth, cache hit-rate, circuit-breaker state, shard distribution

## Core distributed-systems components

| Component | Where | Mechanism | Measured |
|---|---|---|---|
| Consistent hashing (virtual nodes) | `shared/hashing.py` (`ConsistentHashRing`, `ModalityHashRings`) | MD5 ring, 150 vnodes/node, `bisect` for O(log n) lookup; separate ring per modality | **11.38%** keys remap on node-add vs **88.83%** for naive mod-hash (20k keys/8 nodes) — **7.8× less churn**, within 0.3pp of the theoretical optimum (11.11%) |
| Token-bucket rate limiter | `shared/rate_limiter.py`, `api-gateway/lib/rateLimiter.js` | Redis `WATCH`/`MULTI`/`EXEC` optimistic-locking read-compute-commit loop (not a Lua script — same atomicity via retry-on-conflict instead of server-side eval), in-memory dict fallback with identical refill math if Redis is down | 200-request burst at capacity=50/refill=100: 76 allowed, 124 rejected (62% shed); sustained 199.9 req/s against a configured 200/s refill |
| Circuit breaker | `shared/circuit_breaker.py`, `supervisor-service/lib/circuitBreaker.js` | 3-state (`CLOSED → OPEN → HALF_OPEN`), exponential backoff (`base * 2^attempt`, capped) + jitter, one breaker per downstream agent | Trips after 4 calls at threshold=3, recovers in 0.33s at base_delay=0.05s; live-verified via chaos test (see below) |
| Priority task queue | `shared/priority_queue.py` | Redis sorted sets (`ZADD`/`BZPOPMIN`), score = `-(urgency*2 + confidence + depth*0.5) * modality_cost`, in-memory heap fallback | Modality cost weighting: image 2.0× > table 1.3× > text 1.0×, so image subtasks queue-jump proportionally to their higher latency cost |
| Two-layer LRU cache | `shared/cache.py` | Custom O(1) hashmap + doubly-linked-list (L1, in-process) in front of Redis `allkeys-lru` (L2); keyed by `sha256(query)[:24]` per modality | 66.2% hit ratio over 20k accesses against 500 keys/100 capacity, ~104.6k ops/sec |
| Hybrid retrieval | `shared/hybrid_retrieval.py`, `shared/bm25.py`, `shared/fusion.py` | From-scratch Okapi BM25 (k1=1.5, b=0.75, inverted index) + BGE-M3 dense cosine, fused via Reciprocal Rank Fusion; HyDE query expansion gated behind a regex intent check (`why/explain/cause/effect/...`) and a `USE_HYDE` flag | See retrieval table below |
| Cross-modal confidence fusion | `shared/fusion.py` | Weighted geometric mean of retrieval score and agent confidence per modality (`text` 1.0, `table` 1.1, `image` 0.85) | — |
| Contradiction detection | `supervisor-service/index.js::detectContradictions` | Regex-extracted numeric tokens from table vs. text content in the fused result set; flags when the two sets are disjoint | Precision 0.833, recall 1.0, F1 0.909 on a 10-pair labeled test set |
| Query decomposition | `supervisor-service/index.js` | LLM call to the synthesizer classifies a query into `text`/`table`/`image`, with a regex heuristic (`table\|row\|column\|compare...`, `diagram\|screenshot\|image...`) as fallback if that call fails |

## Retrieval quality (OpenRAGBench, 3,045 queries / 396 papers)

| Method | Recall@5 | Recall@10 | nDCG@10 | MRR |
|---|---|---|---|---|
| BM25 only | 0.847 | 0.903 | 0.757 | 0.713 |
| Dense (BGE-M3) only | 0.876 | 0.925 | 0.778 | 0.733 |
| **Hybrid (RRF, dense+sparse)** | **0.872** | **0.928** | **0.793** | **0.753** |

Embeddings served by `ml-service`: BGE-M3 (text/table, CLS-pooled + L2-normalized) and Jina-CLIP-v2 (image), both 1024-dim, reranked with BGE-reranker-v2-m3 — all via raw ONNX Runtime sessions (torch-free at inference), CPU-only.

## Load testing and resilience

Locust-driven tests across three container configurations (baseline 1×ml-service, resource-contended 3×ml-service/5×agents = 48 containers, resource-optimized 3×ml-service/2×agents = 24 containers), 50 → 10,000 concurrent users, 0% request failure rate throughout (`loadtest_report.md`, `integration_tests/load_test_report.json`).

The more interesting finding than "0% failures": scaling `ml-service` replicas gives a clean win up to ~1,000 concurrent users (+23.4% throughput, P99 latency cut from 12s to 7.7s) but **zero-to-negative benefit at 10,000 users** on a single host — multiple CPU-bound ONNX inference processes on one socket start fighting for cache/scheduler time. Production recommendation coming out of that: put `ml-service` on dedicated nodes via node-affinity, separate from the lightweight routing services.

Circuit breaker behavior is chaos-tested directly (`integration_tests/chaos_circuit_breaker_test.py`): point the text-agent URL at a stub that accepts connections but never responds, fire 8 sequential queries through the real gateway → supervisor → agent path. Requests 1–5 pay the full ~15s timeout while the breaker is `CLOSED` and counting failures; once the threshold trips, requests 6–8 fail in ~15ms instead of retrying the hung dependency.

## Running locally

```bash
cp .env.example .env        # fill in NIM/Gemini keys as needed
docker compose up --build
```

Frontend: `http://localhost` (via nginx-edge). Load test: `docker compose run locust`. Full benchmark suite (hashing, rate limiter, circuit breaker, cache, retrieval quality, contradiction detection, e2e latency): `python -m benchmarks.testbench` from `backend/`.

## Tech stack

**Backend/Orchestration:** Node.js, FastAPI, Nginx, Docker Compose
**Data:** PostgreSQL + pgvector, MongoDB, Redis
**Retrieval/ML:** BGE-M3, Jina-CLIP-v2, BGE-reranker-v2-m3, BM25, RRF, HyDE, ONNX Runtime
**LLM:** NVIDIA NIM, Gemini, Ollama (local dev)
**Frontend:** React, Vite
**Testing/Load:** Locust, pytest, Vitest
