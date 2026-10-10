"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { ApiError, api, type StageStats, type TraceStats, type TraceSummary } from "@/lib/api";
import {
  ABSTAIN_LABEL,
  EmptyState,
  ErrorNote,
  Field,
  Spinner,
  formatCost,
  formatMs,
  formatTime,
} from "@/components/primitives";

/** The stats window: a week, matching the API's default. */
const STATS_HOURS = 168;

/** Stage names as a reader says them; unknown names fall through as written. */
const STAGE_LABEL: Record<string, string> = {
  version_detection: "Version detection",
  decomposition: "Decomposition",
  query_rewrite: "Query rewrite",
  dense: "Dense search",
  lexical: "Lexical search",
  fusion: "Fusion",
  rerank: "Rerank",
  generation: "Generation",
  verification: "Verification",
  regeneration: "Regeneration",
  conflict_detection: "Conflict detection",
  llm: "All model calls",
};

function percent(part: number, whole: number): string {
  if (!whole) return "—";
  const value = (part / whole) * 100;
  return value > 0 && value < 1 ? "<1%" : `${Math.round(value)}%`;
}

/**
 * Every stage across the week's traces: where the time goes at the median and
 * the tail, how often a stage fell back or failed, and what its model calls
 * cost. One trace shows one query; this is what shows a stage getting worse.
 */
function StageTable({ stats }: { stats: TraceStats }) {
  const { totals } = stats;
  const reasons = Object.entries(totals.abstain_reasons).sort((a, b) => b[1] - a[1]);

  return (
    <section aria-label="Stages across traces" className="mt-8">
      <h2 className="serif text-[26px] leading-tight font-medium text-ink">Every stage, this week</h2>
      <p className="mt-1 mb-4 text-[15px] text-ink-2 max-w-[66ch]">
        A stage that <em>fell back</em> kept the query alive with a default (a rewrite that failed, a verifier
        that did not answer), so the answer was not produced the way the pipeline is configured to produce it.
      </p>

      <dl className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-7 gap-x-6 gap-y-4 border-y border-rule py-4">
        <Field label="Queries">{totals.traces}</Field>
        <Field label="Failed">{percent(totals.errors, totals.traces)}</Field>
        <Field label="Declined">{percent(totals.abstained, totals.traces)}</Field>
        <Field label="Fell back somewhere">{percent(totals.degraded, totals.traces)}</Field>
        <Field label="Median time">{formatMs(totals.p50_ms)}</Field>
        <Field label="95th percentile">{formatMs(totals.p95_ms)}</Field>
        <Field label="Cost">{formatCost(totals.cost_usd)}</Field>
      </dl>

      {reasons.length > 0 && (
        <p className="mt-3 text-[15px] text-ink-2">
          Declined because:{" "}
          {reasons.map(([reason, count], index) => (
            <span key={reason}>
              {index > 0 && ", "}
              {(ABSTAIN_LABEL[reason] ?? reason).toLowerCase()} <span className="mono tnum text-ink">{count}</span>
            </span>
          ))}
          .
        </p>
      )}

      <div className="mt-4 sheet overflow-x-auto">
        <table className="w-full min-w-[760px] text-[15px]">
          <thead>
            <tr className="text-left text-[13px] text-ink-3 border-b border-rule">
              <th className="px-5 py-3 font-normal">Stage</th>
              <th className="px-3 py-3 font-normal text-right">Ran in</th>
              <th className="px-3 py-3 font-normal text-right">Median</th>
              <th className="px-3 py-3 font-normal text-right">95th pct.</th>
              <th className="px-3 py-3 font-normal text-right">Fell back</th>
              <th className="px-3 py-3 font-normal text-right">Failed</th>
              <th className="px-3 py-3 font-normal text-right">Model calls</th>
              <th className="px-5 py-3 font-normal text-right">Cost</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-rule">
            {stats.stages.map((stage) => (
              <StageRow key={stage.name} stage={stage} traces={totals.traces} />
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function StageRow({ stage, traces }: { stage: StageStats; traces: number }) {
  const isCalls = stage.name === "llm";
  const flag = (count: number, colour: string) => (
    <span className="mono text-[13px] tnum" style={{ color: count ? colour : "var(--color-ink-3)" }}>
      {count ? percent(count, stage.runs) : "—"}
    </span>
  );

  return (
    <tr className={isCalls ? "bg-paper/70" : undefined}>
      <td className="px-5 py-3 text-ink">
        {STAGE_LABEL[stage.name] ?? stage.name}
        <span className="block mono text-[12px] text-ink-3">{stage.name}</span>
      </td>
      <td className="px-3 py-3 text-right mono text-[13px] text-ink-2 tnum whitespace-nowrap">
        {isCalls ? `${stage.runs} calls` : percent(stage.traces, traces)}
      </td>
      <td className="px-3 py-3 text-right mono text-[13px] text-ink tnum whitespace-nowrap">{formatMs(stage.p50_ms)}</td>
      <td className="px-3 py-3 text-right mono text-[13px] text-ink tnum whitespace-nowrap">{formatMs(stage.p95_ms)}</td>
      <td className="px-3 py-3 text-right">{flag(stage.degraded, "var(--color-partial)")}</td>
      <td className="px-3 py-3 text-right">{flag(stage.errors, "var(--color-fail)")}</td>
      <td className="px-3 py-3 text-right mono text-[13px] text-ink-2 tnum whitespace-nowrap">
        {stage.llm_calls ? (
          <>
            {stage.llm_calls}
            {stage.llm_cached > 0 && <span className="text-ink-3"> · {percent(stage.llm_cached, stage.llm_calls)} cached</span>}
          </>
        ) : (
          "—"
        )}
      </td>
      <td className="px-5 py-3 text-right mono text-[13px] text-ink-2 tnum">
        {stage.llm_calls ? formatCost(stage.cost_usd) : "—"}
      </td>
    </tr>
  );
}

/** How a run ended, in the same fill vocabulary as a claim's verdict. */
function Outcome({ trace }: { trace: TraceSummary }) {
  if (trace.status !== "ok") {
    return (
      <span className="inline-flex items-center gap-2 machine font-bold" style={{ color: "var(--color-fail)" }}>
        <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
          <rect x="0.75" y="0.75" width="10.5" height="10.5" rx="1.5" fill="var(--color-fail)" />
          <path d="M4 4l4 4M8 4l-4 4" stroke="var(--color-sheet)" strokeWidth="1.5" strokeLinecap="round" />
        </svg>
        Failed
      </span>
    );
  }
  if (trace.abstained) {
    return (
      <span className="inline-flex items-center gap-2 machine text-ink-2">
        <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
          <rect x="0.75" y="0.75" width="10.5" height="10.5" rx="1.5" fill="none" stroke="var(--color-ink-3)" strokeWidth="1.5" />
        </svg>
        Declined
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-2 machine text-ink">
      <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
        <rect x="0.75" y="0.75" width="10.5" height="10.5" rx="1.5" fill="none" stroke="var(--color-ink)" strokeWidth="1.5" />
        <rect x="3" y="3" width="6" height="6" rx="0.5" fill="var(--color-ink)" />
      </svg>
      Answered
    </span>
  );
}

export default function TracesPage() {
  const [traces, setTraces] = useState<TraceSummary[] | null>(null);
  const [stats, setStats] = useState<TraceStats | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .traces(100)
      .then(setTraces)
      .catch((exc) => setError(exc instanceof ApiError ? exc.message : "Could not load traces."));
    // Secondary: the list is the page, so a failed summary just stays hidden.
    api
      .traceStats(STATS_HOURS)
      .then(setStats)
      .catch(() => setStats(null));
  }, []);

  return (
    <div className="mx-auto max-w-[1320px] px-4 sm:px-8 pt-10 sm:pt-12">
      <h1 className="serif font-medium text-[40px] sm:text-[48px] leading-[1.05] tracking-[-0.025em] text-ink">Traces</h1>
      <p className="mt-2 text-[17px] text-ink-2 max-w-[64ch]">
        Every question asked, failed ones included, with a span for each stage it passed through. Open one
        to see where the time went, and where each excerpt was ranked, kept, or dropped.
      </p>

      {stats && stats.totals.traces > 0 && <StageTable stats={stats} />}

      {traces && traces.length > 0 && (
        <h2 className="mt-12 serif text-[26px] leading-tight font-medium text-ink">Recent questions</h2>
      )}

      <div className="mt-4">
        {error && <ErrorNote message={error} />}
        {!error && traces === null && <Spinner label="Loading traces" />}

        {traces?.length === 0 && (
          <EmptyState title="No traces yet">
            Ask a question on the{" "}
            <Link href="/" className="font-bold text-ink underline">
              Ask
            </Link>{" "}
            page and its trace appears here.
          </EmptyState>
        )}

        {traces && traces.length > 0 && (
          <div className="sheet overflow-x-auto">
            <table className="w-full min-w-[820px] text-[15px]">
              <thead>
                <tr className="text-left text-[13px] text-ink-3 border-b border-rule">
                  <th className="px-5 py-3 font-normal w-36">Outcome</th>
                  <th className="px-3 py-3 font-normal">Question</th>
                  <th className="px-3 py-3 font-normal w-28">Pipeline</th>
                  <th className="px-3 py-3 font-normal w-20">Release</th>
                  <th className="px-3 py-3 font-normal w-24 text-right">Time</th>
                  <th className="px-3 py-3 font-normal w-20 text-right">Cost</th>
                  <th className="px-5 py-3 font-normal w-40">When</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-rule">
                {traces.map((trace) => (
                  <tr key={trace.trace_id} className="group hover:bg-paper/70 transition-colors">
                    <td className="px-5 py-3.5 align-top whitespace-nowrap">
                      <Outcome trace={trace} />
                    </td>
                    <td className="px-3 py-3.5 align-top">
                      <Link
                        href={`/traces/${trace.trace_id}`}
                        className="text-ink font-bold group-hover:underline decoration-1"
                      >
                        {trace.question}
                      </Link>
                      {trace.feedback === false && (
                        <span className="block mt-0.5 text-[13px] text-fail">Marked unhelpful</span>
                      )}
                      {trace.abstained && trace.abstain_reason && (
                        <span className="block mt-0.5 text-[13px] text-ink-2">
                          {ABSTAIN_LABEL[trace.abstain_reason] ?? trace.abstain_reason}
                        </span>
                      )}
                      {trace.degraded_stages && trace.degraded_stages.length > 0 && (
                        <span className="block mt-0.5 text-[13px]" style={{ color: "var(--color-partial)" }}>
                          Fell back in {trace.degraded_stages.map((s) => STAGE_LABEL[s]?.toLowerCase() ?? s).join(", ")}
                        </span>
                      )}
                    </td>
                    <td className="px-3 py-3.5 align-top mono text-[13px] text-ink-2">{trace.config_name}</td>
                    <td className="px-3 py-3.5 align-top mono text-[13px] text-ink-2">{trace.version_used ?? "—"}</td>
                    <td className="px-3 py-3.5 align-top mono text-[13px] text-ink text-right tnum whitespace-nowrap">
                      {formatMs(trace.latency_ms)}
                    </td>
                    <td className="px-3 py-3.5 align-top mono text-[13px] text-ink-2 text-right tnum">
                      {formatCost(trace.cost_usd)}
                    </td>
                    <td className="px-5 py-3.5 align-top mono text-[13px] text-ink-3 whitespace-nowrap">
                      {formatTime(trace.started_at)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
