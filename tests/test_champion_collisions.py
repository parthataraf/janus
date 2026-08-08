"""Champion display names are NOT unique — the index must pick the base champion.

Production incident: every live-stats question about Garen degraded to "OP.GG
doesn't have current stats for Garen right now", while Darius, Sett, Mordekaiser,
Yasuo and Master Yi all worked, and direct MCP calls for GAREN returned complete
data. The endpoint was fine; the champion id we sent was wrong.

Patch 16.15.1 added 60 `Jade_*` champions that reuse the base champion's display
name verbatim. Data Dragon carries both `Garen` and `Jade_Garen`, BOTH named
"Garen". `_build_index` keyed a dict on the normalized display name, so the two
collided and whichever row the database returned last silently won. On the
deployed corpus that was `Jade_Garen` -> we asked OP.GG for JADE_GAREN, which is
a real champion to them but has `average_stats: None`, so the payload looked
empty and the answer degraded.

Two things made it hard to see: the failure named the RIGHT champion ("Garen")
because the display name was correct, and it was arbitrary which champions broke
because the SQL had no ORDER BY.
"""

from __future__ import annotations

import pytest

from core import lol_routing
from core.lol_routing import _build_index, _canonicality, _norm

# The real shape of the 16.15.1 rows, base and variant sharing a display name.
GAREN = ("Garen", "Garen")
JADE_GAREN = ("Jade_Garen", "Garen")
YI = ("MasterYi", "Master Yi")
JADE_YI = ("Jade_MasterYi", "Master Yi")
WUKONG = ("MonkeyKing", "Wukong")
JADE_WUKONG = ("Jade_MonkeyKing", "Wukong")


@pytest.fixture(autouse=True)
def _clear_index_cache():
    """_build_index is lru_cached; don't leak an index between tests."""
    _build_index.cache_clear()
    yield
    _build_index.cache_clear()


def _index(champion_rows, items=()):
    def fake_names(patch):
        return {"champions": list(champion_rows), "items": list(items)}

    orig = lol_routing.store.lol_entity_names
    lol_routing.store.lol_entity_names = fake_names
    try:
        return _build_index("16.15.1")["champions"]
    finally:
        lol_routing.store.lol_entity_names = orig
        _build_index.cache_clear()


# --------------------------------------------------------------------------- #
# The incident
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("rows", [
    [GAREN, JADE_GAREN],      # variant last — the deployed ordering that broke
    [JADE_GAREN, GAREN],      # variant first
])
def test_garen_resolves_to_the_base_champion_whatever_the_row_order(rows):
    assert _index(rows)[_norm("Garen")] == GAREN


def test_the_variant_is_not_reachable_under_the_shared_name():
    """Asking for "Garen" must never hand back Jade_Garen's id."""
    idx = _index([GAREN, JADE_GAREN])
    assert idx[_norm("Garen")][0] == "Garen"
    assert all(cid != "Jade_Garen" for cid, _ in idx.values())


@pytest.mark.parametrize("rows,expected", [
    ([YI, JADE_YI], YI),
    ([JADE_YI, YI], YI),
    ([WUKONG, JADE_WUKONG], WUKONG),
    ([JADE_WUKONG, WUKONG], WUKONG),
])
def test_ids_that_differ_from_their_display_name_still_resolve(rows, expected):
    """MasterYi/MonkeyKing never equal their display name, so the canonical rule
    can't separate them from their variant — the length tiebreak does, since a
    variant is the base id plus a prefix."""
    assert _index(rows)[_norm(expected[1])] == expected


# --------------------------------------------------------------------------- #
# Determinism
# --------------------------------------------------------------------------- #
def test_resolution_is_independent_of_row_order():
    rows = [JADE_GAREN, GAREN, JADE_YI, YI, JADE_WUKONG, WUKONG]
    expected = _index(rows)
    for i in range(len(rows)):
        rotated = rows[i:] + rows[:i]
        assert _index(rotated) == expected


def test_canonicality_prefers_base_then_shorter_id():
    assert _canonicality(GAREN) < _canonicality(JADE_GAREN)
    assert _canonicality(YI) < _canonicality(JADE_YI)


def test_a_future_prefix_resolves_the_same_way():
    """The rule keys on 'id is just the name', not on the literal 'Jade_'."""
    ruby = ("Ruby_Garen", "Garen")
    assert _index([ruby, GAREN])[_norm("Garen")] == GAREN
    assert _index([GAREN, ruby])[_norm("Garen")] == GAREN


# --------------------------------------------------------------------------- #
# Champions without a variant are untouched
# --------------------------------------------------------------------------- #
def test_unduplicated_champions_are_unaffected():
    rows = [("Darius", "Darius"), ("Sett", "Sett"), ("Mordekaiser", "Mordekaiser")]
    idx = _index(rows)
    assert idx[_norm("Darius")] == ("Darius", "Darius")
    assert idx[_norm("Sett")] == ("Sett", "Sett")
    assert idx[_norm("Mordekaiser")] == ("Mordekaiser", "Mordekaiser")
    # Every row survives, and every key still points at one of them. NOT a key
    # count: the index also carries community aliases ("morde"), so counting
    # keys would measure the alias map rather than the variant rule.
    assert {v for v in idx.values()} == set(rows)


def test_a_variant_with_its_own_distinct_name_is_kept():
    """Only same-display-name rows collide; a genuinely distinct champion stays."""
    idx = _index([GAREN, ("Jade_Garen", "Jade Garen")])
    assert idx[_norm("Garen")] == GAREN
    assert idx[_norm("Jade Garen")] == ("Jade_Garen", "Jade Garen")


# --------------------------------------------------------------------------- #
# The ingest-side rule (core.lol_roster), shared with entity linking so the two
# cannot drift apart.
# --------------------------------------------------------------------------- #
from core.lol_roster import canonicality as _c  # noqa: E402
from core.lol_roster import norm_name, select_canonical  # noqa: E402

# Real champions whose id NEVER equals their display name. A literal
# "id != display name" exclusion would delete all twelve from the corpus.
ODD_IDS = [
    ("AurelionSol", "Aurelion Sol"), ("DrMundo", "Dr. Mundo"),
    ("JarvanIV", "Jarvan IV"), ("LeeSin", "Lee Sin"),
    ("MasterYi", "Master Yi"), ("MissFortune", "Miss Fortune"),
    ("MonkeyKing", "Wukong"), ("Nunu", "Nunu & Willump"),
    ("Renata", "Renata Glasc"), ("TahmKench", "Tahm Kench"),
    ("TwistedFate", "Twisted Fate"), ("XinZhao", "Xin Zhao"),
]


@pytest.mark.parametrize("row", ODD_IDS)
def test_real_champions_with_odd_ids_are_never_dropped(row):
    """The regression this rule exists to avoid: these have no twin, so they are
    kept regardless of how little their id resembles their name."""
    kept, dropped = select_canonical([row, ("Darius", "Darius")])
    assert row in kept and dropped == []


@pytest.mark.parametrize("row", ODD_IDS)
def test_odd_ids_survive_even_when_they_do_have_a_variant(row):
    cid, name = row
    variant = (f"Jade_{cid}", name)
    kept, dropped = select_canonical([variant, row])
    assert kept == [row] and dropped == [variant]


def test_select_canonical_drops_only_the_variant():
    rows = [GAREN, JADE_GAREN, ("Darius", "Darius")]
    kept, dropped = select_canonical(rows)
    assert kept == [GAREN, ("Darius", "Darius")]
    assert dropped == [JADE_GAREN]


def test_select_canonical_is_order_independent():
    rows = [JADE_GAREN, GAREN, JADE_YI, YI, ("Sett", "Sett")]
    expected = {r for r in select_canonical(rows)[0]}
    for i in range(len(rows)):
        rotated = rows[i:] + rows[:i]
        assert {r for r in select_canonical(rotated)[0]} == expected


def test_nothing_is_dropped_when_there_are_no_duplicates():
    rows = [("Garen", "Garen"), ("Darius", "Darius"), ("MasterYi", "Master Yi")]
    kept, dropped = select_canonical(rows)
    assert kept == rows and dropped == []


def test_rule_does_not_match_on_the_string_jade():
    """A future roster prefix must be handled without touching this code."""
    ruby = ("Ruby_Sett", "Sett")
    kept, dropped = select_canonical([ruby, ("Sett", "Sett")])
    assert dropped == [ruby]


def test_routing_and_roster_share_one_normalizer():
    """If these diverge, ingest and entity linking disagree about which Garen is
    which — the exact failure this consolidation prevents."""
    assert lol_routing._norm is norm_name
    assert lol_routing._canonicality is _c
