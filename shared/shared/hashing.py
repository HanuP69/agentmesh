"""Consistent hashing with virtual nodes. One ring instance per modality."""
import bisect
import hashlib
from typing import Dict, List, Optional


def _hash(key: str) -> int:
    return int(hashlib.md5(key.encode("utf-8")).hexdigest(), 16)


class ConsistentHashRing:
    def __init__(self, nodes: Optional[List[str]] = None, vnodes: int = 150):
        self.vnodes = vnodes
        self._ring: Dict[int, str] = {}
        self._sorted_keys: List[int] = []
        self._nodes: set = set()
        for n in nodes or []:
            self.add_node(n)

    def _vnode_key(self, node: str, i: int) -> str:
        return f"{node}#vn{i}"

    def add_node(self, node: str) -> None:
        if node in self._nodes:
            return
        self._nodes.add(node)
        for i in range(self.vnodes):
            h = _hash(self._vnode_key(node, i))
            self._ring[h] = node
            bisect.insort(self._sorted_keys, h)

    def remove_node(self, node: str) -> None:
        if node not in self._nodes:
            return
        self._nodes.discard(node)
        for i in range(self.vnodes):
            h = _hash(self._vnode_key(node, i))
            if h in self._ring:
                del self._ring[h]
                idx = bisect.bisect_left(self._sorted_keys, h)
                if idx < len(self._sorted_keys) and self._sorted_keys[idx] == h:
                    self._sorted_keys.pop(idx)

    def get_node(self, key: str) -> Optional[str]:
        if not self._ring:
            return None
        h = _hash(key)
        idx = bisect.bisect_left(self._sorted_keys, h)
        if idx == len(self._sorted_keys):
            idx = 0
        return self._ring[self._sorted_keys[idx]]

    @property
    def nodes(self) -> List[str]:
        return sorted(self._nodes)


class ModalityHashRings:
    """Holds one ring per modality: text / table / image."""

    def __init__(self, vnodes: int = 150):
        self.rings: Dict[str, ConsistentHashRing] = {
            "text": ConsistentHashRing(vnodes=vnodes),
            "table": ConsistentHashRing(vnodes=vnodes),
            "image": ConsistentHashRing(vnodes=vnodes),
        }

    def add_node(self, modality: str, node: str) -> None:
        self.rings[modality].add_node(node)

    def remove_node(self, modality: str, node: str) -> None:
        self.rings[modality].remove_node(node)

    def route(self, modality: str, key: str) -> Optional[str]:
        return self.rings[modality].get_node(key)
