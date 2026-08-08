"""Multi-row item answers: find every item, carry its value, never over-claim.

"Which items give armor penetration?" returned 5 of 23. Not a LIMIT — the query
searched the description text for the literal phrase, and lethality items
describe themselves as "18 Lethality" and never say it. The values were being
dropped too: the list passage emitted name and gold only, though the numbers sit
in the description we already store.

And 31 items were duplicated by alternate-mode variants, so a list could name
one item twice at two different prices.
"""

from __future__ import annotations

import pytest

from core import store
from core.lol_roster import select_canonical
from core.lol_routing import _format_item_list, _stat_value


# --------------------------------------------------------------------------- #
# Tag lookup: the structured property, not the prose
# --------------------------------------------------------------------------- #
def test_armor_pen_uses_the_tag_but_lethality_does_not():
    """The containment runs ONE way. Lethality is armor penetration, so the broad
    question must return lethality items — but the narrow question must not
    return percentage-penetration items. Mapping both to the tag put Lord
    Dominik's Regards (35% armor pen, no lethality) into the lethality answer,
    and the faithfulness judge scored it 3/5."""
    assert store._STAT_TAGS["armor penetration"] == "ArmorPenetration"
    assert "lethality" not in store._STAT_TAGS


@pytest.mark.parametrize("phrase,tag", [
    ("magic penetration", "MagicPenetration"),
    ("critical strike", "CriticalStrike"),
    ("life steal", "LifeSteal"),
    ("lifesteal", "LifeSteal"),
    ("attack speed", "AttackSpeed"),
    ("ability haste", "AbilityHaste"),
    ("tenacity", "Tenacity"),
])
def test_stat_phrases_map_to_real_tags(phrase, tag):
    assert store._STAT_TAGS[phrase] == tag


def test_untagged_stats_fall_back_to_text_search():
    """Riot tags most stats but not all — omnivamp has no tag, and the text
    search still has to answer for it."""
    assert "omnivamp" not in store._STAT_TAGS


# --------------------------------------------------------------------------- #
# Values come out of the description
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("desc,phrase,expected", [
    ("20 Attack Damage 18% Armor Penetration", "armor penetration", "18% armor penetration"),
    ("35 Attack Damage 35% Armor Penetration", "armor penetration", "35% armor penetration"),
    ("55 Attack Damage 18 Lethality", "armor penetration", "18 lethality"),
    ("55 Attack Damage 18 Lethality", "lethality", "18 lethality"),
    ("40 Attack Damage 12% Life Steal", "life steal", "12% life steal"),
])
def test_stat_value_is_read_off_the_description(desc, phrase, expected):
    assert _stat_value(desc, phrase) == expected


def test_stat_value_is_none_when_absent():
    assert _stat_value("40 Attack Damage 20 Ability Haste", "armor penetration") is None
    assert _stat_value("", "armor penetration") is None
    assert _stat_value(None, "armor penetration") is None


def test_stat_value_does_not_match_a_longer_word():
    assert _stat_value("15 Tenacity Boost", "tenacity") == "15 tenacity"
    assert _stat_value("10 Armor", "armor penetration") is None


# --------------------------------------------------------------------------- #
# The passage: values present, completeness stated honestly
# --------------------------------------------------------------------------- #
def _items(n, **over):
    base = [{"name": f"Item{i}", "gold_total": 3000 - i,
             "description": f"{10 + i}% Armor Penetration", "plaintext": ""}
            for i in range(n)]
    for b in base:
        b.update(over)
    return base


def test_every_listed_item_carries_its_value():
    out = _format_item_list("armor penetration",
                            {"items": _items(3), "total": 3}, "16.14.1")
    for i in range(3):
        assert f"Item{i}" in out["content"]
        assert f"{10 + i}% armor penetration" in out["content"]


def test_a_complete_list_says_it_is_complete():
    out = _format_item_list("armor penetration",
                            {"items": _items(23), "total": 23}, "16.14.1")
    assert "That is all 23 items" in out["content"]
    assert "Showing the" not in out["content"]


def test_a_truncated_list_says_it_was_cut():
    """The generator is told to enumerate every member, so a silent truncation
    would read as the complete set."""
    out = _format_item_list("health", {"items": _items(30), "total": 89}, "16.14.1")
    assert "Showing the 30 most expensive of 89" in out["content"]
    assert "That is all" not in out["content"]


def test_items_without_a_value_still_appear_with_gold():
    items = _items(2)
    items[1]["description"] = "No stat block here"
    out = _format_item_list("armor penetration", {"items": items, "total": 2}, "16.14.1")
    assert "Item1 (2999g)" in out["content"]


def test_the_cap_is_above_every_enumerable_category():
    """Armor penetration is the largest category a player asks to see in full,
    at 23; the cap has to clear it with room."""
    assert store.ITEM_LIST_LIMIT >= 30


# --------------------------------------------------------------------------- #
# Alternate-mode item variants — the champion rule, applied to items
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("base,variant,name", [
    ("3075", "323075", "Thornmail"),
    ("2065", "322065", "Shurelya's Battlesong"),
    ("6676", "667666", "The Collector"),
    ("3146", "663146", "Hextech Gunblade"),
    ("1101", "1107", "Scorchclaw Pup"),
])
def test_the_base_item_wins_over_its_alternate_mode_variant(base, variant, name):
    """Item ids never equal their name, so the canonical test can't separate
    them — the length tiebreak does, because a variant id is the base id with a
    numeric prefix. Same mechanism as MasterYi vs Jade_MasterYi."""
    for rows in ([(base, name), (variant, name)], [(variant, name), (base, name)]):
        kept, dropped = select_canonical(rows)
        assert kept == [(base, name)], f"{rows} kept {kept}"
        assert dropped == [(variant, name)]


def test_items_with_distinct_names_are_all_kept():
    rows = [("3075", "Thornmail"), ("3006", "Berserker's Greaves"), ("6676", "The Collector")]
    kept, dropped = select_canonical(rows)
    assert kept == rows and dropped == []
