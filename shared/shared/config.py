import os


class Settings:
    REDIS_URL: str = os.getenv("REDIS_URL", "redis://redis:6379/0")
    MONGO_URL: str = os.getenv("MONGO_URL", "mongodb://mongo:27017")
    MONGO_DB: str = os.getenv("MONGO_DB", "agentmesh")
    VECTOR_BACKEND: str = os.getenv("VECTOR_BACKEND", "pgvector")  # pgvector or memory
    ML_SERVICE_URL: str = os.getenv("ML_SERVICE_URL")
    if not ML_SERVICE_URL:
        if os.name == "nt":
            ML_SERVICE_URL = "http://localhost:8099"
        else:
            ML_SERVICE_URL = "http://nginx-internal:8080/ml"
    elif os.name == "nt" and "nginx-internal" in ML_SERVICE_URL:
        ML_SERVICE_URL = "http://localhost:8099"
    PGVECTOR_DSN: str = os.getenv("PGVECTOR_DSN", "postgresql://postgres:postgres@pgvector:5432/agentmesh")
    NIM_API_KEY: str = os.getenv("NIM_API_KEY", "")
    NIM_BASE_URL: str = os.getenv("NIM_BASE_URL", "https://integrate.api.nvidia.com/v1")
    NIM_LLM_MODEL: str = os.getenv("NIM_LLM_MODEL", "meta/llama-3.1-70b-instruct")
    NIM_VISION_MODEL: str = os.getenv("NIM_VISION_MODEL", "nvidia/neva-22b")
    CLIP_MODEL: str = os.getenv("CLIP_MODEL", "ViT-B-32")

    LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "ollama")  # ollama (local dev) | nim | gemini (container)
    OLLAMA_BASE_URL: str = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
    # Defaults below match models already pulled locally (`ollama ls`) rather
    # than requiring a fresh pull. Swap via env if you pull nomic-embed-text etc.
    # gemma4:e4b is natively multimodal (unlike llava, a separate
    # vision-adapter architecture) -- one resident model handles both
    # per-agent text calls AND image captioning, so there's no model swap
    # between those two call types. Only the final-synthesis call swaps to
    # the larger qwen2.5, since that's a single call per query where quality
    # matters most.
    OLLAMA_LLM_MODEL: str = os.getenv("OLLAMA_LLM_MODEL", "gemma4:e4b")               # light, per-agent calls
    OLLAMA_SYNTH_MODEL: str = os.getenv("OLLAMA_SYNTH_MODEL", "gemma4:e4b")  # final answer quality
    OLLAMA_VISION_MODEL: str = os.getenv("OLLAMA_VISION_MODEL", "gemma4:e4b")         # native multimodal, image captioning
    OLLAMA_EMBED_MODEL: str = os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text")     # dedicated embedding model
    # keep_alive controls how long a model stays resident in VRAM after a
    # call. "0" unloads immediately (true lazy-load, safest for 6GB VRAM
    # when swapping between gemma4:e4b and qwen2.5) at the cost of reload
    # latency on the next call. Raise to e.g. "5m" if VRAM allows keeping
    # a model warm across a query.
    OLLAMA_KEEP_ALIVE: str = os.getenv("OLLAMA_KEEP_ALIVE", "0")

    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
    GEMINI_BASE_URL: str = os.getenv("GEMINI_BASE_URL", "https://generativelanguage.googleapis.com")
    GEMINI_LLM_MODEL: str = os.getenv("GEMINI_LLM_MODEL", "gemini-3.1-flash-lite")
    GEMINI_EMBED_MODEL: str = os.getenv("GEMINI_EMBED_MODEL", "text-embedding-004")

    RRF_K: int = int(os.getenv("RRF_K", "60"))
    HYBRID_DENSE_WEIGHT: float = float(os.getenv("HYBRID_DENSE_WEIGHT", "0.25"))
    HYBRID_SPARSE_WEIGHT: float = float(os.getenv("HYBRID_SPARSE_WEIGHT", "0.75"))
    USE_HYDE: bool = os.getenv("USE_HYDE", "false").lower() == "true"

    # --- Auth ---
    GOOGLE_CLIENT_ID: str = os.getenv("GOOGLE_CLIENT_ID", "")
    JWT_SECRET: str = os.getenv("JWT_SECRET", "dev-insecure-secret-change-me")
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = int(os.getenv("JWT_EXPIRE_MINUTES", str(60 * 24 * 7)))  # 7 days
    COOKIE_NAME: str = os.getenv("COOKIE_NAME", "agentmesh_session")
    COOKIE_SECURE: bool = os.getenv("COOKIE_SECURE", "false").lower() == "true"
    FRONTEND_ORIGIN: str = os.getenv("FRONTEND_ORIGIN", "http://localhost:5173")
    USE_REDIS: bool = os.getenv("USE_REDIS", "true").lower() == "true"
    VNODES: int = int(os.getenv("VNODES", "150"))
    SHARD_NODES: int = int(os.getenv("SHARD_NODES", "3"))


settings = Settings()
