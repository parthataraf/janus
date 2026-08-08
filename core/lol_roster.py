"""Which champion rows are the real Summoner's Rift roster.

Patch 16.15 shipped a parallel `Jade_*` roster for the new classic/retro game
mode: the same champions with different balance data for a different ruleset.
Data Dragon returns both under the SAME display name — `Garen` and `Jade_Garen`
are both named "Garen" — which grew the champion list from 173 to 233.

That is a problem in two places, so the rule lives here and both import it:

  * INGEST — a variant's stats and ability text would enter the structured tables
    and the prose corpus as a second, contradictory "Garen", competing with the
    real one in retrieval.
  * ENTITY LINKING — the name->champion index is keyed on display name, so the
    two collide and one silently wins.

The rule is "not the canonical row for a shared display name", NOT "id differs
from display name". That distinction matters: `MasterYi`, `MonkeyKing`, `LeeSin`,
`XinZhao`, `Nunu`, `TahmKench` and six others never equal their display names,
and a literal id-vs-name test drops twelve real champions along with the sixty
variants. A row is only ever discarded when a BETTER row exists for the same
name, so a champion with no twin is always kept whatever its id looks like.

Nothing here matches on the string "Jade_", so a future `Ruby_*` roster is
handled without a code change.
"""

from __future__ import annotations

import re


def norm_name(s: str) -> str:
    """Lowercase, drop possessives/apostrophes, punctuation → spaces."""
    s = s.lower()
    s = re.sub(r"'s\b", "", s)      # "yasuo's" -> "yasuo"
    s = s.replace("'", "")          # "kai'sa" -> "kaisa"
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def canonicality(row: tuple[str, str]) -> tuple:
    """Sort key ranking rows that share a display name; lowest wins.

    The base champion's id is just its name (`Garen`), while a variant carries a
    prefix (`Jade_Garen`). Where neither id equals the name (`MasterYi` vs
    `Jade_MasterYi`) the length tiebreak decides, which is sound because a
    variant is the base id PLUS a prefix. Id last, so the result never depends
    on input order.
    """
    cid, name = row
    return (0 if norm_name(cid) == norm_name(name) else 1, len(cid), cid)


def select_canonical(rows):
    """Split (id, name) rows into (kept, dropped).

    `kept` holds one row per display name — the canonical champion. `dropped`
    holds the variants. Order-independent: shuffling `rows` yields the same split.
    """
    best: dict[str, tuple[str, str]] = {}
    for row in rows:
        key = norm_name(row[1])
        current = best.get(key)
        if current is None or canonicality(row) < canonicality(current):
            best[key] = row
    keep = set(best.values())
    kept = [r for r in rows if r in keep]
    dropped = [r for r in rows if r not in keep]
    return kept, dropped
