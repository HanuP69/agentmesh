import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.rate_limiter import BucketConfig, TokenBucketRateLimiter


def test_allows_within_capacity():
    rl = TokenBucketRateLimiter(configs={"a": BucketConfig(capacity=5, refill_rate=1)})
    for _ in range(5):
        assert rl.allow("a") is True
    assert rl.allow("a") is False


def test_rejection_counter_tracks_real_denials():
    """Regression test: /status used to return a hardcoded {} for this --
    make sure the counter actually increments on denial and not on success."""
    rl = TokenBucketRateLimiter(configs={"a": BucketConfig(capacity=2, refill_rate=0)})
    assert rl.rejection_counts() == {}
    rl.allow("a")
    rl.allow("a")
    assert rl.rejection_counts() == {}  # no denials yet
    rl.allow("a")  # capacity exhausted -> denied
    rl.allow("a")  # denied again
    assert rl.rejection_counts() == {"a": 2}


def test_refills_over_time():
    rl = TokenBucketRateLimiter(configs={"a": BucketConfig(capacity=2, refill_rate=10)})
    assert rl.allow("a") is True
    assert rl.allow("a") is True
    assert rl.allow("a") is False
    time.sleep(0.2)  # ~2 tokens refilled at rate=10/s
    assert rl.allow("a") is True


def test_redis_watch_multi_exec_no_double_spend_under_concurrency():
    """Regression test for the WATCH/MULTI/EXEC atomicity: without it, two
    threads reading the same stale token count and both decrementing would
    let more requests through than capacity allows. zero refill_rate isolates
    this from legitimate refill during the test run."""
    import threading

    fakeredis = pytest.importorskip("fakeredis")
    r = fakeredis.FakeRedis(decode_responses=True)
    rl = TokenBucketRateLimiter(redis_client=r, configs={"b": BucketConfig(capacity=100, refill_rate=0)})

    allowed_count = [0]
    lock = threading.Lock()

    def worker():
        for _ in range(20):
            if rl.allow("b"):
                with lock:
                    allowed_count[0] += 1

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert allowed_count[0] == 100
