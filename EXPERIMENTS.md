# Experiments

Every number here is copied from a file in `experiments/`. The README results
table is generated from those same files by `scripts/generate_results_table.py`.

## Setup

| | |
|---|---|
| Corpus | Kubernetes docs, `concepts/` + `tasks/`, versions **1.26 and 1.30** |
| Indexed | 639 documents · 3,905 chunks (`fixed`) / 7,277 chunks (`structure_aware`) |
| Golden set | `golden_v1.jsonl`, 32 curated items · dev 19 / test 13 |
| Database | PostgreSQL 16.2 + pgvector via `pgserver` (no Docker) |
| LLM | `nvidia/nemotron-3-super-120b-a12b` on NVIDIA NIM's free tier, $0.00 |

Three caveats that bound everything below. They are not boilerplate — each one
changes how much weight a number deserves.

1. **The golden set is 32 items.** At 19 dev items **one item is worth ~5 points
   of recall**. Read directions, not third decimals. Anything under ~0.10 is one
   or two items.
2. **The corpus is a subset.** Embedding all 3,102 pages on CPU ran over two
   CPU-hours without finishing, so ingestion was scoped to `concepts/` and
   `tasks/` in two versions. Every ingestion run records the filter it used.
3. **The judge and the answerer are the same model.** `answer_correctness` is
   Nemotron grading Nemotron's own output. That is self-evaluation, and it
   inflates. It is why judge validation (below) is the outstanding gap, not a
   formality.

---

## Phase 3 — retrieval

Four configs, each moving exactly one variable. Retrieval-only mode: no model
calls, no key, $0.00 per run.

| Config | recall@5 dev | recall@5 test | MRR dev | Verdict |
|---|---|---|---|---|
| `baseline` | 0.286 | 0.100 | 0.274 | — |
| `structure_aware` | **0.786** | 0.700 | 0.500 | kept |
| `hybrid` | **0.786** | 0.700 | **0.583** | kept |
| `hybrid_rerank` | 0.714 | 0.800 | 0.530 | **reverted** |

### `structure_aware` — the chunker (+0.500 recall@5)

Per category, dev recall@5:

| Category | baseline | structure_aware |
|---|---|---|
| `table_or_code` | 0.000 | **1.000** |
| `exact_term` | 0.200 | **0.800** |
| `version_sensitive` | 0.000 | **1.000** |
| `factual` | 0.750 | 0.750 |

The cleanest result in the series, because of what *didn't* move. `factual` is
unchanged at 0.750 — prose questions never depended on block integrity, so
fixing block integrity did nothing for them. Meanwhile `table_or_code` went from
**0.000 to 1.000**: under fixed windows not a single question whose answer lived
in a table or code block could be answered, because no chunk contained a whole
one. A change that had lifted everything uniformly would have been suspicious.

Cost: chunks rose 3,905 → 7,277 (+86%), ingestion 379s → 547s.

### `hybrid` — lexical + RRF (rank, not recall)

recall@5 did not move at all. recall@1 rose 0.286 → 0.429 and MRR 0.500 → 0.583.
The hypothesis was that lexical search would find exact strings dense retrieval
misses and lift `exact_term` recall; instead the gold chunks were *already*
being retrieved, and lexical evidence pushed them to the top.

Worth stating plainly rather than filing under "hybrid retrieval helped". The
likely reason recall didn't move: at 639 documents, dense retrieval with k=20
already had the gold chunk in its candidate set. On a corpus ten times larger
this could differ — untested.

Cost: p50 23ms → 50ms.

### `hybrid_rerank` — the cross-encoder ❌

| Metric | hybrid | hybrid_rerank |
|---|---|---|
| recall@5 dev | 0.786 | **0.714** |
| recall@5 test | 0.700 | **0.800** |
| `version_sensitive` dev | 1.000 | **0.000** |
| `ranking_miss` | 0 | **1** |
| p50 latency | 50ms | **4,678ms (93×)** |

**Reverted — but the honest version is messier than "it made things worse".**
It hurt on dev and *helped* on test. Both are one-item swings on 19 and 13
items respectively, which is exactly the noise floor caveat above. The
measurement does not settle it.

What does settle it is the other two columns. The attribution system flagged a
new `ranking_miss` — meaning the gold chunk was retrieved and then demoted out
of the top 5, which is precisely the failure a reranker is supposed to fix. And
the `version_sensitive` item went to 0.000: its gold quote is
`[Feature state: beta]`, a four-word status marker, and a cross-encoder scoring
query-document *semantic* relevance rates that poorly against a
natural-language question even though it is the right chunk.

A 93× latency cost needs a clear win to justify. There isn't one, so it is off
in `configs/full.yaml`, with a comment saying why. Keeping it enabled because
the spec lists it would contradict the evidence this project exists to produce.

**What would change the verdict:** a reranker trained on technical retrieval, or
reranking only when the fused scores are close. Neither is tested.

---

## Phase 4 — generation

Config `full`: `hybrid` retrieval plus query rewriting, multi-hop decomposition,
per-sentence claim verification with regenerate-or-abstain, and cross-version
conflict notes.

### The first run found a bug in the evaluator, not the model

| Metric (dev) | first run | after fix |
|---|---|---|
| answer_correctness | 0.684 | **0.842** |
| faithfulness | 0.790 | **0.974** |
| citation_precision | 0.500 | **1.000** |
| `false_abstention` | 4 | **1** |

The first run reported 4 false abstentions — the system refusing to answer when
the gold evidence *was* in its context. The `support_fraction` distribution gave
it away: every item scored exactly **0.0 or 1.0**, never anything between. That
is not a threshold to tune, it is a binary parsing failure.

Two bugs, both in the verifier, both punishing the model for behaving correctly:

1. **Orphaned trailing citations.** The sentence splitter treated `[` as a
   sentence start, so `...no more than 253 characters. [3]` split into a claim
   plus a lone `[3]`. The claim was then uncited, and an uncited factual
   sentence is scored unsupported by design — so a correctly cited answer
   scored 0.0 and was abstained away.
2. **Non-ASCII citation brackets.** The model wrote `【1】`, the full-width CJK
   form. The citation regex matched only `[n]`, so an answer citing every
   sentence was read as citing none.

Both are now fixed and regression-tested. The comparison above is clean: the
generations were served from the LLM cache, so the *only* variable between the
two runs was citation parsing.

This is the eval harness earning its keep in the least glamorous way — by
catching a defect in itself. Without the `support_fraction` distribution and the
`false_abstention` label, the obvious move would have been to lower the
abstention threshold, which would have hidden the bug and degraded the product.

### Held-out results (`full`, test split, 13 items)

| Metric | Value |
|---|---|
| answer_correctness | 0.923 |
| faithfulness | 1.000 |
| citation_precision | 1.000 |
| abstention_precision | 1.000 |
| abstention_recall | 1.000 |
| version_correctness | 1.000 |
| recall@5 | 0.800 |
| Failures | 1 `generation_failure` |

**Do not read these at face value.** Three reasons:

- **The judge is the model being judged.** Self-evaluation inflates
  correctness. Until the judge is validated against human labels, 0.923 is a
  number the system awarded itself.
- **13 items.** One item is 7.7 points.
- `faithfulness` and `citation_precision` at exactly 1.000 mean the verifier
  found nothing wrong, which for the same model checking its own citations is
  a weaker claim than it looks.

`abstention_recall 1.000` is the most trustworthy figure here: every
unanswerable question was refused, and that is decided by an exact string match
on the abstention sentence, not by a model.

### Retrieval improved too

`full` reached recall@5 **0.857** on dev, against `hybrid`'s 0.786 — query
rewriting and decomposition helped retrieval. Not isolated to one variable, so
it is an observation, not an experiment.

---

## What is still open

- **Judge validation is the biggest gap.** `evals/judge/label.py` is built and
  wired; it needs ~50 human labels and a Cohen's kappa. Until then every
  correctness number is self-assessed. Below a kappa of ~0.6 the honest move is
  to fix the judge prompt, not publish what it produced.
- **`multi_hop` is 0.000 in every single run.** Two items, each needing evidence
  from two pages, and recall@5 requires *all* gold evidence. Decomposition is
  enabled in `full` and still did not fix it.
- **A separate judge model.** The cheapest real improvement available: point
  `GT_CHEAP_MODEL` at a different model from `GT_GENERATION_MODEL` so the judge
  is not grading itself. Blocked here only because Nemotron-3-Super was the one
  model on the free tier that reliably answered in under 25 seconds — 55 of 58
  others timed out or were not served.
- **The full corpus**, and a golden set nearer the ~300 the spec targets.
