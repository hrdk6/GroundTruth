# GroundTruth

A self-evaluating, version-aware RAG platform over the Kubernetes documentation.

> **Status: Phase 0 of 7 (scaffold).** No retrieval quality numbers exist yet —
> the evaluation harness lands in Phase 2 and the results table below is
> generated from `experiments/`, never written by hand. Sections marked
> _(pending)_ are deliberately empty rather than aspirational.

## The problem

RAG systems work in the demo and degrade quietly in production. GroundTruth
targets five specific failures, and is built to *prove* it fixes each one:

| Failure | How this repo addresses it | Phase |
|---|---|---|
| **Unmeasured quality** — chunking and prompt changes ship with no evidence | Eval harness built before any retrieval work; every change is a recorded experiment with before/after numbers | 2–3 |
| **Stale and conflicting knowledge** — old doc versions answer today's question | Three K8s release branches indexed side by side; version detection, version-filtered retrieval, explicit conflict notes | 4 |
| **Retrieval misses** — vector search fails on exact terms, tables, code | Hybrid dense + lexical with RRF, cross-encoder reranking, structure-aware chunking that never splits a code block | 3 |
| **Hallucinated citations** — answers cite sources that don't support them | Per-sentence claim verification against cited chunks, with a regenerate-or-abstain policy | 4 |
| **Undiagnosable failures** — nobody can tell what broke | Span-level tracing plus automatic failure attribution (retrieval miss vs ranking miss vs generation failure) | 5 |

## Architecture

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for diagrams and the decision
log. In short: one Postgres 16 + pgvector holds vectors, full-text indexes,
metadata, and traces; embeddings and reranking run locally on CPU; every
pipeline choice lives in a YAML config so an experiment is reproducible from
`(config hash, git SHA, dataset version)`.

## Quick start

Requires Docker and [uv](https://docs.astral.sh/uv/).

```bash
cp .env.example .env     # ANTHROPIC_API_KEY is optional until Phase 1
make install
make up
```

`make up` starts Postgres and the API, waits for health, and applies
migrations. Then:

```bash
curl -s http://localhost:8000/health
```

On Windows, GNU make is not installed by default — use the shim, which mirrors
every target:

```bash
./make.ps1 up
```

Run `make help` (or `./make.ps1 help`) for the full target list.

### Common commands

| Command | What it does |
|---|---|
| `make check` | Lint, type-check, and test — everything CI runs |
| `make dev` | Run the API natively, without containers |
| `make ingest` | Ingest the corpus _(Phase 1)_ |
| `make eval CONFIG=configs/baseline.yaml SPLIT=dev MODE=retrieval` | Run an evaluation _(Phase 2)_ |
| `make results` | Regenerate the results table below from `experiments/` |

## Results

_(pending — Phase 2)_

<!-- RESULTS_TABLE_START -->
No experiments recorded yet. Run `make eval` and then `make results`.
<!-- RESULTS_TABLE_END -->

## Judge validation

_(pending — Phase 4.)_ The LLM judge will be validated against ~50 hand-labeled
items, and its agreement with human labels (accuracy and Cohen's kappa) reported
here. An unvalidated judge is an opinion, not a metric.

## Failure attribution

_(pending — Phase 2.)_ Every failed eval item is classified as `retrieval_miss`,
`ranking_miss`, `generation_failure`, `false_answer`, `false_abstention`, or
`version_error`, and the distribution is reported per experiment.

## Limitations

Known weaknesses are tracked honestly in
[docs/LIMITATIONS.md](docs/LIMITATIONS.md) — including that generation is not
bit-for-bit reproducible (current Claude models reject `temperature`), and that
the lexical leg is BM25-*like* rather than true BM25.

## Project layout

```
backend/app/        FastAPI service: core, ingestion, retrieval, generation, tracing
backend/evals/      Golden dataset, metrics, judge, attribution, runner
configs/            YAML experiment configs — the only place pipeline choices live
experiments/        One JSON result file per run (committed)
data/golden/        Curated golden sets (committed); data/raw is not
docs/               Architecture, limitations, demo script
frontend/           Next.js chat, trace viewer, experiments dashboard (Phase 6)
```

## Attribution

The corpus is the [Kubernetes documentation](https://github.com/kubernetes/website)
(`content/en/docs/` from branches `release-1.26`, `release-1.28`, `release-1.30`),
used under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).
Copyright belongs to the Kubernetes authors. The full corpus is fetched at
ingestion time into `data/raw/`, which is gitignored. A 30-page excerpt is
committed under `backend/tests/fixtures/corpus/` so CI can run ingestion and a
retrieval evaluation without a 750MB download; see the `NOTICE.md` there.
