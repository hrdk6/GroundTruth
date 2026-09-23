# Demo script — three minutes

A walkthrough that shows the four things this project is actually about. Each
beat has a *point*; if you only have ninety seconds, do beats 2 and 3.

## Before you start

```bash
make up          # Postgres + API, migrations applied
make ingest      # ~3,100 pages across 1.26 / 1.28 / 1.30
cd frontend && npm run dev
```

Have these ready in tabs: `localhost:3000`, and one prepared trace.

> **Prerequisite check.** `make ingest` needs several minutes and downloads
> ~750MB on first run. Do it before the demo, not during it.

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
are the ranks it held at each stage. Find a row that reads `dense 2 → rrf 4 →
rerank 17` — that chunk was retrieved and then thrown away by the reranker.
That's a *ranking* miss, not a retrieval miss, and they need completely
different fixes. Without this you'd only know that recall went down."

## 4 · The evidence that any of it works (45s)

Open **Experiments**. Select two runs — ideally `baseline` and `hybrid_rerank`.

**Point at:** the per-category deltas and the attribution bars.

**Say:** "Every pipeline choice is a YAML config, and every run writes a JSON
file with its config hash, git SHA and dataset version. This is baseline versus
hybrid retrieval with reranking. The overall recall number moved, but look at
the per-category column — the gain is concentrated where the hypothesis said it
would be. And the failure bars show the mix shifting from retrieval misses to
ranking misses, which is what tells you what to work on next."

**Close on:** "The README table is generated from those files by a script. No
number in this repo was typed by hand."

---

## If something goes wrong

| Symptom | Cause | Fix |
|---|---|---|
| "The API didn't respond" | backend not running | `make up`, or `make dev` for no containers |
| Answers but no verification marks | verification is off in that config | switch the Pipeline selector to `full` |
| No conflicts shown | the question isn't version-sensitive, or `conflict_detection` is off | use the `full` config and a question about a changed page |
| Experiments page is empty | no runs recorded | `make eval CONFIG=configs/baseline.yaml` |
| Trace has no rank trail | the run predates tracing, or retrieval returned nothing | ask a fresh question |

## What not to claim

Be straight about the limits — an interviewer will find them anyway, and
[docs/LIMITATIONS.md](LIMITATIONS.md) lists them:

- the lexical leg is `ts_rank_cd`, which is BM25-*like*, not BM25;
- generation is not bit-reproducible, because current Claude models reject
  `temperature` — the LLM cache is what makes an eval re-run deterministic;
- the judge is validated against ~50 hand labels, and the kappa is reported in
  the README. If it's below 0.6, say so rather than quoting the metrics it
  produced.
