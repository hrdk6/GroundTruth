# Progress

Phase checklist from `PROJECT_SPEC.md` §12.

Legend: `[x]` done and verified · `[~]` built, partly verified · `[ ]` not done

> ## Read this first
>
> **Everything runs end to end, measured, for $0.00 — and was audited.** A
> review of the evaluation harness against its own claims found that the
> project's headline result was a measurement bug: the baseline's chunker
> stored `tokenizer.decode()` output, so most gold quotes could not match any
> baseline chunk. Corrected, the dense baseline scores recall@5 **0.857** dev /
> **0.700** test, and **no retrieval change is distinguishable from noise** on
> this golden set. EXPERIMENTS.md has the twelve measurement defects and what
> each did to the numbers.
>
> Every record in `experiments/` comes from a clean tree after the audit, and
> carries a gold-integrity audit and 95% intervals. The pre-audit records are
> kept in `experiments/superseded/` and are not results.
>
> **Still not done: judge validation.** `answer_correctness` is the answering
> model grading itself, and the audit showed it flipping a verdict on a
> formatting detail. It needs ~50 human labels
> (`python -m evals.judge.label --count 50`) and a Cohen's kappa.

---

## Phase 0 — Scaffold  `[x]`

- [x] Repo structure, `uv` project, Python 3.12, Docker Compose, Alembic
- [x] Two-layer config, cached cost-aware LLM client, Makefile + `make.ps1`
- [x] `GET /health` reports `ok` against a live database with pgvector
- [x] Migrations `0001`–`0003` apply cleanly to an **empty** database (verified
      in the local CI simulation; the integration tests use `create_all`)
- [ ] `make up` unverified — Docker cannot start on this machine. The Compose
      fixes from the audit were made by inspection (LIMITATIONS L19).

## Phase 1 — Ingestion + naive RAG baseline  `[x]`

- [x] Fetched all three branches (3,102 pages); evaluated subset is
      `concepts/` + `tasks/`, 1.26 and 1.30: 639 pages
- [x] `fixed` and `structure_aware` chunkers, cutting text **verbatim** from
      the source (audit fix); 3,882 and 7,177 chunks
- [x] **Acceptance met, with two chunk sets:** a second ingest of each embeds
      0 chunks (1.7 s), and an edit reaches *every* chunk set (audit fix)
- [x] Ingestion checkpoints every 512 chunks, so a long run resumes

## Phase 2 — Evaluation harness + baseline numbers  `[x]`

- [x] Metrics over the full ranked list; context recall separately (audit fix)
- [x] Gold-integrity audit on every run; CI fails when a quote is unmatchable
- [x] Bootstrap intervals on every metric; `make compare` for paired tests
- [x] Golden set: 32 curated items, every quote verified against source *and*
      against the chunks of both chunk sets
- [x] **Baseline recorded** (corrected): recall@5 0.857 dev / 0.700 test
- [ ] ~300-item generated set — generators built and cost-gated; `build` now
      merges drafts instead of overwriting the curated set

## Phase 3 — Retrieval improvements  `[x]`

Six configs, both splits, one commit. Paired comparisons in EXPERIMENTS.md.

| Config | recall@5 dev | recall@5 test | Verdict against its predecessor |
|---|---|---|---|
| `baseline` | 0.857 | 0.700 | — |
| `structure_aware` | 0.786 | 0.700 | noise |
| `hybrid_all_terms` | 0.786 | 0.700 | noise (lexical leg empty for 20/32 questions) |
| `hybrid` | 0.714 | 0.600 | noise (any-term, no IDF) |
| `hybrid_bm25` | 0.714 | 0.700 | noise; **shipped** — fixes the fixture regression |
| `hybrid_rerank` | 0.714 | 0.700 | noise, at ~100× latency — off |

- [x] Lexical leg: any-term matching and **BM25 computed in SQL**, checked
      against an independent Python BM25 to 1e-9
- [x] Dense leg returns `k` despite pgvector 0.6.2's post-filtering (audit fix)

## Phase 4 — Generation quality  `[~]`

- [x] Grounded prompt, per-sentence verification (concurrent), regenerate-or-abstain
- [x] Version detection (bare `1.28` included) and conflict notes on **cited**
      sections only
- [x] Judge, Cohen's kappa, labeling CLI that shows reference and citations and
      ties each label to the exact answer
- [x] **Measured** (`full`, versions resolved from the question):

  | Metric | dev | test |
  |---|---|---|
  | answer_correctness (self-judged) | 0.842 | 0.923 |
  | faithfulness | 0.974 | 0.942 |
  | citation_precision | 1.000 | 0.900 |
  | abstention recall / precision | 1.000 / 0.625 | 1.000 / 0.750 |

- [ ] **Judge validation — the outstanding gap.**

## Phase 5 — Observability  `[x]`

- [x] Span per stage including verification, with outputs for version,
      decomposition and rewrite; failed queries leave a trace (audit fix)
- [x] `GET /traces` in one query; `POST /feedback` validates the trace

## Phase 6 — Frontend  `[x]`

- [x] Ask renders server-side sentence segments with verdicts; defaults to `full`
- [x] Trace viewer: a real waterfall on one time axis, and a rank trail
- [x] Experiments: intervals, dirty-tree and recall-ceiling markers, paired
      deltas labelled *real* / *noise*, optional superseded runs
- [x] `tsc --noEmit` and `next build` clean, from a fresh clone

## Phase 7 — CI, docs, demo  `[~]`

- [x] CI: lint, types, tests, fixture ingest, retrieval eval, gate — all run
      locally end to end against a fresh, migrated database; the gate passes
- [x] README table generated from `experiments/`; `--check` wired into CI
- [x] ARCHITECTURE (18 decisions), LIMITATIONS (25 entries), DEMO_SCRIPT
- [x] **A fresh clone installs, lints, type-checks, passes all 252 tests, and
      builds the frontend** — which it could not before the audit, because
      `.gitignore` had kept `backend/app/models/` out of every commit
- [ ] CI has never run on GitHub — no remote is configured

---

## Bugs found by running it

Each was invisible to unit tests. The first nine predate the audit; the rest
were found by it.

| Bug | Consequence | Caught by |
|---|---|---|
| Sentence splitter orphaned a trailing `[3]` citation | correctly-cited answers abstained away | the bimodal `support_fraction` distribution |
| Citation regex missed full-width `【1】` brackets | an answer citing every sentence read as citing none | reproducing a false abstention |
| Integration tests truncated the developer's own database | `make test` silently wiped an ingest | an empty database |
| The gate compared runs from different golden sets | a build failed for no regression | running it after a generation eval |
| `websearch_to_tsquery(varchar, varchar)` does not exist | all lexical retrieval failed | integration test |
| A tombstoned document was never resurrected | a page invisible to retrieval forever | restoring a corpus |
| Unanswerable items scored `false_answer` in retrieval-only mode | five invented failures | reading the first baseline run |
| The gate crashed on Windows (`✓` in cp1252) | gate unusable locally | running it |
| `min_tokens >= max_tokens` dropped every chunk but the first | most of a document lost | chunker unit test |
| **Chunk text was `tokenizer.decode(ids)`** | baseline recall capped at ~0.3; the headline result | counting matchable quotes per chunk set |
| **`app/models/` was gitignored** | no clone could import the ORM | re-running an old commit in a worktree |
| Recall@10 computed over the 5-chunk context | a column that could not differ from recall@5 | the README table |
| pgvector filters after the HNSW scan | dense leg returned fewer than `k` | checking a docstring's claim |
| Lexical leg ANDed every term, `-o` meant NOT | empty for 20 of 32 questions | counting hits per question |
| Chunk freshness judged per document | only the first chunker saw an edit | a re-ingest reporting all 639 pages unchanged |
| HTML comments and heading shortcodes kept | 103 wordless chunks, contributor TODOs as evidence | the BM25 reference test disagreeing about N |
| The runner forced each item's version | version correctness a tautology, conflicts never exercised | asking how the metric could fail |
| Conflicts checked against every chunk of a section / the whole context | 245 "conflicts" where 38 were real; notes on uncited sections | a test asserting only `isinstance(list)` |
| Attribution used ANY gold where recall needs ALL | multi-hop failures labelled success; justified abstentions labelled false | multi-hop tests |
| The judge saw citation markers | a correct answer docked for "incorrect indices" | reading a test-split failure |
| Each run's output made the next run "dirty"; SHA read at the end of a run | no record reproducible from its SHA | reading the records |

## Verified on this machine

| | |
|---|---|
| Tests | **252 (227 unit + 25 integration), all passing** — in the repo *and* in a fresh clone |
| Lint / types | `ruff` and `mypy` clean, 58 source files |
| Frontend | `tsc --noEmit` and `next build` clean, fresh clone |
| Database | PostgreSQL 16.2 + pgvector 0.6.2 via `pgserver`, migrations from empty |
| Corpus | 639 pages; 3,882 `fixed` + 7,177 `structure_aware` chunks, all verbatim, gold 26/26 matchable in both |
| Experiments | 16 current records (12 retrieval, 2 generation, 2 fixture); 16 superseded |
| CI gate | passes on the shipping retrieval config, locally |
| LLM spend | **$0.00** (free tier) |
