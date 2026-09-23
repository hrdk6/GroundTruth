# Known limitations

Honest running list, per PROJECT_SPEC.md §3.6. Entries are added as weaknesses
are discovered, not as they are fixed — a limitation that was measured and
accepted is more useful to a reader than a clean page.

Each entry: what is weak, why it is that way, and what it would take to fix.

## Phase 0

### L1 — Generation is not bit-for-bit reproducible

Claude 4.6 and later removed `temperature`, so the generation model cannot be
pinned to greedy decoding; sending the parameter is a 400. Two eval runs against
a *cold* LLM cache can therefore produce slightly different answers and slightly
different judge scores.

**Mitigation in place.** The persistent LLM cache makes any re-run of an already
-executed experiment exactly reproducible, and experiment files record the cache
hit rate so a reader can tell whether a run was cached or fresh.

**Real fix.** None available at the API level. Reporting the same metric across
several seeds, with variance, would quantify the noise — not yet done.

### L2 — Postgres full-text search is not BM25

The lexical retrieval leg (Phase 3) ranks with `ts_rank_cd`, which has no IDF
at all, and is called without a normalization flag, so no document-length
normalization either. A common word in the question weighs as much as a rare
field name. Expect it to underperform a real BM25 implementation on long
documents and rare terms.

Until the audit it was also *AND*-matched (`websearch_to_tsquery`), which is a
different and worse problem: 20 of 32 golden questions matched no chunk at all.
`lexical.match: any` fixed that; `hybrid_all_terms` measures it.

**Real fix.** Postgres's length-normalization flags (`ts_rank_cd(tsv, q, 1)`)
are the cheap next experiment. ParadeDB's `pg_search` is the real upgrade and
keeps the single-database architecture.

### L3 — Cost figures depend on a hand-maintained price table

`PRICING_PER_MTOK` in `app/core/llm.py` is transcribed from published pricing
(cached 2026-06-24). If Anthropic changes prices, every cost figure in the repo
silently becomes wrong. Unknown model ids cost `$0.00` and log a warning rather
than failing.

**Real fix.** No pricing endpoint exists to query. The table carries its cache
date, and unit tests assert the published rates so a careless edit fails CI.

### L4 — Windows and CI run different build paths

The dev machine has no GNU make, so `make.ps1` mirrors the `Makefile` by hand.
The two can drift, and only the `Makefile` is exercised by CI.

**Real fix.** Move both to a single task runner (`just`, or a Python CLI). Not
worth the dependency yet; the drift risk is flagged in `CLAUDE.md`.

### L5 — The local dev repo sits inside OneDrive

`.venv/`, `data/raw/`, and `node_modules/` are gitignored, which keeps them out
of git but not out of OneDrive's sync scope in every configuration. Sync churn
or file locks can cause confusing build failures on the dev machine. Not a
problem for anyone cloning the repo elsewhere.

### L6 — Running the API natively on Windows needs a special entrypoint

`uvicorn app.main:app` on Windows builds a `ProactorEventLoop`, which async
psycopg refuses to use. uvicorn passes the loop factory explicitly, so an event
loop *policy* cannot override it. `app/run.py` exists solely to own the
`asyncio.run` call and force `SelectorEventLoop`.

Separately, Windows Application Control on the dev machine blocks the venv's
generated `.exe` console-script shims (`os error 4551`), so all tooling is
invoked as `python -m <tool>`.

**Real fix.** Neither is a problem in Docker, which is the supported path. Both
are documented in `CLAUDE.md` so the next person does not spend an hour on them.

## Phases 1-7

### L7 — The judge grades its own work

The single biggest caveat on every generation number. `answer_correctness`
(0.842 dev, 0.923 test) was produced by `nvidia/nemotron-3-super-120b-a12b`
grading answers written by the same model. Self-evaluation inflates, and no
human labels exist yet to say by how much.

It is also fragile in a way that has now been measured. The same answer
(`beta [2]`) against the same reference was scored 3 with the citation marker
visible and 4 without it. Removing markers from the judge's input — a generic
fix for a judge that read `field[1][2]` as array indexing — moved test
correctness from 0.769 to 0.923. On 13 items, treat this judge as good to
about ±0.15 at best.

`faithfulness` and `citation_precision` should be read the same way: the
verifier is the same model checking citations it wrote.

The one figure here that does not depend on a model is
`abstention_recall 1.000` — abstention is decided by an exact string match on
the fixed abstention sentence.

**Why it happened.** The free tier served only three models fast enough to use:
of 58 chat models probed with a 25-second timeout, 55 timed out or returned
`NotFoundError`. There was no second usable model to judge with.

**Fix.** Point `GT_CHEAP_MODEL` at a different model from
`GT_GENERATION_MODEL`, and run the labeling CLI for ~50 items to get a kappa.

### L8 — The golden set is small, and I wrote it

32 curated items (19 dev / 13 test) against a target of ~300, of which only
14 dev and 10 test carry gold evidence and so count toward retrieval metrics.
Every result now carries a bootstrap interval and every comparison a paired
test (L18), and the verdict is unambiguous: after the measurement audit, **no
retrieval change in the project is distinguishable from noise**. The corrected
baseline, dense retrieval over fixed windows, is as good as anything measured.
The only distinguishable effect in the repo is the audit's own correction.

The set also has one multi-hop item per split, and no item that names an older
release, so `version_correctness` tests the latest-version default rather than
version detection.

The items were also authored by reading the documentation rather than generated
by a model and curated, because the generators need an API key. That is
*stronger* provenance per item — every quote is verified against the parsed
source — but it means the set reflects one author's idea of a good question,
with none of the variety an LLM sweep over thousands of sections would produce.

### L9 — The evaluated corpus is a subset

639 pages from `concepts/` and `tasks/` across versions 1.26 and 1.30, not the
3,102 pages across three branches that `make ingest` fetches. Embedding the
full corpus on CPU ran for over two CPU-hours without finishing.

This bounds every retrieval conclusion. At 639 pages, dense retrieval over 20
candidates may simply be enough, which is one explanation for why no hybrid,
BM25, or reranking variant separated from it. The stages exist for corpora
where dense retrieval is not enough; this one does not test that. Untested.

Every ingestion run records the `include` filter it used, so a result can never
quietly claim more coverage than it had.

### L10 — The chunker's token budget can still overflow the encoder

`bge-small-en-v1.5` accepts 512 tokens. A config with `max_tokens: 512` plus a
prepended heading path exceeds that, and the encoder truncates the tail
silently. `structure_aware` flags such chunks with `exceeds_model_window` in
their metadata, and an atomic block bigger than the window (a long YAML
manifest) is emitted whole rather than split.

**Fix.** Lower `max_tokens` to ~448 to leave room for the heading path, or
measure whether the truncation actually costs recall -- which is an experiment
nobody has run yet.

### L11 — Multi-hop recall is scored strictly, and this flatters nothing

`recall@k` requires *every* piece of gold evidence for an item. A multi-hop
question with one of its two pages retrieved scores 0, not 0.5.
`partial_recall@10` is reported alongside for diagnosis. This makes the
headline number lower than a laxer definition would produce; it is the honest
one, because half the evidence does not answer the question.

### L12 — The frontend is dark-only

No light theme ships. For people who read better on light backgrounds that is a
real narrowing, and it is a choice rather than an oversight: the trace
waterfall and rank trail are the product's core artifacts and read better on a
dark ground. Recorded as decision D10.

### L13 — Ingesting a different root tombstones the rest of that version

`ingest(root=...)` treats whatever is under `root` as the whole corpus for the
versions it touches, so anything absent is marked deleted. Ingesting the
fixture corpus into a database that already holds the real one tombstones 289
real pages, and they vanish from retrieval.

This is correct behaviour for "the corpus is now this" and it is what CI wants
on a fresh database, but locally it is a foot-gun. Re-ingesting the real corpus
now resurrects them (that resurrection path was itself a bug, fixed and
regression-tested), but the surprise remains.

**Fix.** Use a separate database for fixture runs — which is what the local CI
simulation does (`postgres_ci`). Tombstoning is already scoped to the `include`
filter; it is `root` that means "the whole corpus".

### L14 — Smart App Control blocks freshly written binaries

Windows Smart App Control is **enforced** on the dev machine. It blocks
binaries it does not recognise, and "does not recognise" includes any file
newly written to disk — so reinstalling a package can break it even though the
same package worked minutes earlier.

It first showed up on the venv's `.exe` console shims (`os error 4551`), which
is why all tooling runs as `python -m <tool>`. It later blocked
`torch/lib/torch_python.dll` after a `uv sync` rewrote the file, which stops
anything that embeds: ingestion, retrieval evals, and the integration tests.
Those had all run green beforehand.

**Fix.** A reboot often lets the reputation check settle. Otherwise the
alternatives are to disable Smart App Control — which cannot be re-enabled
without reinstalling Windows, so it is a real decision, not a toggle — or to
run the project on a machine or CI runner without it. CI is unaffected.

**Not affected by this:** anything that does not load torch, including
`make llm-check` and the whole LLM provider path, so a provider can still be
configured and verified while embedding is blocked.

### L15 — `multi_hop` is 0.000 in every run

Every configuration, both splits, retrieval-only and full: one item per split
requiring evidence from two pages, neither ever satisfied. `recall@5` demands
*all* gold evidence for an item, so retrieving one of the two pages scores zero
(`partial_recall@10` is reported alongside for diagnosis).

Decomposition in `full` gets further than it looks: on both items it surfaces
*both* gold pages somewhere in the ranked list, but never both in the top 10.
Merging per-sub-query result lists by raw score is the likely culprit — each
sub-query's best chunk should be guaranteed a context slot. With one item per
split this is a diagnosis, not a measurement.

### L16 — Integration tests used to destroy the developer's corpus

The integration tests truncate the corpus tables. They originally ran against
whatever `DATABASE_URL` pointed at, so `make test` silently wiped a freshly
ingested corpus — which happened once during development and cost a full
re-ingest.

They now create and use a separate `<name>_test` database. Worth knowing
because the failure was silent: the tests passed, and the damage only showed up
later as an empty database.

## Found in the measurement audit

Each of these was found by re-reading the code against its own claims, then
measuring. The ones that were bugs are fixed; what remains is recorded here.

### L17 — HNSW filters after the index scan (pgvector 0.6.2)

`pgserver` ships pgvector 0.6.2, which has no iterative index scans. An HNSW
scan returns `ef_search` nearest neighbours from the *whole* index, and the
chunk-set and version filters apply to those. With several chunk sets and
versions sharing one index, dense retrieval came back short (16 of 20 for the
baseline) without any error.

**Mitigation in place.** `dense.ef_search` (default 200) is set per query, and
an exact scan runs whenever the index still returns fewer than `k`; the
fallback is logged. `python -m app.ingestion.prune` removes chunk sets no
config uses, so dead rows stop crowding live ones.

**Real fix.** pgvector ≥ 0.8 with `hnsw.iterative_scan`, or a partial HNSW
index per chunk set. The exact fallback is a sequential scan: fine at tens of
thousands of chunks, not at tens of millions.

### L18 — The intervals are honest, and that is discouraging

Every result now carries a 95% bootstrap interval, and comparisons carry a
paired interval and an exact sign-flip p-value. On 14 scored dev items and 10
test items the intervals are wide: most differences between configs are not
distinguishable from noise, and the tables say so. Percentile bootstrap
intervals are, if anything, slightly too narrow at this n. The fix is more
items, not better statistics.

### L19 — The Docker path is fixed by inspection, not by running it

Docker cannot start on the dev machine (L6), and no GitHub remote exists, so CI
has never run. The Compose fixes in the audit — the frontend's proxy target,
the LLM cache volume path, the provider environment — were made by reading the
files, not by running them. `make db-local` is the path every number came from.

### L20 — Conflict detection is lexical similarity on an exact heading

A conflict is a section whose best-matching counterpart in another version
(same `source_path`, *identical* `heading_path`) has a `difflib` similarity
below 0.92. So: a renamed heading has no counterpart and is never flagged; a
paragraph that was merely reordered can be; and a one-number change in a long
section may stay above the threshold. The audit fixed the gross error —
comparing each chunk against *every* chunk of the section, which made 245 of
the fixture's chunks "conflicts" where 38 were — but the method is still a
heuristic, and it has no golden set of its own.

### L21 — Bare version numbers are detected by shape

`how does this work in 1.28?` now names a version (spec 8.4), via a rule:
major version 1, a two-digit minor, and no unit after it. So "1.5 GB" and
"1.25 CPUs" are quantities, but "set the ratio to 1.20" would read as a
release. There is no version-detection golden set; the rule's tests are the
only evidence it works.

### L22 — Sentence splitting is a regex

The verifier and the UI split answers with one regex on sentence-ending
punctuation followed by a capital, backtick or bracket. "e.g. Pods" splits in
the wrong place, and a sentence starting with a lowercase identifier does not
split at all. Each misplaced boundary changes which claim a citation is
checked against. The audit fixed the splitter's worst case (a mid-answer
`[3]` after its full stop was attributed to the next sentence) but not the
approach.

### L23 — Stored heading paths keep Hugo anchors

Headings like `## Termination of Pods {#pod-termination}` keep the `{#...}`
anchor in the stored heading path, so it is embedded with the chunk (the
heading path is prepended) and matched by conflict detection. The UI strips it
for display. The real fix is in the parser, and it changes every affected
document's content hash, so it waits for the next re-ingest and re-measurement.

### L24 — The pgserver data directory can need crash recovery

The dev database lives under OneDrive, and a killed session left it needing
crash recovery. `make db-local` then timed out: pgserver waits 10 seconds for
`pg_ctl start`, and recovery spent 30 seconds retrying an fsync of pgserver's
own log file, which Windows reported as a sharing violation. Recovery completed
on its own, and re-running `make db-local` picked up the new port. Nothing was
lost, but the first failure looks like a dead database.

### L25 — Generation latency has never been measured cold

Every published `full` record replays the LLM cache, so its p50 measures disk
reads, not a query (the results table marks it †). A cold measurement needs
`GT_LLM_CACHE_ENABLED=false` and a provider fast enough that the number means
something; on the free tier, one verification call per sentence dominates.

