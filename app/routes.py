"""HTTP routes for the Janus API.

  POST /ask     -> SSE: `sources` event first, then `token` events, then `done`
                  (or `sources` then `refusal` when the context is too weak).
  GET  /corpora -> available corpora + ingested versions (UI dropdowns).
  GET  /health  -> DB reachable + models loaded + generation endpoint reachable.

This is a serving layer only: it drives the existing core/ pipeline and never
alters it. Retrieval flows through `hybrid_search` + `rerank` with their
promoted defaults (including release-notes exclusion) — the two stages are
called separately here purely so the log can report per-stage latency; together
they are exactly `retrieval.retrieve()`.
"""

from __future__ import annotations

import json
import logging
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from threading import Lock

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from app.schemas import AskRequest
from core import config, followup, generation, retrieval, store
from core.embeddings import EmbeddingUnavailable

router = APIRouter()
logger = logging.getLogger("janus")

# Chunks sent to generation / shown as sources. Matches retrieve()'s top_n=5.
TOP_N = 5
# Longest content shown in a `sources` preview (the panel links out for more).
PREVIEW_CHARS = 280


# --------------------------------------------------------------------------- #
# Rate limit: in-memory sliding window per client IP
# --------------------------------------------------------------------------- #
_RL_WINDOW_S = 60.0
_rl_hits: dict[str, deque] = defaultdict(deque)
_rl_lock = Lock()


def rate_limit(request: Request) -> None:
    """Per-IP sliding-window limiter for the expensive /ask endpoint. Raises 429
    with a Retry-After header once RATE_LIMIT_PER_MIN is exceeded in the window."""
    ip = request.client.host if request.client else "unknown"
    now = time.time()
    with _rl_lock:
        dq = _rl_hits[ip]
        cutoff = now - _RL_WINDOW_S
        while dq and dq[0] < cutoff:
            dq.popleft()
        if len(dq) >= config.RATE_LIMIT_PER_MIN:
            retry_after = int(_RL_WINDOW_S - (now - dq[0])) + 1
            raise HTTPException(
                status_code=429,
                detail=(
                    f"Rate limit exceeded ({config.RATE_LIMIT_PER_MIN}/min). "
                    f"Retry in ~{retry_after}s."
                ),
                headers={"Retry-After": str(retry_after)},
            )
        dq.append(now)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _round(x, n=4):
    return round(x, n) if x is not None else None


def _log_request(*, question, corpus, doc_version, ip, retrieve_ms, rerank_ms,
                 gen_ms, refused, top_score, n_sources, mcp_ms=0.0, live=None,
                 embed_ms=0.0) -> None:
    logger.info(json.dumps({
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": "ask",
        "ip": ip,
        "corpus": corpus,
        "doc_version": doc_version,
        "question": question,
        "live": live,                       # None | "ok" | "degraded"
        "refused": refused,
        "top_rerank_score": _round(top_score),
        "n_sources": n_sources,
        "latency_ms": {
            "embed": round(embed_ms, 1),    # query-embedding API leg
            "retrieve": round(retrieve_ms, 1),
            "rerank": round(rerank_ms, 1),
            "mcp": round(mcp_ms, 1),        # OP.GG live leg — reported honestly
            "generation": round(gen_ms, 1),
            "total": round(embed_ms + retrieve_ms + rerank_ms + mcp_ms + gen_ms, 1),
        },
    }, ensure_ascii=False))


# Last-resort copy for a live failure with no specific message of its own. The
# real wording comes from the exception (opgg_live.OpggUnavailable.user_message),
# because one message for every failure was actively misleading: an empty payload
# and a timeout both used to say "still warming up — try again", which sent users
# into a retry loop for a condition retrying cannot fix.
LIVE_UNAVAILABLE = "Live stats are unavailable right now."

# Embeddings are served by an external API (EMBED_PROVIDER=api), so an outage
# there means we cannot search at all. Say so plainly rather than answering from
# the keyword leg alone and passing off a degraded result as a normal one.
RETRIEVAL_UNAVAILABLE = (
    "Search is temporarily unavailable — the embedding service could not be "
    "reached, so I can't look anything up right now. Please try again shortly."
)


def _fetch_live_card(intent: dict) -> dict:
    """Dispatch a live-stats intent to the OP.GG MCP and format its context card.
    Raises opgg_live.OpggUnavailable on any failure (caller degrades)."""
    from core import opgg_live
    cid, name = intent["a"]
    kind = intent["kind"]
    pos = intent.get("position")  # explicit lane override, or None -> most-played
    if kind == "counters":
        # `exclude` is set only by a "who else / any more" follow-up, so the next
        # rows of the same list are shown instead of the same ones again.
        return opgg_live.format_counters_card(
            opgg_live.analyze(cid, name, "counters", position=pos),
            intent.get("direction", "weak"), exclude=intent.get("exclude"))
    if kind == "build":
        return opgg_live.format_build_card(
            opgg_live.analyze(cid, name, "build", position=pos))
    if kind == "champion_stats":
        a = opgg_live.analyze(cid, name, "stats", position=pos)
        if intent.get("role_query"):
            return opgg_live.format_role_card(a)
        return opgg_live.format_stats_card(a)
    # matchup
    a = opgg_live.analyze(cid, name, "both", position=pos)
    return opgg_live.format_matchup_card(a, intent["b"][1])


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #
@router.post("/ask")
def ask(req: AskRequest, request: Request, _rl: None = Depends(rate_limit)):
    """Sources-first SSE answer. Retrieval + rerank run before streaming so the
    `sources` event (and the refusal decision) are known up front."""
    question, corpus, doc_version = req.question, req.corpus, req.doc_version
    ip = request.client.host if request.client else "unknown"

    retrieve_ms = rerank_ms = mcp_ms = 0.0
    live_mode = None          # None | "ok" | "degraded"
    live_message = LIVE_UNAVAILABLE   # replaced when the failure has specific copy
    live_frame = None         # carryover frame; minted only if this turn answers
    live_meta: dict = {}
    chunks = []
    stats: dict = {}          # retrieval sub-timings (currently: embed_ms)
    embed_failed = False

    # --- LoL live-stats (OP.GG) meta-question path -------------------------- #
    # Win rates / counters / popularity are answered by a LIVE per-question MCP
    # call (never cached). Everything else routes exactly as before.
    if corpus == "lol":
        from core import lol_routing, opgg_live
        patches = store.lol_patches()
        patch = patches[-1] if patches else None
        intent = (lol_routing.detect_live_intent(question, patch, req.context, corpus)
                  if patch else None)
        if intent:
            try:
                card = _fetch_live_card(intent)
                mcp_ms = float(card.get("mcp_ms") or 0.0)
                chunks = [retrieval.Candidate(
                    id=-99, content=card["content"], source_url=card["source_url"],
                    heading_path=card["heading_path"],
                    rerank_score=retrieval.STRUCTURED_SCORE, sources={"live_stats"})]
                live_meta = {"patch": card.get("patch"), "fetched_at": card.get("fetched_at"),
                             "preview": card.get("preview")}
                live_mode = "ok"
                # Mint the carryover frame only on a turn that ANSWERED, and
                # record the champions this answer named so a later back-
                # reference to one of them isn't mistaken for a new subject.
                live_frame = followup.mint(
                    intent, card,
                    lol_routing.champions_in(card.get("preview") or "", patch),
                    corpus, patch)
            except opgg_live.OpggUnavailable as e:
                live_mode = "degraded"
                # Each failure kind carries its own copy: "no data for X" must not
                # read as "try again", and a timeout must not read as an outage.
                live_message = getattr(e, "user_message", LIVE_UNAVAILABLE)
                logger.info(json.dumps(
                    {"event": "opgg_degrade", "kind": type(e).__name__,
                     "reason": str(e)[:80], "q": question[:80]}))

    # --- normal routing (unchanged) when not a live-stats question ---------- #
    if live_mode is None:
        # A query we cannot embed cannot be searched. Degrade honestly instead of
        # silently serving keyword-only results dressed up as a full answer.
        try:
            if corpus == "lol":
                t0 = time.perf_counter()
                chunks = retrieval.route(
                    question, corpus, doc_version=doc_version, top_n=TOP_N, stats=stats
                )
                retrieve_ms = (time.perf_counter() - t0) * 1000.0
            else:
                t0 = time.perf_counter()
                candidates = retrieval.hybrid_search(
                    question, corpus, doc_version=doc_version, stats=stats
                )
                retrieve_ms = (time.perf_counter() - t0) * 1000.0
                t1 = time.perf_counter()
                chunks = retrieval.rerank(question, candidates, top_n=TOP_N)
                rerank_ms = (time.perf_counter() - t1) * 1000.0
        except EmbeddingUnavailable as e:
            embed_failed = True
            chunks = []
            logger.info(json.dumps(
                {"event": "embed_degrade", "reason": str(e)[:200], "q": question[:80]}))

    embed_ms = float(stats.get("embed_ms") or 0.0)
    # embed_ms is measured inside the retrieval stage, so subtract it out to keep
    # the readout's parts summing to the total instead of double-counting.
    retrieve_ms = max(0.0, retrieve_ms - embed_ms)

    top_score = generation._top_rerank_score(chunks)
    refused = embed_failed or (live_mode == "degraded") or generation._should_refuse(chunks)

    # Provenance shown on corpus cards, so a citation can name where the number
    # came from without linking to raw Data Dragon JSON. Resolved once: when no
    # version is pinned, LoL answers come from the newest ingested patch.
    doc_patch = doc_version
    if doc_patch is None and corpus == "lol":
        doc_patch = (store.lol_patches() or [None])[-1]

    sources = [
        {
            "index": i,
            "source_url": c.source_url,
            "heading_path": c.heading_path,
            "rerank_score": _round(c.rerank_score),
            "similarity": _round(c.vector_similarity),
            "preview": (live_meta.get("preview") if live_mode == "ok"
                        else (c.content or "")[:PREVIEW_CHARS]),
            "kind": "live_stats" if live_mode == "ok" else "doc",
            "patch": live_meta.get("patch") if live_mode == "ok" else doc_patch,
            "fetched_at": live_meta.get("fetched_at") if live_mode == "ok" else None,
        }
        for i, c in enumerate(chunks, start=1)
    ]

    def stream():
        gen_ms = 0.0
        yield _sse("sources", {
            "corpus": corpus,
            "doc_version": doc_version,
            "refused": refused,
            "live": live_mode,
            "top_score": _round(top_score),
            "sources": sources,
        })
        if embed_failed:
            yield _sse("refusal", {
                "message": RETRIEVAL_UNAVAILABLE, "top_score": None, "live": False,
            })
        elif live_mode == "degraded":
            yield _sse("refusal", {"message": live_message, "top_score": None, "live": True})
        elif refused:
            yield _sse("refusal", {
                "message": generation.REFUSAL_TEXT,
                "top_score": _round(top_score),
            })
        else:
            g0 = time.perf_counter()
            try:
                for token in generation.generate_stream(question, chunks):
                    yield _sse("token", {"text": token})
            finally:
                gen_ms = (time.perf_counter() - g0) * 1000.0
            yield _sse("done", {
                "embed_ms": round(embed_ms, 1),      # query-embedding API leg
                "retrieve_ms": round(retrieve_ms, 1),
                "rerank_ms": round(rerank_ms, 1),
                "mcp_ms": round(mcp_ms, 1),          # OP.GG live leg (honest)
                "generation_ms": round(gen_ms, 1),
                "total_ms": round(embed_ms + retrieve_ms + rerank_ms + mcp_ms + gen_ms, 1),
                # Echoed back on the next question so a referential follow-up
                # resolves. Only present when this turn actually answered.
                "context": live_frame,
            })

        _log_request(
            question=question, corpus=corpus, doc_version=doc_version, ip=ip,
            retrieve_ms=retrieve_ms, rerank_ms=rerank_ms, gen_ms=gen_ms,
            refused=refused, top_score=top_score, n_sources=len(chunks),
            mcp_ms=mcp_ms, live=live_mode, embed_ms=embed_ms,
        )

    return StreamingResponse(stream(), media_type="text/event-stream")


@router.get("/corpora")
def corpora():
    """Corpora + ingested versions from the DB (drives the UI switcher/dropdown)."""
    return {"corpora": store.list_corpora()}


@router.get("/health")
def health():
    """Liveness/readiness: DB reachable, models loaded, generation endpoint up."""
    from core import embeddings  # local import to read the singleton without loading

    checks: dict[str, bool] = {}
    try:
        checks["db"] = store.ping()
    except Exception:
        checks["db"] = False
    # What "the embedder is ready" means depends on the provider: a loaded local
    # singleton, or a reachable API. Under EMBED_PROVIDER=api the server cannot
    # retrieve at all without the embedding endpoint, so this check is a hard
    # readiness signal, not a nicety.
    if config.EMBED_PROVIDER == "local":
        checks["embeddings"] = embeddings._model is not None
    else:
        try:
            embeddings.embed_query("healthcheck")
            checks["embeddings"] = True
        except Exception:
            checks["embeddings"] = False
    checks["reranker_loaded"] = retrieval._reranker is not None
    try:
        resp = httpx.get(
            config.OPENAI_BASE_URL.rstrip("/") + "/models", timeout=3.0
        )
        checks["generation_endpoint"] = resp.status_code < 500
    except Exception:
        checks["generation_endpoint"] = False

    ok = all(checks.values())
    return JSONResponse(
        {"status": "ok" if ok else "degraded", "checks": checks},
        status_code=200 if ok else 503,
    )
