# Progress

Phase checklist from `PROJECT_SPEC.md` §12. Updated at the end of every phase.

Legend: `[x]` done · `[~]` in progress · `[ ]` not started

## Phase 0 — Scaffold  `[~]`

- [x] Repository structure
- [x] `uv` project (`backend/pyproject.toml`), Python 3.12 pinned
- [x] Docker Compose: Postgres 16 + pgvector, backend, frontend placeholder
- [x] Alembic configured; migration `0001` enables `vector` + `pg_trgm`
- [x] Config loader: `Settings` (environment) + `PipelineConfig` (experiments)
- [x] LLM client wrapper with persistent cache and cost logging
- [x] `Makefile` + `make.ps1` (Windows shim)
- [x] `.env.example`, `.gitignore`, pre-commit
- [x] `CLAUDE.md` with conventions and commands
- [x] `GET /health` reporting database + pgvector + cache state
- [x] Test suite green (37 tests), ruff and mypy clean

**Acceptance:** `make up` works; `GET /health` returns OK; tests pass.

| Criterion | State |
|---|---|
| Tests pass | ✅ 37 passed; ruff and mypy clean |
| `GET /health` returns OK | ⚠️ Endpoint verified live against a running server, but it correctly reports `degraded` without Postgres. `ok` requires a database. |
| `make up` works | ❌ **Unverified** — Docker is not installed on the dev machine yet (`winget install -e --id Docker.DockerDesktop`). |

Phase 0 closes once Docker is installed and `make up` brings the stack to
`status: "ok"` with migration `0001` applied.

## Phase 1 — Ingestion + naive RAG baseline  `[ ]`

- [ ] Fetch K8s docs for `release-1.26`, `release-1.28`, `release-1.30`
- [ ] Markdown + front-matter parsing, Hugo shortcode handling
- [ ] `fixed` chunker
- [ ] Batched embedding, model recorded in metadata
- [ ] Incremental re-ingestion via content hash
- [ ] Dense-only retrieval
- [ ] Grounded generation with citations
- [ ] `POST /query`

**Acceptance:** re-running ingestion with no changes re-embeds 0 docs; a query
returns a cited answer.

## Phase 2 — Evaluation harness + baseline numbers  `[ ]`
## Phase 3 — Retrieval improvements  `[ ]`
## Phase 4 — Generation quality  `[ ]`
## Phase 5 — Observability  `[ ]`
## Phase 6 — Frontend  `[ ]`
## Phase 7 — CI, docs, demo  `[ ]`
