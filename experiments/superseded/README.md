# Superseded runs

The ten records in this directory were produced before the measurement audit
(branch `fix/measurement-audit`). They are kept, unedited, as history. **None of
their numbers should be quoted as a result**, for three reasons that each apply
to every file:

1. **The `fixed` chunker stored corrupted text.** Chunk text was
   `tokenizer.decode(ids)`, which with bge's uncased WordPiece tokenizer
   lowercases and spaces out punctuation. Only 8 of `golden_v1`'s 26 gold
   quotes could match *any* `fixed` chunk of the right page, so every
   `baseline` number here has a recall ceiling of about 0.3 that the record
   does not mention. 100 of 7,277 `structure_aware` chunks were affected the
   same way.
2. **Recall@10 and nDCG@10 were computed over the five-chunk context**, not
   the ranked list, so `recall@10` equals `recall@5` by construction in every
   retrieval run here.
3. **Every file has `"git_dirty": true`.** They were recorded from a working
   tree with uncommitted changes at `0f08127`, so none is reproducible from
   its SHA.

The dense leg was also affected by an HNSW filtering bug (pgvector filters
after the index scan, so queries returned fewer than `k` chunks), and the
verifier attached a mid-answer trailing citation to the wrong sentence.

The results that replace these are the files one directory up. EXPERIMENTS.md
("The measurement audit") walks through what changed and by
how much.

`scripts/generate_results_table.py` reads only `experiments/*.json`, so nothing
in this directory reaches the README.

## Superseded during the audit

Six later records are also here. They are generation runs of `full` from two
commits *inside* the audit, each replaced by a fix, and they exist so that the
numbers EXPERIMENTS.md quotes about those fixes trace to a file. Each was
reproduced from its commit after the fact, with every model call served from
the LLM cache (`cost.cached_calls == cost.calls`), so they are exact replays of
what those commits produced.

| Record | Commit | Superseded by |
|---|---|---|
| `20260923T173236Z_full` (dev), `20260923T173252Z_full` (test) | `ba8bd3b` | conflict notes checked only for cited sections (`f42eb20`) |
| `20260923T173311Z_full` (dev), `20260923T173329Z_full` (test) | `f42eb20` | the judge no longer sees citation markers (`48781b1`) |
| `20260923T171939Z_full` (dev), `20260923T172025Z_full` (test) | `48781b1` | abstention with only part of the evidence is no longer a `false_abstention` |

The replays needed `backend/app/models/` copied in: those commits predate the
fix that stopped `.gitignore` from excluding it, which is itself one of the
audit's findings.
