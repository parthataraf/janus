"""LoL entity linking + structured/prose routing (Phase 4b).

The LoL face has two kinds of question:
  - "What does Yasuo's passive do?" — mechanics prose. Answered by the SAME
    hybrid retrieval as the fastapi corpus, over the embedded lol chunks.
  - "Cooldown of Yasuo's Q at rank 3?" / "Which items give armor penetration?"
    — exact numbers. Prose retrieval is the wrong tool; these are looked up in
    the structured lol_* tables and formatted into a passage the model cites.

This module does the entity linking and the structured lookups (returning plain
passage dicts); `retrieval.route()` wraps them as Candidates and merges them with
prose retrieval, so a single generation call can see both. The pure helpers
(`link_entities`, `detect_slot`, `detect_rank`, `has_numeric_intent`) take their
inputs directly and are unit-tested without a DB. The entity index is built from
the DB and cached per patch; `link_entities` itself is corpus-agnostic (Phase 4e
can feed it a Palworld index).
"""

from __future__ import annotations

import json
import logging
import re
from functools import lru_cache

from core import followup, lol_aliases, store
from core.lol_roster import canonicality, norm_name, select_canonical

logger = logging.getLogger("docpilot")

# Data Dragon base (for building human-checkable source_urls on structured hits).
_DDRAGON = "https://ddragon.leagueoflegends.com/cdn/{patch}/data/en_US/champion/{cid}.json"

_NUMERIC_WORDS = (
    "cooldown", "cd", "cost", "mana", "energy", "damage", "dmg", "range", "gold",
    "price", "cheap", "how much", "how long", "how far", "how many", "scaling",
    "base", "stat", "stats", "haste", "penetration", "lethality", "value",
)
# Multi-word stat phrases for the "which items give X" multi-row lookup.
_STAT_PHRASES = (
    "armor penetration", "magic penetration", "ability power", "attack damage",
    "attack speed", "critical strike", "movement speed", "move speed",
    "ability haste", "cooldown reduction", "life steal", "lifesteal", "omnivamp",
    "lethality", "tenacity", "mana regen", "health regen",
)


# Normalization and the variant rule live in core.lol_roster so ingest and entity
# linking cannot drift apart — one definition of "which Garen is the real Garen".
_norm = norm_name


def link_entities(query: str, index: dict) -> dict:
    """Find champion/item mentions in `query`. `index` is {champions: {norm: (id,
    name)}, items: {norm: name}} — built by `_build_index` (or any corpus's).

    Matches the name OR its apostrophe-less possessive (name + "s"), so gamer
    spellings like "master yis" -> Master Yi and "jinxs" -> Jinx link even though
    `_norm`'s `'s` stripping only fires when the apostrophe is present."""
    padded = f" {_norm(query)} "

    def hit(norm: str) -> bool:
        return f" {norm} " in padded or f" {norm}s " in padded

    # One champion, listed once, however many keys matched. "master yi counters"
    # matches BOTH the display name and the "yi" alias, and a duplicate is not
    # cosmetic: live_stats_intent reads `len(champs) >= 2` as "two champions
    # named", so the same champion twice became a Master Yi vs Master Yi matchup.
    champions, seen = [], set()
    for norm, val in index["champions"].items():
        if hit(norm) and val[0] not in seen:
            seen.add(val[0])
            champions.append(val)
    # Items are NOT deduplicated: overlapping item names ("Boots" inside
    # "Berserker's Boots") are genuinely two different items.
    items = [name for norm, name in index["items"].items() if hit(norm)]
    return {"champions": champions, "items": items}


def detect_slot(query: str) -> str | None:
    """Which ability the query is about: P (passive), Q/W/E/R, or ult→R."""
    q = query.lower()
    if "passive" in q:
        return "P"
    if re.search(r"\bult(imate|i)?\b", q):
        return "R"
    m = re.search(r"\b([QWER])\b", query)  # single letters: require uppercase
    return m.group(1) if m else None


def detect_rank(query: str) -> int | None:
    m = re.search(r"\b(?:rank|level|lvl)\s*(\d+)", query, re.IGNORECASE)
    return int(m.group(1)) if m else None


def has_numeric_intent(query: str) -> bool:
    q = query.lower()
    if detect_rank(query) is not None:
        return True
    return any(w in q for w in _NUMERIC_WORDS)


def _stat_phrase(query: str) -> str | None:
    q = query.lower()
    for phrase in sorted(_STAT_PHRASES, key=len, reverse=True):
        if phrase in q:
            return phrase
    return None


# Champion BASE-stat phrases -> lol_champions.stats field. Ordered so multi-word
# and regen phrases win over their bare substrings (e.g. "attack range" before
# "range", "health regen" before "health").
_BASE_STAT_MAP = [
    ("movement speed", "movespeed"), ("move speed", "movespeed"), ("movespeed", "movespeed"),
    ("attack range", "attackrange"), ("attack damage", "attackdamage"),
    ("attack speed", "attackspeed"), ("magic resistance", "spellblock"),
    ("magic resist", "spellblock"), ("health regen", "hpregen"), ("mana regen", "mpregen"),
    ("armor", "armor"), ("health", "hp"), ("hp", "hp"), ("mana", "mp"),
    ("range", "attackrange"),
]


def base_stat_field(query: str) -> str | None:
    """The lol_champions.stats field a base-stat question is asking about."""
    q = query.lower()
    for phrase, field in _BASE_STAT_MAP:
        if phrase in q:
            return field
    return None


# General "give me the whole stat line" phrasings. These carry no single field,
# so they answer with the full base stat block instead of one number. Checked
# only AFTER base_stat_field: "master yi armor" stays a one-field answer, while
# "master yi base stats" gets the table.
_FULL_STAT_PHRASES = (
    "base stats", "base stat", "stat line", "statline", "stat block",
    "stat sheet", "statsheet", "all stats", "full stats", "stats",
    "stat", "attributes", "base numbers",
)


def wants_full_stat_line(query: str) -> bool:
    """Is this a general 'what are X's stats' question rather than one field?"""
    q = f" {_norm(query)} "
    return any(f" {p} " in q for p in _FULL_STAT_PHRASES)


def decide_branch(query: str, ents: dict, slot: str | None) -> str | None:
    """Pick the structured branch (or None for pure prose) from the linked
    entities + slot. Pure — no DB — so routing decisions are unit-testable.
      ability      : champion + Q/W/E/R + numeric intent
      champion_stat: champion + no slot + a base-stat phrase (one field), or a
                     general stats phrase (the whole base stat line)
      items_with   : no champion, no item, a stat phrase, "which"/"item"
      item         : a named item + numeric intent
    """
    q = query.lower()
    if ents["champions"] and slot in ("Q", "W", "E", "R") and has_numeric_intent(query):
        return "ability"
    if ents["champions"] and not slot and (base_stat_field(query) or wants_full_stat_line(query)):
        return "champion_stat"
    if (not ents["items"] and not ents["champions"] and _stat_phrase(query)
            and ("item" in q or "which" in q)):
        return "items_with"
    if ents["items"] and has_numeric_intent(query):
        return "item"
    return None


# --- Live-stats (OP.GG meta questions) intent detection (Phase 4h) ---------- #
# These route to the LIVE OP.GG path (win rates / counters / popularity), which
# is answered by a live per-question MCP call, never from our tables. Kept as a
# pure classifier so it's unit-tested without a DB or network.
_MATCHUP_WORDS = (
    "counter", "counters", "countered", "beat", "beats", "who beats", "who wins",
    " vs ", " vs.", " versus ", "matchup", "match up", "good into", "bad into",
    "good against", "bad against", "best against", "wins against", "lose to",
    "loses to", "struggle against", "strong against", "weak against",
)
# Phrasings where the named champion is the STRONG side (it beats them) — these
# resolve to the champion's strong_counters instead of weak_counters.
_STRONG_WORDS = (
    "good against", "strong against", "good into", "best against", "wins against",
)
_POPULARITY_WORDS = (
    "win rate", "winrate", "win-rate", " wr ", "win%", "pick rate", "ban rate",
    "popular", "popularity", "tier", "meta", "good right now", "how strong",
    "how good", "for beginners", "beginner", "beginner-friendly", "worth playing",
    "is it good", "strong right now", "best pick",
)
# Role/lane questions ("what role is X played in", "X role", "what lane does X
# play") -> champion_stats path, answered from OP.GG positions[] (the same source
# used for role inference). Kept separate so the answer is role-shaped.
_ROLE_WORDS = (
    "role", "what lane", "which lane", "what position", "which position",
    "lane does", "position does", "played in", "where do you play",
    "where does he play", "where is he played", "primary role", "main role",
)
# Item-build / rune / skill-order questions ("master yi items", "what should I
# build on Zed") -> the live build path. Our own lol_items table holds item
# PROPERTIES (gold, stats), never build RECOMMENDATIONS, so this can only be
# answered live from OP.GG's aggregate — or honestly refused.
_BUILD_WORDS = (
    "items", "item", "build", "builds", "building", "buy", "buys",
    "build path", "core items", "best items", "item build", "first item",
    "runes", "rune", "rune page", "skill order", "skill max", "max order",
    "what to get", "what do i get",
)
# "build up" is prose ("how does Yasuo build up his shield?"), not a build
# question. Guard it so the verb sense doesn't hijack the live path.
_BUILD_NOT = (" build up ", " builds up ", " building up ")


def wants_build(query: str) -> bool:
    """Is this asking what to buy/build/rune, rather than a mechanics question?"""
    q = f" {_norm(query)} "
    if any(n in f" {query.lower()} " for n in _BUILD_NOT):
        return False
    return any(f" {w} " in q for w in _BUILD_WORDS)


# Explicit lane in the query -> override the most-played role for the live lookup.
_LANE_TERMS = {
    "top": "top", "mid": "mid", "middle": "mid", "jungle": "jungle", "jg": "jungle",
    "jungler": "jungle", "adc": "adc", "bot": "adc", "bottom": "adc",
    "botlane": "adc", "support": "support", "supp": "support", "sup": "support",
}


def detect_lane(query: str) -> str | None:
    """A lane explicitly named in the query (top/mid/jungle/adc/support), or None.
    Used to OVERRIDE OP.GG's most-played role for the live lookup."""
    q = f" {query.lower()} "
    for term, lane in _LANE_TERMS.items():
        if f" {term} " in q:
            return lane
    return None


def live_stats_intent(query: str, ents: dict) -> dict | None:
    """Classify a query as a live OP.GG meta question, or None to route normally.
      matchup        : two champions, or one champion + a "vs/counter" word
      counters       : one champion + a counter/beats word
      build          : one champion + an item/build/rune/skill-order word
      champion_stats : one champion + a popularity/tier/beginner word
    Returns {kind, a:(cid,name)[, b:(cid,name)]} or None."""
    q = f" {query.lower()} "
    champs = ents.get("champions") or []
    lane = detect_lane(query)  # explicit lane override, or None (-> most-played)
    matchup = any(w in q for w in _MATCHUP_WORDS)
    if len(champs) >= 2 and matchup:
        return {"kind": "matchup", "a": champs[0], "b": champs[1], "position": lane}
    if len(champs) == 1 and matchup:
        direction = "strong" if any(w in q for w in _STRONG_WORDS) else "weak"
        return {"kind": "counters", "a": champs[0], "direction": direction, "position": lane}
    # Build is checked before the popularity/role catch-all: "master yi meta
    # build" is a build question, not a tier question.
    if len(champs) >= 1 and wants_build(query):
        return {"kind": "build", "a": champs[0], "position": lane}
    role = any(w in q for w in _ROLE_WORDS)
    if len(champs) >= 1 and (role or any(w in q for w in _POPULARITY_WORDS)):
        return {"kind": "champion_stats", "a": champs[0], "position": lane, "role_query": role}
    return None


def detect_live_intent(query: str, patch: str, frame=None,
                       corpus: str = "lol") -> dict | None:
    """DB-backed wrapper: link entities for `patch`, then classify. Returns the
    same shape as `live_stats_intent` (or None).

    `frame` is the previous answered turn (see core/followup.py). It is consulted
    ONLY when the query classifies to nothing on its own, so conversational
    carryover can rescue a question that would have refused but can never
    override one that routed successfully.
    """
    ents = link_entities(query, _build_index(patch))
    intent = live_stats_intent(query, ents)
    if intent is not None:
        return intent
    if frame:
        return followup.resolve(query, ents, frame, live_stats_intent, corpus, patch)
    return None


def champions_in(text: str, patch: str) -> list[str]:
    """Display names of champions mentioned in `text` — used to record which
    champions an answer actually named, so a later "you told me Yone..." is
    recognised as a back-reference rather than a new subject."""
    return [name for _cid, name in link_entities(text, _build_index(patch))["champions"]]


_canonicality = canonicality      # re-exported: tests and callers use one name


@lru_cache(maxsize=4)
def _build_index(patch: str) -> dict:
    names = store.lol_entity_names(patch)
    # Champions are keyed by normalized display name, which is NOT unique. A
    # plain dict comprehension silently kept whichever row the database returned
    # last; on 16.15.1 that resolved "garen" to `Jade_Garen`, whose OP.GG entry
    # exists but carries no stats, so every live Garen question degraded while
    # the real Garen sat right there. Ingest now filters variants out, but this
    # stays as the second line of defence: an older corpus, a partial re-ingest,
    # or a future variant that slips through must not reintroduce the ambiguity.
    kept, _dropped = select_canonical(names["champions"])
    champions = {_norm(name): (cid, name) for cid, name in kept}

    # Community shorthand ("yi", "j4", "mf"), added AFTER the real names and
    # without overwriting them, so a display name always outranks an alias.
    # Aliases are built from `kept`, so they inherit the variant filtering.
    alias_map, problems = lol_aliases.resolve(kept)
    for alias, row in alias_map.items():
        champions.setdefault(alias, row)
    if problems:
        logger.warning(json.dumps({"event": "alias_problems", "problems": problems[:10]}))

    items = {_norm(name): name for name in names["items"]}
    return {"champions": champions, "items": items}


def _burn(arr) -> str:
    return "/".join(str(x) for x in arr) if arr else "?"


def _format_ability(cname, cid, slot, ab, rank, patch) -> dict:
    url = _DDRAGON.format(patch=patch, cid=cid)
    cd, cost, rng = ab.get("cooldown"), ab.get("cost"), ab.get("range")
    lines = [
        f"{cname} — {ab['name']} ({slot}) [structured stats, patch {patch}].",
        f"Cooldown per rank: {_burn(cd)} s.",
        f"Cost per rank: {_burn(cost)}.",
        f"Range per rank: {_burn(rng)}.",
    ]
    if rank and cd and 1 <= rank <= len(cd):
        lines.append(f"At rank {rank}: cooldown {cd[rank-1]} s, "
                     f"cost {cost[rank-1] if cost else '?'}, "
                     f"range {rng[rank-1] if rng else '?'}.")
    if ab.get("description"):
        lines.append(ab["description"])
    return {"content": " ".join(lines),
            "heading_path": f"{cname} > {ab['name']} ({slot}) — stats",
            "source_url": url}


def _format_item(it) -> dict:
    stats = ", ".join(f"{k}={v}" for k, v in (it.get("stats") or {}).items())
    return {"content": f"{it['name']} — {it.get('gold_total')} gold. "
                       f"{it.get('plaintext') or ''}. Stats: {stats or 'n/a'}. "
                       f"{it.get('description') or ''}",
            "heading_path": f"Items > {it['name']} — stats",
            "source_url": ""}


# "18% Armor Penetration", "18 Lethality", "35 Attack Damage" — Data Dragon puts
# the number before the stat name in the description's leading stat block. The
# values are only there; the `stats` JSONB holds legacy mods and carries no
# penetration field at all.
def _stat_value(description: str, phrase: str) -> str | None:
    """The item's value FOR THIS STAT, e.g. "18% armor penetration"."""
    if not description:
        return None
    for term in _STAT_SYNONYMS.get(phrase, (phrase,)):
        m = re.search(rf"(\d+(?:\.\d+)?\s*%?)\s*{re.escape(term)}\b",
                      description, re.IGNORECASE)
        if m:
            return f"{m.group(1).replace(' ', '')} {term}"
    return None


# A tag groups stats a player treats as one question; the VALUE still has to be
# read off whichever word the item actually uses.
# Only the BROAD phrase takes the narrow one as a synonym. A lethality item's
# penetration value IS its lethality number, but a percentage-penetration item
# has no lethality value to report.
_STAT_SYNONYMS = {
    "armor penetration": ("armor penetration", "lethality"),
}


def _format_item_list(phrase, result, patch) -> dict:
    items, total = result["items"], result["total"]
    parts = []
    for i in items:
        gold = i.get("gold_total")
        val = _stat_value(i.get("description") or "", phrase)
        # Some items carry the tag through a passive rather than a stat line —
        # Black Cleaver shreds armor, it does not list a penetration number. Fall
        # back to Riot's own one-line summary so the item still says why it is
        # here; never fabricate a value it does not state.
        if not val:
            val = (i.get("plaintext") or "").strip() or None
        detail = ", ".join(x for x in (f"{gold}g" if gold else None, val) if x)
        parts.append(f"{i['name']} ({detail})" if detail else i["name"])
    rows = "; ".join(parts)
    # The armor-penetration set reports two units — "35% armor penetration" on
    # some items and "22 lethality" on others — and without this the generator
    # reads the lethality rows as a different stat and silently drops them,
    # answering with 5 of 23. Riot files both under one tag; say so in the
    # passage so the grouping is grounded rather than assumed by the model.
    grouping = ""
    if _STAT_SYNONYMS.get(phrase) and any("lethality" in (p or "") for p in parts):
        grouping = (" Riot groups all of these under one armor-penetration tag: "
                    "a percentage value is percent armor penetration and a "
                    "lethality value is flat armor penetration. Every item listed "
                    "here provides armor penetration.")
    # Never present a cut list as the whole set — the generator is told to
    # enumerate every member, so a silent truncation would read as completeness.
    note = (f" Showing the {len(items)} most expensive of {total} items with "
            f"{phrase}." if total > len(items) else
            f" That is all {total} items with {phrase} at this patch.")
    note += grouping
    return {"content": f"Items with {phrase} (patch {patch}): {rows}.{note}",
            "heading_path": f"Items > with {phrase}",
            "source_url": ""}


_STAT_LABEL = {
    "movespeed": "movement speed", "attackrange": "attack range",
    "attackdamage": "attack damage", "armor": "armor", "spellblock": "magic resist",
    "hp": "health", "mp": "mana", "attackspeed": "attack speed",
    "hpregen": "health regen", "mpregen": "mana regen", "crit": "critical strike",
}


def _format_champion_stat(cname, cid, field, stats, patch) -> dict:
    url = _DDRAGON.format(patch=patch, cid=cid)
    label = _STAT_LABEL.get(field, field)
    extras = ", ".join(
        f"{_STAT_LABEL.get(k, k)} {stats[k]}"
        for k in ("hp", "armor", "attackdamage", "movespeed")
        if k in stats and k != field
    )
    return {"content": f"{cname} — base {label}: {stats.get(field)} (patch {patch}) "
                       f"[structured base stat]. Other base stats: {extras}.",
            "heading_path": f"{cname} > base stats — {label}",
            "source_url": url}


# Order the full stat line the way a champion page reads: durability, then
# offence, then utility. (key, label, per-level key or None).
_STAT_LINE = [
    ("hp", "health", "hpperlevel"),
    ("hpregen", "health regen", "hpregenperlevel"),
    ("mp", "resource", "mpperlevel"),
    ("mpregen", "resource regen", "mpregenperlevel"),
    ("armor", "armor", "armorperlevel"),
    ("spellblock", "magic resist", "spellblockperlevel"),
    ("attackdamage", "attack damage", "attackdamageperlevel"),
    ("attackspeed", "attack speed", "attackspeedperlevel"),
    ("attackrange", "attack range", None),
    ("movespeed", "movement speed", None),
]


def _format_champion_stat_line(cname, cid, ch, patch) -> dict:
    """The champion's FULL base stat line, for a general 'stats' question.

    Per-level growth is included where Data Dragon has it, because "base stats"
    without growth is only half the answer. Manaless champions (Energy, Rage,
    None) are labelled with their real resource rather than being shown 0 mana.
    """
    stats = ch.get("stats") or {}
    # Data Dragon writes the LITERAL STRING "None" as the resource for champions
    # that have none (Garen, Katarina, Riven, Viego, Zac, Dr. Mundo) and leaves
    # it empty for a few more (Bel'Veth). Both mean manaless.
    partype = (ch.get("partype") or "").strip()
    manaless = partype.lower() in ("", "none", "manaless")

    parts = []
    for key, label, per in _STAT_LINE:
        if key not in stats or stats[key] is None:
            continue
        if key in ("mp", "mpregen"):
            if manaless or not stats.get(key):
                continue
            label = label.replace("resource", partype.lower())
        val = stats[key]
        growth = stats.get(per) if per else None
        parts.append(f"{label} {val}" + (f" (+{growth}/lvl)" if growth else ""))

    body = "; ".join(parts)
    # Never render the bare word "None" as a value. Echoing Riot's string gave
    # "Resource: None.", which reads as a missing field rather than as the fact
    # that the champion has no resource — the same ambiguity as the live card's
    # "win None%". State the fact in words instead.
    resource_note = (" No mana or secondary resource."
                     if manaless else f" Resource: {partype}.")
    return {
        "content": f"{cname} — base stats at level 1 (patch {patch}) "
                   f"[structured base stats]. {body}.{resource_note}",
        "heading_path": f"{cname} > base stats",
        "source_url": _DDRAGON.format(patch=patch, cid=cid),
    }


def analyze(query: str, patch: str) -> dict:
    """Entity-link and, if the question is numeric, do the structured lookup.
    Returns {passages: [dict], structured: bool, entities, slot, rank, numeric}."""
    index = _build_index(patch)
    ents = link_entities(query, index)
    slot = detect_slot(query)
    rank = detect_rank(query)
    branch = decide_branch(query, ents, slot)
    passages: list[dict] = []

    if branch == "ability":
        cid, cname = ents["champions"][0]
        ab = store.lol_ability(cid, slot, patch)
        if ab:
            passages.append(_format_ability(cname, cid, slot, ab, rank, patch))
    elif branch == "champion_stat":
        cid, cname = ents["champions"][0]
        field = base_stat_field(query)
        ch = store.lol_champion(cid, patch)
        if ch and field and (ch.get("stats") or {}).get(field) is not None:
            # A named stat wins over a general "stats" mention, so
            # "master yi armor stats" still answers with just armor.
            passages.append(_format_champion_stat(cname, cid, field, ch["stats"], patch))
        elif ch and (ch.get("stats") or {}):
            passages.append(_format_champion_stat_line(cname, cid, ch, patch))
    elif branch == "items_with":
        phrase = _stat_phrase(query)
        result = store.lol_items_by_keyword(phrase, patch)
        if result["items"]:
            passages.append(_format_item_list(phrase, result, patch))
    elif branch == "item":
        it = store.lol_item(ents["items"][0], patch)
        if it:
            passages.append(_format_item(it))

    return {"passages": passages, "structured": bool(passages), "branch": branch,
            "entities": ents, "slot": slot, "rank": rank,
            "numeric": has_numeric_intent(query)}
