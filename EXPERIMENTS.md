# Experiments

Human-readable log of every experiment and what it concluded. Numbers here are
copied from files in `experiments/` and must match them exactly — the README
results table is generated from those files, never typed by hand.

Format per entry:

```
## <date> — <config name>
Baseline: <what it is compared against>
Change:   <the single variable that moved>
Result:   <metrics, overall and per category>
Verdict:  kept | reverted
Why:      <analysis — especially when the result was negative>
```

---

## No experiments have been run

`experiments/` is empty, so this log is empty.

The retrieval pipeline, the six configs, and the eval runner are all
implemented, but running an experiment needs a Postgres database with pgvector,
and this machine cannot start one: Docker is installed, but WSL2 will not start
because hardware virtualization is disabled in firmware.

Writing plausible numbers here would defeat the entire purpose of the project,
so the log stays empty until a real run produces a real file.

## The experiments that are queued

Each config moves exactly one variable against the one before it, and each
carries its hypothesis in a comment at the top of the YAML file. In order:

| # | Config | Variable introduced | Hypothesis |
|---|---|---|---|
| 0 | `baseline` | — | Fixed 512-token windows, dense-only. The thing to beat. |
| 1 | `structure_aware` | chunker | The baseline cuts YAML examples and tables in half, so `table_or_code` and `exact_term` should gain most. |
| 2 | `hybrid` | lexical leg + RRF | Dense retrieval can't match literal strings like `terminationGracePeriodSeconds`; the gain should concentrate in `exact_term`. |
| 3 | `hybrid_rerank` | cross-encoder | Fusion gives a good candidate *set* and a mediocre ordering; reranking should convert recall@20 into recall@5, i.e. fix `ranking_miss` rather than `retrieval_miss`. Watch p95 latency. |
| 4 | `hybrid_rerank_rewrite` | query rewriting | Expanding abbreviations should help both legs. First config that costs money and latency per query, so it has to earn them. |
| 5 | `full` | decomposition, verification, conflicts | Multi-hop questions need two pages. Only keep decomposition if it helps `multi_hop` more than it hurts everything else. |

## How to run them

```bash
make up && make ingest
make eval CONFIG=configs/baseline.yaml SPLIT=dev MODE=retrieval
make eval CONFIG=configs/structure_aware.yaml SPLIT=dev MODE=retrieval
# ... and so on; then compare on the Experiments page, or with:
make results
```

Record each one here as it finishes — including the ones that make things
worse. A negative result is a finding, and a log with no negative results in it
is a log nobody should believe.
