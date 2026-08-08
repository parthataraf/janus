"""Derive LoL numeric / multi-row labels from the ingested Data Dragon tables.

This is the single source of truth for structured-path expected answers: the
test set stores an `answer` LOOKUP SPEC, and these functions compute the value(s)
from the `lol_*` tables for the current patch. Used three ways:
  - drafting  — fill the cached `value` / `items` when writing the test set;
  - scoring   — (the cached value is what evaluate.py compares the model to);
  - refresh   — re-derive on a patch bump and diff vs the cached value.

Lookup specs:
  ability : {"lookup":"ability","champion":"Yasuo","slot":"Q","field":"cooldown","rank":3}
  item    : {"lookup":"item","item":"Infinity Edge","field":"gold_total"}
            {"lookup":"item","item":"Infinity Edge","field":"FlatPhysicalDamageMod"}
  champion: {"lookup":"champion","champion":"Yasuo","field":"hp"}   (base stat)
  items_with (multi-row): {"lookup":"items_with","phrase":"armor penetration"}
"""

from __future__ import annotations

from core import store


def latest_patch() -> str | None:
    patches = store.lol_patches()
    return patches[-1] if patches else None


def derive_numeric(answer: dict, patch: str):
    """Return the numeric value for a single-value spec, or None if it can no
    longer be derived (entity removed/renamed)."""
    kind = answer.get("lookup")
    field = answer.get("field")

    if kind == "ability":
        cid = store.lol_champion_id(answer["champion"], patch)
        if not cid:
            return None
        ab = store.lol_ability(cid, answer["slot"], patch)
        if not ab or ab.get(field) is None:
            return None
        arr = ab[field]
        rank = answer.get("rank")
        if isinstance(arr, list):
            if rank is not None:
                return arr[rank - 1] if 1 <= rank <= len(arr) else None
            return arr
        return arr

    if kind == "item":
        it = store.lol_item(answer["item"], patch)
        if not it:
            return None
        if field == "gold_total":
            return it.get("gold_total")
        return (it.get("stats") or {}).get(field)

    if kind == "champion":
        cid = store.lol_champion_id(answer["champion"], patch)
        ch = store.lol_champion(cid, patch) if cid else None
        if not ch:
            return None
        return (ch.get("stats") or {}).get(field)

    return None


def derive_multi_row(answer: dict, patch: str) -> list[str] | None:
    """Return the sorted set of item names for an items_with spec."""
    if answer.get("lookup") == "items_with":
        items = store.lol_items_by_keyword(answer["phrase"], patch)
        # Distinct names — item.json can hold two entries with the same name.
        return sorted({i["name"] for i in items})
    return None


def derive(answer: dict, patch: str):
    """Dispatch: multi-row specs return a list, single-value specs a scalar."""
    if answer.get("lookup") == "items_with":
        return derive_multi_row(answer, patch)
    return derive_numeric(answer, patch)
