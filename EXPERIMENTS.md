# Experiments

Every number here is read from a file in `experiments/`, and the tables are
printed by `scripts/experiment_tables.py` rather than typed. The README results
table is generated from the same files by `scripts/generate_results_table.py`.

> ### The short version
>
> The first edition of this file reported that structure-aware chunking lifted
> recall@5 from **0.286 to 0.786** — the headline result of the project. It was
> a measurement bug. The baseline's chunker stored lowercased, space-mangled
> text, so most gold quotes could not match *any* baseline chunk, and its
> recall had a ceiling of about 0.3 that nothing reported.
>
> Corrected, the baseline scores **0.857** on dev and **0.700** on test. A
> paired test over the same items separates that correction from noise
> (dev: +0.571, 95% CI [+0.29, +0.86], p = 0.008, 8 items better and 0 worse).
> **No retrieval change in this project does.** On 14 dev and 10 test items,
> chunking, lexical fusion, BM25 and reranking are all within noise of dense
> retrieval over fixed windows.
>
> That is the result, and the harness is now built to make the next one
> harder to get wrong: every run audits its own gold, reports intervals, and
> refuses to publish a comparison it cannot pair.
>
> A second pass found two more defects. nDCG counted duplicate chunks, which
> inflated every dev nDCG by up to 0.23. A lexical query had no tie-breaker, so
> `hybrid` could not be reproduced. Neither changes the verdict
> ([the second pass](#the-second-pass)).

## Setup

| | |
|---|---|
| Corpus | Kubernetes docs, `concepts/` + `tasks/`, versions **1.26 and 1.30** (639 pages) |
| Indexed | 3,882 chunks (`fixed-512-64-r2`) · 7,177 chunks (`structure_aware-512-64-r3`) |
| Golden set | `golden_v1.jsonl`, 32 curated items · dev 19 (14 with gold) / test 13 (10 with gold) |
| Database | PostgreSQL 16.2 + **pgvector 0.6.2** via `pgserver` (no Docker) |
| LLM | `nvidia/nemotron-3-super-120b-a12b` on NVIDIA NIM's free tier, $0.00 |
| Records | every run from a clean tree; the pre-audit runs are in `experiments/superseded/` |

Caveats that bound every number below, now with numbers attached:

1. **Small n.** A 95% interval on a dev recall is about ±0.2 wide. The paired
   comparisons below are the honest way to read any difference.
2. **A corpus subset.** 639 pages. Dense retrieval over 20 candidates may simply
   be enough at this size; the lexical and reranking stages exist for corpora
   where it is not, which this one does not test.
3. **The judge is the model it judges** (generation section). Correctness is
   self-assessed until [judge validation](#whats-still-open) happens.

---

## The measurement audit

A re-read of the harness against its own claims found that the evaluation was
measuring several things other than retrieval. Each item below was fixed,
regression-tested, and then re-measured; the commit messages have the detail.

| Finding | Effect on the published numbers | How it surfaced |
|---|---|---|
| **Chunk text was `tokenizer.decode(ids)`.** bge's uncased WordPiece lowercases and spaces out punctuation: `--service-node-port-range` → `- - service - node - port - range` | Only **8 of 26** gold quotes could match any `fixed` chunk; the baseline's recall ceiling was ~0.3. 100 structure-aware chunks were corrupted too | Reading `FixedChunker`, then counting matchable quotes per chunk set |
| **Recall@10 and nDCG@10 were computed over the five-chunk context** | `recall@10 == recall@5` in every retrieval run, by construction | The README table: two columns always equal |
| **pgvector 0.6.2 filters after the HNSW scan** | The dense leg returned fewer than `k` (16 of 20 for the baseline; 20 of 60 on the fixture) | Checking a docstring's claim that SQL filtering "keeps k honest" |
| **The lexical leg ANDed every term** (`websearch_to_tsquery`), and read `-o` as NOT | **20 of 32** golden questions matched no chunk at all, so "hybrid" was mostly dense retrieval | Counting lexical hits per question |
| **HTML comments and heading shortcodes survived parsing** | 103 wordless chunks; 980 more carrying contributor `TODO`s; "Before you begin" and "What's next" sharing one empty heading on 266 pages | The BM25 reference test disagreeing about N |
| **Chunk freshness was judged per document, not per chunk set** | After any edit, only the first chunker ingested was ever refreshed | A re-ingest reporting 639 pages "unchanged" right after they changed |
| **The runner passed each item's version as an explicit request** | `version_correctness 1.000` was a tautology, and conflict detection never ran in an eval | Reading how `version_correctness` could ever fail |
| **Conflict detection compared each chunk to every chunk of its section** | 245 "conflicts" on the fixture corpus where 38 were real | Strengthening a test that asserted only `isinstance(list)` |
| **Attribution tested for ANY gold, recall for ALL of it** — in the miss labels and, separately, in the abstention check | A multi-hop item with one page found scored recall 0 and attribution "success"; declining to answer with half the evidence was a "false abstention" | Writing the multi-hop tests, then reading a test-split failure |
| **The verifier glued a mid-answer `[3]` to the next sentence** | Correctly cited claims scored unsupported; the next claim was checked against the wrong excerpt | A segment test whose expected verdicts would not attach |
| **The judge was shown citation markers** it had no context for | A correct answer docked for "incorrect indices"; test correctness 0.769 → 0.923 once removed | Reading a test-split failure |
| **Every record said `git_dirty: true`**, partly because each run's own output made the next one dirty | No published run was reproducible from its SHA | Reading the records |
| **Numbers in the docs that no file contained** | The `full` dev results and every latency figure in the first edition | Tracing each number to a record |

The one that mattered most was the first, and what it teaches is where to
check. An integrity test existed — every gold quote is asserted to occur in the
fixture's *source documents* — and it passed the whole time. The quotes were in
the documents; they were not in the chunks. `evals/integrity.py` now audits
every run at the level that is actually scored, records
`integrity.recall_ceiling`, and the CI gate fails when it is below 1.0.

### The second pass

Adding per-stage tracing (ARCHITECTURE D23) meant replaying every record to
show that tracing changes no result. The replays found two more defects of
the same kind. Both are fixed and regression-tested, every record was
re-recorded from one clean commit (`33ace42`), and the replaced files are in
`experiments/superseded/`.

| Finding | Effect on the published numbers | How it surfaced |
|---|---|---|
| **nDCG counted every chunk that contained a gold quote** as a relevant hit, while its ideal ranking assumed one per piece of gold. Overlapping `fixed` windows, and a section split into several structure-aware chunks, all matched the same quote | Per-item nDCG@10 up to **3.31** against a ceiling of 1.0. Every dev nDCG@10 was inflated: `hybrid_bm25` 0.850 → **0.624**, `hybrid_all_terms` 0.842 → 0.640, `full` 0.808 → 0.692. The test split had no item above 1 | Scanning the records for a per-item score above 1 |
| **The `ts_rank_cd` lexical query had no tie-breaker.** Tied scores came back in whatever order the plan produced | The `hybrid` record could not be reproduced from its SHA. Two runs of the same code ranked four test items differently, and test recall@5 is **0.700**, not the recorded 0.600. `hybrid_rerank` moved one unanswerable item, with no metric change | Replaying `hybrid` twice on the old code |

Neither changes the verdict: no retrieval variant was distinguishable from
noise before, and none is now. Apart from `hybrid`'s, every recall and MRR
figure is unchanged, and so is every generation figure. With nDCG computed correctly, the dev configs sit within 0.06 of
each other (0.578 to 0.640) instead of spanning 0.713 to 0.850. The spread was
mostly a count of how many duplicate chunks each config retrieved. The fixture
records reproduce exactly, so the CI gate's floors still hold.

#### What the audit changed, config by config (recall@5)

| Config | dev before | dev after | test before | test after |
|---|---|---|---|---|
| `baseline` | 0.286 | 0.857 | 0.100 | 0.700 |
| `structure_aware` | 0.786 | 0.786 | 0.700 | 0.700 |
| `hybrid` | 0.786 | 0.714 | 0.700 | 0.700 |
| `hybrid_rerank` | 0.714 | 0.714 | 0.800 | 0.700 |

`structure_aware` did not move at all: its chunks were 98.6% verbatim already.
Its "win" was entirely the baseline being mismeasured. `hybrid` moved on dev
because its lexical leg changed semantics (next section); its old behaviour is
preserved as `hybrid_all_terms`, which reproduces the old numbers exactly. The
"after" column is the current record. `hybrid`'s test figure was first
recorded as 0.600, and that was a coin flip on tied lexical scores. Broken
deterministically it is 0.700 ([the second pass](#the-second-pass), below).

---

## Retrieval, re-measured

Every config, both splits, from one commit and a clean tree (`33ace42`, after
the second pass). Retrieval-only mode: no model calls, $0.00. Recall@k, MRR@10 and nDCG@10 are over the full
ranked list; `n` counts items with gold evidence.

#### dev

| Config | n | Recall@1 | Recall@5 (95% CI) | Recall@10 | MRR@10 | nDCG@10 | p50 | Failures |
|---|---|---|---|---|---|---|---|---|
| `baseline` | 14 | 0.357 | 0.857 [0.64, 1.00] | 0.857 | 0.538 | 0.616 | 24ms | ranking_miss 1, retrieval_miss 1 |
| `structure_aware` | 14 | 0.286 | 0.786 [0.57, 1.00] | 0.786 | 0.500 | 0.578 | 19ms | retrieval_miss 3 |
| `hybrid_all_terms` | 14 | 0.429 | 0.786 [0.57, 1.00] | 0.786 | 0.583 | 0.640 | 22ms | retrieval_miss 3 |
| `hybrid` | 14 | 0.429 | 0.714 [0.50, 0.93] | 0.786 | 0.559 | 0.622 | 76ms | retrieval_miss 3, ranking_miss 1 |
| `hybrid_bm25` | 14 | 0.429 | 0.714 [0.50, 0.93] | 0.786 | 0.572 | 0.624 | 109ms | ranking_miss 2, retrieval_miss 2 |
| `hybrid_rerank` | 14 | 0.286 | 0.714 [0.50, 0.93] | 0.786 | 0.537 | 0.588 | 6,641ms | retrieval_miss 3, ranking_miss 1 |

#### test

| Config | n | Recall@1 | Recall@5 (95% CI) | Recall@10 | MRR@10 | nDCG@10 | p50 | Failures |
|---|---|---|---|---|---|---|---|---|
| `baseline` | 10 | 0.500 | 0.700 [0.40, 1.00] | 0.900 | 0.603 | 0.671 | 23ms | ranking_miss 2, retrieval_miss 1 |
| `structure_aware` | 10 | 0.300 | 0.700 [0.40, 1.00] | 0.700 | 0.467 | 0.526 | 20ms | retrieval_miss 2, ranking_miss 1 |
| `hybrid_all_terms` | 10 | 0.400 | 0.700 [0.40, 1.00] | 0.700 | 0.517 | 0.563 | 30ms | retrieval_miss 2, ranking_miss 1 |
| `hybrid` | 10 | 0.400 | 0.700 [0.40, 1.00] | 0.700 | 0.505 | 0.563 | 66ms | ranking_miss 3 |
| `hybrid_bm25` | 10 | 0.300 | 0.700 [0.40, 1.00] | 0.700 | 0.496 | 0.559 | 91ms | ranking_miss 3 |
| `hybrid_rerank` | 10 | 0.400 | 0.700 [0.40, 1.00] | 0.900 | 0.592 | 0.676 | 6,737ms | ranking_miss 3 |

### What each experiment found

Paired over the same items: a bootstrap interval on the per-item difference,
and an exact sign-flip test. "Distinguishable" means the interval excludes zero.

#### dev

| A → B | Recall@5 Δ (95% CI) | p | better / worse | MRR@10 Δ (95% CI) | Verdict |
|---|---|---|---|---|---|
| baseline (pre-audit) → baseline | +0.571 [+0.29, +0.86] | 0.008 | 8 / 0 | +0.264 [+0.11, +0.46] | **distinguishable** |
| baseline → structure_aware | -0.071 [-0.21, +0.00] | 1.000 | 0 / 1 | -0.038 [-0.29, +0.20] | noise |
| structure_aware → hybrid_all_terms | +0.000 [+0.00, +0.00] | 1.000 | 0 / 0 | +0.083 [+0.00, +0.20] | noise |
| hybrid_all_terms → hybrid | -0.071 [-0.21, +0.00] | 1.000 | 0 / 1 | -0.024 [-0.19, +0.13] | noise |
| hybrid → hybrid_bm25 | +0.000 [-0.21, +0.21] | 1.000 | 1 / 1 | +0.013 [-0.19, +0.23] | noise |
| hybrid → hybrid_rerank | +0.000 [-0.21, +0.21] | 1.000 | 1 / 1 | -0.023 [-0.27, +0.24] | noise |
| baseline → hybrid_bm25 | -0.143 [-0.36, +0.00] | 0.500 | 0 / 2 | +0.034 [-0.24, +0.30] | noise |

#### test

| A → B | Recall@5 Δ (95% CI) | p | better / worse | MRR@10 Δ (95% CI) | Verdict |
|---|---|---|---|---|---|
| baseline (pre-audit) → baseline | +0.600 [+0.30, +0.90] | 0.031 | 6 / 0 | +0.503 [+0.24, +0.77] | **distinguishable** |
| baseline → structure_aware | +0.000 [-0.30, +0.30] | 1.000 | 1 / 1 | -0.137 [-0.40, +0.07] | noise |
| structure_aware → hybrid_all_terms | +0.000 [+0.00, +0.00] | 1.000 | 0 / 0 | +0.050 [+0.00, +0.15] | noise |
| hybrid_all_terms → hybrid | +0.000 [+0.00, +0.00] | 1.000 | 0 / 0 | -0.012 [-0.21, +0.15] | noise |
| hybrid → hybrid_bm25 | +0.000 [+0.00, +0.00] | 1.000 | 0 / 0 | -0.009 [-0.14, +0.09] | noise |
| hybrid → hybrid_rerank | +0.000 [+0.00, +0.00] | 1.000 | 0 / 0 | +0.087 [-0.16, +0.35] | noise |
| baseline → hybrid_bm25 | +0.000 [-0.30, +0.30] | 1.000 | 1 / 1 | -0.107 [-0.36, +0.10] | noise |

**The correction is the only distinguishable effect.** Every retrieval
experiment is within noise on both splits, and most change the outcome of one
or two items. Read the verdicts as that, not as a leaderboard:

- **`structure_aware`** — no measurable retrieval benefit here. The corrected
  per-category table is the reverse of the old story: the *fixed-window*
  baseline scores 1.000 on `table_or_code`, so fixed windows were not
  cutting the answer out of tables and code at this chunk size. The original
  claim ("under fixed windows not a single table_or_code question could be
  answered") was the decode bug.
- **`hybrid_all_terms`** — the lexical leg as first built. It matched nothing for
  20 of 32 questions, so fusing it changed little: recall identical, MRR +0.08
  on dev (noise).
- **`hybrid`** — any-term matching fixes the empty leg but, ranked by
  `ts_rank_cd`, which has no IDF, it helps `exact_term` items and hurts
  `factual` ones: a question about ConfigMap size had its gold chunk fall from
  rank 1 to 8, outranked by chunks repeating "data". On the fixture it lost
  the one `table_or_code` item, and the CI gate caught it.
- **`hybrid_bm25`** — Okapi BM25 computed in SQL (next section) puts the
  ConfigMap item back at rank 1, restores what `hybrid` lost on the fixture
  (fixture recall@5 1.000 vs 0.667), and ties it on both golden splits. It is
  what `full` ships, on the strength of the fixture and the IDF argument, not
  on a golden-set difference, which it does not have. (It was once said to
  restore a test-split loss too. That loss was the tie-order coin flip of
  [the second pass](#the-second-pass).)
- **`hybrid_rerank`** — within noise of `hybrid` on both splits, at p50
  **6.6 s** against 76 ms on CPU. Off in `full`.

#### Recall@5 by category

#### dev

| Category | n | `baseline` pre-audit | `baseline` | `structure_aware` | `hybrid_bm25` |
|---|---|---|---|---|---|
| `exact_term` | 5 | 0.200 | 0.800 | 0.800 | 0.600 |
| `factual` | 4 | 0.750 | 1.000 | 0.750 | 0.750 |
| `multi_hop` | 1 | 0.000 | 0.000 | 0.000 | 0.000 |
| `table_or_code` | 3 | 0.000 | 1.000 | 1.000 | 1.000 |
| `version_sensitive` | 1 | 0.000 | 1.000 | 1.000 | 1.000 |

#### test

| Category | n | `baseline` pre-audit | `baseline` | `structure_aware` | `hybrid_bm25` |
|---|---|---|---|---|---|
| `exact_term` | 3 | 0.000 | 0.667 | 1.000 | 1.000 |
| `factual` | 4 | 0.250 | 0.750 | 0.750 | 0.750 |
| `multi_hop` | 1 | 0.000 | 0.000 | 0.000 | 0.000 |
| `table_or_code` | 1 | 0.000 | 1.000 | 1.000 | 1.000 |
| `version_sensitive` | 1 | 0.000 | 1.000 | 0.000 | 0.000 |

`multi_hop` is 0.000 in every run: both items need two pages in the top 5, and
no config retrieves both. With one item per split this is an anecdote, not a
measurement.

### BM25 in Postgres

`ts_rank_cd` has no IDF and no length normalization, which the first edition
listed as its top limitation and then did not measure. `lexical.ranking: bm25`
computes Okapi BM25 in one statement over exactly the filtered chunk set: `N`
and mean length from the scope, document frequency from a GIN-backed `@@`
count per query term, term frequency from `unnest(tsv)`. No extension, no side
table. `test_bm25_in_sql_matches_a_reference_implementation` recomputes every
returned score with an independent Python BM25 over the same tsvectors and
agrees to 1e-9. That test is also what found the HTML-comment chunks: it
disagreed with the SQL about `N`.

Searching each golden question in its gold evidence's release, `ts_rank_cd`
has no gold chunk anywhere in its top 20 for six questions. BM25 ranks it 1st,
2nd, 3rd, 4th, 7th and 20th. This was measured directly against the index
(`lexical_search` with each config, at `33ace42`) rather than read from a
record. The cost is 40–60 ms per query, about twice `ts_rank_cd`'s.

---

## Generation

Config `full`: `hybrid_bm25` retrieval plus query rewriting and multi-hop
decomposition, six chunks of context, per-sentence claim verification with
regenerate-once-then-abstain, and version notes on cited sections. Versions are
resolved from each question, as for a real user — the first time an eval has
exercised version detection and conflict notes at all.

| Metric | dev | test |
|---|---|---|
| Correctness (self-judged) | 0.842 [0.68, 1.00] | 0.923 [0.77, 1.00] |
| Faithfulness | 0.974 [0.92, 1.00] | 0.942 [0.83, 1.00] |
| Citation precision | 1.000 [1.00, 1.00] | 0.900 [0.70, 1.00] |
| Abstention recall | 1.000 | 1.000 |
| Abstention precision | 0.625 | 0.750 |
| Version correctness | 1.000 | 1.000 |
| Retrieval recall@5 | 0.786 [0.57, 1.00] | 0.800 [0.50, 1.00] |
| Context recall (top 6) | 0.786 | 0.900 |

_dev: 19 items; failures: `ranking_miss` 2, `false_abstention` 1; 77 model calls (77 cached), $0.00; record `20261010T141045Z_full`._

_test: 13 items; failures: `ranking_miss` 1; 59 model calls (59 cached), $0.00; record `20261010T141101Z_full`._

How much weight each row deserves, most trustworthy first:

- **Abstention recall 1.000** is decided by an exact string match, not a model:
  all 8 unanswerable questions across both splits were refused.
- **Abstention precision** is where the failures are. On dev, 3 of 8
  abstentions were on answerable questions: two where the gold chunk was cut
  from the context (`ranking_miss` — declining was the right call given what
  the model saw) and one where the evidence was there and verification still
  pulled the answer (`false_abstention`). On test the one answerable abstention
  is the multi-hop item, with one of its two pages in context: again the right
  call, attributed to the page that was cut.
- **Version correctness 1.000** now means something, but not much: both
  version-sensitive items ask about the latest release, so the latest-version
  default answers them. The golden set has no item that names an older
  release, which is the case this metric exists for.
- **Faithfulness and citation precision** come from the verifier, which is the
  same model that wrote the answers.
- **Correctness is self-judged, and demonstrably fragile.** The judge was being
  shown citation markers it had no context for; `.spec.revisionHistoryLimit[1][2]`
  — the right answer — was scored 3 for "incorrect indices". Stripping markers
  from the judge's input (a generic fix, not one aimed at an item) moved test
  correctness from 0.769 (`superseded/20260923T173329Z_full`) to 0.923. One of
  the two items that flipped was that misreading. The other, `beta [2]`, is the
  *same answer against the same reference*, scored 3 with the marker visible
  and 4 without it. A judge whose
  verdict turns on a formatting detail is not measuring correctness to better
  than about ±0.15 on 13 items — which is why judge validation leads the list
  below.

With versions resolved from the question, conflict detection ran in an eval
for the first time — and showed notes attached to sections the answer never
cited. The label-value answer, cited from *Labels and Selectors*, carried a note
about *Metrics For Kubernetes System Components*
(`superseded/20260923T173236Z_full`). Conflicts are now checked only for cited
sections; notes fell from 5 to 4 answers on dev and from 2 to 1 on test, and
every remaining note concerns a section the answer cites.

**Latency is not reported here.** These records are replays — every model call
was served from the LLM cache — so their p50 measures the cache, not a query
(the results table marks it †). On the free tier a cold `full` answer takes
tens of seconds, dominated by one verification call per sentence; measuring it
properly needs a run with the cache disabled.

## Observability: tracing changes no result

Per-component observability (ARCHITECTURE D23) puts every eval item under an
in-memory tracer and every model call behind an observer. Both only read, but
"only reads" is a claim, so it was tested. Every recorded `golden_v1`
retrieval run was re-run with tracing on and `--no-write`, and diffed against
its committed record, metric by metric and chunk id by chunk id:

| Record | Result |
|---|---|
| `baseline` dev, test | identical |
| `structure_aware` dev, test | identical |
| `hybrid_all_terms` dev, test | identical |
| `hybrid_bm25` dev, test (shipping retrieval) | identical |
| `hybrid` dev, test | **differs, and differs between two runs of the old code too** |
| `hybrid_rerank` dev, test | metrics identical; one unanswerable item's ranking differs (same cause) |

The `hybrid` difference is not tracing. The same replay on the code *before*
this change, run twice in a row, gave a different ranking each time:
`ts_rank_cd` produces ties, and that query orders by rank alone, so Postgres
returns tied chunks in whatever order the plan produces. The BM25 query breaks
ties by chunk id; this one did not. So the committed `hybrid` record could not
be reproduced, which was a measurement defect of its own
([the second pass](#the-second-pass)).

From here on every record carries a `stages` block (p50/p95 per stage, model
calls, cache hits, cost, and how many items each stage *fell back* on), and
every item its own per-stage breakdown. A metric that moved because the
rewrite stage silently failed on half the items now says so in the record.

---

## What's still open

- **Judge validation is still the biggest gap.** `answer_correctness` is
  Nemotron grading Nemotron. The labeling CLI now shows the reference answer
  and the cited excerpts, ties each label to the answer it judged, and leaves
  rule-decided abstentions out of kappa; it needs ~50 human labels.
- **A bigger golden set.** Every retrieval question above is unanswerable at
  n = 14. The generators are built and cost-gated, and `build` now merges
  drafts into the golden set instead of overwriting it.
- **Golden items that name an older release**, so `version_correctness` tests
  version detection rather than the latest-version default.
- **A cold-cache latency measurement** for `full`; every published run here was
  mostly served from the LLM cache.
- **A generation-level chunking test.** Whether whole YAML blocks in the context
  improve *answers* is the remaining case for `structure_aware`, and it is
  untested: `full` with `fixed` chunks against `full`.
- **BM25 length normalization uses distinct-lexeme counts**, not token counts.
  A token-count column would make it exact.
