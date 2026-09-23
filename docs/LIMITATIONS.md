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

The lexical retrieval leg (Phase 3) uses `ts_rank_cd`, which lacks BM25's IDF
saturation and document-length normalization. Expect it to underperform a real
BM25 implementation on long documents and rare terms.

**Real fix.** ParadeDB's `pg_search` extension, which would keep the
single-database architecture. Deferred until the eval shows lexical recall is
actually the bottleneck — otherwise it is an assumption, not a finding.

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

### L7 — No generation metric has been measured

Retrieval is measured; generation is not. Grounded answering, claim
verification, conflict notes and the LLM judge are implemented and unit tested,
but every one of them calls a model, and this environment has no
`ANTHROPIC_API_KEY`. There is therefore **no** answer-correctness,
faithfulness, citation-precision, abstention or judge-agreement figure in this
repository, and the sections that would hold them say so.

The same block stops `hybrid_rerank_rewrite` and `full` from being evaluated,
which means query rewriting and multi-hop decomposition are untested against
data — and `multi_hop` recall is 0.000 in every run, so decomposition is
precisely the change most worth testing.

**Fix.** Set a key, then `make eval CONFIG=configs/full.yaml MODE=full`,
label ~50 items, and report the kappa.

### L8 — The golden set is small, and I wrote it

32 curated items (19 dev / 13 test) against a target of ~300. At 19 dev items,
**one item is worth about 5 points of recall**, so differences below ~0.10
should be read as noise. The `hybrid_rerank` regression of −0.072 is one or two
items; it was reverted on the combination of direction, a new `ranking_miss`
and a 93x latency cost, not on that number alone.

The items were also authored by reading the documentation rather than generated
by a model and curated, because the generators need an API key. That is
*stronger* provenance per item — every quote is verified against the parsed
source — but it means the set reflects one author's idea of a good question,
with none of the variety an LLM sweep over thousands of sections would produce.

### L9 — The evaluated corpus is a subset

639 pages from `concepts/` and `tasks/` across versions 1.26 and 1.30, not the
3,102 pages across three branches that `make ingest` fetches. Embedding the
full corpus on CPU ran for over two CPU-hours without finishing.

This matters for interpreting the `hybrid` result in particular: lexical search
improved *rank* but not *recall*, and the most likely reason is that at 639
documents dense retrieval already had the gold chunk in its top 20. On a corpus
ten times larger the recall story could differ. Untested.

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

**Fix.** Use a separate database for fixture runs, or scope tombstoning to the
`include` filter as well as the root.
