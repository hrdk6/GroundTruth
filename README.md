# GroundTruth

A self-evaluating, version-aware RAG platform over the Kubernetes documentation.

> ### Status: end to end, measured, $0.00
>
> Nine experiments have run against a live database — retrieval *and*
> generation. The table below is generated from them, never typed.
> [EXPERIMENTS.md](EXPERIMENTS.md) has the analysis, including a reranker that
> made things worse and was reverted, and a bug the harness found **in itself**
> that was inflating abstentions.
>
> Runs on free infrastructure: PostgreSQL + pgvector with no Docker
> (`make db-local`), and an OpenAI-compatible free-tier model. Total spend on
> every number in this repo: **$0.00**.
>
> **Read the caveats before the numbers.** The golden set is 32 items, so one
> item is worth ~5 points of recall. The corpus is a 639-page subset. And the
> LLM judge is the *same model* that wrote the answers, so `correctness` is
> self-assessed until [judge validation](#judge-validation) is done — that is
> the biggest outstanding gap, and it is stated rather than buried.

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
diagrams and the seventeen recorded decisions, each with its trade-off.

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
| Config | Dataset | Split | Recall@5 | Recall@10 | MRR | nDCG@10 | Correctness | Faithfulness | Citation prec. | Cost | Commit |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `hybrid` | `fixture_golden` | dev | 0.833 | 0.833 | 0.833 | 0.833 | — | — | — | $0.00 | `0f08127` |
| `full` | `golden_v1` | test | 0.800 | 0.900 | 0.675 | 0.736 | 0.923 | 1.000 | 1.000 | $0.00 | `0f08127` |
| `hybrid_rerank` | `golden_v1` | test | 0.800 | 0.800 | 0.583 | 0.639 | — | — | — | $0.00 | `0f08127` |
| `hybrid_rerank` | `golden_v1` | dev | 0.714 | 0.714 | 0.530 | 0.692 | — | — | — | $0.00 | `0f08127` |
| `hybrid` | `golden_v1` | test | 0.700 | 0.700 | 0.517 | 0.563 | — | — | — | $0.00 | `0f08127` |
| `hybrid` | `golden_v1` | dev | 0.786 | 0.786 | 0.583 | 0.774 | — | — | — | $0.00 | `0f08127` |
| `structure_aware` | `golden_v1` | test | 0.700 | 0.700 | 0.417 | 0.489 | — | — | — | $0.00 | `0f08127` |
| `structure_aware` | `golden_v1` | dev | 0.786 | 0.786 | 0.500 | 0.712 | — | — | — | $0.00 | `0f08127` |
| `baseline` | `golden_v1` | test | 0.100 | 0.100 | 0.100 | 0.100 | — | — | — | $0.00 | `0f08127` |
| `baseline` | `golden_v1` | dev | 0.286 | 0.286 | 0.274 | 0.308 | — | — | — | $0.00 | `0f08127` |

_Generated by `scripts/generate_results_table.py` from 10 run(s) in `experiments/`. Do not edit by hand._
<!-- RESULTS_TABLE_END -->

The table above is written by `scripts/generate_results_table.py` from the files
in `experiments/`, and CI fails if it is edited by hand. The queued experiments
and their hypotheses are listed in [EXPERIMENTS.md](EXPERIMENTS.md).

## Judge validation

**Not done — and this is the most important caveat in the repo.**

`answer_correctness` above was produced by `nvidia/nemotron-3-super-120b-a12b`
grading answers written by `nvidia/nemotron-3-super-120b-a12b`. That is
self-evaluation, and it inflates. The free tier served exactly three models
fast enough to be usable (55 of 58 timed out or were not served), so a separate
judge was not available here.

The machinery is built and tested; it needs ~50 human labels:

```bash
cd backend
uv run python -m evals.judge.label --count 50   # judge verdicts hidden, to avoid anchoring
uv run python -m evals.judge.label --report     # accuracy + Cohen's kappa
```

Agreement is reported as accuracy *and* kappa, because accuracy alone lies: on
a set that is 85% correct, a judge that always says "pass" scores 85% while
carrying no information at all. Below a kappa of about 0.6 the honest move is
to fix the judge prompt, not to publish what it produced.

The cheapest real fix is to point `GT_CHEAP_MODEL` at a *different* model from
`GT_GENERATION_MODEL`, so the judge is not marking its own homework.

## Failure attribution

Every failed item is classified as `retrieval_miss`, `ranking_miss`,
`generation_failure`, `false_answer`, `false_abstention`, or `version_error`,
and the distribution is reported per experiment and charted on the Experiments
page. That distribution is what decides which experiment is worth running next.

## Limitations

Tracked honestly in [docs/LIMITATIONS.md](docs/LIMITATIONS.md) — including the
self-evaluating judge, the 32-item golden set and 639-page corpus that bound
every number above, `multi_hop` scoring 0.000 in every run, and the lexical leg
being BM25-*like* rather than BM25.

## Testing and CI

123 unit tests and 12 integration tests. The integration tests run against
their own `_test` database — they truncate tables, so they must never touch a
corpus you have just spent ten minutes ingesting. They skip when no database is
reachable, and run in CI against a `pgvector/pgvector:pg16` service container.
CI lints,
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
