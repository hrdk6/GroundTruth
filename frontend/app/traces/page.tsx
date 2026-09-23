"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { ApiError, api, type TraceSummary } from "@/lib/api";
import { EmptyState, ErrorNote, Spinner, formatCost, formatMs, formatTime } from "@/components/primitives";

export default function TracesPage() {
  const [traces, setTraces] = useState<TraceSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .traces(100)
      .then(setTraces)
      .catch((exc) =>
        setError(exc instanceof ApiError ? exc.message : "Could not load traces."),
      );
  }, []);

  return (
    <div className="mx-auto max-w-[1440px] px-4 sm:px-6 py-8">
      <h1 className="text-lg text-bright font-medium">Traces</h1>
      <p className="mt-1 text-sm text-mute max-w-prose">
        Every query records a span per stage. Open one to see where a chunk
        entered the candidate set and where it was dropped.
      </p>

      <div className="mt-6">
        {error && <ErrorNote message={error} />}
        {!error && traces === null && <Spinner label="Loading traces" />}

        {traces?.length === 0 && (
          <EmptyState title="No traces yet">
            Ask a question on the <Link href="/" className="text-brass hover:underline">Ask</Link>{" "}
            page and it will appear here.
          </EmptyState>
        )}

        {traces && traces.length > 0 && (
          <div className="border border-line rounded-sm overflow-x-auto">
            <table className="w-full text-sm min-w-[760px]">
              <thead>
                <tr className="bg-panel text-left text-[11px] text-dim">
                  <th className="px-3 py-2 font-medium">Question</th>
                  <th className="px-3 py-2 font-medium w-28">Pipeline</th>
                  <th className="px-3 py-2 font-medium w-16">Ver</th>
                  <th className="px-3 py-2 font-medium w-16 text-right">Spans</th>
                  <th className="px-3 py-2 font-medium w-20 text-right">Latency</th>
                  <th className="px-3 py-2 font-medium w-20 text-right">Cost</th>
                  <th className="px-3 py-2 font-medium w-36">When</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {traces.map((trace) => (
                  <tr key={trace.trace_id} className="hover:bg-panel/60 transition-colors">
                    <td className="px-3 py-2">
                      <Link
                        href={`/traces/${trace.trace_id}`}
                        className="text-text hover:text-brass"
                      >
                        {trace.question}
                      </Link>
                      {trace.status !== "ok" && (
                        <span
                          className="ml-2 mono text-[10px] px-1 rounded-[2px] border"
                          style={{ color: "var(--color-alarm)", borderColor: "var(--color-alarm)" }}
                        >
                          failed
                        </span>
                      )}
                      {trace.abstained && (
                        <span className="ml-2 mono text-[10px] text-brass border border-brass-dim px-1 rounded-[2px]">
                          abstained
                        </span>
                      )}
                      {trace.feedback === false && (
                        <span
                          className="ml-2 mono text-[10px] px-1 rounded-[2px] border"
                          style={{ color: "var(--color-alarm)", borderColor: "var(--color-alarm)" }}
                        >
                          marked unhelpful
                        </span>
                      )}
                    </td>
                    <td className="px-3 py-2 mono text-xs text-mute">{trace.config_name}</td>
                    <td className="px-3 py-2 mono text-xs text-mute">
                      {trace.version_used ?? "—"}
                    </td>
                    <td className="px-3 py-2 mono text-xs text-mute text-right tnum">
                      {trace.span_count}
                    </td>
                    <td className="px-3 py-2 mono text-xs text-text text-right tnum">
                      {formatMs(trace.latency_ms)}
                    </td>
                    <td className="px-3 py-2 mono text-xs text-mute text-right tnum">
                      {formatCost(trace.cost_usd)}
                    </td>
                    <td className="px-3 py-2 mono text-xs text-dim">
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
