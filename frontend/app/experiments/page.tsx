"use client";

/**
 * Experiments — the record of what each engineering decision actually did.
 *
 * Selecting two runs shows deltas overall and per category. Per-category is the
 * point: a change that lifts `exact_term` by 0.2 while costing 0.05 everywhere
 * else is a different decision from one that lifts everything slightly, and an
 * overall average hides which one happened.
 *
 * Every headline delta carries a paired 95% interval and says plainly whether
 * it is distinguishable from noise. On a 19-item split most differences under
 * ~0.15 are not, and a dashboard that shows a green +0.071 without saying so is
 * making a claim the data does not support.
 */

import { useEffect, useMemo, useState } from "react";
import {
  ApiError,
  api,
  type Comparison as PairedComparison,
  type ExperimentDetail,
  type ExperimentSummary,
  type Interval,
} from "@/lib/api";
import { Delta, EmptyState, ErrorNote, Meter, Spinner, formatCost, formatTime } from "@/components/primitives";

const HEADLINE_METRICS = [
  ["recall@5", "Recall@5"],
  ["recall@10", "Recall@10"],
  ["mrr", "MRR@10"],
  ["ndcg@10", "nDCG@10"],
  // The answering model grades itself here, so the label says so.
  ["answer_correctness", "Correctness, self-judged"],
  ["faithfulness", "Faithfulness"],
  ["citation_precision", "Citation precision"],
  ["abstention_recall", "Abstention recall"],
  ["version_correctness", "Version correctness"],
] as const;

const ATTRIBUTION_LABELS: Record<string, string> = {
  retrieval_miss: "Retrieval miss",
  ranking_miss: "Ranking miss",
  generation_failure: "Generation failure",
  false_answer: "Answered the unanswerable",
  false_abstention: "Declined despite evidence",
  version_error: "Wrong version",
};

const ATTRIBUTION_COLORS: Record<string, string> = {
  retrieval_miss: "#B8322C",
  ranking_miss: "#D69E2E",
  generation_failure: "#7A5195",
  false_answer: "#C46A2B",
  false_abstention: "#2E8B8B",
  version_error: "#666C78",
};

export default function ExperimentsPage() {
  const [runs, setRuns] = useState<ExperimentSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [details, setDetails] = useState<Record<string, ExperimentDetail>>({});
  // Pre-audit runs are history. Off by default; on, they can be compared
  // against their corrected re-runs to see what the measurement bugs did.
  const [showSuperseded, setShowSuperseded] = useState(false);

  useEffect(() => {
    api
      .experiments(showSuperseded)
      .then((data) => {
        setRuns(data);
        setSelected((current) => {
          const ids = new Set(data.map((run) => run.id));
          const kept = current.filter((id) => ids.has(id));
          if (kept.length) return kept;
          // Default to the newest run and the newest *comparable* one: same
          // golden set, same split. The two newest runs are often dev and test
          // of one config, and comparing those measures the questions, not the
          // pipeline.
          const [newest] = data;
          if (!newest) return [];
          const partner = data.find(
            (run) =>
              run.id !== newest.id &&
              run.dataset_version === newest.dataset_version &&
              run.split === newest.split,
          );
          return partner ? [partner.id, newest.id] : [newest.id];
        });
      })
      .catch((exc) => setError(exc instanceof ApiError ? exc.message : "Could not load experiments."));
  }, [showSuperseded]);

  useEffect(() => {
    for (const id of selected) {
      if (details[id]) continue;
      api
        .experiment(id)
        .then((detail) => setDetails((current) => ({ ...current, [id]: detail })))
        .catch(() => {
          /* the row stays selectable; the panel just shows nothing */
        });
    }
  }, [selected, details]);

  const toggle = (id: string) => {
    setSelected((current) => {
      if (current.includes(id)) return current.filter((x) => x !== id);
      // Keep at most two: this is a before/after comparison, not a leaderboard.
      return [...current, id].slice(-2);
    });
  };

  const [baseId, headId] = selected;
  const base = baseId ? details[baseId] : undefined;
  const head = headId ? details[headId] : undefined;

  const categories = useMemo(() => {
    const names = new Set<string>();
    for (const detail of [base, head]) {
      if (detail) Object.keys(detail.metrics_by_category).forEach((c) => names.add(c));
    }
    return [...names].sort();
  }, [base, head]);

  return (
    <div className="mx-auto max-w-[1320px] px-4 sm:px-8 pt-10 sm:pt-12">
      <h1 className="text-[34px] leading-tight font-extrabold tracking-[-0.02em] text-ink">Experiments</h1>
      <p className="mt-2 text-[17px] text-ink-2 max-w-[66ch]">
        Every run is a file in <code className="mono text-[0.9em] px-1 rounded bg-well">experiments/</code>,
        recorded with its config, commit, and question set. Select two to compare them question by question:
        a difference is called real only when its paired 95% interval leaves out zero.
      </p>
      <label className="mt-4 inline-flex items-center gap-2.5 text-[15px] text-ink-2 cursor-pointer">
        <input
          type="checkbox"
          checked={showSuperseded}
          onChange={(event) => setShowSuperseded(event.target.checked)}
          className="size-4 accent-[#15171C]"
        />
        Include superseded pre-audit runs (kept as history; their numbers are not results)
      </label>

      <div className="mt-7">
        {error && <ErrorNote message={error} />}
        {!error && runs === null && <Spinner label="Loading experiments" />}

        {runs?.length === 0 && (
          <EmptyState title="No experiments recorded yet">
            Run <code className="mono text-[0.9em]">make eval CONFIG=configs/baseline.yaml</code> to produce one.
            Until then there are no numbers to show, and inventing them would defeat the point of the project.
          </EmptyState>
        )}

        {runs && runs.length > 0 && (
          <>
            <div className="sheet overflow-x-auto">
              <table className="w-full min-w-[980px] text-[15px]">
                <thead>
                  <tr className="text-left text-[13px] text-ink-3 border-b border-rule">
                    <th className="pl-5 pr-2 py-3 w-10 font-normal">
                      <span className="sr-only">Compare</span>
                    </th>
                    <th className="px-3 py-3 font-normal">Config</th>
                    <th className="px-3 py-3 font-normal w-32">Questions</th>
                    <th className="px-3 py-3 font-normal w-16">Split</th>
                    <th className="px-3 py-3 font-normal w-24">Mode</th>
                    <th className="px-3 py-3 font-normal w-12 text-right">n</th>
                    <th className="px-3 py-3 font-normal w-56">Recall@5, with 95% interval</th>
                    <th className="px-3 py-3 font-normal w-36">MRR@10</th>
                    <th className="px-3 py-3 font-normal w-16 text-right">Cost</th>
                    <th className="px-3 py-3 font-normal w-24">Commit</th>
                    <th className="pl-3 pr-5 py-3 font-normal w-36">When</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-rule">
                  {runs.map((run) => {
                    const checked = selected.includes(run.id);
                    return (
                      <tr
                        key={run.id}
                        onClick={() => toggle(run.id)}
                        className={`cursor-pointer transition-colors ${checked ? "bg-paper" : "hover:bg-paper/60"}`}
                      >
                        <td className="pl-5 pr-2 py-3">
                          <input
                            type="checkbox"
                            checked={checked}
                            onChange={() => toggle(run.id)}
                            onClick={(event) => event.stopPropagation()}
                            aria-label={`Compare ${run.config_name}, ${run.split}`}
                            className="size-4 accent-[#15171C]"
                          />
                        </td>
                        <td className="px-3 py-3">
                          <span className="mono text-[14px] font-bold text-ink">{run.config_name}</span>
                          {run.superseded && (
                            <span className="ml-2 machine text-fail">superseded</span>
                          )}
                        </td>
                        <td className="px-3 py-3 mono text-[13px] text-ink-2">{run.dataset_version ?? "—"}</td>
                        <td className="px-3 py-3 mono text-[13px] text-ink-2">{run.split}</td>
                        <td className="px-3 py-3 mono text-[13px] text-ink-2">{run.mode}</td>
                        <td className="px-3 py-3 mono text-[13px] text-ink-2 text-right tnum">
                          {run.metrics["count"] ?? "—"}
                        </td>
                        <td className="px-3 py-3">
                          <div className="flex items-center gap-2.5">
                            <Meter value={run.metrics["recall@5"] as number | null} />
                            <CI interval={run.confidence?.["recall@5"]} />
                          </div>
                          <CeilingWarning ceiling={run.integrity?.recall_ceiling} />
                        </td>
                        <td className="px-3 py-3">
                          <Meter value={run.metrics["mrr"] as number | null} width={56} />
                        </td>
                        <td className="px-3 py-3 mono text-[13px] text-ink-2 text-right tnum">
                          {formatCost(run.cost_usd)}
                        </td>
                        <td
                          className="px-3 py-3 mono text-[13px] text-ink-3 whitespace-nowrap"
                          title={
                            run.git_dirty
                              ? "Recorded from uncommitted changes: not reproducible from this commit"
                              : undefined
                          }
                        >
                          {run.git_sha ? run.git_sha.slice(0, 7) : "—"}
                          {run.git_dirty && <span className="text-fail">*</span>}
                        </td>
                        <td className="pl-3 pr-5 py-3 mono text-[13px] text-ink-3 whitespace-nowrap">
                          {formatTime(run.timestamp)}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>

            {selected.length === 2 && base && head && (
              <Comparison base={base} head={head} categories={categories} />
            )}

            {selected.length === 1 && (
              <p className="mt-5 text-ink-2">Select one more run to compare them.</p>
            )}
          </>
        )}
      </div>
    </div>
  );
}

function Comparison({
  base,
  head,
  categories,
}: {
  base: ExperimentDetail;
  head: ExperimentDetail;
  categories: string[];
}) {
  // Paired statistics come from the server, which pairs the two runs item by
  // item. A 409 means the runs are over different items and cannot pair.
  const [paired, setPaired] = useState<PairedComparison | null>(null);
  const [pairError, setPairError] = useState<string | null>(null);
  useEffect(() => {
    setPaired(null);
    setPairError(null);
    api
      .compare(base.id, head.id)
      .then(setPaired)
      .catch((exc) => setPairError(exc instanceof ApiError ? exc.message : "unavailable"));
  }, [base.id, head.id]);
  const pairedBy = Object.fromEntries((paired?.metrics ?? []).map((m) => [m.metric, m]));

  return (
    <div className="mt-12">
      <h2 className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[24px] font-extrabold text-ink tracking-[-0.01em]">
        <span className="mono text-[20px]">{base.config_name}</span>
        <svg width="22" height="14" viewBox="0 0 22 14" aria-label="compared with">
          <path d="M1 7h18M14 2l5 5-5 5" fill="none" stroke="var(--color-ink-3)" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
        <span className="mono text-[20px]">{head.config_name}</span>
        <span className="text-[15px] font-normal text-ink-2 tracking-normal">
          {head.dataset_size} questions · {head.split} split
        </span>
      </h2>

      {(base.dataset_version !== head.dataset_version || base.split !== head.split) && (
        <div className="mt-4 max-w-[80ch]">
          <ErrorNote
            message={`These runs scored different questions (${base.dataset_version}/${base.split} vs ${head.dataset_version}/${head.split}), so the differences below compare two question sets, not two configs.`}
          />
        </div>
      )}

      <div className="mt-6 grid grid-cols-1 xl:grid-cols-[minmax(0,1.35fr)_minmax(0,1fr)] gap-8">
        {/* Headline metrics */}
        <section aria-label="Headline metrics" className="sheet overflow-x-auto">
          <table className="w-full min-w-[600px] text-[15px]">
            <thead>
              <tr className="text-left text-[13px] text-ink-3 border-b border-rule">
                <th className="px-5 py-3 font-normal">Metric</th>
                <th className="px-3 py-3 font-normal text-right w-24 mono">{base.config_name}</th>
                <th className="px-3 py-3 font-normal text-right w-24 mono">{head.config_name}</th>
                <th className="px-3 py-3 font-normal text-right w-20">Difference</th>
                <th className="px-3 py-3 font-normal text-right w-32">Paired 95% interval</th>
                <th className="pl-3 pr-5 py-3 font-normal w-24">Verdict</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-rule">
              {HEADLINE_METRICS.map(([key, label]) => {
                const before = number(base.metrics[key]);
                const after = number(head.metrics[key]);
                if (before == null && after == null) return null;
                const pair = pairedBy[key];
                return (
                  <tr key={key}>
                    <td className="px-5 py-2.5 text-ink">{label}</td>
                    <td className="px-3 py-2.5 mono text-[13px] text-ink-2 text-right tnum">
                      {before?.toFixed(3) ?? "—"}
                    </td>
                    <td className="px-3 py-2.5 mono text-[13px] text-ink text-right tnum">
                      {after?.toFixed(3) ?? "—"}
                    </td>
                    <td className="px-3 py-2.5 text-right">
                      <Delta
                        value={before != null && after != null ? after - before : null}
                        significant={!!pair?.distinguishable}
                      />
                    </td>
                    <td className="px-3 py-2.5 mono text-[13px] text-ink-2 text-right tnum whitespace-nowrap">
                      {pair ? `[${signed(pair.low)}, ${signed(pair.high)}]` : "—"}
                    </td>
                    <td className="pl-3 pr-5 py-2.5">
                      {pair ? (
                        <span
                          title={`p = ${pair.p_value.toFixed(3)} · ${pair.wins} question(s) better, ${pair.losses} worse, of ${pair.n}`}
                        >
                          <Verdict real={pair.distinguishable} />
                        </span>
                      ) : (
                        <span className="text-ink-3">—</span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <p className="px-5 py-3 border-t border-rule text-[13px] text-ink-2">
            {paired
              ? "Paired by question: a bootstrap interval on each question's difference, and an exact sign-flip test (hover a verdict for p). Noise means the interval includes zero."
              : pairError
                ? `No paired statistics: ${pairError}`
                : "Computing paired statistics…"}
          </p>
        </section>

        <div className="space-y-8">
          {/* Per category */}
          <section aria-label="Recall by category" className="sheet overflow-x-auto">
            <table className="w-full min-w-[420px] text-[15px]">
              <thead>
                <tr className="text-left text-[13px] text-ink-3 border-b border-rule">
                  <th className="px-5 py-3 font-normal">Recall@5 by category (unpaired)</th>
                  <th className="px-3 py-3 font-normal text-right w-20">Before</th>
                  <th className="px-3 py-3 font-normal text-right w-20">After</th>
                  <th className="pl-3 pr-5 py-3 font-normal text-right w-20">Difference</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-rule">
                {categories.map((category) => {
                  const before = categoryRecall(base, category);
                  const after = categoryRecall(head, category);
                  return (
                    <tr key={category}>
                      <td className="px-5 py-2.5">
                        <span className="mono text-[13px] text-ink">{category}</span>
                        <span className="ml-2 text-[13px] text-ink-3">
                          n={String(head.metrics_by_category[category]?.["count"] ?? "—")}
                        </span>
                      </td>
                      <td className="px-3 py-2.5 mono text-[13px] text-ink-2 text-right tnum">
                        {before?.toFixed(3) ?? "—"}
                      </td>
                      <td className="px-3 py-2.5 mono text-[13px] text-ink text-right tnum">
                        {after?.toFixed(3) ?? "—"}
                      </td>
                      <td className="pl-3 pr-5 py-2.5 text-right">
                        <Delta value={before != null && after != null ? after - before : null} />
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </section>

          <dl className="grid grid-cols-2 gap-x-6 gap-y-4 text-[14px]">
            <div>
              <dt className="text-[13px] text-ink-3">p95 latency</dt>
              <dd
                className="mono text-ink tnum"
                title={
                  [base, head].some(servedFromCache)
                    ? "† mostly replayed from the LLM cache: this measures disk reads, not a cold query"
                    : undefined
                }
              >
                {base.latency.p95_ms.toFixed(0)} ms{servedFromCache(base) && "†"} →{" "}
                {head.latency.p95_ms.toFixed(0)} ms{servedFromCache(head) && "†"}
              </dd>
            </div>
            <div>
              <dt className="text-[13px] text-ink-3">Cost</dt>
              <dd className="mono text-ink tnum">
                {formatCost(base.cost.cost_usd)} → {formatCost(head.cost.cost_usd)}
              </dd>
            </div>
            <div className="col-span-2">
              <dt className="text-[13px] text-ink-3">Chunk set</dt>
              <dd className="mono text-ink break-all">{head.config.chunker_name}</dd>
            </div>
            {[base, head].some((d) => (d.integrity?.recall_ceiling ?? 1) < 1) && (
              <div className="col-span-2">
                <dt className="text-[13px] text-fail">Recall ceiling below 1: gold not matchable</dt>
                <dd className="mono text-fail tnum">
                  {base.integrity?.recall_ceiling?.toFixed(3) ?? "—"} →{" "}
                  {head.integrity?.recall_ceiling?.toFixed(3) ?? "—"}
                </dd>
              </div>
            )}
          </dl>
        </div>
      </div>

      {/* Attribution */}
      <section aria-label="Where the failures are" className="mt-10">
        <h3 className="text-[20px] font-extrabold text-ink">Where the failures are</h3>
        <p className="mt-1 text-[15px] text-ink-2 max-w-[62ch]">
          Each failed question is attributed to the stage that lost it, so a fix goes to the right place.
        </p>
        <div className="mt-4 grid grid-cols-1 md:grid-cols-2 gap-6">
          <AttributionBar title={base.config_name} attribution={base.attribution} total={base.dataset_size} />
          <AttributionBar title={head.config_name} attribution={head.attribution} total={head.dataset_size} />
        </div>
      </section>
    </div>
  );
}

/** Real: the interval excludes zero. Noise: it does not. */
function Verdict({ real }: { real: boolean }) {
  return real ? (
    <span className="inline-flex items-center gap-2 machine font-bold text-ink">
      <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
        <rect x="0.75" y="0.75" width="10.5" height="10.5" rx="1.5" fill="none" stroke="var(--color-ink)" strokeWidth="1.5" />
        <rect x="3" y="3" width="6" height="6" rx="0.5" fill="var(--color-ink)" />
      </svg>
      Real
    </span>
  ) : (
    <span className="inline-flex items-center gap-2 machine text-ink-3">
      <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
        <rect x="0.75" y="0.75" width="10.5" height="10.5" rx="1.5" fill="none" stroke="var(--color-rule-strong)" strokeWidth="1.5" />
      </svg>
      Noise
    </span>
  );
}

function AttributionBar({
  title,
  attribution,
  total,
}: {
  title: string;
  attribution: Record<string, number>;
  total: number;
}) {
  const entries = Object.entries(attribution).sort((a, b) => b[1] - a[1]);
  const failures = entries.reduce((sum, [, count]) => sum + count, 0);
  const passed = Math.max(0, total - failures);

  return (
    <div className="sheet px-5 py-4">
      <div className="flex items-baseline justify-between mb-3">
        <span className="mono text-[14px] font-bold text-ink">{title}</span>
        <span className="text-[14px] text-ink-2">
          <span className="mono tnum text-ink">{passed}</span> of <span className="mono tnum">{total}</span> passed
        </span>
      </div>

      <div className="flex h-3 rounded-full overflow-hidden bg-well gap-px">
        {passed > 0 && (
          <span title={`${passed} passed`} style={{ width: `${(passed / total) * 100}%`, backgroundColor: "var(--color-ink-2)" }} />
        )}
        {entries.map(([failure, count]) => (
          <span
            key={failure}
            title={`${ATTRIBUTION_LABELS[failure] ?? failure}: ${count}`}
            style={{ width: `${(count / total) * 100}%`, backgroundColor: ATTRIBUTION_COLORS[failure] ?? "#666C78" }}
          />
        ))}
      </div>

      <ul className="mt-3 space-y-1.5">
        {entries.map(([failure, count]) => (
          <li key={failure} className="flex items-center gap-2.5 text-[14px]">
            <span className="size-2.5 rounded-[2px] shrink-0" style={{ backgroundColor: ATTRIBUTION_COLORS[failure] ?? "#666C78" }} />
            <span className="text-ink-2 flex-1">{ATTRIBUTION_LABELS[failure] ?? failure}</span>
            <span className="mono text-ink tnum">{count}</span>
          </li>
        ))}
        {entries.length === 0 && <li className="text-[14px] text-ink-3">No failures recorded.</li>}
      </ul>
    </div>
  );
}

function number(value: unknown): number | null {
  return typeof value === "number" && !Number.isNaN(value) ? value : null;
}

function signed(value: number): string {
  return `${value >= 0 ? "+" : "−"}${Math.abs(value).toFixed(2)}`;
}

/**
 * Recall@5 for one category, or null when no item in it has gold evidence:
 * records store 0.0 for a mean over nothing, which is not a score.
 */
function categoryRecall(run: ExperimentDetail, category: string): number | null {
  const values = run.metrics_by_category[category];
  if (!values || !values["count"]) return null;
  return number(values["recall@5"]);
}

/**
 * A run that mostly replayed the LLM cache measured the cache, not the model.
 * Same rule as the README table's dagger (scripts/generate_results_table.py).
 */
function servedFromCache(run: ExperimentDetail): boolean {
  const { calls, cached_calls } = run.cost;
  return calls > 0 && cached_calls / calls >= 0.5;
}

/** A run's own 95% interval, shown next to its point estimate. */
function CI({ interval }: { interval: Interval | undefined }) {
  if (!interval) return null;
  return (
    <span className="mono text-[12px] text-ink-3 tnum whitespace-nowrap">
      [{interval.low.toFixed(2)}, {interval.high.toFixed(2)}]
    </span>
  );
}

/**
 * Some gold quote could not match any chunk, so recall had a ceiling below 1
 * before retrieval even ran. This is how the decode bug would have shown up.
 */
function CeilingWarning({ ceiling }: { ceiling: number | undefined }) {
  if (ceiling == null || ceiling >= 1) return null;
  return (
    <div className="mt-1 text-[12px] text-fail">Recall capped at {ceiling.toFixed(3)}: gold not matchable</div>
  );
}
