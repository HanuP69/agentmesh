"""ml-service: ONNX Runtime inference for embeddings and reranking.

Torch-free. Uses raw onnxruntime.InferenceSession + AutoTokenizer.
Models are pre-exported to ONNX during the Docker build stage.
"""
import os
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["VECLIB_MAXIMUM_THREADS"] = "1"
os.environ["NUMEXPR_NUM_THREADS"] = "1"
import threading
try:
    import torch
    try:
        torch.set_num_threads(1)
    except Exception:
        pass
    try:
        torch.set_num_interop_threads(1)
    except Exception:
        pass
    torch_lib = os.path.join(os.path.dirname(torch.__file__), "lib")
    if os.path.exists(torch_lib):
        os.environ["PATH"] += os.pathsep + torch_lib
        if hasattr(os, "add_dll_directory"):
            os.add_dll_directory(torch_lib)
except ImportError:
    pass

import logging
import base64
from io import BytesIO
from pathlib import Path
from typing import List, Optional

import numpy as np
import onnxruntime as ort
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from transformers import AutoTokenizer

app = FastAPI(title="ml-service", description="ONNX-based embedding & reranking")
logger = logging.getLogger("ml-service")

@app.on_event("startup")
def startup_event():
    def warm_models():
        logger.info("Pre-warming models on startup...")
        try:
            _load_bge()
        except Exception as e:
            logger.error(f"Failed to pre-warm BGE: {e}")
        try:
            _load_jina()
        except Exception as e:
            logger.error(f"Failed to pre-warm Jina CLIP: {e}")
        try:
            _load_reranker()
        except Exception as e:
            logger.error(f"Failed to pre-warm Reranker: {e}")
    threading.Thread(target=warm_models, daemon=True).start()

MODELS_DIR = Path(os.getenv("MODELS_DIR", "/app/models"))

# ---------------------------------------------------------------------------
# Lazy-loaded model state
# ---------------------------------------------------------------------------
_bge_session: Optional[ort.InferenceSession] = None
_bge_tokenizer = None
_bge_input_names: Optional[List[str]] = None
_bge_lock = threading.Lock()

_reranker_session: Optional[ort.InferenceSession] = None
_reranker_tokenizer = None
_reranker_input_names: Optional[List[str]] = None
_reranker_lock = threading.Lock()


def _load_bge():
    """Lazy-load BGE-M3 ONNX session + tokenizer (thread-safe)."""
    global _bge_session, _bge_tokenizer, _bge_input_names
    if _bge_session is not None:
        return
    with _bge_lock:
        if _bge_session is not None:
            return
        model_dir = MODELS_DIR / "bge-m3"
        onnx_path = model_dir / "model.onnx"
        if not onnx_path.exists():
            raise RuntimeError(f"BGE-M3 ONNX not found at {onnx_path}")
        logger.info(f"Loading BGE-M3 ONNX from {onnx_path} ...")
        opts = ort.SessionOptions()
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        opts.intra_op_num_threads = os.cpu_count() or 4
        
        session = ort.InferenceSession(
            str(onnx_path), sess_options=opts,
            providers=["CUDAExecutionProvider", "CPUExecutionProvider"]
        )
        input_names = [inp.name for inp in session.get_inputs()]
        tokenizer = AutoTokenizer.from_pretrained(str(model_dir))
        
        # Assign only after everything is fully loaded to prevent race conditions
        _bge_input_names = input_names
        _bge_tokenizer = tokenizer
        _bge_session = session
        logger.info("BGE-M3 ONNX loaded.")


def _load_reranker():
    """Lazy-load BGE-reranker-v2-m3 ONNX session + tokenizer (thread-safe)."""
    global _reranker_session, _reranker_tokenizer, _reranker_input_names
    if _reranker_session is not None:
        return
    with _reranker_lock:
        if _reranker_session is not None:
            return
        model_dir = MODELS_DIR / "bge-reranker"
        onnx_path = model_dir / "model.onnx"
        if not onnx_path.exists():
            raise RuntimeError(f"BGE-reranker ONNX not found at {onnx_path}")
        logger.info(f"Loading BGE-reranker ONNX from {onnx_path} ...")
        opts = ort.SessionOptions()
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        opts.intra_op_num_threads = os.cpu_count() or 4
        
        session = ort.InferenceSession(
            str(onnx_path), sess_options=opts,
            providers=["CUDAExecutionProvider", "CPUExecutionProvider"]
        )
        input_names = [inp.name for inp in session.get_inputs()]
        tokenizer = AutoTokenizer.from_pretrained(str(model_dir))
        
        # Assign only after everything is fully loaded
        _reranker_input_names = input_names
        _reranker_tokenizer = tokenizer
        _reranker_session = session
        logger.info("BGE-reranker ONNX loaded.")


# ---------------------------------------------------------------------------
# Numpy utilities
# ---------------------------------------------------------------------------
def _cls_pool_normalize(token_embeddings: np.ndarray) -> List[float]:
    """BGE-M3 dense embedding: CLS token (position 0), L2-normalized."""
    cls = token_embeddings[:, 0, :]
    norm = np.linalg.norm(cls, axis=1, keepdims=True)
    normalized = cls / np.maximum(norm, 1e-9)
    return normalized[0].tolist()


def _cls_pool_normalize_batch(token_embeddings: np.ndarray) -> List[List[float]]:
    """BGE-M3 dense embedding: CLS token (position 0), L2-normalized (batch mode)."""
    cls = token_embeddings[:, 0, :]
    norm = np.linalg.norm(cls, axis=1, keepdims=True)
    normalized = cls / np.maximum(norm, 1e-9)
    return normalized.tolist()


def _sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-x))


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------
class EmbedTextReq(BaseModel):
    text: str

class EmbedTextBatchReq(BaseModel):
    texts: List[str]

class EmbedTableReq(BaseModel):
    text: str

class EmbedImageReq(BaseModel):
    image_b64: str

class EmbedImageQueryReq(BaseModel):
    text: str

class RerankReq(BaseModel):
    query: str
    candidates: List[str]
    top_k: int = 5


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@app.get("/health")
def health():
    return {
        "status": "ok",
        "backend": "onnxruntime",
        "bge_m3_loaded": _bge_session is not None,
        "bge_m3_providers": _bge_session.get_providers() if _bge_session else None,
        "jina_clip_v2_loaded": _jina is not None,
        "reranker_loaded": _reranker_session is not None,
        "reranker_providers": _reranker_session.get_providers() if _reranker_session else None,
    }


@app.post("/embed/text")
def embed_text(req: EmbedTextReq):
    _load_bge()
    inputs = _bge_tokenizer(
        req.text, return_tensors="np",
        padding=True, truncation=True, max_length=512,
    )
    feed = {k: v for k, v in inputs.items() if k in _bge_input_names}
    outputs = _bge_session.run(None, feed)
    embedding = _cls_pool_normalize(outputs[0])
    return {"embedding": embedding}


@app.post("/embed/text_batch")
def embed_text_batch(req: EmbedTextBatchReq):
    _load_bge()
    if not req.texts:
        return {"embeddings": []}
    inputs = _bge_tokenizer(
        req.texts, return_tensors="np",
        padding=True, truncation=True, max_length=512,
    )
    feed = {k: v for k, v in inputs.items() if k in _bge_input_names}
    outputs = _bge_session.run(None, feed)
    embeddings = _cls_pool_normalize_batch(outputs[0])
    return {"embeddings": embeddings}


@app.post("/embed/table")
def embed_table(req: EmbedTableReq):
    _load_bge()
    inputs = _bge_tokenizer(
        req.text, return_tensors="np",
        padding=True, truncation=True, max_length=512,
    )
    feed = {k: v for k, v in inputs.items() if k in _bge_input_names}
    outputs = _bge_session.run(None, feed)
    embedding = _cls_pool_normalize(outputs[0])
    return {"embedding": embedding}


_jina = None
_jina_lock = threading.Lock()

def _load_jina():
    global _jina
    if _jina is not None:
        return _jina
    with _jina_lock:
        if _jina is not None:
            return _jina
        logger.info("Loading Jina-CLIP-v2 in PyTorch CPU mode...")
        from transformers import AutoModel
        _jina = AutoModel.from_pretrained("jinaai/jina-clip-v2", trust_remote_code=True)
        _jina.to("cpu")
        _jina.eval()
        logger.info("Jina-CLIP-v2 loaded.")
    return _jina


@app.post("/embed/image")
def embed_image(req: EmbedImageReq):
    import io
    from PIL import Image
    model = _load_jina()
    try:
        img_bytes = base64.b64decode(req.image_b64)
        img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
        vec = model.encode_image([img], normalize_embeddings=True)[0]
        return {"embedding": vec.tolist() if hasattr(vec, "tolist") else list(vec)}
    except Exception as e:
        logger.error(f"Image embedding failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/embed/image_query")
def embed_image_query(req: EmbedImageQueryReq):
    model = _load_jina()
    try:
        vec = model.encode_text([req.text], normalize_embeddings=True)[0]
        return {"embedding": vec.tolist() if hasattr(vec, "tolist") else list(vec)}
    except Exception as e:
        logger.error(f"Image query embedding failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/rerank")
def rerank(req: RerankReq):
    _load_reranker()
    if not req.candidates:
        return {"ranked": []}

    pairs = [(req.query, c) for c in req.candidates]
    inputs = _reranker_tokenizer(
        pairs, return_tensors="np",
        padding=True, truncation="only_second", max_length=1024,
    )
    feed = {k: v for k, v in inputs.items() if k in _reranker_input_names}
    outputs = _reranker_session.run(None, feed)

    logits = outputs[0]  # shape (n_pairs, num_labels)
    # For bge-reranker, logits has 1 column — squeeze and sigmoid
    if logits.ndim == 2 and logits.shape[1] == 1:
        scores = _sigmoid(logits[:, 0])
    else:
        scores = _sigmoid(logits[:, 0]) if logits.ndim == 2 else _sigmoid(logits)

    indexed_scores = list(enumerate(scores.tolist()))
    indexed_scores.sort(key=lambda x: x[1], reverse=True)
    ranked = indexed_scores[: req.top_k]
    return {"ranked": ranked}
