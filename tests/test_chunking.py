"""Unit tests for the markdown/code-aware chunker (spec 2a).

Covers the three invariants the spec calls out:
  - heading-path correctness (including nesting)
  - a fenced code block is never severed and stays with its prose
  - a tiny section merges upward into its heading neighbor

Run: python -m pytest tests/test_chunking.py -q
"""

from __future__ import annotations

from core.chunking import chunk_markdown


def _find(chunks, needle):
    """Return the single chunk whose content contains `needle` (asserts unique)."""
    matches = [c for c in chunks if needle in c["content"]]
    assert len(matches) == 1, f"expected exactly one chunk with {needle!r}, got {len(matches)}"
    return matches[0]


# --- heading-path correctness ---------------------------------------------

def test_heading_path_nesting():
    md = """# Tutorial

Intro to the tutorial.

## Query Parameters

Some text about query parameters.

### Optional parameters

Details about optional ones.
"""
    # Disable merge-up so this test isolates heading-path assignment.
    chunks = chunk_markdown(md, source_url="u", doc_version="v", min_merge_tokens=0)

    assert _find(chunks, "Intro to the tutorial")["heading_path"] == "Tutorial"
    assert (
        _find(chunks, "Some text about query parameters")["heading_path"]
        == "Tutorial > Query Parameters"
    )
    assert (
        _find(chunks, "Details about optional ones")["heading_path"]
        == "Tutorial > Query Parameters > Optional parameters"
    )


def test_heading_path_pops_sibling():
    # A second H2 must not inherit the first H2 in its path.
    md = """# Guide

## First

Content of first.

## Second

Content of second.
"""
    chunks = chunk_markdown(md, source_url="u", doc_version="v", min_merge_tokens=0)
    assert _find(chunks, "Content of first")["heading_path"] == "Guide > First"
    assert _find(chunks, "Content of second")["heading_path"] == "Guide > Second"


def test_metadata_stamped_on_every_chunk():
    md = "# A\n\ntext\n\n## B\n\nmore text here for a second chunk\n"
    chunks = chunk_markdown(md, source_url="http://x", doc_version="0.139.0")
    assert chunks, "expected at least one chunk"
    for c in chunks:
        assert c["source_url"] == "http://x"
        assert c["doc_version"] == "0.139.0"
        assert set(c) == {"content", "heading_path", "source_url", "doc_version"}


# --- code blocks are never severed ----------------------------------------

def test_code_block_stays_intact_and_with_prose():
    code = "```python\n" + "\n".join(f"line_{i} = {i}" for i in range(40)) + "\n```"
    md = f"""# API

Here is how you configure the thing:

{code}

And that is the whole story.
"""
    # Force a tiny size budget so the packer is pressured to split — it must
    # still keep the code block whole and attached to its preceding prose.
    chunks = chunk_markdown(md, target_max_tokens=20, min_merge_tokens=0)

    code_chunks = [c for c in chunks if "line_0 = 0" in c["content"]]
    assert len(code_chunks) == 1, "code block was split across chunks"
    host = code_chunks[0]
    # Whole code block present, fences included.
    assert "line_39 = 39" in host["content"]
    assert host["content"].count("```") == 2
    # Travels with the prose immediately preceding it.
    assert "Here is how you configure the thing" in host["content"]


def test_code_block_with_blank_lines_survives():
    code = "```\nfoo()\n\n\nbar()\n```"
    md = f"# T\n\nintro\n\n{code}\n"
    chunks = chunk_markdown(md, target_max_tokens=10, min_merge_tokens=0)
    host = _find(chunks, "foo()")
    assert "bar()" in host["content"]
    assert host["content"].count("```") == 2


# --- tiny sections merge upward -------------------------------------------

def test_tiny_section_merges_up():
    big = " ".join(["paragraph"] * 200)  # well over the merge threshold
    md = f"""# Doc

## Big Section

{big}

## Note

See above.
"""
    # "See above." is tiny; with a healthy merge threshold it should fold into
    # the preceding chunk instead of standing alone.
    chunks = chunk_markdown(md, target_max_tokens=500, min_merge_tokens=120)

    # It must not exist as its own standalone chunk...
    assert not any(c["content"].strip() == "See above." for c in chunks)
    # ...and its text must live in a chunk that also holds the big section,
    # under the big section's heading path (merged "up" into the neighbor).
    host = _find(chunks, "See above.")
    assert "paragraph" in host["content"]
    assert host["heading_path"] == "Doc > Big Section"


def test_small_leading_chunk_has_nowhere_to_merge():
    # A tiny first chunk with nothing above it is kept, not dropped.
    md = "# Only\n\nhi\n"
    chunks = chunk_markdown(md, min_merge_tokens=120)
    assert len(chunks) == 1
    assert "hi" in chunks[0]["content"]
    assert chunks[0]["heading_path"] == "Only"
