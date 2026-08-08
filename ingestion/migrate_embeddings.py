"""Embedding-space migration.

    python -m ingestion.migrate_embeddings --yes

Changing EMBED_MODEL changes the vector space AND usually the vector width, and
old vectors are meaningless in the new space — there is no way to convert them,
only to recompute them. So this rebuilds `chunks` empty at the configured width
and hands you back a re-ingest list.

Destructive and deliberately explicit: it refuses to run without --yes, prints
exactly what it will drop first, and never touches the structured `lol_*` tables
(those hold no embeddings and are rebuilt by the lol ingest anyway).
"""

from __future__ import annotations

import argparse
import sys

from core import config, store


def plan() -> tuple[int | None, list[tuple[str, str, int]]]:
    """Current column width and the (corpus, version, rows) that will be lost."""
    current = store.embedding_dim()
    with store._connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.chunks')")
        if cur.fetchone()[0] is None:
            return None, []
        cur.execute(
            "SELECT corpus, doc_version, count(*) FROM chunks "
            "GROUP BY 1, 2 ORDER BY 1, 2"
        )
        rows = cur.fetchall()
    return current, [(c, v, n) for c, v, n in rows]


def migrate(yes: bool) -> int:
    current, slices = plan()
    target = config.EMBED_DIM

    print(f"embedding provider : {config.EMBED_PROVIDER}")
    print(f"embedding model    : {config.EMBED_MODEL}")
    print(f"column width       : {current if current is not None else '(no table)'} -> {target}")
    if slices:
        print("\nrows that will be DELETED (re-ingest required afterwards):")
        for c, v, n in slices:
            print(f"    {c:<10} {v:<12} {n:>6} chunks")
    else:
        print("\nno existing chunk rows.")

    if current == target and slices:
        print(
            f"\nColumn is already vector({target}). Nothing to migrate — but note that "
            "rows embedded by a DIFFERENT model of the same width would still be stale; "
            "re-ingest if you changed EMBED_MODEL without changing EMBED_DIM."
        )
        return 0

    if not yes:
        print("\nRefusing to proceed without --yes. Nothing changed.")
        return 1

    with store._connect() as conn, conn.cursor() as cur:
        # Drop rather than ALTER: every existing vector is in the old space and
        # would have to be discarded regardless, and DROP also clears the
        # dependent FTS/corpus indexes that init_schema then recreates cleanly.
        cur.execute("DROP TABLE IF EXISTS chunks")
        conn.commit()
    print("\ndropped table `chunks`.")

    store.init_schema()
    print(f"recreated `chunks` with embedding vector({store.embedding_dim()}).")

    corpora = sorted({c for c, _, _ in slices if c != "smoke"}) or ["lol", "fastapi"]
    print("\nNow re-ingest (order does not matter):")
    for c in corpora:
        print(f"    python -m ingestion.run_ingest --corpus {c}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Migrate chunks.embedding to the configured EMBED_DIM.")
    p.add_argument("--yes", action="store_true", help="actually perform the destructive migration")
    args = p.parse_args(argv)
    return migrate(args.yes)


if __name__ == "__main__":
    sys.exit(main())
