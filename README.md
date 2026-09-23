# GroundTruth

A self-evaluating, version-aware RAG platform over the Kubernetes documentation.

> ### Status
>
> **Retrieval is built and measured.** Seven experiments have run end to end;
> the table below is generated from them, and [EXPERIMENTS.md](EXPERIMENTS.md)
> has the analysis, including one change that made things *worse* and was
> reverted.
>
> **Generation is built but unmeasured.** Grounded answering, claim
> verification, conflict notes and the LLM judge are implemented and unit
> tested, but running them needs an `ANTHROPIC_API_KEY` this environment does
> not have. So there is no correctness, faithfulness, citation-precision,
> abstention or judge-agreement number anywhere in this repo — those sections
> say so rather than estimating.
>
> Two scoping caveats that bound every number here: the corpus was scoped to
> `concepts/` + `tasks/` in two versions (639 pages), and the golden set is 32
> hand-authored items rather than the ~300 the spec targets. Details in
> [EXPERIMENTS.md](EXPERIMENTS.md); full state in [PROGRESS.md](PROGRESS.md).

## The problem

RAG systems work in the demo and degrade quietly in production. GroundTruth
targets five specific failures, and is built to *prove* it fixes each one.

| Failure | How this repo addresses it |
|---|---|
| **Unmeasured quality** — changes ship with no evidence | The eval harness was built before any retrieval work. Every improvement is a recorded experiment with a config hash, git SHA, and before/after numbers |
| **Stale and conflicting knowledge** | Three release branches indexed side by side; version detection, version-filtered retrieval as a SQL pre-filter, and explicit conflict notes beside the answer |
| **Retrieval misses** on exact terms, tables, code | Hybrid dense + lexical fused with RRF, cross-encoder reranking, and chunking that never splits a fenced block or a table |
| **Hallucinated citations** | Per-sentence claim verification against the cited excerpt, with a regenerate-once-then-abstain policy |
| **Undiagnosable failures** | A span per stage, plus six-way failure attribution that separates a retrieval miss from a ranking miss from a generation failure |

## Quick start

Requires Docker and [uv](https://docs.astral.sh/uv/).

```bash
cp .env.example .env     # ANTHROPIC_API_KEY is optional: retrieval evals don't need one
make install
make up                  # Postgres + API, migrations applied
make ingest              # ~3,100 pages across 1.26 / 1.28 / 1.30 (slow: CPU embedding)
make eval CONFIG=configs/baseline.yaml SPLIT=dev MODE=retrieval
```

Then `cd frontend && npm install && npm run dev` for the UI on
`localhost:3000`.

On Windows, GNU make is not installed by default — `./make.ps1 <target>`
mirrors every target. Run `make help` for the full list.

| Command | What it does |
|---|---|
| `make check` | Lint, type-check, and test — everything CI runs |
| `make dev` | Run the API natively, without containers |
| `make ingest` | Fetch, parse, chunk, embed; skips unchanged documents |
| `make eval` | Run an evaluation and write an experiment file |
| `make results` | Regenerate the results table below from `experiments/` |

### No Docker? Two ways round it

Docker needs hardware virtualization, which not every machine has enabled.

**Postgres without Docker.** `pgserver` ships a real PostgreSQL 16 with pgvector
as a wheel, running as an ordinary user process:

```bash
make db-local            # starts it and writes DATABASE_URL to .env
make migrate && make ingest
```

This is how every result in this README was produced.

**Or managed Postgres** — any Postgres 16 with pgvector works:

```bash
echo 'DATABASE_URL=postgresql+psycopg://user:pass@host/db' >> .env
make migrate && make ingest
```

Ingestion is CPU-bound on embedding. `--include concepts tasks` scopes it to a
subset when a full run is too slow:

```bash
cd backend && uv run python -m app.ingestion.run   --config ../configs/hybrid.yaml --versions 1.26 1.30 --include concepts tasks
```

## How it works

```
fetch → parse → chunk → embed → Postgres (pgvector + tsvector)
                                       ↓
question → version detect → [rewrite] → [decompose]
                                       ↓
                        dense ─┐
                               ├─ RRF → rerank → grounded answer
                      lexical ─┘                      ↓
                                            claim verification
                                                      ↓
                                     return · regenerate once · abstain
```

Every stage is a config toggle, so an experiment is one line of YAML rather
than a code change. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the
diagrams and the ten recorded decisions, each with its trade-off.

Three ideas do most of the work:

- **Gold evidence is documentation coordinates, not chunk ids.** An item records
  `(source_path, heading_path, version, key_quote)`, so labels survive the
  re-chunking that Phase 3 performs deliberately. An id-keyed dataset would
  silently start measuring nothing.
- **Failure attribution is one label per item, in a fixed order.** The
  distribution's job is to answer "where should the next hour go", so
  double-counting would make it lie.
- **An uncited factual sentence counts as unsupported.** Without that rule, the
  cheapest way to raise the support fraction would be to stop citing.

## Results

<!-- RESULTS_TABLE_START -->
| Config | Dataset | Split | Recall@5 | Recall@10 | MRR | nDCG@10 | Cost | Commit |
|---|---|---|---|---|---|---|---|---|
| `hybrid` | `fixture_golden` | dev | 0.833 | 0.833 | 0.833 | 0.833 | $0.00 | `bee198a` |
| `hybrid` | `golden_v1` | test | 0.700 | 0.700 | 0.517 | 0.563 | $0.00 | `bee198a` |
| `baseline` | `golden_v1` | test | 0.100 | 0.100 | 0.100 | 0.100 | $0.00 | `bee198a` |
| `hybrid_rerank` | `golden_v1` | dev | 0.714 | 0.714 | 0.530 | 0.692 | $0.00 | `bee198a` |
| `hybrid` | `golden_v1` | dev | 0.786 | 0.786 | 0.583 | 0.774 | $0.00 | `bee198a` |
| `structure_aware` | `golden_v1` | dev | 0.786 | 0.786 | 0.500 | 0.712 | $0.00 | `bee198a` |
| `baseline` | `golden_v1` | dev | 0.286 | 0.286 | 0.274 | 0.308 | $0.00 | `bee198a` |

_Generated by `scripts/generate_results_table.py` from 7 run(s) in `experiments/`. Do not edit by hand._
<!-- RESULTS_TABLE_END -->

The table above is written by `scripts/generate_results_table.py` from the files
in `experiments/`, and CI fails if it is edited by hand. The queued experiments
and their hypotheses are listed in [EXPERIMENTS.md](EXPERIMENTS.md).

## Judge validation

**Not run** — it needs an API key. The judge, the labeling CLI, and the
agreement calculation are implemented and unit tested; validating them needs a
completed `full` run and about fifty hand labels:

```bash
make eval CONFIG=configs/full.yaml SPLIT=dev MODE=full
cd backend && uv run python -m evals.judge.label --count 50
uv run python -m evals.judge.label --report
```

Agreement is reported as accuracy *and* Cohen's kappa. Accuracy alone is
misleading: on a set that is 85% correct, a judge that always says "pass"
scores 85% while carrying no information. Below a kappa of about 0.6 the honest
move is to fix the judge prompt, not to publish the metrics it produced.

## Failure attribution

Every failed item is classified as `retrieval_miss`, `ranking_miss`,
`generation_failure`, `false_answer`, `false_abstention`, or `version_error`,
and the distribution is reported per experiment and charted on the Experiments
page. That distribution is what decides which experiment is worth running next.

## Limitations

Tracked honestly in [docs/LIMITATIONS.md](docs/LIMITATIONS.md) — including the
small golden set and scoped corpus that bound every number above, that no
generation metric has been measured at all, that generation is not
bit-reproducible because current Claude models reject `temperature`, and that
the lexical leg is BM25-*like* rather than BM25.

## Testing and CI

112 unit tests and 12 integration tests. The integration tests skip when no
database is reachable and run in CI against a `pgvector/pgvector:pg16` service
container. CI lints,
type-checks, tests, ingests the committed fixture corpus, runs a retrieval
evaluation on the fixture golden set, and checks it against
`evals/thresholds.yaml`. Generation evals are a separate manual workflow,
because they cost money.

## Project layout

```
backend/app/        core, ingestion, retrieval, generation, tracing, api
backend/evals/      dataset, metrics, judge, attribution, runner, gate
configs/            the only place a pipeline choice lives
experiments/        one JSON per run (committed)
data/golden/        curated golden sets (committed); data/raw is not
frontend/           Next.js: ask, trace viewer, experiments dashboard
docs/               architecture, limitations, demo script
```

## Attribution

The corpus is the [Kubernetes documentation](https://github.com/kubernetes/website)
(`content/en/docs/` from `release-1.26`, `release-1.28`, `release-1.30`), used
under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Copyright
belongs to the Kubernetes authors. The full corpus is fetched at ingestion time
into `data/raw/`, which is gitignored; a 30-page excerpt is committed under
`backend/tests/fixtures/corpus/` so CI can run without a 750MB download — see
the `NOTICE.md` there.
