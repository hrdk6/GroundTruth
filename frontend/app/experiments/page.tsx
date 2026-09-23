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
import {
  Delta,
  EmptyState,
  ErrorNote,
  Meter,
  Spinner,
  formatCost,
  formatTime,
} from "@/components/primitives";

const HEADLINE_METRICS = [
  ["recall@5", "Recall@5"],
  ["recall@10", "Recall@10"],
  ["mrr", "MRR@10"],
  ["ndcg@10", "nDCG@10"],
  ["answer_correctness", "Correctness"],
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
  false_abstention: "Abstained with evidence",
  version_error: "Wrong version",
};

const ATTRIBUTION_COLORS: Record<string, string> = {
  retrieval_miss: "#E5645E",
  ranking_miss: "#E0B33A",
  generation_failure: "#7C8CF8",
  false_answer: "#D98B3A",
  false_abstention: "#5BC98C",
  version_error: "#C96FD0",
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
      .catch((exc) =>
        setError(exc instanceof ApiError ? exc.message : "Could not load experiments."),
      );
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
    <div className="mx-auto max-w-[1440px] px-4 sm:px-6 py-8">
      <h1 className="text-lg text-bright font-medium">Experiments</h1>
      <p className="mt-1 text-sm text-mute max-w-prose">
        Every run is a file in <span className="mono text-xs">experiments/</span>,
        recorded with its config hash, git SHA, and dataset version. Select two to
        compare.
      </p>
      <label className="mt-3 inline-flex items-center gap-2 text-xs text-mute cursor-pointer">
        <input
          type="checkbox"
          checked={showSuperseded}
          onChange={(event) => setShowSuperseded(event.target.checked)}
          className="accent-[#E0B33A]"
        />
        Show superseded pre-audit runs (history; their numbers are not results)
      </label>

      <div className="mt-6">
        {error && <ErrorNote message={error} />}
        {!error && runs === null && <Spinner label="Loading experiments" />}

        {runs?.length === 0 && (
          <EmptyState title="No experiments recorded yet">
            Run <span className="mono text-xs">make eval CONFIG=configs/baseline.yaml</span>{" "}
            to produce one. Until then there are no numbers to show, and inventing
            them would defeat the point of the project.
          </EmptyState>
        )}

        {runs && runs.length > 0 && (
          <>
            <div className="border border-line rounded-sm overflow-x-auto">
              <table className="w-full text-sm min-w-[900px]">
                <thead>
                  <tr className="bg-panel text-left text-[11px] text-dim">
                    <th className="px-3 py-2 w-10" />
                    <th className="px-3 py-2 font-medium">Config</th>
                    <th className="px-3 py-2 font-medium w-28">Dataset</th>
                    <th className="px-3 py-2 font-medium w-20">Split</th>
                    <th className="px-3 py-2 font-medium w-24">Mode</th>
                    <th className="px-3 py-2 font-medium w-12 text-right">n</th>
                    <th className="px-3 py-2 font-medium w-48">Recall@5 · 95% CI</th>
                    <th className="px-3 py-2 font-medium w-40">MRR@10</th>
                    <th className="px-3 py-2 font-medium w-20 text-right">Cost</th>
                    <th className="px-3 py-2 font-medium w-24">Commit</th>
                    <th className="px-3 py-2 font-medium w-36">When</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-line">
                  {runs.map((run) => {
                    const checked = selected.includes(run.id);
                    return (
                      <tr
                        key={run.id}
                        onClick={() => toggle(run.id)}
                        className={`cursor-pointer transition-colors ${
                          checked ? "bg-raised" : "hover:bg-panel/60"
                        }`}
                      >
                        <td className="px-3 py-2">
                          <input
                            type="checkbox"
                            checked={checked}
                            onChange={() => toggle(run.id)}
                            onClick={(event) => event.stopPropagation()}
                            aria-label={`Compare ${run.config_name}`}
                            className="accent-[#E0B33A]"
                          />
                        </td>
                        <td className="px-3 py-2 mono text-xs text-text">
                          {run.config_name}
                          {run.superseded && (
                            <span className="ml-2 text-[10px] text-alarm">superseded</span>
                          )}
                        </td>
                        <td className="px-3 py-2 mono text-xs text-mute">
                          {run.dataset_version ?? "—"}
                        </td>
                        <td className="px-3 py-2 mono text-xs text-mute">{run.split}</td>
                        <td className="px-3 py-2 mono text-xs text-mute">{run.mode}</td>
                        <td className="px-3 py-2 mono text-xs text-mute text-right tnum">
                          {run.metrics["count"] ?? "—"}
                        </td>
                        <td className="px-3 py-2">
                          <div className="flex items-center gap-2">
                            <Meter value={run.metrics["recall@5"] as number | null} />
                            <CI interval={run.confidence?.["recall@5"]} />
                          </div>
                          <CeilingWarning ceiling={run.integrity?.recall_ceiling} />
                        </td>
                        <td className="px-3 py-2">
                          <Meter value={run.metrics["mrr"] as number | null} />
                        </td>
                        <td className="px-3 py-2 mono text-xs text-mute text-right tnum">
                          {formatCost(run.cost_usd)}
                        </td>
                        <td
                          className="px-3 py-2 mono text-xs text-dim whitespace-nowrap"
                          title={
                            run.git_dirty
                              ? "Recorded from uncommitted changes: not reproducible from this SHA"
                              : undefined
                          }
                        >
                          {run.git_sha ? run.git_sha.slice(0, 7) : "—"}
                          {run.git_dirty && <span className="text-alarm">*</span>}
                        </td>
                        <td className="px-3 py-2 mono text-xs text-dim whitespace-nowrap">
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
              <p className="mt-4 text-sm text-mute">
                Select one more run to see the deltas.
              </p>
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
    <div className="mt-8">
      <div className="rule-ticked mb-5" />
      <h2 className="text-sm text-mute mb-4">
        <span className="mono text-text">{base.config_name}</span>
        <span className="mx-2 text-dim">→</span>
        <span className="mono text-brass">{head.config_name}</span>
        <span className="ml-3 text-dim">
          {head.dataset_size} items · {head.split}
        </span>
      </h2>

      {(base.dataset_version !== head.dataset_version || base.split !== head.split) && (
        <div
          role="alert"
          className="mb-5 border-l-2 px-4 py-3 text-sm bg-panel"
          style={{ borderColor: "var(--color-alarm)" }}
        >
          These runs scored different questions (
          <span className="mono text-xs">
            {base.dataset_version}/{base.split}
          </span>{" "}
          vs{" "}
          <span className="mono text-xs">
            {head.dataset_version}/{head.split}
          </span>
          ), so the deltas below are not a comparison of the two configs — only of two
          question sets.
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-8">
        {/* Headline metrics */}
        <section>
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-[11px] text-dim">
                <th className="py-2 font-medium">Metric</th>
                <th className="py-2 font-medium w-24 text-right">{base.config_name}</th>
                <th className="py-2 font-medium w-24 text-right">{head.config_name}</th>
                <th className="py-2 font-medium w-20 text-right">Δ</th>
                <th className="py-2 font-medium w-40 text-right">paired 95% CI</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {HEADLINE_METRICS.map(([key, label]) => {
                const before = number(base.metrics[key]);
                const after = number(head.metrics[key]);
                if (before == null && after == null) return null;
                const pair = pairedBy[key];
                return (
                  <tr key={key}>
                    <td className="py-2 text-text">{label}</td>
                    <td className="py-2 mono text-xs text-mute text-right tnum">
                      {before?.toFixed(3) ?? "—"}
                    </td>
                    <td className="py-2 mono text-xs text-text text-right tnum">
                      {after?.toFixed(3) ?? "—"}
                    </td>
                    <td className="py-2 text-right">
                      <Delta
                        value={before != null && after != null ? after - before : null}
                      />
                    </td>
                    <td className="py-2 text-right mono text-[11px] tnum">
                      {pair ? (
                        <span
                          title={`p = ${pair.p_value.toFixed(3)} · ${pair.wins} item(s) better, ${pair.losses} worse, of ${pair.n}`}
                          className={pair.distinguishable ? "text-text" : "text-dim"}
                        >
                          <span className="whitespace-nowrap">
                            [{signed(pair.low)}, {signed(pair.high)}]
                          </span>
                          <span className="block sm:inline sm:ml-2">
                            {pair.distinguishable ? "real" : "noise"}
                          </span>
                        </span>
                      ) : (
                        <span className="text-dim">—</span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <p className="mt-2 text-[11px] text-dim max-w-prose">
            {paired
              ? "Paired by item: bootstrap interval on the per-item difference, and an exact sign-flip test (hover for p). \u201cnoise\u201d means the interval spans zero."
              : pairError
                ? `No paired statistics: ${pairError}`
                : "Computing paired statistics\u2026"}
          </p>

          <div className="mt-4 rule pt-3 flex flex-wrap gap-x-6 gap-y-2 text-xs text-mute mono">
            <span
              title={
                [base, head].some(servedFromCache)
                  ? "† mostly replayed from the LLM cache: this measures disk reads, not a cold query"
                  : undefined
              }
            >
              p95 {base.latency.p95_ms.toFixed(0)}ms{servedFromCache(base) && "†"} →{" "}
              {head.latency.p95_ms.toFixed(0)}ms{servedFromCache(head) && "†"}
            </span>
            <span>
              cost {formatCost(base.cost.cost_usd)} → {formatCost(head.cost.cost_usd)}
            </span>
            <span>chunker {head.config.chunker_name}</span>
            {[base, head].some((d) => (d.integrity?.recall_ceiling ?? 1) < 1) && (
              <span className="text-alarm">
                recall ceiling {base.integrity?.recall_ceiling?.toFixed(3) ?? "—"} →{" "}
                {head.integrity?.recall_ceiling?.toFixed(3) ?? "—"}
              </span>
            )}
          </div>
        </section>

        {/* Per category */}
        <section>
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-[11px] text-dim">
                <th className="py-2 font-medium">Category · recall@5 (unpaired, n per row)</th>
                <th className="py-2 font-medium w-24 text-right">Before</th>
                <th className="py-2 font-medium w-24 text-right">After</th>
                <th className="py-2 font-medium w-20 text-right">Δ</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-line">
              {categories.map((category) => {
                const before = categoryRecall(base, category);
                const after = categoryRecall(head, category);
                return (
                  <tr key={category}>
                    <td className="py-2 mono text-xs text-text">
                      {category}
                      <span className="ml-2 text-dim">
                        n={String(head.metrics_by_category[category]?.["count"] ?? "—")}
                      </span>
                    </td>
                    <td className="py-2 mono text-xs text-mute text-right tnum">
                      {before?.toFixed(3) ?? "—"}
                    </td>
                    <td className="py-2 mono text-xs text-text text-right tnum">
                      {after?.toFixed(3) ?? "—"}
                    </td>
                    <td className="py-2 text-right">
                      <Delta
                        value={before != null && after != null ? after - before : null}
                      />
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </section>
      </div>

      {/* Attribution */}
      <div className="mt-8">
        <h3 className="text-sm text-mute mb-3">Where the failures are</h3>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-6">
          <AttributionBar title={base.config_name} attribution={base.attribution} total={base.dataset_size} />
          <AttributionBar title={head.config_name} attribution={head.attribution} total={head.dataset_size} />
        </div>
      </div>
    </div>
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
    <div>
      <div className="flex items-baseline justify-between mb-2">
        <span className="mono text-xs text-text">{title}</span>
        <span className="mono text-[11px] text-dim tnum">
          {passed}/{total} passed
        </span>
      </div>

      <div className="flex h-3 rounded-[2px] overflow-hidden bg-line">
        {passed > 0 && (
          <span
            title={`${passed} passed`}
            style={{ width: `${(passed / total) * 100}%`, backgroundColor: "var(--color-line-bright)" }}
          />
        )}
        {entries.map(([failure, count]) => (
          <span
            key={failure}
            title={`${ATTRIBUTION_LABELS[failure] ?? failure}: ${count}`}
            style={{
              width: `${(count / total) * 100}%`,
              backgroundColor: ATTRIBUTION_COLORS[failure] ?? "#7C8B9E",
            }}
          />
        ))}
      </div>

      <ul className="mt-2 space-y-1">
        {entries.map(([failure, count]) => (
          <li key={failure} className="flex items-center gap-2 text-xs">
            <span
              className="w-2 h-2 rounded-[1px] shrink-0"
              style={{ backgroundColor: ATTRIBUTION_COLORS[failure] ?? "#7C8B9E" }}
            />
            <span className="text-mute flex-1">{ATTRIBUTION_LABELS[failure] ?? failure}</span>
            <span className="mono text-text tnum">{count}</span>
          </li>
        ))}
        {entries.length === 0 && <li className="text-xs text-dim">No failures recorded.</li>}
      </ul>
    </div>
  );
}

function number(value: unknown): number | null {
  return typeof value === "number" && !Number.isNaN(value) ? value : null;
}

function signed(value: number): string {
  return `${value >= 0 ? "+" : "\u2212"}${Math.abs(value).toFixed(2)}`;
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
    <span className="mono text-[11px] text-dim tnum">
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
    <div className="mt-1 mono text-[10px] text-alarm">
      recall capped at {ceiling.toFixed(3)}: gold not matchable
    </div>
  );
}
