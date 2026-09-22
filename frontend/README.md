# Frontend (Phase 6)

Next.js (App Router) + TypeScript + Tailwind. Three pages:

1. **Chat** — ask a question, pick a version or let it auto-detect, see the
   answer with clickable citations (source + version + heading path), conflict
   notes, per-sentence verification badges, and thumbs up/down.
2. **Trace viewer** — span waterfall for any query, with the retrieved chunks
   and their scores at each stage, so a ranking miss is visible rather than
   inferred.
3. **Experiments dashboard** — every run, with side-by-side comparison of two
   runs (metric deltas overall and per category) and attribution charts.

Not started yet. The backend API it consumes is built in Phases 1–5.

`docker compose` keeps this service behind the `frontend` profile until then:

```bash
docker compose --profile frontend up -d
```
