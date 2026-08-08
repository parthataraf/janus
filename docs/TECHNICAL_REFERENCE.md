# Janus — Technical Reference: How Everything Works

A component-by-component explanation of the system. Written for the owner;
suitable for the repo as internal documentation and as interview
preparation. State reflects the project as of the GAMING PIVOT during
Phase 4c: Janus is now a gaming knowledge engine — League of Legends is
the primary (and currently only) live face, Palworld is the coming-soon
second face, and the FastAPI docs face is RETIRED FROM THE PRODUCT but
retained in-repo as engineering history and the regression harness.
Deploy pending (ships LoL-only).

---

## 1. The big picture

Janus is a retrieval-augmented generation (RAG) platform for game
knowledge: one shared engine, multiple corpus "faces." Live face: League
of Legends. Coming soon: Palworld. Retired from product, retained in
repo: the FastAPI docs face the engine was originally built and evaluated
on (see 2.1 and 7). The pipeline for every question:

```
question
  → embed (text-embedding-3-small via OpenRouter)
  → route (LoL only: structured vs prose)
  → candidate retrieval (vector + keyword, release-notes filtered)
  → RRF fusion
  → cross-encoder rerank
  → refusal gate (below threshold → stop, no model call)
  → generation (gpt-4o-mini via OpenRouter; phi-4 local fallback), grounded + cited
  → SSE stream to the React UI (sources first, then tokens)
```

Design principles that shaped everything: no RAG frameworks (mechanics are
hand-rolled for understanding and defensibility); every retrieval change
must be eval-justified; corpora are version-pinned; the model is a
synthesizer, never the knowledge source.

---

## 2. Ingestion — getting knowledge into the system

### 2.1 FastAPI docs face (RETIRED from product; retained as regression harness)
- Product status: excluded from /corpora and the UI via EXCLUDED_CORPORA
  config (deactivation, not deletion). The adapter, corpus data, golden
  set, and eval baselines remain in-repo: they are the regression
  harness for any future pipeline change (e.g. the contemplated
  embedding-model migration) and the documented engineering history
  behind the headline eval numbers.
- Source: shallow git clone of fastapi/fastapi at pinned tag **0.139.0**.
  Docs are markdown under docs/en/docs; each file maps to its live URL on
  fastapi.tiangolo.com (citation target).
- **MkDocs include resolution**: the docs pull code samples via
  `{* ../../docs_src/... *}` directives. The ingester resolves each
  directive (440/440 resolved), reads the referenced file from docs_src/,
  handles line-range (`ln[a:b]`) and title variants, and splices the code
  in as a fenced block BEFORE chunking. Without this, the corpus had
  prose but almost no code.
- **Fence-aware HTML cleaner**: raw HTML in the markdown (img, div, span,
  comments) is stripped; content-bearing tags (abbr, a, details, table)
  are unwrapped (inner text kept). HTML inside code fences is untouched —
  it's example code. Result: zero raw HTML outside fences.
- Idempotent per (corpus, version): re-running deletes and re-inserts
  only that pair. Current corpus: **1,582 chunks**, 441 code-bearing.

### 2.2 Chunking (core/chunking.py)
- Splits on markdown heading structure; every chunk carries its full
  **heading path** ("Tutorial > Query Parameters > Optional parameters")
  as metadata — used for citations and display.
- **Never splits inside a fenced code block**; a code block travels with
  the prose immediately preceding it.
- Target ~300–500 tokens; small sections merge upward, oversized sections
  split at paragraph boundaries with overlap.

### 2.3 LoL face (Data Dragon)
- Source: Riot's official Data Dragon JSON at pinned patch **16.14.1**.
  Free, structured, legal.
- **Two write paths**:
  1. **Structured tables** (lol_champions / lol_abilities / lol_items):
     typed columns for per-rank cooldowns, costs, ranges, item gold and
     stats (JSONB for ragged fields). 173 champions / 692 abilities /
     280 items. This is the numeric source of truth.
  2. **Prose chunks** (corpus `lol`, 1,225 chunks at 16.14.1 / 1,241 at
     16.15.1): ability/passive/item descriptions with Riot's markup
     stripped, embedded through the same pipeline as any corpus.
- Verified by diffing stored values against Data Dragon's raw JSON
  (exact matches on spot-checked champions/items).

**Summoner's Rift only — the classic-mode roster is deliberately excluded.**
Patch 16.15 shipped a parallel roster for the new classic/retro game mode:
the same champions with different balance data for a different ruleset,
published by Data Dragon under `Jade_*` ids and — critically — the SAME
display names. That took the champion list from 173 to 233.

Both are dropped at ingest (`core/lol_roster.select_canonical`, applied in
`ingestion/lol_datadragon.py`), so structured tables and prose chunks alike
stay single-ruleset. The reason is consistency ACROSS paths, not tidiness:
the OP.GG live path serves Summoner's Rift, so a corpus carrying both
rosters would answer "Garen's Q cooldown" from one ruleset and "is Garen
strong right now" from another, in the same session, with no way for the
reader to tell. Two contradictory "Garen"s would also compete directly in
retrieval.

The rule is **"not the canonical row for a shared display name"**, never a
`Jade_` string match, so a future variant roster needs no code change. It is
deliberately NOT "id differs from display name": `MasterYi`, `MonkeyKing`,
`LeeSin`, `XinZhao`, `Nunu`, `TahmKench` and six others never equal their
display names, and that test would delete twelve real champions. A row is
only discarded when a better row exists for the same name.

The same function backs entity linking (§5.6), so ingest and routing cannot
disagree about which Garen is the real one — see §13 for the classic-mode
support that is deferred, not forgotten.

---

## 3. Vectorization / embeddings (core/embeddings.py)

- Model: **openai/text-embedding-3-small** via OpenRouter — **1536-dimensional**,
  served over the OpenAI-compatible `/embeddings` endpoint, sharing
  `OPENAI_BASE_URL` / `OPENAI_API_KEY` with generation. *(Adopted 2026-07-27,
  replacing all-MiniLM-L6-v2/384. See §13 and eval/results.md.)*
- **Provider-switchable** via `EMBED_PROVIDER`:
  - `api` (default) — any OpenAI-compatible embeddings endpoint.
  - `local` — sentence-transformers in-process, free and offline. Still fully
    supported; it is what the test suite and offline development use.
- A **bi-encoder** either way: texts are encoded independently into a shared
  space where cosine similarity ≈ semantic similarity. Chunk vectors are
  precomputed at ingestion; a query costs one encode.
- **Invariant**: queries and documents MUST use the same model — vectors from
  different models are geometrically meaningless to compare. `EMBED_DIM` is
  baked into the pgvector column type, and `store.init_schema` now *refuses to
  start* if the live column width disagrees with it, pointing at
  `python -m ingestion.migrate_embeddings --yes`.
- **What gets embedded is load-bearing**: `heading_path + "\n\n" + content`
  (`run_ingest._augment_for_embedding`), never bare `content`. Feeding the
  embedder bare content is exactly the defect that invalidated the first
  bake-off — it cost the candidate model ~16 points of hit@1 and inverted the
  result. Every ingest re-embeds a random sample through that same function and
  asserts cosine ≈ 1 against the stored vector (§12).
- **Robustness the API path must provide** (`core/embeddings.py`):
  - **Order safety** — the response `data` array is sorted by its `index` field
    before pairing back to inputs. Pairing by position would silently give every
    chunk a wrong-but-plausible vector, which is indistinguishable from "the
    model is bad".
  - **Body inspection, not just status** — OpenRouter tunnels upstream failures
    as **HTTP 200 with an `{"error": ...}` body**; a 429 "engine overloaded"
    arrives as a `200`. Transient inner codes are retried with backoff.
  - **Never emit a bad vector** — wrong width, non-finite, or all-zero raises
    `EmbeddingUnavailable`. It is never written to the DB and never used as a
    query; `/ask` refuses honestly instead of ranking noise confidently.
- **Hard dependency, stated plainly**: with `EMBED_PROVIDER=api` the server
  **cannot retrieve at all** if the embeddings endpoint is unreachable — there
  is no query vector, so there is no search. `/health` probes it; `/ask` returns
  an honest "search is temporarily unavailable" refusal. `EMBED_PROVIDER=local`
  removes that dependency at a cost in retrieval quality.

---

## 4. Storage (core/store.py — Postgres + pgvector)

- Single Postgres 16 database (Docker, host port 5433) with the pgvector
  extension. One `chunks` table for all corpora:
  `id, corpus, source_url, heading_path, doc_version, content,
  embedding vector(1536)` — plus the LoL structured tables. The width tracks
  `EMBED_DIM`; changing the embedding model requires
  `python -m ingestion.migrate_embeddings --yes` plus a full re-ingest, because
  vectors from one model are meaningless in another's space.
- **corpus column** is what makes multi-face work: every query filters
  on it. **doc_version** enables version pinning and multi-version
  coexistence.
- Indexes: GIN on to_tsvector(content) for keyword search; (corpus,
  doc_version) for filtering. Vector search is currently exact scan
  (fine at ~3K chunks; HNSW/IVFFlat is the known scale-up path).
- The `<=>` operator computes cosine distance; similarity = 1 − distance.

---

## 5. Retrieval (core/retrieval.py)

### 5.1 The legs
- **vector_search**: embed query → nearest chunks by cosine via pgvector.
  Strong on paraphrase/meaning; weak on exact identifiers.
- **keyword_search**: Postgres full-text (plainto_tsquery + ts_rank)
  against the GIN index. Exists for exact tokens (Depends,
  response_model) that embeddings fumble.

### 5.2 Fusion — Reciprocal Rank Fusion (RRF)
- Both legs return ranked lists; RRF merges by summing 1/(k + rank)
  (k=60) per document across lists. Chosen over score-mixing because
  cosine similarity and ts_rank are incomparable scales; ranks are safe.

### 5.3 Release-notes exclusion (eval-promoted default)
- ~31% of the FastAPI corpus is the release-notes changelog: API-name-
  dense (matches everything) but explanation-poor (answers nothing).
- Query-time filter (source_url NOT LIKE '%/release-notes/%'), each leg
  oversamples 2× so the pool stays full. NOT a deletion.
- Eval evidence: +7.8 pts hit@1 (66.7→74.5), MRR 0.797→0.837. The
  decomposition run further showed the keyword leg's apparent value had
  been mostly changelog pollution.

### 5.4 Reranking
- Model: **cross-encoder/ms-marco-MiniLM-L-6-v2** (local, lazy
  singleton). A cross-encoder reads (query, chunk) TOGETHER and outputs
  a relevance logit — far more precise than bi-encoder cosine, far
  slower, so it only scores the fused top-20 and keeps top-5.
- Scores are raw logits (can be negative); they are meaningful relative
  to this model only.
- Eval verdict: modest ranking gains (hit@3) on this test set, but its
  scores power the refusal gate — retained for calibrated confidence.

### 5.5 Refusal gate
- If the best rerank score < threshold (0.0), the pipeline STOPS before
  any generation call: no model invocation, no cost, no hallucination.
  The refusal response carries the best below-threshold score so the UI
  can show "closest passage scored X".
- Verified by a tripwire test proving the model client is never
  constructed on refusals.

### 5.6 LoL routing (core/lol_routing.py + retrieval.route())
- Entity dictionary built from the lol_* tables (champion/ability/item
  names + aliases). Pure helpers: link_entities, detect_slot,
  detect_rank, has_numeric_intent.
- route(): for non-lol corpora, identical to retrieve() (byte-identical
  path). For lol: entities + numeric intent → structured lookup
  (exact per-rank numbers or item multi-row) prepended as a high-score
  candidate ABOVE prose retrieval, so one generation call sees both.
- Champion base stats (`champion_stat` branch) answer from
  lol_champions, in two shapes:
  - **one named stat** — "Garen's base movement speed" -> that single
    number plus a few companions (`base_stat_field`);
  - **the whole stat line** — "master yi base stats", "what are Ashe's
    stats", "Yasuo stat line" -> every base stat with per-level growth
    (`wants_full_stat_line`). Added 2026-07-27: general phrasings used to
    match no single field, so they fell through to prose and REFUSED even
    though the table held the answer. A named stat still wins over the
    general phrasing, so "master yi armor stats" stays a one-number
    answer. Non-mana resources are labelled correctly (Yasuo reads
    "flow 100 ... Resource: Flow", not 0 mana).
  A routing guard was also fixed during the 4c second review (item
  stat-phrase branch no longer hijacks champion-named base-stat
  questions).

### 5.7 Live stats path — OP.GG MCP (Phase 4h)
Meta questions — matchups/counters ("who counters Yasuo?"), win-rate /
popularity / tier ("Jinx win rate", "is Zed strong right now"),
beginner-suitability — are answered from LIVE OP.GG data, not our corpus.
Owner-approved design (**Option 2: live-query, no cache**):

- **Sanctioned access, no key.** OP.GG publishes an official MCP server
  (`opgginc/opgg-mcp`, MIT) at `mcp-api.op.gg`; we call
  `lol_get_champion_analysis` live, per question.
- **No caching — deliberate.** We NEVER store OP.GG's computed stats in
  our tables. Rationale: their MCP is sanctioned for programmatic
  *access*, not for us to rehost their aggregated data; live-querying
  respects that and keeps our own corpus self-contained. Every live
  answer is attributed — **OP.GG + patch + fetched-at timestamp**, on a
  visually distinct UI card.
- **Routing** (`lol_routing.live_stats_intent`, pure + unit-tested):
  matchup / counters / **build** / champion_stats; everything else routes
  exactly as before. Guards + an eval check prove the **32 pinned questions never
  hit this path**. Champion role comes from OP.GG's own `positions[]`
  (role_rate), not a static map.
- **Session + resilience** (`core/opgg_live.py`): a persistent warm MCP
  session on a background event loop (the serving path is sync, so a
  sync↔async bridge is needed); **self-heals once** on a dead/stale
  session; **pre-warms** common champions at startup so first queries
  land in the fast regime.
- **Timeout + degradation:** per-attempt **8s**, with **one automatic
  retry on timeout** (OP.GG's per-champion compute keeps running server
  side after we give up, so the second call often lands on the finished
  result). Failures degrade — never a hang, never a guess — and each kind
  carries its OWN copy, because a single message for all of them was
  actively misleading:

  | Failure | `user_message` |
  |---|---|
  | `OpggIncomplete` — arrived, no usable fields | "OP.GG doesn't have current stats for *X* right now." |
  | `OpggTimeout` (champion already fetched this process) | "OP.GG is slow to respond right now — try again in a moment." |
  | `OpggTimeout` (first touch) | "Live stats are still warming up… try again in a few seconds." |
  | `OpggEndpointError` — tool error, transport, unparseable | "Live stats are unavailable right now." |

  The cold/slow split is a **proxy**: we only know whether *this process*
  has fetched the champion, not whether OP.GG's shared server-side cache
  is warm. It changes wording only, never behaviour. `app/routes.py` reads
  `.user_message` off whatever it catches rather than matching on type.
  The MCP leg is reported honestly in `/ask` latency (`mcp_ms`) and
  request logs.
- **Item builds (added 2026-07-27).** "master yi items", "what should I
  build on Zed", "Yasuo runes", "Jinx skill order" -> a live build card:
  core items (with pick rate, win rate and sample size), boots, starter
  items, both rune pages, and the skill max order. This can ONLY come
  from the live path: our `lol_items` table holds item PROPERTIES (gold,
  stats), never build RECOMMENDATIONS. `summoner_spells` is deliberately
  omitted — OP.GG returns numeric ids and we do not ingest Data Dragon's
  summoner-spell map, so printing them would mean guessing.
- **Grounding:** numbers with statistical-tendency framing only
  ("statistically favorable/unfavorable"); NEVER dodge/avoid/play advice.
  The build card follows the same rule: "what players are most often
  building", never "what you should build".
- **Eval:** behavior-scored (numbers are patch-volatile) — routed, MCP
  called, attribution+timestamp present, numbers present, hedging
  respected; plus a forced-degradation test. See `eval/eval_live.py` +
  `eval/testset_lol_live.jsonl` (8/8 checks).

**Latency profile & the 5s→8s deviation.** OP.GG computes per-champion
stats on first touch: measured **~7s cold, ~2.5s warm** (their
server-side cache). The originally-approved 5s timeout degraded on nearly
every *uncached* champion, so it was raised to **8s** (still degrades
gracefully on a true hang). Latency is also variable — an occasional cold
champion exceeds even 8s and degrades. Startup pre-warm + the warm session
keep common/demo champions fast.

---

## 6. Generation (core/generation.py)

- **Provider-agnostic** via the OpenAI SDK with configurable base_url.
  **Primary = OpenRouter** (hosted, OpenAI-compatible) serving
  **openai/gpt-4o-mini** — reliable [n] citations, sub-cent/query, no GPU
  needed, so it's the same generator in local dev and the public deploy.
  **Documented local fallback = a llama.cpp server on the LAN (`:8080`)**
  serving **phi-4-Q4_K_M** (14B, 4-bit quantized) for offline/no-cost runs.
  Swapping between them is a two-line .env toggle (base_url + model); the
  OpenAI SDK client is a lazy singleton built from config, so nothing in
  the retrieval/generation code changes.
- The prompt enforces grounding: answer ONLY from provided context,
  include code when relevant, cite as [n] mapping to the chunk list,
  say the available sources don't cover it rather than guess (corpus-
  neutral phrasing — no "docs"-specific wording, matching the LoL face),
  and **enumerate a provided LIST/SET exhaustively** (no summarizing/"and
  others" — added to fix a multi-row abbreviation finding; see §7).
- **Tolerant citation parsing**: small local models emit [n] markers
  inconsistently; the parser degrades gracefully (answer without inline
  links) rather than failing. Hosted models cite more reliably — one
  reason the public deploy uses one.
- generate_stream() yields tokens for the SSE path. GEN_TIMEOUT guards
  slow local inference.

---

## 7. Evaluation (eval/)

- **Golden sets**: hand-curated, double-reviewed JSONL — 54 FastAPI
  questions (howto/concept/api/off_corpus mix, paired phrasings), 32
  LoL questions (17 numeric / 4 multi-row / 8 prose / 3 off_corpus).
  Protocol: draft
  assist → independent second review via retrieval → human adjudication
  of disagreements + sampled audit of agreements. Labels record where
  the answer lives in the PINNED corpus, never what retrieval can find
  and never the owner's memory.
- **Metrics**: hit@k (expected URL in top-k), MRR, faithfulness
  (LLM-as-judge, 1–5). **Provenance policy** (see §13): results.md names
  BOTH the generator and the judge, and they are **different families** —
  the LoL eval generates with `openai/gpt-4o-mini` and judges with
  `google/gemini-2.5-flash`, so no model grades its own output (the old
  self-judging caveat is retired; still treat scores as directional). The
  judge model is `evaluate.py`'s `JUDGE_MODEL` (env-overridable). LoL
  numeric questions: **answer-level accuracy** headline (did the user get
  the right number) with **context-level** alongside (did routing fetch
  it); the gap is the structured-path generation-fidelity signal.
  Multi-row: item-set recall (answer vs context). Off-corpus: correct iff
  refused.
- **Maintenance policy**: sets version-lock to their pinned corpus, so
  results never rot on their own. Reruns only on: pipeline change
  (automated), corpus version bump (rerun + --refresh-labels: numeric
  labels auto-re-derive from tables with printed diffs; prose URLs
  flag-only for human review), question depreciation (refresh flags
  dead URLs). No scheduled reruns.
- Key results to date (FastAPI corpus — now the engineering-history
  baseline): release-notes exclusion promoted (+7.8 hit@1); code-margin
  and top_n=8 declined on evidence; vector-only+exclusion best on this
  natural-language-skewed set, hybrid retained because the set
  under-represents identifier-literal queries (logged as future
  supplementary test set).
- **LoL eval — the PRODUCT's headline numbers** (32 Q @ patch 16.14.1;
  generator `openai/gpt-4o-mini`, judge `google/gemini-2.5-flash`):
  numeric **100% answer-level = 100% context-level** (gap 0 — the router
  surfaces the exact row and the model states it), prose **100% hit@5 /
  MRR 1.000**, off-corpus **3/3 refused**, faithfulness **4.93/5**. The
  answer-vs-context gap earned its keep on **multi-row item sets**: the
  first run had context-recall 100% but answer-recall **~74%** — the
  generator abbreviated long lists. A scoped **exhaustive-enumeration**
  instruction in the generation prompt (grounding/citation/refusal rules
  untouched) closed it to **100% answer-recall / 100% pass** with no
  regression to numeric or prose (both before and after documented in
  results.md). (The named judge substitutes `gemini-2.5-flash` for the
  retired `gemini-2.0-flash-001`.)
- Retired-face policy: the FastAPI golden set continues to run on
  pipeline changes as the regression harness even though the product no
  longer serves that corpus.

---

## 8. API layer (app/)

- FastAPI service. **POST /ask** streams Server-Sent Events with
  **sources-first ordering**: a `sources` event (chunks with preview,
  URL, heading path, scores, citation index) arrives BEFORE token
  events, then `done` with the full latency breakdown
  (retrieve/rerank/generation/total ms). Refusals send sources (with
  below-threshold best-match) then a `refusal` event.
- GET /corpora (drives the UI switcher; EXCLUDED_CORPORA filters the
  smoke test corpus AND, post-pivot, the retired fastapi corpus),
  GET /health (db + models + generation endpoint). DEFAULT_CORPUS=lol.
- JSON-lines request logging (per-stage latency, refusal flag, top
  rerank score); in-memory per-IP sliding-window rate limit; CORS from
  env. Models warmed once at startup (lifespan), schema init idempotent
  at boot (fresh-DB safe).

---

## 9. Frontend (frontend/ — Vite + React)

- Implements the design direction captured in `design/Janus UI ss.png`.
  Minimal deps: react-markdown + rehype-highlight + a custom rehype
  plugin that wraps citations and citable sentences.
- SSE via fetch + manual parser (EventSource is GET-only; /ask is POST).
- Four states: empty/first-visit, streaming (panel fills from the
  sources event while tokens arrive), completed, refusal (honest card
  showing the below-threshold score).
- **Two-way citation interaction**: click [n] → its source card scrolls
  into view with a glow pulse; click a card → the citing sentence(s)
  highlight and the rest of the answer dims. Each answer carries its own
  source set; the panel labels whose sources are shown and swaps when a
  citation in an earlier answer is clicked.
- Header: corpus switcher (from /corpora — post-pivot: League of
  Legends active/default, "Palworld — coming soon" as a disabled entry
  that degrades safely while absent from /corpora), patch indicator
  ("Patch 16.14"), latency readout, light/dark toggle. Empty state,
  try-chips (one numeric, one conceptual, one deliberate-refusal
  matchup question), and composer placeholder are LoL-facing. Meta/OG
  tags shipped for link unfurling; hero/og image is a LoL
  structured-routing answer.
- Design pass (pending implementation at time of writing): base surface
  stack lifted one step lighter with elevation logic preserved.

---

## 10. Packaging & deploy (Phase 3c artifacts)

- Dockerfile.api: python-slim, non-root, CPU torch, **reranker** baked at
  build (no cold-start download), baked before code copy so code edits
  don't re-download. Since the 2026-07-27 embedding adoption **no
  embedding weights ship in the image** — embeddings come from the
  OpenAI-compatible API, so the image shrank and the embedder is swapped
  by config instead of a rebuild. The flip side is a new hard runtime
  dependency: no embedding endpoint ⇒ no query vector ⇒ no retrieval at
  all (see §3), so the deploy budget must include outbound HTTPS to the
  provider and the same key now gates both generation and retrieval.
- Dockerfile.frontend: Vite build → nginx. docker-compose.deploy.yml:
  db (persistent volume) + api + frontend + **Caddy** (auto-HTTPS; only
  Caddy publishes ports; /api/* proxied to the API).
- Secrets only at runtime via server .env (verified absent from all
  image layers). One-off ingestion runs as a compose command on the
  server — post-pivot the deploy ingests LoL only (fastapi ingest
  dropped from the runbook; the corpus can be ingested locally when the
  regression harness runs). README runbook covers provision → DNS →
  ingest → up → flip-repo-public → swap demo URL.
- Generation = **OpenRouter (gpt-4o-mini) in both dev and deploy** (no
  GPU needed anywhere); phi-4 on a local llama.cpp server is the documented
  local fallback. Same code, different .env.

---

## 11. Model inventory (quick reference)

| Role | Model | Size | Where | Why |
|---|---|---|---|---|
| Embeddings (PRIMARY) | openai/text-embedding-3-small | hosted / 1536-dim | OpenRouter (`/api/v1/embeddings`) | +9.9 pts hit@1 over MiniLM on the audited bake-off; same key as generation; NOT baked into the image |
| Embeddings (local option) | BAAI/bge-base-en-v1.5 | 109M / 768-dim | local CPU | statistically tied with 3-small for $0 and no network dependency; `EMBED_PROVIDER=local` |
| Reranker | cross-encoder/ms-marco-MiniLM-L-6-v2 | ~22M | local CPU (+ baked into API image) | precise pairwise scoring; powers refusal gate. The ONLY model still baked |
| Generation (PRIMARY) | openai/gpt-4o-mini | hosted | OpenRouter (`/api/v1`) | same generator in dev & deploy; reliable [n] citations; sub-cent/query (a+b measured ~$0.00025); no GPU needed |
| Generation (local fallback) | phi-4-Q4_K_M (14B, 4-bit) | ~9GB | llama.cpp on the LAN | offline / zero-cost runs; two-line .env toggle |
| Faithfulness judge | google/gemini-2.5-flash (via OpenRouter) | hosted | OpenRouter | independent of the generator — removes the self-judging caveat; still directional. Substitutes for the retired 2.0-flash-001 |

---

## 12. The invariants (things that must stay true)

1. Same embedding model for queries and documents, always. EMBED_DIM must
   match the model; `init_schema` refuses to start on a mismatch.
2. Retrieval defaults change only with eval justification.
2b. What gets embedded is `heading_path + "

" + content`, never bare
   `content` — and every ingest proves it, by re-embedding a random sample and
   asserting cosine ≈ 1 against the stored vectors. This check exists because
   its absence let a wrong bake-off result stand.
3. Golden-set labels record where answers live in the pinned corpus —
   never tuned to what retrieval finds, never from memory.
4. The generator answers only from provided context; refusal is a
   feature.
5. Corpora are version-pinned; ingestion is idempotent per
   (corpus, version).
6. Secrets live in runtime .env only — never in images, commits, or
   chat.
7. Owner owns: test-set curation verdicts, error analysis, design
   decisions, deploys.

---

## 13. Pivot record & open items (as of this revision)

**The gaming pivot (decided during Phase 4c):** Janus repositioned from
"docs + game demo" to a gaming knowledge engine. FastAPI face retired
from the product via config (EXCLUDED_CORPORA), preserved in-repo as
regression harness and engineering history; README reframed with the
FastAPI eval story under "Engineering history." Rationale: coherent
gamer-facing product identity; the two-face architecture claim is
restored when Palworld ships (LoL + Palworld).

**Open items at time of writing:**
- 4c finalization: champion base-stat routing branch; owner curation
  verdicts on the 30–32 question LoL set; full LoL eval run appended to
  results.md (these become the product's headline numbers).
- Design implementation: lifted surface stack + pivot states from the
  updated design direction into frontend/.
- ~~Contemplated (eval-gated): embedding migration MiniLM →
  text-embedding-3-small.~~ **RESOLVED 2026-07-27 — ADOPTED.** See below.

**Embedding migration (resolved).** `openai/text-embedding-3-small` (1536-dim,
via OpenRouter) replaced `all-MiniLM-L6-v2` (384-dim, local) as the production
embedder.

*How the decision was reached, including a wrong turn worth remembering:* the
first bake-off (2026-07-25) reported MiniLM **beating** 3-small and was written
up as NOT ADOPTED. A later audit found the cause — the harness fed the API arm
bare `content` while production feeds MiniLM `heading_path + content`. The
candidate was being scored without the single most discriminating feature the
baseline had. The prime suspect going in had been response misordering; that
turned out to be clean, and the real defect was in the input, not the plumbing.
Corrected five-way re-run (hit@1, vector-only, n=51):

| space | hit@1 | MRR |
|---|---|---|
| all-MiniLM-L6-v2 (384) | 72.5% | 0.815 |
| bge-base-en-v1.5 (768, local) | 82.4% | 0.881 |
| **text-embedding-3-small (1536)** | **82.4%** | **0.882** |
| text-embedding-3-large (3072) | 86.3% | 0.904 |
| bge-m3 (1024) | 78.4% | 0.845 |

*Adoption rationale, and the trade knowingly accepted:* **3-small and
bge-base-en-v1.5 are statistically indistinguishable here** — 82.4% hit@1 each,
MRR 0.882 vs 0.881, and bge-base is actually *ahead* under the production
release-notes-exclusion config (84.3% vs 82.4%). bge-base is local, free, adds
no network dependency, and needs no key. It was the cost-adjusted
recommendation. The owner chose 3-small anyway, accepting:
  - a **hard runtime dependency** on embedding-API reachability (no endpoint ⇒
    no query vector ⇒ no retrieval at all — the failure is total, not partial),
  - **~800 ms** added per query versus ~68 ms for local bge-base,
  - **recurring cost** per re-ingest (~$0.009 per full corpus embed) and per
    query (negligible: 675 tokens ≈ $0.000013 across 51 queries),
  - a **2× vector column** vs bge-base (1536 vs 768).
In exchange: no local embedding weights in the API image, one provider and one
key for both generation and embeddings, and a hosted model that can be upgraded
by changing config rather than rebuilding an image. Neither arm's advantage over
MiniLM survives Holm correction at n=51, so this is adoption on a consistent
direction plus an identified mechanism, not on a certified p-value — that
caveat is recorded in eval/results.md and should not be forgotten if the
question is revisited.

*Also learned, and now permanent:* the eval harness could not have caught this
defect, because the defect was in the harness. What catches it now is the
**post-ingest pairing-integrity check** (invariant 2b) — every ingest re-embeds
a random sample through `_augment_for_embedding` and asserts cosine ≈ 1 against
the stored vectors, so an input-text or ordering drift fails the ingest loudly
instead of quietly degrading retrieval.

- **Now open — classic-mode support (deferred, with a trigger condition).**
  Patch 16.15 added a second, separately-balanced roster for the new
  classic/retro mode (Data Dragon `Jade_*` ids under the base champions'
  display names). It is excluded at ingest today (§2.3) so every path stays
  Summoner's Rift. Supporting it properly is four pieces, and they only pay
  off together:
    1. **Mode-aware corpus** — ingest both rosters tagged by mode rather than
       dropping one, with the mode carried on the chunk and the structured row.
    2. **Mode intent detection** in routing, so "Garen's Q cooldown in classic"
       reaches the right roster.
    3. **Disambiguation when the user doesn't say** — the numbers genuinely
       differ, so the honest default is to answer for SR and name the ruleset,
       or ask, rather than silently pick.
    4. **A UI affordance** showing which ruleset answered, since otherwise two
       correct answers look like a contradiction.
  **Trigger condition: revisit once OP.GG publishes meta stats for the classic
  mode.** They do not yet — `JADE_*` champions resolve on their side but return
  `average_stats: null`. Until then the live path could only ever answer for SR,
  so a mode-aware corpus would make the *static* paths mode-aware while the
  *live* path silently stayed SR — strictly worse than today's single coherent
  ruleset. Discovered via the Garen incident (see below).
- **Resolved (2026-08-01) — counter lists were reading the wrong field.** A user
  pointed at OP.GG's own Master Yi jungle page, which shows five weak-against and
  five strong-against matchups, while we answered "no sufficient favorable-matchup
  sample". Cause: `lol_get_champion_analysis` exposes only OP.GG's *curated*
  `weak_counters`/`strong_counters` — three rows per direction, sample-thresholded,
  and empty on the favourable side for Yi. The payload also carries
  `counters_meta.message`, which said in plain English *"Insufficient matchup
  sample. See data.summary.positions[].counters[] for raw matchup data"*; we
  requested that field and never read it. The real fix was a different tool
  entirely: `lol_get_lane_matchup_guide` returns `data.counters`, the whole
  13–41 row table for the champion+lane, and sorting it reproduces both panels of
  their counter tab exactly, to the game count. Adopted as the source for counter
  and matchup answers, with a **50-game floor** (±13pp at n=50; a 100-game floor
  deletes every favourable matchup Master Yi has, reinstating the bug, and empties
  Azir, Kindred and Soraka). `tier` is deliberately left unset — passing
  `tier="all"` returns a *different, larger* pool that does not match their page,
  despite the tool's own docs claiming omission means all-tier.
  *Worth remembering:* three separate reviews of this path accepted "the field is
  empty" as "the data does not exist", because the field we read really was empty.
  The disconfirming evidence was in the payload the whole time.
- **Now open — 28 of OP.GG's 30 tools are unused, and two cover questions we
  currently refuse.** Deferred, not rejected; logged so the next gap in coverage
  starts here rather than with another hand-built trigger family.
    - `lol_get_champion_synergies` — "who works well with X". The analysis
      payload already carries a `synergies` block (top/mid/adc/support, with
      win rate and sample) that we discard unread, so a first version may not
      even need the extra call.
    - `lol_list_lane_meta_champions` — "who are the best junglers right now",
      lane-by-lane tiers with win/pick/ban. Today this refuses.
  **Deliberately excluded: `lol_search_champion_meta`.** It is OP.GG's own
  RAG — retrieval plus reranking, returning LLM-ready passages. Piping it into
  ours would answer more questions and make every retrieval number in
  `eval/results.md` meaningless, since we would be measuring their pipeline
  through our reranker. The point of this project is the retrieval, not the
  answer coverage.
- **Still open, and the case is now stronger:** the keyword-based live-intent
  classifier has needed a hand-added trigger group per phrase family. That is
  now the **fourth**: winrate → good-against → role → **build** (`_BUILD_WORDS`),
  and conversational carryover adds a **fifth** vocabulary of a different kind —
  the referential markers in `core/followup.py` (pronouns, demonstratives,
  ellipsis). Each was added reactively, after a real question missed. The
  pattern is the argument: every new phrasing family costs a hand-maintained
  word list, and the lists cannot be exhaustive by construction. An **LLM-based
  intent router** would collapse all five into one classification call and is
  the natural home for the follow-up gate too (resolving "he" is exactly what a
  language model is for). Still post-deploy and eval-gated: it must clear the
  same guard the keyword router does — 0/32 pinned questions routed live, now
  also with a conversation frame present — and its latency has to fit inside a
  path that already spends ~800 ms embedding and up to ~16 s on OP.GG.
- **Phase 1 SHIPPED (2026-07-31): conversational carryover.** Follow-ups that
  named no champion ("what about some more champs he's good against") linked no
  entity and refused. A resolved frame from the previous ANSWERED turn — champion,
  lane, kind, and the champions that answer named — is minted server-side,
  returned in the `done` event and echoed back by the client, so `/ask` stays
  stateless. The gate is deliberately narrow: normal classification runs first
  and its result is final, so carryover can only ever rescue a question that
  would otherwise have gone unanswered; a question must positively look
  referential (absence of an entity is not enough); an unmentioned champion is a
  new subject; frames expire after 10 minutes and never cross corpus or patch.
  Scored in `eval_live.py` in both directions, including the negative case
  ("how do I climb in ranked" must not carry over). Open follow-ups: ordinal
  references ("the first one"), substitution ("what about Yasuo" meaning the
  same question for a different champion), and carryover for the structured and
  prose paths — today it is live-intent only.
- **Resolved (2026-07-31) — duplicate champion display names.** Every live
  question about Garen degraded to "OP.GG doesn't have current stats for
  Garen", while other champions worked and direct MCP calls for `GAREN`
  returned complete data. Cause: 16.15.1's `Jade_Garen` shares the display
  name "Garen", the entity index was keyed on display name, and the SELECT had
  no ORDER BY — so an arbitrary winner took the key and we asked OP.GG about
  `JADE_GAREN`, which exists but has no stats. Fixed at both ends (ingest
  filter + canonical resolution in the index) from one shared rule in
  `core/lol_roster.py`. *Worth remembering:* the empty-payload guard behaved
  exactly as designed and still produced a wrong answer, because it was handed
  the wrong question — and the message named the RIGHT champion, which hid the
  id mismatch for three rounds of debugging.
- **Now open:** `text-embedding-3-large` measured best of five (86.3% hit@1,
  the only arm with zero release-notes and zero `/reference/` rank-1 captures).
  It was not adopted — +3.9 pts over 3-small for 8× the vector width and ~6.5×
  the embed cost. Revisit only with an eval that shows the gap surviving the
  reranker end-to-end.
- **Now open:** the release-notes exclusion default was shown to be largely a
  *MiniLM* crutch (+4.0 pts to MiniLM, +0.0 to 3-small/3-large, which never put
  a release-notes page at rank 1). It is retained unchanged for now — see
  eval/results.md for the re-measurement under the new embeddings.
- **Eval provenance policy:** results.md names BOTH the generator and the
  judge model for every run (a result is only interpretable if you know
  what produced and what graded it). The LoL eval **generates with the
  primary (openai/gpt-4o-mini via OpenRouter)** and **judges faithfulness
  with google/gemini-2.5-flash** — an independent family, which retires
  the earlier self-judging caveat (still report as directional). *(The
  originally-planned `gemini-2.0-flash-001` was retired from OpenRouter;
  `gemini-2.5-flash` is the stable same-family substitute.)* Historical
  FastAPI runs stay labeled with their original phi-4 self-judge
  provenance; they are not retro-rescored.
- **Phase 4h SHIPPED (2026-07-25): live OP.GG meta stats** (matchups /
  win-rate / popularity) via the sanctioned OP.GG MCP, live-queried and
  never cached (§5.7). This supersedes the earlier "matchup data" idea
  below — done as a live third-party path, not a cached structured table.
  Open follow-ups: (1) latency is upstream-bound (~7s cold), so cold
  champions can still degrade; a background refresh/expanded pre-warm
  could reduce that. (2) the live-intent classifier is keyword/rule-based
  (core/lol_routing.py); each new phrase family has needed a hand-added
  trigger group (win-rate → good-against → role/lane), so an **LLM-based
  intent router** is logged as a candidate to generalize the trigger
  surface — post-deploy and eval-gated, since it trades determinism for
  coverage.
- Deploy (owner): VPS + domain + OpenRouter credit + runbook; ships
  LoL-only + the live OP.GG path (needs outbound HTTPS to `mcp-api.op.gg`);
  flip repo public; swap demo URL; pin repo.
- Post-deploy: Palworld face (4e/4f, licensing-gated wiki adapter);
  candidate extension logged: identifier-literal supplementary test set.
