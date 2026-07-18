"""Real pgvector-based vector store implementation. Connects using psycopg2,
creates per-modality tables dynamically with correct vector dimensions, and
falls back to InMemoryVectorIndex if the database is unreachable."""
import json
import time
import logging
from typing import List, Tuple

from shared.vector_store import MultiModalVectorStore

logger = logging.getLogger(__name__)

# Try to import psycopg2; if missing, we will print a warning and fall back
try:
    import psycopg2
    from psycopg2 import sql
except ImportError:
    psycopg2 = None
    sql = None


class PGVectorMultiModalStore:
    """Multi-modal vector store backed by PostgreSQL pgvector."""

    def __init__(self, dsn: str):
        self.dsn = dsn
        self.conn = None
        self._initialized_tables = set()
        self._fallback_store = MultiModalVectorStore()
        self._using_fallback = False
        self._fallback_since = None
        self._retry_interval = 60

        if psycopg2 is None:
            logger.warning("psycopg2 is not installed. Falling back to in-memory vector store.")
            self._using_fallback = True
            self._fallback_since = time.time()
            return

        try:
            self._connect()
            logger.info("Successfully connected to pgvector database.")
        except Exception as e:
            logger.warning(
                f"Failed to connect to pgvector at DSN. Falling back to in-memory store. Error: {e}"
            )
            self._using_fallback = True
            self._fallback_since = time.time()

    def _connect(self):
        self.conn = psycopg2.connect(self.dsn)
        self.conn.autocommit = True
        # Try to enable pgvector extension
        with self.conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")

    def _ensure_connection(self) -> bool:
        if self._using_fallback:
            if self._fallback_since is not None and (time.time() - self._fallback_since) >= self._retry_interval:
                try:
                    self._connect()
                    self._using_fallback = False
                    self._fallback_since = None
                    logger.info("Successfully reconnected to pgvector database.")
                    return True
                except Exception as e:
                    logger.debug(f"pgvector retry reconnect failed: {e}. Staying on in-memory store.")
                    self._fallback_since = time.time()
                    return False
            return False
        try:
            if self.conn is None or self.conn.closed != 0:
                self._connect()
            return True
        except Exception as e:
            logger.warning(f"pgvector connection lost and reconnect failed: {e}. Switching to in-memory store.")
            self._using_fallback = True
            self._fallback_since = time.time()
            return False

    def _ensure_table(self, modality: str, dim: int):
        table_name = f"vectors_{modality}"
        if table_name in self._initialized_tables:
            return

        if not self._ensure_connection():
            return

        try:
            with self.conn.cursor() as cur:
                # Create table with correct vector dimension
                cur.execute(
                    sql.SQL(
                        """
                        CREATE TABLE IF NOT EXISTS {} (
                            doc_id TEXT PRIMARY KEY,
                            shard TEXT,
                            vector vector({}),
                            metadata JSONB
                        );
                        """
                    ).format(sql.Identifier(table_name), sql.Literal(dim))
                )
                # Create index on the shard column
                cur.execute(
                    sql.SQL("CREATE INDEX IF NOT EXISTS {} ON {} (shard);").format(
                        sql.Identifier(f"idx_{table_name}_shard"),
                        sql.Identifier(table_name),
                    )
                )
            self._initialized_tables.add(table_name)
            logger.info(f"Initialized pgvector table {table_name} with dimension {dim}")
        except Exception as e:
            logger.error(f"Failed to initialize pgvector table {table_name}: {e}. Falling back to in-memory.")
            self._using_fallback = True
            self._fallback_since = time.time()

    def upsert(self, modality: str, shard: str, doc_id: str, vector: list, metadata: dict) -> None:
        if self._using_fallback or not self._ensure_connection():
            self._fallback_store.upsert(modality, shard, doc_id, vector, metadata)
            return

        dim = len(vector)
        self._ensure_table(modality, dim)

        if self._using_fallback:
            self._fallback_store.upsert(modality, shard, doc_id, vector, metadata)
            return

        table_name = f"vectors_{modality}"
        try:
            with self.conn.cursor() as cur:
                # Upsert query using standard ON CONFLICT
                cur.execute(
                    sql.SQL(
                        """
                        INSERT INTO {} (doc_id, shard, vector, metadata)
                        VALUES (%s, %s, %s, %s)
                        ON CONFLICT (doc_id)
                        DO UPDATE SET shard = EXCLUDED.shard, vector = EXCLUDED.vector, metadata = EXCLUDED.metadata;
                        """
                    ).format(sql.Identifier(table_name)),
                    (doc_id, shard, vector, json.dumps(metadata)),
                )
        except Exception as e:
            logger.error(f"pgvector upsert failed for modality {modality}: {e}. Falling back to in-memory.")
            self._using_fallback = True
            self._fallback_since = time.time()
            self._fallback_store.upsert(modality, shard, doc_id, vector, metadata)

    def search(self, modality: str, shard: str, query_vector: list, top_k: int = 5) -> List[dict]:
        if self._using_fallback or not self._ensure_connection():
            return self._fallback_store.search(modality, shard, query_vector, top_k)

        dim = len(query_vector)
        self._ensure_table(modality, dim)

        if self._using_fallback:
            return self._fallback_store.search(modality, shard, query_vector, top_k)

        table_name = f"vectors_{modality}"
        try:
            with self.conn.cursor() as cur:
                # Retrieve items ordered by cosine distance (<=>) filtering by shard
                cur.execute(
                    sql.SQL(
                        """
                        SELECT doc_id, vector <=> %s::vector AS distance, metadata
                        FROM {}
                        WHERE shard = %s
                        ORDER BY distance ASC
                        LIMIT %s;
                        """
                    ).format(sql.Identifier(table_name)),
                    (query_vector, shard, top_k),
                )
                rows = cur.fetchall()

                results = []
                for doc_id, distance, meta_raw in rows:
                    meta = meta_raw if isinstance(meta_raw, dict) else json.loads(meta_raw)
                    results.append({
                        "id": doc_id,
                        "score": max(0.0, 1.0 - float(distance)) if distance is not None else 0.0,
                        "metadata": meta
                    })
                return results
        except Exception as e:
            logger.error(f"pgvector search failed for modality {modality}: {e}. Falling back to in-memory.")
            self._using_fallback = True
            self._fallback_since = time.time()
            return self._fallback_store.search(modality, shard, query_vector, top_k)
