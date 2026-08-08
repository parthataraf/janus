"""League of Legends Data Dragon ingestion (Phase 4a).

Data Dragon is Riot's official static-data CDN — versioned per patch, free to
use. We fetch a pinned patch and write TWO ways, mirroring the fastapi face's
prose path but adding a structured one:

  1. Structured tables (`lol_champions`, `lol_abilities`, `lol_items` in
     core.store) — the NUMERIC source of truth: per-rank cooldowns/costs, item
     gold and stats. Phase 4b's routing reads these for exact-number questions.
  2. Prose chunks (corpus 'lol', doc_version = patch) — champion ability /
     passive / item / rune / summoner-spell descriptions with Data Dragon's
     HTML-ish markup stripped, embedded like any other corpus so conceptual
     questions work through the same retrieval pipeline.

`load_chunks()` performs the structured writes as a side effect (idempotent per
patch) and RETURNS the prose chunks for run_ingest to embed + store — so the LoL
corpus flows through the exact same embed/store path as fastapi.

Data source is Data Dragon only (Riot-official, licensing-clean). The richer but
license-encumbered community wikis are deliberately out of scope here: ingesting
one would require passing a hard licensing gate (permissive content license we
can actually satisfy, plus robots.txt clearance) first.
"""

from __future__ import annotations

import html
import re

import httpx

from core import store
from core.lol_roster import select_canonical

BASE = "https://ddragon.leagueoflegends.com"
LOCALE = "en_US"
# Only real, purchasable Summoner's Rift items — skip consumables-less junk,
# map-specific and untradeable entries that would just be corpus noise.
SR_MAP = "11"

_TIMEOUT = httpx.Timeout(30.0)


def latest_patch() -> str:
    """The newest Data Dragon patch id, e.g. '15.14.1'."""
    return _get(f"{BASE}/api/versions.json")[0]


def _get(url: str):
    resp = httpx.get(url, timeout=_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


# Data Dragon descriptions carry HTML-ish tags (<br>, <b>, <physicalDamage>,
# <scaleAP>, …) and tooltips carry {{ eN }} placeholders. Strip all of it to
# readable prose.
_PLACEHOLDER_RE = re.compile(r"\{\{.*?\}\}")
_BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _strip_markup(text: str | None) -> str:
    if not text:
        return ""
    t = _PLACEHOLDER_RE.sub("", text)
    t = _BR_RE.sub(" ", t)
    t = _TAG_RE.sub("", t)
    t = html.unescape(t)
    return _WS_RE.sub(" ", t).strip()


def _chunk(content: str, heading_path: str, source_url: str) -> dict:
    return {"content": content, "heading_path": heading_path, "source_url": source_url}


def _champion_url(patch: str, cid: str) -> str:
    return f"{BASE}/cdn/{patch}/data/{LOCALE}/champion/{cid}.json"


def load_chunks(version: str, **_chunk_kwargs) -> list[dict]:
    """Ingest one patch: write the structured tables and return prose chunks.

    `version` is the patch (run_ingest resolves 'latest' before calling us).
    """
    patch = version
    print(f"  Data Dragon patch {patch}")

    champ_rows: list[dict] = []
    ability_rows: list[dict] = []
    item_rows: list[dict] = []
    chunks: list[dict] = []

    # --- champions (summary lists ids; each detail has spells + passive) ---
    # Patch 16.15 added a parallel `Jade_*` roster for the classic/retro game
    # mode — the same champions, rebalanced for a different ruleset, published
    # under the SAME display names. Excluded here so the corpus stays Summoner's
    # Rift only, matching the OP.GG live path, which is also SR. Without this the
    # corpus holds two contradictory "Garen"s competing in retrieval. Filtering
    # the id list covers structured tables AND prose chunks, since both are built
    # in the loop below. See core/lol_roster.py for why the rule is not a
    # "Jade_" string match.
    summary = _get(f"{BASE}/cdn/{patch}/data/{LOCALE}/champion.json")["data"]
    kept, variants = select_canonical([(cid, c["name"]) for cid, c in summary.items()])
    champ_ids = [cid for cid, _ in kept]
    if variants:
        print(f"  excluding {len(variants)} non-Summoner's-Rift variant champions "
              f"(e.g. {', '.join(cid for cid, _ in variants[:3])}) — classic-mode roster")
    print(f"  fetching {len(champ_ids)} champions ...")
    for i, cid in enumerate(champ_ids, 1):
        detail = _get(_champion_url(patch, cid))["data"][cid]
        url = _champion_url(patch, cid)
        cname = detail["name"]
        champ_rows.append({
            "id": cid, "patch": patch, "name": cname, "title": detail.get("title"),
            "tags": detail.get("tags") or [], "partype": detail.get("partype"),
            "stats": detail.get("stats") or {},
        })

        # passive (no numbers) -> prose only
        passive = detail.get("passive") or {}
        p_desc = _strip_markup(passive.get("description"))
        if p_desc:
            chunks.append(_chunk(
                f"{cname} — Passive: {passive.get('name','')}. {p_desc}",
                f"{cname} > Passive: {passive.get('name','')}", url))

        # spells Q/W/E/R -> structured row + prose chunk
        for slot, spell in zip("QWER", detail.get("spells") or []):
            desc = _strip_markup(spell.get("description"))
            ability_rows.append({
                "champion_id": cid, "patch": patch, "slot": slot,
                "name": spell.get("name"), "description": desc,
                "cooldown": spell.get("cooldown"), "cost": spell.get("cost"),
                "range": spell.get("range"), "max_rank": spell.get("maxrank"),
            })
            nums = f"Cooldown {spell.get('cooldownBurn','?')}s · cost {spell.get('costBurn','?')} · range {spell.get('rangeBurn','?')}."
            chunks.append(_chunk(
                f"{cname} — {spell.get('name','')} ({slot}). {desc} {nums}",
                f"{cname} > {spell.get('name','')} ({slot})", url))
        if i % 40 == 0:
            print(f"    {i}/{len(champ_ids)}")

    # --- items (structured + prose) ---
    item_url = f"{BASE}/cdn/{patch}/data/{LOCALE}/item.json"
    items = _get(item_url)["data"]
    # Alternate-mode item variants, the same problem as the Jade_* champions and
    # solved by the same rule. Patch 16.15 ships e.g. Thornmail twice: id 3075 at
    # 2450g and id 323075 at 2650g, both flagged for map 11, so the map filter
    # below cannot separate them. Left in, a "which items give X" answer lists
    # one item twice with two different prices, which is a correctness bug, not
    # noise. In all 31 pairs the variant id is the base id with a numeric prefix
    # (322065 from 2065), so select_canonical's length tiebreak picks the base -
    # the same tiebreak that resolves MasterYi against Jade_MasterYi.
    purchasable = {iid: it for iid, it in items.items()
                   if it.get("name") and (it.get("gold") or {}).get("total", 0) > 0
                   and (it.get("maps") or {}).get(SR_MAP)}
    item_kept, item_variants = select_canonical(
        [(iid, it["name"]) for iid, it in purchasable.items()])
    keep_ids = {iid for iid, _name in item_kept}
    if item_variants:
        print(f"  excluding {len(item_variants)} duplicate alternate-mode items "
              f"(e.g. {', '.join(n for _i, n in item_variants[:3])})")
    for iid, it in items.items():
        gold = it.get("gold") or {}
        if iid not in keep_ids:
            continue  # non-purchasable, off-map, or an alternate-mode duplicate
        desc = _strip_markup(it.get("description"))
        item_rows.append({
            "id": iid, "patch": patch, "name": it["name"],
            "plaintext": it.get("plaintext"), "description": desc,
            "gold_total": gold.get("total"), "gold_base": gold.get("base"),
            "stats": it.get("stats") or {}, "tags": it.get("tags") or [],
        })
        plain = it.get("plaintext") or ""
        chunks.append(_chunk(
            f"{it['name']} (item, {gold.get('total')} gold). {plain}. {desc}",
            f"Items > {it['name']}", item_url))
    print(f"  {len(item_rows)} Summoner's Rift items kept (of {len(items)}).")

    # --- runes + summoner spells (prose only) ---
    for tree in _get(f"{BASE}/cdn/{patch}/data/{LOCALE}/runesReforged.json"):
        rune_url = f"{BASE}/cdn/{patch}/data/{LOCALE}/runesReforged.json"
        for slot in tree.get("slots") or []:
            for rune in slot.get("runes") or []:
                rdesc = _strip_markup(rune.get("longDesc") or rune.get("shortDesc"))
                if rdesc:
                    chunks.append(_chunk(
                        f"{rune['name']} (rune, {tree['name']} tree). {rdesc}",
                        f"Runes > {tree['name']} > {rune['name']}", rune_url))

    summ_url = f"{BASE}/cdn/{patch}/data/{LOCALE}/summoner.json"
    for spell in _get(summ_url)["data"].values():
        sdesc = _strip_markup(spell.get("description"))
        if sdesc:
            chunks.append(_chunk(
                f"{spell['name']} (summoner spell). {sdesc} Cooldown {spell.get('cooldownBurn','?')}s.",
                f"Summoner Spells > {spell['name']}", summ_url))

    # --- write structured tables (idempotent per patch) ---
    store.init_lol_schema()
    store.delete_lol_patch(patch)
    store.insert_lol_champions(champ_rows)
    store.insert_lol_abilities(ability_rows)
    store.insert_lol_items(item_rows)
    print(f"  structured: {len(champ_rows)} champions, {len(ability_rows)} abilities, "
          f"{len(item_rows)} items.")
    print(f"  prose chunks: {len(chunks)}.")
    return chunks
