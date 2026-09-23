"use client";

/**
 * Trace detail — the waterfall, and the thing it exists for.
 *
 * The memorable element here is the **rank trail**: for every chunk any stage
 * saw, the ranks it held at each stage, in order. A row reading
 * `dense 2 → rrf 4 → rerank 17` is a ranking miss you can point at, which is
 * the difference between "recall dropped" and knowing which stage to fix.
 */

import { use, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { ApiError, api, type Span, type TraceDetail } from "@/lib/api";
import { AnswerText } from "@/components/answer-text";
import {
  ErrorNote,
  Field,
  Spinner,
  cleanHeading,
  formatCost,
  formatMs,
  formatTime,
} from "@/components/primitives";

interface ChunkRow {
  chunk_id: number;
  source_path: string;
  version: string;
  heading_path: string;
  ranks: Record<string, number>;
  scores: Record<string, number>;
}

/** Stage order as the pipeline runs it, for reading a rank trail left to right. */
const STAGE_ORDER = ["dense", "lexical", "rrf", "fusion", "rerank"];

interface Timeline {
  /** Milliseconds from the first span's start to the last span's end. */
  totalMs: number;
  /** Span id -> [offset, width] as percentages of `totalMs`. */
  bars: Record<string, [number, number]>;
}

/**
 * Place each span on one shared time axis.
 *
 * Sizing bars by duration alone draws a bar chart, not a waterfall: it hides
 * *when* a stage ran, so sequential stages and overlapping ones look the same,
 * and the gaps between stages -- time spent outside any span -- vanish.
 */
function timeline(spans: Span[]): Timeline {
  const starts = spans.map((s) => Date.parse(s.started_at));
  const origin = Math.min(...starts);
  const ends = spans.map((s, i) => starts[i] + s.duration_ms);
  const totalMs = Math.max(1, Math.max(...ends) - origin);
  const bars: Record<string, [number, number]> = {};
  spans.forEach((span, i) => {
    const offset = ((starts[i] - origin) / totalMs) * 100;
    const width = Math.max(0.8, (span.duration_ms / totalMs) * 100);
    bars[span.span_id] = [Math.min(offset, 100 - width), width];
  });
  return { totalMs, bars };
}

function collectChunks(spans: Span[]): ChunkRow[] {
  const byId = new Map<number, ChunkRow>();

  for (const span of spans) {
    const top = (span.output?.top ?? []) as Array<Record<string, unknown>>;
    if (!Array.isArray(top)) continue;

    for (const entry of top) {
      const id = Number(entry.chunk_id);
      if (!Number.isFinite(id)) continue;
      const existing = byId.get(id) ?? {
        chunk_id: id,
        source_path: String(entry.source_path ?? ""),
        version: String(entry.version ?? ""),
        heading_path: String(entry.heading_path ?? ""),
        ranks: {},
        scores: {},
      };
      Object.assign(existing.ranks, (entry.ranks ?? {}) as Record<string, number>);
      Object.assign(existing.scores, (entry.scores ?? {}) as Record<string, number>);
      byId.set(id, existing);
    }
  }

  // Order by the last stage that ranked them, so the final context is on top.
  return [...byId.values()].sort((a, b) => {
    const rank = (row: ChunkRow) => {
      for (const stage of [...STAGE_ORDER].reverse()) {
        if (row.ranks[stage] != null) return row.ranks[stage];
      }
      return 9999;
    };
    return rank(a) - rank(b);
  });
}

export default function TraceDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [trace, setTrace] = useState<TraceDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [openSpan, setOpenSpan] = useState<string | null>(null);

  useEffect(() => {
    api
      .trace(id)
      .then(setTrace)
      .catch((exc) =>
        setError(exc instanceof ApiError ? exc.message : "Could not load this trace."),
      );
  }, [id]);

  const axis = useMemo(
    () => (trace && trace.spans.length ? timeline(trace.spans) : { totalMs: 1, bars: {} }),
    [trace],
  );
  const chunks = useMemo(() => (trace ? collectChunks(trace.spans) : []), [trace]);

  if (error) {
    return (
      <div className="mx-auto max-w-[1440px] px-4 sm:px-6 py-8">
        <ErrorNote message={error} />
        <Link href="/traces" className="mt-4 inline-block text-brass hover:underline">
          Back to traces
        </Link>
      </div>
    );
  }

  if (!trace) {
    return (
      <div className="mx-auto max-w-[1440px] px-4 sm:px-6 py-8">
        <Spinner label="Loading trace" />
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-[1440px] px-4 sm:px-6 py-8">
      <Link href="/traces" className="text-sm text-mute hover:text-bright">
        ← Traces
      </Link>

      <h1 className="mt-3 text-lg text-bright font-medium">{trace.question}</h1>

      {trace.status !== "ok" && (
        <div className="mt-4">
          <ErrorNote
            message={`This query failed: ${String(trace.meta?.error ?? "error")}${
              trace.meta?.message ? ` — ${String(trace.meta.message)}` : ""
            }. The stage that raised is marked below.`}
          />
        </div>
      )}

      <div className="mt-4 flex flex-wrap gap-x-8 gap-y-3 rule pt-4">
        <Field label="Pipeline">{trace.config_name}</Field>
        <Field label="Answered from">
          {trace.version_used ? `v${trace.version_used}` : "—"}
        </Field>
        <Field label="Total">{formatMs(trace.latency_ms)}</Field>
        <Field label="Cost">{formatCost(trace.cost_usd)}</Field>
        <Field label="Spans">{trace.spans.length}</Field>
        <Field label="When">{formatTime(trace.started_at)}</Field>
      </div>

      <div className="mt-8 grid grid-cols-1 xl:grid-cols-[minmax(0,1fr)_minmax(0,460px)] gap-8">
        {/* ---------------- Waterfall ---------------- */}
        <section>
          <div className="flex items-baseline justify-between mb-3">
            <h2 className="text-sm text-mute">Stages</h2>
            <span className="mono text-[11px] text-dim tnum">
              0 — {formatMs(axis.totalMs)}
            </span>
          </div>
          <div className="border border-line rounded-sm divide-y divide-line">
            {trace.spans.map((span) => {
              const [offset, width] = axis.bars[span.span_id] ?? [0, 1];
              const isOpen = openSpan === span.span_id;
              const failed = span.status !== "ok";
              const count = Number(span.output?.count ?? NaN);

              return (
                <div key={span.span_id}>
                  <button
                    onClick={() => setOpenSpan(isOpen ? null : span.span_id)}
                    aria-expanded={isOpen}
                    className="w-full text-left px-3 py-2 hover:bg-panel/60 transition-colors"
                  >
                    <div className="flex items-center gap-3">
                      <span
                        className="mono text-xs w-[9.5rem] shrink-0"
                        style={{ color: failed ? "var(--color-alarm)" : "var(--color-text)" }}
                      >
                        {span.name}
                      </span>

                      <span className="flex-1 h-[10px] bg-line/50 rounded-[1px] relative overflow-hidden">
                        <span
                          className="absolute inset-y-0 rounded-[1px]"
                          style={{
                            left: `${offset}%`,
                            width: `${width}%`,
                            backgroundColor: failed
                              ? "var(--color-alarm)"
                              : "var(--color-brass)",
                            opacity: failed ? 1 : 0.75,
                          }}
                        />
                      </span>

                      {Number.isFinite(count) && (
                        <span className="mono text-[11px] text-dim tnum w-16 text-right">
                          {count} hits
                        </span>
                      )}
                      <span className="mono text-xs text-text tnum w-16 text-right">
                        {formatMs(span.duration_ms)}
                      </span>
                    </div>
                  </button>

                  {isOpen && (
                    <div className="px-3 pb-3 bg-panel/40">
                      <div className="grid sm:grid-cols-2 gap-3 mt-1">
                        <SpanBlock title="Input" data={span.input} />
                        <SpanBlock title="Output" data={stripTop(span.output)} />
                      </div>
                      {Array.isArray(span.output?.top) && (
                        <div className="mt-3">
                          <div className="text-[11px] text-dim mb-1">
                            Top results from this stage
                          </div>
                          <ol className="space-y-1">
                            {(span.output.top as Array<Record<string, unknown>>)
                              .slice(0, 10)
                              .map((entry, index) => (
                                <li
                                  key={index}
                                  className="mono text-[11px] text-mute flex gap-2"
                                >
                                  <span className="text-dim w-5 text-right tnum">
                                    {index + 1}
                                  </span>
                                  <span className="flex-1 truncate text-text/80">
                                    {String(entry.source_path ?? "")}
                                  </span>
                                  <span className="text-dim truncate max-w-[14rem]">
                                    {cleanHeading(String(entry.heading_path ?? ""))}
                                  </span>
                                </li>
                              ))}
                          </ol>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </div>

          {trace.answer && (
            <>
              <h2 className="text-sm text-mute mt-8 mb-3">Answer</h2>
              <div className="border border-line rounded-sm px-4 py-3 text-[14px] leading-relaxed whitespace-pre-wrap wrap-anywhere">
                <AnswerText
                  text={trace.answer}
                  citation={(marker, key) => (
                    <span key={key} className="mono text-[11px] align-super px-0.5 text-brass">
                      {marker}
                    </span>
                  )}
                />
              </div>
            </>
          )}
        </section>

        {/* ---------------- Rank trail ---------------- */}
        <aside>
          <h2 className="text-sm text-mute mb-1">Rank trail</h2>
          <p className="text-xs text-dim mb-3 max-w-prose">
            Where each chunk placed at every stage. A number that climbs from left
            to right is a chunk the pipeline demoted — a ranking miss you can point
            at.
          </p>

          {chunks.length === 0 ? (
            <p className="text-sm text-dim">This trace recorded no ranked results.</p>
          ) : (
            <div className="border border-line rounded-sm divide-y divide-line">
              {chunks.slice(0, 25).map((chunk) => {
                const stages = STAGE_ORDER.filter((s) => chunk.ranks[s] != null);
                const first = stages.length ? chunk.ranks[stages[0]] : null;
                const last = stages.length ? chunk.ranks[stages[stages.length - 1]] : null;
                const demoted = first != null && last != null && last > first + 2;

                return (
                  <div key={chunk.chunk_id} className="px-3 py-2">
                    <div className="mono text-[11px] text-text/85 truncate">
                      {chunk.source_path}
                      <span className="text-dim"> v{chunk.version}</span>
                    </div>
                    {chunk.heading_path && (
                      <div className="mono text-[10px] text-dim truncate">
                        {cleanHeading(chunk.heading_path)}
                      </div>
                    )}
                    <div className="mt-1.5 flex items-center gap-1.5 flex-wrap">
                      {stages.map((stage, index) => (
                        <span key={stage} className="flex items-center gap-1.5">
                          {index > 0 && <span className="text-dim text-[10px]">→</span>}
                          <span
                            className="mono text-[10px] px-1.5 py-0.5 rounded-[2px] border tnum"
                            style={{
                              borderColor:
                                demoted && index === stages.length - 1
                                  ? "var(--color-alarm)"
                                  : "var(--color-line-bright)",
                              color:
                                demoted && index === stages.length - 1
                                  ? "var(--color-alarm)"
                                  : "var(--color-mute)",
                            }}
                          >
                            {stage} {chunk.ranks[stage]}
                          </span>
                        </span>
                      ))}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </aside>
      </div>
    </div>
  );
}

/** `top` is rendered separately; showing it twice would bury everything else. */
function stripTop(output: Record<string, unknown>): Record<string, unknown> {
  const { top, ...rest } = output ?? {};
  void top;
  return rest;
}

function SpanBlock({ title, data }: { title: string; data: Record<string, unknown> }) {
  const empty = !data || Object.keys(data).length === 0;
  return (
    <div>
      <div className="text-[11px] text-dim mb-1">{title}</div>
      <pre className="mono text-[11px] text-text/75 bg-ink border border-line rounded-sm p-2 overflow-x-auto max-h-48">
        {empty ? "—" : JSON.stringify(data, null, 2)}
      </pre>
    </div>
  );
}
