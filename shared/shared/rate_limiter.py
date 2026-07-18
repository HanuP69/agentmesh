"""Token bucket rate limiter. Atomicity on Redis is done via optimistic
locking (WATCH/MULTI/EXEC) instead of a Lua script: WATCH the bucket key,
read+compute the new token count client-side, then commit with MULTI/EXEC.
If another client wrote to the key in between, EXEC aborts (WatchError) and
we retry read-compute-commit — same end result as a server-side Lua script,
just via optimistic concurrency + retry instead of running our logic inside
Redis. Trade-off: a few round-trips per call instead of one eval, and a
retry loop under heavy contention on the same key — acceptable here since
each agent type has its own bucket key, so contention is per-agent-type, not
global. Falls back to a local in-memory bucket (same math) when no Redis
client is supplied, so the module is testable without a Redis server.
"""
import time
from dataclasses import dataclass
from typing import Optional


@dataclass
class BucketConfig:
    capacity: float
    refill_rate: float  # tokens per second


class TokenBucketRateLimiter:
    """Per-agent-type token buckets. Uses Redis WATCH/MULTI/EXEC if a client
    is supplied, otherwise an in-process dict (identical refill math)."""

    def __init__(self, redis_client=None, configs: Optional[dict] = None, max_retries: int = 5):
        self.redis = redis_client
        self.max_retries = max_retries
        self.configs = configs or {
            "text_agent": BucketConfig(capacity=20, refill_rate=5),
            "image_agent": BucketConfig(capacity=8, refill_rate=1.5),
            "table_agent": BucketConfig(capacity=15, refill_rate=4),
            "synthesizer": BucketConfig(capacity=10, refill_rate=2),
        }
        self._local_state: dict = {}
        self._rejections: dict = {}  # agent_type -> count, real counter (was a hardcoded {} in /status before)

    def _refill(self, tokens: float, ts: float, now: float, cfg: BucketConfig) -> float:
        elapsed = max(0.0, now - ts)
        return min(cfg.capacity, tokens + elapsed * cfg.refill_rate)

    def _allow_redis(self, agent_type: str, tokens_requested: float, cfg: BucketConfig) -> bool:
        key = f"ratelimit:{agent_type}"
        for _ in range(self.max_retries):
            pipe = self.redis.pipeline()
            try:
                pipe.watch(key)
                raw = pipe.hmget(key, "tokens", "ts")
                tokens = float(raw[0]) if raw[0] is not None else cfg.capacity
                ts = float(raw[1]) if raw[1] is not None else time.time()

                now = time.time()
                tokens = self._refill(tokens, ts, now, cfg)
                allowed = tokens >= tokens_requested
                if allowed:
                    tokens -= tokens_requested

                pipe.multi()
                pipe.hset(key, mapping={"tokens": tokens, "ts": now})
                pipe.expire(key, 3600)
                pipe.execute()  # raises WatchError if the key changed since WATCH
                return allowed
            except Exception as e:
                # redis-py raises WatchError (subclass of Exception) on a
                # concurrent write; retry read-compute-commit from scratch.
                import redis
                if not isinstance(e, redis.exceptions.WatchError):
                    raise
                continue
            finally:
                pipe.reset()
        # exhausted retries under heavy contention on this key: fail closed
        return False

    def allow(self, agent_type: str, tokens_requested: float = 1) -> bool:
        cfg = self.configs[agent_type]
        if self.redis is not None:
            allowed = self._allow_redis(agent_type, tokens_requested, cfg)
        else:
            # in-memory fallback, same refill formula as the Redis path
            now = time.time()
            state = self._local_state.setdefault(agent_type, {"tokens": cfg.capacity, "ts": now})
            state["tokens"] = self._refill(state["tokens"], state["ts"], now, cfg)
            state["ts"] = now
            if state["tokens"] >= tokens_requested:
                state["tokens"] -= tokens_requested
                allowed = True
            else:
                allowed = False

        if not allowed:
            self._rejections[agent_type] = self._rejections.get(agent_type, 0) + 1
        return allowed

    def rejection_counts(self) -> dict:
        """Real per-agent-type rejection counts since process start. Process-
        local even in Redis mode (the token state is shared across replicas,
        this counter isn't) -- fine for a single-instance dashboard stat."""
        return dict(self._rejections)
