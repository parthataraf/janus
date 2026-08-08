"""Resolve a referential follow-up against the previous answered turn.

`/ask` is stateless, so a follow-up that names no champion — "what about some
more champs he's good against" — linked no entity, missed the live-stats path,
fell through to corpus retrieval and refused. The conversation was there on
screen; the router just couldn't see it.

The frame carried between turns is a RESOLVED intent, not raw text: which
champion, which lane, which kind of question, plus the champions the previous
answer actually named. It is minted server-side, echoed to the client in the
`done` event, and handed back on the next request, so the API stays stateless.

Carryover is deliberately hard to trigger. Absence of an entity is NOT enough —
"how do I climb in ranked" names nobody and must still refuse. A follow-up has
to POSITIVELY look referential, and even then an explicit new champion wins.
The order of checks is the safety property: normal classification runs first and
its answer is final, so carryover only ever fires on a question that would
otherwise have gone unanswered. It can rescue a refusal; it can never override
a successful route.
"""

from __future__ import annotations

import time

# How long a frame stays usable. Long enough for a real exchange, short enough
# that a tab left open overnight doesn't resolve tomorrow's question against
# yesterday's champion.
FRAME_TTL_S = 600

# A follow-up must contain one of these to be treated as referential.
_PRONOUNS = (
    "he", "him", "his", "she", "her", "hers", "they", "them", "their", "theirs",
    "it", "its",
)
_DEMONSTRATIVES = (
    "that champion", "this champion", "that one", "this one", "the same",
    "same champion", "those", "these",
)
# Ellipsis: the question continues the previous one without restating it.
_ELLIPSIS = (
    "what about", "how about", "what else", "who else", "anyone else",
    "any more", "any others", "more", "others", "other", "else", "instead",
    "same for", "and what", "go on", "keep going", "continue",
)
_KINDS = ("counters", "build", "champion_stats", "matchup")

# A follow-up asking for MORE of the same list, as opposed to one that merely
# points back ("is he good?"). Only these should skip the rows already given —
# see `_wants_more` and the `exclude` note in resolve().
_MORE = (
    "what else", "who else", "anyone else", "any more", "any others",
    "more", "others", "other", "else", "go on", "keep going", "continue",
)


def _padded(query: str) -> str:
    """Lowercased and space-padded so markers match on word boundaries: " it "
    must not fire inside "items" or "critical"."""
    cleaned = "".join(c if c.isalnum() else " " for c in query.lower())
    return f" {' '.join(cleaned.split())} "


def is_referential(query: str) -> bool:
    """Does this question point back at something already said?"""
    q = _padded(query)
    return (any(f" {w} " in q for w in _PRONOUNS)
            or any(f" {w} " in q for w in _DEMONSTRATIVES)
            or any(f" {w} " in q for w in _ELLIPSIS))


def frame_is_usable(frame, corpus: str, patch: str, now: float | None = None) -> bool:
    """A frame is only good for the same corpus and patch, and only briefly.

    Refused and degraded turns never mint one, so anything present here came
    from a turn that actually answered.
    """
    if not isinstance(frame, dict) or not frame.get("champion"):
        return False
    if frame.get("corpus") != corpus or frame.get("patch") != patch:
        return False
    if frame.get("kind") not in _KINDS:
        return False
    ts = frame.get("ts")
    if not isinstance(ts, (int, float)):
        return False
    return 0 <= (now if now is not None else time.time()) - ts <= FRAME_TTL_S


def _reuse_previous_kind(frame, carried) -> dict | None:
    """No intent words at all ("tell me more") — repeat the previous question
    for the carried champion."""
    kind = frame.get("kind")
    intent = {"kind": kind, "a": carried, "position": frame.get("lane")}
    if kind == "counters":
        intent["direction"] = frame.get("direction") or "weak"
    elif kind == "champion_stats":
        intent["role_query"] = bool(frame.get("role_query"))
    elif kind == "matchup":
        opp = frame.get("opponent")
        if not opp:
            return None
        intent["b"] = tuple(opp)
    return intent


def resolve(query: str, ents: dict, frame, classify, corpus: str, patch: str,
            now: float | None = None) -> dict | None:
    """A live-stats intent recovered from `frame`, or None to route normally.

    `classify` is lol_routing.live_stats_intent, passed in so this module stays
    free of routing imports (and of the database) — the follow-up reuses the
    SAME intent vocabulary rather than growing a parallel one that could drift.
    """
    if not frame_is_usable(frame, corpus, patch, now):
        return None
    if not is_referential(query):
        return None

    carried = tuple(frame["champion"])
    # An explicit champion normally means a new topic — EXCEPT when it is one the
    # previous answer named. "you told me yone but can't tell me more" is a
    # back-reference to the answer, not a question about Yone.
    back_refs = {str(n).lower() for n in (frame.get("mentioned") or ())}
    back_refs.add(str(carried[1]).lower())
    for _cid, name in (ents.get("champions") or ()):
        if str(name).lower() not in back_refs:
            return None

    # Re-run the real classifier with the carried champion substituted in, so
    # "...he's good against" resolves exactly as "Jax good against" would.
    intent = classify(query, dict(ents, champions=[carried]))
    if intent is None:
        intent = _reuse_previous_kind(frame, carried)
    if intent is None:
        return None

    # Inherit the lane only when the follow-up doesn't name one of its own.
    if not intent.get("position"):
        intent["position"] = frame.get("lane")
    intent["followup"] = True

    # "who else counters yasuo" used to restate the same three names, because the
    # intent it resolves to is identical to the previous turn's. Hand the already-
    # named champions down so the card can skip them and show the next rows.
    #
    # Narrow on purpose. It fires only when the follow-up literally asks for more
    # AND nothing about the question has moved: same kind, same direction, same
    # lane. A flipped direction or a changed lane is a different list, and its
    # best rows should be shown from the top even if a name repeats.
    if (_wants_more(query)
            and intent.get("kind") == frame.get("kind") == "counters"
            and intent.get("direction") == (frame.get("direction") or "weak")
            and intent.get("position") == frame.get("lane")):
        intent["exclude"] = [str(n) for n in (frame.get("mentioned") or ())]
    return intent


def _wants_more(query: str) -> bool:
    q = _padded(query)
    return any(f" {w} " in q for w in _MORE)


def mint(intent: dict, card: dict, mentioned, corpus: str, patch: str,
         now: float | None = None) -> dict:
    """The frame to hand back to the client after a turn that ANSWERED."""
    a = intent.get("a") or (None, None)
    b = intent.get("b")
    # When this turn already skipped rows, `mentioned` has to ACCUMULATE — it
    # carries the names to skip next time, and this turn's card only names the
    # ones it just printed. Without the union, a third "who else" would circle
    # back to the first answer's champions.
    mentioned = list(dict.fromkeys(
        [str(n) for n in (intent.get("exclude") or ())]
        + [str(n) for n in (mentioned or ())]))
    return {
        "champion": [a[0], a[1]],
        "opponent": [b[0], b[1]] if b else None,
        "lane": intent.get("position") or (card or {}).get("lane"),
        "kind": intent.get("kind"),
        "direction": intent.get("direction"),
        "role_query": bool(intent.get("role_query")),
        "mentioned": list(mentioned or ()),
        "corpus": corpus,
        "patch": patch,
        "ts": now if now is not None else time.time(),
    }
