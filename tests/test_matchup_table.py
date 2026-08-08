"""The full matchup table, and the follow-up that walks down it.

"Is Master Yi good against anyone?" answered "OP.GG has no sufficient
favorable-matchup sample" while op.gg's own page showed five favourable
matchups. The analysis tool exposes only OP.GG's curated weak/strong_counters:
three rows per direction, sample-thresholded, and empty on the favourable side
for Yi. lol_get_lane_matchup_guide returns `data.counters` — the whole table,
31 rows for Yi jungle — and sorting it reproduces both panels of their counter
tab exactly, to the game count.

The fixture below IS Master Yi's jungle table as returned on patch 16.15.
"""

from __future__ import annotations

import json

import pytest

from core import opgg_live
from core.opgg_live import (MATCHUP_ROWS, MIN_MATCHUP_GAMES, _matchup_rows,
                            _parse_json, _split_matchups)

# (name, play, win) — verbatim from data.counters for MASTER_YI / JUNGLE.
YI_JUNGLE = [
    ("Lee Sin", 210, 99), ("Graves", 201, 95), ("Viego", 150, 68),
    ("Wukong", 115, 54), ("Kha'Zix", 96, 55), ("Bel'Veth", 94, 47),
    ("Nocturne", 92, 42), ("Sylas", 90, 40), ("Jarvan IV", 86, 48),
    ("Vi", 82, 41), ("Kayn", 81, 43), ("Hecarim", 80, 34),
    ("Talon", 76, 44), ("Naafiri", 74, 36), ("Briar", 64, 30),
    ("Shaco", 58, 29), ("Qiyana", 56, 24), ("Jax", 55, 29),
    ("Nasus", 54, 25), ("Rengar", 52, 31), ("Zac", 50, 19),
    ("Nidalee", 48, 26), ("Xin Zhao", 47, 26), ("Zed", 46, 25),
    ("Rammus", 45, 19), ("Shyvana", 44, 24), ("Diana", 42, 19),
    ("Ekko", 42, 19), ("Warwick", 38, 14), ("Evelynn", 31, 18),
    ("Fiddlesticks", 30, 14),
]
COUNTERS = [{"champion_id": i, "champion_name": n, "play": p, "win": w}
            for i, (n, p, w) in enumerate(YI_JUNGLE)]


def _analysis(**over):
    every, kept = _matchup_rows(COUNTERS)
    weak, strong = _split_matchups(kept)
    a = {"display": "Master Yi", "champion_id": "MasterYi", "position": "jungle",
         "position_source": "discovered", "patch": "16.15",
         "fetched_at": "2026-08-01 00:00 UTC", "source_url": "https://op.gg/x",
         "stats": {"win_rate": 48.3, "pick_rate": 6.0, "ban_rate": 7.9,
                   "tier": 4, "rank": 124, "kda": 2.09, "play": 14848},
         "roles": [{"lane": "jungle", "share": 81.3}],
         "weak_counters": weak, "strong_counters": strong,
         "all_matchups": every, "matchup_total": len(every),
         "matchups_below_floor": len(every) - len(kept),
         "build": {}}
    a.update(over)
    return a


# --------------------------------------------------------------------------- #
# Reproducing op.gg's counter tab
# --------------------------------------------------------------------------- #
def test_the_table_reproduces_the_published_weak_panel():
    """op.gg's Master Yi Counter panel, "Weak against", top five."""
    _, kept = _matchup_rows(COUNTERS, floor=0)
    weak, _ = _split_matchups(kept)
    assert [(c["name"], c["my_win_rate"], c["play"]) for c in weak[:5]] == [
        ("Warwick", 36.8, 38), ("Zac", 38.0, 50), ("Rammus", 42.2, 45),
        ("Hecarim", 42.5, 80), ("Qiyana", 42.9, 56)]


def test_the_table_reproduces_the_published_strong_panel():
    """The side the old source could not answer at all."""
    _, kept = _matchup_rows(COUNTERS, floor=0)
    _, strong = _split_matchups(kept)
    assert [(c["name"], c["my_win_rate"], c["play"]) for c in strong[:5]] == [
        ("Rengar", 59.6, 52), ("Evelynn", 58.1, 31), ("Talon", 57.9, 76),
        ("Kha'Zix", 57.3, 96), ("Jarvan IV", 55.8, 86)]


def test_master_yi_now_has_favourable_matchups_at_all():
    """The original bug, stated as a test."""
    card = opgg_live.format_counters_card(_analysis(), "strong")
    assert "no sufficient favorable-matchup sample" not in card["content"]
    assert "Rengar" in card["content"]


# --------------------------------------------------------------------------- #
# Rates are ours to compute; League has no draws
# --------------------------------------------------------------------------- #
def test_the_two_rates_are_complements():
    every, _ = _matchup_rows(COUNTERS)
    for c in every:
        assert round(c["my_win_rate"] + c["counter_win_rate"], 1) == 100.0


@pytest.mark.parametrize("play,win", [(0, 0), (None, 3), (50, None)])
def test_unusable_rows_are_dropped_not_divided_by(play, win):
    every, _ = _matchup_rows([{"champion_name": "X", "play": play, "win": win}])
    assert every == []


def test_a_nameless_row_is_dropped():
    assert _matchup_rows([{"champion_name": None, "play": 90, "win": 50}])[0] == []


# --------------------------------------------------------------------------- #
# The sample floor
# --------------------------------------------------------------------------- #
def test_the_floor_removes_exactly_the_rows_below_it():
    every, kept = _matchup_rows(COUNTERS)
    assert len(every) == 31
    assert all(c["play"] >= MIN_MATCHUP_GAMES for c in kept)
    assert {c["name"] for c in every} - {c["name"] for c in kept} == {
        "Nidalee", "Xin Zhao", "Zed", "Rammus", "Shyvana", "Diana", "Ekko",
        "Warwick", "Evelynn", "Fiddlesticks"}


def test_the_floor_is_inclusive():
    """Zac sits exactly on 50 games and must survive."""
    _, kept = _matchup_rows(COUNTERS)
    assert any(c["name"] == "Zac" and c["play"] == 50 for c in kept)


def test_an_even_matchup_joins_neither_side():
    """A 50.0% row is not a champion anyone is strong or weak against."""
    weak, strong = _split_matchups(
        [{"name": "Even", "my_win_rate": 50.0, "counter_win_rate": 50.0, "play": 200}])
    assert weak == [] and strong == []


def test_a_hundred_game_floor_would_reinstate_the_bug():
    """Records WHY the floor is 50. At 100 games Master Yi has no favourable
    matchup at all, which is the failure this whole change exists to fix."""
    _, kept = _matchup_rows(COUNTERS, floor=100)
    _, strong = _split_matchups(kept)
    assert strong == []


# --------------------------------------------------------------------------- #
# Disclosure: position in the table, and the rows the floor ate
# --------------------------------------------------------------------------- #
def test_the_card_says_how_many_of_how_many():
    """21 of Yi's 31 matchups clear the floor: 12 unfavourable, 6 favourable, and
    3 (Bel'Veth, Vi, Shaco) sitting on exactly 50.0% and belonging to neither."""
    card = opgg_live.format_counters_card(_analysis(), "weak")
    assert f"OP.GG lists 12 counters for Master Yi (JUNGLE) at 50+ games" in card["content"]
    assert f"these are the {MATCHUP_ROWS} most one-sided" in card["content"]
    assert "10 matchups had fewer than 50 games" in card["content"]


def test_the_note_reads_as_a_sentence():
    """It follows a full stop, so it has to start with a capital."""
    for direction in ("weak", "strong"):
        body = opgg_live.format_counters_card(_analysis(), direction)["content"]
        assert " games). OP.GG lists" in body or " games). That is every" in body


def test_no_favourable_matchup_is_not_reported_as_a_sampling_problem():
    """Azir (MID) has matchups above the floor, just none he wins. Saying "none
    reaches 50 games" would blame the sample for a fact about the champion."""
    every = [{"name": f"C{i}", "my_win_rate": 40.0 + i, "counter_win_rate": 60.0 - i,
              "play": 200} for i in range(5)]
    a = _analysis(strong_counters=[], all_matchups=every, matchup_total=5,
                  matchups_below_floor=0)
    body = opgg_live.format_counters_card(a, "strong")["content"]
    assert "there is no champion Master Yi wins more than half its games against" in body
    assert "reaches 50 games" not in body


def test_the_card_prints_every_row_with_its_sample():
    card = opgg_live.format_counters_card(_analysis(), "strong")
    for name, games in [("Rengar", 52), ("Talon", 76), ("Kha'Zix", 96)]:
        assert f"{name} (Master Yi wins" in card["content"]
        assert f"{games} games)" in card["content"]


def test_the_card_does_not_call_a_fifty_game_sample_statistically_strong():
    """+/-13pp at n=50. The number is reportable; the word "statistically" was
    doing work the sample cannot support."""
    body = opgg_live.format_counters_card(_analysis(), "strong")["content"]
    assert "statistically strong" not in body
    assert "observed win rate over the sample" in body


def test_no_reportable_rows_is_distinct_from_no_data():
    """Nine favourable matchups exist; all are too thin to print."""
    every = [{"name": f"C{i}", "my_win_rate": 60.0, "counter_win_rate": 40.0, "play": 20}
             for i in range(9)]
    a = _analysis(strong_counters=[], weak_counters=[], all_matchups=every,
                  matchup_total=9, matchups_below_floor=9)
    body = opgg_live.format_counters_card(a, "strong")["content"]
    assert f"9 favourable matchups" in body
    assert f"none reaches {MIN_MATCHUP_GAMES} games" in body
    assert "no sufficient favorable-matchup sample" not in body


def test_an_empty_table_still_says_no_sample():
    a = _analysis(strong_counters=[], weak_counters=[], all_matchups=[],
                  matchup_total=0, matchups_below_floor=0)
    assert "no sufficient favorable-matchup sample" in \
        opgg_live.format_counters_card(a, "strong")["content"]


# --------------------------------------------------------------------------- #
# "who else counters yasuo" — walking down the list
# --------------------------------------------------------------------------- #
def test_a_followup_returns_the_next_rows_not_the_same_ones():
    first = opgg_live.format_counters_card(_analysis(), "weak")
    named = [n for n in ("Zac", "Hecarim", "Qiyana", "Nocturne", "Naafiri")
             if n in first["content"]]
    second = opgg_live.format_counters_card(_analysis(), "weak", exclude=named)
    for n in named:
        assert f"{n} (beats" not in second["content"], f"{n} repeated"
    assert "Continuing the same list" in second["content"]


def test_the_followup_matches_case_insensitively():
    card = opgg_live.format_counters_card(_analysis(), "weak", exclude=["  zAc  "])
    assert "Zac (beats" not in card["content"]


def test_exhausting_the_list_says_so_rather_than_repeating():
    every = [c["name"] for c in _analysis()["weak_counters"]]
    card = opgg_live.format_counters_card(_analysis(), "weak", exclude=every)
    assert "no further counters" in card["content"]
    assert "no sufficient counter sample" not in card["content"]


def test_no_exclusion_means_no_continuation_wording():
    assert "Continuing" not in opgg_live.format_counters_card(_analysis(), "weak")["content"]


# --------------------------------------------------------------------------- #
# A specific head-to-head answers from the UNFLOORED table
# --------------------------------------------------------------------------- #
def test_a_thin_matchup_is_answered_with_a_warning_not_refused():
    """Warwick has 38 games — below the list floor, but a direct "Yi vs Warwick"
    should get the number and be told the sample is thin."""
    card = opgg_live.format_matchup_card(_analysis(), "Warwick")
    assert "Master Yi wins 36.8%" in card["content"]
    assert "38 games is too few" in card["content"]
    assert "no direct head-to-head" not in card["content"]


def test_a_well_sampled_matchup_carries_no_warning():
    card = opgg_live.format_matchup_card(_analysis(), "Lee Sin")
    assert "Master Yi wins 47.1%" in card["content"]
    assert "too few" not in card["content"]


def test_an_untracked_opponent_still_falls_back_honestly():
    card = opgg_live.format_matchup_card(_analysis(), "Teemo")
    assert "no direct head-to-head matchup data" in card["content"]


# --------------------------------------------------------------------------- #
# Wiring
# --------------------------------------------------------------------------- #
def test_the_guide_is_parsed_as_json_not_the_positional_repr():
    """Two tools, two payload formats — the analysis tool answers in OP.GG's
    compact repr and this one in plain JSON."""
    assert _parse_json('{"data": {"counters": []}}') == {"data": {"counters": []}}
    with pytest.raises(Exception):
        _parse_json("[1, 2]")


def test_the_guide_gets_its_own_longer_timeout():
    """A measured 6.8s call under an 8s budget has no margin."""
    assert opgg_live.GUIDE_TIMEOUT_S > opgg_live.TIMEOUT_S


def test_fetch_matchups_passes_the_champion_as_its_own_opponent(monkeypatch):
    """The tool requires an opponent; data.counters is identical whichever one is
    passed, so a "who counters X" question with no opponent uses X itself."""
    seen = {}

    def fake(name, args, timeout=None, parse=None):
        seen.update({"tool": name, "args": args, "timeout": timeout})
        return {"data": {"counters": COUNTERS}}

    monkeypatch.setattr(opgg_live._mgr, "call_tool", fake)
    out = opgg_live.fetch_matchups("MasterYi", "jungle")
    assert seen["tool"] == "lol_get_lane_matchup_guide"
    assert seen["args"]["my_champion"] == seen["args"]["opponent_champion"] == "MASTER_YI"
    assert seen["args"]["position"] == "jungle"
    assert seen["timeout"] == opgg_live.GUIDE_TIMEOUT_S
    assert out["matchup_total"] == 31 and out["matchups_below_floor"] == 10
    assert len(out["weak_counters"]) == 12 and len(out["strong_counters"]) == 6


def test_a_guide_failure_degrades_rather_than_serving_the_thin_list(monkeypatch):
    """Falling back to the analysis tool's three rows would quietly restore the
    behaviour this replaces, with no way for the reader to tell."""
    def boom(name, args, timeout=None, parse=None):
        raise opgg_live.OpggEndpointError("tool_error")

    monkeypatch.setattr(opgg_live._mgr, "call_tool", boom)
    with pytest.raises(opgg_live.OpggUnavailable):
        opgg_live.fetch_matchups("MasterYi", "jungle")
