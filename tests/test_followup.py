"""Conversational carryover: resolve a referential follow-up, and nothing else.

Reported from live use: after "who is jax good against in top lane" answered,
both "what about some more champs he's good against" and "you told me yone but
can't tell me more" refused — no champion in the question, so nothing linked, so
the live path never fired and corpus retrieval refused.

The risk in fixing it is the opposite failure: carrying an entity into a question
that has moved on. These tests pin the gate from both sides.
"""

from __future__ import annotations

import time

import pytest

from core import followup
from core.followup import FRAME_TTL_S, is_referential, resolve
from core.lol_routing import live_stats_intent

JAX = ("Jax", "Jax")
YONE = ("Yone", "Yone")
NOW = 1_000_000.0


def _frame(**over):
    """The frame minted by "who is jax good against in top lane"."""
    f = {"champion": ["Jax", "Jax"], "opponent": None, "lane": "top",
         "kind": "counters", "direction": "strong", "role_query": False,
         "mentioned": ["Yone"], "corpus": "lol", "patch": "16.15.1", "ts": NOW}
    f.update(over)
    return f


def _ents(champions=()):
    return {"champions": list(champions), "items": []}


_DEFAULT = object()   # sentinel: frame=None must mean "no frame", not "default"


def _resolve(query, ents=None, frame=_DEFAULT, now=NOW):
    return resolve(query, ents or _ents(), _frame() if frame is _DEFAULT else frame,
                   live_stats_intent, "lol", "16.15.1", now=now)


# --------------------------------------------------------------------------- #
# The two reported failures
# --------------------------------------------------------------------------- #
def test_pronoun_followup_resolves_to_the_previous_champion():
    intent = _resolve("what about some more champs he's good against")
    assert intent is not None
    assert intent["a"] == JAX
    assert intent["kind"] == "counters"
    assert intent["direction"] == "strong"     # "good against" -> strong side
    assert intent["position"] == "top"         # lane inherited from the frame
    assert intent["followup"] is True


def test_back_reference_to_a_named_result_is_not_a_new_topic():
    """"you told me yone..." names Yone, but Yone came FROM the previous answer,
    so it is a reference back to it, not a question about Yone."""
    intent = _resolve("you told me yone but can't tell me more", _ents([YONE]))
    assert intent is not None
    assert intent["a"] == JAX                  # still about Jax
    assert intent["kind"] == "counters" and intent["direction"] == "strong"


# --------------------------------------------------------------------------- #
# False-positive protection — the part that must not regress
# --------------------------------------------------------------------------- #
def test_entity_less_but_non_referential_question_does_not_carry_over():
    """The negative case: no entity is NOT enough to trigger carryover."""
    assert _resolve("how do I climb in ranked") is None


@pytest.mark.parametrize("query", [
    "how do I climb in ranked",
    "what is the best way to farm minions",
    "how does ranked matchmaking work",
    "when does the season end",
    "how do I bake chocolate chip cookies",
    "what is the best topping for pizza",
])
def test_non_referential_questions_never_carry_over(query):
    assert _resolve(query) is None


def test_a_genuinely_new_champion_wins_over_the_frame():
    """Naming someone the previous answer never mentioned is a new subject."""
    assert _resolve("what about Darius", _ents([("Darius", "Darius")])) is None


def test_referential_marker_alone_is_not_enough_without_a_frame():
    assert _resolve("what about him", frame=None) is None


def test_stale_frames_expire():
    assert _resolve("what about him", now=NOW + FRAME_TTL_S + 1) is None
    assert _resolve("what about him", now=NOW + FRAME_TTL_S - 1) is not None


def test_frame_from_another_corpus_is_ignored():
    assert _resolve("what about him", frame=_frame(corpus="palworld")) is None


def test_frame_from_another_patch_is_ignored():
    assert _resolve("what about him", frame=_frame(patch="16.14.1")) is None


@pytest.mark.parametrize("bad", [None, {}, {"champion": None}, {"champion": ["Jax", "Jax"]},
                                 "not-a-dict", 42])
def test_malformed_frames_are_ignored(bad):
    assert _resolve("what about him", frame=bad) is None


def test_frame_with_an_unknown_kind_is_ignored():
    assert _resolve("what about him", frame=_frame(kind="something_else")) is None


# --------------------------------------------------------------------------- #
# Referential detection
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("query", [
    "what about some more champs he's good against",
    "who else is he strong against",
    "and what about his build",
    "tell me more",
    "any others",
    "what about that champion",
    "same for support",
])
def test_referential_phrasings_are_recognised(query):
    assert is_referential(query)


@pytest.mark.parametrize("query", [
    "how do I climb in ranked",
    "which items give armor penetration",
    "what does Yasuo's passive do",
    "what is the cooldown of Zed's ultimate at rank 1",
    "how much gold is Infinity Edge",
])
def test_non_referential_phrasings_are_not(query):
    assert not is_referential(query)


def test_markers_match_on_word_boundaries_only():
    """" it " must not fire inside "items"; "other" not inside "mother"."""
    assert not is_referential("which items give armor penetration")
    assert not is_referential("what is critical strike")


# --------------------------------------------------------------------------- #
# Kind reuse and lane inheritance
# --------------------------------------------------------------------------- #
def test_bare_continuation_reuses_the_previous_kind():
    intent = _resolve("tell me more", frame=_frame(kind="build", direction=None))
    assert intent["kind"] == "build" and intent["a"] == JAX


def test_explicit_lane_in_the_followup_beats_the_inherited_one():
    intent = _resolve("who else is he good against in mid")
    assert intent["position"] == "mid"


def test_matchup_carryover_needs_the_opponent():
    assert _resolve("tell me more", frame=_frame(kind="matchup", opponent=None)) is None
    intent = _resolve("tell me more",
                      frame=_frame(kind="matchup", opponent=["Darius", "Darius"]))
    assert intent["kind"] == "matchup" and intent["b"] == ("Darius", "Darius")


# --------------------------------------------------------------------------- #
# mint(): only answered turns produce a frame
# --------------------------------------------------------------------------- #
def test_mint_records_what_the_answer_named():
    intent = {"kind": "counters", "a": JAX, "position": "top", "direction": "strong"}
    f = followup.mint(intent, {}, ["Yone"], "lol", "16.15.1", now=NOW)
    assert f["champion"] == ["Jax", "Jax"] and f["mentioned"] == ["Yone"]
    assert f["lane"] == "top" and f["kind"] == "counters" and f["ts"] == NOW
    assert followup.frame_is_usable(f, "lol", "16.15.1", now=NOW)


def test_a_minted_frame_round_trips_into_a_resolution():
    intent = {"kind": "counters", "a": JAX, "position": "top", "direction": "strong"}
    f = followup.mint(intent, {}, ["Yone"], "lol", "16.15.1", now=NOW)
    assert _resolve("what about more champs he's good against", frame=f)["a"] == JAX


# --------------------------------------------------------------------------- #
# "who else" walks down the list instead of restating it
#
# The follow-up resolved to an intent IDENTICAL to the previous turn's, so it
# re-fetched and re-printed the same names. That was invisible while the source
# only held three rows; with the full matchup table behind it, there are more to
# give, and the card needs to know which ones are spent.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("q,direction", [
    ("who else counters him", "weak"),
    ("any more champs he's good against", "strong"),
    ("what else", "weak"),
    ("tell me the others", "weak"),
    ("go on", "weak"),
])
def test_a_more_request_carries_the_already_named_champions(q, direction):
    intent = _resolve(q, frame=_frame(direction=direction,
                                      mentioned=["Yone", "Darius"]))
    assert intent is not None, q
    assert intent["exclude"] == ["Yone", "Darius"], q


def test_a_plain_back_reference_does_not_skip_anything():
    """"is he good?" is referential but is not asking for MORE of the list."""
    intent = _resolve("is he any good", frame=_frame(kind="champion_stats",
                                                     direction=None))
    assert intent is not None
    assert "exclude" not in intent


def test_flipping_direction_starts_the_new_list_from_the_top():
    """"who is he good against instead" is a different list; its best rows should
    show even if a name repeats from the counters answer."""
    intent = _resolve("who is he good against instead",
                      frame=_frame(direction="weak", mentioned=["Darius"]))
    assert intent is not None
    assert intent["direction"] == "strong"
    assert "exclude" not in intent


def test_a_new_lane_starts_from_the_top():
    intent = _resolve("who else counters him in mid",
                      frame=_frame(direction="weak", mentioned=["Darius"]))
    assert intent is not None
    assert intent["position"] == "mid"
    assert "exclude" not in intent


def test_only_counter_lists_are_walked():
    """There is no second page of a build or a win rate."""
    intent = _resolve("what else does he build", frame=_frame(kind="build",
                                                              direction=None))
    assert intent is not None
    assert "exclude" not in intent


def test_the_skip_list_accumulates_across_turns():
    """Turn 3 must not circle back to turn 1's champions: the card only names
    what it just printed, so mint has to union in what was already skipped."""
    intent = {"a": JAX, "kind": "counters", "direction": "weak", "position": "top",
              "exclude": ["Darius", "Aatrox"]}
    frame = followup.mint(intent, {"lane": "top"}, ["Jayce", "Mordekaiser"],
                          "lol", "16.15.1", now=NOW)
    assert frame["mentioned"] == ["Darius", "Aatrox", "Jayce", "Mordekaiser"]


def test_minting_without_a_skip_list_is_unchanged():
    frame = followup.mint({"a": JAX, "kind": "counters", "direction": "weak"},
                          {"lane": "top"}, ["Yone"], "lol", "16.15.1", now=NOW)
    assert frame["mentioned"] == ["Yone"]


def test_the_skip_list_does_not_duplicate_a_repeated_name():
    frame = followup.mint({"a": JAX, "kind": "counters", "exclude": ["Yone"]},
                          {"lane": "top"}, ["Yone", "Darius"], "lol", "16.15.1", now=NOW)
    assert frame["mentioned"] == ["Yone", "Darius"]
