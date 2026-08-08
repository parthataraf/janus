"""Markdown/code-aware chunker.

Turns a markdown document into retrieval chunks under three hard rules:

  1. Every chunk carries its full heading path
     ("Tutorial > Query Parameters > Optional parameters"), so a retrieved
     snippet is self-locating for citations and for the embedding.
  2. A fenced code block is NEVER split, and always travels with the prose
     immediately preceding it — a code example divorced from its explanation
     is useless to both the model and the reader.
  3. Chunks target ~300-500 tokens (approximated as chars/4). Tiny sections
     merge up into their neighbor; oversized sections split at paragraph
     boundaries with ~15% overlap so context isn't lost at the seam.

Output is a list of dicts: {content, heading_path, source_url, doc_version}.
`content` is body text only; the heading path lives in its own field so the
caller can embed an augmented string while storing clean prose.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Rough token estimate. The spec explicitly sanctions chars/4 — good enough for
# sizing, and avoids a tokenizer dependency in the hot path.
CHARS_PER_TOKEN = 4
DEFAULT_TARGET_MAX_TOKENS = 500
DEFAULT_MIN_MERGE_TOKENS = 60  # below this a chunk merges up into its neighbor
# Note: merge-up can relabel a noise-sized section with its neighbor's heading
# path. Kept conservative so only genuinely tiny fragments (a heading with a
# one-line pointer) fold up; substantive short sections keep their own path.
# This is exactly what the "spot-check 10 random chunks" human checkpoint tests.
OVERLAP_RATIO = 0.15

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
_FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})")


def estimate_tokens(text: str) -> int:
    """Cheap token estimate used for all size decisions."""
    return max(1, len(text) // CHARS_PER_TOKEN)


@dataclass
class _Element:
    """A parsed top-level markdown element: a heading, or a body block (a prose
    paragraph or a fenced code block)."""

    kind: str  # "heading" | "block"
    text: str
    level: int = 0  # heading level, only for kind == "heading"
    is_code: bool = False  # only for kind == "block"


@dataclass
class _Section:
    """A run of body blocks sharing one heading path."""

    heading_path: str
    blocks: list[_Element]


def _parse_elements(text: str) -> list[_Element]:
    """Flatten markdown into headings and body blocks. Fenced code blocks are
    captured whole (including internal blank lines) so they can never be split
    downstream. Blank lines separate prose paragraphs."""
    elements: list[_Element] = []
    para: list[str] = []
    fence: str | None = None  # the opening fence marker while inside a code block
    code: list[str] = []

    def flush_para() -> None:
        nonlocal para
        joined = "\n".join(para).strip()
        if joined:
            elements.append(_Element(kind="block", text=joined, is_code=False))
        para = []

    for line in text.splitlines():
        if fence is not None:
            # Inside a code block: everything is literal until the closing fence.
            code.append(line)
            if line.strip().startswith(fence):
                elements.append(
                    _Element(kind="block", text="\n".join(code), is_code=True)
                )
                fence, code = None, []
            continue

        fence_match = _FENCE_RE.match(line)
        if fence_match:
            flush_para()
            fence = fence_match.group(1)[:3]  # normalize ``` or ~~~
            code = [line]
            continue

        heading_match = _HEADING_RE.match(line)
        if heading_match:
            flush_para()
            elements.append(
                _Element(
                    kind="heading",
                    text=heading_match.group(2).strip(),
                    level=len(heading_match.group(1)),
                )
            )
            continue

        if line.strip() == "":
            flush_para()
            continue

        para.append(line)

    # An unterminated code fence still travels as one intact block.
    if fence is not None:
        elements.append(_Element(kind="block", text="\n".join(code), is_code=True))
    flush_para()
    return elements


def _group_sections(elements: list[_Element]) -> list[_Section]:
    """Attach each body block to the heading path in effect at that point.

    Maintains a heading stack: a heading of level L pops all stack entries of
    level >= L (closing shallower-or-equal sections) before pushing itself, so
    the path is always the chain of enclosing headings.
    """
    sections: list[_Section] = []
    stack: list[tuple[int, str]] = []
    blocks: list[_Element] = []

    def current_path() -> str:
        return " > ".join(t for _level, t in stack)

    def flush() -> None:
        nonlocal blocks
        if blocks:
            sections.append(_Section(heading_path=current_path(), blocks=blocks))
        blocks = []

    for el in elements:
        if el.kind == "heading":
            flush()  # close the previous section under its (old) path
            while stack and stack[-1][0] >= el.level:
                stack.pop()
            stack.append((el.level, el.text))
        else:
            blocks.append(el)
    flush()
    return sections


def _group_units(blocks: list[_Element]) -> list[str]:
    """Glue each code block to the prose immediately before it, yielding
    'units' that are the smallest thing the packer is allowed to place. A prose
    paragraph starts a new unit; a code block appends to the current unit (or
    starts one if it leads a section). This is what enforces rule #2."""
    units: list[list[str]] = []
    for block in blocks:
        if block.is_code and units:
            units[-1].append(block.text)
        else:
            units.append([block.text])
    return ["\n\n".join(parts) for parts in units]


def _overlap_tail(units: list[str], budget_chars: int) -> list[str]:
    """Return the trailing units that fit within the overlap budget, to seed the
    next chunk with ~15% of the previous chunk's context."""
    tail: list[str] = []
    total = 0
    for unit in reversed(units):
        if total + len(unit) > budget_chars:
            break
        tail.insert(0, unit)
        total += len(unit)
    return tail


def _pack_units(
    units: list[str], heading_path: str, max_chars: int
) -> list[dict]:
    """Greedily pack units into chunks no larger than max_chars, carrying a
    small overlap across each seam. A single unit larger than max_chars (e.g. a
    big code example) becomes its own oversized chunk rather than being split —
    correctness of the code beats the size target."""
    chunks: list[dict] = []
    cur: list[str] = []
    cur_len = 0
    overlap_budget = int(OVERLAP_RATIO * max_chars)

    for unit in units:
        if cur and cur_len + len(unit) > max_chars:
            chunks.append({"content": "\n\n".join(cur), "heading_path": heading_path})
            cur = _overlap_tail(cur, overlap_budget)
            cur_len = sum(len(u) for u in cur)
        cur.append(unit)
        cur_len += len(unit) + 2  # +2 for the "\n\n" join

    if cur:
        chunks.append({"content": "\n\n".join(cur), "heading_path": heading_path})
    return chunks


def _merge_small(chunks: list[dict], min_chars: int) -> list[dict]:
    """Fold under-sized chunks up into the preceding chunk (their 'heading
    neighbor'), keeping that neighbor's heading path. A small leading chunk with
    nothing above it has nowhere to merge and is kept as-is."""
    out: list[dict] = []
    for chunk in chunks:
        if out and len(chunk["content"]) < min_chars:
            out[-1]["content"] += "\n\n" + chunk["content"]
        else:
            out.append(dict(chunk))
    return out


def chunk_markdown(
    text: str,
    source_url: str | None = None,
    doc_version: str | None = None,
    *,
    target_max_tokens: int = DEFAULT_TARGET_MAX_TOKENS,
    min_merge_tokens: int = DEFAULT_MIN_MERGE_TOKENS,
) -> list[dict]:
    """Chunk one markdown document. See module docstring for the invariants.

    target_max_tokens / min_merge_tokens are parameters (not constants) so the
    unit tests can force boundary and merge behavior deterministically.
    """
    max_chars = target_max_tokens * CHARS_PER_TOKEN
    min_chars = min_merge_tokens * CHARS_PER_TOKEN

    elements = _parse_elements(text)
    sections = _group_sections(elements)

    chunks: list[dict] = []
    for section in sections:
        units = _group_units(section.blocks)
        chunks.extend(_pack_units(units, section.heading_path, max_chars))

    chunks = _merge_small(chunks, min_chars)

    # Stamp shared metadata last so the packing/merging logic stays focused on
    # content and heading path only.
    for chunk in chunks:
        chunk["source_url"] = source_url
        chunk["doc_version"] = doc_version
    return chunks
