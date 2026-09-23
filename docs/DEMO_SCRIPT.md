# Demo script — three minutes

A walkthrough that shows the four things this project is actually about. Each
beat has a *point*; if you only have ninety seconds, do beats 2 and 3.

## Before you start

```bash
make db-local    # Postgres + pgvector, no Docker needed
make migrate
cd backend && uv run python -m app.ingestion.run \n  --config ../configs/hybrid.yaml --versions 1.26 1.30 --include concepts tasks
make dev         # API on :8000
cd frontend && npm run dev
```

Have these ready in tabs: `localhost:3000`, and one prepared trace.

> **Prerequisite check.** Ingestion is CPU-bound on embedding: the scoped
> corpus above takes ~9 minutes, the full one over two CPU-hours. Do it before
> the demo, not during it. Also run `make llm-check` first -- it verifies the
> model in three calls, and a wrong model id otherwise surfaces as a dead
> demo.

---

## 1 · The problem, in one question (30s)

Open **Ask**. Type a question whose answer changed between releases — the
seccomp or security-context pages are reliable:

> *What changed about seccomp profiles between versions?*

**Point at:** the **Version note** beneath the answer. Two columns, one per
release, each with its own citation.

**Say:** "Most RAG systems would blend these two releases into one confident
paragraph. This one answers for the latest version and shows you what was
different before, separately. It never silently mixes them."

## 2 · A verified citation (45s)

Stay on the answer. Click a `[2]` marker in the text.

**Point at:** the excerpt panel scrolling to that source and flashing, and the
small square in the left margin of each sentence.

**Say:** "Every sentence carries a mark: filled means a second model pass
confirmed the cited excerpt actually supports that sentence, half-filled means
partly, hollow means it doesn't. An uncited factual sentence counts as
unsupported — otherwise the cheapest way to score well would be to stop
citing."

Then ask something the docs don't cover:

> *What memory limit should I set for a Spring Boot service at 5000 rps?*

**Point at:** the abstention.

**Say:** "It declined. That's a feature. If fewer than 80% of the claims hold
up, it regenerates once with the failures as feedback, and if it still can't
support the answer it says so instead of guessing."

## 3 · A trace that explains a failure (60s)

Click **See how this answer was produced**, or open **Traces** and pick one.

**Point at:** the waterfall — a bar per stage, widths proportional to time.

**Say:** "Every query records a span per stage: version detection, dense,
lexical, fusion, rerank, generation, verification. You can see where the time
went."

Now the important part. **Point at the rank trail** on the right.

**Say:** "This is the bit I care about. For every chunk any stage saw, these
are the ranks it held at each stage. On this trace one row reads
`dense 4 → lexical 2 → rrf 1` — dense ranked it fourth, lexical second, and
fusion promoted it to first. That is hybrid retrieval earning its place,
visibly. When a row climbs instead, that is a *ranking* miss rather than a
retrieval miss, and the two need completely different fixes. Without this you'd
only know recall went down."

## 4 · The evidence that any of it works (45s)

Open **Experiments**. Select `baseline` and `hybrid`, both on the `dev` split
and the same dataset — the table shows the dataset because comparing across
golden sets would be meaningless.

**Point at:** the per-category deltas and the attribution bars.

**Say:** "Every pipeline choice is a YAML config, and every run writes a JSON
file with its config hash, git SHA and dataset version. Baseline to hybrid is
recall@5 0.286 to 0.786. But the per-category column is the real story:
`table_or_code` went 0.000 to 1.000, and `factual` did not move *at all*. That
is the control — prose questions never depended on whether a code block
survived chunking, so fixing chunking did nothing for them. A change that
improved everything uniformly would have made me suspicious. The failure bars
show retrieval misses dropping from 9 to 2."

**Close on:** "The README table is generated from those files by a script. No
number in this repo was typed by hand — and all of it ran on free
infrastructure: Postgres with no Docker, and a free-tier model. Total spend,
zero."

## 5 · The part I would fix first (15s)

Say this before they ask it.

**Say:** "The judge is the same model that wrote the answers, so correctness is
self-assessed and it inflates. That is the first thing I would fix — point the
cheap model at a different provider and run the fifty human labels for a kappa.
The harness is built for exactly that; I just could not get a second model fast
enough on the free tier. And `multi_hop` is 0.000 in every run: two items, both
needing evidence from two pages, and decomposition did not fix it."

---

## If something goes wrong

| Symptom | Cause | Fix |
|---|---|---|
| "The API didn't respond" | backend not running | `make db-local` then `make dev` |
| Model errors or 20s+ latencies | free-tier model unavailable | `make llm-check`; the catalogue lists far more models than it serves |
| Answers but no verification marks | verification is off in that config | switch the Pipeline selector to `full` |
| No conflicts shown | the question isn't version-sensitive, or `conflict_detection` is off | use the `full` config and a question about a changed page |
| Experiments page is empty | no runs recorded | `make eval CONFIG=configs/baseline.yaml` |
| Trace has no rank trail | the run predates tracing, or retrieval returned nothing | ask a fresh question |

## What not to claim

Be straight about the limits — an interviewer will find them anyway, and
[docs/LIMITATIONS.md](LIMITATIONS.md) lists them:

- **the judge has not been validated.** It is the same model that wrote the
  answers, so correctness is self-assessed. Say so before you quote 0.923;
- the golden set is 32 items, so one item is worth ~5 points of recall on dev.
  The `hybrid_rerank` result is a one-item swing in each direction;
- the corpus is a 639-page subset of `concepts/` and `tasks/`, two versions;
- the lexical leg is `ts_rank_cd`, which is BM25-*like*, not BM25;
- `multi_hop` is 0.000 everywhere, and decomposition did not fix it.
