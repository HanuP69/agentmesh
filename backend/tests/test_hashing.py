import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.hashing import ConsistentHashRing, ModalityHashRings


def test_deterministic_routing():
    ring = ConsistentHashRing(nodes=["a", "b", "c"], vnodes=100)
    assert ring.get_node("key1") == ring.get_node("key1")


def test_add_node_low_remap():
    keys = [f"k{i}" for i in range(5000)]
    before = ConsistentHashRing(nodes=["a", "b", "c", "d"], vnodes=150)
    after = ConsistentHashRing(nodes=["a", "b", "c", "d"], vnodes=150)
    after.add_node("e")
    b = {k: before.get_node(k) for k in keys}
    a = {k: after.get_node(k) for k in keys}
    remapped_pct = sum(1 for k in keys if a[k] != b[k]) / len(keys) * 100
    # theoretical ~1/5 = 20% upper bound for a 4->5 node add; should be well under 30%
    assert remapped_pct < 30


def test_remove_node_only_its_keys_move():
    keys = [f"k{i}" for i in range(5000)]
    before = ConsistentHashRing(nodes=["a", "b", "c"], vnodes=150)
    b = {k: before.get_node(k) for k in keys}
    before.remove_node("b")
    a = {k: before.get_node(k) for k in keys}
    for k in keys:
        if b[k] != "b":
            assert a[k] == b[k]  # keys not on removed node stay put


def test_modality_rings_independent():
    rings = ModalityHashRings(vnodes=50)
    rings.add_node("text", "t1")
    rings.add_node("image", "i1")
    assert rings.route("text", "x") == "t1"
    assert rings.route("image", "x") == "i1"
    assert rings.route("table", "x") is None
