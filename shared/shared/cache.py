"""Two-layer cache: custom in-process LRU (hashmap + doubly linked list) as
L1, Redis (allkeys-lru policy expected server-side) as L2. Tracks per-modality
hit ratio."""
import hashlib
import json
from typing import Any, Optional


class _Node:
    __slots__ = ("key", "value", "prev", "next")

    def __init__(self, key=None, value=None):
        self.key = key
        self.value = value
        self.prev = None
        self.next = None


class InProcessLRU:
    """O(1) get/put LRU via hashmap + doubly linked list."""

    def __init__(self, capacity: int = 512):
        self.capacity = capacity
        self._map = {}
        self.head = _Node()  # MRU sentinel
        self.tail = _Node()  # LRU sentinel
        self.head.next = self.tail
        self.tail.prev = self.head

    def _remove(self, node: _Node) -> None:
        node.prev.next = node.next
        node.next.prev = node.prev

    def _insert_front(self, node: _Node) -> None:
        node.next = self.head.next
        node.prev = self.head
        self.head.next.prev = node
        self.head.next = node

    def get(self, key: str) -> Optional[Any]:
        node = self._map.get(key)
        if node is None:
            return None
        self._remove(node)
        self._insert_front(node)
        return node.value

    def put(self, key: str, value: Any) -> None:
        if key in self._map:
            node = self._map[key]
            node.value = value
            self._remove(node)
            self._insert_front(node)
            return
        node = _Node(key, value)
        self._map[key] = node
        self._insert_front(node)
        if len(self._map) > self.capacity:
            lru = self.tail.prev
            self._remove(lru)
            del self._map[lru.key]

    def __len__(self):
        return len(self._map)


def cache_key(query: str, modality: str) -> str:
    h = hashlib.sha256(query.encode("utf-8")).hexdigest()[:24]
    return f"cache:{modality}:{h}"


class ModalityAwareCache:
    def __init__(self, redis_client=None, l1_capacity: int = 512):
        self.redis = redis_client
        self.l1 = InProcessLRU(l1_capacity)
        self.hits = {"text": 0, "table": 0, "image": 0, "response": 0}
        self.misses = {"text": 0, "table": 0, "image": 0, "response": 0}

    def get(self, query: str, modality: str) -> Optional[Any]:
        key = cache_key(query, modality)
        val = self.l1.get(key)
        if val is None and self.redis is not None:
            raw = self.redis.get(key)
            if raw is not None:
                val = json.loads(raw)
                self.l1.put(key, val)
        if modality in self.hits:
            if val is not None:
                self.hits[modality] += 1
            else:
                self.misses[modality] += 1
        return val

    def put(self, query: str, modality: str, value: Any, ttl: int = 3600) -> None:
        key = cache_key(query, modality)
        self.l1.put(key, value)
        if self.redis is not None:
            self.redis.set(key, json.dumps(value), ex=ttl)

    def hit_ratio(self, modality: str) -> float:
        total = self.hits[modality] + self.misses[modality]
        return self.hits[modality] / total if total else 0.0

    def stats(self) -> dict:
        return {m: {"hits": self.hits[m], "misses": self.misses[m], "hit_ratio": self.hit_ratio(m)}
                for m in self.hits}
