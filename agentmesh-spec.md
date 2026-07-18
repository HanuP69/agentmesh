# AgentMesh — Distributed Multi-Agent Multimodal RAG Orchestration Platform

## 1. Problem Statement

Most RAG projects focus on retrieval quality on a single node. AgentMesh focuses on **distributed RAG infrastructure**: modality-specialized agents, sharded routing via consistent hashing, Redis-backed coordination (queueing, rate limiting, caching), and resilience patterns — all wrapped around a genuinely multimodal (text + table + image) knowledge base.

Core differentiator: multimodal retrieval **routed through specialized agents across distributed shards**, with system-design-interview-grade infra (hashing, rate limiting, queuing, circuit breaking) as the core engineering artifact — not just retrieval quality on one node.

## 2. Stack

| Layer | Tech |
|---|---|
| Frontend | React + Vite, SSE/WebSocket live agent status dashboard |
| Backend | FastAPI |
| Orchestration/coordination | Redis (hashing, priority queue, rate limiter, cache) |
| Metadata store | MongoDB |
| Vector store(s) | pgvector or Qdrant — separate indices per modality |
| Image embeddings | CLIP or NVIDIA NIM vision model |
| LLM | NVIDIA NIM |
| Deploy | Docker Compose → Cloud Run / k8s (stretch) |

## 3. RAG Design: Agentic + Multimodal

**Modality-specialized agents** (core differentiator):
- **Text agent** — prose, docs, issue descriptions. Own embedding space + index.
- **Table/structured-data agent** — extracted tables, code blocks. Own index, structure-aware chunking.
- **Image agent** — diagrams, screenshots. CLIP/NIM-vision embeddings, own index.

**Supervisor agent** decomposes incoming query → dispatches subtasks to relevant modality agent(s) → results go through **cross-modal fusion agent** → final synthesized, cited answer.

**Retrieval quality features:**
- Cross-modal reranking: when a query matches both text and image/table, combine scores instead of ranking modalities independently.
- Contradiction/consistency check agent: flags when table data and prose disagree (common in real-world docs).

## 4. Core Math/Algorithm Components

### 4.1 Consistent Hashing with Virtual Nodes (per-modality rings)
- Separate hash ring per modality (text ring, table ring, image ring) — each routes to its own set of shards/nodes.
- Virtual nodes (100–200 per physical node) for load balance.
- On node add/remove: only ~K/n keys re-shard. Prove empirically — benchmark script showing re-shard % vs naive mod-hashing.

### 4.2 Token Bucket Rate Limiter (Redis-backed)
- Protects NIM API (text LLM + vision model) from concurrent agent bursts.
- Per-agent-type budget: text agent, image agent, synthesizer each get separate bucket configs.
- Refill formula: `tokens = min(capacity, tokens + elapsed * refill_rate)`.
- Implemented atomically via Redis Lua script.

### 4.3 Priority Task Queue
- Redis sorted sets: `ZADD queue score=priority task_id`, workers use `BZPOPMIN`.
- Priority = f(query urgency, agent confidence, subtask depth, modality cost — image retrieval costlier than text, weight accordingly).

### 4.4 LRU Embedding/Result Cache
- Redis native `allkeys-lru`. Track cache-hit ratio per modality (image cache likely lower hit-rate — good discussion point).
- Bonus: custom in-process LRU (hashmap + doubly linked list) as secondary layer, benchmark vs Redis-only.

### 4.5 Circuit Breaker + Exponential Backoff
- Wraps agent→agent and agent→LLM/vision-model calls.
- 3-state machine: `CLOSED → OPEN → HALF_OPEN`.
- Backoff: `delay = min(cap, base * 2^attempt)` with jitter.

### 4.6 Cross-Modal Confidence Fusion
- Weighted combination of per-agent confidence scores (text/table/image) into final ranked answer — lightweight Bayesian-style fusion, weighted by modality rather than source.

## 5. Architecture Flow

1. Query hits FastAPI → LangGraph supervisor agent.
2. Supervisor decomposes query, tags subtasks by modality → pushed to Redis priority queue.
3. Modality agents (text/table/image) pop tasks by priority, each routes vector lookup via its own consistent-hash ring to the correct shard.
4. LLM/vision-model calls gated through Redis token-bucket rate limiter + circuit breaker.
5. Cross-modal fusion agent merges + reranks results; contradiction-check agent flags disagreements.
6. Results cached (LRU) keyed by query-hash + modality.
7. Final answer streamed to frontend via SSE; dashboard shows per-modality queue depth, cache hit-rate, circuit state, shard distribution.

## 6. Phases (no fixed timeline — sequential, each phase gated on previous)

**Phase 0 — Setup**
- Repo scaffold, Docker Compose (Mongo, Redis, vector store), base FastAPI skeleton.
- Build base text+table ingestion pipeline.

**Phase 1 — Consistent Hashing Layer**
- Implement hash ring + virtual nodes from scratch, per-modality rings.
- Benchmark: re-shard % on node add/remove vs naive mod-hashing.
- Standalone module, testable independent of RAG pipeline.

**Phase 2 — Base Multimodal Ingestion**
- Text + table ingestion pipeline (chunking, embedding, indexing).
- Image ingestion: CLIP/NIM-vision embedding pipeline, own index.
- Verify each modality independently retrievable before adding agent layer.

**Phase 3 — Agentic Layer**
- Supervisor agent: query decomposition + modality tagging.
- Modality-specialized worker agents (text, table, image).
- Basic sequential orchestration first (no distribution yet) — validate agent logic correctness.

**Phase 4 — Distributed Coordination**
- Redis priority queue integration for subtask scheduling.
- Token bucket rate limiter (Lua script) wrapping all LLM/vision-model calls.
- Route agent vector lookups through Phase 1's hash rings.

**Phase 5 — Cross-Modal Fusion & Quality**
- Cross-modal reranking (combine scores across modalities).
- Confidence fusion agent (weighted/Bayesian-lite).
- Contradiction/consistency-check agent (table vs prose disagreement flagging).

**Phase 6 — Resilience**
- Circuit breaker + exponential backoff around all agent/LLM/vision calls.
- Failure injection testing: kill NIM endpoint mid-query, verify graceful degradation.

**Phase 7 — Caching + Observability**
- LRU cache layer, per-modality hit-rate tracking.
- Logging/metrics: queue depth, shard distribution, circuit state, rate-limiter rejections.

**Phase 8 — Frontend + Polish**
- React dashboard: live agent status, per-modality queue depth, cache stats, shard map, circuit breaker state (SSE/WebSocket).
- README, architecture diagram, demo queries covering all 3 modalities + a contradiction-detection example.

**Phase 9 — Stretch Goals**
- Multi-node Redis Cluster deployment (real sharding, not simulated).
- Chaos testing: kill a shard node mid-query, show system self-heals via re-hash + circuit breaker.
- Load testing with Locust: throughput before/after rate limiter + priority queue.
- k8s deployment instead of plain Docker Compose.
- Extend contradiction-check agent to suggest which source (text/table) is likely stale/incorrect based on metadata (last-updated timestamps).

## 7. Resume/Interview Angle

- Genuinely multimodal (text+table+image) AND agentic AND distributed — three axes of depth in a single project.
- Distributed systems fundamentals with real implementation: consistent hashing, rate limiting, priority scheduling, circuit breaking — classic system-design interview topics, backed by actual code + benchmarks.
- Redis shown as a multi-purpose distributed-systems tool (coordination + queueing + rate-limiting + caching), not just "cache."
- Concrete metrics to cite in interviews: re-shard % on node change, per-modality cache hit-rate, rate-limiter rejection rate under load, circuit breaker trip/recovery logs, contradiction-detection precision on a test set.
