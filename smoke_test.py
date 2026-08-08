"""Phase 1 smoke test: end-to-end proof that embed -> store -> search works.

Seeds five FastAPI-concept chunks plus two unrelated sentences, then searches
for "how do I use query parameters?" and asserts:
  1. the query-parameters chunk ranks #1, and
  2. both unrelated sentences score below 0.3 (they must not look relevant).

This grows into the Phase 2 integration test. Run against the dockerized DB:
    docker compose up -d
    python smoke_test.py
Prints "SMOKE TEST PASSED" and exits 0 on success; raises and exits non-zero
otherwise.
"""

from __future__ import annotations

import sys

from core import store
from core.embeddings import embed_query, embed_texts

CORPUS = "smoke"

# Five real FastAPI concepts. The first is the intended top hit for our query.
FASTAPI_CHUNKS = [
    (
        "Query Parameters",
        "In FastAPI, function parameters that are not part of the path are "
        "interpreted as query parameters. You declare them as arguments with "
        "default values, and FastAPI reads them from the URL query string.",
    ),
    (
        "Path Parameters",
        "Path parameters are parts of the URL path declared with curly braces "
        "in the route, like /items/{item_id}. FastAPI passes the value to your "
        "function argument and validates its type.",
    ),
    (
        "Request Body",
        "To declare a request body in FastAPI, define a Pydantic model and use "
        "it as a type hint on a function parameter. FastAPI reads and validates "
        "the JSON body against the model.",
    ),
    (
        "Dependencies",
        "FastAPI's dependency injection lets you declare shared logic with "
        "Depends(). Dependencies run before your path operation and can provide "
        "values like database sessions or the current user.",
    ),
    (
        "Response Model",
        "Use the response_model parameter on a path operation to declare the "
        "shape of the response. FastAPI filters and validates the returned data "
        "against that Pydantic model.",
    ),
]

# Two sentences with nothing to do with web frameworks. Must score low.
UNRELATED_CHUNKS = [
    ("Cooking", "Simmer the onions in olive oil until they turn golden brown."),
    ("Astronomy", "Jupiter is the largest planet in our solar system."),
]

QUERY = "how do I use query parameters?"
UNRELATED_MAX_SIMILARITY = 0.3


def _reset_corpus() -> None:
    """Delete any leftover rows from a prior run so the test is deterministic.

    Uses the store's own connection helper rather than opening a second path to
    the DB, keeping all Postgres access inside core.store.
    """
    with store._connect() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM chunks WHERE corpus = %s", (CORPUS,))
        conn.commit()


def _seed() -> None:
    heading_texts = FASTAPI_CHUNKS + UNRELATED_CHUNKS
    contents = [content for _heading, content in heading_texts]
    embeddings = embed_texts(contents)

    rows = [
        {
            "corpus": CORPUS,
            "source_url": None,
            "heading_path": heading,
            "doc_version": "smoke",
            "content": content,
            "embedding": emb,
        }
        for (heading, content), emb in zip(heading_texts, embeddings)
    ]
    inserted = store.insert_chunks(rows)
    assert inserted == len(rows), f"expected {len(rows)} inserts, got {inserted}"


def main() -> int:
    print("Initializing schema...")
    store.init_schema()

    print("Resetting smoke corpus...")
    _reset_corpus()

    print("Seeding 5 FastAPI chunks + 2 unrelated sentences...")
    _seed()

    print(f"Searching: {QUERY!r}")
    hits = store.search_chunks(embed_query(QUERY), corpus=CORPUS, top_k=7)

    print("\nRanked results:")
    for rank, hit in enumerate(hits, start=1):
        print(f"  {rank}. {hit.similarity:+.4f}  {hit.heading_path}")

    # --- Assertions ---
    assert hits, "search returned no results"

    top = hits[0]
    assert top.heading_path == "Query Parameters", (
        f"expected 'Query Parameters' to rank #1, got {top.heading_path!r}"
    )

    unrelated_headings = {h for h, _ in UNRELATED_CHUNKS}
    for hit in hits:
        if hit.heading_path in unrelated_headings:
            assert hit.similarity < UNRELATED_MAX_SIMILARITY, (
                f"unrelated chunk {hit.heading_path!r} scored "
                f"{hit.similarity:.4f} >= {UNRELATED_MAX_SIMILARITY}"
            )

    print("\nSMOKE TEST PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
