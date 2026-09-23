# GroundTruth

A self-evaluating, version-aware RAG platform over the Kubernetes documentation.

> ### Status: complete, unmeasured
>
> Every phase is implemented — ingestion, hybrid retrieval, grounded generation
> with claim verification, the evaluation harness, tracing, and the UI. **No
> experiment has been run**, because the development machine cannot start a
> database: Docker is installed but WSL2 will not start without hardware
> virtualization, which is disabled in firmware.
>
> So `experiments/` is empty, the results table below is empty, and
> `EXPERIMENTS.md` has no entries. They stay empty rather than getting
> plausible-looking numbers. Everything else in this README describes code that
> exists and is tested; [PROGRESS.md](PROGRESS.md) states exactly what is
> verified and what is not.

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
make ingest              # ~3,100 pages across 1.26 / 1.28 / 1.30 (several minutes)
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

### No virtualization? Use managed Postgres

Everything except `make up` works against any Postgres 16 with pgvector — a
free Neon or Supabase project needs no virtualization:

```bash
echo 'DATABASE_URL=postgresql+psycopg://user:pass@host/db' >> .env
make migrate && make ingest
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
No experiments recorded yet. Run `make eval` and then `make results`.
<!-- RESULTS_TABLE_END -->

The table above is written by `scripts/generate_results_table.py` from the files
in `experiments/`, and CI fails if it is edited by hand. The queued experiments
and their hypotheses are listed in [EXPERIMENTS.md](EXPERIMENTS.md).

## Judge validation

Not yet run. The judge, the labeling CLI, and the agreement calculation are
implemented; validating them needs an API key, a completed `full` run, and
about fifty hand labels:

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

Tracked honestly in [docs/LIMITATIONS.md](docs/LIMITATIONS.md) — eleven entries,
including that no results exist yet, that the regression gate has no floors
until a baseline is measured, that generation is not bit-reproducible because
current Claude models reject `temperature`, and that the lexical leg is
BM25-*like* rather than BM25.

## Testing and CI

103 unit tests, plus integration tests that are skipped without a database and
run in CI against a `pgvector/pgvector:pg16` service container. CI lints,
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
