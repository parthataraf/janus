"""Manual QA helper: retrieve + generate for one question from the CLI.

Not part of the eval harness (Phase 2e) — just a way to exercise the 2c/2d
pipeline by hand and eyeball grounding, citations, and the refusal path.

    python ask.py "How do I declare query parameters?"
    python ask.py --corpus fastapi "What is a WebSocket in FastAPI?"
"""

from __future__ import annotations

import argparse

from core import generation, retrieval


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("question", nargs="+")
    parser.add_argument("--corpus", default="fastapi")
    parser.add_argument("--version", default=None)
    args = parser.parse_args()
    question = " ".join(args.question)

    chunks = retrieval.retrieve(question, args.corpus, doc_version=args.version)

    print(f"\nQ: {question}\n")
    print("Retrieved (rerank score  |  heading path):")
    for c in chunks:
        print(f"  {c.rerank_score:+.3f}  {c.heading_path}")

    result = generation.generate(question, chunks)
    print(f"\n{'[REFUSED] ' if result['refused'] else ''}Answer:\n{result['answer']}\n")

    if result["citations"]:
        print("Citations:")
        for cite in result["citations"]:
            print(f"  [{cite['index']}] {cite['heading_path']} -> {cite['source_url']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
