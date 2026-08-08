"""Community shorthand must resolve, without ever outranking a real champion.

Reported from live use: "yi counters", "yi worst matchups" and "yi items" all
refused, while "Master Yi ..." worked. Entity linking only matched display
names, so every shorthand a player actually types fell through to corpus
retrieval and refused a question that clearly named a champion.

The danger in fixing it is the reverse: an alias that shadows a real champion.
"vi", "sett" and "yorick" ARE champions, and a naive prefix rule would hand "vi"
to Viktor or Viego. These tests pin both directions.
"""

from __future__ import annotations

import pytest

from core import lol_aliases, lol_routing
from core.lol_aliases import ALIASES, resolve
from core.lol_roster import norm_name

# A roster shaped like the real one, including the three names that must never
# be aliased and the awkward-id champions the shorthand exists for.
ROSTER = [
    ("MasterYi", "Master Yi"), ("AurelionSol", "Aurelion Sol"),
    ("DrMundo", "Dr. Mundo"), ("JarvanIV", "Jarvan IV"), ("LeeSin", "Lee Sin"),
    ("MissFortune", "Miss Fortune"), ("MonkeyKing", "Wukong"),
    ("Nunu", "Nunu & Willump"), ("Renata", "Renata Glasc"),
    ("TahmKench", "Tahm Kench"), ("TwistedFate", "Twisted Fate"),
    ("XinZhao", "Xin Zhao"), ("Yasuo", "Yasuo"), ("Katarina", "Katarina"),
    ("Caitlyn", "Caitlyn"), ("Mordekaiser", "Mordekaiser"), ("Ezreal", "Ezreal"),
    ("Tristana", "Tristana"), ("Alistar", "Alistar"), ("Blitzcrank", "Blitzcrank"),
    ("Cassiopeia", "Cassiopeia"), ("Evelynn", "Evelynn"), ("Gangplank", "Gangplank"),
    ("Hecarim", "Hecarim"), ("Kassadin", "Kassadin"), ("Khazix", "Kha'Zix"),
    ("Leblanc", "LeBlanc"), ("Malzahar", "Malzahar"), ("Nautilus", "Nautilus"),
    ("Nidalee", "Nidalee"), ("Nocturne", "Nocturne"), ("Orianna", "Orianna"),
    ("Pantheon", "Pantheon"), ("RekSai", "Rek'Sai"), ("Renekton", "Renekton"),
    ("Sejuani", "Sejuani"), ("Seraphine", "Seraphine"), ("Shyvana", "Shyvana"),
    ("Tryndamere", "Tryndamere"), ("Vladimir", "Vladimir"), ("Volibear", "Volibear"),
    ("Warwick", "Warwick"), ("Zilean", "Zilean"), ("KogMaw", "Kog'Maw"),
    ("Chogath", "Cho'Gath"), ("Velkoz", "Vel'Koz"), ("Morgana", "Morgana"),
    ("Nasus", "Nasus"),
    # The three that must never be shadowed by an alias.
    ("Vi", "Vi"), ("Sett", "Sett"), ("Yorick", "Yorick"),
    # Prefix-collision bait: a derived rule would give "kar" to both.
    ("Karma", "Karma"), ("Karthus", "Karthus"),
]


def _index():
    """The champion index as _build_index assembles it: names, then aliases,
    with names winning."""
    champs = {norm_name(name): (cid, name) for cid, name in ROSTER}
    for alias, row in resolve(ROSTER)[0].items():
        champs.setdefault(alias, row)
    return {"champions": champs, "items": {}}


def _link(query):
    return lol_routing.link_entities(query, _index())["champions"]


# --------------------------------------------------------------------------- #
# The reported failures
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("query", ["yi counters", "yi worst matchups", "yi items"])
def test_the_reported_yi_questions_resolve(query):
    assert _link(query) == [("MasterYi", "Master Yi")]


@pytest.mark.parametrize("query,expected", [
    ("yi counters", "counters"),
    ("yi items", "build"),
    ("yi win rate", "champion_stats"),
    ("who counters cait", "counters"),
    ("mf build", "build"),
])
def test_shorthand_reaches_the_right_live_intent(query, expected):
    ents = lol_routing.link_entities(query, _index())
    intent = lol_routing.live_stats_intent(query, ents)
    assert intent is not None and intent["kind"] == expected


# --------------------------------------------------------------------------- #
# Possessives and plurals — the same missing-apostrophe class as full names
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("query,cid", [
    ("yi's items", "MasterYi"),
    ("yis build", "MasterYi"),
    ("yi's counters", "MasterYi"),
    ("cait's range", "Caitlyn"),
    ("caits range", "Caitlyn"),
    ("mf's win rate", "MissFortune"),
    ("mfs build", "MissFortune"),
    ("j4's build", "JarvanIV"),
    ("tf's counters", "TwistedFate"),
    ("vlads matchups", "Vladimir"),
])
def test_possessive_and_plural_forms_resolve(query, cid):
    assert _link(query)[0][0] == cid


# --------------------------------------------------------------------------- #
# Every alias in the map resolves, in the casing a player types
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("alias,cid", sorted(ALIASES.items()))
def test_every_alias_resolves(alias, cid):
    got = _link(f"{alias} counters")
    assert got, f"{alias!r} linked nothing"
    assert got[0][0] == cid


@pytest.mark.parametrize("alias", ["YI", "Yi", "yI", "MF", "J4", "Monkey King"])
def test_aliases_are_case_insensitive(alias):
    assert _link(f"{alias} counters")


# --------------------------------------------------------------------------- #
# Collision guard — the reason this is curated and validated, not derived
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name", ["vi", "sett", "yorick"])
def test_real_champion_names_are_never_shadowed(name):
    """These appear in shorthand lists but are real champions."""
    aliases, _ = resolve(ROSTER)
    assert name not in aliases
    assert _link(f"{name} counters")[0][1].lower() == name


def test_an_alias_colliding_with_a_new_champion_is_dropped():
    """If Riot ships a champion literally called "Yi", the alias must stop
    resolving to Master Yi rather than shadowing the newcomer."""
    aliases, problems = resolve(ROSTER + [("Yi", "Yi")])
    assert "yi" not in aliases
    assert any("collides" in p for p in problems)


def test_no_alias_maps_to_two_champions():
    seen = {}
    for alias, cid in ALIASES.items():
        key = norm_name(alias)
        assert seen.get(key, cid) == cid, f"{key!r} maps to {seen[key]!r} and {cid!r}"
        seen[key] = cid


def test_aliases_meet_the_length_floor():
    for alias in ALIASES:
        assert len(norm_name(alias)) >= lol_aliases.MIN_ALIAS_LEN, alias


def test_unknown_champion_ids_are_reported_not_silently_dropped():
    aliases, problems = resolve([("Yasuo", "Yasuo")])
    assert "yi" not in aliases
    assert any("unknown champion id" in p for p in problems)


def test_the_shipped_map_is_clean_against_a_realistic_roster():
    """Authoring errors fail here rather than in production."""
    _aliases, problems = resolve(ROSTER)
    assert problems == []


def test_war_is_not_an_alias():
    """Dropped deliberately: an ordinary English word, and "ww" covers Warwick."""
    assert "war" not in ALIASES
    assert _link("ww counters")[0][0] == "Warwick"


# --------------------------------------------------------------------------- #
# The twelve awkward-id champions all have a natural short form
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("query,cid", [
    ("yi build", "MasterYi"),
    ("asol build", "AurelionSol"),
    ("mundo build", "DrMundo"),
    ("dr mundo build", "DrMundo"),          # full name still works
    ("jarvan build", "JarvanIV"),
    ("j4 build", "JarvanIV"),
    ("lee build", "LeeSin"),
    ("mf build", "MissFortune"),
    ("wukong build", "MonkeyKing"),          # display name
    ("monkey king build", "MonkeyKing"),     # community name
    ("nunu build", "Nunu"),
    ("renata build", "Renata"),
    ("tahm build", "TahmKench"),
    ("tk build", "TahmKench"),
    ("tf build", "TwistedFate"),
    ("xin build", "XinZhao"),
])
def test_awkward_names_have_a_natural_short_form(query, cid):
    assert _link(query)[0][0] == cid


# --------------------------------------------------------------------------- #
# A real name still beats an alias for the same champion
# --------------------------------------------------------------------------- #
def test_full_names_still_resolve():
    assert _link("master yi counters")[0][0] == "MasterYi"
    assert _link("nunu & willump counters")[0][0] == "Nunu"
    assert _link("tahm kench counters")[0][0] == "TahmKench"


def test_alias_does_not_fire_inside_a_longer_word():
    """Word-boundary matching: "ez" must not hit inside "freeze"."""
    assert not _link("how does freeze work")
    assert not _link("what is a lane")        # "ali"/"lee" must not fire


# --------------------------------------------------------------------------- #
# An alias inside a full name must not link the champion twice.
#
# Caught by the sanity sweep, not by the unit tests: "yi" matches inside
# "master yi", so the champion linked twice, and live_stats_intent reads
# len(champs) >= 2 as "two champions named" -- turning "master yi counters"
# into a Master Yi vs Master Yi matchup. Same shape for "tahm kench",
# "lee sin", "nunu & willump", "dr mundo", "jarvan iv", "xin zhao".
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("query,cid", [
    ("master yi counters", "MasterYi"),
    ("tahm kench counters", "TahmKench"),
    ("lee sin counters", "LeeSin"),
    ("nunu & willump counters", "Nunu"),
    ("dr mundo counters", "DrMundo"),
    ("jarvan iv counters", "JarvanIV"),
    ("xin zhao counters", "XinZhao"),
    ("renata glasc counters", "Renata"),
])
def test_a_full_name_containing_an_alias_links_once(query, cid):
    got = _link(query)
    assert got == [(cid, got[0][1])], f"{query!r} linked {got}"


@pytest.mark.parametrize("query", [
    "master yi counters", "tahm kench counters", "lee sin counters",
    "dr mundo counters", "jarvan iv counters",
])
def test_single_champion_questions_are_counters_not_self_matchups(query):
    ents = lol_routing.link_entities(query, _index())
    intent = lol_routing.live_stats_intent(query, ents)
    assert intent["kind"] == "counters", intent
    assert "b" not in intent


@pytest.mark.parametrize("query,ids", [
    ("master yi vs yasuo", {"MasterYi", "Yasuo"}),
    ("yi vs yasuo", {"MasterYi", "Yasuo"}),          # both sides shorthand
    ("yi vs katarina", {"MasterYi", "Katarina"}),
])
def test_two_real_champions_still_read_as_a_matchup(query, ids):
    """Dedup must not break the genuine two-champion case."""
    ents = lol_routing.link_entities(query, _index())
    assert {cid for cid, _n in ents["champions"]} == ids
    assert lol_routing.live_stats_intent(query, ents)["kind"] == "matchup"
