"""The /ask request body must bound the question length.

`question` goes into the generation prompt verbatim, so the field is a cost
control as much as a validation rule. EMBED_MAX_CHARS does not help here: it
clips what gets embedded, never what gets sent to the model.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.schemas import AskRequest
from core import config


def _body(question: str) -> dict:
    return {"question": question, "corpus": "lol"}


def test_normal_question_is_accepted():
    req = AskRequest(**_body("What is the cooldown of Zed's ultimate at rank 1?"))
    assert req.corpus == "lol"


def test_question_at_the_limit_is_accepted():
    AskRequest(**_body("a" * config.MAX_QUESTION_CHARS))


def test_question_over_the_limit_is_rejected():
    with pytest.raises(ValidationError):
        AskRequest(**_body("a" * (config.MAX_QUESTION_CHARS + 1)))


def test_a_megabyte_question_is_rejected():
    """The actual abuse case: a huge body inflating the prompt bill."""
    with pytest.raises(ValidationError):
        AskRequest(**_body("a" * 1_000_000))


def test_empty_question_is_still_rejected():
    """min_length must survive the max_length change."""
    with pytest.raises(ValidationError):
        AskRequest(**_body(""))


def test_limit_is_roomy_enough_for_real_questions():
    """A cap tight enough to reject genuine questions would be a UX bug."""
    longest_real = (
        "Which items give armor penetration, and how much gold does each of "
        "them cost at the current patch, including the ones that only give it "
        "as a passive rather than a flat stat?"
    )
    assert len(longest_real) <= config.MAX_QUESTION_CHARS
