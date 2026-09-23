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
      <div className="mx-auto max-w-[1320px] px-4 sm:px-8 pt-10">
        <ErrorNote message={error} />
        <BackLink />
      </div>
    );
  }

  if (!trace) {
    return (
      <div className="mx-auto max-w-[1320px] px-4 sm:px-8 pt-10">
        <Spinner label="Loading trace" />
      </div>
    );
  }

  const ticks = [0, 0.25, 0.5, 0.75, 1];

  return (
    <div className="mx-auto max-w-[1320px] px-4 sm:px-8 pt-4 sm:pt-6">
      <BackLink />

      <h1 className="mt-4 text-[28px] sm:text-[34px] leading-[1.2] font-extrabold tracking-[-0.02em] text-ink max-w-[34ch] wrap-break-word">
        {trace.question}
      </h1>

      {trace.status !== "ok" && (
        <div className="mt-5 max-w-[80ch]">
          <ErrorNote
            message={`This query failed: ${String(trace.meta?.error ?? "error")}${
              trace.meta?.message ? ` — ${String(trace.meta.message)}` : ""
            }. The stage that raised is marked in red below.`}
          />
        </div>
      )}

      <dl className="mt-5 grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-x-6 gap-y-4 border-y border-rule py-4">
        <Field label="Pipeline">{trace.config_name}</Field>
        <Field label="Answered from">{trace.version_used ? `v${trace.version_used}` : "—"}</Field>
        <Field label="Total time">{formatMs(trace.latency_ms)}</Field>
        <Field label="Cost">{formatCost(trace.cost_usd)}</Field>
        <Field label="Stages">{trace.spans.length}</Field>
        <Field label="When">{formatTime(trace.started_at)}</Field>
      </dl>

      <div className="mt-10 grid grid-cols-1 xl:grid-cols-[minmax(0,1fr)_minmax(0,440px)] gap-10 xl:gap-12">
        {/* ---------------- Waterfall ---------------- */}
        <section aria-label="Stages">
          <h2 className="text-[20px] font-extrabold text-ink">Stages on one clock</h2>
          <p className="mt-1 mb-4 text-[15px] text-ink-2 max-w-[62ch]">
            Each bar starts when its stage started, so gaps and overlaps are real. Select a stage for its
            inputs and outputs.
          </p>

          <div className="sheet overflow-hidden">
            {/* The axis: labelled ticks over the bar column. */}
            <div className="hidden sm:flex items-end gap-3 px-4 pt-3 pb-2 border-b border-rule">
              <span className="w-[10.5rem] shrink-0" />
              <span className="relative flex-1 h-4">
                {ticks.map((t) => (
                  <span
                    key={t}
                    className={`absolute bottom-0 mono text-[11px] text-ink-3 tnum whitespace-nowrap ${
                      t === 0 ? "" : t === 1 ? "-translate-x-full" : "-translate-x-1/2"
                    }`}
                    style={{ left: `${t * 100}%` }}
                  >
                    {formatMs(axis.totalMs * t)}
                  </span>
                ))}
              </span>
              <span className="w-16 shrink-0" />
              <span className="w-16 shrink-0" />
            </div>

            <div className="divide-y divide-rule">
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
                      className={`w-full text-left px-4 py-2.5 transition-colors ${isOpen ? "bg-paper" : "hover:bg-paper/60"}`}
                    >
                      {/* Below sm the bar takes a line of its own: beside four
                          fixed columns it was squeezed to nothing. */}
                      <div className="flex flex-wrap sm:flex-nowrap items-center gap-x-3 gap-y-1.5">
                        <span
                          className="mono text-[13px] flex-1 sm:flex-none sm:w-[10.5rem] shrink-0"
                          style={{ color: failed ? "var(--color-fail)" : "var(--color-ink)" }}
                        >
                          {span.name}
                        </span>

                        <span className="order-last sm:order-none basis-full sm:basis-auto flex-1 h-3 rounded-[3px] bg-well relative overflow-hidden">
                          <span
                            className="absolute inset-y-0 rounded-[3px]"
                            style={{
                              left: `${offset}%`,
                              width: `${width}%`,
                              // A 3ms span on a 40s axis is still a span.
                              minWidth: 3,
                              backgroundColor: failed ? "var(--color-fail)" : "var(--color-ink-2)",
                            }}
                          />
                        </span>

                        <span className="mono text-[12px] text-ink-3 tnum w-16 text-right">
                          {Number.isFinite(count) ? `${count} hits` : ""}
                        </span>
                        <span className="mono text-[13px] text-ink tnum w-16 text-right">
                          {formatMs(span.duration_ms)}
                        </span>
                      </div>
                    </button>

                    {isOpen && (
                      <div className="px-4 pb-4 pt-1 bg-paper">
                        <div className="grid sm:grid-cols-2 gap-3">
                          <SpanBlock title="Input" data={span.input} />
                          <SpanBlock title="Output" data={stripTop(span.output)} />
                        </div>
                        {Array.isArray(span.output?.top) && (
                          <div className="mt-4">
                            <h3 className="text-[13px] font-bold text-ink mb-1.5">Top results from this stage</h3>
                            <ol className="space-y-1">
                              {(span.output.top as Array<Record<string, unknown>>).slice(0, 10).map((entry, index) => (
                                <li key={index} className="mono text-[12px] text-ink-2 flex gap-2">
                                  <span className="text-ink-3 w-5 text-right tnum">{index + 1}</span>
                                  <span className="flex-1 truncate text-ink">{String(entry.source_path ?? "")}</span>
                                  <span className="text-ink-3 truncate max-w-[14rem]">
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
          </div>

          {trace.answer && (
            <>
              <h2 className="mt-10 text-[20px] font-extrabold text-ink">Final answer</h2>
              <div className="mt-3 sheet px-5 py-4 text-[16px] leading-[1.65] text-ink whitespace-pre-wrap wrap-break-word">
                <AnswerText
                  text={trace.answer}
                  citation={(marker, key) => (
                    <span
                      key={key}
                      className="mono text-[12px] align-[0.35em] mx-0.5 px-1.5 rounded-[4px] border border-rule-strong"
                    >
                      {marker}
                    </span>
                  )}
                />
              </div>
            </>
          )}
        </section>

        {/* ---------------- Rank trail ---------------- */}
        <aside aria-label="Rank trail">
          <h2 className="text-[20px] font-extrabold text-ink">Rank trail</h2>
          <p className="mt-1 mb-4 text-[15px] text-ink-2 max-w-[62ch]">
            Where each excerpt placed at every stage, left to right. A number that climbs is an excerpt the
            pipeline demoted: a ranking miss you can point at.
          </p>

          {chunks.length === 0 ? (
            <p className="text-ink-3">This trace recorded no ranked results.</p>
          ) : (
            <ol className="sheet divide-y divide-rule overflow-hidden">
              {chunks.slice(0, 25).map((chunk) => {
                const stages = STAGE_ORDER.filter((s) => chunk.ranks[s] != null);
                const first = stages.length ? chunk.ranks[stages[0]] : null;
                const last = stages.length ? chunk.ranks[stages[stages.length - 1]] : null;
                const demoted = first != null && last != null && last > first + 2;

                return (
                  <li key={chunk.chunk_id} className="px-4 py-3">
                    <p className="text-[14px] font-bold text-ink leading-snug">
                      {cleanHeading(chunk.heading_path).split(" > ").slice(-1)[0] || chunk.source_path}
                    </p>
                    <p className="mono text-[12px] text-ink-3 truncate">
                      {chunk.source_path} · v{chunk.version}
                    </p>
                    <div className="mt-2 flex items-center gap-1.5 flex-wrap">
                      {stages.map((stage, index) => {
                        const alarm = demoted && index === stages.length - 1;
                        return (
                          <span key={stage} className="flex items-center gap-1.5">
                            {index > 0 && (
                              <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true">
                                <path d="M2 5h6M5.5 2.5 8 5 5.5 7.5" fill="none" stroke="var(--color-ink-3)" strokeWidth="1.25" />
                              </svg>
                            )}
                            <span
                              className="mono text-[12px] px-2 py-0.5 rounded-[4px] border tnum"
                              style={{
                                borderColor: alarm ? "var(--color-fail)" : "var(--color-rule-strong)",
                                color: alarm ? "var(--color-fail)" : "var(--color-ink-2)",
                                backgroundColor: alarm ? "var(--color-fail-wash)" : "transparent",
                              }}
                            >
                              {stage} <span className="font-bold">{chunk.ranks[stage]}</span>
                            </span>
                          </span>
                        );
                      })}
                    </div>
                  </li>
                );
              })}
            </ol>
          )}
        </aside>
      </div>
    </div>
  );
}

function BackLink() {
  return (
    <Link href="/traces" className="inline-flex items-center gap-1.5 mt-4 text-[15px] text-ink-2 hover:text-ink">
      <svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true">
        <path d="M8.5 3 4.5 7l4 4" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
      All traces
    </Link>
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
      <h3 className="text-[13px] font-bold text-ink mb-1">{title}</h3>
      <pre className="mono text-[12px] leading-relaxed text-ink-2 bg-well rounded-md p-3 overflow-x-auto max-h-56">
        {empty ? "—" : JSON.stringify(data, null, 2)}
      </pre>
    </div>
  );
}
