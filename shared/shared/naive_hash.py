"""Naive mod-N hashing, used only as a benchmark baseline vs consistent hashing."""
import hashlib
from typing import List, Optional


def _hash(key: str) -> int:
    return int(hashlib.md5(key.encode("utf-8")).hexdigest(), 16)


class NaiveModHash:
    def __init__(self, nodes: Optional[List[str]] = None):
        self._nodes: List[str] = list(nodes or [])

    def add_node(self, node: str) -> None:
        if node not in self._nodes:
            self._nodes.append(node)

    def remove_node(self, node: str) -> None:
        if node in self._nodes:
            self._nodes.remove(node)

    def get_node(self, key: str) -> Optional[str]:
        if not self._nodes:
            return None
        return self._nodes[_hash(key) % len(self._nodes)]

    @property
    def nodes(self) -> List[str]:
        return list(self._nodes)
