"""Retrieval evaluation.

Measures, per retrieval configuration, hit@k / MRR over a hand-curated test set,
plus faithfulness (LLM-as-judge, 1-5) on the best config's generated answers.
Writes a comparison table + per-config worst-5 error analysis to eval/results.md.

Nothing here mutates the pipeline: every configuration is composed from the
PUBLIC retrieval functions (vector_search / keyword_search / _rrf_merge / rerank)
with different parameters, so core defaults are untouched. The three tunables
(top_n 5 vs 8, a code-chunk rerank margin, release-notes exclusion) are applied
here in the harness only.

Configs
  vector-only     : dense cosine ranking
  hybrid          : vector + keyword fused with RRF
  hybrid+rerank   : hybrid -> cross-encoder rerank  (production base)
  +code-margin    : base, but chunks containing a fenced code block get a small
                    additive rerank margin (re-sorted)
  +release-excl   : base, but release-notes chunks are filtered out of the
                    candidate pool at query time (source_url ~ '/release-notes/'),
                    NOT deleted from the corpus
  top_n 5 vs 8    : same base ranking; reported as hit@5 (top_n=5) vs hit@8
                    (top_n=8), since top_n changes only the retrieved-set cutoff,
                    not the ranking

Scoring
  in-corpus (51)  : hit@k = an expected_url appears among the top-k retrieved
                    chunks; MRR over the first expected_url's rank.
  off-corpus (3)  : correct iff the refusal gate fires (top rerank score <
                    RERANK_THRESHOLD). The gate only exists for reranked configs;
                    vector-only / hybrid have no rerank score, so it's N/A there.
  faithfulness    : best config only; phi-4 judges each generated answer 1-5.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import sys
import time
from pathlib import Path

from core import config, generation, retrieval

CORPUS = "fastapi"
DOC_VERSION = "0.139.0"

# Faithfulness judge model — independent of the generator (config.GEN_MODEL) so a
# model never grades its own output (removes the self-judging caveat). Both are
# named in results.md for provenance. Override via the JUDGE_MODEL env var.
JUDGE_MODEL = os.environ.get("JUDGE_MODEL", "google/gemini-2.5-flash")

_HERE = Path(__file__).resolve().parent
TESTSET = _HERE / "testset_fastapi.jsonl"
JUDGE_PROMPT_FILE = _HERE / "judge_prompt.txt"
RESULTS = _HERE / "results.md"

LEG_K = 20          # per-leg candidate count (production hybrid default)
REL_LEG_K = 40      # wider pool so the release-notes filter still fills top-k
RRF_TRUNC = 20      # RRF output size fed into the reranker
CODE_MARGIN = 1.0   # additive rerank bump for fenced-code chunks (tunable)
TOP_N_GEN = 5       # chunks sent to generation / used for the refusal gate
K_LEVELS = (1, 3, 5, 8)

RETRIEVAL_CONFIGS = [
    "vector-only", "hybrid", "hybrid+rerank", "+code-margin", "+release-excl",
]
RERANKED_CONFIGS = {"hybrid+rerank", "+code-margin", "+release-excl"}


def log(msg: str) -> None:
    print(msg, flush=True)


# --------------------------------------------------------------------------- #
# Test set
# --------------------------------------------------------------------------- #
def load_testset(path: Path = TESTSET) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("#") or not line.strip():
            continue
        rows.append(json.loads(line))
    return rows


# --------------------------------------------------------------------------- #
# Configs -> ranked candidate lists
# --------------------------------------------------------------------------- #
def _is_release(cand) -> bool:
    return "/release-notes/" in (cand.source_url or "")


def _has_code(cand) -> bool:
    return "```" in (cand.content or "")


def _apply_code_margin(reranked: list) -> list:
    """Re-sort a reranked list, adding CODE_MARGIN to fenced-code chunks. Raw
    rerank_score is left intact (the margin is a ranking tweak only)."""
    def key(c):
        return (c.rerank_score or 0.0) + (CODE_MARGIN if _has_code(c) else 0.0)
    return sorted(reranked, key=key, reverse=True)


def rankings_for(question: str) -> dict[str, list]:
    """Return {config_name: ranked_candidate_list} for one question."""
    vec = retrieval.vector_search(question, CORPUS, k=LEG_K, doc_version=DOC_VERSION)
    kw = retrieval.keyword_search(question, CORPUS, k=LEG_K, doc_version=DOC_VERSION)

    out: dict[str, list] = {}
    out["vector-only"] = list(vec)  # already sorted by cosine similarity

    rrf = retrieval._rrf_merge([copy.deepcopy(vec), copy.deepcopy(kw)], k=RRF_TRUNC)
    out["hybrid"] = rrf

    base = retrieval.rerank(question, copy.deepcopy(rrf), top_n=RRF_TRUNC)
    out["hybrid+rerank"] = base

    out["+code-margin"] = _apply_code_margin(base)

    vec2 = [c for c in retrieval.vector_search(question, CORPUS, k=REL_LEG_K, doc_version=DOC_VERSION) if not _is_release(c)]
    kw2 = [c for c in retrieval.keyword_search(question, CORPUS, k=REL_LEG_K, doc_version=DOC_VERSION) if not _is_release(c)]
    rrf2 = retrieval._rrf_merge([vec2, kw2], k=RRF_TRUNC)
    out["+release-excl"] = retrieval.rerank(question, rrf2, top_n=RRF_TRUNC) if rrf2 else []
    return out


# --------------------------------------------------------------------------- #
# Metrics
# --------------------------------------------------------------------------- #
def score_ranking(expected_urls: list[str], ranked: list):
    """Return (hits_by_k, reciprocal_rank, first_rank)."""
    urls = [c.source_url for c in ranked]
    first_rank = None
    for i, u in enumerate(urls, start=1):
        if u in expected_urls:
            first_rank = i
            break
    hits = {k: (first_rank is not None and first_rank <= k) for k in K_LEVELS}
    rr = (1.0 / first_rank) if first_rank else 0.0
    return hits, rr, first_rank


def mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


# --------------------------------------------------------------------------- #
# Faithfulness judge
# --------------------------------------------------------------------------- #
# The judge must see what the GENERATOR saw. At 1200 the cap silently truncated
# the multi-row item passages once they carried per-item stat values (1231 chars
# for armor penetration), so items named at the end of an answer looked
# ungrounded and cost faithfulness points the answer had not actually lost.
def build_judge_context(chunks: list, cap: int = 4000) -> str:
    blocks = []
    for i, c in enumerate(chunks, start=1):
        content = (c.content or "")[:cap]
        heading = c.heading_path or "(untitled)"
        blocks.append(f"[{i}] {heading}\n{content}")
    return "\n\n".join(blocks)


def judge_faithfulness(template: str, question: str, chunks: list, answer: str):
    """Ask phi-4 to score faithfulness 1-5. Returns (score|None, raw_text)."""
    prompt = template.format(
        context=build_judge_context(chunks), question=question, answer=answer
    )
    resp = generation._get_client().chat.completions.create(
        model=JUDGE_MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
    )
    text = resp.choices[0].message.content or ""
    m = re.search(r"[1-5]", text)
    return (int(m.group()) if m else None), text.strip()


# --------------------------------------------------------------------------- #
# FastAPI eval (retrieval-config comparison)
# --------------------------------------------------------------------------- #
def run_fastapi_eval() -> int:
    t_start = time.perf_counter()
    rows = load_testset(TESTSET)
    in_corpus = [r for r in rows if r["type"] != "off_corpus"]
    off_corpus = [r for r in rows if r["type"] == "off_corpus"]
    log(f"Loaded {len(rows)} questions: {len(in_corpus)} in-corpus, "
        f"{len(off_corpus)} off-corpus.")
    log(f"Model (generation + judge): {config.GEN_MODEL} @ {config.OPENAI_BASE_URL}")
    log(f"RERANK_THRESHOLD={config.RERANK_THRESHOLD}  CODE_MARGIN={CODE_MARGIN}  "
        f"top_n(gen)={TOP_N_GEN}\n")

    # -- Step 1: retrieval metrics over in-corpus questions ------------------
    # per_config accumulates hits/rr; stored_rankings caches rankings for reuse.
    per_config = {c: {"hits": {k: [] for k in K_LEVELS}, "rr": []} for c in RETRIEVAL_CONFIGS}
    worst = {c: [] for c in RETRIEVAL_CONFIGS}  # (rr, first_rank, question, expected, top1)
    stored = []  # [(row, rankings)]

    log("=== Step 1/3: retrieval metrics (in-corpus) ===")
    for n, r in enumerate(in_corpus, start=1):
        q, exp = r["question"], r["expected_urls"]
        rankings = rankings_for(q)
        stored.append((r, rankings))
        for c in RETRIEVAL_CONFIGS:
            hits, rr, fr = score_ranking(exp, rankings[c])
            for k in K_LEVELS:
                per_config[c]["hits"][k].append(1.0 if hits[k] else 0.0)
            per_config[c]["rr"].append(rr)
            top1 = rankings[c][0].source_url if rankings[c] else None
            worst[c].append((rr, fr, q, exp, top1))
        log(f"  [{n:>2}/{len(in_corpus)}] {q[:64]}")

    # -- Step 2: off-corpus refusal (reranked configs) -----------------------
    log("\n=== Step 2/3: off-corpus refusal ===")
    off_results = {c: [] for c in RERANKED_CONFIGS}  # (question, top_score, refused)
    for n, r in enumerate(off_corpus, start=1):
        q = r["question"]
        rankings = rankings_for(q)
        for c in RERANKED_CONFIGS:
            chunks = rankings[c][:TOP_N_GEN]
            top = generation._top_rerank_score(chunks)
            refused = generation._should_refuse(chunks)
            off_results[c].append((q, top, refused))
        log(f"  [{n}/{len(off_corpus)}] {q[:64]}  "
            f"(base top={generation._top_rerank_score(rankings['hybrid+rerank'][:TOP_N_GEN]):+.2f})")

    # -- Aggregate + pick best (by hit@5, tie-break MRR) ---------------------
    summary = {}
    for c in RETRIEVAL_CONFIGS:
        summary[c] = {
            "hit": {k: mean(per_config[c]["hits"][k]) for k in K_LEVELS},
            "mrr": mean(per_config[c]["rr"]),
        }
    best = max(RETRIEVAL_CONFIGS, key=lambda c: (summary[c]["hit"][5], summary[c]["mrr"]))
    log(f"\nBest config by hit@5 (tie-break MRR): {best}  "
        f"(hit@5={summary[best]['hit'][5]:.3f}, MRR={summary[best]['mrr']:.3f})")

    # -- Step 3: generation + faithfulness on the best config ----------------
    log(f"\n=== Step 3/3: generation + faithfulness judge on '{best}' "
        f"(this is the slow LAN part) ===")
    judge_template = JUDGE_PROMPT_FILE.read_text(encoding="utf-8")
    faith_scores = []
    faith_detail = []  # (question, score, refused, err)
    for n, (r, rankings) in enumerate(stored, start=1):
        q = r["question"]
        chunks = rankings[best][:TOP_N_GEN]
        t0 = time.perf_counter()
        try:
            gen = generation.generate(q, chunks)
        except Exception as e:  # LAN hiccup: record and continue
            faith_detail.append((q, None, None, f"generate error: {e}"))
            log(f"  [{n:>2}/{len(stored)}] GEN FAIL {q[:48]} :: {e}")
            continue
        if gen["refused"]:
            faith_detail.append((q, None, True, None))
            log(f"  [{n:>2}/{len(stored)}] refused (in-corpus!) {q[:48]}")
            continue
        try:
            score, _raw = judge_faithfulness(judge_template, q, chunks, gen["answer"])
        except Exception as e:
            faith_detail.append((q, None, False, f"judge error: {e}"))
            log(f"  [{n:>2}/{len(stored)}] JUDGE FAIL {q[:48]} :: {e}")
            continue
        if score is not None:
            faith_scores.append(score)
        faith_detail.append((q, score, False, None))
        dt = time.perf_counter() - t0
        log(f"  [{n:>2}/{len(stored)}] faith={score}  ({dt:4.1f}s)  {q[:48]}")

    elapsed = time.perf_counter() - t_start
    write_results(summary, best, off_results, worst, faith_scores, faith_detail,
                  len(in_corpus), len(off_corpus), elapsed)
    log(f"\nWrote {RESULTS}  (total {elapsed/60:.1f} min)")
    return 0


def write_results(summary, best, off_results, worst, faith_scores, faith_detail,
                  n_in, n_off, elapsed):
    from datetime import date

    L = []
    A = L.append
    A("# Retrieval evaluation results\n")
    A(f"- **Date:** {date.today().isoformat()}")
    A(f"- **Corpus:** `{CORPUS}` @ `{DOC_VERSION}`  ·  **in-corpus questions:** "
      f"{n_in}  ·  **off-corpus:** {n_off}")
    A(f"- **Generation + faithfulness judge model:** `{config.GEN_MODEL}` "
      f"(phi-4-Q4_K_M, local llama.cpp)")
    A(f"- **Refusal threshold:** `{config.RERANK_THRESHOLD}`  ·  **code margin:** "
      f"`{CODE_MARGIN}`  ·  **top_n (generation):** `{TOP_N_GEN}`")
    A(f"- **Wall time:** {elapsed/60:.1f} min\n")

    # --- comparison table ---
    A("## Retrieval comparison\n")
    A("hit@k = an expected answer page appears in the top-k retrieved chunks; "
      "MRR over the first expected page's rank. Over the "
      f"{n_in} in-corpus questions.\n")
    A("| Config | hit@1 | hit@3 | hit@5 | hit@8 | MRR | off-corpus refusal |")
    A("|---|---|---|---|---|---|---|")
    for c in RETRIEVAL_CONFIGS:
        s = summary[c]
        if c in RERANKED_CONFIGS:
            refs = off_results[c]
            n_ref = sum(1 for _, _, ref in refs if ref)
            off_cell = f"{n_ref}/{len(refs)}"
        else:
            off_cell = "n/a (no gate)"
        star = " **←best**" if c == best else ""
        A(f"| `{c}`{star} | {s['hit'][1]*100:.1f}% | {s['hit'][3]*100:.1f}% | "
          f"{s['hit'][5]*100:.1f}% | {s['hit'][8]*100:.1f}% | {s['mrr']:.3f} | {off_cell} |")
    A("")
    A("**top_n 5 vs 8** (same `hybrid+rerank` ranking — top_n changes only the "
      "cutoff, not the order):")
    A(f"- top_n=5 operating point → hit@5 = **{summary['hybrid+rerank']['hit'][5]*100:.1f}%**")
    A(f"- top_n=8 operating point → hit@8 = **{summary['hybrid+rerank']['hit'][8]*100:.1f}%**")
    A(f"- Δ (pages recovered by widening the window to 8) = "
      f"**{(summary['hybrid+rerank']['hit'][8]-summary['hybrid+rerank']['hit'][5])*100:+.1f} pts**\n")

    # --- off-corpus detail ---
    A("## Off-corpus refusal detail\n")
    A("Correct iff the refusal gate fires (top rerank score < "
      f"`{config.RERANK_THRESHOLD}`). Top rerank score per config:\n")
    A("| Question | " + " | ".join(f"`{c}`" for c in sorted(RERANKED_CONFIGS)) + " |")
    A("|---|" + "|".join("---" for _ in RERANKED_CONFIGS) + "|")
    qs = [q for q, _, _ in off_results["hybrid+rerank"]]
    for i, q in enumerate(qs):
        cells = []
        for c in sorted(RERANKED_CONFIGS):
            _, top, ref = off_results[c][i]
            mark = "✓ refuse" if ref else "✗ ANSWER"
            cells.append(f"{top:+.2f} {mark}" if top is not None else "n/a")
        A(f"| {q} | " + " | ".join(cells) + " |")
    A("")

    # --- faithfulness ---
    A("## Faithfulness (LLM-as-judge)\n")
    judged = [s for s in faith_scores]
    n_ref = sum(1 for _, _, ref, _ in faith_detail if ref)
    n_err = sum(1 for _, _, _, err in faith_detail if err)
    A(f"Judged the **best config (`{best}`)** generated answers only, scored 1-5 "
      f"by `{config.GEN_MODEL}` (phi-4-Q4_K_M).\n")
    if judged:
        dist = {k: judged.count(k) for k in range(1, 6)}
        A(f"- **Mean faithfulness:** **{mean(judged):.2f} / 5** over {len(judged)} "
          f"judged answers.")
        A(f"- **Score distribution:** " + ", ".join(f"{k}★×{dist[k]}" for k in range(5, 0, -1)))
    else:
        A("- No answers were judged (all refused or errored).")
    if n_ref:
        A(f"- {n_ref} in-corpus question(s) were *refused* by the best config "
          f"(below threshold) — no answer to judge; see worst-5.")
    if n_err:
        A(f"- {n_err} question(s) hit a generation/judge error on the LAN server "
          f"(excluded from the mean).")
    A("")
    low = sorted((d for d in faith_detail if d[1] is not None), key=lambda d: d[1])[:5]
    if low:
        A("Lowest-faithfulness answers (for review):\n")
        A("| Score | Question |")
        A("|---|---|")
        for q, sc, _, _ in low:
            A(f"| {sc} | {q} |")
        A("")

    # --- worst-5 per config ---
    A("## Worst-5 questions per config (error analysis)\n")
    A("Ranked by reciprocal rank (misses first). \"rank\" = position of the first "
      "expected page in that config's ranking; \"—\" = not retrieved (top-20 miss).\n")
    for c in RETRIEVAL_CONFIGS:
        A(f"### `{c}`\n")
        A("| rank | Question | Expected | Top-1 retrieved |")
        A("|---|---|---|---|")
        worst_sorted = sorted(worst[c], key=lambda w: (w[0], (w[1] or 999)))[:5]
        for rr, fr, q, exp, top1 in worst_sorted:
            rank_s = str(fr) if fr else "—"
            exp_s = ", ".join(u.replace("https://fastapi.tiangolo.com", "") for u in exp)
            top_s = (top1 or "").replace("https://fastapi.tiangolo.com", "") or "—"
            A(f"| {rank_s} | {q} | {exp_s} | {top_s} |")
        A("")

    A("---")
    A("*Retrieval pipeline defaults unchanged; all config variation applied in the "
      "eval harness. Faithfulness scored by phi-4-Q4_K_M on the local llama.cpp "
      "endpoint (a small quantized judge — treat scores as directional).*")

    RESULTS.write_text("\n".join(L), encoding="utf-8")


# --------------------------------------------------------------------------- #
# LoL eval (single production pipeline: route() = structured + prose)
# --------------------------------------------------------------------------- #
_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")


def _numbers(text: str) -> list[float]:
    return [float(x) for x in _NUM_RE.findall(text or "")]


def _value_present(value, text: str, tol: float) -> bool:
    """Does the expected numeric value appear in the text within tolerance?"""
    if value is None or isinstance(value, list):
        return False
    return any(abs(n - float(value)) <= tol + 1e-9 for n in _numbers(text))


def _items_named(text: str, vocab: set[str]) -> set[str]:
    low = (text or "").lower()
    return {name for name in vocab if name.lower() in low}


def _recall(expected: list[str], named: set[str]) -> float:
    exp = set(expected)
    return len(exp & named) / len(exp) if exp else 0.0


def run_lol_eval(testset_path: Path) -> int:
    from core import lol_routing  # noqa: F401  (ensures routing importable)

    from core import store

    t_start = time.perf_counter()
    rows = load_testset(testset_path)
    patch = (store.lol_patches() or [None])[-1]
    vocab = set(store.lol_entity_names(patch)["items"]) if patch else set()
    judge_template = JUDGE_PROMPT_FILE.read_text(encoding="utf-8")

    log(f"LoL eval: {len(rows)} questions, patch {patch}, generator {config.GEN_MODEL}, "
        f"judge {JUDGE_MODEL}")

    buckets = {"prose": [], "numeric": [], "multi_row": [], "off_corpus": []}
    faith_scores: list[int] = []

    for n, r in enumerate(rows, start=1):
        q, typ = r["question"], r["type"]
        chunks = retrieval.route(q, "lol", top_n=TOP_N_GEN)
        ctx = " ".join(c.content or "" for c in chunks)
        try:
            gen = generation.generate(q, chunks)
        except Exception as e:
            log(f"  [{n:>2}] GEN FAIL {q[:44]} :: {e}")
            gen = {"refused": True, "answer": "", "citations": []}
        ans = "" if gen["refused"] else gen["answer"]

        if typ == "prose":
            hits, rr, _ = score_ranking(r["expected_urls"], chunks)
            buckets["prose"].append({"q": q, "hits": hits, "rr": rr})
        elif typ == "numeric":
            val, tol = r.get("value"), float(r.get("tolerance", 0))
            buckets["numeric"].append({
                "q": q, "value": val,
                "context_ok": _value_present(val, ctx, tol),
                "answer_ok": (not gen["refused"]) and _value_present(val, ans, tol),
            })
        elif typ == "multi_row":
            exp = r.get("items", [])
            buckets["multi_row"].append({
                "q": q, "min_recall": float(r.get("min_recall", 1.0)),
                "context_recall": _recall(exp, _items_named(ctx, vocab)),
                "answer_recall": _recall(exp, _items_named(ans, vocab)),
            })
        elif typ == "off_corpus":
            buckets["off_corpus"].append({"q": q, "refused": gen["refused"]})

        # faithfulness on any answered (non-refused, in-corpus) question
        sc = None
        if typ != "off_corpus" and not gen["refused"]:
            try:
                sc, _ = judge_faithfulness(judge_template, q, chunks, ans)
                if sc is not None:
                    faith_scores.append(sc)
            except Exception:
                pass
        # Log the per-question score, as the fastapi path does. Without it a mean
        # of 4.93 is unattributable — you cannot tell which answer drifted, which
        # is the only thing that makes the number actionable.
        mark = f"faith={sc}" if sc is not None else "faith=-"
        log(f"  [{n:>2}/{len(rows)}] {typ:9} {mark:8} {q[:48]}")

    _append_lol_results(testset_path, patch, buckets, faith_scores,
                        time.perf_counter() - t_start)
    log(f"\nAppended LoL section to {RESULTS}")
    return 0


def _append_lol_results(testset_path, patch, buckets, faith_scores, elapsed):
    from datetime import date
    out = []
    def A(s=""):
        out.append(s)

    A("\n\n---\n")
    A(f"# LoL evaluation — `lol` @ `{patch}`\n")
    A(f"- **Date:** {date.today().isoformat()}  ·  **test set:** `{testset_path.name}`  ·  "
      f"**pipeline:** production `route()` (structured + prose)")
    A(f"- **Generator:** `{config.GEN_MODEL}`  ·  **Faithfulness judge:** `{JUDGE_MODEL}` "
      f"(independent family)  ·  **wall time:** {elapsed/60:.1f} min\n")

    pr = buckets["prose"]
    if pr:
        A("## Prose (mechanics/lore) — URL-hit scored\n")
        A("| n | hit@1 | hit@3 | hit@5 | MRR |")
        A("|---|---|---|---|---|")
        A(f"| {len(pr)} | {mean([1.0 if x['hits'][1] else 0 for x in pr])*100:.1f}% | "
          f"{mean([1.0 if x['hits'][3] else 0 for x in pr])*100:.1f}% | "
          f"{mean([1.0 if x['hits'][5] else 0 for x in pr])*100:.1f}% | "
          f"{mean([x['rr'] for x in pr]):.3f} |\n")

    nu = buckets["numeric"]
    if nu:
        aa = mean([1.0 if x["answer_ok"] else 0 for x in nu])
        ca = mean([1.0 if x["context_ok"] else 0 for x in nu])
        A("## Numeric (structured) — answer-level vs context-level\n")
        A("Answer-level = the model *stated* the right number; context-level = the "
          "right number was *retrieved*. The gap is structured-path generation fidelity.\n")
        A("| n | answer-accuracy | context-accuracy | gap |")
        A("|---|---|---|---|")
        A(f"| {len(nu)} | **{aa*100:.1f}%** | {ca*100:.1f}% | {(ca-aa)*100:+.1f} pts |\n")
        misses = [x["q"] for x in nu if not x["answer_ok"]]
        if misses:
            A("Answer-level misses: " + "; ".join(misses[:6]) + "\n")

    mr = buckets["multi_row"]
    if mr:
        ar = mean([x["answer_recall"] for x in mr])
        cr = mean([x["context_recall"] for x in mr])
        correct = mean([1.0 if x["answer_recall"] >= x["min_recall"] else 0 for x in mr])
        A("## Multi-row (item sets) — recall\n")
        A("| n | answer-recall | context-recall | pass rate (≥ min_recall) |")
        A("|---|---|---|---|")
        A(f"| {len(mr)} | **{ar*100:.1f}%** | {cr*100:.1f}% | {correct*100:.1f}% |\n")

    oc = buckets["off_corpus"]
    if oc:
        nref = sum(1 for x in oc if x["refused"])
        A(f"## Off-corpus refusal\n\n**{nref}/{len(oc)}** refused correctly.\n")

    if faith_scores:
        A(f"## Faithfulness\n\nMean **{mean(faith_scores):.2f} / 5** over "
          f"{len(faith_scores)} answered questions (judge: `{JUDGE_MODEL}`, independent family).\n")

    A("*Numeric labels derive from and are auto-verified against the ingested "
      "`lol_*` tables; see `evaluate.py --refresh-labels`.*")

    with RESULTS.open("a", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")


# --------------------------------------------------------------------------- #
# Label refresh (golden-set maintenance on a corpus version bump)
# --------------------------------------------------------------------------- #
def refresh_labels(testset_path: Path, corpus: str, apply: bool) -> int:
    from core import store
    from eval import lol_labels

    patch = lol_labels.latest_patch()
    lines = testset_path.read_text(encoding="utf-8").splitlines()
    changed, dead, out_lines = [], [], []

    for line in lines:
        if line.startswith("#") or not line.strip():
            out_lines.append(line)
            continue
        r = json.loads(line)
        typ = r.get("type")

        if typ in ("numeric", "multi_row") and "answer" in r:
            new = lol_labels.derive(r["answer"], patch)
            key = "items" if typ == "multi_row" else "value"
            old = r.get(key)
            if new is None:
                dead.append((r["question"], f"lookup failed ({r['answer']})"))
            elif (sorted(new) if typ == "multi_row" else new) != (
                    sorted(old) if (typ == "multi_row" and old) else old):
                changed.append((r["question"], key, old, new))
                if apply:
                    r[key] = new
        elif r.get("expected_urls"):
            for url in r["expected_urls"]:
                if not store.chunk_url_exists(corpus, url):
                    dead.append((r["question"], f"dead expected_url: {url}"))

        out_lines.append(json.dumps(r, ensure_ascii=False))

    # Report — always print the before/after of every change, even when applying.
    log(f"=== refresh-labels: {testset_path.name} (corpus={corpus}, patch={patch}, "
        f"{'APPLY' if apply else 'report-only'}) ===")
    log(f"\n{'AUTO-REFRESHED' if apply else 'NUMERIC/MULTI_ROW CHANGED'} "
        f"({len(changed)}):")
    for q, key, old, new in changed:
        log(f"  {key}: {old!r} -> {new!r}   | {q}")
    log(f"\nDEAD / BROKEN — human decides delete-or-replace ({len(dead)}):")
    for q, why in dead:
        log(f"  {why}   | {q}")
    if not changed and not dead:
        log("  (nothing changed; labels still match the ingested tables)")

    if apply and changed:
        testset_path.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
        log(f"\nApplied {len(changed)} numeric/multi_row refresh(es) to {testset_path.name}. "
            f"Dead/broken prose entries left untouched (flagged above).")
    return 0


# --------------------------------------------------------------------------- #
# Dispatch
# --------------------------------------------------------------------------- #
def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Janus retrieval evaluation.")
    p.add_argument("--corpus", default="fastapi", choices=["fastapi", "lol"])
    p.add_argument("--testset", default=None, help="path to a testset .jsonl")
    p.add_argument("--refresh-labels", action="store_true",
                   help="re-derive numeric labels from the tables + check prose URLs; report a diff")
    p.add_argument("--apply", action="store_true",
                   help="with --refresh-labels: auto-apply numeric/multi_row refreshes (prose stays flag-only)")
    args = p.parse_args(argv)

    default_testset = TESTSET if args.corpus == "fastapi" else _HERE / "testset_lol.jsonl"
    testset = Path(args.testset) if args.testset else default_testset

    if args.refresh_labels:
        return refresh_labels(testset, args.corpus, args.apply)
    if args.corpus == "lol":
        return run_lol_eval(testset)
    return run_fastapi_eval()


if __name__ == "__main__":
    sys.exit(main())
