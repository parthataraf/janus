"""Each live-path failure kind must produce its own honest message.

Production logs showed two distinct failures — an incomplete payload for Garen
and a plain timeout — rendering the SAME copy: "Live stats are still warming up
— try again in a few seconds." That was wrong for both. It invited a retry that
could never succeed for the empty-payload case, and it asserted a cause (cold
start) we had not established for the timeout.

The mapping under test:
    incomplete/empty payload -> "OP.GG doesn't have current stats for <X> right now."
    timeout (champion seen)  -> "OP.GG is slow to respond right now ..."
    timeout (first touch)    -> "... still warming up ..."   (the ONLY warming copy)
    endpoint error           -> "Live stats are unavailable right now."
"""

from __future__ import annotations

import asyncio

import pytest

from core import opgg_live
from core.opgg_live import (OpggEndpointError, OpggIncomplete, OpggTimeout,
                            OpggUnavailable)


@pytest.fixture(autouse=True)
def _clean_seen():
    """The cold/slow split is process state; isolate it between tests."""
    before = set(opgg_live._SEEN_CHAMPIONS)
    opgg_live._SEEN_CHAMPIONS.clear()
    yield
    opgg_live._SEEN_CHAMPIONS.clear()
    opgg_live._SEEN_CHAMPIONS.update(before)


# --------------------------------------------------------------------------- #
# One message per failure kind
# --------------------------------------------------------------------------- #
def test_incomplete_names_the_champion_and_does_not_invite_retry():
    msg = OpggIncomplete("Garen", "the entire analysis payload").user_message
    assert msg == "OP.GG doesn't have current stats for Garen right now."
    assert "try again" not in msg.lower()
    assert "warming" not in msg.lower()


def test_timeout_on_a_known_champion_says_slow_and_invites_retry():
    opgg_live._SEEN_CHAMPIONS.add("GAREN")
    msg = OpggTimeout(champion="GAREN", first_touch=False).user_message
    assert msg == "OP.GG is slow to respond right now — try again in a moment."
    assert "warming" not in msg.lower()


def test_timeout_on_first_touch_is_the_only_warming_up_message():
    msg = OpggTimeout(champion="GAREN", first_touch=True).user_message
    assert "warming up" in msg
    assert "try again" in msg.lower()


def test_endpoint_error_says_unavailable():
    msg = OpggEndpointError("tool_error").user_message
    assert msg == "Live stats are unavailable right now."
    assert "warming" not in msg.lower()


def test_every_failure_kind_has_distinct_copy():
    """The bug was two kinds sharing one message; assert they cannot again."""
    msgs = [
        OpggIncomplete("Garen", "x").user_message,
        OpggTimeout(champion="GAREN", first_touch=False).user_message,
        OpggTimeout(champion="GAREN", first_touch=True).user_message,
        OpggEndpointError("tool_error").user_message,
        OpggUnavailable("generic").user_message,
    ]
    assert len(set(msgs)) == 4      # the generic base shares the endpoint wording


def test_all_kinds_remain_catchable_as_the_base():
    for exc in (OpggIncomplete("Garen", "x"), OpggTimeout(), OpggEndpointError("x")):
        assert isinstance(exc, OpggUnavailable)


def test_base_class_always_offers_a_message():
    """routes.py reads .user_message off whatever it catches."""
    assert OpggUnavailable("anything").user_message


# --------------------------------------------------------------------------- #
# Cold vs slow is decided by what this process has actually fetched
# --------------------------------------------------------------------------- #
def test_first_touch_flag_tracks_seen_champions():
    mgr = opgg_live._mgr
    assert mgr._timeout({"champion": "GAREN"}).first_touch is True
    opgg_live._SEEN_CHAMPIONS.add("GAREN")
    assert mgr._timeout({"champion": "GAREN"}).first_touch is False


def test_a_seen_champion_times_out_as_slow_not_warming():
    opgg_live._SEEN_CHAMPIONS.add("GAREN")
    assert "slow" in opgg_live._mgr._timeout({"champion": "GAREN"}).user_message


# --------------------------------------------------------------------------- #
# One automatic retry on timeout
# --------------------------------------------------------------------------- #
def _drive(mgr, coro):
    """Run one _call_healing coroutine to completion without a live loop."""
    return asyncio.run(coro)


def test_timeout_is_retried_once_and_can_succeed(monkeypatch):
    """The compute keeps running server-side, so the second call often lands."""
    calls = []

    async def flaky(name, args, timeout):
        calls.append(args)
        if len(calls) == 1:
            raise asyncio.TimeoutError()
        return "payload"

    mgr = opgg_live._Manager()
    mgr._session = object()
    monkeypatch.setattr(mgr, "_one_call", flaky)
    assert _drive(mgr, mgr._call_healing("t", {"champion": "GAREN"}, opgg_live.TIMEOUT_S)) == "payload"
    assert len(calls) == 2, "expected exactly one retry"


def test_two_timeouts_give_up_as_a_timeout(monkeypatch):
    calls = []

    async def always_slow(name, args, timeout):
        calls.append(args)
        raise asyncio.TimeoutError()

    mgr = opgg_live._Manager()
    mgr._session = object()
    monkeypatch.setattr(mgr, "_one_call", always_slow)
    with pytest.raises(OpggTimeout):
        _drive(mgr, mgr._call_healing("t", {"champion": "GAREN"}, opgg_live.TIMEOUT_S))
    assert len(calls) == 2, "must not retry more than once"


def test_retry_does_not_apply_to_endpoint_errors(monkeypatch):
    """Only timeouts get the second chance — a tool error would just repeat."""
    from mcp.types import ErrorData

    calls = []

    async def tool_error(name, args, timeout):
        calls.append(args)
        raise opgg_live.McpError(ErrorData(code=-32000, message="boom"))

    mgr = opgg_live._Manager()
    mgr._session = object()
    monkeypatch.setattr(mgr, "_one_call", tool_error)
    with pytest.raises(OpggEndpointError):
        _drive(mgr, mgr._call_healing("t", {"champion": "GAREN"}, opgg_live.TIMEOUT_S))
    assert len(calls) == 1, "tool errors must not be retried"


# --------------------------------------------------------------------------- #
# The route surfaces the exception's own copy
# --------------------------------------------------------------------------- #
def test_route_uses_the_exception_message_not_a_fixed_string():
    from app import routes
    for exc, expected in [
        (OpggIncomplete("Garen", "x"), "OP.GG doesn't have current stats for Garen right now."),
        (OpggTimeout(champion="GAREN", first_touch=False),
         "OP.GG is slow to respond right now — try again in a moment."),
        (OpggEndpointError("tool_error"), "Live stats are unavailable right now."),
    ]:
        assert getattr(exc, "user_message", routes.LIVE_UNAVAILABLE) == expected


def test_route_fallback_message_is_not_the_warming_copy():
    """If some new failure has no message, the default must not assert a cause."""
    from app import routes
    assert "warming" not in routes.LIVE_UNAVAILABLE.lower()
