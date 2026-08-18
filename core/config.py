"""Central configuration. All settings are read from the environment (loaded
from `.env`) exactly once here, so the rest of the codebase never touches
`os.environ` directly and there is a single place to see every knob.
"""

from __future__ import annotations

import os

from dotenv import load_dotenv

# Load .env from the project root on import. Real environment variables take
# precedence over .env (override=False), which is what we want in Docker/CI.
load_dotenv(override=False)


def _require(name: str) -> str:
    """Fetch a mandatory var, failing loudly instead of surfacing as a confusing
    error deep inside a DB call or model load."""
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(
            f"Required environment variable {name!r} is not set. "
            "Copy .env.example to .env and fill it in."
        )
    return value


# --- Phase 1 ---
DATABASE_URL: str = _require("DATABASE_URL")
# Where embeddings come from: "api" = any OpenAI-compatible /embeddings endpoint
# (shares OPENAI_BASE_URL / OPENAI_API_KEY with generation); "local" =
# sentence-transformers in-process. Adopted 2026-07-27 after the corrected
# five-way bake-off (see eval/results.md); "local" remains a supported fallback
# and is what the test suite and offline development use.
EMBED_PROVIDER: str = os.environ.get("EMBED_PROVIDER", "api").strip().lower()
EMBED_MODEL: str = os.environ.get("EMBED_MODEL", "openai/text-embedding-3-small")
# EMBED_DIM defines the pgvector column width, so it must be an int and must
# match the embedding model. Kept as config (not hardcoded) so swapping models
# is a config change, per the spec's "model-agnostic design" goal.
EMBED_DIM: int = int(os.environ.get("EMBED_DIM", "1536"))
# --- API-provider tuning (ignored when EMBED_PROVIDER=local) ---
# Per-request timeout for one embeddings call.
EMBED_TIMEOUT: float = float(os.environ.get("EMBED_TIMEOUT", "60"))
# Attempts per batch before giving up (transient statuses AND transient codes
# tunnelled inside a 200-with-error body).
EMBED_RETRIES: int = int(os.environ.get("EMBED_RETRIES", "4"))
# Linear backoff base, seconds: sleep = EMBED_BACKOFF * attempt_number.
EMBED_BACKOFF: float = float(os.environ.get("EMBED_BACKOFF", "2.0"))
# Inputs per HTTP call. Ingestion may hand over thousands of chunks at once;
# the client sub-batches to keep requests inside provider payload limits.
EMBED_BATCH: int = int(os.environ.get("EMBED_BATCH", "64"))
# Hard per-input character cap. text-embedding-3-* accept 8191 tokens; ~24k
# chars stays clear of that with slack for token-dense text. Clipping one
# pathological chunk beats failing an entire ingest.
EMBED_MAX_CHARS: int = int(os.environ.get("EMBED_MAX_CHARS", "24000"))

# --- Phase 2+ (declared here so all config lives in one file; unused until
# the generation/retrieval phases import them). Not required at import time
# because Phase 1 must run without an OpenAI key. ---
OPENAI_API_KEY: str = os.environ.get("OPENAI_API_KEY", "")
GEN_MODEL: str = os.environ.get("GEN_MODEL", "gpt-4o-mini")
# Generation provider is any OpenAI-compatible endpoint: the OpenAI SDK takes a
# custom base_url, so pointing at Google's / Together's / a local server's
# OpenAI-compatible API is purely a config change. Defaults to OpenAI itself.
OPENAI_BASE_URL: str = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
# Per-request timeout (seconds) for the generation call. Local inference
# (Ollama, llama.cpp, ...) is far slower than a hosted API, so the default is
# generous. Applied to the OpenAI client so a stalled local server fails with a
# clean timeout instead of hanging the request forever.
GEN_TIMEOUT: float = float(os.environ.get("GEN_TIMEOUT", "60"))
# Ceiling on generated tokens per answer. OpenRouter injects no default when the
# parameter is omitted (it passes the omission upstream), so without this an
# answer is bounded only by the model's context window, on a public endpoint
# backed by a paid key.
#
# Set well above measured need rather than close to it. Real answers from the
# deployed demo run 52-216 tokens, the longest being a live-stats build query, so
# this is roughly 19x the observed worst case. The asymmetry justifies the
# generosity: a ceiling caps output rather than reserving it, so unused headroom
# costs nothing, while one set too near typical length silently truncates long
# item lists and regresses the exhaustive-enumeration result in eval/results.md
# (100% answer-recall on item sets). Hitting it logs generation_truncated.
GEN_MAX_TOKENS: int = int(os.environ.get("GEN_MAX_TOKENS", "4096"))
RERANK_MODEL: str = os.environ.get(
    "RERANK_MODEL", "cross-encoder/ms-marco-MiniLM-L-6-v2"
)
# Refusal gate: if the best cross-encoder rerank score is below this, generation
# short-circuits to "the docs don't cover this" without calling the LLM. The
# ms-marco cross-encoder emits logits (~negative = irrelevant, ~positive =
# relevant), so 0.0 is a sensible neutral cut. Tune from eval data later.
RERANK_THRESHOLD: float = float(os.environ.get("RERANK_THRESHOLD", "0.0"))

# --- Phase 3 (API serving layer) ---
# CORS allowed origins, comma-separated. Default is the Vite dev server so the
# Phase 3b frontend works out of the box; set to the deployed origin in prod.
CORS_ORIGINS: list[str] = [
    o.strip()
    for o in os.environ.get("CORS_ORIGINS", "http://localhost:5173").split(",")
    if o.strip()
]
# Per-IP rate limit for /ask (requests per rolling minute). It's a public demo
# with a paid generation key behind it, so cap it; tune per deployment.
RATE_LIMIT_PER_MIN: int = int(os.environ.get("RATE_LIMIT_PER_MIN", "30"))
# Longest accepted /ask question, in characters. The rate limit caps how OFTEN a
# caller can spend money; this caps how MUCH any single call can spend, which the
# rate limit cannot: the question is interpolated into the generation prompt
# verbatim, so an unbounded field means an unbounded prompt bill. (EMBED_MAX_CHARS
# clips the embedding input only, never the prompt.) Real questions are a line or
# two; 500 is roomy for anything a human actually types.
MAX_QUESTION_CHARS: int = int(os.environ.get("MAX_QUESTION_CHARS", "500"))
# Corpora hidden from /corpora so they never reach the UI switcher. The product
# is the League of Legends face; the FastAPI corpus is retained in-repo as
# engineering history but not surfaced (product pivot, 2026-07-21). smoke_test.py
# also reseeds a 'smoke' corpus on every run, so filtering beats deleting rows
# that just come back. Comma-separated; env override still works for anyone
# resurrecting the fastapi face locally. Set empty to show everything.
EXCLUDED_CORPORA: list[str] = [
    c.strip()
    for c in os.environ.get("EXCLUDED_CORPORA", "smoke,fastapi").split(",")
    if c.strip()
]
