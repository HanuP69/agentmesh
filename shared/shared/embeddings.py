import os
import requests
import socket

ML_SERVICE_URL = os.getenv("ML_SERVICE_URL")
if not ML_SERVICE_URL or (os.name == "nt" and "nginx-internal" in ML_SERVICE_URL):
    ML_SERVICE_URL = "http://localhost:8099"
else:
    try:
        socket.getaddrinfo("nginx-internal", 8080)
        ML_SERVICE_URL = "http://nginx-internal:8080/ml"
    except socket.gaierror:
        ML_SERVICE_URL = "http://localhost:8099"


def configure_embedder(*args, **kwargs):
    pass


def configure_clip(*args, **kwargs):
    pass


def _post(path, payload):
    r = requests.post(f"{ML_SERVICE_URL}{path}", json=payload, timeout=120)
    r.raise_for_status()
    return r.json()["embedding"]


def embed_text(text: str) -> list:
    return _post("/embed/text", {"text": text})


def embed_text_batch(texts: list) -> list:
    r = requests.post(f"{ML_SERVICE_URL}/embed/text_batch", json={"texts": texts}, timeout=120)
    r.raise_for_status()
    return r.json()["embeddings"]


def embed_query(query: str) -> list:
    return _post("/embed/text", {"text": query})


def embed_table(table_text: str) -> list:
    return _post("/embed/table", {"text": table_text})


def embed_image_clip(image_bytes: bytes) -> list:
    import base64
    return _post("/embed/image", {"image_b64": base64.b64encode(image_bytes).decode()})


def embed_image_text(text: str) -> list:
    return _post("/embed/image_query", {"text": text})


def is_real_embedder() -> bool:
    try:
        r = requests.get(f"{ML_SERVICE_URL}/health", timeout=5)
        return r.ok
    except Exception:
        return False
