# PROJECT_SPEC.md — GroundTruth: A Self-Evaluating, Version-Aware RAG Platform

> **For Claude Code:** This file is the single source of truth for this project. Read it fully before doing anything. Work strictly phase by phase (Section 12). At the end of every phase, stop, summarize what you built, report the metrics, and wait for my approval before starting the next phase. If anything in this spec is ambiguous or seems wrong, ask me instead of guessing.

---

## 1. Your Role

You are a senior AI/ML platform engineer building a production-grade RAG system. You care about measurement over intuition, clean architecture, reproducibility, and honest reporting. You write typed, tested, readable code, and you explain trade-offs in comments and docs where they matter.

## 2. The Problem This Project Solves

RAG systems in companies usually work in the demo and then degrade quietly in production. The five failures we are targeting:

1. **Unmeasured quality:** changes to chunking, embeddings, or prompts ship without any evidence that they helped, so regressions go unnoticed.
2. **Stale and conflicting knowledge:** old document versions stay in the index; the system answers from outdated content or blends contradictory sources without saying so.
3. **Retrieval misses:** pure vector search fails on exact terms (API fields, error messages, flags), breaks tables and code blocks, and can't handle multi-hop questions.
4. **Hallucinated citations:** answers cite sources that don't actually support the claim.
5. **Undiagnosable failures:** when an answer is wrong, nobody can tell whether retrieval or generation failed.

GroundTruth is a RAG platform that fixes each of these, and — most importantly — **proves** it with an evaluation harness, failure attribution, and a CI regression gate.

## 3. Non-Negotiable Principles

1. **Eval-first.** The evaluation harness is built before any retrieval improvements. Every improvement is an experiment with before/after numbers.
2. **Never fabricate results.** Every number in the README, dashboard, or my summaries must come from an actual run stored in `experiments/`. The README results table is generated from those files by a script, never typed by hand.
3. **Config-driven.** Every pipeline choice (chunker, embedding model, retrieval mode, k values, reranker on/off, prompts, models) lives in YAML config, so any experiment is reproducible from `config + git SHA + dataset version`.
4. **Cost-aware.** All LLM calls go through a single client wrapper with a persistent cache keyed on (model, prompt, params) so re-running evals is cheap and deterministic. Log token usage and cost per query.
5. **Small, working increments.** Commit after each meaningful step with clear messages. Tests pass at every commit.
6. **Honest limitations.** Document known weaknesses in `docs/LIMITATIONS.md` as you discover them.

## 4. Tech Stack

| Layer | Choice | Notes |
|---|---|---|
| Language | Python 3.11+ | Type hints everywhere, `ruff` + `mypy` (non-strict is fine) |
| Package mgmt | `uv` | `pyproject.toml` |
| API | FastAPI | Async, Pydantic v2 models |
| Database | Postgres 16 + `pgvector` | Vectors (HNSW index), full-text search (`tsvector`), metadata filters, traces — one database |
| Embeddings | `BAAI/bge-small-en-v1.5` via `sentence-transformers` (default) | Local, free, deterministic; config allows swapping in an API model |
| Reranker | `BAAI/bge-reranker-base` cross-encoder | Local |
| Generation | Anthropic API (`anthropic` SDK) | Default `claude-sonnet-5`; cheap tasks (judging, query rewriting, verification) default to `claude-haiku-4-5-20251001`. Both configurable. |
| Tracing | Custom tracing stored in Postgres | Span-based, OpenTelemetry-style schema. Optional exporter to Langfuse later. |
| Frontend | Next.js (App Router) + TypeScript + Tailwind | Chat UI, trace viewer, experiments dashboard |
| Infra | Docker Compose | `make up` brings up everything |
| CI | GitHub Actions | Lint, tests, retrieval-eval regression gate |
| Tests | `pytest` | Unit + integration (integration uses a fixture corpus) |

Secrets go in `.env` (never committed); provide `.env.example`.

## 5. Repository Structure

```
groundtruth/
├── CLAUDE.md                 # Your working notes/conventions for future sessions (create in Phase 0)
├── PROJECT_SPEC.md           # This file
├── PROGRESS.md               # Phase checklist, updated at end of each phase
├── EXPERIMENTS.md            # Human-readable log of every experiment and its conclusion
├── README.md
├── Makefile
├── docker-compose.yml
├── .env.example
├── configs/                  # YAML experiment configs (baseline.yaml, hybrid.yaml, hybrid_rerank.yaml, ...)
├── backend/
│   ├── pyproject.toml
│   ├── app/
│   │   ├── api/              # FastAPI routers
│   │   ├── core/             # config loading, logging, LLM client wrapper + cache, db session
│   │   ├── ingestion/        # fetch, parse, chunk, embed, version tracking
│   │   ├── retrieval/        # dense, lexical, fusion, rerank, query rewriting, decomposition
│   │   ├── generation/       # prompts, grounded answering, claim verification, abstention, conflicts
│   │   ├── tracing/          # span recording, trace storage
│   │   └── models/           # SQLAlchemy + Pydantic schemas
│   ├── evals/
│   │   ├── dataset/          # golden set generation, curation CLI, dataset versioning
│   │   ├── metrics/          # retrieval + generation metrics
│   │   ├── judge/            # LLM-as-judge + judge validation against human labels
│   │   ├── attribution/      # failure classification
│   │   └── runner.py         # `python -m evals.runner --config configs/x.yaml --split dev`
│   ├── migrations/           # Alembic
│   └── tests/
│       └── fixtures/         # tiny corpus + tiny golden set for CI
├── frontend/
├── data/                     # raw + processed corpus (gitignored), golden sets (committed)
├── experiments/              # one JSON result file per run (committed)
├── scripts/                  # generate_results_table.py, etc.
└── docs/
    ├── ARCHITECTURE.md       # with Mermaid diagrams
    ├── LIMITATIONS.md
    └── DEMO_SCRIPT.md
```

## 6. Corpus

**Primary:** the Kubernetes documentation (`kubernetes/website` repo, `content/en/docs/`) from three release branches: **`release-1.26`, `release-1.28`, `release-1.30`**. Markdown with front matter, lots of code blocks, YAML examples, tables, and genuine differences between versions. It's licensed CC BY 4.0 — attribute it in the README.

**Secondary (Phase 4+):** Kubernetes release notes / CHANGELOG entries for those versions, used to strengthen version-sensitive answers.

Ingest with a sparse checkout or GitHub tarball per branch. Keep the raw files under `data/raw/{version}/` (gitignored).

**Fixture corpus** for CI and tests: ~30 hand-picked docs across two versions, committed under `backend/tests/fixtures/`.

## 7. Data Model (Postgres)

- **`documents`** — `id`, `source_path` (path within repo), `version` (e.g. "1.28"), `title`, `url`, `content_hash`, `ingested_at`, `is_latest_for_path` (bool), metadata JSONB.
- **`chunks`** — `id`, `document_id`, `version`, `chunk_index`, `text`, `heading_path` (e.g. "Pods > Pod Lifecycle > Container probes"), `chunk_type` (`prose` | `code` | `table` | `mixed`), `token_count`, `embedding vector(384)`, `tsv tsvector` (generated column), `chunker_name` (so multiple chunking strategies can coexist for experiments), metadata JSONB.
- **`ingestion_runs`** — run id, config, counts of added/updated/unchanged/deleted docs, duration.
- **`traces`** and **`spans`** — `trace_id`, `span_id`, `parent_span_id`, `name`, `start/end`, `input` JSONB, `output` JSONB, `attributes` JSONB (model, tokens, cost, k, scores), `status`.
- **`feedback`** — trace id, thumbs up/down, optional comment.

Indexes: HNSW on `embedding` (cosine), GIN on `tsv`, btree on `(version, chunker_name)` and `source_path`.

## 8. Component Specifications

### 8.1 Ingestion
- **Incremental:** compute a SHA-256 of normalized content per `(source_path, version)`. Re-parse and re-embed only new or changed docs; mark deleted docs. Report counts per run.
- **Parsing:** strip Hugo shortcodes sensibly (keep the content inside them where meaningful), keep front matter title, resolve relative links to absolute doc URLs.
- **Chunkers** (implement all; they are experiment variables):
  - `fixed`: fixed token windows with overlap (the naive baseline).
  - `structure_aware`: split on Markdown headings; never split inside code blocks or tables; merge tiny sections; split oversized sections at paragraph boundaries; prepend the heading path to the chunk text used for embedding.
- **Embedding:** batched; store model name in metadata.

### 8.2 Retrieval
Each stage is a pluggable, individually toggleable step recorded as a trace span.
- **Dense:** pgvector cosine similarity, top `k_dense`.
- **Lexical:** Postgres full-text search with `ts_rank_cd`, top `k_lexical`. Note in docs that this is BM25-*like*, not true BM25; mention ParadeDB `pg_search` as an upgrade path.
- **Fusion:** Reciprocal Rank Fusion (default `k=60`).
- **Rerank:** cross-encoder over fused candidates, keep top `k_final`.
- **Version filter:** applied as a pre-filter in SQL (Section 8.4).
- **Query rewriting (toggle):** cheap-model rewrite that expands abbreviations and adds likely exact terms; keep the original query in the lexical search too.
- **Multi-hop decomposition (toggle):** a cheap-model classifier decides if a question is multi-part; if so, decompose into ≤3 sub-queries, retrieve for each, merge and dedupe, then rerank against the original question.

### 8.3 Grounded Generation
- Context chunks are numbered `[1]..[n]` with version and source shown to the model.
- The model must cite chunks inline for every factual sentence and must answer "I don't have enough information in the documentation to answer that" when the context doesn't support an answer.
- **Claim verification pass:** split the answer into sentences with their citations; a cheap model judges whether each cited chunk supports the sentence (`supported` / `partially` / `unsupported`). Uncited factual sentences count as unsupported.
- **Policy:** if the supported fraction is below a configurable threshold, regenerate once with feedback; if still below, abstain. Return the verification results in the API response and trace.

### 8.4 Version & Conflict Awareness
- **Version detection:** regex for explicit versions ("1.28", "v1.28", "k8s 1.28"); if none, default to latest indexed version. Return the version used in the response.
- **Conflict handling:** when the user gives no version and the question concerns something that differs across versions (detected by retrieving across all versions and finding same-`source_path`/same-`heading_path` chunks whose content differs materially), answer for the latest version and add a clearly marked note summarizing how it differed in earlier versions, with citations for each.
- Never silently blend content from different versions into one answer.

### 8.5 API (FastAPI)
- `POST /query` — `{question, version?, config_name?}` → `{answer, citations[], version_used, conflicts[], verification{}, abstained, trace_id, latency_ms, cost_usd}`
- `GET /traces`, `GET /traces/{id}` — trace list and full span tree
- `POST /feedback`
- `GET /experiments`, `GET /experiments/{id}` — read from `experiments/`
- `POST /ingest` — trigger an ingestion run (admin)
- `GET /health`

## 9. Evaluation Harness (Centerpiece)

### 9.1 Golden Dataset
Schema per item (JSONL): `id`, `question`, `category`, `version` (nullable), `reference_answer`, `gold_chunk_ids` or, more robustly, `gold_evidence` (`source_path` + `heading_path` + `version` + a key quote, so golds survive re-chunking), `answerable` (bool), `curated` (bool), `notes`.

**Categories and target counts (~300 total):**
| Category | Target | How to generate |
|---|---|---|
| `factual` | 80 | Sample sections, LLM writes a question answerable from that section |
| `exact_term` | 50 | Questions centered on specific API fields, flags, error messages, kubectl commands |
| `version_sensitive` | 60 | **Diff the same file across versions**; generate questions whose answer changed |
| `multi_hop` | 40 | Pair pages linked to each other; question requires both |
| `table_or_code` | 30 | Answer lives in a table or YAML/code block |
| `unanswerable` | 40 | Plausible Kubernetes questions not covered by the docs, or about versions not indexed |

**Curation CLI:** `python -m evals.dataset.curate` shows each generated item with its evidence and lets me accept / edit / reject. Only curated items are used for reported results.

**Splits:** `dev` (~60%) for iterating, `test` (~40%) held out and only run for final reported numbers. Version the dataset (`golden_v1.jsonl`, etc.).

### 9.2 Metrics
- **Retrieval:** Recall@1/5/10, MRR, nDCG@10 — overall and per category. Gold matching is by `gold_evidence`, not raw chunk IDs.
- **Generation:** answer correctness (judge vs. reference, 1–5 plus binary pass), faithfulness (fraction of claims supported by retrieved context), citation precision (cited chunks that actually support their claims), abstention precision/recall (on `unanswerable` vs. answerable items), version correctness for `version_sensitive`.
- **System:** latency p50/p95 per stage, tokens and cost per query.

### 9.3 Judge Validation
- I'll hand-label ~50 items for correctness and faithfulness via a small labeling CLI.
- Compute judge–human agreement (accuracy + Cohen's kappa) and report it in the README. If agreement is poor, iterate on the judge prompt and log it in `EXPERIMENTS.md`.

### 9.4 Runner & Experiment Records
- `make eval CONFIG=configs/hybrid_rerank.yaml SPLIT=dev [MODE=retrieval|full]`
- Retrieval-only mode needs no API key (local embeddings + reranker) — this is what CI runs.
- Each run writes `experiments/{timestamp}_{config_name}.json` containing config, git SHA, dataset version, split, all metrics (overall + per category), per-item results, attribution breakdown, cost.
- `scripts/generate_results_table.py` builds the README results table from these files.

### 9.5 Failure Attribution
For each failed item, classify:
- **`retrieval_miss`** — gold evidence not in any candidate set.
- **`ranking_miss`** — gold in candidates but dropped before final context.
- **`generation_failure`** — gold in context, but answer incorrect or unfaithful.
- **`false_answer`** — answered an unanswerable question.
- **`false_abstention`** — abstained despite gold being in context.
- **`version_error`** — answered from the wrong version.

Report the distribution per experiment. This drives what to improve next.

## 10. Observability & Frontend

**Tracing:** every `/query` produces a trace with spans for version detection, rewriting, decomposition, each retrieval stage, fusion, rerank, generation, verification. Each span stores inputs/outputs (truncated), scores, timings, tokens, cost.

**Frontend pages:**
1. **Chat** — ask questions, pick version (or auto), see answer with clickable citations (source + version + heading path), conflict notes, verification badges per sentence, thumbs up/down.
2. **Trace viewer** — span tree / waterfall for any query, with retrieved chunks and their scores at each stage (so you can *see* a ranking miss).
3. **Experiments dashboard** — table of runs; select two to compare side by side with metric deltas overall and per category, plus attribution breakdown charts.

Keep the UI clean and professional; it's going to be shown in interviews.

## 11. CI/CD

GitHub Actions on every PR:
1. `ruff`, `mypy`, `pytest` (unit + integration on fixture corpus using a Postgres service container).
2. **Retrieval regression gate:** ingest the fixture corpus, run retrieval-only eval on the fixture golden set, compare against `evals/thresholds.yaml`; fail the build if any tracked metric drops below threshold. Post a metrics summary as a job summary.
3. A separate manually triggered workflow runs full generation evals using an `ANTHROPIC_API_KEY` secret.

## 12. Phases & Acceptance Criteria

At the end of **every** phase: run tests, update `PROGRESS.md` and `EXPERIMENTS.md`, commit, then **stop and give me a summary** (what was built, metrics if any, problems found, what's next).

**Phase 0 — Scaffold**
- Repo structure, `uv` project, Docker Compose (Postgres+pgvector, backend, frontend placeholder), Alembic, config loader, LLM client wrapper with caching and cost logging, Makefile, `.env.example`, pre-commit.
- Create `CLAUDE.md` with conventions, commands, and a pointer to this spec.
- ✅ `make up` works; `GET /health` returns OK; tests pass.

**Phase 1 — Ingestion + Naive RAG Baseline**
- Fetch the three K8s doc versions; parse; `fixed` chunker; embed; incremental re-ingestion.
- Dense-only retrieval; basic answer generation with citations; `POST /query`.
- ✅ Re-running ingestion with no changes re-embeds 0 docs; a query returns a cited answer.

**Phase 2 — Evaluation Harness + Baseline Numbers**
- Golden set generation for all categories (including the version-diff generator), curation CLI, splits, metrics, runner, experiment JSON output, attribution, results-table script, fixture golden set.
- I curate the dataset (pause for me here).
- ✅ Baseline experiment recorded on `dev` with per-category metrics and attribution breakdown.

**Phase 3 — Retrieval Improvements (one experiment each)**
- `structure_aware` chunker → lexical search → RRF hybrid → reranker → query rewriting → multi-hop decomposition.
- Record each as a separate experiment against the previous best; keep only what helps; log conclusions.
- ✅ `EXPERIMENTS.md` shows the progression with real numbers and a short analysis of *why* each change did or didn't help.

**Phase 4 — Generation Quality**
- Grounded prompt, claim verification, regenerate/abstain policy, version detection, conflict handling, release notes ingestion.
- Judge implementation + labeling CLI + judge validation (pause for me to label).
- ✅ Full evals on `dev` show faithfulness, citation precision, abstention metrics, and version correctness; judge agreement reported.

**Phase 5 — Observability**
- Tracing across all stages, trace API, feedback API.
- ✅ Every query produces a complete trace; attribution results link to traces.

**Phase 6 — Frontend**
- Chat, trace viewer, experiments dashboard.
- ✅ All three pages work against the live backend, look polished on desktop, and are usable on mobile.

**Phase 7 — CI, Docs, Demo**
- GitHub Actions workflows and thresholds; final `test`-split evaluation; README (problem, architecture diagram, how to run, generated results table, judge agreement, limitations, attribution); `docs/ARCHITECTURE.md` with Mermaid diagrams; `docs/DEMO_SCRIPT.md` (a 3-minute walkthrough showing a version conflict, a verified citation, a trace revealing a ranking miss, and the experiments comparison).
- ✅ A fresh clone runs with `make up && make ingest && make eval`; CI is green; README numbers match `experiments/`.

**Stretch (only after Phase 7, if I ask):** permission-aware retrieval (ACL tags on documents + pre-filtering + leakage tests), streaming responses, semantic caching, embedding-model comparison experiment, Langfuse export.

## 13. Working Agreement

- Prefer simple, readable solutions; don't add frameworks (e.g. LangChain/LlamaIndex) — the point is to show I understand the internals.
- When you make a significant design decision, add a short entry to `docs/ARCHITECTURE.md` under "Decisions" (context, choice, trade-off).
- If a step would cost more than ~$2 in API calls, tell me the estimate first.
- If an experiment makes things worse, that's a valid result — record it honestly.
- Keep `CLAUDE.md` updated with anything a future session needs to know.

## 14. Definition of Done

A reviewer can clone the repo, run three commands, ask version-specific Kubernetes questions in a polished UI, see cited and verified answers with conflict notes, open a trace to see exactly why an answer came out the way it did, and open a dashboard showing — with real, reproducible numbers — how each engineering decision improved retrieval and answer quality.
