# Progress

Phase checklist from `PROJECT_SPEC.md` §12.

Legend: `[x]` done and verified · `[~]` built but not verified · `[ ]` not started

> ## The one thing to read first
>
> **Every phase is implemented. No phase past 0 has been run end to end,**
> because this machine cannot start a database.
>
> Docker is installed, but WSL2 cannot start: hardware virtualization is
> disabled in the machine's firmware (`wsl --status` reports *"virtualization is
> not enabled on this machine"*). That needs a BIOS/UEFI change and a reboot.
> No virtualization means no Docker containers, which means no Postgres, which
> means no ingestion, no retrieval, and **no measured numbers**.
>
> There is also no `ANTHROPIC_API_KEY` set, which blocks golden-set generation,
> answer generation, verification, and judging.
>
> Consequently `experiments/` is empty, the README results table is empty, and
> `EXPERIMENTS.md` has no entries. Those stay empty rather than being filled
> with plausible-looking numbers — see `PROJECT_SPEC.md` §3.2.
>
> **To unblock:** either enable virtualization in firmware and run
> `make up && make ingest`, or point `DATABASE_URL` at any Postgres 16 with
> pgvector (a free Neon or Supabase project works and needs no virtualization).

---

## Phase 0 — Scaffold  `[~]`

- [x] Repository structure, `uv` project, Python 3.12 pinned
- [x] Docker Compose: Postgres 16 + pgvector, backend, frontend placeholder
- [x] Alembic; migrations `0001` extensions, `0002` corpus, `0003` tracing
- [x] Config loader: `Settings` (environment) + `PipelineConfig` (experiments)
- [x] LLM client with persistent cache and cost accounting
- [x] `Makefile` + `make.ps1`, `.env.example`, pre-commit
- [x] `CLAUDE.md`
- [x] `GET /health` — verified live; reports `degraded` without a database
- [ ] **`make up` — blocked on virtualization**

## Phase 1 — Ingestion + naive RAG baseline  `[~]`

- [x] Fetch `release-1.26` / `1.28` / `1.30` — **run: 3,102 pages on disk**
- [x] Hugo-aware parsing; shortcodes unwrapped or dropped, code fences untouched
- [x] `fixed` chunker (and `structure_aware`, which Phase 3 needs)
- [x] Batched local embedding, model recorded per chunk
- [x] Incremental re-ingestion via normalized content hash
- [x] Dense retrieval, grounded generation with citations, `POST /query`
- [ ] **Acceptance ("a re-run embeds 0 docs") — asserted by
      `test_reingesting_unchanged_corpus_embeds_nothing`, which needs a database**

## Phase 2 — Evaluation harness + baseline numbers  `[~]`

- [x] Six category generators, including the version-diff generator
- [x] Every generated quote verified against its source before becoming gold
- [x] Curation CLI, deterministic hash-based dev/test splits
- [x] Retrieval metrics (Recall@k, MRR, nDCG), generation metrics, attribution
- [x] Runner writing `experiments/*.json`; results-table script
- [x] Fixture golden set — **8 hand-written items, every quote verified**
- [ ] **~300-item golden set — needs `ANTHROPIC_API_KEY`**
- [ ] **Curation pass — yours to do, after generation**
- [ ] **Baseline experiment — needs a database**

## Phase 3 — Retrieval improvements  `[~]`

- [x] All six stages implemented and config-gated
- [x] Five configs, each moving one variable, each carrying its hypothesis
- [ ] **Six experiments — need a database. `EXPERIMENTS.md` is empty.**

## Phase 4 — Generation quality  `[~]`

- [x] Grounded prompt, per-sentence claim verification
- [x] Regenerate-once-then-abstain policy
- [x] Version detection and cross-version conflict notes
- [x] Judge, Cohen's kappa agreement, labeling CLI
- [ ] **Judge validation — needs a key, a run, and ~50 labels from you**

## Phase 5 — Observability  `[~]`

- [x] Span per stage, `GET /traces`, `GET /traces/{id}`, `POST /feedback`
- [ ] **Verification that a real query writes a complete trace — needs a database**

## Phase 6 — Frontend  `[x]`

- [x] Ask, trace viewer with rank trail, experiments dashboard with deltas
- [x] `tsc --noEmit` and `next build` clean across all four routes
- [x] Rendered and inspected in a browser; empty and error states checked
- [ ] Not yet seen against live data, for the reason above

## Phase 7 — CI, docs, demo  `[~]`

- [x] CI: lint, types, tests, fixture ingestion, retrieval eval, regression gate
- [x] Manual full-eval workflow with artifact upload
- [x] `docs/ARCHITECTURE.md` (10 decisions), `LIMITATIONS.md`, `DEMO_SCRIPT.md`
- [x] README, with the results table generated rather than typed
- [ ] **Final `test`-split evaluation — needs a database and a key**
- [ ] **CI green — not yet run; no GitHub remote is configured**

---

## Verified on this machine

| | |
|---|---|
| Tests | 103 passed, 11 skipped (integration, no database) |
| Lint | `ruff check` + `ruff format --check` clean |
| Types | `mypy` clean, 53 source files |
| Frontend | `tsc --noEmit` clean, `next build` clean |
| `GET /health` | returns 200 and correctly reports `degraded` |
| Corpus | 3,102 pages fetched across three release branches |
| Fixture corpus | 30 pages × 2 versions, committed |
| Fixture golden set | 8 items, every quote verified against source |
| Configs | 6 load with distinct hashes |
