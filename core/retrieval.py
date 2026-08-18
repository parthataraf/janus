"""Retrieval pipeline: vector, keyword, hybrid (RRF), cross-encoder rerank.

Each stage is a small, independently testable function operating on a common
`Candidate`. The default `retrieve()` wires them into the spec pipeline:
hybrid top-20 -> rerank -> top-5. Every stage logs its output at DEBUG so a
bad answer can be traced to the stage that produced the bad context.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

from core import config, store
from core.embeddings import embed_query

logger = logging.getLogger(__name__)

# RRF constant. 60 is the value from the original RRF paper and the de-facto
# default; it damps the influence of any single ranker's top positions.
RRF_K = 60


@dataclass
class Candidate:
    """A retrieval candidate threaded through the pipeline. Scores accumulate as
    it passes each stage; unset stages stay None so logs show what ran."""

    id: int
    content: str
    source_url: str | None
    heading_path: str | None
    vector_similarity: float | None = None
    keyword_rank: float | None = None
    rrf_score: float | None = None
    rerank_score: float | None = None
    # Which rankers contributed (for debugging why something surfaced).
    sources: set[str] = field(default_factory=set)


def vector_search(
    query: str,
    corpus: str,
    k: int = 20,
    doc_version: str | None = None,
    stats: dict | None = None,
) -> list[Candidate]:
    """Dense retrieval: embed the query and cosine-search pgvector.

    With an API embedding provider the embed call is a network round-trip and
    dominates this stage, so its cost is recorded separately in `stats` and
    surfaced in the latency readout rather than being buried in `retrieve_ms`.

    Propagates EmbeddingUnavailable: a query we could not embed must refuse
    honestly, never fall back to a zero vector (which would "succeed" and return
    confident nonsense).
    """
    t0 = time.perf_counter()
    query_vector = embed_query(query)
    if stats is not None:
        stats["embed_ms"] = stats.get("embed_ms", 0.0) + (time.perf_counter() - t0) * 1000.0
    hits = store.search_chunks(query_vector, corpus, top_k=k, doc_version=doc_version)
    cands = [
        Candidate(
            id=h.id,
            content=h.content,
            source_url=h.source_url,
            heading_path=h.heading_path,
            vector_similarity=h.similarity,
            sources={"vector"},
        )
        for h in hits
    ]
    logger.debug("vector_search(%r) -> %d hits", query, len(cands))
    return cands


def keyword_search(
    query: str, corpus: str, k: int = 20, doc_version: str | None = None
) -> list[Candidate]:
    """Sparse retrieval: Postgres full-text ranking. Complements vectors on
    exact identifiers (`Depends`, `response_model`) that embed poorly."""
    hits = store.keyword_search_chunks(query, corpus, top_k=k, doc_version=doc_version)
    cands = [
        Candidate(
            id=h.id,
            content=h.content,
            source_url=h.source_url,
            heading_path=h.heading_path,
            keyword_rank=h.rank,
            sources={"keyword"},
        )
        for h in hits
    ]
    logger.debug("keyword_search(%r) -> %d hits", query, len(cands))
    return cands


def _rrf_merge(
    ranked_lists: list[list[Candidate]], k: int
) -> list[Candidate]:
    """Reciprocal Rank Fusion. For each list, a candidate at 1-based rank r
    contributes 1/(RRF_K + r); scores sum across lists. Fusing by RANK (not raw
    score) is the whole point — cosine similarity and ts_rank live on
    incomparable scales, so mixing their scores directly would be meaningless.
    """
    merged: dict[int, Candidate] = {}
    for ranked in ranked_lists:
        for rank, cand in enumerate(ranked, start=1):
            contribution = 1.0 / (RRF_K + rank)
            existing = merged.get(cand.id)
            if existing is None:
                cand.rrf_score = contribution
                merged[cand.id] = cand
            else:
                # Same chunk surfaced by another ranker: accumulate score and
                # fold in whichever per-ranker score/source this copy carries.
                existing.rrf_score = (existing.rrf_score or 0.0) + contribution
                existing.sources |= cand.sources
                if cand.vector_similarity is not None:
                    existing.vector_similarity = cand.vector_similarity
                if cand.keyword_rank is not None:
                    existing.keyword_rank = cand.keyword_rank

    fused = sorted(merged.values(), key=lambda c: c.rrf_score or 0.0, reverse=True)
    return fused[:k]


# Release-notes pages are changelog entries — API-name-dense but low in
# explanatory value. The retrieval eval showed that filtering them out of the
# candidate pool at query time lifts hit@1 by +7.8 pts and MRR by +0.040 with no
# metric degraded, so it is the default. Pass exclude_url_substr=None to search
# the raw corpus (e.g. for evaluation baselines). See eval/results.md.
DEFAULT_EXCLUDE_URL_SUBSTR = "/release-notes/"
# When excluding, oversample each leg so the fusion still sees a full pool of
# kept candidates after filtering (~31% of the fastapi corpus is release-notes).
EXCLUDE_OVERSAMPLE = 2


def hybrid_search(
    query: str,
    corpus: str,
    k: int = 20,
    doc_version: str | None = None,
    exclude_url_substr: str | None = DEFAULT_EXCLUDE_URL_SUBSTR,
    stats: dict | None = None,
) -> list[Candidate]:
    """Run vector + keyword search and fuse with RRF. Each leg pulls `k` so the
    fusion has a full pool from both before truncating back to `k`.

    When `exclude_url_substr` is set (the default), candidates whose source_url
    contains it are dropped from each leg BEFORE fusion — a query-time filter,
    not a corpus deletion — and the legs oversample so the kept pool stays full.
    """
    leg_k = k * EXCLUDE_OVERSAMPLE if exclude_url_substr else k
    vec = vector_search(query, corpus, k=leg_k, doc_version=doc_version, stats=stats)
    kw = keyword_search(query, corpus, k=leg_k, doc_version=doc_version)
    if exclude_url_substr:
        vec = [c for c in vec if exclude_url_substr not in (c.source_url or "")]
        kw = [c for c in kw if exclude_url_substr not in (c.source_url or "")]
    fused = _rrf_merge([vec, kw], k=k)
    logger.debug(
        "hybrid_search(%r): %d vector + %d keyword -> %d fused (exclude=%r)",
        query, len(vec), len(kw), len(fused), exclude_url_substr,
    )
    return fused


# --- Reranker: lazy singleton (hundreds of MB, load once) ---
_reranker = None


def get_reranker():
    """Return the process-wide cross-encoder, loading it on first use."""
    global _reranker
    if _reranker is None:
        # Imported lazily so modules that only do vector/keyword search (or the
        # chunker tests) never pay the CrossEncoder import/load cost.
        from sentence_transformers import CrossEncoder

        _reranker = CrossEncoder(config.RERANK_MODEL)
    return _reranker


def rerank(query: str, candidates: list[Candidate], top_n: int = 5) -> list[Candidate]:
    """Score each (query, chunk) pair with the cross-encoder and keep the best
    top_n. The cross-encoder reads query and chunk jointly, so it judges
    relevance far more precisely than the bi-encoder retrieval that produced
    the candidates — but it's expensive, hence only on the ~20 finalists."""
    if not candidates:
        return []
    pairs = [(query, c.content) for c in candidates]
    scores = get_reranker().predict(pairs)
    for cand, score in zip(candidates, scores):
        cand.rerank_score = float(score)
    reranked = sorted(candidates, key=lambda c: c.rerank_score or 0.0, reverse=True)
    logger.debug(
        "rerank(%r): %d candidates, top score %.4f",
        query, len(candidates), reranked[0].rerank_score if reranked else float("nan"),
    )
    return reranked[:top_n]


def retrieve(
    query: str,
    corpus: str,
    doc_version: str | None = None,
    *,
    hybrid_k: int = 20,
    top_n: int = 5,
    exclude_url_substr: str | None = DEFAULT_EXCLUDE_URL_SUBSTR,
    stats: dict | None = None,
) -> list[Candidate]:
    """Default pipeline: hybrid top-`hybrid_k` -> rerank -> top-`top_n`.

    Release-notes chunks are excluded at query time by default (eval-justified;
    see eval/results.md). Returns candidates carrying their rerank_score, which
    generation uses for the refusal gate (below-threshold top score => don't call
    the LLM).
    """
    candidates = hybrid_search(
        query, corpus, k=hybrid_k, doc_version=doc_version,
        exclude_url_substr=exclude_url_substr, stats=stats,
    )
    final = rerank(query, candidates, top_n=top_n)
    logger.debug("retrieve(%r) -> %d final chunks", query, len(final))
    return final


# Structured lookups are ground truth (exact Data Dragon numbers), so they sit
# above any reranked prose score — high enough to clear the refusal gate and lead
# the context the model cites.
STRUCTURED_SCORE = 15.0


def route(
    query: str,
    corpus: str,
    doc_version: str | None = None,
    *,
    top_n: int = 5,
    stats: dict | None = None,
) -> list[Candidate]:
    """Top-level retrieval entry. For every corpus except 'lol' this is exactly
    the default `retrieve()` (the fastapi path is unchanged). For 'lol' it links
    entities and, on a numeric question, prepends a structured Candidate (exact
    cooldown/cost/stat numbers) to the prose retrieval — so one generation call
    sees both the numbers and the mechanics text."""
    if corpus != "lol":
        return retrieve(query, corpus, doc_version=doc_version, top_n=top_n, stats=stats)

    from core import lol_routing, store

    patches = store.lol_patches()
    patch = doc_version if doc_version in patches else (patches[-1] if patches else None)
    prose = retrieve(query, corpus, doc_version=patch, top_n=top_n, stats=stats)
    if not patch:
        return prose

    result = lol_routing.analyze(query, patch)
    structured = [
        Candidate(
            id=-1 - i,
            content=p["content"],
            source_url=p["source_url"] or None,
            heading_path=p["heading_path"],
            rerank_score=STRUCTURED_SCORE,
            sources={"structured"},
        )
        for i, p in enumerate(result["passages"])
    ]
    if structured:
        logger.debug("route(%r) lol: %d structured + %d prose", query, len(structured), len(prose))
    return structured + prose
