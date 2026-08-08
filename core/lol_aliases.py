"""Community shorthand for champion names.

Players type "yi counters", not "Master Yi counters". Entity linking matched
only display names, so every shorthand fell through to corpus retrieval and
refused — the question was understood by nobody despite naming a champion.

The map is CURATED, not derived. `j4`, `tf`, `mf`, `ww`, `tk` and `lb` follow
from no rule about the id or the name; they are conventions. And derivation is
actively unsafe: a "first three letters" rule collides `kar` between Karma and
Karthus, and would hand `vi` — a real champion — to Viktor or Viego.

Curation alone is unsafe too, because the roster moves underneath it. So the
safety properties are enforced in code (`resolve`) and pinned by tests against
the live roster: an alias that ever collides with a real champion name is
DROPPED, not silently preferred. If Riot ships a champion called "Yi", the alias
stops resolving to Master Yi rather than shadowing the new champion.
"""

from __future__ import annotations

from core.lol_roster import norm_name

# alias -> Data Dragon champion id.
#
# Multi-word entries are here because the display name is awkward to type:
# "Nunu & Willump" (ampersand), "Wukong" (whose id and community name is Monkey
# King), "Aurelion Sol", "Renata Glasc", "Jarvan IV".
#
# NOT included, deliberately: "war" for Warwick. It is an ordinary English word,
# and "ww" plus the full name already cover the champion. "eve", "lee" and "ali"
# are kept — also words, but overwhelmingly champion references in a League
# corpus, and a champion entity alone cannot trigger the live path without an
# intent word alongside it.
ALIASES: dict[str, str] = {
    # --- the twelve champions whose id never matches their display name ---
    "yi": "MasterYi",
    "asol": "AurelionSol",
    "mundo": "DrMundo",
    "jarvan": "JarvanIV", "j4": "JarvanIV",
    "lee": "LeeSin",
    "mf": "MissFortune",
    "monkey king": "MonkeyKing", "wu": "MonkeyKing",
    "nunu": "Nunu",
    "renata": "Renata",
    "tahm": "TahmKench", "tk": "TahmKench",
    "tf": "TwistedFate",
    "xin": "XinZhao",
    # --- everyday shorthand ---
    "yas": "Yasuo",
    "kat": "Katarina", "kata": "Katarina",
    "cait": "Caitlyn",
    "morde": "Mordekaiser",
    "ez": "Ezreal",
    "trist": "Tristana",
    "ali": "Alistar",
    "blitz": "Blitzcrank",
    "cass": "Cassiopeia",
    "eve": "Evelynn",
    "gp": "Gangplank",
    "heca": "Hecarim",
    "kass": "Kassadin",
    "kha": "Khazix",
    "lb": "Leblanc",
    "malz": "Malzahar",
    "naut": "Nautilus",
    "nid": "Nidalee",
    "noc": "Nocturne",
    "ori": "Orianna",
    "panth": "Pantheon",
    "rek": "RekSai",
    "renek": "Renekton",
    "seju": "Sejuani", "sej": "Sejuani",
    "sera": "Seraphine",
    "shyv": "Shyvana",
    "trynd": "Tryndamere",
    "vlad": "Vladimir",
    "voli": "Volibear",
    "ww": "Warwick",
    "zil": "Zilean",
    "kog": "KogMaw",
    "cho": "Chogath",
    "vel": "Velkoz",
    "morg": "Morgana",
    "nas": "Nasus",
}

MIN_ALIAS_LEN = 2


def resolve(roster) -> tuple[dict[str, tuple[str, str]], list[str]]:
    """Map normalized alias -> (champion_id, display_name) for THIS roster.

    `roster` is the [(id, name)] the corpus actually holds, so aliases inherit
    the classic-mode variant filtering for free and a champion missing from the
    corpus simply has no shorthand.

    Returns (aliases, problems). Problems are reported, never raised: a bad
    alias must not take the API down. The test suite asserts the list is empty
    against the live roster, so authoring mistakes fail in CI instead.
    """
    by_id = {cid: name for cid, name in roster}
    real_names = {norm_name(name) for _cid, name in roster}

    out: dict[str, tuple[str, str]] = {}
    problems: list[str] = []
    for raw, cid in ALIASES.items():
        alias = norm_name(raw)
        if len(alias) < MIN_ALIAS_LEN:
            problems.append(f"{raw!r}: shorter than {MIN_ALIAS_LEN} characters")
            continue
        if alias in real_names:
            # The decisive guard. "vi", "sett" and "yorick" are real champions;
            # an alias must never outrank one.
            problems.append(f"{raw!r}: collides with a real champion name")
            continue
        if cid not in by_id:
            problems.append(f"{raw!r}: unknown champion id {cid!r}")
            continue
        if alias in out and out[alias][0] != cid:
            problems.append(f"{raw!r}: already maps to {out[alias][0]!r}")
            continue
        out[alias] = (cid, by_id[cid])
    return out, problems
