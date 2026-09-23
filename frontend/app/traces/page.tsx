"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { ApiError, api, type TraceSummary } from "@/lib/api";
import { EmptyState, ErrorNote, Spinner, formatCost, formatMs, formatTime } from "@/components/primitives";

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
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .traces(100)
      .then(setTraces)
      .catch((exc) => setError(exc instanceof ApiError ? exc.message : "Could not load traces."));
  }, []);

  return (
    <div className="mx-auto max-w-[1320px] px-4 sm:px-8 pt-10 sm:pt-12">
      <h1 className="text-[34px] leading-tight font-extrabold tracking-[-0.02em] text-ink">Traces</h1>
      <p className="mt-2 text-[17px] text-ink-2 max-w-[64ch]">
        Every question asked, failed ones included, with a span for each stage it passed through. Open one
        to see where the time went, and where each excerpt was ranked, kept, or dropped.
      </p>

      <div className="mt-8">
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
