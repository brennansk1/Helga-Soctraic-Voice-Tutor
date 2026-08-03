"""Embeddings via Ollama — no PyTorch in the service process.

WHY
---
Dense retrieval previously meant `sentence-transformers`, which drags in
PyTorch + transformers: hundreds of MB on disk, far more resident, and a
recurring source of dependency conflicts (torch / transformers /
huggingface-hub churn). On a 24 GB Mac Mini that is already swapping, paying
that twice — once in `core`, once in `rag` — is not affordable.

Ollama is already running and already serves embeddings. Using it:

  * removes PyTorch from the service entirely — zero new Python dependencies
  * UPGRADES the model: bge-m3 is 1024-dim vs all-MiniLM-L6-v2's 384
  * lets Ollama own the model's memory, so it is shared and can be evicted,
    instead of being pinned inside a long-lived Python process

Verified working before this module was written:
    POST /api/embed {"model":"bge-m3","input":"eigenvalue"} -> 1024 dims

MIGRATION NOTE
--------------
The dimension changes 384 -> 1024, so any previously built dense index is
INVALID and must be rebuilt. `expected_dim()` is exposed so callers can detect
a stale index rather than silently comparing vectors of different spaces.
"""

import logging
import os
import time

logger = logging.getLogger(__name__)

# bge-m3 is already pulled on the target machine.
EMBED_MODEL = os.getenv("EMBED_MODEL", "bge-m3")
_OLLAMA = os.getenv("OLLAMA_URL", "http://host.docker.internal:11434")
BATCH = int(os.getenv("EMBED_BATCH", "32"))
TIMEOUT_S = float(os.getenv("EMBED_TIMEOUT_S", "120"))

_dim_cache = {}


class EmbeddingUnavailable(RuntimeError):
    """Raised when embeddings cannot be produced at all."""


def _endpoint():
    return f"{_OLLAMA.rstrip('/')}/api/embed"


def _post_batch(texts, model, retries=2):
    import requests
    last = None
    for attempt in range(retries + 1):
        try:
            r = requests.post(_endpoint(),
                              json={"model": model, "input": texts},
                              timeout=TIMEOUT_S)
            if r.status_code != 200:
                last = f"HTTP {r.status_code}: {r.text[:160]}"
            else:
                data = r.json()
                vecs = data.get("embeddings")
                # Older Ollama used {"embedding": [...]} for a single input.
                if vecs is None and "embedding" in data:
                    vecs = [data["embedding"]]
                if not vecs or len(vecs) != len(texts):
                    last = f"expected {len(texts)} vectors, got {len(vecs or [])}"
                else:
                    return vecs
        except Exception as e:
            last = str(e)
        if attempt < retries:
            time.sleep(1.5 * (attempt + 1))
    raise EmbeddingUnavailable(f"embedding failed ({model}): {last}")


def embed(texts, model=None):
    """Embed a list of strings. Returns list[list[float]].

    Batched, because a whole course's concepts can be thousands of strings and
    one giant request is a good way to hit a timeout with nothing to show.
    """
    if isinstance(texts, str):
        texts = [texts]
    texts = [t if isinstance(t, str) else str(t) for t in texts]
    if not texts:
        return []
    mdl = model or EMBED_MODEL
    out = []
    for i in range(0, len(texts), BATCH):
        chunk = texts[i:i + BATCH]
        # Ollama rejects empty strings; substitute a single space so indices
        # stay aligned with the caller's list.
        chunk = [t if t.strip() else " " for t in chunk]
        out.extend(_post_batch(chunk, mdl))
    return out


def get_embed_fn(model=None):
    """Return a callable with the (list[str]) -> vectors contract that
    StorageManager.build_dense_index / hybrid_search expect."""
    mdl = model or EMBED_MODEL

    def _fn(texts):
        return embed(texts, model=mdl)

    return _fn


def expected_dim(model=None):
    """Dimensionality of the configured model, probed once and cached.

    Callers should compare this against a stored index's dimension: switching
    embedding models invalidates the index, and comparing vectors across
    different spaces produces silently wrong neighbours rather than an error.
    """
    mdl = model or EMBED_MODEL
    if mdl not in _dim_cache:
        _dim_cache[mdl] = len(embed(["dimension probe"], model=mdl)[0])
    return _dim_cache[mdl]


def is_available(model=None):
    """Cheap health check — does the endpoint actually serve this model?"""
    try:
        expected_dim(model)
        return True
    except Exception as e:
        logger.warning(f"embeddings unavailable: {e}")
        return False
