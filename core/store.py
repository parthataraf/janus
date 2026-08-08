"""All Postgres/pgvector access lives here. Nothing else in the codebase opens
a DB connection, so the schema and query details have exactly one owner.

Design notes:
  - pgvector accepts a vector literal as the string "[f1,f2,...]". We hand it
    Python's `str(list_of_floats)` (which produces exactly that format) and let
    Postgres cast it with `::vector`.
  - Similarity is reported as cosine SIMILARITY in [~0,1], computed as
    `1 - (embedding <=> query)` because pgvector's `<=>` returns cosine
    DISTANCE. Callers think in "higher = more similar".
"""

from __future__ import annotations

import json
from typing import Iterable, NamedTuple

import psycopg

from core import config


class SearchHit(NamedTuple):
    """One vector-search result. Tuple-shaped so callers can unpack, but named
    for readability at call sites and in logs. `similarity` is cosine
    similarity (higher = closer)."""

    id: int
    content: str
    source_url: str | None
    heading_path: str | None
    similarity: float


class KeywordHit(NamedTuple):
    """One full-text-search result. Separate type from SearchHit because its
    score is a Postgres `ts_rank` (not a cosine similarity) — different scale,
    not comparable to vector scores, which is exactly why hybrid search fuses
    by rank (RRF) rather than by raw score."""

    id: int
    content: str
    source_url: str | None
    heading_path: str | None
    rank: float


def _connect() -> psycopg.Connection:
    """Single place that dials the database, so connection config is uniform."""
    return psycopg.connect(config.DATABASE_URL)


def init_schema() -> None:
    """Create the extension, table, and indexes if absent. Idempotent — safe to
    call on every startup."""
    with _connect() as conn, conn.cursor() as cur:
        cur.execute("CREATE EXTENSION IF NOT EXISTS vector")

        # The embedding column width is baked from config so the schema always
        # matches the active model. `content` and `corpus` are NOT NULL because
        # a chunk without either is meaningless.
        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS chunks (
                id           SERIAL PRIMARY KEY,
                corpus       TEXT NOT NULL,
                source_url   TEXT,
                heading_path TEXT,
                doc_version  TEXT,
                content      TEXT NOT NULL,
                embedding    vector({config.EMBED_DIM})
            )
            """
        )

        # GIN index over the full-text vector of `content`. Unused in Phase 1
        # but created now so Phase 2's keyword_search (plainto_tsquery/ts_rank)
        # is fast the moment it lands. Expression must match the query exactly.
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS chunks_content_fts
            ON chunks
            USING GIN (to_tsvector('english', content))
            """
        )

        # Every query filters by corpus (and often doc_version), so index the
        # pair to keep those scans cheap as the table grows.
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS chunks_corpus_version
            ON chunks (corpus, doc_version)
            """
        )

        conn.commit()

    # CREATE TABLE IF NOT EXISTS is a no-op on an existing table, so a changed
    # EMBED_DIM would otherwise be silently ignored and every insert would fail
    # deep inside a ::vector cast. Check the live column width and say exactly
    # what to run.
    actual = embedding_dim()
    if actual is not None and actual != config.EMBED_DIM:
        raise RuntimeError(
            f"chunks.embedding is vector({actual}) but EMBED_DIM={config.EMBED_DIM} "
            f"(EMBED_MODEL={config.EMBED_MODEL!r}). The column cannot be reused across "
            "embedding models. Run:  python -m ingestion.migrate_embeddings --yes  "
            "then re-ingest every corpus."
        )


def embedding_dim() -> int | None:
    """Declared width of `chunks.embedding`, or None if the table/column is
    absent. pgvector stores the dimension in atttypmod."""
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT a.atttypmod
            FROM pg_attribute a
            JOIN pg_class c ON c.oid = a.attrelid
            WHERE c.relname = 'chunks' AND a.attname = 'embedding' AND a.attnum > 0
            """
        )
        row = cur.fetchone()
    if not row or row[0] is None or row[0] < 0:
        return None
    return int(row[0])


def insert_chunks(rows: Iterable[dict]) -> int:
    """Bulk-insert chunk rows. Each row is a dict with keys:
        corpus, source_url, heading_path, doc_version, content, embedding
    where `embedding` is a list[float]. Returns the number of rows inserted.

    The embedding list is stringified so Postgres can cast it to `vector` —
    this is the format pgvector expects for a literal.
    """
    params = [
        (
            r["corpus"],
            r.get("source_url"),
            r.get("heading_path"),
            r.get("doc_version"),
            r["content"],
            str(r["embedding"]),  # [0.1, 0.2, ...] -> "[0.1, 0.2, ...]"
        )
        for r in rows
    ]
    if not params:
        return 0

    with _connect() as conn, conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO chunks
                (corpus, source_url, heading_path, doc_version, content, embedding)
            VALUES (%s, %s, %s, %s, %s, %s::vector)
            """,
            params,
        )
        conn.commit()
    return len(params)


def delete_corpus_version(corpus: str, doc_version: str) -> int:
    """Delete all chunks for one (corpus, doc_version) pair. Used to make
    ingestion idempotent: re-running a version wipes just that slice before
    re-inserting, never touching other corpora or versions. Returns row count.
    """
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "DELETE FROM chunks WHERE corpus = %s AND doc_version = %s",
            (corpus, doc_version),
        )
        deleted = cur.rowcount
        conn.commit()
    return deleted


def keyword_search_chunks(
    query: str,
    corpus: str,
    top_k: int = 5,
    doc_version: str | None = None,
) -> list[KeywordHit]:
    """Postgres full-text search within one corpus (optionally one version).

    Uses `plainto_tsquery` (treats the query as plain words, AND-ed) against the
    GIN-indexed `to_tsvector('english', content)`, ranked by `ts_rank`. Exists
    alongside vector search because exact tokens like `Depends` or
    `response_model` embed poorly but match verbatim here.
    """
    # Version filter is optional; build the corpus-scoped WHERE around it.
    version_filter = ""
    version_args: list = []
    if doc_version is not None:
        version_filter = "AND doc_version = %s"
        version_args = [doc_version]

    # `to_tsvector('english', content)` is written identically here and in the
    # GIN index definition, so the planner can use the index for the @@ filter.
    # ts_rank recomputes the vector for scoring the (already filtered) rows.
    sql = f"""
        SELECT id, content, source_url, heading_path,
               ts_rank(to_tsvector('english', content),
                       plainto_tsquery('english', %s)) AS rank
        FROM chunks
        WHERE corpus = %s
          {version_filter}
          AND to_tsvector('english', content) @@ plainto_tsquery('english', %s)
        ORDER BY rank DESC
        LIMIT %s
    """
    # Statement order: rank query, corpus, [version], filter query, limit.
    params = [query, corpus, *version_args, query, top_k]

    with _connect() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return [KeywordHit(*row) for row in cur.fetchall()]


def search_chunks(
    query_embedding: list[float],
    corpus: str,
    top_k: int = 5,
    doc_version: str | None = None,
) -> list[SearchHit]:
    """Cosine-similarity search within one corpus (optionally one version).

    Ordered by nearest cosine distance via `<=>`; similarity returned as
    `1 - distance` for caller-friendly "higher = better" scores.
    """
    vec = str(query_embedding)

    # Build the WHERE clause dynamically only for the optional version filter,
    # keeping parameters positional and injection-safe.
    where = "WHERE corpus = %s"
    args: list = [corpus]
    if doc_version is not None:
        where += " AND doc_version = %s"
        args.append(doc_version)

    sql = f"""
        SELECT id, content, source_url, heading_path,
               1 - (embedding <=> %s::vector) AS similarity
        FROM chunks
        {where}
        ORDER BY embedding <=> %s::vector
        LIMIT %s
    """
    # The vector appears twice (SELECT and ORDER BY); pass it in both slots
    # around the WHERE args in statement order.
    params = [vec, *args, vec, top_k]

    with _connect() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return [SearchHit(*row) for row in cur.fetchall()]


def sample_chunks(corpus: str, doc_version: str, n: int) -> list[tuple[int, str | None, str]]:
    """Random (id, heading_path, content) rows from one corpus slice. Used by the
    post-ingest pairing-integrity check."""
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, heading_path, content FROM chunks "
            "WHERE corpus = %s AND doc_version = %s ORDER BY random() LIMIT %s",
            (corpus, doc_version, n),
        )
        return cur.fetchall()


def cosine_to_stored(chunk_id: int, embedding: list[float]) -> float:
    """Cosine similarity between a freshly computed vector and the one stored for
    that chunk. 1.0 means the row holds exactly the vector its own text produces."""
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT 1 - (embedding <=> %s::vector) FROM chunks WHERE id = %s",
            (str(embedding), chunk_id),
        )
        row = cur.fetchone()
    return float(row[0]) if row and row[0] is not None else 0.0


def chunk_url_exists(corpus: str, source_url: str) -> bool:
    """Does the corpus have any chunk from this source_url? Used by the eval
    label-refresh tool to flag prose expected_urls that no longer resolve."""
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM chunks WHERE corpus = %s AND source_url = %s LIMIT 1",
            (corpus, source_url),
        )
        return cur.fetchone() is not None


def ping() -> bool:
    """Lightweight connectivity check for /health: is the DB reachable?"""
    with _connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1")
        return cur.fetchone()[0] == 1


def list_corpora() -> list[dict]:
    """Distinct corpora and their ingested doc_versions (with chunk counts),
    for the /corpora endpoint that drives the UI's corpus + version dropdowns."""
    with _connect() as conn, conn.cursor() as cur:
        # Hide test/scratch corpora (config.EXCLUDED_CORPORA) from the switcher.
        # An empty list makes ANY('{}') false, so NOT(...) keeps every corpus.
        cur.execute(
            """
            SELECT corpus, doc_version, count(*)
            FROM chunks
            WHERE NOT (corpus = ANY(%s))
            GROUP BY corpus, doc_version
            ORDER BY corpus, doc_version
            """,
            (config.EXCLUDED_CORPORA,),
        )
        rows = cur.fetchall()

    by_corpus: dict[str, dict] = {}
    for corpus, version, n in rows:
        entry = by_corpus.setdefault(
            corpus, {"corpus": corpus, "versions": [], "chunks": 0}
        )
        entry["versions"].append({"version": version, "chunks": n})
        entry["chunks"] += n
    return list(by_corpus.values())


# --------------------------------------------------------------------------- #
# LoL structured tables (Phase 4a)
#
# The `chunks` table above holds the embedded PROSE for every corpus (fastapi,
# lol, ...). These extra tables hold the LoL face's NUMERIC source of truth —
# ability cooldowns/costs, item gold/stats — so Phase 4b's routing can answer
# "cooldown of Yasuo's Q at rank 3" from exact numbers, not from prose. JSONB
# holds ragged/per-rank arrays. Keyed by (id, patch) so a patch is idempotent.
# --------------------------------------------------------------------------- #
def init_lol_schema() -> None:
    """Create the LoL structured tables if absent. Idempotent."""
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS lol_champions (
                id       TEXT NOT NULL,
                patch    TEXT NOT NULL,
                name     TEXT NOT NULL,
                title    TEXT,
                tags     TEXT[],
                partype  TEXT,
                stats    JSONB,
                PRIMARY KEY (id, patch)
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS lol_abilities (
                champion_id  TEXT NOT NULL,
                patch        TEXT NOT NULL,
                slot         TEXT NOT NULL,   -- P, Q, W, E, R
                name         TEXT,
                description  TEXT,
                cooldown     JSONB,           -- per-rank array
                cost         JSONB,
                range        JSONB,
                max_rank     INT,
                PRIMARY KEY (champion_id, patch, slot)
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS lol_items (
                id          TEXT NOT NULL,
                patch       TEXT NOT NULL,
                name        TEXT,
                plaintext   TEXT,
                description TEXT,
                gold_total  INT,
                gold_base   INT,
                stats       JSONB,
                tags        TEXT[],
                PRIMARY KEY (id, patch)
            )
            """
        )
        conn.commit()


def delete_lol_patch(patch: str) -> None:
    """Clear all LoL structured rows for one patch, so re-ingesting a patch is
    idempotent (mirrors delete_corpus_version for the chunks table)."""
    with _connect() as conn, conn.cursor() as cur:
        for table in ("lol_champions", "lol_abilities", "lol_items"):
            cur.execute(f"DELETE FROM {table} WHERE patch = %s", (patch,))
        conn.commit()


def insert_lol_champions(rows: Iterable[dict]) -> int:
    params = [
        (r["id"], r["patch"], r["name"], r.get("title"), r.get("tags") or [],
         r.get("partype"), json.dumps(r.get("stats") or {}))
        for r in rows
    ]
    if not params:
        return 0
    with _connect() as conn, conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO lol_champions (id, patch, name, title, tags, partype, stats) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)",
            params,
        )
        conn.commit()
    return len(params)


def insert_lol_abilities(rows: Iterable[dict]) -> int:
    params = [
        (r["champion_id"], r["patch"], r["slot"], r.get("name"), r.get("description"),
         json.dumps(r.get("cooldown")), json.dumps(r.get("cost")),
         json.dumps(r.get("range")), r.get("max_rank"))
        for r in rows
    ]
    if not params:
        return 0
    with _connect() as conn, conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO lol_abilities "
            "(champion_id, patch, slot, name, description, cooldown, cost, range, max_rank) "
            "VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s)",
            params,
        )
        conn.commit()
    return len(params)


def insert_lol_items(rows: Iterable[dict]) -> int:
    params = [
        (r["id"], r["patch"], r.get("name"), r.get("plaintext"), r.get("description"),
         r.get("gold_total"), r.get("gold_base"), json.dumps(r.get("stats") or {}),
         r.get("tags") or [])
        for r in rows
    ]
    if not params:
        return 0
    with _connect() as conn, conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO lol_items "
            "(id, patch, name, plaintext, description, gold_total, gold_base, stats, tags) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s)",
            params,
        )
        conn.commit()
    return len(params)


# --- LoL structured reads (Phase 4b routing) ------------------------------- #
def lol_patches() -> list[str]:
    """Patches present in the LoL structured tables, oldest→newest-ish."""
    with _connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT DISTINCT patch FROM lol_champions ORDER BY patch")
        return [r[0] for r in cur.fetchall()]


def lol_entity_names(patch: str) -> dict:
    """Names for the entity dictionary: champions (id, name) and item names."""
    with _connect() as conn, conn.cursor() as cur:
        # ORDER BY so the row order is stable. Display names are not unique
        # (16.15.1 ships `Garen` and `Jade_Garen`, both named "Garen"), and the
        # caller has to choose between them; an unordered scan made that choice
        # depend on physical row order. The caller still picks explicitly — this
        # just removes the nondeterminism underneath it.
        cur.execute("SELECT id, name FROM lol_champions WHERE patch = %s ORDER BY id",
                    (patch,))
        champions = cur.fetchall()
        cur.execute("SELECT DISTINCT name FROM lol_items WHERE patch = %s", (patch,))
        items = [r[0] for r in cur.fetchall()]
    return {"champions": champions, "items": items}


def lol_ability(champion_id: str, slot: str, patch: str) -> dict | None:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT name, description, cooldown, cost, range, max_rank "
            "FROM lol_abilities WHERE champion_id = %s AND slot = %s AND patch = %s",
            (champion_id, slot, patch),
        )
        row = cur.fetchone()
    if not row:
        return None
    keys = ("name", "description", "cooldown", "cost", "range", "max_rank")
    return dict(zip(keys, row))


def lol_champion_id(name_or_id: str, patch: str) -> str | None:
    """Resolve a champion display name OR Data Dragon id to its id (they differ
    for a few, e.g. Wukong -> MonkeyKing)."""
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM lol_champions "
            "WHERE patch = %s AND (id = %s OR lower(name) = lower(%s)) LIMIT 1",
            (patch, name_or_id, name_or_id),
        )
        row = cur.fetchone()
    return row[0] if row else None


def lol_champion(champion_id: str, patch: str) -> dict | None:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id, name, title, tags, partype, stats "
            "FROM lol_champions WHERE id = %s AND patch = %s",
            (champion_id, patch),
        )
        row = cur.fetchone()
    if not row:
        return None
    return dict(zip(("id", "name", "title", "tags", "partype", "stats"), row))


def lol_item(name: str, patch: str) -> dict | None:
    with _connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT name, plaintext, description, gold_total, stats, tags "
            "FROM lol_items WHERE lower(name) = lower(%s) AND patch = %s",
            (name, patch),
        )
        row = cur.fetchone()
    if not row:
        return None
    return dict(zip(("name", "plaintext", "description", "gold_total", "stats", "tags"), row))


# Stat phrase -> Data Dragon item tag. Tags are a structured property of the
# item; the prose is not. Searching the text for "armor penetration" returned 5
# items, because lethality items describe themselves as "18 Lethality" and never
# say the phrase — the tag finds all 23.
#
# "lethality" is deliberately NOT mapped to that tag, even though Riot files it
# there. The containment runs one way: lethality IS armor penetration, so the
# broad question should return the lethality items, but the narrow question must
# not return percentage-penetration items. Mapping it symmetrically put Lord
# Dominik's Regards — 35% armor penetration, no lethality — in the answer to
# "which items give lethality", which the faithfulness judge caught (scored 3/5).
# Untagged phrases fall through to the text search, which finds the 16 items
# that actually state a lethality value.
_STAT_TAGS = {
    "armor penetration": "ArmorPenetration",
    "magic penetration": "MagicPenetration",
    "ability power": "SpellDamage",
    "attack damage": "Damage",
    "attack speed": "AttackSpeed",
    "critical strike": "CriticalStrike",
    "ability haste": "AbilityHaste",
    "cooldown reduction": "CooldownReduction",
    "life steal": "LifeSteal",
    "lifesteal": "LifeSteal",
    "tenacity": "Tenacity",
    "mana regen": "ManaRegen",
    "health regen": "HealthRegen",
    "movement speed": "NonbootsMovement",
    "move speed": "NonbootsMovement",
}

# Enough to enumerate any category a player actually asks to see in full — the
# largest of those is armor penetration at 23 — while still bounding the broad
# "every item has some" categories (Health 89, Damage 78, AbilityHaste 76),
# where an exhaustive list would be noise rather than an answer. When the cap
# bites, the caller SAYS the list was cut; it never presents a truncated list as
# the complete set.
ITEM_LIST_LIMIT = 30


def lol_items_by_keyword(keyword: str, patch: str,
                         limit: int = ITEM_LIST_LIMIT) -> dict:
    """Items providing a stat, for multi-row questions.

    Prefers the item's `tags` array and falls back to a text search for stats
    Riot does not tag (omnivamp, say). Returns {"items": [...], "total": n} so
    the caller can disclose a truncated list instead of implying completeness.

    DISTINCT ON (name) is defence in depth: ingest now drops alternate-mode
    duplicates, but a corpus ingested before that fix still holds them, and two
    rows of the same item with different gold costs must never both be listed.
    """
    # UNION of tag and text, not tag instead of text. The tag is far broader for
    # some stats (armor penetration: 23 tagged vs 5 that say the phrase) but it
    # is NOT a superset — Cull grants life steal in its text and carries no
    # LifeSteal tag, and a tag-only query silently dropped it. Each source
    # catches what the other misses.
    like = f"%{keyword}%"
    text = "(description ILIKE %s OR plaintext ILIKE %s)"
    tag = _STAT_TAGS.get(keyword.lower())
    if tag:
        where, params = f"(%s = ANY(tags) OR {text})", [tag, like, like]
    else:
        where, params = text, [like, like]

    with _connect() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT count(DISTINCT name) FROM lol_items "
                    f"WHERE patch = %s AND {where}", (patch, *params))
        total = cur.fetchone()[0]
        cur.execute(
            f"SELECT DISTINCT ON (name) name, plaintext, gold_total, description "
            f"FROM lol_items WHERE patch = %s AND {where} "
            f"ORDER BY name, gold_total DESC NULLS LAST",
            (patch, *params),
        )
        rows = [dict(zip(("name", "plaintext", "gold_total", "description"), r))
                for r in cur.fetchall()]
    rows.sort(key=lambda r: (r["gold_total"] is None, -(r["gold_total"] or 0)))
    return {"items": rows[:limit], "total": total}
