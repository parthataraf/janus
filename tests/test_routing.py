"""Unit tests for LoL entity linking + routing signals (Phase 4b).

These cover the pure helpers only — no DB. The DB-backed structured lookups are
covered by the integration verification.
"""

from core.lol_routing import (
    base_stat_field,
    wants_full_stat_line,
    wants_build,
    decide_branch,
    detect_lane,
    detect_rank,
    detect_slot,
    has_numeric_intent,
    link_entities,
    live_stats_intent,
    _stat_phrase,
    _norm,
)

YAS = ("Yasuo", "Yasuo")
ZED = ("Zed", "Zed")
GAR = ("Garen", "Garen")
JINX = ("Jinx", "Jinx")


def _ents(*champs):
    return {"champions": list(champs), "items": []}

CHAMP = {"champions": [("Garen", "Garen")], "items": []}
ITEM = {"champions": [], "items": ["Infinity Edge"]}
NONE = {"champions": [], "items": []}

# A tiny hand-built index (what _build_index would produce from the DB).
INDEX = {
    "champions": {
        "yasuo": ("Yasuo", "Yasuo"),
        "twisted fate": ("TwistedFate", "Twisted Fate"),
        "kaisa": ("Kaisa", "Kai'Sa"),
    },
    "items": {
        "infinity edge": "Infinity Edge",
        "the collector": "The Collector",
    },
}


def test_link_entities_champion_possessive():
    hits = link_entities("What is the cooldown of Yasuo's Q at rank 3?", INDEX)
    assert ("Yasuo", "Yasuo") in hits["champions"]
    assert hits["items"] == []


def test_link_entities_multiword_and_apostrophe():
    assert ("TwistedFate", "Twisted Fate") in link_entities("Twisted Fate's ult", INDEX)["champions"]
    assert ("Kaisa", "Kai'Sa") in link_entities("Kai'Sa W damage", INDEX)["champions"]


def test_link_entities_item():
    assert link_entities("Infinity Edge cost", INDEX)["items"] == ["Infinity Edge"]


def test_link_entities_misses():
    hits = link_entities("How do I bake chocolate chip cookies?", INDEX)
    assert hits["champions"] == [] and hits["items"] == []


def test_detect_slot():
    assert detect_slot("Yasuo's Q at rank 3") == "Q"
    assert detect_slot("what does his passive do") == "P"
    assert detect_slot("Yasuo ultimate range") == "R"
    assert detect_slot("cast the ult") == "R"
    assert detect_slot("Yasuo W wall") == "W"
    # lowercase stray letters must NOT be read as a slot
    assert detect_slot("how do i escape early game") is None


def test_detect_rank():
    assert detect_rank("cooldown at rank 3") == 3
    assert detect_rank("at level 5") == 5
    assert detect_rank("what does it do") is None


def test_numeric_intent():
    assert has_numeric_intent("cooldown of Yasuo's Q") is True
    assert has_numeric_intent("Yasuo Q at rank 3") is True  # via rank
    assert has_numeric_intent("which items give armor penetration") is True
    assert has_numeric_intent("what does Yasuo's passive do") is False


def test_stat_phrase():
    assert _stat_phrase("which items give armor penetration?") == "armor penetration"
    assert _stat_phrase("items with attack speed") == "attack speed"
    assert _stat_phrase("what does the passive do") is None


def test_norm_possessive_and_apostrophe():
    assert _norm("Yasuo's") == "yasuo"
    assert _norm("Kai'Sa") == "kaisa"


# --- champion base-stat routing (4b follow-up) ---
def test_base_stat_field():
    assert base_stat_field("What is Garen's base movement speed?") == "movespeed"
    assert base_stat_field("Ashe's base attack range") == "attackrange"  # not bare "range"
    assert base_stat_field("how much base armor does he have") == "armor"
    assert base_stat_field("base attack damage") == "attackdamage"
    assert base_stat_field("what does the passive do") is None


def test_route_champion_base_stat():
    # The Garen case: champion + no slot + base-stat phrase -> champion_stat,
    # NOT hijacked into a movement-speed item list.
    assert decide_branch("What is Garen's base movement speed?", CHAMP, None) == "champion_stat"
    assert decide_branch("Garen's base attack range", CHAMP, None) == "champion_stat"


def test_wants_full_stat_line_general_phrasings():
    # The reported bug: "master yi base stats" refused because no single field
    # matched, even though lol_champions holds the whole stat block.
    for q in (
        "master yi base stats",
        "master yi stats",
        "what are master yi's stats",
        "what are Master Yi's base stats?",
        "master yi stat line",
        "master yi statline",
        "show me garen's stat block",
        "ashe full stats",
        "ashe all stats",
        "what is jinx's stat sheet",
        "garen base stat",
        "yasuo attributes",
    ):
        assert wants_full_stat_line(q), q


def test_wants_full_stat_line_ignores_non_stat_questions():
    for q in (
        "what does Yasuo's passive do",
        "how does Thresh's lantern work",
        "who counters yasuo",
        "what is the cooldown of Ahri's Q",
    ):
        assert not wants_full_stat_line(q), q


def test_route_general_stats_reaches_structured_branch():
    for q in ("master yi base stats", "master yi stats", "what are master yi's stats"):
        assert decide_branch(q, CHAMP, None) == "champion_stat", q
    # No champion named -> not a champion_stat question.
    assert decide_branch("base stats", NONE, None) != "champion_stat"


def test_specific_stat_still_wins_over_general():
    """GUARD: adding the general phrasing must not swallow the specific ones."""
    # Still routes to the structured branch...
    assert decide_branch("What is Garen's base movement speed?", CHAMP, None) == "champion_stat"
    assert decide_branch("Ashe's base attack range", CHAMP, None) == "champion_stat"
    # ...and still resolves to the ONE field, even when "stats" is also present,
    # so these keep answering with a single number rather than the whole block.
    assert base_stat_field("What is Garen's base movement speed?") == "movespeed"
    assert base_stat_field("garen movement speed stats") == "movespeed"
    assert base_stat_field("master yi armor stats") == "armor"
    # A general question resolves to no single field -> the full line.
    assert base_stat_field("master yi base stats") is None


def test_route_ability_and_item_unchanged():
    # Guard: existing ability / item / multi-row routing is untouched.
    assert decide_branch("cooldown of Yasuo's Q at rank 3", CHAMP, "Q") == "ability"
    assert decide_branch("how much gold does Infinity Edge cost", ITEM, None) == "item"
    assert decide_branch("which items give armor penetration", NONE, None) == "items_with"
    # a champion passive question is pure prose (no structured branch)
    assert decide_branch("what does Garen's passive do", CHAMP, "P") is None


# --- live item-build intent (OP.GG) --- #
def test_live_build_intent():
    for q in (
        "master yi items",
        "what items should I build on Yasuo",
        "Yasuo build",
        "best items for Garen",
        "what should I buy on Jinx",
        "Zed build path",
        "Garen core items",
        "what runes for Yasuo",
        "Yasuo rune page",
        "Jinx skill order",
        "Yasuo first item",
    ):
        ents = _ents(YAS if "yasuo" in q.lower() else
                     GAR if "garen" in q.lower() else
                     JINX if "jinx" in q.lower() else
                     ZED if "zed" in q.lower() else YAS)
        r = live_stats_intent(q, ents)
        assert r and r["kind"] == "build", q


def test_live_build_needs_a_champion():
    # "which items give armor penetration" is the structured multi-row lookup,
    # not a live build question -- it names no champion.
    assert live_stats_intent("which items give armor penetration", NONE) is None
    assert live_stats_intent("how much gold does Infinity Edge cost", NONE) is None


def test_live_build_ignores_the_verb_sense_of_build():
    # "build up" is a mechanics question -> must stay on the prose path.
    assert live_stats_intent("how does Yasuo build up his shield?", _ents(YAS)) is None
    assert not wants_build("how does Yasuo build up his shield?")


def test_live_build_respects_explicit_lane():
    r = live_stats_intent("what should Garen build top", _ents(GAR))
    assert r["kind"] == "build" and r["position"] == "top"


def test_live_build_does_not_hijack_other_live_intents():
    # GUARD: matchup/counter/role/winrate questions keep their own kinds.
    assert live_stats_intent("who counters Yasuo?", _ents(YAS))["kind"] == "counters"
    assert live_stats_intent("Yasuo vs Zed matchup", _ents(YAS, ZED))["kind"] == "matchup"
    assert live_stats_intent("what's Jinx's win rate?", _ents(JINX))["kind"] == "champion_stats"
    r = live_stats_intent("what role is Garen played in", _ents(GAR))
    assert r["kind"] == "champion_stats" and r["role_query"] is True


def test_live_build_does_not_fire_on_mechanics_questions():
    for q in ("what does Yasuo's passive do", "how does Thresh's lantern work",
              "what is the cooldown of Yasuo's Q"):
        assert live_stats_intent(q, _ents(YAS)) is None, q


# --- live-stats (OP.GG) intent detection (Phase 4h) --- #
def test_live_matchup_two_champions():
    r = live_stats_intent("Yasuo vs Zed matchup", _ents(YAS, ZED))
    assert r and r["kind"] == "matchup" and r["a"] == YAS and r["b"] == ZED


def test_live_counters_one_champion():
    r = live_stats_intent("who counters Yasuo?", _ents(YAS))
    assert r and r["kind"] == "counters" and r["a"] == YAS
    assert live_stats_intent("what beats Garen top", _ents(GAR))["kind"] == "counters"
    assert live_stats_intent("what champions beat Zed in mid", _ents(ZED))["kind"] == "counters"


def test_live_popularity_and_beginner():
    assert live_stats_intent("what's Jinx's win rate?", _ents(JINX))["kind"] == "champion_stats"
    assert live_stats_intent("is Garen good for beginners?", _ents(GAR))["kind"] == "champion_stats"
    assert live_stats_intent("is Zed strong right now", _ents(ZED))["kind"] == "champion_stats"


def test_live_intent_guards_existing_questions():
    # None of the existing question shapes may trigger the live path.
    assert live_stats_intent("What is the cooldown of Yasuo's Q at rank 3?", _ents(YAS)) is None
    assert live_stats_intent("What is Garen's base movement speed?", _ents(GAR)) is None
    assert live_stats_intent("What does Yasuo's passive do?", _ents(YAS)) is None
    assert live_stats_intent("Which items give armor penetration?", _ents()) is None
    assert live_stats_intent("How much gold does Infinity Edge cost?", _ents()) is None


# --- hardening: gamer spellings, possessives, matchup direction (Phase 4h) --- #
MYI = ("MasterYi", "Master Yi")
REN = ("Renekton", "Renekton")


def test_link_entities_possessive_no_apostrophe():
    idx = {"champions": {"master yi": MYI, "jinx": JINX}, "items": {}}
    assert MYI in link_entities("what is master yis winrate", idx)["champions"]
    assert JINX in link_entities("hows jinxs win rate", idx)["champions"]
    # the apostrophe form still works
    assert MYI in link_entities("master yi's stats", idx)["champions"]


def test_live_winrate_one_word_and_wr():
    assert live_stats_intent("what is master yis winrate", _ents(MYI))["kind"] == "champion_stats"
    assert live_stats_intent("jinx wr right now", _ents(JINX))["kind"] == "champion_stats"


def test_live_matchup_direction():
    r = live_stats_intent("what jungler is master yi good against", _ents(MYI))
    assert r["kind"] == "counters" and r["direction"] == "strong"
    assert live_stats_intent("who counters Yasuo", _ents(YAS))["direction"] == "weak"
    assert live_stats_intent("what is Zed weak against", _ents(ZED))["direction"] == "weak"


def test_live_vs_tail_two_champions():
    r = live_stats_intent("master yis winrate vs renekton", _ents(MYI, REN))
    assert r["kind"] == "matchup" and r["a"] == MYI and r["b"] == REN


def test_detect_lane():
    assert detect_lane("who counters yasuo top") == "top"
    assert detect_lane("jinx support winrate") == "support"
    assert detect_lane("is master yi jungle good") == "jungle"
    assert detect_lane("zed mid win rate") == "mid"
    assert detect_lane("gragas bot") == "adc"
    assert detect_lane("who counters yasuo") is None  # no lane -> most-played


def test_live_role_intent():
    for q in ["what role is master yi played in", "master yi role",
              "what is master yi's primary role", "what lane does jinx play",
              "where do you play garen", "what position is zed"]:
        champ = MYI if "master yi" in q else (JINX if "jinx" in q else
                (GAR if "garen" in q else ZED))
        r = live_stats_intent(q, _ents(champ))
        assert r and r["kind"] == "champion_stats" and r["role_query"] is True, q
    # matchup phrasing still wins over the word "role"
    assert live_stats_intent("what role is master yi good against", _ents(MYI))["kind"] == "counters"
    # a plain win-rate question is champion_stats but NOT a role query
    assert live_stats_intent("what's Jinx's win rate?", _ents(JINX))["role_query"] is False


def test_live_intent_lane_override_and_default():
    # explicit lane passes through as `position`
    assert live_stats_intent("who counters yasuo top", _ents(YAS))["position"] == "top"
    assert live_stats_intent("jinx support winrate", _ents(JINX))["position"] == "support"
    # no lane named -> position None (mechanism uses OP.GG's most-played)
    assert live_stats_intent("who counters yasuo", _ents(YAS))["position"] is None
    assert live_stats_intent("what's Jinx's win rate?", _ents(JINX))["position"] is None


# --------------------------------------------------------------------------- #
# Base stat line: a manaless champion must not render the bare word "None".
#
# Data Dragon stores the LITERAL string "None" as the resource for Garen,
# Katarina, Riven, Viego, Zac and Dr. Mundo, and "" for Bel'Veth. Echoing it
# produced "Resource: None.", which reads as a missing value rather than as the
# fact that the champion has no resource — the same ambiguity class as the live
# card's "win None% · tier None".
# --------------------------------------------------------------------------- #
import pytest  # noqa: E402

from core.lol_routing import _format_champion_stat_line  # noqa: E402

_STATS = {"hp": 690, "hpperlevel": 98, "hpregen": 8, "hpregenperlevel": 0.5,
          "mp": 0, "mpperlevel": 0, "armor": 38, "armorperlevel": 4.2,
          "spellblock": 32, "spellblockperlevel": 1.55, "attackdamage": 69,
          "attackspeed": 0.625, "attackrange": 175, "movespeed": 340}


def _card(partype, stats=None):
    ch = {"stats": dict(stats or _STATS), "partype": partype}
    return _format_champion_stat_line("Garen", "Garen", ch, "16.15.1")["content"]


@pytest.mark.parametrize("partype", ["None", "none", "", "   ", None, "manaless"])
def test_manaless_champions_never_print_the_word_none(partype):
    card = _card(partype)
    assert "None" not in card
    assert "No mana or secondary resource." in card
    assert "movement speed 340" in card          # the real stats still render


@pytest.mark.parametrize("partype,expected", [
    ("Mana", "Resource: Mana."),
    ("Energy", "Resource: Energy."),
    ("Fury", "Resource: Fury."),
    ("Blood Well", "Resource: Blood Well."),
])
def test_champions_with_a_real_resource_still_name_it(partype, expected):
    stats = dict(_STATS, mp=263, mpperlevel=58, mpregen=6.6, mpregenperlevel=0.35)
    card = _card(partype, stats)
    assert expected in card
    assert "No mana or secondary resource" not in card


def test_a_real_resource_relabels_the_mana_rows():
    stats = dict(_STATS, mp=200, mpperlevel=40)
    assert "energy 200" in _card("Energy", stats)


def test_manaless_champions_omit_the_mana_rows_entirely():
    stats = dict(_STATS, mp=100, mpperlevel=10)   # DDragon sometimes carries junk
    card = _card("None", stats)
    # No mana STAT row (the phrase "No mana or secondary resource" is the point).
    assert "mana 100" not in card and "resource 100" not in card
    assert "mana regen" not in card
    assert card.count("mana") == 1                # only the manaless sentence
