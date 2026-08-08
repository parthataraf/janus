"""Text embedding, provider-switchable.

    EMBED_PROVIDER=api    (default) any OpenAI-compatible /embeddings endpoint,
                          sharing OPENAI_BASE_URL / OPENAI_API_KEY with generation.
    EMBED_PROVIDER=local  sentence-transformers, in-process, free, offline.

Four invariants are encoded here:

  1. The SAME model embeds both documents (at ingest) and queries (at search).
     Mixing models puts the two in incompatible vector spaces and silently
     wrecks retrieval.
  2. The local model is loaded ONCE (lazy singleton). It is hundreds of MB and
     takes seconds to initialize; re-loading per call would be crippling.
  3. ORDER SAFETY. An embeddings API returns a `data` array that is NOT
     guaranteed to be in request order, so it is sorted by its `index` field
     before pairing back to inputs. Assuming positional order silently assigns
     the wrong vector to every chunk — that failure mode is indistinguishable
     from "the model is bad", which is exactly how it hides.
  4. NEVER emit a bad vector. A wrong-width, non-finite, or all-zero vector is
     raised as `EmbeddingUnavailable`, never written to the database and never
     used as a query. Retrieval degrades honestly instead of returning garbage
     ranked confidently.

Provider quirk this module exists to absorb: OpenRouter tunnels upstream
failures as **HTTP 200 with an `{"error": ...}` body**. Status-code-only
handling treats a 429 "engine overloaded" as success and then dies on a
KeyError somewhere downstream. We inspect the body, not just the status.
"""

from __future__ import annotations

import json
import logging
import math
import time

from core import config

logger = logging.getLogger("docpilot")


class EmbeddingUnavailable(RuntimeError):
    """The embedding provider could not produce a trustworthy vector.

    Raised rather than returning a degraded vector: callers must decide to
    refuse honestly, not to search with noise.
    """


# --------------------------------------------------------------------------- #
# Local provider (sentence-transformers)
# --------------------------------------------------------------------------- #
_model = None


def get_model():
    """Return the process-wide local embedding model, loading it on first use."""
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer

        _model = SentenceTransformer(config.EMBED_MODEL)
    return _model


def _embed_local(texts: list[str]) -> list[list[float]]:
    # normalize_embeddings=True gives unit vectors, so cosine similarity is a
    # clean dot product and scores are stable/comparable across queries.
    return get_model().encode(
        texts, normalize_embeddings=True, convert_to_numpy=True
    ).tolist()


# --------------------------------------------------------------------------- #
# API provider (OpenAI-compatible /embeddings)
# --------------------------------------------------------------------------- #
# Inner error codes worth another attempt. Applied to BOTH the HTTP status and
# the tunnelled code inside a 200-with-error body.
_TRANSIENT = {408, 409, 425, 429, 500, 502, 503, 504, 520, 522, 524}


def _api_url() -> str:
    return config.OPENAI_BASE_URL.rstrip("/") + "/embeddings"


def _validate(vectors: list[list[float]], n_expected: int) -> None:
    """Reject anything we would regret writing to the database."""
    if len(vectors) != n_expected:
        raise EmbeddingUnavailable(
            f"expected {n_expected} vectors, got {len(vectors)}"
        )
    for i, v in enumerate(vectors):
        if len(v) != config.EMBED_DIM:
            raise EmbeddingUnavailable(
                f"vector {i} has width {len(v)}, expected EMBED_DIM={config.EMBED_DIM} "
                f"(EMBED_MODEL={config.EMBED_MODEL!r} — do these agree?)"
            )
        if not all(math.isfinite(x) for x in v):
            raise EmbeddingUnavailable(f"vector {i} contains NaN/inf")
        if not any(v):
            raise EmbeddingUnavailable(f"vector {i} is all zeros")


def _post_batch(texts: list[str]) -> list[list[float]]:
    """One embeddings call, with retries. Order-safe and body-inspecting."""
    import httpx

    payload = {"model": config.EMBED_MODEL, "input": texts}
    headers = {
        "Authorization": f"Bearer {config.OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }
    last = "no attempt made"
    for attempt in range(config.EMBED_RETRIES):
        try:
            r = httpx.post(
                _api_url(), headers=headers, json=payload, timeout=config.EMBED_TIMEOUT
            )
        except Exception as e:  # noqa: BLE001 — transport flake; retry
            last = f"{type(e).__name__}: {str(e)[:160]}"
            _backoff(attempt, last)
            continue

        # --- non-200: retry only if transient -------------------------------
        if r.status_code != 200:
            last = f"HTTP {r.status_code}: {r.text[:160]}"
            if r.status_code in _TRANSIENT and attempt < config.EMBED_RETRIES - 1:
                _backoff(attempt, last)
                continue
            raise EmbeddingUnavailable(last)

        try:
            body = r.json()
        except Exception:  # noqa: BLE001
            last = f"HTTP 200 with non-JSON body: {r.text[:160]}"
            _backoff(attempt, last)
            continue

        # --- 200 WITH AN ERROR BODY (the OpenRouter case) -------------------
        if "data" not in body:
            err = body.get("error") or {}
            code = err.get("code")
            last = f"HTTP 200 error-body code={code}: {str(err.get('message'))[:160]}"
            if code in _TRANSIENT and attempt < config.EMBED_RETRIES - 1:
                _backoff(attempt, last)
                continue
            raise EmbeddingUnavailable(last)

        data = body["data"]
        # --- ORDER SAFETY: pair by `index`, never by position ---------------
        try:
            indices = sorted(row["index"] for row in data)
        except (KeyError, TypeError) as e:
            raise EmbeddingUnavailable(f"malformed data rows: {e}") from e
        if indices != list(range(len(texts))):
            raise EmbeddingUnavailable(
                f"index set {indices[:8]}... does not cover 0..{len(texts) - 1}"
            )
        vectors = [row["embedding"] for row in sorted(data, key=lambda x: x["index"])]
        _validate(vectors, len(texts))
        return vectors

    raise EmbeddingUnavailable(f"exhausted {config.EMBED_RETRIES} attempts: {last}")


def _backoff(attempt: int, reason: str) -> None:
    logger.info(json.dumps({"event": "embed_retry", "attempt": attempt + 1, "reason": reason[:200]}))
    time.sleep(config.EMBED_BACKOFF * (attempt + 1))


def _embed_api(texts: list[str]) -> list[list[float]]:
    if not config.OPENAI_API_KEY:
        raise EmbeddingUnavailable(
            "EMBED_PROVIDER=api but OPENAI_API_KEY is empty — the server cannot "
            "embed, and therefore cannot retrieve."
        )
    out: list[list[float]] = []
    for i in range(0, len(texts), config.EMBED_BATCH):
        batch = [t[: config.EMBED_MAX_CHARS] if t else " " for t in texts[i : i + config.EMBED_BATCH]]
        out.extend(_post_batch(batch))
    return out


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed a batch of documents. Batching is far cheaper than one call each,
    so ingestion should pass many texts at once.

    Raises EmbeddingUnavailable rather than returning a suspect vector.
    """
    if not texts:
        return []
    if config.EMBED_PROVIDER == "local":
        vectors = _embed_local(texts)
        _validate(vectors, len(texts))
        return vectors
    return _embed_api(texts)


def embed_query(text: str) -> list[float]:
    """Embed a single query string. Thin wrapper over embed_texts so query and
    document embedding provably share the same code path and model."""
    return embed_texts([text])[0]
