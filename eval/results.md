# Phase 2e — retrieval evaluation results

- **Date:** 2026-07-18
- **Corpus:** `fastapi` @ `0.139.0`  ·  **in-corpus questions:** 51  ·  **off-corpus:** 3
- **Generation + faithfulness judge model:** `phi-4-Q4_K_M.gguf` (phi-4-Q4_K_M, local llama.cpp)
- **Refusal threshold:** `0.0`  ·  **code margin:** `1.0`  ·  **top_n (generation):** `5`
- **Wall time:** 11.2 min

## Retrieval comparison

hit@k = an expected answer page appears in the top-k retrieved chunks; MRR over the first expected page's rank. Over the 51 in-corpus questions.

| Config | hit@1 | hit@3 | hit@5 | hit@8 | MRR | off-corpus refusal |
|---|---|---|---|---|---|---|
| `vector-only` | 72.5% | 90.2% | 96.1% | 96.1% | 0.814 | n/a (no gate) |
| `hybrid` | 66.7% | 90.2% | 94.1% | 96.1% | 0.779 | n/a (no gate) |
| `hybrid+rerank` | 66.7% | 92.2% | 96.1% | 96.1% | 0.797 | 3/3 |
| `+code-margin` | 66.7% | 92.2% | 94.1% | 96.1% | 0.792 | 3/3 |
| `+release-excl` **←best** | 74.5% | 92.2% | 96.1% | 96.1% | 0.837 | 3/3 |

**top_n 5 vs 8** (same `hybrid+rerank` ranking — top_n changes only the cutoff, not the order):
- top_n=5 operating point → hit@5 = **96.1%**
- top_n=8 operating point → hit@8 = **96.1%**
- Δ (pages recovered by widening the window to 8) = **+0.0 pts**

## Off-corpus refusal detail

Correct iff the refusal gate fires (top rerank score < `0.0`). Top rerank score per config:

| Question | `+code-margin` | `+release-excl` | `hybrid+rerank` |
|---|---|---|---|
| How do I bake chocolate chip cookies? | -9.94 ✓ refuse | -9.94 ✓ refuse | -9.94 ✓ refuse |
| How do I center a div in CSS? | -6.22 ✓ refuse | -6.22 ✓ refuse | -6.22 ✓ refuse |
| How do I train a neural network in PyTorch? | -10.83 ✓ refuse | -9.88 ✓ refuse | -10.83 ✓ refuse |

## Faithfulness (LLM-as-judge)

Judged the **best config (`+release-excl`)** generated answers only, scored 1-5 by `phi-4-Q4_K_M.gguf` (phi-4-Q4_K_M).

- **Mean faithfulness:** **4.39 / 5** over 49 judged answers.
- **Score distribution:** 5★×20, 4★×28, 3★×1, 2★×0, 1★×0
- 2 in-corpus question(s) were *refused* by the best config (below threshold) — no answer to judge; see worst-5.

Lowest-faithfulness answers (for review):

| Score | Question |
|---|---|
| 3 | How do I add a custom header to every response? |
| 4 | What is dependency injection in FastAPI and why would I use Depends? |
| 4 | What does the response_model parameter actually do to my endpoint's output? |
| 4 | Show me the code to declare a response_model on a path operation. |
| 4 | What are background tasks and when should I use them instead of a real queue? |

## Worst-5 questions per config (error analysis)

Ranked by reciprocal rank (misses first). "rank" = position of the first expected page in that config's ranking; "—" = not retrieved (top-20 miss).

### `vector-only`

| rank | Question | Expected | Top-1 retrieved |
|---|---|---|---|
| — | How do I require a logged-in user on an endpoint with OAuth2? | /tutorial/security/get-current-user/ | /tutorial/security/oauth2-jwt/ |
| — | How do I add a custom header to every response? | /tutorial/middleware/ | /reference/responses/ |
| 5 | How do I write tests for my endpoints with TestClient? | /tutorial/testing/ | /release-notes/ |
| 4 | Show me the code to inject a shared dependency into a path operation with Depends. | /tutorial/dependencies/ | /tutorial/dependencies/dependencies-in-path-operation-decorators/ |
| 4 | How do I add query parameters to a path operation? | /tutorial/query-params/ | /tutorial/path-params/ |

### `hybrid`

| rank | Question | Expected | Top-1 retrieved |
|---|---|---|---|
| — | How do I require a logged-in user on an endpoint with OAuth2? | /tutorial/security/get-current-user/ | /tutorial/security/oauth2-jwt/ |
| — | How do I add a custom header to every response? | /tutorial/middleware/ | /reference/responses/ |
| 8 | How do I add query parameters to a path operation? | /tutorial/query-params/ | /release-notes/ |
| 5 | How do I write tests for my endpoints with TestClient? | /tutorial/testing/ | /release-notes/ |
| 4 | Show me the code to inject a shared dependency into a path operation with Depends. | /tutorial/dependencies/ | /tutorial/dependencies/dependencies-in-path-operation-decorators/ |

### `hybrid+rerank`

| rank | Question | Expected | Top-1 retrieved |
|---|---|---|---|
| — | How do I require a logged-in user on an endpoint with OAuth2? | /tutorial/security/get-current-user/ | /tutorial/security/first-steps/ |
| — | How do I add a custom header to every response? | /tutorial/middleware/ | /tutorial/handling-errors/ |
| 4 | Show me the code to declare a response_model on a path operation. | /tutorial/response-model/ | /advanced/path-operation-advanced-configuration/ |
| 4 | response_model — what is it for and how do I set it? | /tutorial/response-model/ | /advanced/response-headers/ |
| 3 | How do I return a custom error response with a specific status code? | /tutorial/handling-errors/, /tutorial/response-status-code/ | /advanced/additional-responses/ |

### `+code-margin`

| rank | Question | Expected | Top-1 retrieved |
|---|---|---|---|
| — | How do I require a logged-in user on an endpoint with OAuth2? | /tutorial/security/get-current-user/ | /tutorial/security/first-steps/ |
| — | How do I add a custom header to every response? | /tutorial/middleware/ | /tutorial/handling-errors/ |
| 6 | Show me the code to declare a response_model on a path operation. | /tutorial/response-model/ | /advanced/path-operation-advanced-configuration/ |
| 4 | response_model — what is it for and how do I set it? | /tutorial/response-model/ | /advanced/response-headers/ |
| 3 | How do I return a custom error response with a specific status code? | /tutorial/handling-errors/, /tutorial/response-status-code/ | /advanced/additional-responses/ |

### `+release-excl`

| rank | Question | Expected | Top-1 retrieved |
|---|---|---|---|
| — | How do I require a logged-in user on an endpoint with OAuth2? | /tutorial/security/get-current-user/ | /tutorial/security/first-steps/ |
| — | How do I add a custom header to every response? | /tutorial/middleware/ | /tutorial/handling-errors/ |
| 4 | Show me the code to declare a response_model on a path operation. | /tutorial/response-model/ | /advanced/path-operation-advanced-configuration/ |
| 4 | response_model — what is it for and how do I set it? | /tutorial/response-model/ | /advanced/response-headers/ |
| 3 | How do I return a custom error response with a specific status code? | /tutorial/handling-errors/, /tutorial/response-status-code/ | /advanced/additional-responses/ |

---
*Retrieval pipeline defaults unchanged; all config variation applied in the eval harness. Faithfulness scored by phi-4-Q4_K_M on the local llama.cpp endpoint (a small quantized judge — treat scores as directional).*

## Keyword-leg decomposition — release-notes exclusion without rerank

Report-only follow-up. All four configs share the same **k=40** candidate pool and use **no reranker**, so differences isolate (a) the release-notes filter and (b) the keyword leg. Over the 51 in-corpus questions. (These bases are recomputed at k=40, so they differ slightly from the main table's k=20 bases.)

| Config | hit@1 | hit@3 | hit@5 | hit@8 | MRR |
|---|---|---|---|---|---|
| `vector-only (k=40)` | 72.5% | 90.2% | 96.1% | 96.1% | 0.815 |
| `vector-only + release-excl` | 76.5% | 90.2% | 96.1% | 96.1% | 0.845 |
| `hybrid (k=40)` | 66.7% | 90.2% | 94.1% | 94.1% | 0.782 |
| `hybrid + release-excl` | 74.5% | 90.2% | 94.1% | 96.1% | 0.836 |

**Reading it:**
- Exclusion on **vector-only**: hit@1 +3.9 pts, MRR +0.030.
- Exclusion on **hybrid**: hit@1 +7.8 pts, MRR +0.054.
- **Does the keyword leg add value after exclusion?** hybrid+excl vs vector-only+excl: hit@1 -2.0 pts, hit@3 +0.0, hit@5 -2.0, MRR -0.009.

## Promotion — release-notes exclusion is now the default

Adopted 2026-07-18. `core/retrieval.py` now filters release-notes chunks at query
time by default (`DEFAULT_EXCLUDE_URL_SUBSTR = "/release-notes/"`; each leg
oversamples 2x so the kept pool stays full; pass `exclude_url_substr=None` to
disable, e.g. for eval baselines). Re-running the best-config row through the real
`retrieve()` default reproduces the eval row exactly:

| Source | hit@1 | hit@3 | hit@5 | hit@8 | MRR | off-corpus |
|---|---|---|---|---|---|---|
| default `retrieve()` (excluded) | 74.5% | 92.2% | 96.1% | 96.1% | 0.837 | 3/3 |
| eval `+release-excl` row | 74.5% | 92.2% | 96.1% | 96.1% | 0.837 | 3/3 |

vs the prior `hybrid+rerank` default: **hit@1 +7.8 pts, MRR +0.040, no metric
degraded**. **Not adopted** per the results above: the code-chunk rerank margin
(net-hurt at 1.0) and top_n=8 (no gain on this set).

---

# LoL evaluation — `lol` @ `16.14.1` (MiniLM-384 baseline, superseded)

> Kept as the **before** side of the embedding migration below. Production
> moved to text-embedding-3-small on 2026-07-27; the current numbers are in
> "LoL evaluation (production, text-embedding-3-small)".

- **Date:** 2026-07-25  ·  **test set:** `testset_lol.jsonl`  ·  **pipeline:** production `route()` (structured + prose)
- **Generator:** `openai/gpt-4o-mini`  ·  **Faithfulness judge:** `google/gemini-2.5-flash` (independent family)  ·  **wall time:** 1.1 min

## Prose (mechanics/lore) — URL-hit scored

| n | hit@1 | hit@3 | hit@5 | MRR |
|---|---|---|---|---|
| 8 | 100.0% | 100.0% | 100.0% | 1.000 |

## Numeric (structured) — answer-level vs context-level

Answer-level = the model *stated* the right number; context-level = the right number was *retrieved*. The gap is structured-path generation fidelity.

| n | answer-accuracy | context-accuracy | gap |
|---|---|---|---|
| 17 | **100.0%** | 100.0% | +0.0 pts |

## Multi-row (item sets) — recall

| n | answer-recall | context-recall | pass rate (≥ min_recall) |
|---|---|---|---|
| 4 | **100.0%** | 100.0% | 100.0% |

**Prompt fix (adopted).** The initial run of this eval scored **answer-recall 73.9%
/ pass 50%**: retrieval surfaced every item (context-recall 100%), but the
generator *abbreviated* long lists — armor-pen named 2 of 5, omnivamp 5 of 9. A
scoped **exhaustive-enumeration** instruction was added to the generation system
prompt (*"if the context provides a LIST/SET, name EVERY item; no
summarizing / 'and others'"*), leaving the grounding, citation, and refusal rules
untouched. Re-running the 4 multi-row questions: **answer-recall 73.9% → 100.0%,
pass 50% → 100%**, all items enumerated with citations intact. A guard check
(2 numeric + 2 prose) confirmed **no change** to exact-number answers or prose
citations/ranks, so the numeric / prose / off-corpus / faithfulness rows above
still hold. See commit *"Fix multi-row abbreviation via exhaustive-enumeration
prompt instruction."*

## Off-corpus refusal

**3/3** refused correctly.

## Faithfulness

Mean **4.93 / 5** over 29 answered questions (judge: `google/gemini-2.5-flash`, independent family).

*Numeric labels derive from and are auto-verified against the ingested `lol_*` tables; see `evaluate.py --refresh-labels`.*

---

# Embedding comparison (experimental) — audited five-way re-run

> **STATUS: EXPERIMENTAL — NOT ADOPTED.** Report only. No production default was
> changed, no `core/` code touched, no production data re-ingested. Every arm was
> built in its own **scratch table**, all of which were **dropped** at the end of
> the run. Adoption remains an owner decision, to be made from this table.

> ⚠️ **CORRECTION — the 2026-07-25 result on this page was a methodology
> artifact and has been retracted.** That run reported MiniLM-384 *beating*
> `text-embedding-3-small` by −5.9 / −7.8 pts hit@1. The cause was an **input
> asymmetry**, found by audit and fixed here: production ingestion embeds
> `heading_path + "\n\n" + content` (`ingestion/run_ingest.py::_augment_for_embedding`),
> but the bake-off fed the API arm **bare `content`**. 99.7% of fastapi chunks
> carry a heading path, and the code comment beside that function calls it
> "strong signal" — so arm B was scored without the single most discriminating
> feature arm A had. **With byte-identical input, the result reverses: 3-small
> goes 66.7% → 82.4% hit@1, from 5.9 pts behind MiniLM to 9.9 pts ahead.** The
> superseded numbers are preserved in git history (commit `0932d06`).

- **Date:** 2026-07-27 · **test bed:** `eval/testset_fastapi.jsonl` (51 in-corpus
  questions) · **corpus:** `fastapi` @ `0.139.0` (1582 chunks) · dense-retrieval
  only — no keyword leg, no reranker, no generation.

## Phase A — audit of the 2026-07-25 run

| # | Failure mode checked | Result |
|---|---|---|
| 1 | **Batch order** — is API `data` sorted by `index` before pairing to chunks? | ✅ **PASS** — `sorted(d["data"], key=lambda x: x["index"])` in both scripts. The prime suspect was **not** the cause. |
| 2 | **Failed / truncated calls** — zero, null, non-finite vectors | ⚠️ **UNVERIFIABLE** — retries existed and hard errors raised, so silent corruption was unlikely, but the run **never validated vector health** and dropped its table, so it cannot be checked after the fact. Now validated per-arm (all clean). |
| 3 | **Distance operator** — cosine, consistent with the MiniLM path | ✅ **PASS** — `<=>` on the scratch table; `store.search_chunks` uses `<=>` too. MiniLM vectors are unit-norm (`normalize_embeddings=True`), OpenAI's are ~unit; cosine makes norm irrelevant regardless. |
| 4 | **Invariant #1** — same model embeds queries and docs, per arm | ✅ **PASS** — arm A: `embed_query` (MiniLM) → production `chunks` (MiniLM docs). Arm B: OpenRouter 3-small → scratch table (3-small docs). No cross-contamination. |
| 5 | **Params** — no `dimensions` truncation, correct input format, **chunk text byte-identical to what MiniLM received** | ❌ **FAIL** — no `dimensions` truncation ✅ and input format was correct ✅, but the text was **not** identical: MiniLM had `heading_path + content`, 3-small got `content` only. **This is the defect.** |

Two further defects surfaced while rebuilding the rig:

- **OpenRouter tunnels upstream failures as HTTP 200 with an `{"error": …}` body.**
  Status-code-only retry logic is blind to them — a 429 "engine is currently
  overloaded" arrives as a `200`. The old script would have crashed on `KeyError:
  'data'`, not silently corrupted, so the published run is unaffected; the new
  rig retries on the inner code.
- **`bge-m3` has a hard 8192-token ceiling.** One chunk (25,012 chars) exceeds it.
  The rerun therefore applies a **uniform 20,000-char cap to every arm** so all
  five receive byte-identical input; it clips exactly **1 of 1582 chunks (0.06%)**.

## Phase B — per-space sanity checks (all five passed; none was scored blind)

| Space | (a) self-similarity | (c) known-pairs | (b) top-5 for *"how do I declare a query parameter"* | Verdict |
|---|---|---|---|---|
| all-MiniLM-L6-v2 | 1.000000 | 5/5, margin +0.482 | **Header** Params, **Cookie** Params, Query Params & Validations, … | ✅ PASS (weakest neighbours) |
| bge-base-en-v1.5 | 1.000000 | 5/5, margin +0.267 | 5/5 Query-Parameter pages | ✅ PASS |
| text-embedding-3-small | 1.000000 | 5/5, margin +0.349 | 4/5 Query-Parameter pages | ✅ PASS |
| text-embedding-3-large | 1.000000 | 5/5, margin +0.413 | 4/5 Query-Parameter pages | ✅ PASS |
| bge-m3 | 0.999999 | 5/5, margin +0.236 | 5/5 Query-Parameter pages | ✅ PASS |

Vector health, all arms: **0 all-zero, 0 non-finite, 1582/1582 rows**. Pairing
integrity — 12 chunks per arm re-embedded individually and compared to the stored
vector — **min cos = 0.999973**, i.e. the misordering failure mode is directly
excluded, not merely assumed absent.

The eyeball test is already informative: MiniLM ranks *Header Parameters* and
*Cookie Parameters* above every Query-Parameter page.

## Phase C — results (51 questions, identical scoring code, identical input text)

**vector-only**

| Embedding space | dim | hit@1 | hit@3 | hit@5 | MRR | q-embed latency (mean/med) | corpus embed cost |
|---|---|---|---|---|---|---|---|
| all-MiniLM-L6-v2 (local) | 384 | 72.5% | 90.2% | **96.1%** | 0.815 | **17 / 13 ms** | **$0** (50 s) |
| bge-base-en-v1.5 (local) | 768 | 82.4% | 92.2% | 94.1% | 0.881 | 68 / 62 ms | **$0** (386 s) |
| text-embedding-3-small | 1536 | 82.4% | 92.2% | 94.1% | 0.882 | 822 / 719 ms | $0.0094 |
| **text-embedding-3-large** | 3072 | **86.3%** | 92.2% | **96.1%** | **0.904** | 765 / 728 ms | $0.0610 |
| bge-m3 | 1024 | 78.4% | 92.2% | 94.1% | 0.845 | 1107 / 755 ms | $0.0059 |

**vector-only + release-notes exclusion** (production's dense config)

| Embedding space | dim | hit@1 | hit@3 | hit@5 | MRR |
|---|---|---|---|---|---|
| all-MiniLM-L6-v2 (local) | 384 | 76.5% | 90.2% | **96.1%** | 0.845 |
| bge-base-en-v1.5 (local) | 768 | 84.3% | 92.2% | 94.1% | 0.891 |
| text-embedding-3-small | 1536 | 82.4% | 92.2% | 94.1% | 0.882 |
| **text-embedding-3-large** | 3072 | **86.3%** | 92.2% | **96.1%** | **0.904** |
| bge-m3 | 1024 | 82.4% | 92.2% | 94.1% | 0.871 |

*Baseline sanity: MiniLM reproduces **72.5% / 0.815** vector-only and **76.5% /
0.845** with exclusion — matching both the Phase-2e baseline (72.5% / 0.814) and
the retracted run's arm A exactly. Only the API arm moved, which is what isolates
the defect to the input text.*

**Total experiment cost: $0.076** (all five corpora + 255 query embeds).

## Phase D — diagnosis

**Where the gap is.** All four challengers beat MiniLM at hit@1, and they win on
substantially the **same questions** — a shared, non-random failure set for
MiniLM. Its five most common losses, with what it returned at rank 1:

| Question | Expected | MiniLM's rank-1 |
|---|---|---|
| "What is middleware and what can it intercept?" | `/tutorial/middleware/` | `/advanced/middleware/` |
| "How do WebSockets work compared to normal HTTP routes?" | `/advanced/websockets/` | `/release-notes/` |
| "How do I write tests with TestClient?" | `/tutorial/testing/` | `/release-notes/` |
| "How do I register a custom exception handler?" | `/tutorial/handling-errors/` | `/release-notes/` |
| "`response_model` — what is it for?" | `/tutorial/response-model/` | `/how-to/general/` |

**Failure signature (what each arm returns at rank 1 when wrong):**

| Space | wrong | → `/release-notes/` | → `/reference/` | other |
|---|---|---|---|---|
| all-MiniLM-L6-v2 | 14 | **3** | 2 | 9 |
| bge-base-en-v1.5 | 9 | 1 | 2 | 6 |
| text-embedding-3-small | 9 | **0** | 2 | 7 |
| text-embedding-3-large | 7 | **0** | 0 | 7 |
| bge-m3 | 11 | 2 | 2 | 7 |

This reframes an existing production default: **the release-notes exclusion rule
is largely a patch for a MiniLM weakness.** It is worth +4.0 pts hit@1 to MiniLM
(72.5 → 76.5) and worth **nothing** to 3-small or 3-large (82.4 → 82.4, 86.3 →
86.3), which never put the release-notes page at rank 1 in the first place.

**By question type** — the gap is *not* evenly spread. It is concentrated in
natural-language questions; identifier-literal (`api`) questions are already
solved by every non-MiniLM arm:

| Space | `api` (n=8, identifier-literal) | `concept` (n=11, NL) | `howto` (n=32, NL) |
|---|---|---|---|
| all-MiniLM-L6-v2 | 75.0% | 81.8% | 68.8% |
| bge-base-en-v1.5 | **100.0%** | **100.0%** | 71.9% |
| text-embedding-3-small | **100.0%** | 81.8% | 78.1% |
| text-embedding-3-large | **100.0%** | **100.0%** | 78.1% |
| bge-m3 | **100.0%** | 72.7% | 75.0% |

**Significance (McNemar exact, paired hit@1, n=51).** Discordant pairs, `b` =
MiniLM right / challenger wrong, `c` = the reverse:

| Arm vs MiniLM | b | c | net | p (raw) | p (Holm, 4 comparisons) |
|---|---|---|---|---|---|
| bge-base-en-v1.5 | 2 | 7 | +5 | 0.180 | 0.539 |
| text-embedding-3-small | 3 | 8 | +5 | 0.227 | 0.539 |
| text-embedding-3-large | 1 | 8 | **+7** | **0.039** | 0.156 |
| bge-m3 | 5 | 8 | +3 | 0.581 | 0.581 |

**Read this honestly: only 3-large clears p<.05 unadjusted, and nothing survives
Holm correction.** n=51 with ~10 discordant pairs is simply underpowered to
certify a 10-point difference. What raises confidence above the individual
p-values is that the direction is **consistent across four independent model
families**, the wins concentrate on a **coherent, explainable failure set**, and
the mechanism (missing heading signal → release-notes capture) is identified
rather than inferred. Under the production config every effect shrinks further
(nothing reaches p<.05 even unadjusted).

**MiniLM's 256-token truncation.** **837 / 1582 chunks (52.9%)** exceed MiniLM's
`max_seq_length=256`. A binary long/short split on the gold page is degenerate
here (50 of 51 questions have at least one over-length gold chunk), so bucketing
by the gold page's *mean* chunk length in MiniLM tokens (terciles):

| Bucket | n | gold mean tokens | MiniLM hit@1 | 3-large hit@1 | MiniLM deficit |
|---|---|---|---|---|---|
| short | 17 | 147–280 | 88.2% | 94.1% | −5.9 |
| mid | 17 | 296–356 | 52.9% | 76.5% | **−23.5** |
| long | 17 | 356–805 | 76.5% | 88.2% | −11.8 |

The deficit is real but **not monotonic in length** — it peaks in the middle
tercile, and *every* challenger's hit@1 wins (7–8 each) land on questions whose
gold page has truncated chunks. Truncation is therefore a **contributing** factor,
not the whole story: MiniLM also loses short-gold questions on plain semantics
(e.g. `tutorial/middleware` vs `advanced/middleware`).

## Phase E — verdict

**The original result does not stand. It was a methodology artifact**, caused by
denying the API arm the `heading_path` prefix that production gives MiniLM. Under
byte-identical input the ordering inverts: MiniLM is the **weakest** of the five
spaces on hit@1 and MRR under both configs, not the strongest.

**But the corrected result is not statistically certified either.** No arm
survives multiplicity correction at n=51. The honest summary is: *the claim
"MiniLM beats the hosted models" is refuted; the claim "model X is significantly
better" is not yet established on this instrument.*

**Worth adopting, if the owner chooses:** **`bge-base-en-v1.5` (768d, local)** is
the standout on cost-adjusted grounds — **+9.9 pts hit@1 vector-only / +7.8 with
exclusion, +0.066 MRR, for $0, no API key, no network dependency, no new failure
mode**, at 68 ms/query vs MiniLM's 17 ms (both negligible against the ~800 ms a
hosted embedder adds, and against generation latency). It matches 3-small
outright and beats it under the production config. The only costs are a 2× vector
column (384 → 768) and a full re-ingest.

`text-embedding-3-large` is the accuracy ceiling (86.3% hit@1, the only arm with
zero release-notes captures and zero `/reference/` captures) but it buys +3.9 pts
over free local bge-base in exchange for a per-query network round-trip, an
external key, an 8× vector column, and recurring cost. **`bge-m3` is not
competitive** and can be dropped from consideration.

**Ceiling check:** 3-large *did* beat MiniLM decisively, so the corpus is **not**
the limiting factor — the previous run's implicit conclusion ("even a bigger model
can't help, so it's the data") was an artifact of the same defect.

**Scope caveat, unchanged and still important:** this measures the **prose dense
path only**. In the shipped gaming product, LoL numeric and multi-row questions are
answered by **structured table lookup, which bypasses embeddings entirely**, and
the live OP.GG path bypasses them too. Any win here is bounded to the
mechanics/lore prose slice, and hit@5 is saturated (94–96% everywhere) — the
reranker sees essentially the same candidate pool regardless of arm, so
**end-to-end answer quality would move less than these hit@1 deltas suggest.**

**Status: NOT ADOPTED — MiniLM remains the production default.** A switch to
bge-base-en-v1.5 is the recommended candidate if the owner wants to spend a
re-ingest on it; it should be re-validated end-to-end (with reranker + generation,
on the LoL prose set) before adoption, since this experiment only measures the
dense leg. All five scratch tables dropped. *(Retrieval-only experiment — no
generator or judge involved.)*


---

# LoL evaluation — `lol` @ `16.14.1` (production, text-embedding-3-small-1536)

- **Date:** 2026-07-27  ·  **test set:** `testset_lol.jsonl`  ·  **pipeline:** production `route()` (structured + prose)
- **Generator:** `openai/gpt-4o-mini`  ·  **Faithfulness judge:** `google/gemini-2.5-flash` (independent family)  ·  **wall time:** 1.8 min

## Prose (mechanics/lore) — URL-hit scored

| n | hit@1 | hit@3 | hit@5 | MRR |
|---|---|---|---|---|
| 8 | 100.0% | 100.0% | 100.0% | 1.000 |

## Numeric (structured) — answer-level vs context-level

Answer-level = the model *stated* the right number; context-level = the right number was *retrieved*. The gap is structured-path generation fidelity.

| n | answer-accuracy | context-accuracy | gap |
|---|---|---|---|
| 17 | **100.0%** | 100.0% | +0.0 pts |

## Multi-row (item sets) — recall

| n | answer-recall | context-recall | pass rate (≥ min_recall) |
|---|---|---|---|
| 4 | **100.0%** | 100.0% | 100.0% |

## Off-corpus refusal

**3/3** refused correctly.

## Faithfulness

Mean **5.00 / 5** over 29 answered questions (judge: `google/gemini-2.5-flash`, independent family).

*Numeric labels derive from and are auto-verified against the ingested `lol_*` tables; see `evaluate.py --refresh-labels`.*

---

# Embedding migration verification — MiniLM-384 → text-embedding-3-small-1536

**ADOPTED 2026-07-27.** Production embeddings moved from local
`all-MiniLM-L6-v2` (384-dim) to `openai/text-embedding-3-small` (1536-dim, via
OpenRouter), on the evidence of the audited five-way bake-off above. Both corpora
were migrated (`ingestion.migrate_embeddings`) and re-ingested; the embedding
input is unchanged (`heading_path + "\n\n" + content`) and now verified on every
ingest by a pairing-integrity check.

## LoL eval — before / after (same test set, generator, and judge)

Generator `openai/gpt-4o-mini` · judge `google/gemini-2.5-flash` · `lol` @ `16.14.1`
· 32 questions.

| Slice | Metric | Before (MiniLM-384) | After (3-small-1536) | Δ |
|---|---|---|---|---|
| Numeric (n=17) | answer-accuracy | 100.0% | **100.0%** | — |
| Numeric (n=17) | context-accuracy | 100.0% | **100.0%** | — |
| Multi-row (n=4) | answer-recall | 100.0% | **100.0%** | — |
| Multi-row (n=4) | context-recall | 100.0% | **100.0%** | — |
| Multi-row (n=4) | pass rate | 100.0% | **100.0%** | — |
| Prose (n=8) | hit@1 / hit@3 / hit@5 | 100% / 100% / 100% | **100% / 100% / 100%** | — |
| Prose (n=8) | MRR | 1.000 | **1.000** | — |
| Off-corpus (n=3) | refused correctly | 3/3 | **3/3** | — |
| Faithfulness (n=29) | mean score | 4.93 / 5 | **5.00 / 5** | **+0.07** |

**Read honestly.** Numeric, multi-row and refusal rows are unchanged *by
construction* — those paths are structured-table lookups and the refusal gate,
which never touch embeddings. They are here as a **regression check** (that the
migration broke nothing), not as evidence the new embedder helps. The prose rows
were **already saturated at 100% / MRR 1.000 on 8 questions** before the change,
so they cannot show improvement either — this eval had no headroom to detect the
+9.9 pts hit@1 the bake-off measured on the 51-question FastAPI set. The
faithfulness bump (+0.07 on a 5-point scale, single run, LLM judge) is **not a
meaningful signal**. The honest summary: **the migration is verified
non-regressive on the product's own eval; its measured benefit lives in the
FastAPI regression harness, which is the only instrument here with the headroom
to show it.**

## End-to-end latency (production `/ask`, LoL corpus, local run)

The embedding leg is now a network round-trip and is reported separately in the
`done` event and the request log rather than hidden inside `retrieve_ms`.

| Question | embed | retrieve | rerank | generation | total |
|---|---|---|---|---|---|
| "What does Yasuo's passive do?" | 655 ms | 747 ms | 0 ms | 4026 ms | **5428 ms** |
| "What is the cooldown of Ahri's Q at rank 1?" | 1239 ms | 392 ms | 0 ms | 1719 ms | **3350 ms** |
| "Which items give lethality?" | 645 ms | 309 ms | 0 ms | 1851 ms | **2804 ms** |

Query-embed latency over the 51-question FastAPI set: **mean 727 ms, median
686 ms** (MiniLM local CPU was ~17 ms). That is the price of the adoption:
**~0.7 s added to every non-live question**, against generation at 1.7–4.0 s.

## Release-notes exclusion — re-checked under the new embeddings (report only)

No default was changed. 51 in-corpus FastAPI questions.

| Stage | Config | hit@1 | hit@3 | hit@5 | MRR |
|---|---|---|---|---|---|
| dense-only | no exclusion | 82.4% | 92.2% | 94.1% | 0.882 |
| dense-only | + release-notes excl | 82.4% | 92.2% | 94.1% | 0.882 |
| dense-only | **delta** | **+0.0** | +0.0 | +0.0 | **+0.000** |
| full pipeline | no exclusion | 72.5% | 94.1% | 96.1% | 0.837 |
| full pipeline | + release-notes excl | **80.4%** | 94.1% | 96.1% | **0.876** |
| full pipeline | **delta** | **+7.8** | +0.0 | +0.0 | **+0.039** |

**It still earns its place — but for a different reason than in Phase 2e.** On
the **dense leg it is now worth exactly nothing** (+0.0 / +0.000): 3-small never
puts a release-notes page at rank 1, so there is nothing left to filter. The
**+7.8 pts hit@1** it still delivers end-to-end comes from the **keyword leg**,
which continues to surface API-name-dense changelog pages that RRF then fuses
into the pool. The rule was originally justified as a fix for dense retrieval;
it is now, in effect, a keyword-leg filter. Kept as the default (the end-to-end
gain is unchanged at +7.8 pts, coincidentally identical to the Phase-2e figure),
but the *rationale* in any future write-up should say keyword, not dense.


---

# LoL evaluation — `lol` @ `16.14.1` (fuller-answer prompt)

*Same test set, generator, judge and corpus as the run above; the only
change is the generation prompt (prose, complete figures, explicit ban on
ungrounded strategy). Scores are unchanged, which is the point: the answers
got longer without drifting.*

- **Date:** 2026-07-31  ·  **test set:** `testset_lol.jsonl`  ·  **pipeline:** production `route()` (structured + prose)
- **Generator:** `openai/gpt-4o-mini`  ·  **Faithfulness judge:** `google/gemini-2.5-flash` (independent family)  ·  **wall time:** 2.5 min

## Prose (mechanics/lore) — URL-hit scored

| n | hit@1 | hit@3 | hit@5 | MRR |
|---|---|---|---|---|
| 8 | 100.0% | 100.0% | 100.0% | 1.000 |

## Numeric (structured) — answer-level vs context-level

Answer-level = the model *stated* the right number; context-level = the right number was *retrieved*. The gap is structured-path generation fidelity.

| n | answer-accuracy | context-accuracy | gap |
|---|---|---|---|
| 17 | **100.0%** | 100.0% | +0.0 pts |

## Multi-row (item sets) — recall

| n | answer-recall | context-recall | pass rate (≥ min_recall) |
|---|---|---|---|
| 4 | **100.0%** | 100.0% | 100.0% |

## Off-corpus refusal

**3/3** refused correctly.

## Faithfulness

Mean **5.00 / 5** over 29 answered questions (judge: `google/gemini-2.5-flash`, independent family).

*Numeric labels derive from and are auto-verified against the ingested `lol_*` tables; see `evaluate.py --refresh-labels`.*


---

# LoL evaluation — `lol` @ `16.14.1` (richer item lists, deduped corpus)

*Corpus re-ingested with alternate-mode item variants excluded (280 -> 249
items). Item lists are now found by Riot's item TAG union'd with the text
search, capped at 30, and each item carries its own stat value.*

*Two regressions surfaced and were fixed before this run, both worth
remembering. (1) Querying by tag ALONE dropped Cull, which states life steal
in its text and carries no LifeSteal tag - the tag is broader for some stats
but is not a superset, so the query unions both. (2) Mapping "lethality" to
the ArmorPenetration tag put Lord Dominik's Regards (35% armor penetration,
no lethality) into the answer for "which items give lethality"; the
faithfulness judge caught it at 3/5. The containment runs one way only.
The judge's context cap was also raised 1200 -> 4000 chars, because the
longer item passages were being truncated before the judge saw them.*

- **Date:** 2026-07-31  ·  **test set:** `testset_lol.jsonl`  ·  **pipeline:** production `route()` (structured + prose)
- **Generator:** `openai/gpt-4o-mini`  ·  **Faithfulness judge:** `google/gemini-2.5-flash` (independent family)  ·  **wall time:** 1.8 min

## Prose (mechanics/lore) — URL-hit scored

| n | hit@1 | hit@3 | hit@5 | MRR |
|---|---|---|---|---|
| 8 | 100.0% | 100.0% | 100.0% | 1.000 |

## Numeric (structured) — answer-level vs context-level

Answer-level = the model *stated* the right number; context-level = the right number was *retrieved*. The gap is structured-path generation fidelity.

| n | answer-accuracy | context-accuracy | gap |
|---|---|---|---|
| 17 | **100.0%** | 100.0% | +0.0 pts |

## Multi-row (item sets) — recall

| n | answer-recall | context-recall | pass rate (≥ min_recall) |
|---|---|---|---|
| 4 | **100.0%** | 100.0% | 100.0% |

## Off-corpus refusal

**3/3** refused correctly.

## Faithfulness

Mean **5.00 / 5** over 29 answered questions (judge: `google/gemini-2.5-flash`, independent family).

*Numeric labels derive from and are auto-verified against the ingested `lol_*` tables; see `evaluate.py --refresh-labels`.*


---

# LoL evaluation — `lol` @ `16.14.1` (control run: full matchup table adopted)

*Counter and matchup answers now come from `lol_get_lane_matchup_guide` ->
`data.counters` (the whole 13-41 row table) instead of the analysis tool's
curated three-per-direction lists. Re-run here to show the corpus pipeline is
untouched: every figure below is identical to the run above. The live path has
no test set in this file - it is scored by `eval/eval_live.py`, which went from
17 to 19 behaviour checks with the two new "who else returns new champions"
cases, and passes 19/19.*

- **Date:** 2026-08-01  ·  **test set:** `testset_lol.jsonl`  ·  **pipeline:** production `route()` (structured + prose)
- **Generator:** `openai/gpt-4o-mini`  ·  **Faithfulness judge:** `google/gemini-2.5-flash` (independent family)  ·  **wall time:** 1.8 min

## Prose (mechanics/lore) — URL-hit scored

| n | hit@1 | hit@3 | hit@5 | MRR |
|---|---|---|---|---|
| 8 | 100.0% | 100.0% | 100.0% | 1.000 |

## Numeric (structured) — answer-level vs context-level

Answer-level = the model *stated* the right number; context-level = the right number was *retrieved*. The gap is structured-path generation fidelity.

| n | answer-accuracy | context-accuracy | gap |
|---|---|---|---|
| 17 | **100.0%** | 100.0% | +0.0 pts |

## Multi-row (item sets) — recall

| n | answer-recall | context-recall | pass rate (≥ min_recall) |
|---|---|---|---|
| 4 | **100.0%** | 100.0% | 100.0% |

## Off-corpus refusal

**3/3** refused correctly.

## Faithfulness

Mean **5.00 / 5** over 29 answered questions (judge: `google/gemini-2.5-flash`, independent family).

*Numeric labels derive from and are auto-verified against the ingested `lol_*` tables; see `evaluate.py --refresh-labels`.*
