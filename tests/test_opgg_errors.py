"""Live-path (OP.GG MCP) error handling.

Same bug class as the embedding client: a transport-level success that carries a
failure in its payload. The MCP equivalent of "HTTP 200 with an error body" is a
CallToolResult with `isError=True` — the call returns normally, so code that only
catches exceptions treats a tool error as data.

`opgg_live.call_tool` already inspects `isError`; these lock that in, plus the
adjacent "returned fine but there is nothing usable in it" cases.
"""

from __future__ import annotations

import pytest

from core import opgg_live
from core.opgg_live import OpggUnavailable


class FakeContent:
    def __init__(self, text=None):
        self.text = text


class FakeResult:
    def __init__(self, content=(), isError=False):
        self.content = list(content)
        self.isError = isError


class FakeFuture:
    def __init__(self, result):
        self._result = result

    def result(self, timeout=None):
        return self._result


def _patch_call(monkeypatch, result):
    """Make the manager's coroutine bridge return `result` without any network."""

    def fake_run_coro(coro, timeout=None):
        coro.close()          # we never await it; close it so pytest stays clean
        return FakeFuture(result)

    monkeypatch.setattr(opgg_live._mgr, "_loop", object())
    monkeypatch.setattr(opgg_live._mgr, "_run_coro", fake_run_coro)


def test_is_error_result_raises_rather_than_returning_payload(monkeypatch):
    """A tool error must NOT be read as data just because the call returned."""
    _patch_call(monkeypatch, FakeResult(content=[FakeContent("garbage")], isError=True))
    with pytest.raises(OpggUnavailable, match="tool_error"):
        opgg_live._mgr.call_tool("champion-analysis", {})


def test_empty_content_degrades(monkeypatch):
    _patch_call(monkeypatch, FakeResult(content=[], isError=False))
    with pytest.raises(OpggUnavailable, match="empty"):
        opgg_live._mgr.call_tool("champion-analysis", {})


def test_content_without_text_degrades(monkeypatch):
    _patch_call(monkeypatch, FakeResult(content=[FakeContent(None)], isError=False))
    with pytest.raises(OpggUnavailable, match="empty"):
        opgg_live._mgr.call_tool("champion-analysis", {})


def test_unparseable_text_degrades_not_crashes(monkeypatch):
    _patch_call(monkeypatch, FakeResult(content=[FakeContent("!!! not the repr format")]))
    with pytest.raises(OpggUnavailable, match="parse:"):
        opgg_live._mgr.call_tool("champion-analysis", {})


def test_wellformed_result_parses(monkeypatch):
    payload = 'class Summary: name\nSummary("MASTER_YI")'
    _patch_call(monkeypatch, FakeResult(content=[FakeContent(payload)]))
    out = opgg_live._mgr.call_tool("champion-analysis", {})
    assert out == {"name": "MASTER_YI"}
