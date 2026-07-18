"""Ingestion Service — document upload, chunking, embedding, indexing.
Handles the write path: /ingest/text, /ingest/table, /ingest/image, /ingest/upload."""
import logging
import uuid

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from shared.config import settings
from shared.hashing import ModalityHashRings
from shared.hybrid_retrieval import HybridRetriever
from shared.vector_store import create_vector_store
from shared.embeddings import embed_text, embed_table, embed_image_clip, embed_image_text, configure_clip, configure_embedder

logger = logging.getLogger(__name__)
app = FastAPI(title="AgentMesh Ingestion Service")

# --- Redis ---
redis_client = None
if settings.USE_REDIS:
    try:
        import redis
        redis_client = redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)
        redis_client.ping()
    except Exception:
        redis_client = None

# --- Infrastructure ---
rings = ModalityHashRings(vnodes=settings.VNODES)
for modality in ["text", "table", "image"]:
    for i in range(settings.SHARD_NODES):
        rings.add_node(modality, f"shard-{i}")

store = create_vector_store(settings.VECTOR_BACKEND, settings.PGVECTOR_DSN)
hybrid = HybridRetriever(redis_client=redis_client)

# Wire real embeddings (Ollama/Gemini) instead of hash fallback
configure_embedder(provider=settings.LLM_PROVIDER,
                   ollama_base_url=settings.OLLAMA_BASE_URL)


# --- Chunking functions (from pipelines.py) ---
def chunk_text(text, chunk_size=256, overlap=64):
    words = text.split()
    chunks = []
    i = 0
    while i < len(words):
        chunk = " ".join(words[i:i + chunk_size])
        if chunk.strip():
            chunks.append(chunk)
        i += chunk_size - overlap
    return chunks or [text]


def chunk_table(table_text):
    lines = [l for l in table_text.strip().splitlines() if l.strip()]
    if not lines:
        return [table_text]
    header = lines[0]
    chunks = []
    for row in lines[1:]:
        chunks.append(f"{header}\n{row}")
    return chunks or [table_text]


def extract_pdf_text(pdf_bytes):
    try:
        import fitz  # PyMuPDF
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        text = ""
        for page in doc:
            text += page.get_text() + "\n"
        return text
    except Exception as e:
        logger.warning(f"PDF extraction failed: {e}")
        return ""


def extract_pdf_images(pdf_bytes):
    images = []
    try:
        import fitz  # PyMuPDF
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        for page_num in range(len(doc)):
            page = doc[page_num]
            image_list = page.get_images(full=True)
            for img_index, img in enumerate(image_list):
                xref = img[0]
                base_image = doc.extract_image(xref)
                images.append((base_image["image"], base_image["ext"], page_num + 1))
    except Exception as e:
        logger.warning(f"PDF image extraction failed: {e}")
    return images


def extract_pdf_tables(pdf_bytes):
    tables = []
    try:
        import fitz  # PyMuPDF
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        for page_num in range(len(doc)):
            page = doc[page_num]
            if hasattr(page, "find_tables"):
                found_tables = page.find_tables()
                for t_idx, table in enumerate(found_tables):
                    data = table.extract()
                    if data:
                        csv_lines = []
                        for row in data:
                            cleaned_row = [str(cell or "").replace("\n", " ").strip() for cell in row]
                            csv_lines.append(",".join(cleaned_row))
                        tables.append(("\n".join(csv_lines), page_num + 1))
    except Exception as e:
        logger.warning(f"PDF table extraction failed: {e}")
    return tables


# --- Ingestion Pipeline ---
class IngestionPipeline:
    def __init__(self, rings, store, hybrid):
        self.rings = rings
        self.store = store
        self.hybrid = hybrid

    def ingest_text(self, doc_id_prefix, text, source):
        chunks = chunk_text(text)
        for i, chunk in enumerate(chunks):
            doc_id = f"{doc_id_prefix}:text:{i}"
            vec = embed_text(chunk)
            shard = self.rings.route("text", doc_id) or "shard-0"
            self.store.upsert("text", shard, doc_id, vec, {"content": chunk, "source": source})
            self.hybrid.index_doc("text", shard, doc_id, chunk, {"content": chunk, "source": source})
        return len(chunks)

    def ingest_table(self, doc_id_prefix, table_text, source):
        chunks = chunk_table(table_text)
        for i, chunk in enumerate(chunks):
            doc_id = f"{doc_id_prefix}:table:{i}"
            vec = embed_table(chunk)
            shard = self.rings.route("table", doc_id) or "shard-0"
            self.store.upsert("table", shard, doc_id, vec, {"content": chunk, "source": source})
            self.hybrid.index_doc("table", shard, doc_id, chunk, {"content": chunk, "source": source})
        return len(chunks)

    def ingest_image(self, doc_id_prefix, image_bytes, source, caption=""):
        import base64
        base64_str = base64.b64encode(image_bytes).decode("utf-8")
        doc_id = f"{doc_id_prefix}:image:0"
        shard = self.rings.route("image", doc_id) or "shard-0"
        meta = {"content": caption or "[image]", "source": source, "image_base64": base64_str}
        try:
            vec = embed_image_clip(image_bytes)
            self.store.upsert("image", shard, doc_id, vec, meta)
        except Exception as e:
            logger.warning(f"Image embedding unavailable, skipping dense index for {doc_id}: {e}")
        if caption:
            self.hybrid.index_doc("image", shard, doc_id, caption, meta)
        return 1

    def ingest_pdf(self, doc_id_prefix, pdf_bytes, source):
        chunks_count = 0
        
        # 1. Ingest text
        text = extract_pdf_text(pdf_bytes)
        if text.strip():
            chunks_count += self.ingest_text(doc_id_prefix, text, source)
        
        # 2. Ingest extracted tables
        pdf_tables = extract_pdf_tables(pdf_bytes)
        for idx, (table_text, page_num) in enumerate(pdf_tables):
            table_doc_id = f"{doc_id_prefix}_tbl_{page_num}_{idx}"
            self.ingest_table(table_doc_id, table_text, source)
            chunks_count += 1

        # 3. Ingest extracted images
        pdf_images = extract_pdf_images(pdf_bytes)
        for idx, (img_bytes, img_ext, page_num) in enumerate(pdf_images):
            img_doc_id = f"{doc_id_prefix}_img_{page_num}_{idx}"
            caption = f"Image extracted from page {page_num} of {source}"
            self.ingest_image(img_doc_id, img_bytes, source, caption=caption)
            chunks_count += 1
            
        return chunks_count


pipeline = IngestionPipeline(rings, store, hybrid)

TEXT_EXTS = {".txt", ".md"}
TABLE_EXTS = {".csv", ".tsv"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
PDF_EXTS = {".pdf"}
MAX_UPLOAD_BYTES = 25 * 1024 * 1024


@app.on_event("startup")
def startup():
    configure_clip(settings.CLIP_MODEL)


# --- Routes ---
@app.get("/health")
def health():
    return {"status": "ok", "service": "ingestion"}


@app.post("/ingest/text")
def ingest_text(doc_id: str, source: str, text: str):
    n = pipeline.ingest_text(doc_id, text, source)
    return {"chunks_ingested": n}


@app.post("/ingest/table")
def ingest_table(doc_id: str, source: str, table_text: str):
    n = pipeline.ingest_table(doc_id, table_text, source)
    return {"chunks_ingested": n}


@app.post("/ingest/image")
def ingest_image(doc_id: str, source: str, caption: str = "", file: UploadFile = File(...)):
    data = file.file.read()
    n = pipeline.ingest_image(doc_id, data, source, caption)
    return {"chunks_ingested": n}


@app.post("/ingest/upload")
def ingest_upload(file: UploadFile = File(...), caption: str = Form("")):
    name = file.filename or "upload"
    ext = "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""
    data = file.file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="file too large (25MB limit)")
    doc_id = name.rsplit(".", 1)[0][:40] or "upload"

    if ext in TEXT_EXTS:
        text = data.decode("utf-8", errors="replace")
        n = pipeline.ingest_text(doc_id, text, name)
        modality = "text"
    elif ext in TABLE_EXTS:
        text = data.decode("utf-8", errors="replace")
        n = pipeline.ingest_table(doc_id, text, name)
        modality = "table"
    elif ext in IMAGE_EXTS:
        n = pipeline.ingest_image(doc_id, data, name, caption)
        modality = "image"
    elif ext in PDF_EXTS:
        n = pipeline.ingest_pdf(doc_id, data, name)
        modality = "multimodal"
    else:
        raise HTTPException(status_code=400, detail=f"unsupported file type: {ext or 'unknown'}")
    return {"filename": name, "modality": modality, "chunks_ingested": n}
