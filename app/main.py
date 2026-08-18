"""FastAPI app factory for the Janus serving layer (Phase 3a).

- CORS configured from `config.CORS_ORIGINS`.
- Heavy models (embedder + cross-encoder) are warmed ONCE at startup via the
  lifespan hook, so the first request isn't slow and every request reuses the
  process-wide singletons — never a per-request load.
- Request logging is emitted as JSON lines on stdout by the `janus` logger.

Run: `uvicorn app.main:app --host 0.0.0.0 --port 8000`
"""

from __future__ import annotations

import json
import logging
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routes import router
from core import config

# Dedicated JSON-lines logger to stdout, independent of uvicorn's own config.
logger = logging.getLogger("janus")
if not logger.handlers:
    _handler = logging.StreamHandler(sys.stdout)
    _handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(_handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False


@asynccontextmanager
async def lifespan(app: FastAPI):
    from core import store
    from core.embeddings import embed_query
    from core.retrieval import get_reranker

    # Ensure the schema exists so a fresh DB serves cleanly before ingestion
    # (init_schema is idempotent). Without this, /corpora 500s on an empty DB.
    logger.info('{"event": "startup", "msg": "ensuring schema"}')
    store.init_schema()

    # Warm the singletons so model loading happens once, at startup.
    logger.info('{"event": "startup", "msg": "warming models"}')
    # With EMBED_PROVIDER=api this is a live reachability probe, not a model
    # load. Never fatal: a transient embedding-API blip must not stop the server
    # from booting — /ask degrades honestly per request, and /health reports it.
    from core.embeddings import EmbeddingUnavailable
    try:
        embed_query("warmup")
    except EmbeddingUnavailable as e:
        logger.info(json.dumps(
            {"event": "startup", "msg": "embedding provider unreachable",
             "detail": str(e)[:200]}))
    get_reranker()          # loads the cross-encoder reranker (always local)
    logger.info('{"event": "startup", "msg": "models ready"}')

    # Warm the OP.GG live-stats MCP session (persistent, reused per request).
    # Never fatal: if it fails, the live path lazily reconnects and degrades.
    from core import opgg_live
    opgg_live.start()
    # Pre-warm OP.GG's server-side compute for common champions on a background
    # thread so demo/first queries land in the fast (~2.5s) regime. Non-blocking;
    # stores nothing. (Deploy startup runs this too — see the deploy runbook.)
    import threading
    threading.Thread(target=opgg_live.prewarm, name="opgg-prewarm", daemon=True).start()

    yield

    opgg_live.stop()


def create_app() -> FastAPI:
    app = FastAPI(title="Janus API", version="3a", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.CORS_ORIGINS,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router)
    return app


app = create_app()
