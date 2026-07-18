"""
Circuit breaker chaos test: repoints the supervisor's TEXT_AGENT_URL at
hung_agent_stub.py (a server that accepts connections but never responds,
simulating a hung — not down — agent) and fires 8 sequential unique
queries through the real gateway -> supervisor -> text-agent path.

Expected result (see integration_tests/README.md for full run instructions):
  requests 1-5: ~15s each (axios timeout) while the breaker is still CLOSED
                and failureCount climbs 1 -> 5
  requests 6-8: near-instant (~15ms) once failureCount hits the threshold (5)
                and the breaker trips OPEN, fast-failing instead of paying
                the 15s timeout again

This is the concrete, live proof for the "no circuit breaker in the Node
supervisor path" bug fix: before, every request in this scenario paid the
full 15s; after, only the first 5 do.
"""
import subprocess
import time

import requests

GATEWAY = "http://127.0.0.1:8000"


def main():
    print("Firing 8 unique queries at the gateway while text-agent is hung...")
    for i in range(1, 9):
        t0 = time.perf_counter()
        try:
            r = requests.post(
                f"{GATEWAY}/query",
                json={"query": f"hung agent chaos test unique query {i} abcxyz"},
                timeout=20,
            )
            status = r.status_code
        except Exception as e:
            status = f"error: {e}"
        dt = time.perf_counter() - t0
        print(f"req {i}: {dt:.3f}s status={status}")

    status = requests.get(f"{GATEWAY}/status", timeout=5).json()
    print("\nFinal circuit_states:")
    print(status.get("circuit_states"))


if __name__ == "__main__":
    main()
