"""Priority task queue on Redis sorted sets (ZADD/BZPOPMIN), with an
in-memory heap fallback for tests/local dev without Redis."""
import heapq
import itertools
import json
import time
from dataclasses import dataclass, field
from typing import Any, Optional

MODALITY_COST = {"text": 1.0, "table": 1.3, "image": 2.0}


@dataclass
class Task:
    task_id: str
    modality: str
    payload: dict
    urgency: float = 1.0
    agent_confidence: float = 0.5
    subtask_depth: int = 0

    def priority(self) -> float:
        # Lower score = popped first (BZPOPMIN semantics). Higher urgency/
        # confidence/depth/cost -> more urgent -> lower score.
        cost = MODALITY_COST.get(self.modality, 1.0)
        raw = (self.urgency * 2 + self.agent_confidence + self.subtask_depth * 0.5) * cost
        return -raw


class PriorityTaskQueue:
    def __init__(self, redis_client=None, queue_key: str = "agentmesh:queue"):
        self.redis = redis_client
        self.queue_key = queue_key
        self._heap: list = []
        self._counter = itertools.count()
        self._store: dict = {}

    def push(self, task: Task) -> None:
        score = task.priority()
        if self.redis is not None:
            self.redis.hset(f"{self.queue_key}:data", task.task_id, json.dumps(task.__dict__))
            self.redis.zadd(self.queue_key, {task.task_id: score})
        else:
            self._store[task.task_id] = task
            heapq.heappush(self._heap, (score, next(self._counter), task.task_id))

    def pop(self, timeout: float = 1.0) -> Optional[Task]:
        if self.redis is not None:
            res = self.redis.bzpopmin(self.queue_key, timeout=timeout)
            if not res:
                return None
            _, task_id, _ = res
            raw = self.redis.hget(f"{self.queue_key}:data", task_id)
            if raw is None:
                self.redis.hdel(f"{self.queue_key}:data", task_id)
                return None
            self.redis.hdel(f"{self.queue_key}:data", task_id)
            return Task(**json.loads(raw))
        if not self._heap:
            return None
        _, _, task_id = heapq.heappop(self._heap)
        return self._store.pop(task_id, None)

    def cancel(self, task_id: str) -> None:
        if self.redis is not None:
            self.redis.zrem(self.queue_key, task_id)
            self.redis.hdel(f"{self.queue_key}:data", task_id)
        else:
            self._store.pop(task_id, None)
            self._heap = [item for item in self._heap if item[2] != task_id]
            heapq.heapify(self._heap)

    def depth(self) -> int:
        if self.redis is not None:
            return self.redis.zcard(self.queue_key)
        return len(self._heap)
