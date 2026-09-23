# Experiments

Every number here is copied from a file in `experiments/`. The README results
table is generated from those same files by `scripts/generate_results_table.py`.

## Setup for this series

| | |
|---|---|
| Corpus | Kubernetes docs, `concepts/` + `tasks/`, versions **1.26 and 1.30** |
| Indexed | 639 documents · 3,905 chunks (`fixed`) / 6,929 chunks (`structure_aware`) |
| Golden set | `golden_v1.jsonl`, 32 curated items · dev 19 / test 13 |
| Mode | `retrieval` — no API key, no model calls, $0.00 per run |

Two scoping notes, because they bound what these numbers mean:

- **The corpus is a subset.** Embedding all 3,102 pages across three branches on
  CPU ran for over two CPU-hours without finishing, so ingestion was scoped to
  `concepts/` and `tasks/` in two versions. That is 639 real pages with real
  distractors — enough for the comparisons below to mean something, and not
  the full corpus. The `include` filter used is recorded on every ingestion run.
- **The golden set is 32 items, not the ~300 the spec targets**, and it was
  **authored by reading the documentation**, not LLM-generated: the generators
  in `evals/dataset/generate.py` need an `ANTHROPIC_API_KEY` this environment
  does not have. Every quote is verified to exist in the parsed source before
  becoming gold. With 19 dev items, one item is worth ~5 points of recall — so
  read the direction of these results, not the third decimal place.

---

## 2026-09-23 — `structure_aware`

**Baseline:** `baseline` (fixed 512-token windows, dense-only)
**Change:** chunker `fixed` → `structure_aware`. Nothing else.

| Metric (dev) | baseline | structure_aware | Δ |
|---|---|---|---|
| recall@5 | 0.286 | **0.786** | **+0.500** |
| recall@1 | 0.214 | 0.286 | +0.072 |
| MRR | 0.274 | 0.500 | +0.226 |
| nDCG@10 | 0.308 | 0.712 | +0.404 |
| `retrieval_miss` | 9 | 2 | −7 |

Per category, recall@5:

| Category | baseline | structure_aware |
|---|---|---|
| `table_or_code` | 0.000 | **1.000** |
| `exact_term` | 0.200 | **0.800** |
| `version_sensitive` | 0.000 | **1.000** |
| `factual` | 0.750 | 0.750 |
| `multi_hop` | 0.000 | 0.000 |

**Verdict: kept.** The largest single improvement in the series.

**Why.** The hypothesis was that fixed windows cut YAML examples and tables in
half, making them unretrievable as a unit — and the per-category split says
exactly that. `table_or_code` went from **0.000 to 1.000**: under the baseline,
*not one* question whose answer lived in a table or a code block could be
answered, because no chunk contained a whole one. `factual` did not move at
all, which is the control: prose questions never depended on block integrity,
so fixing block integrity did nothing for them. A change that had improved
everything uniformly would have been suspicious.

Chunk count rose from 3,905 to 6,929 (+77%) for the same documents, so this
costs index size and ingestion time (379s → 613s).

---

## 2026-09-23 — `hybrid`

**Baseline:** `structure_aware`
**Change:** added the Postgres full-text leg and RRF fusion (k=60).

| Metric (dev) | structure_aware | hybrid | Δ |
|---|---|---|---|
| recall@5 | 0.786 | 0.786 | **±0** |
| recall@1 | 0.286 | **0.429** | **+0.143** |
| MRR | 0.500 | **0.583** | **+0.083** |
| nDCG@10 | 0.712 | **0.774** | **+0.062** |
| p50 latency | 23ms | 50ms | +27ms |

**Verdict: kept** — but not for the reason predicted.

**Why.** The hypothesis was that lexical search would find exact strings dense
retrieval misses, and so lift `exact_term` *recall*. It did not: recall@5 is
identical, and no category's recall@5 changed. What moved was **rank**.
recall@1 rose by 0.143 and MRR by 0.083, meaning the gold chunks were already
being retrieved — lexical evidence pushed them to the top of the list.

That is a real improvement (an answer built from the top 5 does better when the
right chunk is first) but it is a different improvement from the one predicted,
and worth stating plainly rather than filing under "hybrid retrieval helped".
The likely reason recall did not move: at 639 documents, dense retrieval with
k=20 already had the gold chunk in the candidate set for everything it was
going to find. On a corpus ten times larger, the recall story would probably
differ — untested.

Cost: 2.2× the latency, still only 50ms.

---

## 2026-09-23 — `hybrid_rerank` ❌

**Baseline:** `hybrid`
**Change:** added a `bge-reranker-base` cross-encoder over the fused candidates.

| Metric (dev) | hybrid | hybrid_rerank | Δ |
|---|---|---|---|
| recall@5 | 0.786 | 0.714 | **−0.072** |
| recall@1 | 0.429 | 0.286 | **−0.143** |
| MRR | 0.583 | 0.530 | **−0.053** |
| nDCG@10 | 0.774 | 0.692 | **−0.082** |
| `version_sensitive` recall@5 | 1.000 | **0.000** | **−1.000** |
| `ranking_miss` | 0 | **1** | +1 |
| p50 latency | 50ms | **4,678ms** | **93×** |

**Verdict: reverted.** Not shipped; `hybrid` remains the best configuration.

**Why.** This was the change most expected to help, and it hurt on every metric
while costing 93× the latency. Two things are worth separating:

1. **The attribution system caught the mechanism.** A `ranking_miss` appeared
   where there had been none. That label means the gold chunk *was* in the
   candidate set and the reranker demoted it out of the top 5 — which is
   precisely the failure mode a reranker is supposed to fix. Without
   per-stage attribution this would have looked like "recall went down" with
   no explanation.
2. **The version-sensitive item is the clearest casualty.** Its recall went
   from 1.000 to 0.000. That item's gold quote is `[Feature state: beta]` —
   a short, low-information string. A cross-encoder scores query-document
   *semantic* relevance, and a chunk whose distinguishing content is a
   four-word status marker scores poorly against a natural-language question,
   even though it is exactly the right chunk. The reranker is optimising for
   something subtly different from what this corpus needs.

The honest reading is that `bge-reranker-base` is not well matched to short,
identifier-dense documentation chunks, and that a 93× latency cost would have
needed a large win to justify even if it had helped. With 19 dev items a
−0.072 swing is one or two items, so the *magnitude* is noisy — but the
direction, the latency, and the new `ranking_miss` all point the same way, and
none of them argue for keeping it.

**What would change the verdict:** a reranker trained on technical retrieval,
or reranking only when the fused scores are close. Neither is tested here.

---

## Held-out test split

Run once, after the dev comparisons were finished.

| Metric (test, 13 items) | baseline | hybrid | Δ |
|---|---|---|---|
| recall@5 | 0.100 | **0.700** | **+0.600** |
| recall@1 | 0.100 | 0.400 | +0.300 |
| MRR | 0.100 | 0.517 | +0.417 |
| nDCG@10 | 0.100 | 0.563 | +0.463 |
| `exact_term` recall@5 | 0.000 | **1.000** | +1.000 |
| `table_or_code` recall@5 | 0.000 | **1.000** | +1.000 |
| `retrieval_miss` | 9 | 2 | −7 |

The improvement holds on data never used for iteration, and the shape matches
dev: the gains are concentrated in `exact_term` and `table_or_code`, and
`multi_hop` remains 0.000 in both.

---

## What is still unmeasured

- **`hybrid_rerank_rewrite` and `full`** — query rewriting, decomposition,
  claim verification and the judge all need an `ANTHROPIC_API_KEY`. No
  generation metric in this repo has been measured: no correctness, no
  faithfulness, no citation precision, no abstention, no judge agreement.
- **`multi_hop` is 0.000 everywhere.** Two items, both needing evidence from two
  pages, and recall@5 requires *all* gold evidence. Decomposition is the change
  aimed at this, and it is exactly what could not be run.
- **The full corpus.** Everything above is 639 pages from two versions.
