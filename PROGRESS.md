# Progress

Phase checklist from `PROJECT_SPEC.md` §12.

Legend: `[x]` done and verified · `[~]` built, partly verified · `[ ]` blocked

> ## Read this first
>
> **Retrieval is built and measured. Generation is built and unmeasured.**
>
> The dev machine cannot run Docker — WSL2 will not start because hardware
> virtualization is disabled in firmware. That was worked around with
> `pgserver`, a real PostgreSQL 16 + pgvector shipped as a wheel, which runs as
> an ordinary process with no container (`make db-local`). Every retrieval
> number in this repo was produced that way.
>
> What is still blocked is **everything that calls a model**: there is no
> `ANTHROPIC_API_KEY` in this environment. So golden-set generation, answer
> generation, claim verification, conflict notes and the LLM judge are
> implemented and unit tested but have never run. No correctness, faithfulness,
> citation-precision, abstention or judge-agreement figure exists anywhere, and
> those sections say so rather than estimating.
>
> Two caveats bound every measured number:
> **the corpus was scoped** to `concepts/` + `tasks/` across versions 1.26 and
> 1.30 (639 pages), because embedding all 3,102 pages on CPU ran over two
> CPU-hours without finishing; and **the golden set is 32 hand-authored items**,
> not the ~300 the spec targets, because the LLM generators need a key. At 19
> dev items one item is worth ~5 points of recall.

---

## Phase 0 — Scaffold  `[x]`

- [x] Repo structure, `uv` project, Python 3.12, Docker Compose, Alembic
- [x] Two-layer config, cached cost-aware LLM client, Makefile + `make.ps1`
- [x] `GET /health` — verified live, reports `degraded` without a database
- [x] Migrations `0001`–`0003` applied to a live database
- [ ] `make up` still unverified — Docker cannot start here. `make db-local` is
      the working substitute and is what CI's compose path is checked against.

## Phase 1 — Ingestion + naive RAG baseline  `[x]`

- [x] Fetched all three branches: **3,102 pages on disk**
- [x] Hugo-aware parsing; `fixed` and `structure_aware` chunkers
- [x] Ingested **639 documents → 3,905 chunks** (`fixed`), 6,929 (`structure_aware`)
- [x] **Acceptance met:** a re-run over the unchanged corpus reported
      `0 written, 0 embedded` and took **1.9s against 379s** for the first pass
- [x] Dense retrieval and `POST /query` implemented
- [ ] A cited *answer* has never been generated — needs an API key

## Phase 2 — Evaluation harness + baseline numbers  `[~]`

- [x] Metrics, attribution, runner, experiment records, results-table script
- [x] Golden set: **32 curated items**, every quote verified against source
- [x] Fixture golden set: 8 items, used by the CI gate
- [x] **Baseline recorded** on dev: recall@5 **0.286**, MRR 0.274
- [ ] ~300-item LLM-generated set and its curation pass — need an API key

## Phase 3 — Retrieval improvements  `[x]`

Four experiments run, each moving one variable. Full analysis in
[EXPERIMENTS.md](EXPERIMENTS.md).

| Config | recall@5 (dev) | Verdict |
|---|---|---|
| `baseline` | 0.286 | — |
| `structure_aware` | **0.786** | kept (+0.500) |
| `hybrid` | **0.786** | kept — lifted rank, not recall (MRR +0.083) |
| `hybrid_rerank` | 0.714 | **reverted** — worse on every metric, 93× latency |

Held out: `baseline` 0.100 → `hybrid` **0.700** on the `test` split.

- [ ] `hybrid_rerank_rewrite` and `full` — query rewriting and decomposition
      need an API key

## Phase 4 — Generation quality  `[~]`

- [x] Grounded prompt, per-sentence verification, regenerate-or-abstain policy
- [x] Version detection and cross-version conflict detection (integration tested)
- [x] Judge, Cohen's kappa, labeling CLI
- [ ] **Nothing here has been measured.** Needs a key, a `full` run, ~50 labels.

## Phase 5 — Observability  `[~]`

- [x] Span per stage, `GET /traces`, `GET /traces/{id}`, `POST /feedback`
- [ ] A complete end-to-end trace needs a generated answer, so needs a key

## Phase 6 — Frontend  `[x]`

- [x] Ask, trace viewer with rank trail, experiments dashboard with deltas
- [x] `tsc --noEmit` and `next build` clean; rendered and inspected in-browser
- [ ] Not yet seen against a live answer

## Phase 7 — CI, docs, demo  `[~]`

- [x] CI: lint, types, tests, fixture ingest, retrieval eval, regression gate
- [x] **Thresholds set from a measured baseline** (recall@5 floor 0.75 against
      an observed 0.833) — the gate can now actually fail
- [x] Gate verified passing locally, and its failure paths unit tested
- [x] README table generated from `experiments/`; `--check` wired into CI
- [x] ARCHITECTURE (10 decisions), LIMITATIONS (11 entries), DEMO_SCRIPT
- [ ] CI has never run — no GitHub remote is configured

---

## Bugs found by running it

Each of these was invisible to unit tests and surfaced only against a live
database or a real corpus.

| Bug | Consequence | Caught by |
|---|---|---|
| `websearch_to_tsquery(varchar, varchar)` does not exist | **all lexical retrieval failed** | integration test |
| A tombstoned document was never resurrected when it reappeared unchanged | the page stayed invisible to retrieval permanently | restoring a corpus after a fixture ingest |
| Unanswerable items scored `false_answer` in retrieval-only mode | invented 5 failures the system was never given a chance to make | reading the first baseline run |
| The regression gate crashed on Windows (`✓` in cp1252) | gate unusable locally | running it |
| `min_tokens >= max_tokens` silently dropped every chunk but the first | most of a document lost, no error | chunker unit test |

## Verified on this machine

| | |
|---|---|
| Tests | **112 unit + 12 integration, all passing** |
| Lint / types | `ruff` and `mypy` clean, 53 source files |
| Frontend | `tsc --noEmit` and `next build` clean |
| Database | PostgreSQL 16.2 + pgvector, live, migrated |
| Corpus | 3,102 pages fetched; 639 ingested and embedded |
| Experiments | 7 recorded in `experiments/` |
| Regression gate | passes against measured floors |
