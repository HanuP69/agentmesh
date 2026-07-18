"""Synthesizer Service — wraps LLM clients (Ollama/NIM/Gemini) behind a
unified HTTP API. Other services call /chat and /synthesize instead of
importing LLM clients directly."""
import logging
import requests as http_requests

from fastapi import FastAPI
from pydantic import BaseModel
from typing import Optional, List

from shared.config import settings
from shared.circuit_breaker import CircuitBreaker
from shared.rate_limiter import TokenBucketRateLimiter, BucketConfig

logger = logging.getLogger(__name__)
app = FastAPI(title="AgentMesh Synthesizer Service")

# --- Redis (optional) ---
redis_client = None
if settings.USE_REDIS:
    try:
        import redis
        redis_client = redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)
        redis_client.ping()
    except Exception:
        redis_client = None

# --- Rate limiter & Circuit breakers ---
rate_limiter = TokenBucketRateLimiter(redis_client=redis_client)
breakers = {
    "text_agent": CircuitBreaker("text_agent"),
    "image_agent": CircuitBreaker("image_agent"),
    "table_agent": CircuitBreaker("table_agent"),
    "synthesizer": CircuitBreaker("synthesizer"),
}


# --- LLM Clients (copied from monolith, imports changed to shared) ---
class NimClient:
    def __init__(self, rl, br):
        self.rate_limiter = rl
        self.breakers = br
        self._headers = {"Authorization": f"Bearer {settings.NIM_API_KEY}", "Content-Type": "application/json"}

    def chat(self, agent_type, prompt, model=None, max_tokens=None):
        if not self.rate_limiter.allow(agent_type):
            raise RuntimeError(f"rate limit exceeded for {agent_type}")
        def _call():
            if not settings.NIM_API_KEY:
                return f"[stub-response] summarized({len(prompt)} chars of context)"
            payload = {"model": model or settings.NIM_LLM_MODEL, "messages": [{"role": "user", "content": prompt}], "max_tokens": max_tokens or 512}
            resp = http_requests.post(f"{settings.NIM_BASE_URL}/chat/completions", headers=self._headers, json=payload, timeout=30)
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]
        breaker = self.breakers.get(agent_type)
        return breaker.call(_call) if breaker else _call()


class OllamaClient:
    def __init__(self, rl, br):
        import threading
        self.rate_limiter = rl
        self.breakers = br
        self.base_url = settings.OLLAMA_BASE_URL
        self._available = None
        self._last_probed = 0.0
        self._is_llama_cpp = False
        self._lock = threading.Lock()

    def is_available(self):
        import time
        now = time.time()
        if self._available is not None and self._last_probed + 10.0 > now:
            return self._available
        self._last_probed = now
        try:
            r = http_requests.get(f"{self.base_url}/api/tags", timeout=2)
            if r.status_code == 200:
                self._available = True; self._is_llama_cpp = False; return True
        except Exception: pass
        try:
            r = http_requests.get(f"{self.base_url}", timeout=2)
            if r.headers.get("Server") == "llama.cpp" or r.status_code == 404:
                self._available = True; self._is_llama_cpp = True; return True
        except Exception: pass
        self._available = False; return False

    def chat(self, agent_type, prompt, model=None, max_tokens=None):
        if not self.rate_limiter.allow(agent_type):
            raise RuntimeError(f"rate limit exceeded for {agent_type}")
        def _call():
            if not self.is_available():
                return f"[ollama-offline-stub] summarized({len(prompt)} chars of context)"
            model_name = model
            if model_name is None:
                model_name = settings.OLLAMA_SYNTH_MODEL if agent_type == "synthesizer" else settings.OLLAMA_LLM_MODEL
            payload = {"model": model_name, "messages": [{"role": "user", "content": prompt}], "stream": False, "keep_alive": settings.OLLAMA_KEEP_ALIVE}
            if max_tokens is not None:
                payload["options"] = {"num_predict": max_tokens}
            try:
                # ACQUIRE LOCK TO PREVENT CONCURRENT OLLAMA GPU OVERLOAD on 6GB VRAM!
                with self._lock:
                    resp = http_requests.post(f"{self.base_url}/api/chat", json=payload, timeout=120)
                resp.raise_for_status()
                return resp.json()["message"]["content"]
            except Exception as e:
                logger.warning(f"Ollama call failed: {e}. Falling back to offline stub.")
                return f"[ollama-offline-stub] summarized({len(prompt)} chars of context)"
        breaker = self.breakers.get(agent_type)
        return breaker.call(_call) if breaker else _call()


class GeminiClient:
    def __init__(self, rl, br):
        self.rate_limiter = rl
        self.breakers = br
        self.base_url = settings.GEMINI_BASE_URL
        self.api_key = settings.GEMINI_API_KEY

    def chat(self, agent_type, prompt, model=None, max_tokens=None):
        if not self.rate_limiter.allow(agent_type):
            raise RuntimeError(f"rate limit exceeded for {agent_type}")
        def _call():
            if not self.api_key:
                return f"[gemini-offline-stub] summarized({len(prompt)} chars of context)"
            model_name = model or settings.GEMINI_LLM_MODEL
            url = f"{self.base_url}/v1beta/models/{model_name}:generateContent"
            resp = http_requests.post(url, params={"key": self.api_key}, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=30)
            resp.raise_for_status()
            return resp.json()["candidates"][0]["content"]["parts"][0]["text"]
        breaker = self.breakers.get(agent_type)
        return breaker.call(_call) if breaker else _call()


# --- Provider selection ---
_PROVIDERS = {
    "ollama": OllamaClient(rate_limiter, breakers),
    "nim": NimClient(rate_limiter, breakers),
    "gemini": GeminiClient(rate_limiter, breakers),
}
llm_client = _PROVIDERS.get(settings.LLM_PROVIDER, _PROVIDERS["nim"])

def get_client(provider: Optional[str] = None):
    if not provider:
        return llm_client
    return _PROVIDERS.get(provider.lower(), llm_client)


# --- Request/Response models ---
class ChatReq(BaseModel):
    agent_type: str
    prompt: str
    max_tokens: Optional[int] = None
    provider: Optional[str] = None

class SynthesizeReq(BaseModel):
    query: str
    context: List[dict]
    provider: Optional[str] = None


# --- Routes ---
@app.get("/health")
def health():
    provider_ready = True
    if settings.LLM_PROVIDER == "ollama" and isinstance(llm_client, OllamaClient):
        provider_ready = llm_client.is_available()
    return {"status": "ok", "provider": settings.LLM_PROVIDER, "provider_ready": provider_ready}


@app.post("/chat")
def chat(req: ChatReq):
    client = get_client(req.provider)
    response = client.chat(req.agent_type, req.prompt, max_tokens=req.max_tokens)
    return {"response": response}


@app.post("/synthesize")
def synthesize(req: SynthesizeReq):
    logger.info(f"Received synthesize request: query='{req.query}', provider='{req.provider}'")
    context_lines = []
    for r in req.context[:8]:
        mod = r.get('modality', '?')
        src = r.get('source', '?')
        content = r.get('content', '')
        if mod == 'image':
            context_lines.append(f"[image:{src}] (Extracted Image: {content})")
        else:
            context_lines.append(f"[{mod}:{src}] {content}")
    context = "\n\n".join(context_lines)
    prompt = (
        f"Answer the query using only the provided context, and cite sources "
        f"as [modality:source]. If the context contains an image, you can explicitly "
        f"refer to it using the [image:source] citation tag.\n\nQuery: {req.query}\n\nContext:\n{context}\n\nAnswer:"
    )
    try:
        client = get_client(req.provider)
        logger.info(f"Selected client: {client.__class__.__name__}")
        answer = client.chat("synthesizer", prompt)
    except Exception as e:
        logger.error(f"Synthesis failed: {e}", exc_info=True)
        answer = f"(synthesis unavailable: {e}) Top result: {req.context[0].get('content','none') if req.context else 'none'}"
    return {"answer": answer}


@app.get("/breakers")
def get_breakers():
    return [b.snapshot() for b in breakers.values()]


@app.get("/rate-limiter/rejections")
def get_rejections():
    return rate_limiter.rejection_counts()
