"""Ingestion CLI.

    python -m ingestion.run_ingest --corpus fastapi --version 0.139.0

Idempotent per (corpus, version): the existing slice is deleted and rebuilt, so
re-running is safe and never duplicates or touches other corpora/versions.
Embeddings are computed in batches (far cheaper than one call per chunk).

Design note — what gets embedded: we store the clean chunk `content` but embed
`heading_path + content`. The heading path ("Tutorial > Query Parameters") is
strong signal for a query like "how do I use query parameters", yet we don't
want it polluting the text shown to the user or fed to the reranker/LLM. So the
embedding sees the augmented string; everything else sees clean content.

This augmentation is load-bearing and must not drift: feeding the embedder bare
`content` is precisely the defect that invalidated the first embedding bake-off
(it handicapped the candidate model by ~16 points of hit@1 and inverted the
result). `_augment_for_embedding` is the single definition of that input, and
the post-ingest verification below re-embeds through it.
"""

from __future__ import annotations

import argparse
import math
import random
import sys

from core import store
from core.embeddings import embed_texts
from ingestion import fastapi_docs


def _augment_for_embedding(chunk: dict) -> str:
    heading = chunk.get("heading_path")
    return f"{heading}\n\n{chunk['content']}" if heading else chunk["content"]


def verify_pairing(corpus: str, version: str, sample: int) -> None:
    """Post-ingest integrity check: re-embed N randomly chosen stored chunks and
    confirm each matches the vector actually saved for that row.

    This is the direct test for the misordering failure mode — an embeddings API
    may return `data` out of request order, and pairing by position instead of by
    `index` assigns every chunk a wrong-but-plausible vector. Retrieval then
    degrades in a way that looks exactly like "the model is bad", which is how it
    escaped the first bake-off. A shuffled table scores ~0 here.
    """
    if sample <= 0:
        print("  verification skipped (--verify-sample 0).")
        return

    rows = store.sample_chunks(corpus, version, sample)
    if not rows:
        print("  verification skipped: no rows returned.")
        return

    fresh = embed_texts([
        _augment_for_embedding({"heading_path": h, "content": c}) for _, h, c in rows
    ])
    sims = [
        store.cosine_to_stored(cid, vec) for (cid, _, _), vec in zip(rows, fresh)
    ]
    worst = min(sims)
    print(
        f"  pairing integrity: {len(sims)} sampled chunks re-embedded, "
        f"cos(stored, fresh) min={worst:.6f} mean={sum(sims) / len(sims):.6f}"
    )
    if worst < 0.999 or not math.isfinite(worst):
        raise SystemExit(
            f"INGEST FAILED VERIFICATION: a sampled chunk's stored vector does not "
            f"match a fresh embedding of its own text (cosine {worst:.6f}, expected "
            f">= 0.999). Chunks and vectors are misaligned — do NOT serve this data."
        )


def _resolve_version(corpus: str, version: str | None) -> str:
    """Default the version per corpus when not given: the pinned fastapi release,
    or the latest Data Dragon patch for lol."""
    if version:
        return version
    if corpus == "fastapi":
        return fastapi_docs.DEFAULT_FASTAPI_VERSION
    if corpus == "lol":
        from ingestion import lol_datadragon
        return lol_datadragon.latest_patch()
    raise SystemExit(f"Unknown corpus {corpus!r}. Supported: fastapi, lol")


def _load(corpus: str, version: str, repo_url: str | None) -> list[dict]:
    if corpus == "fastapi":
        kwargs = {"repo_url": repo_url} if repo_url else {}
        return fastapi_docs.load_chunks(version=version, **kwargs)
    if corpus == "lol":
        # Also writes the structured lol_* tables as a side effect (idempotent
        # per patch); returns the prose chunks for the shared embed/store path.
        from ingestion import lol_datadragon
        return lol_datadragon.load_chunks(version=version)
    raise SystemExit(f"Unknown corpus {corpus!r}. Supported: fastapi, lol")


def ingest(corpus: str, version: str | None, batch_size: int, repo_url: str | None,
           verify_sample: int = 12) -> None:
    version = _resolve_version(corpus, version)
    print(f"Loading + chunking corpus={corpus} version={version} ...")
    chunks = _load(corpus, version, repo_url)
    print(f"  {len(chunks)} chunks produced.")
    if not chunks:
        raise SystemExit("No chunks produced — nothing to ingest.")

    store.init_schema()

    # Idempotency: clear just this (corpus, version) before inserting.
    deleted = store.delete_corpus_version(corpus, version)
    print(f"  deleted {deleted} existing rows for ({corpus}, {version}).")

    inserted = 0
    for start in range(0, len(chunks), batch_size):
        batch = chunks[start : start + batch_size]
        embeddings = embed_texts([_augment_for_embedding(c) for c in batch])
        rows = [
            {
                "corpus": corpus,
                "source_url": c.get("source_url"),
                "heading_path": c.get("heading_path"),
                "doc_version": version,
                "content": c["content"],
                "embedding": emb,
            }
            for c, emb in zip(batch, embeddings)
        ]
        inserted += store.insert_chunks(rows)
        print(f"  embedded + inserted {inserted}/{len(chunks)}", end="\r", flush=True)

    print(f"\nInserted {inserted} chunks for ({corpus}, {version}). Verifying ...")
    verify_pairing(corpus, version, verify_sample)
    print(f"Done. ({corpus}, {version}) ingested and verified.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ingest a corpus into pgvector.")
    parser.add_argument("--corpus", required=True, help="e.g. fastapi")
    parser.add_argument(
        "--version",
        default=None,
        help="pinned source version stamped as doc_version. Optional: defaults to "
        "the pinned fastapi release, or the latest Data Dragon patch for lol.",
    )
    parser.add_argument("--batch-size", type=int, default=128)
    parser.add_argument(
        "--repo-url", default=None, help="override the source repo URL (testing)"
    )
    parser.add_argument(
        "--verify-sample",
        type=int,
        default=12,
        help="chunks to re-embed after ingest to prove vectors are paired to the "
        "right rows (0 disables; not recommended)",
    )
    args = parser.parse_args(argv)

    ingest(args.corpus, args.version, args.batch_size, args.repo_url, args.verify_sample)
    return 0


if __name__ == "__main__":
    sys.exit(main())
