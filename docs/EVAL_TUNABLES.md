# Retrieval tunables: eval outcomes

The 2026-07-18 eval covered the three tunables below over the hand-curated
54-question test set (see `eval/results.md`). Outcomes are recorded
per item. One change was adopted into `core/retrieval.py`; the pipeline is no
longer frozen — changes are now gated on eval evidence.

## 1. `top_n` (final chunks sent to generation) — NOT adopted
- **Default:** `5` — `retrieve(query, corpus, top_n=5)`.
- **Tested:** `5` vs `8`.
- **Outcome:** **no gain.** hit@8 = hit@5 = 96.1% (Δ +0.0); the remaining misses
  are true top-20 misses, not rank 6–8 near-misses, so widening the window
  recovers nothing on this set. Left at `5`.

## 2. Code-chunk rerank weighting — NOT adopted
- Idea: add a small additive rerank margin to chunks containing a fenced code
  block so a grounded code example isn't edged out by conceptual prose.
- **Tested:** margin `+1.0`.
- **Outcome:** **net-hurt.** hit@5 94.1% vs 96.1% base, MRR 0.792 vs 0.797 — the
  margin pushed a code chunk above the correct page in ≥1 case (e.g. "declare a
  response_model" fell rank 4 → 6). Not adopted. A smaller margin or
  code-seeking-only gating might help, but not worth it at `1.0`.

## 3. Corpus composition — release-notes exclusion — ADOPTED as default
Release-notes chunks are API-name-dense (`Depends`, `response_model` appear
constantly) but low in explanatory value, and at **491 of 1582 chunks (~31.0%)**
of the fastapi 0.139.0 corpus they crowd out tutorial prose in the top-k.
- **Tested:** query-time filter dropping `source_url` containing `/release-notes/`
  (filter, **not** deletion), legs oversampled so the kept pool stays full.
- **Outcome:** **best change in the eval — adopted.** vs the `hybrid+rerank`
  base: **hit@1 66.7% → 74.5% (+7.8 pts), MRR 0.797 → 0.837 (+0.040)**, no metric
  degraded. Promoted to the default in `core/retrieval.py`
  (`DEFAULT_EXCLUDE_URL_SUBSTR="/release-notes/"`, `retrieve()`); the default path
  reproduces the eval row **exactly** (74.5% / 92.2% / 96.1% / 96.1% / 0.837,
  off-corpus 3/3). Origin: 2026-07-17 manual spot-check flagged release-notes
  chunks as only partially reviewable.

## 4. Identifier-literal supplementary test set — future work
The keyword-leg decomposition (`eval/results.md`) showed that **once
release-notes are excluded, the keyword (full-text) leg adds no value on the
current set** (hybrid+excl vs vector-only+excl: hit@1 −2.0, MRR −0.009). But the
curated 54-question set is fairly natural-language, which under-represents the
keyword leg's real job: exact-identifier lookups. Before any decision to drop the
keyword leg, build a **~15-question identifier-literal supplementary set** —
questions that hinge on a verbatim symbol, e.g. *"what does
`response_model_exclude_unset` do"*, *"`OAuth2PasswordRequestForm` fields"*,
*"`status_code=status.HTTP_201_CREATED`"* — and re-run the decomposition. If the
keyword leg earns its keep there, keep it; if not, that's the evidence to drop
it. Do **not** touch the hybrid pipeline until this runs.

## Evidence motivating this (2026-07-12)
After resolving the MkDocs `{* ... *}` code includes, the fastapi 0.139.0
corpus holds real code examples (1608 chunks, up from 1498). But for the
conceptual query *"How do I use dependency injection with Depends?"* the
reranker put prose above code — the first Python chunk landed at **rank #6**,
one slot outside the top-5 window — so the model synthesized code instead of
quoting the docs. A code-seeking phrasing pulled the real chunk into top-5 and
the model reproduced the `common_parameters` example verbatim. So this is a
ranking/cutoff artifact, not an ingestion gap — exactly what the eval should
quantify before any default is changed.
