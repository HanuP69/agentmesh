"""
Evaluates AgentMesh's retrieval (BM25-only / dense-only / hybrid RRF) against
the Open RAG Benchmark (vectara/open_ragbench) — a multimodal (text/table/
image) RAG dataset built from arXiv PDFs, BEIR-format.
Reports Recall@1, Recall@3, Recall@5, Recall@10, MRR, and NDCG@10 overall
and broken down by query source (text / text-table / text-image / text-table-image).
"""
import argparse
import json
import math
import pickle
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from shared.config import settings
from shared.bm25 import BM25Index
from shared.hybrid_retrieval import reciprocal_rank_fusion, score_normalized_fusion
from shared.rate_limiter import BucketConfig, TokenBucketRateLimiter
from shared.vector_store import InMemoryVectorIndex
from shared import embeddings as embeddings_module
from shared.embeddings import embed_text, embed_query, embed_text_batch
from shared.reranker import Reranker

FIXTURE_DIR = Path(__file__).parent / "openrag_fixture"
EMBEDDING_BACKEND = "hash-fallback (offline)"


def configure_real_embedder():
    """Same real-embedder wiring as testbench.py — without this, embed_text()
    silently stays on the deterministic hash fallback even with Ollama/Gemini
    reachable."""
    global EMBEDDING_BACKEND
    if embeddings_module.is_real_embedder():
        EMBEDDING_BACKEND = f"ml-service ({settings.ML_SERVICE_URL}, live)"
    else:
        print(f"[warn] ml-service unreachable at {settings.ML_SERVICE_URL} — embeddings will error.")
        EMBEDDING_BACKEND = f"ml-service unreachable ({settings.ML_SERVICE_URL})"


def load_dataset(data_dir: Path, limit: int = None):
    corpus_json = data_dir / "corpus.json"
    corpus_dir = data_dir / "corpus"

    queries = json.loads((data_dir / "queries.json").read_text())
    qrels = json.loads((data_dir / "qrels.json").read_text())

    if limit:
        query_ids = list(queries.keys())[:limit]
        queries = {k: queries[k] for k in query_ids if k in queries}
        qrels = {k: v for k, v in qrels.items() if k in queries}

    relevant_doc_ids = set(v["doc_id"] for v in qrels.values())

    corpus = {}
    if corpus_json.is_file():
        full_corpus = json.loads(corpus_json.read_text())
        for doc_id, doc in full_corpus.items():
            if doc_id in relevant_doc_ids:
                corpus[doc_id] = doc
    elif corpus_dir.is_dir():
        import re
        prefix_pattern = re.compile(r"^(\d{4}\.\d{4,5})")
        
        # Build mapping of stem/prefix to actual json files
        file_map = {}
        for f in corpus_dir.glob("*.json"):
            match = prefix_pattern.match(f.stem)
            if match:
                file_map[match.group(1)] = f
            file_map[f.stem] = f

        for doc_id in relevant_doc_ids:
            f = file_map.get(doc_id)
            if f and f.is_file():
                doc = json.loads(f.read_text())
                doc_id_actual = doc.get("id", f.stem)
                corpus[doc_id_actual] = doc
    else:
        raise FileNotFoundError(
            f"Neither {corpus_json} nor {corpus_dir}/ found. Expected the dataset's "
            f"corpus.json file or corpus/ directory of per-paper *.json files under {data_dir}."
        )

    return corpus, queries, qrels




def section_text(section: dict) -> str:
    """Prose-only text for a section (tables/images are chunked separately —
    see section_aux_texts — so their signal doesn't get diluted inside a
    256-word prose chunk's averaged dense vector)."""
    return section.get("text", "")


def section_aux_texts(section: dict) -> list:
    """Returns [(modality_tag, text), ...] for each table/image in the
    section, kept as their own standalone chunks."""
    aux = []
    for table_md in (section.get("tables") or {}).values():
        if table_md and table_md.strip():
            aux.append(("tbl", table_md))
    for img in (section.get("images") or {}).values():
        caption = ""
        if isinstance(img, str):
            caption = img
        elif isinstance(img, dict):
            caption = img.get("caption") or img.get("alt") or img.get("text") or ""
        if caption and caption.strip():
            aux.append(("img", caption))
    return aux


def build_indices(corpus: dict):
    dense = InMemoryVectorIndex()
    bm25 = BM25Index()
    chunk_meta = {}  # chunk_id -> (paper_id, section_id)
    
    def chunk_text(text: str, chunk_size: int = 256, overlap: int = 64) -> list:
        words = text.split()
        if len(words) <= chunk_size:
            return [text]
        chunks = []
        step = chunk_size - overlap
        for i in range(0, len(words), step):
            chunk_words = words[i:i+chunk_size]
            chunks.append(" ".join(chunk_words))
            if i + chunk_size >= len(words):
                break
        return chunks

    from tqdm import tqdm
    chunks_to_process = []  # List of (paper_id, section_id, chunk_idx, chunk_text, tag)
    for paper_id, doc in corpus.items():
        for section_id, section in enumerate(doc.get("sections", [])):
            text = section_text(section)
            if text.strip():
                section_chunks = chunk_text(text)
                for chunk_idx, chunk in enumerate(section_chunks):
                    chunks_to_process.append((paper_id, section_id, chunk_idx, chunk, "txt"))

            # Tables/images get their own standalone chunks instead of being
            # appended onto prose and blindly re-sliced — keeps their dense
            # vector un-diluted by surrounding narrative text.
            for aux_idx, (tag, aux_text) in enumerate(section_aux_texts(section)):
                chunks_to_process.append((paper_id, section_id, f"{tag}{aux_idx}", aux_text, tag))

    # Process in batches of 64 sorted by text length to minimize padding overhead on GPU
    batch_size = 64
    results = []
    
    # Sort chunks by length (in characters) to group similar lengths together
    sorted_indices = sorted(range(len(chunks_to_process)), key=lambda idx: len(chunks_to_process[idx][3]))
    
    sorted_embeddings = [None] * len(chunks_to_process)
    for i in tqdm(range(0, len(chunks_to_process), batch_size), desc="Indexing Chunks"):
        batch_idx_list = sorted_indices[i:i+batch_size]
        texts = [chunks_to_process[idx][3] for idx in batch_idx_list]
        embeddings = embed_text_batch(texts)
        for idx, emb in zip(batch_idx_list, embeddings):
            sorted_embeddings[idx] = emb
            
    for item, emb in zip(chunks_to_process, sorted_embeddings):
        paper_id, val_section_id, chunk_idx, chunk, _tag = item
        chunk_id = f"{paper_id}::{val_section_id}::c{chunk_idx}"
        results.append((chunk_id, emb, chunk, paper_id, val_section_id))

    for chunk_id, vector, chunk, paper_id, val_section_id in results:
        dense.upsert(chunk_id, vector, {"content": chunk})
        bm25.add(chunk_id, chunk, {"content": chunk})
        chunk_meta[chunk_id] = (paper_id, val_section_id)
    return dense, bm25, chunk_meta


def eval_ranked(ranked_chunk_ids, gold, chunk_meta):
    # Find rank of gold document (1-indexed)
    rank = None
    for i, cid in enumerate(ranked_chunk_ids, start=1):
        if chunk_meta.get(cid) == gold:
            rank = i
            break

    rr = 1.0 / rank if rank is not None else 0.0

    recalls = {1: 0.0, 3: 0.0, 5: 0.0, 10: 0.0}
    ndcgs = {1: 0.0, 3: 0.0, 5: 0.0, 10: 0.0}

    if rank is not None:
        for k_val in [1, 3, 5, 10]:
            if rank <= k_val:
                recalls[k_val] = 1.0
                ndcgs[k_val] = 1.0 / math.log2(rank + 1)

    return recalls, rr, ndcgs


def run(data_dir: Path, limit: int = None, rerank: str = "none", rebuild_cache: bool = False):
    corpus, queries, qrels = load_dataset(data_dir, limit)
    print(f"Loaded {len(corpus)} documents, {len(queries)} queries "
          f"({'fixture' if data_dir == FIXTURE_DIR else data_dir})")

    cache_name = "index_cache_fixture.pkl" if data_dir == FIXTURE_DIR else "index_cache_real.pkl"
    cache_file = Path(__file__).resolve().parent / cache_name

    dense_idx, bm25_idx, chunk_meta = None, None, None
    cache_loaded = False

    if cache_file.exists() and not rebuild_cache:
        try:
            with open(cache_file, "rb") as f:
                cached_data = pickle.load(f)
            if cached_data.get("corpus_keys") == set(corpus.keys()) and cached_data.get("version") == "v3_split_table_image_chunks":
                print(f"Loading cached indices from {cache_file} (corpus match)...")
                dense_idx = cached_data["dense_idx"]
                bm25_idx = cached_data["bm25_idx"]
                chunk_meta = cached_data["chunk_meta"]
                cache_loaded = True
            else:
                print("Cache corpus mismatch or format version mismatch. Rebuilding cache...")
        except Exception as e:
            print(f"[warn] Failed to load cache: {e}. Rebuilding...")

    if not cache_loaded:
        dense_idx, bm25_idx, chunk_meta = build_indices(corpus)
        try:
            print(f"Saving indices to cache file {cache_file}...")
            with open(cache_file, "wb") as f:
                pickle.dump({
                    "corpus_keys": set(corpus.keys()),
                    "version": "v3_split_table_image_chunks",
                    "dense_idx": dense_idx,
                    "bm25_idx": bm25_idx,
                    "chunk_meta": chunk_meta
                }, f)
        except Exception as e:
            print(f"[warn] Failed to save cache: {e}")

    # Initialize Reranker if requested
    reranker_instance = None
    if rerank != "none":
        reranker_instance = Reranker()
        print("Using ml-service BGE-reranker-v2-m3.")

    from tqdm import tqdm

    # Pre-compute all query embeddings in batched GPU passes
    eval_queries = [(qid, qobj) for qid, qobj in queries.items() if qid in qrels]
    query_texts = [qobj["query"] for _, qobj in eval_queries]

    print(f"Pre-embedding {len(query_texts)} queries in batches...")
    query_embeddings = {}
    batch_size = 64
    for i in tqdm(range(0, len(query_texts), batch_size), desc="Embedding Queries"):
        batch_texts = query_texts[i:i+batch_size]
        batch_qids = [eval_queries[j][0] for j in range(i, min(i+batch_size, len(query_texts)))]
        embeddings = embed_text_batch(batch_texts)
        for qid, emb in zip(batch_qids, embeddings):
            query_embeddings[qid] = emb

    # Check once, not 3045 times
    use_dense = embeddings_module.is_real_embedder()
    weights = [settings.HYBRID_DENSE_WEIGHT, settings.HYBRID_SPARSE_WEIGHT] if use_dense else [0.0, 1.0]

    rows = []
    for qid, qobj in tqdm(eval_queries, desc="Evaluating Queries"):
        gold = (qrels[qid]["doc_id"], qrels[qid]["section_id"])
        query_text = qobj["query"]

        # Use pre-computed embedding
        dense_hits = dense_idx.search(query_embeddings[qid], top_k=30)
        dense_ids = [h["id"] for h in dense_hits]
        dense_scored = [(h["id"], h["score"]) for h in dense_hits]

        bm25_hits = bm25_idx.search(query_text, top_k=30)
        bm25_ids = [h[0] for h in bm25_hits]
        bm25_scored = [(h[0], h[1]) for h in bm25_hits]

        fused = reciprocal_rank_fusion([dense_ids, bm25_ids], weights=weights)
        # score_normalized_fusion fuses actual cosine/BM25 scores (min-max
        # normalized) instead of just rank, avoiding RRF's "presence bonus"
        # where a mediocre-but-in-both-lists doc beats a doc one ranker was
        # very confident about but the other simply didn't retrieve.
        fused_norm = score_normalized_fusion([dense_scored, bm25_scored], weights=weights)

        if reranker_instance is not None:
            candidates = []
            for cid, score in fused_norm:
                content = bm25_idx._metadata.get(cid, {}).get("content", "")
                candidates.append({"id": cid, "score": score, "metadata": {"content": content}})
            # rerank() now blends the cross-encoder score back in with the
            # incoming fused score instead of discarding it outright.
            reranked = reranker_instance.rerank(query_text, candidates, top_k=30)
            hybrid_ids = [c["id"] for c in reranked]
        else:
            hybrid_ids = [cid for cid, _ in fused]

        hybrid_norm_ids = [cid for cid, _ in fused_norm]

        for method, ranked in (("dense", dense_ids), ("bm25", bm25_ids), ("hybrid_rrf", hybrid_ids),
                                ("hybrid_norm", hybrid_norm_ids)):
            recalls, rr, ndcgs = eval_ranked(ranked, gold, chunk_meta)
            rows.append({
                "qid": qid,
                "method": method,
                "source": qobj.get("source", "unknown"),
                "type": qobj.get("type", "unknown"),
                "recalls": recalls,
                "rr": rr,
                "ndcgs": ndcgs,
            })

    return rows


def summarize(rows):
    def agg(subset):
        if not subset:
            return None
        res = {"n": len(subset)}
        for k_val in [1, 3, 5, 10]:
            res[f"recall@{k_val}"] = round(statistics.mean(r["recalls"][k_val] for r in subset), 3)
            res[f"ndcg@{k_val}"] = round(statistics.mean(r["ndcgs"][k_val] for r in subset), 3)
        res["mrr"] = round(statistics.mean(r["rr"] for r in subset), 3)
        return res

    methods = sorted(set(r["method"] for r in rows))
    sources = sorted(set(r["source"] for r in rows))

    print("\n=== Overall Evaluation ===")
    overall = {}
    for m in methods:
        subset = [r for r in rows if r["method"] == m]
        s = agg(subset)
        overall[m] = s
        print(f"  {m:12s}: R@1={s['recall@1']:.3f}  R@3={s['recall@3']:.3f}  "
              f"R@5={s['recall@5']:.3f}  R@10={s['recall@10']:.3f}  "
              f"MRR={s['mrr']:.3f}  nDCG@10={s['ndcg@10']:.3f}  (n={s['n']})")

    print("\n=== By Modality Source ===")
    by_source = {}
    for src in sources:
        print(f"\nModality: {src}")
        by_source[src] = {}
        for m in methods:
            subset = [r for r in rows if r["method"] == m and r["source"] == src]
            s = agg(subset)
            if s:
                by_source[src][m] = s
                print(f"  {m:12s}: R@1={s['recall@1']:.3f}  R@3={s['recall@3']:.3f}  "
                      f"R@5={s['recall@5']:.3f}  R@10={s['recall@10']:.3f}  "
                      f"MRR={s['mrr']:.3f}  (n={s['n']})")

    return {"overall": overall, "by_source": by_source, "embedding_backend": EMBEDDING_BACKEND}


def main():
    parser = argparse.ArgumentParser(description="Evaluate AgentMesh retrieval on Open RAG Benchmark")
    parser.add_argument("--data-dir", type=str, default=None,
                         help="Path to downloaded vectara/open_ragbench (corpus.json/queries.json/qrels.json). "
                              "By default, auto-searches standard paths.")
    parser.add_argument("--limit", type=int, default=None, help="Cap number of queries (real dataset has 3045)")
    parser.add_argument("--rerank", type=str, choices=["none", "lexical", "llm"], default="none",
                        help="Rerank candidates using lexical or LLM reranker")
    parser.add_argument("--rebuild-cache", action="store_true", help="Force rebuild of indexing cache")
    args = parser.parse_args()

    configure_real_embedder()
    print(f"Embedding backend: {EMBEDDING_BACKEND}")

    # Auto-resolve dataset path
    data_dir = None
    if args.data_dir:
        data_dir = Path(args.data_dir)
    else:
        # Search candidate paths relative to this script
        candidates = [
            Path(__file__).resolve().parents[2] / "openragbench/pdf/arxiv",
            Path(__file__).resolve().parents[2] / "data/openragbench",
            Path("openragbench/pdf/arxiv"),
            Path("data/openragbench"),
            FIXTURE_DIR
        ]
        for c in candidates:
            if c.exists() and (c / "queries.json").exists():
                data_dir = c
                break

    if not data_dir:
        data_dir = FIXTURE_DIR

    rows = run(data_dir, limit=args.limit, rerank=args.rerank, rebuild_cache=args.rebuild_cache)
    summary = summarize(rows)

    out_path = Path(__file__).parent / "openrag_report.json"
    out_path.write_text(json.dumps(summary, indent=2))
    print(f"\nFull report written to {out_path}")


if __name__ == "__main__":
    main()
