# Janus: Full Project Specification

A retrieval-augmented generation (RAG) platform with one shared core engine
and two deployed corpus "faces":

1. **FastAPI Docs Assistant** — a developer support bot that answers
   "how do I do X in FastAPI?" with grounded explanations, runnable code
   snippets, and citations to exact doc pages. The industry-relevant headline.
2. **LoL Wiki Assistant** — a knowledge assistant over League of Legends
   game data and mechanics, combining structured stats (Riot Data Dragon)
   with prose mechanics explanations. The technically-harder, memorable demo.

Built as a portfolio project. Goals in priority order: (1) owner learns RAG
mechanics deeply, (2) resume-credible engineering with real evaluation
numbers, (3) live deployed demo.

---

## Global constraints

- **No LangChain / LlamaIndex / RAG frameworks.** Hand-rolled mechanics are
  the point. Direct use of sentence-transformers, psycopg, OpenAI SDK only.
- Python 3.11+, type hints on public functions, comments explain WHY not what.
- Windows 11 dev machine, VS Code, Docker Desktop (WSL2), RTX 2070 (8GB).
  Local embeddings are fine; local LLM generation is out of scope (API only).
- Postgres + pgvector is the only datastore.
- Every phase ends with something runnable and demoable. Do not start a
  phase until the previous phase's verification passes.
- Secrets live in `.env` (gitignored); `.env.example` documents every var.

## Stack

| Layer | Choice | Rationale |
|---|---|---|
| Embeddings | sentence-transformers `all-MiniLM-L6-v2` (384-dim, local) | Free, fast, CPU-friendly; owner learns model-agnostic design via config |
| Vector store | Postgres 16 + pgvector | Owner knows Postgres; one DB for vectors, full-text, and metadata |
| Generation | OpenAI API (model set in config, e.g. gpt-4o-mini) | Cheap, fast to integrate; keeps focus on retrieval quality |
| Reranking | cross-encoder `cross-encoder/ms-marco-MiniLM-L-6-v2` (local) | Standard, light enough for CPU |
| API | FastAPI + Uvicorn | Fitting for the docs face; industry standard |
| Frontend | React (Vite), plain fetch + SSE for streaming | Owner's existing skill |
| Packaging | Docker Compose (db, api, frontend) | One-command run; VPS deploy target |

## Repository layout (final state)

```
janus/
  core/                      # domain-agnostic engine — knows nothing about
    __init__.py              # FastAPI-the-docs or LoL
    config.py                # env loading; all settings live here
    embeddings.py            # lazy-singleton embedder
    store.py                 # all Postgres access (schema, insert, search)
    chunking.py              # markdown/code-aware chunker
    retrieval.py             # vector, keyword, hybrid, rerank pipeline
    generation.py            # prompt assembly, OpenAI call, citation extraction
  ingestion/
    __init__.py
    fastapi_docs.py          # clone/parse FastAPI docs at a pinned version
    lol_datadragon.py        # fetch/normalize Data Dragon JSON at a pinned patch
    run_ingest.py            # CLI: python -m ingestion.run_ingest --corpus fastapi --version 0.115.0
  app/
    __init__.py
    main.py                  # FastAPI app factory, CORS, routes
    routes.py                # /ask (SSE streaming), /sources, /corpora, /health
    schemas.py               # Pydantic request/response models
  eval/
    __init__.py
    testset_fastapi.jsonl    # labeled Q → expected source page(s)
    testset_lol.jsonl
    evaluate.py              # hit@k, MRR, faithfulness; prints comparison table
  frontend/                  # Vite React app
    src/
      App.jsx                # chat UI, corpus/version switcher
      Citations.jsx          # retrieved-chunks panel with scores + links
      api.js                 # SSE client
  docker-compose.yml         # db only (dev) — override adds api+frontend (deploy)
  docker-compose.deploy.yml
  Dockerfile.api
  Dockerfile.frontend
  requirements.txt
  .env.example
  .gitignore
  README.md                  # includes architecture diagram + eval results table
  smoke_test.py
```

---

# Phase 1 — Retrieval core + skeleton

**Goal:** embed → store in pgvector → similarity search, verified by smoke test.

### Deliverables

- Full repo skeleton above (later-phase files as TODO-docstring placeholders).
- `docker-compose.yml`: `pgvector/pgvector:pg16`, container `janus_db`,
  user `rag` / password `ragpass` / db `ragdb`, port 5432, named volume.
- `core/config.py`: loads `.env`; exposes `DATABASE_URL`, `EMBED_MODEL`,
  `EMBED_DIM` (int), later `OPENAI_API_KEY`, `GEN_MODEL`, `RERANK_MODEL`.
- `core/embeddings.py`: lazy singleton; `get_model()`, `embed_texts(list[str])`,
  `embed_query(str)`. Docstring: same model for queries and documents, load once.
- `core/store.py`:
  - `init_schema()` — `CREATE EXTENSION IF NOT EXISTS vector`; table `chunks`:
    `id SERIAL PK, corpus TEXT NOT NULL, source_url TEXT, heading_path TEXT,
    doc_version TEXT, content TEXT NOT NULL, embedding vector(EMBED_DIM)`,
    plus a GIN index on `to_tsvector('english', content)` for phase-2 keyword
    search, and an index on `(corpus, doc_version)`.
  - `insert_chunks(rows)` — executemany; embedding list passed as `str()`.
  - `search_chunks(query_embedding, corpus, top_k=5, doc_version=None)` —
    cosine via `<=>`, corpus filter, optional version filter, returns
    `(id, content, source_url, heading_path, similarity)`.
- `smoke_test.py`: seeds 5 FastAPI-concept chunks + 2 unrelated sentences,
  searches "how do I use query parameters?", asserts the right chunk ranks
  first and unrelated ones score < 0.3. Prints `SMOKE TEST PASSED` / exits 0.

### Verification
`docker compose up -d` → `pip install -r requirements.txt` → copy `.env` →
`python smoke_test.py` passes.

---

# Phase 2 — Real corpus, real retrieval, real numbers

**Goal:** ingest actual FastAPI docs with code-aware chunking; add hybrid
search + reranking + generation; measure everything.

## 2a. Chunking (`core/chunking.py`)

- Input: markdown text + source metadata. Output: list of chunk dicts
  `{content, heading_path, source_url, doc_version}`.
- Split on markdown heading structure; every chunk keeps its full heading
  path ("Tutorial > Query Parameters > Optional parameters").
- **Never split inside a fenced code block.** A code block always travels
  with the prose immediately preceding it.
- Target chunk size ~300–500 tokens (approximate by chars ÷ 4 is fine),
  with small chunks merged up into their heading neighbor and oversized
  sections split at paragraph boundaries with ~15% overlap.
- Unit tests: heading-path correctness; a code block is never severed; a
  tiny section merges upward.

## 2b. FastAPI docs ingestion (`ingestion/fastapi_docs.py`, `run_ingest.py`)

- Shallow-clone `fastapi/fastapi` at a **pinned release tag** (configurable,
  e.g. `0.115.0`); parse `docs/en/docs/**/*.md`.
- Map each file to its live docs URL for citations
  (e.g. `docs/en/docs/tutorial/query-params.md` →
  `https://fastapi.tiangolo.com/tutorial/query-params/`).
- Stamp every chunk with `doc_version` = the release tag.
- CLI: `python -m ingestion.run_ingest --corpus fastapi --version 0.115.0`.
  Idempotent: re-running a (corpus, version) pair deletes and re-inserts
  that pair only.
- Expected scale: a few thousand chunks; batch the embedding calls.

## 2c. Retrieval pipeline (`core/retrieval.py`)

Composable functions, each independently testable:

- `vector_search(query, corpus, k)` — wraps store.search_chunks.
- `keyword_search(query, corpus, k)` — Postgres full-text
  (`plainto_tsquery` + `ts_rank`) against the GIN index. Exists because
  exact tokens (`Depends`, `response_model`) embed poorly.
- `hybrid_search(query, corpus, k)` — run both, merge with Reciprocal Rank
  Fusion (RRF, k=60). RRF chosen over score mixing because vector and
  ts_rank scores aren't comparable scales.
- `rerank(query, candidates, top_n)` — cross-encoder scores each
  (query, chunk) pair; keep top_n. Lazy-singleton the reranker model.
- `retrieve(query, corpus, doc_version=None)` — default pipeline:
  hybrid top-20 → rerank → top-5. Each stage's output loggable for debugging.

## 2d. Generation (`core/generation.py`)

- `build_prompt(question, chunks)` — system prompt requires: answer ONLY
  from provided context; include a runnable code example when relevant;
  cite sources as [1], [2] mapping to the provided chunk list; if the
  context doesn't contain the answer, say so explicitly and do not guess.
- `generate(question, chunks)` — OpenAI chat completion; returns
  `{answer, citations: [{index, source_url, heading_path}]}`.
- `generate_stream(...)` — same but yields tokens (for phase-3 SSE).
- Refusal behavior is a feature, not an afterthought: if the top rerank
  score is below a config threshold, short-circuit to "the docs don't
  appear to cover this" without calling the LLM.

## 2e. Evaluation (`eval/`)

- `testset_fastapi.jsonl`: 40–60 entries
  `{"question": ..., "expected_urls": [...], "type": "howto|concept|api"}`.
  Questions sourced from real Stack Overflow / GitHub discussions phrasing
  (paraphrased), written by the owner — this is a manual curation task the
  spec flags for the human.
- `evaluate.py` measures, for each retrieval config
  (vector-only / hybrid / hybrid+rerank):
  - **hit@k** (k=1,3,5): expected URL appears in top-k retrieved chunks
  - **MRR** over expected URLs
  - optional **faithfulness**: LLM-as-judge scores whether the generated
    answer is supported by retrieved context (1–5 scale, judge prompt in repo)
- Output: a markdown comparison table written to `eval/results.md` — this
  table goes in the README and its deltas go on the resume.

### Verification
Ingestion completes on the pinned tag; `evaluate.py` runs all three configs
and writes the table; hybrid+rerank ≥ vector-only on hit@5 (if not,
investigate before proceeding); asking a question via a REPL script returns
a grounded, cited answer; an off-corpus question triggers the refusal path.

---

# Phase 3 — Web app + deployment

**Goal:** the live demo. Chat UI with streaming and a citations panel,
dockerized, deployed on a VPS.

## 3a. API (`app/`)

- `POST /ask` — body `{question, corpus, doc_version?}`; **SSE stream**:
  first event `sources` (the retrieved chunks with scores, urls, headings),
  then `token` events, then `done`. Sources-first lets the UI render the
  citations panel while the answer streams.
- `GET /corpora` — available corpora + ingested versions (drives UI dropdowns).
- `GET /health` — checks DB connectivity and model loaded.
- CORS configured from env. Basic per-IP rate limit (simple in-memory
  token bucket) — it's a public demo with a paid API key behind it.
- Log every request: question, corpus, retrieval scores, latency breakdown
  (retrieve ms / rerank ms / LLM ms) as JSON lines. This is the "monitoring"
  story and real debugging data.

## 3b. Frontend (`frontend/`)

- Single-page chat: question input, streamed answer (rendered markdown with
  syntax-highlighted code blocks), and a **citations side panel** showing
  each retrieved chunk with similarity score, heading path, and link out.
  Clicking citation [n] in the answer highlights the matching panel entry.
- Header: corpus switcher (FastAPI / LoL) + version dropdown, driven by
  `/corpora`.
- Deliberately minimal styling budget — clean, readable, not a design project.

## 3c. Packaging + deploy

- `Dockerfile.api` (python-slim, non-root), `Dockerfile.frontend`
  (build → nginx serve), `docker-compose.deploy.yml` adding api + frontend +
  reverse-proxy (Caddy for automatic HTTPS) in front.
- Ingestion runs on the dev machine or as a one-off container command —
  the VPS never needs the embedding of a full corpus at request time,
  but the API container does need the embed + rerank models for queries
  (bake them into the image at build time to avoid cold downloads).
- Target: cheapest Hetzner/DigitalOcean VPS. Document deploy steps in README.
- `.env` on the server holds the OpenAI key; never in the image.

### Verification
`docker compose -f docker-compose.deploy.yml up` serves the full app locally;
deployed URL answers a question end-to-end with streaming + citations;
rate limit demonstrably kicks in.

---

# Phase 4 — The LoL face

**Goal:** second corpus demonstrating structured+unstructured retrieval,
entity linking, and reuse of the whole engine.

## 4a. Data Dragon ingestion (`ingestion/lol_datadragon.py`)

- Fetch Data Dragon JSON for a **pinned patch** (e.g. `14.10.1`): champions
  (with per-ability text and numbers), items, runes, summoner spells.
- Two write paths:
  1. **Structured tables**: `champions`, `abilities`, `items` with typed
     columns (cooldowns, costs, stats as JSONB where ragged). Source of
     truth for numeric lookups.
  2. **Prose chunks**: ability/item descriptions and passive texts rendered
     into readable chunks (corpus `lol`, doc_version = patch), embedded like
     any other corpus so conceptual questions work.
- Strip Data Dragon's HTML-ish markup tags from description text.

## 4b. Entity linking + routing (extends `core/retrieval.py`)

- Build a name dictionary from Data Dragon (champion names, ability names,
  item names + common aliases). `link_entities(query)` → matched entities
  via normalized/fuzzy match.
- `route(query)` heuristic:
  - entities + numeric-intent words (cooldown, cost, damage, "at rank/level")
    → structured lookup first, formatted into context
  - otherwise → standard hybrid retrieval over `lol` chunks
  - both paths can contribute context to one generation call
- This mirrors enterprise catalog+docs RAG; say so in the README.

## 4c. Wiki prose (optional stretch)

- If pursued: small, respectful ingestion of mechanics explanations
  (rate-limited, cached, attributed; respect robots.txt and wiki licensing —
  CC BY-SA requires attribution, include it in citations). If licensing or
  ToS is unclear for any source, skip it — Data Dragon alone is sufficient
  for the demo.

## 4d. Eval + UI

- `testset_lol.jsonl`: ~30 questions across numeric lookups, ability
  mechanics, item interactions. Same evaluate.py, same metrics table.
- Frontend needs zero structural change: LoL appears in the corpus switcher,
  patch versions in the version dropdown. That "zero change" is the
  shared-engine proof point.

### Verification
"What does Yasuo's passive do?" answers from ability text with citation;
"which items give armor penetration?" returns a correct structured-path
answer; eval table for LoL written; both corpora usable side by side in
the deployed app.

---

# Cross-cutting requirements

## README (treat as a deliverable, not an afterthought)

- One-paragraph pitch + architecture diagram (ASCII or image)
- The eval results table with a short honest analysis (where hybrid helped,
  where reranking didn't, known failure modes)
- Setup, ingestion, deploy instructions
- A "design decisions" section: why pgvector, why RRF, why no framework,
  why version pinning — written by the owner, in the owner's words

## Testing

- Unit tests: chunking invariants, RRF merge, entity linking, prompt
  builder citation numbering
- One integration test: seed → retrieve → assert ranking (the smoke test
  grows into this)
- CI (GitHub Actions): lint + unit tests on push. Integration/eval stay local.

## Explicitly out of scope (do not build)

- Agentic / multi-hop retrieval, conversation memory, user accounts,
  feedback buttons, local LLM generation, multiple embedding models,
  A/B infrastructure, Kubernetes. A "future work" README section may
  mention them.

## Resume framing (target bullets — numbers filled from eval/results.md)

- Built and deployed a RAG platform (Python, FastAPI, pgvector, React)
  serving grounded, cited Q&A over two corpora from one shared engine
- Implemented hybrid retrieval (dense + full-text, RRF) with cross-encoder
  reranking, improving hit@5 from X% to Y% on a hand-labeled eval set
- Designed version-aware, code-preserving ingestion and structured+vector
  query routing (Riot Data Dragon + embedded prose)

---

# Build order and human checkpoints

| # | Milestone | Human (owner) tasks |
|---|---|---|
| 1 | Phase 1 skeleton + smoke test | Read core/ modules line by line; confirm understanding |
| 2 | Chunker + ingestion | Spot-check 10 random chunks against live docs |
| 3 | Hybrid + rerank + generation | Try 10 manual questions incl. 2 refusal cases |
| 4 | Eval harness | **Write the test set** (LLM may draft, owner curates every entry) |
| 5 | API + frontend | Manual QA of streaming + citation highlighting |
| 6 | Deploy | Buy VPS, set DNS, hold the API key |
| 7 | LoL ingestion + routing | Sanity-check numbers vs. in-game/patch notes |
| 8 | LoL eval + polish | Final README design-decisions section |

The owner-task column is non-negotiable: this is a learning and portfolio
project, and interview credibility depends on the owner being able to
explain and defend every layer.
