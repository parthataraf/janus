"""Cost controls on the public /ask path.

Two unbounded things sat behind a paid key on a public endpoint: the question a
caller may send, and the answer the model may generate. The rate limiter caps how
OFTEN money is spent; nothing capped how MUCH a single call could spend. These
tests pin both ceilings, and pin the warning that fires when the output ceiling
is actually reached.

No network: the OpenAI client is replaced with a recorder that captures the
kwargs the call was made with.
"""

from __future__ import annotations

import json

import pytest

from core import config, generation


class _Msg:
    def __init__(self, content):
        self.content = content


class _Choice:
    """Stands in for both shapes the SDK returns: a completed choice (.message)
    and a stream event (.delta)."""

    def __init__(self, content="an answer", finish_reason="stop"):
        self.message = _Msg(content)
        self.delta = _Msg(content)
        self.finish_reason = finish_reason


class _Resp:
    def __init__(self, choices):
        self.choices = choices


class _RecordingClient:
    """Captures create() kwargs; returns a canned completion or stream."""

    def __init__(self, finish_reason="stop", stream_events=None):
        self.calls: list[dict] = []
        self._finish_reason = finish_reason
        self._stream_events = stream_events

        outer = self

        class _Completions:
            def create(self, **kwargs):
                outer.calls.append(kwargs)
                if kwargs.get("stream"):
                    return iter(
                        outer._stream_events
                        if outer._stream_events is not None
                        else [_Resp([_Choice("hi", outer._finish_reason)])]
                    )
                return _Resp([_Choice("an answer", outer._finish_reason)])

        class _Chat:
            completions = _Completions()

        self.chat = _Chat()


@pytest.fixture
def client(monkeypatch):
    recorder = _RecordingClient()
    monkeypatch.setattr(generation, "_get_client", lambda: recorder)
    return recorder


# A chunk good enough to clear the refusal gate, so generation actually runs.
CHUNKS = [{"content": "Zed's ultimate has a 120 second cooldown at rank 1.",
           "heading_path": "Zed", "source_url": "http://x", "rerank_score": 5.0}]


# --------------------------------------------------------------------------- #
# Output ceiling
# --------------------------------------------------------------------------- #
def test_generate_sends_max_tokens(client):
    generation.generate("how long is zed's ult cooldown?", CHUNKS)
    assert client.calls[0]["max_tokens"] == config.GEN_MAX_TOKENS


def test_generate_stream_sends_max_tokens(client):
    list(generation.generate_stream("how long is zed's ult cooldown?", CHUNKS))
    assert client.calls[0]["max_tokens"] == config.GEN_MAX_TOKENS


def test_max_tokens_leaves_room_for_the_longest_eval_answer():
    """Guards the enumeration result the ceiling could silently regress.

    Measured against the deployed demo, real answers run 52-216 tokens, the
    longest being a live-stats build query. This floor sits well above that on
    purpose: a ceiling near typical output would truncate the long item lists
    eval/results.md reports 100% answer-recall on.
    """
    assert config.GEN_MAX_TOKENS >= 512


# --------------------------------------------------------------------------- #
# Truncation is reported, never silent
# --------------------------------------------------------------------------- #
def test_truncated_answer_is_logged(monkeypatch, caplog):
    monkeypatch.setattr(generation, "_get_client",
                        lambda: _RecordingClient(finish_reason="length"))
    with caplog.at_level("WARNING", logger="janus"):
        generation.generate("q", CHUNKS)
    events = [json.loads(r.message)["event"] for r in caplog.records]
    assert "generation_truncated" in events


def test_truncated_stream_is_logged(monkeypatch, caplog):
    monkeypatch.setattr(generation, "_get_client",
                        lambda: _RecordingClient(finish_reason="length"))
    with caplog.at_level("WARNING", logger="janus"):
        list(generation.generate_stream("q", CHUNKS))
    events = [json.loads(r.message)["event"] for r in caplog.records]
    assert "generation_truncated" in events


def test_normal_answer_logs_no_truncation(monkeypatch, caplog):
    monkeypatch.setattr(generation, "_get_client",
                        lambda: _RecordingClient(finish_reason="stop"))
    with caplog.at_level("WARNING", logger="janus"):
        generation.generate("q", CHUNKS)
    assert not [r for r in caplog.records if "generation_truncated" in r.message]


# --------------------------------------------------------------------------- #
# A refusal must still cost nothing
# --------------------------------------------------------------------------- #
def test_refusal_makes_no_call_at_all(client):
    """The cap must not have introduced a call on the refusal path."""
    generation.generate("off-corpus question", [])
    assert client.calls == []
