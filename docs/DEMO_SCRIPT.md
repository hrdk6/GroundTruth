# Demo script — three minutes

A walkthrough of the four things this project is actually about. Each beat has
a *point*; with only ninety seconds, do beats 3 and 4.

## Before you start

```bash
make db-local    # Postgres + pgvector, no Docker needed
make migrate
(cd backend && uv run python -m app.ingestion.run --config ../configs/full.yaml \
    --versions 1.26 1.30 --include concepts tasks)
make llm-check   # three calls; a model that is not being served surfaces here, not mid-demo
make dev         # API on :8000, in one terminal
(cd frontend && npm run dev)   # UI on :3000, in another
```

Have `localhost:3000` open, and one trace from a question you asked beforehand.

> **Prerequisite check.** Ingestion is CPU-bound: the scoped corpus takes about
> six minutes per chunker. Do it before the demo, not during it. On the free
> tier a `full`-pipeline answer takes 20-60 seconds (verification is one model
> call per sentence), so ask the first question before you start talking.

---

## 1 · The problem, in one question (30s)

Open **Ask** — it defaults to the `full` pipeline. Ask something whose answer
changed between releases; the seccomp tutorial is a reliable one:

> *How do I create a Pod with a seccomp profile?*

**Point at:** the **"This differs in other versions"** panel beneath the answer —
the section as it reads in v1.30, beside how it read in v1.26.

**Say:** "Most RAG systems would blend the two releases into one confident
paragraph. This answers for the latest version and shows what was different,
separately. It never silently mixes them."

## 2 · A verified citation (45s)

Click a citation marker in the answer.

**Point at:** the excerpt panel scrolling to that source and flashing, and the
small square in the margin of each sentence.

**Say:** "Every sentence carries a mark: filled means a second model pass
confirmed the cited excerpt supports it, half means partly, hollow means it
doesn't. An uncited factual sentence counts as unsupported — otherwise the
cheapest way to score well would be to stop citing."

Then ask something the docs don't cover:

> *What memory limit should I set for a Spring Boot service at 5000 rps?*

**Point at:** the abstention. "If fewer than 80% of the claims hold up it
regenerates once with the failures as feedback, and if it still can't support
the answer it says so instead of guessing."

## 3 · A trace that explains a result (45s)

Click **See how this answer was produced**.

**Point at:** the waterfall — each stage on one time axis — and the
verification span, usually the longest bar.

**Say:** "Every query records a span per stage, failed queries included. And
this is the part I care about —" **point at the rank trail** "— for every chunk
any stage saw, the rank it held at each one. When a chunk dense retrieval had
at rank 2 ends up cut from the context, that's a *ranking* miss, not a
retrieval miss, and the two need completely different fixes."

## 4 · The evidence — and the bug it caught (60s)

Open **Experiments**. Tick **Show superseded pre-audit runs**. Select the old
`baseline` (dev, marked *superseded*) and the current `baseline` (dev).

**Point at:** recall@5 **0.286 → 0.857**, and the paired interval marked
**real** — the only one in the project.

**Say:** "This is the most important thing in the project, and it's a bug I
found in my own harness. My first headline result was that structure-aware
chunking lifted recall from 0.29 to 0.79. When I audited the evaluator I found
the baseline's chunker was storing `tokenizer.decode()` output — lowercased,
punctuation spaced out — so most gold quotes couldn't match *any* baseline
chunk. Fixed, the baseline scores 0.86. Now every run audits its own gold
before scoring, and CI fails if a single quote is unmatchable."

Untick superseded, select `baseline` and `hybrid_bm25` on dev.

**Say:** "And this is the honest result: with the measurement fixed, nothing I
added to retrieval separates from noise on 14 questions — every paired interval
spans zero, and the dashboard says 'noise' rather than showing a green arrow.
The engineering answer is that this golden set is too small to choose between
these configs, and the next hour goes into more labelled questions, not more
retrieval stages."

## 5 · What I'd fix first (15s)

Say this before they ask it.

**Say:** "The judge is the same model that wrote the answers, so correctness is
self-assessed. The labeling tool is built — fifty human labels gives a kappa.
And the golden set needs to be ten times bigger before any retrieval decision
here is more than a judgment call."

---

## If something goes wrong

| Symptom | Cause | Fix |
|---|---|---|
| "Can't reach the API" | backend not running | `make db-local` then `make dev` |
| 502 with a trace id | the pipeline raised | open the trace: the failing span is red and the error is shown |
| Model errors or 60s+ latencies | free-tier model unavailable | `make llm-check`; the catalogue lists far more models than it serves |
| No verification marks | the pipeline selector isn't `full` | switch it back to `full` |
| No conflict panel | the question names a version, or its sections didn't change | ask without a version, about a page that changed |
| Experiments page is empty | no records | `make eval CONFIG=configs/baseline.yaml` |

## What not to claim

Be straight about the limits — an interviewer will find them anyway, and
[docs/LIMITATIONS.md](LIMITATIONS.md) lists them:

- **that any retrieval stage helps.** On this golden set none is
  distinguishable from dense retrieval over fixed windows;
- **correctness numbers as validated.** The judge grades its own model;
- the corpus is a 639-page subset of `concepts/` and `tasks/`, two versions;
- `multi_hop` is 0.000 in every run, on one item per split.
