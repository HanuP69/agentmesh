import unittest
from shared.vector_store import create_vector_store
from shared.pgvector_store import PGVectorMultiModalStore


class TestPGVectorStore(unittest.TestCase):
    def test_factory_fallback(self):
        # If DSN is empty or invalid, it should fall back to in-memory index
        store = create_vector_store("pgvector", "postgresql://invalid_user:invalid_password@localhost:5432/invalid_db")
        self.assertTrue(store._using_fallback)

        # Test upsert and search on fallback
        store.upsert("text", "text-shard-0", "doc1", [0.1] * 64, {"content": "hello world"})
        results = store.search("text", "text-shard-0", [0.1] * 64, top_k=1)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["id"], "doc1")
        self.assertEqual(results[0]["metadata"]["content"], "hello world")

    def test_factory_memory(self):
        # If backend is 'memory', it should return standard MultiModalVectorStore
        store = create_vector_store("memory", "")
        self.assertFalse(hasattr(store, "_using_fallback"))
        store.upsert("text", "text-shard-0", "doc1", [0.1] * 64, {"content": "hello memory"})
        results = store.search("text", "text-shard-0", [0.1] * 64, top_k=1)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["metadata"]["content"], "hello memory")
