# Integration tests (no Docker required)

These exercise the *real* running services (real Redis, real HTTP calls, real
circuit breaker / rate limiter / cache) end-to-end, as plain local processes —
useful in any environment where Docker isn't available, and closer to the
original `AgentMesh Microservices Load Testing Report` methodology than the
unit tests are.

## Setup

Requires `redis-server` and Node 18+ installed locally.

```bash
pip install -r backend/requirements.txt
pip install -e shared/
for d in services/text-agent-service services/table-agent-service services/image-agent-service services/synthesizer-service services/chat-service; do
  pip install -r "$d/requirements.txt"
done
pip install aiohttp pyjwt requests
(cd services/supervisor-service && npm install)
(cd services/api-gateway && npm install)
```

## Start the local stack

```bash
bash integration_tests/run_local_stack.sh
```

This starts: redis (`:6379`), text/table/image agents (`:8101-8103`),
synthesizer (`:8104`, `LLM_PROVIDER=none` — offline stub, no API key needed),
chat-service (`:8105`, falls back to in-memory store, no Mongo needed),
supervisor-service (`:8010`), api-gateway (`:8000`). All service URLs are
env-configurable (`TEXT_AGENT_URL`, `SUPERVISOR_URL`, etc.) — no nginx/Docker
service-discovery required for this local run.

Vector store runs with `VECTOR_BACKEND=memory` (no Postgres needed), so
retrieval will return empty results unless you've ingested data first —
that's fine for latency/throughput/resilience testing, since it's the
network/queueing/breaker/limiter behavior under test, not retrieval quality.
(Run `openrag_bench.py` in `backend/benchmarks/` separately for retrieval
quality — see that file's docstring.)

## Load test (latency/throughput, mirrors the original report's methodology)

```bash
python integration_tests/load_test.py
```

Generates a unique JWT per simulated user (same per-user isolation logic the
original report verified), runs staged concurrency (10/50/150/300 concurrent
users, 12s each), and writes `integration_tests/load_test_report.json` with
p50/p90/p95/p99/max latency, throughput, and fail rate per stage.

Numbers will differ from the original Docker-based report — this sandbox is
a single instance of each service on shared CPU, not 5 replicas behind
nginx `least_conn` load balancing — but the *shape* (0% fail rate, latency
climbing with concurrency as the event loop saturates) should hold.

## Circuit breaker chaos test

```bash
python integration_tests/hung_agent_stub.py &          # simulates a hung (not down) agent on :8199
# restart supervisor-service with TEXT_AGENT_URL=http://127.0.0.1:8199
python integration_tests/chaos_circuit_breaker_test.py
```

Demonstrates the fail-fast fix directly: first 5 requests each pay the full
15s axios timeout while the breaker is CLOSED, then it trips OPEN and the
rest return in single-digit milliseconds instead of hanging again.
