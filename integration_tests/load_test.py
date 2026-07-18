import asyncio
import json
import statistics
import time
import uuid

import aiohttp
import jwt

GATEWAY = "http://127.0.0.1:8000"
JWT_SECRET = "dev-insecure-secret-change-me"


def make_token():
    payload = {
        "sub": f"user-{uuid.uuid4()}",
        "email": f"loadtest-{uuid.uuid4()}@example.com",
        "iat": int(time.time()),
        "exp": int(time.time()) + 3600,
    }
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


async def one_user(session, results, duration_s, sem):
    token = make_token()
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    end = time.time() + duration_s
    while time.time() < end:
        qid = uuid.uuid4().hex[:8]
        body = {"query": f"load test query {qid} about consistent hashing and circuit breakers"}
        t0 = time.perf_counter()
        try:
            async with sem:
                async with session.post(f"{GATEWAY}/query", headers=headers, json=body, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                    await resp.read()
                    status = resp.status
        except Exception as e:
            status = -1
        dt = time.perf_counter() - t0
        results.append((status, dt))
        await asyncio.sleep(0.05 + 0.1 * (hash(qid) % 10) / 10)  # jittered 50-150ms think time, like the report


async def run_stage(concurrency, duration_s):
    results = []
    sem = asyncio.Semaphore(concurrency)  # cap actual in-flight sockets so we don't blow local fd limits
    connector = aiohttp.TCPConnector(limit=0)
    async with aiohttp.ClientSession(connector=connector) as session:
        tasks = [asyncio.create_task(one_user(session, results, duration_s, sem)) for _ in range(concurrency)]
        await asyncio.gather(*tasks)
    return results


def summarize(concurrency, results, wall_s):
    lat = sorted(dt for _, dt in results)
    n = len(lat)
    fails = sum(1 for s, _ in results if s != 200)
    def pct(p):
        if not lat:
            return None
        idx = min(n - 1, int(p * n))
        return round(lat[idx] * 1000, 1)
    return {
        "concurrency": concurrency,
        "completed_reqs": n,
        "throughput_rps": round(n / wall_s, 1) if wall_s else 0,
        "avg_ms": round(statistics.mean(lat) * 1000, 1) if lat else None,
        "p50_ms": pct(0.50),
        "p90_ms": pct(0.90),
        "p95_ms": pct(0.95),
        "p99_ms": pct(0.99),
        "max_ms": round(max(lat) * 1000, 1) if lat else None,
        "fail_rate_pct": round(100 * fails / n, 2) if n else None,
    }


async def main():
    stages = [10, 50, 150, 300]
    duration_s = 12
    report = []
    for c in stages:
        print(f"--- stage: concurrency={c} ---", flush=True)
        t0 = time.time()
        results = await run_stage(c, duration_s)
        wall = time.time() - t0
        row = summarize(c, results, wall)
        report.append(row)
        print(json.dumps(row, indent=2), flush=True)

    with open("/tmp/agentmesh-run/load_test_report.json", "w") as f:
        json.dump(report, f, indent=2)


if __name__ == "__main__":
    asyncio.run(main())
