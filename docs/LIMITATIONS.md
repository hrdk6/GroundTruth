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
