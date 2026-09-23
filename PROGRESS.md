# Progress

Phase checklist from `PROJECT_SPEC.md` §12.

Legend: `[x]` done and verified · `[~]` built, partly verified · `[ ]` blocked

> ## Read this first
>
> **Everything runs, end to end, measured, for $0.00.** Nine experiments are
> recorded in `experiments/`, covering retrieval *and* generation.
>
> Neither original blocker survived. Docker could not start (virtualization is
> disabled in firmware), so Postgres runs via `pgserver` — a real PostgreSQL
> 16.2 + pgvector as a wheel, no container (`make db-local`). There was no
> Anthropic key, so the LLM client became provider-pluggable and runs on
> NVIDIA NIM's free tier.
>
> **The one thing that is NOT done: judge validation.** `answer_correctness`
> was produced by the same model that wrote the answers. That is
> self-evaluation and it inflates. It needs ~50 human labels
> (`python -m evals.judge.label --count 50`) and a Cohen's kappa. Treat every
> correctness figure as provisional until then.
>
> Scope caveats on every number: 32-item golden set (one item ≈ 5 points of
> recall on dev), 639-page corpus subset, one model doing both generation and
> judging.

---

## Phase 0 — Scaffold  `[x]`

- [x] Repo structure, `uv` project, Python 3.12, Docker Compose, Alembic
- [x] Two-layer config, cached cost-aware LLM client, Makefile + `make.ps1`
- [x] **`GET /health` returns `status: "ok"`** against a live database with
      pgvector — the Phase 0 acceptance criterion, met
- [x] Migrations `0001`–`0003` applied to a live database
- [ ] `make up` still unverified — Docker cannot start here. `make db-local` is
      the working substitute and is what CI's compose path is checked against.

## Phase 1 — Ingestion + naive RAG baseline  `[x]`

- [x] Fetched all three branches: **3,102 pages on disk**
- [x] Hugo-aware parsing; `fixed` and `structure_aware` chunkers
- [x] Ingested **639 documents → 3,905 chunks** (`fixed`), 7,277 (`structure_aware`)
- [x] **Acceptance met:** a re-run over the unchanged corpus reported
      `0 written, 0 embedded` and took **1.9s against 379s** for the first pass
- [x] Dense retrieval and `POST /query` implemented
- [x] Cited answers generated and verified end to end

## Phase 2 — Evaluation harness + baseline numbers  `[~]`

- [x] Metrics, attribution, runner, experiment records, results-table script
- [x] Golden set: **32 curated items**, every quote verified against source
- [x] Fixture golden set: 8 items, used by the CI gate
- [x] **Baseline recorded** on dev: recall@5 **0.286**, MRR 0.274
- [ ] ~300-item LLM-generated set and its curation pass — the generators are
      built and cost-gated; scaling the set up is the next real task

## Phase 3 — Retrieval improvements  `[x]`

Four experiments run, each moving one variable. Full analysis in
[EXPERIMENTS.md](EXPERIMENTS.md).

| Config | recall@5 (dev) | Verdict |
|---|---|---|
| `baseline` | 0.286 | — |
| `structure_aware` | **0.786** | kept (+0.500) |
| `hybrid` | **0.786** | kept — lifted rank, not recall (MRR +0.083) |
| `hybrid_rerank` | 0.714 | **reverted** — 93× latency, new `ranking_miss`; helped on test, hurt on dev, both by one item |

Held out: `baseline` 0.100 → `hybrid` **0.700** on the `test` split.

- [x] `full` run with query rewriting and decomposition: recall@5 **0.857** on
      dev, the best of any config (not isolated to one variable, so an
      observation rather than an experiment)

## Phase 4 — Generation quality  `[~]`

- [x] Grounded prompt, per-sentence verification, regenerate-or-abstain policy
- [x] Version detection and cross-version conflict detection
- [x] Judge, Cohen's kappa, labeling CLI
- [x] **Measured** (`full`, held-out test split, 13 items):

  | Metric | dev | test |
  |---|---|---|
  | answer_correctness | 0.842 | 0.923 |
  | faithfulness | 0.974 | 1.000 |
  | citation_precision | 1.000 | 1.000 |
  | abstention precision / recall | 0.714 / 1.000 | 1.000 / 1.000 |
  | version_correctness | 1.000 | 1.000 |

- [ ] **Judge validation — the outstanding gap.** The judge is the same model
      that wrote the answers, so correctness is self-assessed.

## Phase 5 — Observability  `[x]`

- [x] Span per stage, `GET /traces`, `GET /traces/{id}`, `POST /feedback`
- [x] **Verified through the HTTP API**: a live query records 7 spans, and the
      rank trail reads `chunk 6405 → dense 4 → lexical 2 → rrf 1` — a chunk
      dense ranked 4th that fusion promoted to first. That is the feature
      doing its job on real data.

## Phase 6 — Frontend  `[x]`

- [x] Ask, trace viewer with rank trail, experiments dashboard with deltas
- [x] `tsc --noEmit` and `next build` clean
- [x] **Verified against the live backend**: the dashboard renders the real
      baseline → hybrid comparison (recall@5 +0.500, `table_or_code` +1.000,
      `factual` ±0, retrieval misses 9 → 2)
- [x] Added a Dataset column and a mismatch warning after noticing the UI
      could place a `fixture_golden` run beside a `golden_v1` one as if they
      were comparable

## Phase 7 — CI, docs, demo  `[~]`

- [x] CI: lint, types, tests, fixture ingest, retrieval eval, regression gate
- [x] **Thresholds set from a measured baseline** (recall@5 floor 0.75 against
      an observed 0.833) — the gate can now actually fail
- [x] Gate verified passing locally, and its failure paths unit tested
- [x] README table generated from `experiments/`; `--check` wired into CI
- [x] ARCHITECTURE (11 decisions), LIMITATIONS (16 entries), DEMO_SCRIPT
- [ ] CI has never run — no GitHub remote is configured

---

## Bugs found by running it

Each of these was invisible to unit tests and surfaced only against a live
database or a real corpus.

| Bug | Consequence | Caught by |
|---|---|---|
| Sentence splitter orphaned a trailing `[3]` citation | correctly-cited answers scored 0.0 support and were **abstained away** | the `support_fraction` distribution being bimodal |
| Citation regex missed full-width `【1】` brackets | an answer citing every sentence read as citing none | reproducing a false abstention |
| Integration tests truncated the developer's own database | `make test` silently wiped a 10-minute ingest | an empty database after a test run |
| The gate compared runs from different golden sets | build failed for a reason that was not a regression | running it after a generation eval |
| `websearch_to_tsquery(varchar, varchar)` does not exist | **all lexical retrieval failed** | integration test |
| A tombstoned document was never resurrected when it reappeared unchanged | the page stayed invisible to retrieval permanently | restoring a corpus after a fixture ingest |
| Unanswerable items scored `false_answer` in retrieval-only mode | invented 5 failures the system was never given a chance to make | reading the first baseline run |
| The regression gate crashed on Windows (`✓` in cp1252) | gate unusable locally | running it |
| `min_tokens >= max_tokens` silently dropped every chunk but the first | most of a document lost, no error | chunker unit test |

## Verified on this machine

| | |
|---|---|
| Tests | **123 unit + 12 integration, all passing** |
| Lint / types | `ruff` and `mypy` clean, 53 source files |
| Frontend | `tsc --noEmit` and `next build` clean |
| Database | PostgreSQL 16.2 + pgvector, live, migrated |
| Corpus | 3,102 pages fetched; 639 ingested and embedded |
| Experiments | **9 recorded**, retrieval and generation |
| LLM spend | **$0.00** (free tier, 0 paid calls) |
| Regression gate | passes against measured floors |
