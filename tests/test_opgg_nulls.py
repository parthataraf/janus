"""Partial OP.GG payloads must degrade, never render as fact.

Production incident: "Is Garen good for beginners?" answered with a live card
reading "win None% · pick None% · tier None (#None)", which the generator then
narrated as prose. Role also reported MID (Garen is TOP) because an empty
positions[] let the discovery SEED lane pass itself off as a finding.

Both symptoms had one cause — `data.summary` absent from an otherwise HTTP-OK
response — and one shape of bug: `.get()` chains collapsing to None and being
interpolated straight into card copy.

The distinction these tests protect: an EMPTY-BUT-HEALTHY result ("no counter
sample for this champion") is a real finding and still renders. A MISSING result
degrades. Confusing the two either invents data or hides real answers.
"""

from __future__ import annotations

import pytest

from core import opgg_live
from core.opgg_live import OpggIncomplete, OpggUnavailable


def _analysis(**over):
    """A complete, healthy normalized analysis — the baseline each test erodes."""
    base = {
        "display": "Garen", "champion_id": "Garen", "position": "top",
        "position_source": "discovered",
        "patch": "16.15", "data_updated": "2026-07-29T02:51:48+09:00",
        "fetched_at": "2026-07-31 06:00 UTC",
        "source_url": "https://op.gg/lol/champions/garen/build",
        "stats": {"win_rate": 51.0, "pick_rate": 6.0, "ban_rate": 4.0,
                  "tier": 3, "rank": 32, "kda": 1.83, "play": 14589},
        "roles": [{"lane": "top", "share": 89.0}, {"lane": "mid", "share": 9.0}],
        "weak_counters": [{"name": "Darius", "my_win_rate": 47.0,
                           "counter_win_rate": 53.0, "play": 900}],
        "strong_counters": [{"name": "Yasuo", "my_win_rate": 55.0,
                             "counter_win_rate": 45.0, "play": 800}],
        "build": {"core_items": {"names": ["Stridebreaker"], "pick_rate": 30.0,
                                 "play": 500, "win_rate": 52.0},
                  "boots": {"names": ["Plated Steelcaps"], "pick_rate": 40.0},
                  "starter_items": {"names": ["Doran's Blade"], "pick_rate": 60.0},
                  "runes": {"primary_page": "Precision", "primary": ["Conqueror"],
                            "secondary_page": "Resolve", "secondary": ["Second Wind"],
                            "pick_rate": 35.0},
                  "skill_order": ["Q", "E", "W"], "skill_pick_rate": 60.0},
        "mcp_ms": 1200.0,
    }
    base.update(over)
    return base


# The exact payload from the incident: response arrived, `data.summary` missing.
EMPTY_STATS = {"win_rate": None, "pick_rate": None, "ban_rate": None,
               "tier": None, "rank": None, "kda": None, "play": None}


def _stripped():
    """What _normalize produces from a payload with no `summary` at all."""
    return _analysis(stats=dict(EMPTY_STATS), roles=[], weak_counters=[],
                     strong_counters=[], build={}, position="mid",
                     position_source="assumed")


# --------------------------------------------------------------------------- #
# The incident itself
# --------------------------------------------------------------------------- #
def test_the_garen_incident_degrades_instead_of_rendering_nulls():
    with pytest.raises(OpggIncomplete) as exc:
        opgg_live.format_stats_card(_stripped())
    assert "Garen" in exc.value.user_message
    assert "None" not in exc.value.user_message


def test_incomplete_is_catchable_as_opgg_unavailable():
    """Callers catch OpggUnavailable; the subclass must not slip past them."""
    assert issubclass(OpggIncomplete, OpggUnavailable)
    with pytest.raises(OpggUnavailable):
        opgg_live.format_stats_card(_stripped())


@pytest.mark.parametrize("field", ["win_rate", "pick_rate", "tier", "rank"])
def test_any_single_missing_required_stat_degrades(field):
    a = _analysis()
    a["stats"][field] = None
    with pytest.raises(OpggIncomplete) as exc:
        opgg_live.format_stats_card(a)
    assert field in str(exc.value)


def test_no_card_path_can_emit_the_string_none():
    """Belt and braces: whatever a formatter returns, it never contains "None"."""
    for fmt in (opgg_live.format_stats_card,
                opgg_live.format_role_card,
                opgg_live.format_build_card,
                lambda a: opgg_live.format_counters_card(a, "weak"),
                lambda a: opgg_live.format_matchup_card(a, "Darius")):
        with pytest.raises(OpggUnavailable):
            fmt(_stripped())
        card = fmt(_analysis())
        assert "None" not in card["content"], fmt
        assert "None" not in card["preview"], fmt


# --------------------------------------------------------------------------- #
# Role: the seed lane must never be reported as the champion's lane
# --------------------------------------------------------------------------- #
def test_role_card_degrades_when_positions_is_empty():
    with pytest.raises(OpggIncomplete) as exc:
        opgg_live.format_role_card(_analysis(roles=[]))
    assert "positions" in str(exc.value)


def test_role_card_never_reports_the_seed_lane():
    """The old fallback printed a['position'] — i.e. MID for a TOP champion."""
    a = _analysis(roles=[], position=opgg_live._SEED_POSITION, position_source="assumed")
    with pytest.raises(OpggUnavailable):
        opgg_live.format_role_card(a)


def test_role_card_still_works_when_positions_present():
    card = opgg_live.format_role_card(_analysis())
    assert "TOP" in card["content"] and "89.0%" in card["content"]


# --------------------------------------------------------------------------- #
# An assumed lane is not asserted as fact
# --------------------------------------------------------------------------- #
def test_assumed_lane_is_not_claimed_on_the_stats_card():
    """Stats are champion-wide, so they still render — but without a lane claim."""
    a = _analysis(position="mid", position_source="assumed")
    card = opgg_live.format_stats_card(a)
    assert "MID" not in card["content"] and "MID" not in card["preview"]
    assert "51.0%" in card["content"]


def test_known_lane_is_still_labelled():
    card = opgg_live.format_stats_card(_analysis())
    assert "TOP" in card["content"]


def test_explicit_lane_from_the_query_is_trusted():
    card = opgg_live.format_stats_card(
        _analysis(position="jungle", position_source="explicit"))
    assert "JUNGLE" in card["content"]


# --------------------------------------------------------------------------- #
# Matchup
# --------------------------------------------------------------------------- #
def test_matchup_degrades_on_empty_payload():
    with pytest.raises(OpggIncomplete):
        opgg_live.format_matchup_card(_stripped(), "Darius")


def test_matchup_fallback_needs_the_overall_win_rate():
    """No head-to-head row is fine — but that branch quotes the overall rate, so a
    missing win_rate there would have printed "overall win rate is None%"."""
    a = _analysis(weak_counters=[], strong_counters=[])
    a["stats"]["win_rate"] = None
    with pytest.raises(OpggIncomplete):
        opgg_live.format_matchup_card(a, "Teemo")


def test_matchup_fallback_renders_when_win_rate_is_present():
    card = opgg_live.format_matchup_card(
        _analysis(weak_counters=[], strong_counters=[]), "Teemo")
    assert "no direct head-to-head" in card["content"]
    assert "51.0%" in card["content"] and "None" not in card["content"]


def test_matchup_row_renders_normally():
    card = opgg_live.format_matchup_card(_analysis(), "Darius")
    assert "Darius" in card["content"] and "47.0%" in card["content"]


# --------------------------------------------------------------------------- #
# Counters — incomplete ROWS are dropped, not carried as None
# --------------------------------------------------------------------------- #
def test_counter_rows_missing_fields_are_dropped():
    rows = opgg_live._normalize_counters([
        {"champion_name": "Darius", "my_win_rate": 0.47,
         "counter_win_rate": 0.53, "play": 900},          # complete
        {"champion_name": "Teemo", "my_win_rate": None,
         "counter_win_rate": 0.51, "play": 400},          # missing win rate
        {"champion_name": None, "my_win_rate": 0.49,
         "counter_win_rate": 0.51, "play": 400},          # missing name
        {"champion_name": "Jax", "my_win_rate": 0.49,
         "counter_win_rate": 0.51, "play": None},         # missing sample
    ])
    assert [r["name"] for r in rows] == ["Darius"]


def test_counters_card_degrades_on_empty_payload():
    with pytest.raises(OpggIncomplete):
        opgg_live.format_counters_card(_stripped(), "weak")


# --------------------------------------------------------------------------- #
# Build
# --------------------------------------------------------------------------- #
def test_build_degrades_on_empty_payload():
    with pytest.raises(OpggIncomplete):
        opgg_live.format_build_card(_stripped())


def test_build_renders_normally():
    card = opgg_live.format_build_card(_analysis())
    assert "Stridebreaker" in card["content"] and "None" not in card["content"]


# --------------------------------------------------------------------------- #
# The line between "missing" and "genuinely nothing" — must NOT be flattened
# --------------------------------------------------------------------------- #
def test_healthy_payload_with_no_counter_sample_still_answers():
    """OP.GG having no counter data is a real finding, not a degradation."""
    card = opgg_live.format_counters_card(
        _analysis(weak_counters=[], strong_counters=[]), "weak")
    assert "no sufficient counter sample" in card["content"]


def test_healthy_payload_with_no_build_sample_still_answers():
    card = opgg_live.format_build_card(_analysis(build={}))
    assert "no sufficient build sample" in card["content"]


def test_optional_stat_fields_are_omitted_not_nulled():
    """ban_rate/kda/play aren't required; their absence trims the sentence."""
    a = _analysis()
    a["stats"].update({"ban_rate": None, "kda": None, "play": None})
    card = opgg_live.format_stats_card(a)
    assert "None" not in card["content"]
    assert "ban rate" not in card["content"] and "Sample:" not in card["content"]
    assert "51.0%" in card["content"]      # the required ones still render


# --------------------------------------------------------------------------- #
# End to end from the RAW payload — exercises analyze() + _normalize, not just
# hand-built dicts, so a regression in the parsing layer is caught too.
# --------------------------------------------------------------------------- #
def _patch_tool(monkeypatch, payload):
    monkeypatch.setattr(opgg_live._mgr, "call_tool", lambda name, args: payload)


# The incident: HTTP-fine, tool-fine, but `data` has no `summary`.
INCIDENT_PAYLOAD = {"champion": "GAREN", "position": "MID", "data": {}}


def test_raw_incident_payload_degrades_end_to_end(monkeypatch):
    _patch_tool(monkeypatch, INCIDENT_PAYLOAD)
    a = opgg_live.analyze("Garen", "Garen", "stats")
    # The lane was never learned, so it must be flagged as assumed...
    assert a["position_source"] == "assumed"
    # ...and every card path must refuse to state it.
    with pytest.raises(OpggIncomplete):
        opgg_live.format_stats_card(a)
    with pytest.raises(OpggIncomplete):
        opgg_live.format_role_card(a)


def test_raw_healthy_payload_still_renders(monkeypatch):
    """Guard against the validator being so strict it rejects good data."""
    _patch_tool(monkeypatch, {
        "champion": "GAREN", "position": "TOP",
        "data": {"summary": {"average_stats": {"win_rate": 0.51, "pick_rate": 0.06,
                                               "ban_rate": 0.04, "kda": 1.83,
                                               "tier": 3, "rank": 32, "play": 14589},
                             "positions": [{"name": "TOP", "stats": {"role_rate": 0.89}},
                                           {"name": "MID", "stats": {"role_rate": 0.09}}]},
                 "trends": {"win": {"version": "16.15",
                                    "created_at": "2026-07-29T02:51:48+09:00"}}}})
    a = opgg_live.analyze("Garen", "Garen", "stats")
    assert a["position"] == "top" and a["position_source"] == "discovered"
    card = opgg_live.format_stats_card(a)
    assert "TOP" in card["preview"] and "51.0%" in card["preview"]
    assert "None" not in card["content"]


# --------------------------------------------------------------------------- #
# The empty-payload detector itself
# --------------------------------------------------------------------------- #
def test_payload_is_empty_detects_the_incident_shape():
    assert opgg_live._payload_is_empty(_stripped()) is True


def test_payload_is_empty_is_false_when_anything_usable_is_present():
    assert opgg_live._payload_is_empty(_analysis()) is False
    # Only a build, nothing else — still usable.
    only_build = _stripped()
    only_build["build"] = {"core_items": {"names": ["Stridebreaker"]}}
    assert opgg_live._payload_is_empty(only_build) is False
    # Only counters.
    only_counters = _stripped()
    only_counters["weak_counters"] = [{"name": "Darius", "my_win_rate": 47.0,
                                       "counter_win_rate": 53.0, "play": 900}]
    assert opgg_live._payload_is_empty(only_counters) is False


# --------------------------------------------------------------------------- #
# Empty payload on a warm session -> reset and retry once
#
# The live service failed Garen 4/4 while every other champion succeeded on the
# same warm session, and fresh sessions fetched Garen fine. That points at
# session-scoped staleness rather than missing data, so an empty payload rebuilds
# the session and tries again before concluding OP.GG has nothing.
# --------------------------------------------------------------------------- #
EMPTY_RAW = {"champion": "GAREN", "position": "MID", "data": {}}
FULL_RAW = {
    "champion": "GAREN", "position": "TOP",
    "data": {"summary": {"average_stats": {"win_rate": 0.51, "pick_rate": 0.06,
                                           "ban_rate": 0.04, "kda": 1.83,
                                           "tier": 3, "rank": 32, "play": 14589},
                         "positions": [{"name": "TOP", "stats": {"role_rate": 0.89}}]},
             "trends": {"win": {"version": "16.15", "created_at": "2026-07-29"}}}}


def test_empty_payload_resets_the_session_and_retries(monkeypatch):
    calls, resets = [], []

    def fake_call(name, args):
        calls.append(args["position"])
        return EMPTY_RAW if not resets else FULL_RAW

    monkeypatch.setattr(opgg_live._mgr, "call_tool", fake_call)
    monkeypatch.setattr(opgg_live._mgr, "reset",
                        lambda: (resets.append(1), True)[1])

    a = opgg_live.analyze("Garen", "Garen", "stats")
    assert len(resets) == 1, "expected exactly one session reset"
    assert a["stats"]["win_rate"] == 51.0, "should recover after the reset"
    opgg_live.format_stats_card(a)      # renders, does not raise


def test_still_empty_after_reset_degrades_and_does_not_loop(monkeypatch):
    calls, resets = [], []

    def fake_call(name, args):
        calls.append(args["position"])
        return EMPTY_RAW

    monkeypatch.setattr(opgg_live._mgr, "call_tool", fake_call)
    monkeypatch.setattr(opgg_live._mgr, "reset",
                        lambda: (resets.append(1), True)[1])

    a = opgg_live.analyze("Garen", "Garen", "stats")
    assert len(resets) == 1, "must reset at most once — no retry loop"
    with pytest.raises(OpggIncomplete):
        opgg_live.format_stats_card(a)


def test_healthy_payload_never_resets_the_session(monkeypatch):
    resets = []
    monkeypatch.setattr(opgg_live._mgr, "call_tool", lambda n, a: FULL_RAW)
    monkeypatch.setattr(opgg_live._mgr, "reset",
                        lambda: (resets.append(1), True)[1])
    opgg_live.analyze("Garen", "Garen", "stats")
    assert resets == [], "a good payload must not churn the session"


def test_empty_payload_is_logged_with_the_raw_body(monkeypatch, caplog):
    monkeypatch.setattr(opgg_live._mgr, "call_tool", lambda n, a: EMPTY_RAW)
    monkeypatch.setattr(opgg_live._mgr, "reset", lambda: False)  # reset unavailable
    with caplog.at_level("WARNING", logger="docpilot"):
        opgg_live.analyze("Garen", "Garen", "stats")
    logged = " ".join(r.message for r in caplog.records)
    assert "opgg_empty_payload" in logged
    assert "GAREN" in logged and "champion" in logged


# --------------------------------------------------------------------------- #
# Short matchup lists are disclosed as PARTIAL — never as complete.
#
# The note shipped here previously said "they publish only N at this sample size,
# so it is short, not truncated". That was false, and it was live. OP.GG's full
# matchup table (lol_get_lane_matchup_guide -> data.counters) holds 41 rows for
# Jax (TOP); the weak/strong_counters field we read caps at 3 and gave us 1. The
# list WAS truncated. Disclosure must not out-claim the evidence.
# --------------------------------------------------------------------------- #
def _counter_rows(n):
    return [{"name": f"Champ{i}", "my_win_rate": 52.0 + i,
             "counter_win_rate": 48.0 - i, "play": 100 + i} for i in range(n)]


@pytest.mark.parametrize("n", [1, 2, 3])
def test_a_short_list_is_scoped_to_what_opgg_lists(n):
    """Scoped completeness is fine — "every one OP.GG lists at 50+ games" is a
    claim about the source and the floor, both of which we hold."""
    card = opgg_live.format_counters_card(
        _analysis(strong_counters=_counter_rows(n)), "strong")
    assert f"every favourable matchup OP.GG lists" in card["content"]
    assert f"at {opgg_live.MIN_MATCHUP_GAMES}+ games" in card["content"]


@pytest.mark.parametrize("n", [1, 2, 3])
def test_short_counter_list_is_scoped_the_same_way(n):
    card = opgg_live.format_counters_card(
        _analysis(weak_counters=_counter_rows(n)), "weak")
    assert "every counter OP.GG lists" in card["content"]


@pytest.mark.parametrize("n", [1, 2, 3, 4, 5, 12])
def test_no_list_length_ever_claims_bare_completeness(n):
    """The regression guard. The false note said "they publish only N at this
    sample size, so it is short, not truncated". No row count may bring it back,
    and no phrasing may claim completeness without naming source and floor."""
    for direction, key in (("strong", "strong_counters"), ("weak", "weak_counters")):
        card = opgg_live.format_counters_card(
            _analysis(**{key: _counter_rows(n)}), direction)
        body = card["content"].lower()
        for claim in ("not truncated", "complete counter list",
                      "complete favourable-matchup list", "at this sample size"):
            assert claim not in body, f"{direction}/{n} still claims: {claim}"


def test_a_truncated_list_says_where_it_stops():
    """12 rows, 5 printed — the reader must be told the other 7 exist."""
    card = opgg_live.format_counters_card(
        _analysis(weak_counters=_counter_rows(12)), "weak")
    assert "OP.GG lists 12 counters" in card["content"]
    assert "these are the 5 most one-sided" in card["content"]
    assert "(5 of 12)" in card["preview"]
    assert "every counter OP.GG lists" not in card["content"]


def test_rows_below_the_floor_are_disclosed_not_hidden():
    a = _analysis(weak_counters=_counter_rows(2))
    a["matchups_below_floor"] = 7
    card = opgg_live.format_counters_card(a, "weak")
    assert f"7 matchups had fewer than {opgg_live.MIN_MATCHUP_GAMES} games" in card["content"]


def test_a_singular_dropped_row_reads_as_one():
    a = _analysis(weak_counters=_counter_rows(2))
    a["matchups_below_floor"] = 1
    body = opgg_live.format_counters_card(a, "weak")["content"]
    assert "1 matchup had fewer" in body and "were left out" not in body


def test_the_note_does_not_guess_at_a_reason():
    """We know OP.GG returned N; we do not know their threshold. Don't invent one."""
    card = opgg_live.format_counters_card(
        _analysis(strong_counters=_counter_rows(1)), "strong")
    for invented in ("insufficient", "because", "too few games", "minimum of"):
        assert invented not in card["content"].lower()


def test_short_list_still_names_every_row():
    card = opgg_live.format_counters_card(
        _analysis(strong_counters=_counter_rows(1)), "strong")
    assert "Champ0" in card["content"] and "52.0%" in card["content"]


def test_empty_list_keeps_its_own_wording_not_the_short_note():
    """Zero rows is a different statement from a short list."""
    card = opgg_live.format_counters_card(
        _analysis(strong_counters=[], weak_counters=[]), "strong")
    assert "no sufficient favorable-matchup sample" in card["content"]
    assert "OP.GG surfaces" not in card["content"]
