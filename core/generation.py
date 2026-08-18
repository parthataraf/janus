"""Prompt assembly, OpenAI call, citation extraction, and the refusal gate.

The grounding contract lives in the system prompt: answer ONLY from the
provided context, cite sources as [n], and say so plainly when the context
doesn't contain the answer. Refusal is a first-class feature — if retrieval's
best rerank score is below the configured threshold, we never call the LLM at
all, which both saves money and removes any chance of a confident hallucination
on an off-corpus question.

`chunks` are duck-typed: anything exposing .content / .source_url /
.heading_path / .rerank_score (i.e. retrieval.Candidate) or the equivalent
dict works, so this module doesn't import retrieval.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Iterator

from core import config

# Same JSON-lines logger the API and the other core modules write to.
logger = logging.getLogger("janus")

REFUSAL_TEXT = (
    "I couldn't find this in the available sources, so I won't guess."
)

_SYSTEM_PROMPT = """You are a precise, grounded question-answering assistant. Follow these rules exactly.

GROUNDING — absolute, and it outranks every other rule here:
- Answer ONLY from the numbered context passages. Never add a fact, a number, a
  judgement or a recommendation from your own knowledge, however well known it
  seems or however much it would improve the answer.
- In particular: NO strategic or play advice that is not written in a passage.
  Not "good against tanks", not "build this first", not "strong in the early
  game", not "pairs well with". Organising, grouping and explaining the
  retrieved data is your job. Supplying game wisdom is not, and a confident
  sentence you cannot cite is the worst thing you can produce.
- If the passages do not cover the question, say so plainly and stop.
- Cite every claim with bracketed numbers like [1] or [2], matching the passages.

COMPLETENESS — the usual failure is answering with far too little:
- Use everything in the passages that bears on the question. If a passage
  carries several figures and the question asks about one, lead with that one
  and then give the others that put it in context.
- Include exact numbers wherever the passages contain them — percentages, gold
  costs, cooldowns, ranges, sample sizes, patch numbers. A number that is in the
  context should not be missing from the answer.
- If the context provides a LIST or SET of items ("Items with X: A; B; C; ..."),
  name EVERY member. Do not summarise, sample, shorten, or write "and others".
- When the context is technical documentation containing a code example, include
  a short, runnable one drawn from it.

FORM:
- Write prose — a paragraph, or two or three when there is that much to say —
  not a single clipped sentence. Completeness beats brevity.
- Use structure when the DATA has structure: group related figures into one
  sentence, or use a short list for a set of items, matchups or build slots.
  Don't impose headings or bullets on a one-fact answer.
- Do not pad. Every sentence must carry something from the passages. When the
  context is exhausted, stop — an unnecessary closing sentence is padding, and
  padding is where ungrounded claims get in.

LIVE STATS — passages tagged [LIVE STATS] are live third-party data from OP.GG:
- BEGIN with the statistics themselves; any caveat comes after. Never open by
  saying the data doesn't cover the question.
- Report ALL the figures the passage carries, not only the one asked about: win
  rate, pick rate, ban rate, KDA, tier and rank, sample size, and the lane they
  apply to. A win-rate question deserves the whole picture the passage holds.
- Name the source and patch exactly as the passage gives them. Do not invent a
  timestamp, a sample size, or any figure the passage does not contain.
- Frame everything as statistical tendency ("statistically favourable", "a
  higher win rate", "currently a common pick"), never as instruction. Never tell
  the user to dodge, avoid, ban, or not play a champion or a game.
- If the statistics don't measure exactly what was asked (e.g. how
  beginner-friendly a champion is), still lead with the numbers, then say plainly
  what they do and do not measure."""

# Citation parsing is deliberately forgiving: smaller local models (llama3.1:8b
# and friends) format citations less reliably than gpt-4o. We accept a bracketed
# group that may hold several numbers separated by commas/dashes — e.g. [1],
# [1, 2], [1-3], and the full-width 【1】 some models emit — then pull the
# individual integers out of whatever we matched (so [1-3] yields 1 and 3, not
# an expanded 1,2,3). Anything we can't parse is simply ignored: an answer with
# no inline links is acceptable; a crash is not.
_CITATION_GROUP_RE = re.compile(r"[\[【]\s*([\d,\s\-–—]+?)\s*[\]】]")
_INT_RE = re.compile(r"\d+")


def _field(chunk: Any, name: str, default=None):
    """Read a field from a Candidate (attribute) or a dict."""
    if isinstance(chunk, dict):
        return chunk.get(name, default)
    return getattr(chunk, name, default)


def _top_rerank_score(chunks: list[Any]) -> float | None:
    """Best rerank score among chunks, or None if none were reranked."""
    scores = [_field(c, "rerank_score") for c in chunks]
    scores = [s for s in scores if s is not None]
    return max(scores) if scores else None


def _format_context(chunks: list[Any]) -> str:
    """Render chunks as a numbered list the model can cite by index."""
    blocks = []
    for i, c in enumerate(chunks, start=1):
        heading = _field(c, "heading_path") or "(untitled)"
        blocks.append(f"[{i}] {heading}\n{_field(c, 'content')}")
    return "\n\n".join(blocks)


def build_prompt(question: str, chunks: list[Any]) -> list[dict]:
    """Assemble the chat messages (system + user) for a grounded answer."""
    context = _format_context(chunks)
    user = (
        f"Context passages:\n\n{context}\n\n"
        f"Question: {question}\n\n"
        "Answer using only the passages above, with [n] citations. Use every "
        "figure above that bears on the question, and add nothing that isn't there."
    )
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


def _extract_citations(answer: str, chunks: list[Any]) -> list[dict]:
    """Map the [n] markers actually used in the answer back to their chunks.

    Tolerant of grouped/ranged citations and full-width brackets; silently drops
    anything out of range or unparseable so a sloppily-formatted answer still
    returns cleanly (possibly with zero citations) rather than raising.
    """
    citations: list[dict] = []
    seen: set[int] = set()
    if not answer:
        return citations
    for group in _CITATION_GROUP_RE.finditer(answer):
        for token in _INT_RE.findall(group.group(1)):
            try:
                idx = int(token)
            except ValueError:  # defensive; _INT_RE only matches digits
                continue
            if idx in seen or not (1 <= idx <= len(chunks)):
                continue
            seen.add(idx)
            chunk = chunks[idx - 1]
            citations.append(
                {
                    "index": idx,
                    "source_url": _field(chunk, "source_url"),
                    "heading_path": _field(chunk, "heading_path"),
                }
            )
    return citations


def _warn_truncated() -> None:
    """Record that an answer stopped because it hit GEN_MAX_TOKENS.

    Worth a log line rather than silence: a truncated answer loses its closing
    sentences and any citations they carried, which looks like a model or
    retrieval fault. This names the real cause, and a burst of these is the
    signal to raise the ceiling.
    """
    logger.warning(
        json.dumps({"event": "generation_truncated", "max_tokens": config.GEN_MAX_TOKENS})
    )


def _should_refuse(chunks: list[Any]) -> bool:
    """Refuse when there's nothing to ground on, or the best passage scores
    below the relevance threshold."""
    if not chunks:
        return True
    top = _top_rerank_score(chunks)
    # If nothing was reranked we can't gate on score; let the LLM (still bound
    # by the grounding prompt) handle it rather than refuse blindly.
    return top is not None and top < config.RERANK_THRESHOLD


# --- OpenAI client: lazy singleton ---
_client = None


def _get_client():
    global _client
    if _client is None:
        from openai import OpenAI

        # base_url makes the provider swappable (OpenAI, Google's OpenAI-compat
        # endpoint, a local Ollama server, ...) — see config.OPENAI_BASE_URL.
        # timeout keeps a slow/stalled local model from hanging forever.
        _client = OpenAI(
            api_key=config.OPENAI_API_KEY,
            base_url=config.OPENAI_BASE_URL,
            timeout=config.GEN_TIMEOUT,
        )
    return _client


def generate(question: str, chunks: list[Any]) -> dict:
    """Produce a grounded, cited answer. Returns
    {answer, citations, refused, top_score}. Short-circuits to a refusal
    (no LLM call) when the context is too weak."""
    top = _top_rerank_score(chunks)
    if _should_refuse(chunks):
        return {"answer": REFUSAL_TEXT, "citations": [], "refused": True, "top_score": top}

    resp = _get_client().chat.completions.create(
        model=config.GEN_MODEL,
        messages=build_prompt(question, chunks),
        temperature=0,  # deterministic, grounded answers over creative ones
        max_tokens=config.GEN_MAX_TOKENS,
    )
    choice = resp.choices[0]
    if getattr(choice, "finish_reason", None) == "length":
        _warn_truncated()
    answer = choice.message.content or ""
    return {
        "answer": answer,
        "citations": _extract_citations(answer, chunks),
        "refused": False,
        "top_score": top,
    }


def generate_stream(question: str, chunks: list[Any]) -> Iterator[str]:
    """Yield the answer token-by-token (for the Phase 3 SSE endpoint). On a
    refusal, yields the refusal text once and stops — still no LLM call."""
    if _should_refuse(chunks):
        yield REFUSAL_TEXT
        return

    stream = _get_client().chat.completions.create(
        model=config.GEN_MODEL,
        messages=build_prompt(question, chunks),
        temperature=0,
        max_tokens=config.GEN_MAX_TOKENS,
        stream=True,
    )
    for event in stream:
        choice = event.choices[0]
        delta = choice.delta.content
        if delta:
            yield delta
        # The SSE path is what the deployed demo uses, so this is where a ceiling
        # that is set too low would actually bite. Truncation is otherwise
        # invisible: the stream simply stops, and a half-finished answer reads as
        # a model quality problem rather than a configured limit.
        if getattr(choice, "finish_reason", None) == "length":
            _warn_truncated()
