"""Benchmark: % of keys that remap when a node is added/removed, comparing
consistent hashing (with virtual nodes) vs naive mod-N hashing.

Run: python -m benchmarks.reshard_benchmark
"""
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.hashing import ConsistentHashRing
from shared.naive_hash import NaiveModHash

N_KEYS = 20_000
N_NODES = 8


def pct_remapped(before: dict, after: dict) -> float:
    changed = sum(1 for k in before if before[k] != after[k])
    return 100.0 * changed / len(before)


def run(label: str, ring_before, ring_after, keys):
    before = {k: ring_before.get_node(k) for k in keys}
    after = {k: ring_after.get_node(k) for k in keys}
    print(f"{label}: {pct_remapped(before, after):.2f}% keys remapped (theoretical ~{100/N_NODES:.2f}% for add)")


def main():
    keys = [str(uuid.uuid4()) for _ in range(N_KEYS)]
    nodes = [f"node-{i}" for i in range(N_NODES)]

    # --- consistent hashing: add a node ---
    ch_before = ConsistentHashRing(nodes=nodes, vnodes=150)
    ch_after = ConsistentHashRing(nodes=nodes, vnodes=150)
    ch_after.add_node("node-new")
    run("ConsistentHash (add node)", ch_before, ch_after, keys)

    # --- consistent hashing: remove a node ---
    ch_after2 = ConsistentHashRing(nodes=nodes, vnodes=150)
    ch_after2.remove_node("node-3")
    run("ConsistentHash (remove node)", ch_before, ch_after2, keys)

    # --- naive mod hashing: add a node ---
    nh_before = NaiveModHash(nodes=nodes)
    nh_after = NaiveModHash(nodes=nodes)
    nh_after.add_node("node-new")
    run("NaiveModHash   (add node)", nh_before, nh_after, keys)

    # --- naive mod hashing: remove a node ---
    nh_after2 = NaiveModHash(nodes=nodes)
    nh_after2.remove_node("node-3")
    run("NaiveModHash   (remove node)", nh_before, nh_after2, keys)


if __name__ == "__main__":
    main()
