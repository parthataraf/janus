"""FastAPI docs ingestion.

Shallow-clones fastapi/fastapi at a pinned release tag, parses the English
markdown docs, and turns each file into chunks stamped with the source's live
docs URL and the release tag as doc_version. Returns chunk dicts ready for
embedding + storage (the corpus label is added by run_ingest).

Version pinning is deliberate: docs drift between releases, so an answer is
only trustworthy if we know exactly which version produced it — and the UI can
offer a version switcher on top of that.

Code includes: FastAPI's docs don't inline their code examples — each is pulled
in at build time by an MkDocs include directive of the form

    {* ../../docs_src/dependencies/tutorial001_an_py310.py hl[8] title["app.py"] *}

We resolve these before chunking: read the referenced file from the cloned
repo's docs_src/ tree and splice it in as a fenced code block, so the actual
example travels with its surrounding prose into the chunks (rule #2 of the
chunker). The directive path resolves the same way MkDocs resolves it — against
the mkdocs.yml directory (docs/en), so `../../docs_src/...` lands at the repo
root's docs_src/. We honor the directive's variants: `ln[a:b,c,...]` slices the
file to just those 1-based line ranges (FastAPI uses it to show a fragment),
`title[...]` becomes a filename caption, and `hl[...]` (highlight) is parsed but
intentionally dropped — it's a display concern that doesn't change code text.

HTML cleanup: the docs also carry raw HTML that would otherwise survive into
chunks as retrieval noise — layout wrappers (`<div class="termy">`), screenshots
(`<img>`), inline styling (`<span>`, `<strong>`), collapsibles (`<details>`), and
HTML tables. After include resolution and before chunking we strip it from the
PROSE only: fenced code blocks are left byte-for-byte intact, because the HTML
inside them (console colour spans, mermaid `<br/>`, HTML/Jinja snippets) is
example code, not markup to clean. Content-bearing tags are unwrapped (inner text
kept); `<script>`/`<style>`/`<noscript>`/`<iframe>` elements are dropped whole.
"""

from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path, PurePosixPath

from core.chunking import chunk_markdown

# The latest stable FastAPI release at time of writing. Overridable via the CLI.
DEFAULT_FASTAPI_VERSION = "0.139.0"
REPO_URL = "https://github.com/fastapi/fastapi.git"
# English docs live here; each file maps to a page under the docs site.
DOCS_SUBPATH = "docs/en/docs"
DOCS_BASE_URL = "https://fastapi.tiangolo.com/"

# A whole-line include directive: {* <path> <options> *}. Mirrors FastAPI's own
# parser (scripts/doc_parsing_utils.py: CODE_INCLUDE_RE) — group 1 is the file
# path (no spaces), group 2 is the option string (hl/ln/title, any order).
_INCLUDE_RE = re.compile(r"^\s*\{\*\s*(\S+)\s*(.*?)\s*\*\}\s*$")
# Option sub-patterns, matched anywhere in the option string so order is free.
_LN_RE = re.compile(r"ln\[([0-9:,\s]+)\]")
_TITLE_RE = re.compile(r"""title\[\s*["']([^"']*)["']\s*\]""")
# Fence language by source extension. FastAPI's includes are all Python today,
# but keep it a lookup so other corpora / file types degrade sensibly.
_EXT_TO_LANG = {
    ".py": "python",
    ".sh": "bash",
    ".bash": "bash",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".dockerfile": "dockerfile",
}


def _md_path_to_url(rel_path: str) -> str:
    """Map a docs-relative markdown path to its live URL.

    'tutorial/query-params.md'  -> 'https://fastapi.tiangolo.com/tutorial/query-params/'
    'tutorial/index.md'         -> 'https://fastapi.tiangolo.com/tutorial/'
    'index.md'                  -> 'https://fastapi.tiangolo.com/'
    """
    segments = list(PurePosixPath(rel_path).with_suffix("").parts)
    if segments and segments[-1] == "index":
        segments = segments[:-1]
    path = "/".join(segments)
    return f"{DOCS_BASE_URL}{path}/" if path else DOCS_BASE_URL


def _parse_line_ranges(spec: str) -> list[tuple[int, int]]:
    """Parse an `ln[...]` spec like "1:2,12:16,29" into inclusive 1-based
    (start, end) ranges. A bare number `n` becomes (n, n)."""
    ranges: list[tuple[int, int]] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if ":" in part:
            lo, _, hi = part.partition(":")
            try:
                start, end = int(lo), int(hi)
            except ValueError:
                continue
            if start > end:
                start, end = end, start
            ranges.append((start, end))
        else:
            try:
                n = int(part)
            except ValueError:
                continue
            ranges.append((n, n))
    return ranges


def _slice_lines(lines: list[str], ranges: list[tuple[int, int]]) -> list[str]:
    """Return the given 1-based inclusive line ranges, concatenated in order.
    Ranges are clamped to the file; this mirrors how MkDocs shows a fragment
    (the hidden lines are simply omitted, not gapped)."""
    out: list[str] = []
    n = len(lines)
    for start, end in ranges:
        lo = max(1, start)
        hi = min(n, end)
        if lo <= hi:
            out.extend(lines[lo - 1 : hi])
    return out


def _render_include(
    include_base: Path, repo_root: Path, rel_path: str, options: str
) -> str | None:
    """Resolve one directive to a fenced code block, or None if the referenced
    file can't be found (in which case the caller drops the directive rather
    than leaking raw `{* ... *}` markup into a chunk).

    `include_base` is the mkdocs.yml directory (docs/en); the directive path is
    resolved against it exactly as MkDocs does. The resolved path is confirmed
    to live inside the checkout before it's read (defense against a stray `..`)."""
    target = (include_base / rel_path).resolve()
    try:
        target.relative_to(repo_root.resolve())
    except ValueError:
        return None  # escaped the repo — refuse to read
    if not target.is_file():
        return None

    source = target.read_text(encoding="utf-8").splitlines()

    ln_match = _LN_RE.search(options)
    if ln_match:
        selected = _slice_lines(source, _parse_line_ranges(ln_match.group(1)))
    else:
        selected = source
    # `hl[...]` (highlighting) is deliberately ignored — display-only. Strip
    # trailing blank lines so the fenced block is tight.
    while selected and not selected[-1].strip():
        selected.pop()
    if not selected:
        return None

    lang = _EXT_TO_LANG.get(target.suffix.lower(), target.suffix.lstrip(".").lower())
    fence = "```"
    body = "\n".join(selected)
    block = f"{fence}{lang}\n{body}\n{fence}"

    title_match = _TITLE_RE.search(options)
    if title_match:
        # Caption line right before the fence. The chunker glues preceding prose
        # to the code, so the filename stays with its example (and is searchable).
        return f"`{title_match.group(1)}`\n\n{block}"
    return block


def _resolve_includes(
    text: str, include_base: Path, repo_root: Path
) -> tuple[str, int, int]:
    """Replace every include directive in `text` with its fenced code block.
    Returns (new_text, resolved_count, dropped_count)."""
    resolved = dropped = 0
    out_lines: list[str] = []
    for line in text.splitlines():
        m = _INCLUDE_RE.match(line)
        if not m:
            out_lines.append(line)
            continue
        rendered = _render_include(include_base, repo_root, m.group(1), m.group(2))
        if rendered is None:
            dropped += 1  # unresolved: drop the directive line entirely
            continue
        out_lines.append(rendered)
        resolved += 1
    return "\n".join(out_lines), resolved, dropped


# --- HTML cleanup (prose only; fenced code is never touched) --------------
# Fence detection mirrors core.chunking._parse_elements EXACTLY: open on this
# regex, normalize the marker to 3 chars, close on a line whose stripped form
# starts with that marker. Keeping the split identical to the chunker's means
# the prose we clean here is exactly the prose it will chunk.
_FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})")

_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
# Elements whose *contents* are code/embeds, not documentation prose: the whole
# element (open tag + body + close tag) is removed.
_HTML_ELEMENT_DROP_RE = re.compile(
    r"<(script|style|noscript|iframe)\b[^>]*>.*?</\1\s*>", re.IGNORECASE | re.DOTALL
)
# Known HTML tag names (a superset of everything seen in the corpus). Matching
# only real tag names leaves non-HTML angle-bracket text (a stray "<value>")
# alone. Every matched tag is unwrapped to a single space so its inner text
# survives without gluing onto its neighbours.
_HTML_TAG_NAMES = (
    "a|abbr|address|area|article|aside|audio|b|base|bdi|bdo|blockquote|br|button|"
    "canvas|caption|cite|code|col|colgroup|data|datalist|dd|del|details|dfn|dialog|"
    "div|dl|dt|em|embed|fieldset|figcaption|figure|font|footer|form|h1|h2|h3|h4|h5|"
    "h6|header|hr|i|iframe|img|input|ins|kbd|label|legend|li|main|map|mark|nav|"
    "noscript|object|ol|optgroup|option|output|p|param|picture|pre|progress|q|rp|rt|"
    "ruby|s|samp|script|section|select|small|source|span|strong|style|sub|summary|"
    "sup|svg|table|tbody|td|template|textarea|tfoot|th|thead|time|title|tr|track|u|"
    "ul|var|video|wbr"
)
_HTML_TAG_RE = re.compile(rf"</?\s*(?:{_HTML_TAG_NAMES})\b[^>]*>", re.IGNORECASE)
_MULTISPACE_RE = re.compile(r" {2,}")


def _clean_prose_html(block: str) -> tuple[str, int]:
    """Strip HTML from a run of NON-code markdown. Returns (clean_text, removed).

    Comments and script/style/noscript/iframe elements are dropped whole; every
    other HTML tag is unwrapped (inner text kept). Whitespace is then tidied
    WITHOUT disturbing leading indentation (markdown list nesting) or blank-line
    paragraph breaks (the chunker splits paragraphs on those)."""
    removed = (
        len(_HTML_COMMENT_RE.findall(block))
        + len(_HTML_ELEMENT_DROP_RE.findall(block))
        + len(_HTML_TAG_RE.findall(block))
    )
    block = _HTML_COMMENT_RE.sub("", block)
    block = _HTML_ELEMENT_DROP_RE.sub("", block)
    block = _HTML_TAG_RE.sub(" ", block)  # unwrap: keep inner text

    out: list[str] = []
    for line in block.split("\n"):
        stripped = line.lstrip(" ")
        indent = line[: len(line) - len(stripped)]
        line = indent + _MULTISPACE_RE.sub(" ", stripped).rstrip()
        if line == "" and (not out or out[-1] == ""):
            continue  # collapse blank runs, but keep single paragraph breaks
        out.append(line)
    while out and out[-1] == "":
        out.pop()
    return "\n".join(out), removed


def _clean_html(text: str) -> tuple[str, int]:
    """Remove raw HTML from markdown prose while leaving fenced code blocks
    byte-for-byte intact. Returns (clean_text, removed_count)."""
    out: list[str] = []
    pending: list[str] = []
    fence: str | None = None
    removed = 0

    def flush() -> None:
        nonlocal removed
        if pending:
            cleaned, n = _clean_prose_html("\n".join(pending))
            removed += n
            out.append(cleaned)
            pending.clear()

    for line in text.splitlines():
        if fence is not None:
            out.append(line)  # inside a fence: verbatim, never cleaned
            if line.strip().startswith(fence):
                fence = None
            continue
        m = _FENCE_RE.match(line)
        if m:
            flush()
            fence = m.group(1)[:3]
            out.append(line)
            continue
        pending.append(line)
    flush()
    return "\n".join(out), removed


def _clone(version: str, repo_url: str, dest: Path) -> None:
    """Shallow-clone a single tag — fast and small (no history, one commit)."""
    subprocess.run(
        ["git", "clone", "--depth", "1", "--branch", version, repo_url, str(dest)],
        check=True,
        capture_output=True,
        text=True,
    )


def load_chunks(
    version: str = DEFAULT_FASTAPI_VERSION,
    repo_url: str = REPO_URL,
    **chunk_kwargs,
) -> list[dict]:
    """Clone at `version`, parse every English doc, and return chunk dicts with
    keys {content, heading_path, source_url, doc_version}."""
    # ignore_cleanup_errors: git leaves read-only files under .git on Windows,
    # which would otherwise raise during temp-dir teardown.
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        repo = Path(tmp) / "fastapi"
        _clone(version, repo_url, repo)

        docs_dir = repo / DOCS_SUBPATH
        if not docs_dir.is_dir():
            raise FileNotFoundError(
                f"Expected docs at {DOCS_SUBPATH} in the {version} checkout; "
                "the layout may differ for this tag."
            )

        # MkDocs resolves include paths against the mkdocs.yml directory, which
        # is docs/en (one above the docs_dir). So `../../docs_src/...` in a
        # directive lands at the repo root's docs_src/.
        include_base = docs_dir.parent
        repo_root = repo

        chunks: list[dict] = []
        total_resolved = total_dropped = total_html = 0
        for md_file in sorted(docs_dir.rglob("*.md")):
            rel = md_file.relative_to(docs_dir).as_posix()
            url = _md_path_to_url(rel)
            text = md_file.read_text(encoding="utf-8")
            text, resolved, dropped = _resolve_includes(text, include_base, repo_root)
            # Strip raw HTML from prose AFTER includes are resolved (so spliced
            # code fences are already in place and protected) and BEFORE chunking.
            text, html_removed = _clean_html(text)
            total_resolved += resolved
            total_dropped += dropped
            total_html += html_removed
            chunks.extend(
                chunk_markdown(
                    text, source_url=url, doc_version=version, **chunk_kwargs
                )
            )
        print(
            f"  resolved {total_resolved} code includes "
            f"({total_dropped} unresolved / dropped)."
        )
        print(f"  stripped {total_html} raw-HTML tags/comments/elements from prose.")
        return chunks
