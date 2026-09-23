# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Primary: technical reviewers — engineers, hiring managers, recruiters — who
open GroundTruth at a public URL on their own, without the author there to
narrate. They are judging the author's engineering: they arrive curious and
skeptical, ask one or two questions, and click around to see whether the
claims hold up. The UI has to explain itself.

Secondary (inferred from the repo, not confirmed): the author, driving the
same UI in a live demo (`docs/DEMO_SCRIPT.md`).

## Product Purpose

A question-answering system over the Kubernetes documentation that knows which
release it is answering from and checks its own answers. Every claim in an
answer cites the excerpt it came from, and a second model pass marks each
sentence supported, partially supported, or unsupported. When the cited section
reads differently in another release, the difference is shown beside the
answer, never blended into it. When the evidence cannot support an answer, the
system says so instead of guessing.

Success: within seconds, a first-time reviewer sees that an answer is
checkable — they can click a claim, read the evidence behind it, and see how
the system judged it.

## Positioning

The answer is not the product; the link from each claim to its evidence is.
Around it sits an evaluation harness that audited itself: its first headline
result was a measurement bug it caught, and it now reports "noise" rather than
a win when a change is not distinguishable from chance.

## Operating Context

- Three surfaces: **Ask** (question, answer, evidence, version differences),
  **Traces** (every query's stages on one time axis, and where each chunk was
  ranked or dropped), **Experiments** (recorded runs with 95% intervals and
  paired comparisons).
- Intended to be reached at a public deployed URL. No deployment exists yet;
  the host is undecided.
- Answers from the `full` pipeline take 20–60 s on the free-tier model (one
  verification call per sentence); a question asked before replays from the
  LLM cache in under a second. A reviewer will often wait on a cold answer.
- The indexed corpus is a subset: Kubernetes `concepts/` and `tasks/` pages for
  releases 1.26 and 1.30 (639 pages). Questions outside it are expected and
  should be refused honestly.

## Capabilities and Constraints

- Frontend: Next.js 15 (App Router) + Tailwind CSS v4, in `frontend/`; the API
  is FastAPI, reached through the `/api` rewrite.
- No figure may appear in the UI that does not come from the API or a file in
  `experiments/`. Correctness scores are self-judged (the answering model grades
  itself) and must carry that caveat wherever shown.
- Verdict vocabulary: supported / partially / unsupported; abstention;
  "real" vs "noise" for paired differences; superseded (pre-audit) runs are
  history, not results.
- Pipelines are selectable by name (`full` is the default and the shipping
  pipeline).

## Brand Commitments

- Name: GroundTruth.
- Voice (inferred from the repo's docs and UI copy): plain, specific, candid
  about limits — it would rather say "noise" than show a green arrow.

## Evidence on Hand

- Real experiment records in `experiments/` (16 current, 16 superseded).
- Real traces from live queries; a verified demo question whose cited section
  differs between releases: "How do I set a probe-level
  terminationGracePeriodSeconds?" (beta in 1.26, stable in 1.30).
- No users, testimonials, usage numbers, or deployment exist. None may be
  invented.

## Product Principles

1. Show the evidence, not the confidence: every claim is one click from its
   source and its verdict.
2. Never blend versions; show the difference beside the answer.
3. Say "I don't know" and "noise" out loud; honesty is the feature.
4. The first minute must work for someone alone and skeptical.
