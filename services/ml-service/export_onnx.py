"""One-time ONNX export script.

Runs inside Docker build stage 1 (the "exporter" stage).
Downloads BGE-M3 and BGE-reranker-v2-m3 from HuggingFace,
exports them to ONNX format, and saves tokenizers alongside.

The exported files are copied into the runtime stage by the Dockerfile.
"""
import os
from pathlib import Path

MODELS_DIR = Path(os.getenv("MODELS_DIR", "/models"))


def export_bge_m3():
    """Export BAAI/bge-m3 to ONNX for dense text/table embeddings."""
    from optimum.onnxruntime import ORTModelForFeatureExtraction
    from transformers import AutoTokenizer

    out = MODELS_DIR / "bge-m3"
    print(f"[export] Exporting BAAI/bge-m3 → {out} ...")

    model = ORTModelForFeatureExtraction.from_pretrained(
        "BAAI/bge-m3", export=True
    )
    tokenizer = AutoTokenizer.from_pretrained("BAAI/bge-m3")

    model.save_pretrained(str(out))
    tokenizer.save_pretrained(str(out))
    print(f"[export] BGE-M3 done. Files: {list(out.iterdir())}")


def export_reranker():
    """Export BAAI/bge-reranker-v2-m3 to ONNX for cross-encoder reranking."""
    from optimum.onnxruntime import ORTModelForSequenceClassification
    from transformers import AutoTokenizer

    out = MODELS_DIR / "bge-reranker"
    print(f"[export] Exporting BAAI/bge-reranker-v2-m3 → {out} ...")

    model = ORTModelForSequenceClassification.from_pretrained(
        "BAAI/bge-reranker-v2-m3", export=True
    )
    tokenizer = AutoTokenizer.from_pretrained("BAAI/bge-reranker-v2-m3")

    model.save_pretrained(str(out))
    tokenizer.save_pretrained(str(out))
    print(f"[export] Reranker done. Files: {list(out.iterdir())}")


if __name__ == "__main__":
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    export_bge_m3()
    export_reranker()
    print("[export] All exports complete.")
