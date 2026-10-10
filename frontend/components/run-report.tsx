"use client";

/**
 * One answer, read as a test run.
 *
 * Each sentence the server verified is a case: its verdict in the gutter, its
 * citations beneath it, and, when focused, the verifier's own reason. The
 * focused case is pinned to the excerpt it cites by a drawn leader line, so
 * checking a claim is one look, not a hunt through a sources drawer.
 */

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import type { Citation, Conflict, QueryResponse, Segment, Verdict } from "@/lib/api";
import { AnswerText, reflow } from "@/components/answer-text";
import { DiffText, diffWords } from "@/components/word-diff";
import {
  Field,
  VERDICT_WASH,
  VerdictWord,
  VerificationMark,
  cleanHeading,
  formatCost,
  formatDate,
  formatMs,
} from "@/components/primitives";

export interface RecordedMeta {
  firstRunAt: string;
  firstRunLatencyMs: number;
  traceId: string | null;
  onAskLive: () => void;
}

function segmentsOf(result: QueryResponse): Segment[] {
  if (result.segments?.length) return result.segments;
  // An older API without segments: one block, no per-sentence marks.
  return [{ text: result.answer, citations: [], factual: false, verdict: null, reason: "" }];
}

/** At most `limit` characters, cut at a word, with the cut shown. */
function clip(text: string, limit: number): string {
  if (text.length <= limit) return text;
  const cut = text.slice(0, limit);
  const space = cut.lastIndexOf(" ");
  return `${(space > limit * 0.6 ? cut.slice(0, space) : cut).trimEnd()}…`;
}

/** What the recorded run is, said before anyone reads it as live. */
export function RecordedNotice({ recorded }: { recorded: RecordedMeta }) {
  return (
    <div className="flex flex-wrap items-center gap-x-5 gap-y-3 rounded-xl bg-act-wash/60 px-5 py-3.5">
      <p className="text-[15px] leading-snug text-ink-2 flex-1 min-w-[15rem]">
        <span className="font-semibold text-ink">Below is a recorded run:</span> a real answer from the
        live system on {formatDate(recorded.firstRunAt)}, cold, in {formatMs(recorded.firstRunLatencyMs)}.
        Check it now, before asking your own.
      </p>
      <button
        onClick={recorded.onAskLive}
        className="rounded-lg bg-sheet px-4 py-2 text-[15px] font-semibold text-act ring-1 ring-act/30 hover:ring-act transition-shadow"
      >
        Ask it live
      </button>
    </div>
  );
}

export function RunReport({
  question,
  result,
  recorded,
  onFeedback,
  voted,
}: {
  question: string;
  result: QueryResponse;
  recorded?: RecordedMeta;
  onFeedback?: (helpful: boolean) => void;
  voted?: boolean | null;
}) {
  const segments = useMemo(() => segmentsOf(result), [result]);
  const citationsByMarker = useMemo(
    () => Object.fromEntries(result.citations.map((c) => [c.marker, c])) as Record<number, Citation>,
    [result.citations],
  );

  // Start on the first case that cites something: the leader line is the
  // page's argument, and it should already be drawn in the first viewport.
  const firstCited = segments.findIndex((s) => s.citations.length > 0);
  const [focused, setFocused] = useState<number | null>(firstCited >= 0 ? firstCited : null);
  useEffect(() => setFocused(firstCited >= 0 ? firstCited : null), [result, firstCited]);

  const focusedMarkers = useMemo(
    () => new Set(focused != null ? segments[focused]?.citations ?? [] : []),
    [focused, segments],
  );

  const gridRef = useRef<HTMLDivElement>(null);
  const claimsRef = useRef<HTMLOListElement>(null);
  const rowRefs = useRef<Record<number, HTMLLIElement | null>>({});
  const excerptRefs = useRef<Record<number, HTMLDivElement | null>>({});

  const flashExcerpt = useCallback((marker: number, scroll: boolean) => {
    const node = excerptRefs.current[marker];
    if (!node) return;
    if (scroll) node.scrollIntoView({ behavior: "smooth", block: "nearest" });
    node.classList.remove("evidence-lock");
    void node.offsetWidth; // restart the animation on a repeat click
    node.classList.add("evidence-lock");
  }, []);

  const focusCase = (index: number, marker?: number) => {
    setFocused(index);
    const target = marker ?? segments[index]?.citations[0];
    if (target != null) flashExcerpt(target, marker != null);
  };

  const counts = useMemo(() => {
    const tally: Record<Verdict, number> = { supported: 0, partially: 0, unsupported: 0 };
    let unchecked = 0;
    for (const s of segments) {
      if (s.verdict) tally[s.verdict] += 1;
      else unchecked += 1;
    }
    return { ...tally, unchecked, checked: tally.supported + tally.partially + tally.unsupported };
  }, [segments]);

  const latency = recorded ? recorded.firstRunLatencyMs : result.latency_ms;
  const traceId = recorded ? recorded.traceId : result.trace_id;

  return (
    <article>
      <header>
        <h1 className="serif font-medium text-[32px] sm:text-[42px] leading-[1.12] tracking-[-0.02em] text-ink max-w-[26ch] wrap-break-word">
          {question}
        </h1>
        <Summary counts={counts} result={result} latencyMs={latency} abstained={result.abstained} />
      </header>

      {result.abstained ? (
        <Abstention answer={result.answer} />
      ) : (
        <div
          ref={gridRef}
          className="relative mt-8 grid grid-cols-1 lg:grid-cols-[minmax(0,1fr)_minmax(0,400px)] gap-8 lg:gap-12"
        >
          <section aria-label="Claims" className="lg:col-start-1 lg:row-start-1">
            <ol ref={claimsRef} className="sheet divide-y divide-rule overflow-hidden">
              {segments.map((segment, index) => (
                <ClaimRow
                  key={index}
                  caseId={index + 1}
                  segment={segment}
                  focused={focused === index}
                  onFocus={(marker) => focusCase(index, marker)}
                  ref={(node) => {
                    rowRefs.current[index] = node;
                  }}
                />
              ))}
            </ol>
          </section>

          <aside
            aria-label="Evidence"
            className="lg:col-start-2 lg:row-start-1 lg:row-span-2 lg:sticky lg:top-24 lg:self-start lg:max-h-[calc(100vh-7rem)] lg:overflow-y-auto lg:pr-1"
          >
            <h2 className="serif font-medium text-[26px] leading-tight text-ink">Evidence</h2>
            <p className="mt-1.5 mb-4 text-[14.5px] text-ink-2">
              {result.citations.length === 0
                ? "No excerpt was cited, so there is nothing here to check."
                : "The excerpts the claims cite, quoted from the indexed docs. Select a claim to open its source."}
            </p>
            <div className="space-y-3">
              {result.citations.map((citation) => (
                <Excerpt
                  key={citation.marker}
                  citation={citation}
                  open={focusedMarkers.has(citation.marker)}
                  onOpen={() => {
                    const owner = segments.findIndex((s) => s.citations.includes(citation.marker));
                    if (owner >= 0) setFocused(owner);
                  }}
                  ref={(node) => {
                    excerptRefs.current[citation.marker] = node;
                  }}
                />
              ))}
            </div>
          </aside>

          {result.conflicts.length > 0 && (
            <div className="lg:col-start-1 lg:row-start-2 mt-4 lg:mt-0">
              <VersionDifference conflicts={result.conflicts} />
            </div>
          )}

          <LeaderLine
            gridRef={gridRef}
            claimsRef={claimsRef}
            // Read at measure time: on the first render the refs are not attached yet.
            getFrom={() => (focused != null ? rowRefs.current[focused] ?? null : null)}
            getTo={() => {
              const marker = focused != null ? segments[focused]?.citations[0] : undefined;
              return marker != null && citationsByMarker[marker] ? excerptRefs.current[marker] ?? null : null;
            }}
            version={`${focused}`}
          />
        </div>
      )}

      <RunDetails result={result} latencyMs={latency} recorded={!!recorded} />

      <div className="mt-6 flex flex-wrap items-center gap-3">
        {onFeedback && result.trace_id && (
          <>
            <span className="text-[15px] text-ink-2">Was this useful?</span>
            {[true, false].map((helpful) => (
              <button
                key={String(helpful)}
                onClick={() => onFeedback(helpful)}
                aria-pressed={voted === helpful}
                className={`rounded-full border px-4 py-1.5 text-[15px] transition-colors ${
                  voted === helpful
                    ? "border-act bg-act text-sheet"
                    : "border-rule-strong bg-sheet text-ink hover:border-ink"
                }`}
              >
                {helpful ? "Yes" : "No"}
              </button>
            ))}
          </>
        )}
        {traceId && (
          <Link
            href={`/traces/${traceId}`}
            className="ml-auto text-[15px] font-semibold text-act underline decoration-act/40 hover:decoration-act"
          >
            See every stage of this run
          </Link>
        )}
      </div>
    </article>
  );
}

/* ------------------------------------------------------------------------- */

/**
 * Why an answer was withheld, said truthfully. These used to be one sentence,
 * "the evidence did not support one", which was false whenever retrieval found
 * nothing or the verifier never answered. An API older than `abstain_reason`
 * (or a recorded run) falls back to that sentence.
 */
const WITHHELD_BECAUSE: Record<string, string> = {
  no_excerpts: "nothing relevant was found in the indexed docs.",
  model_declined: "the excerpts found do not cover this question.",
  unsupported: "the evidence did not support one.",
  unverifiable: "the verifier did not respond, so the answer could not be checked.",
};

function Summary({
  counts,
  result,
  latencyMs,
  abstained,
}: {
  counts: { supported: number; partially: number; unsupported: number; unchecked: number; checked: number };
  result: QueryResponse;
  latencyMs: number;
  abstained: boolean;
}) {
  const items: Array<{ verdict: Verdict; count: number; label: string; color: string }> = [
    { verdict: "supported", count: counts.supported, label: "supported", color: "var(--color-pass)" },
    { verdict: "partially", count: counts.partially, label: "partly supported", color: "var(--color-partial-fill)" },
    { verdict: "unsupported", count: counts.unsupported, label: "unsupported", color: "var(--color-fail)" },
  ];
  const where = (
    <span className="text-ink-2">
      {result.version_used
        ? `${abstained ? "Searched" : "Answered from"} Kubernetes ${result.version_used}`
        : "No version"}{" "}
      in{" "}
      {formatMs(latencyMs)}.
    </span>
  );

  if (abstained) {
    return (
      <div className="mt-5 flex flex-wrap items-baseline gap-x-6 gap-y-2 text-[16px]">
        <span className="font-semibold text-ink">
          No answer given: {WITHHELD_BECAUSE[result.abstain_reason ?? ""] ?? WITHHELD_BECAUSE.unsupported}
        </span>
        {where}
      </div>
    );
  }

  return (
    <div className="mt-6 max-w-[760px]">
      <p className="text-[17px] text-ink">
        <span className="font-semibold tnum">
          {counts.supported} of {counts.checked}
        </span>{" "}
        {counts.checked === 1 ? "claim is" : "claims are"} fully supported by {counts.checked === 1 ? "its" : "their"}{" "}
        sources. {where}
      </p>
      {/* One segment per checked claim, grouped by verdict: how well the
          answer is grounded, at a glance. */}
      {counts.checked > 0 && (
        <div className="mt-3 flex h-2 gap-1" aria-hidden="true">
          {items.flatMap((item) =>
            Array.from({ length: item.count }, (_, i) => (
              <span key={`${item.verdict}-${i}`} className="flex-1 rounded-full" style={{ background: item.color }} />
            )),
          )}
        </div>
      )}
      <div className="mt-3 flex flex-wrap gap-x-5 gap-y-1.5 text-[14px]">
        {items.map((item) => (
          <span
            key={item.verdict}
            className={`inline-flex items-center gap-2 ${item.count ? "text-ink" : "text-ink-3"}`}
          >
            <VerificationMark verdict={item.verdict} />
            <span className="tnum font-semibold">{item.count}</span> {item.label}
          </span>
        ))}
      </div>
    </div>
  );
}

function ClaimRow({
  caseId,
  segment,
  focused,
  onFocus,
  ref,
}: {
  caseId: number;
  segment: Segment;
  focused: boolean;
  onFocus: (marker?: number) => void;
  ref: (node: HTMLLIElement | null) => void;
}) {
  return (
    <li
      ref={ref}
      tabIndex={0}
      aria-current={focused ? "true" : undefined}
      onClick={() => onFocus()}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onFocus();
        }
      }}
      className={`grid grid-cols-[18px_minmax(0,1fr)] gap-x-4 px-5 sm:px-7 py-6 cursor-pointer transition-colors outline-offset-[-2px] ${
        focused ? "bg-paper/70" : "hover:bg-paper/50"
      }`}
    >
      <span className="pt-[9px]">
        <VerificationMark verdict={segment.verdict} size={14} />
      </span>
      <div className="min-w-0">
        <div className="serif text-[19px] sm:text-[20px] leading-[1.65] text-ink wrap-break-word">
          <AnswerText
            text={segment.display || segment.text}
            citation={(marker, key) => (
              <button
                key={key}
                onClick={(event) => {
                  event.stopPropagation();
                  onFocus(marker);
                }}
                aria-label={`Show excerpt ${marker}`}
                className="font-sans text-[12px] font-semibold align-[0.35em] ml-0.5 min-w-[1.4rem] px-1.5 py-px rounded-full bg-act-wash text-act hover:bg-act hover:text-sheet transition-colors"
              >
                {marker}
              </button>
            )}
          />
        </div>
        <div className="mt-2 flex flex-wrap items-baseline gap-x-1.5 gap-y-1 text-[13px] text-ink-3">
          <VerdictWord verdict={segment.verdict} />
          {segment.citations.length > 0 && (
            <span>
              by {new Set(segment.citations).size > 1 ? "excerpts" : "excerpt"}{" "}
              {[...new Set(segment.citations)].join(", ")}
            </span>
          )}
          <span className="sr-only">(claim {caseId})</span>
        </div>
        {focused && segment.reason && (
          <p className="mt-3 text-[15px] leading-relaxed text-ink-2 max-w-[62ch]">
            <span className="font-semibold text-ink">Why: </span>
            <span
              className={segment.verdict ? "marked" : undefined}
              style={
                segment.verdict
                  ? ({ ["--mark" as string]: VERDICT_WASH[segment.verdict] } as React.CSSProperties)
                  : undefined
              }
            >
              {segment.reason}
            </span>
          </p>
        )}
      </div>
    </li>
  );
}

function Excerpt({
  citation,
  open,
  onOpen,
  ref,
}: {
  citation: Citation;
  open: boolean;
  onOpen: () => void;
  ref: (node: HTMLDivElement | null) => void;
}) {
  const [whole, setWhole] = useState(false);
  const LIMIT = 520;
  const body = useMemo(() => reflow(citation.text), [citation.text]);
  const long = body.length > LIMIT;

  return (
    <div ref={ref} className={`sheet overflow-hidden transition-colors ${open ? "border-ink/40" : ""}`}>
      <button
        onClick={onOpen}
        aria-expanded={open}
        className="w-full text-left px-4 py-3 flex items-start gap-3 hover:bg-paper/60 transition-colors"
      >
        <span className="text-[12px] font-semibold min-w-6 h-6 px-1.5 grid place-items-center rounded-full bg-act text-sheet shrink-0">
          {citation.marker}
        </span>
        <span className="min-w-0 flex-1">
          <span className="block text-[15.5px] font-semibold text-ink leading-snug">
            {cleanHeading(citation.heading_path).split(" > ").slice(-1)[0] || citation.title}
          </span>
          <span className="mt-0.5 block mono text-[12px] text-ink-3 break-all">
            {citation.source_path} · v{citation.version}
          </span>
        </span>
      </button>

      {open && (
        <div className="border-t border-rule px-4 py-3">
          <div className="serif text-[16.5px] leading-[1.65] text-ink whitespace-pre-wrap wrap-break-word">
            <AnswerText
              text={whole || !long ? body : clip(body, LIMIT)}
              citation={(marker, key) => <span key={key}>[{marker}]</span>}
            />
          </div>
          <div className="mt-3 flex flex-wrap gap-x-4 gap-y-2 text-[14px]">
            {long && (
              <button onClick={() => setWhole((v) => !v)} className="font-bold text-ink underline hover:no-underline">
                {whole ? "Show less" : "Show the whole excerpt"}
              </button>
            )}
            {citation.url && (
              <a
                href={citation.url}
                target="_blank"
                rel="noreferrer noopener"
                className="text-ink-2 underline hover:text-ink"
              >
                Open the page on kubernetes.io
              </a>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function VersionDifference({ conflicts }: { conflicts: Conflict[] }) {
  return (
    <section aria-label="Differences between releases" className="mt-2 lg:mt-2">
      <h2 className="serif font-medium text-[26px] leading-tight text-ink">
        This reads differently in Kubernetes {conflicts[0].other_version}
      </h2>
      <p className="mt-1 text-[15px] text-ink-2 max-w-[62ch]">
        The answer comes from one release only. Where a cited section changed, both versions are shown
        here, with the words that differ marked, rather than blended into the answer.
      </p>
      <div className="mt-4 space-y-4">
        {conflicts.slice(0, 3).map((conflict, index) => (
          <ConflictPair key={index} conflict={conflict} />
        ))}
      </div>
    </section>
  );
}

function ConflictPair({ conflict }: { conflict: Conflict }) {
  const [whole, setWhole] = useState(false);
  const diff = useMemo(() => diffWords(conflict.latest_text, conflict.other_text), [conflict]);
  return (
    <div className="sheet overflow-hidden">
      <div className="px-4 sm:px-5 py-3 border-b border-rule">
        <p className="text-[15px] font-bold text-ink">
          {cleanHeading(conflict.heading_path).split(" > ").slice(-1)[0]}
        </p>
        <p className="mono text-[12px] text-ink-3 break-all">{conflict.source_path}</p>
      </div>
      <div className={`relative grid sm:grid-cols-2 ${whole ? "" : "max-h-[19rem] overflow-hidden"}`}>
        {(
          [
            ["latest", conflict.latest_version, "answered from this", diff.left],
            ["other", conflict.other_version, "not used", diff.right],
          ] as const
        ).map(([tone, version, note, pieces]) => (
          <div key={tone} className={`px-4 sm:px-5 py-4 ${tone === "other" ? "sm:border-l border-t sm:border-t-0 border-rule bg-paper/50" : ""}`}>
            <p className="machine text-ink-2 mb-2">
              <span className="font-semibold text-ink">Kubernetes {version}</span>, {note}
            </p>
            <p className="serif text-[16px] leading-[1.65] text-ink whitespace-pre-wrap wrap-break-word">
              <DiffText pieces={pieces} tone={tone} />
            </p>
          </div>
        ))}
      </div>
      <div className="border-t border-rule px-4 sm:px-5 py-2.5">
        <button onClick={() => setWhole((v) => !v)} className="text-[14px] font-bold text-ink underline hover:no-underline">
          {whole ? "Show less" : "Show both sections in full"}
        </button>
      </div>
    </div>
  );
}

function Abstention({ answer }: { answer: string }) {
  return (
    <div className="mt-8 sheet px-5 sm:px-6 py-5 max-w-[70ch]">
      <p className="serif text-[20px] leading-[1.6] text-ink">{answer}</p>
      <p className="mt-3 text-[15px] text-ink-2">
        The system declines rather than answer from excerpts that do not support a claim. That is the
        intended behaviour when the documentation does not cover a question.
      </p>
    </div>
  );
}

function RunDetails({ result, latencyMs, recorded }: { result: QueryResponse; latencyMs: number; recorded: boolean }) {
  return (
    <dl className="mt-10 grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-x-6 gap-y-4 border-t border-rule pt-5">
      <Field label="Answered from">{result.version_used ? `v${result.version_used}` : "—"}</Field>
      <Field label="Why that version" prose>
        {result.version_reason || "—"}
      </Field>
      <Field label={recorded ? "Time, first run" : "Time"}>{formatMs(latencyMs)}</Field>
      <Field label="Cost">{formatCost(result.cost_usd)}</Field>
      <Field label="Pipeline">{result.config_name}</Field>
      <Field label="Regenerated" prose>
        {result.regenerated ? "Once, after verification" : "No"}
      </Field>
    </dl>
  );
}

/* ------------------------------------------------------------------------- */

/**
 * The leader line: an elbow from the focused claim, across the gutter, to the
 * excerpt it cites. Drawn only when both columns sit side by side; stacked on a
 * phone, tapping a citation scrolls to its excerpt instead.
 */
function LeaderLine({
  gridRef,
  claimsRef,
  getFrom,
  getTo,
  version,
}: {
  gridRef: React.RefObject<HTMLDivElement | null>;
  claimsRef: React.RefObject<HTMLOListElement | null>;
  getFrom: () => HTMLElement | null;
  getTo: () => HTMLElement | null;
  version: string;
}) {
  // The getters change every render; listeners read the latest through a ref.
  const ends = useRef({ getFrom, getTo });
  ends.current = { getFrom, getTo };
  const [path, setPath] = useState<{ d: string; length: number; a: [number, number]; b: [number, number] } | null>(
    null,
  );

  const measure = useCallback(() => {
    const grid = gridRef.current;
    const claims = claimsRef.current;
    const from = ends.current.getFrom();
    const to = ends.current.getTo();
    if (!grid || !claims || !from || !to || window.innerWidth < 1024) {
      setPath(null);
      return;
    }
    const g = grid.getBoundingClientRect();
    const c = claims.getBoundingClientRect();
    const row = from.getBoundingClientRect();
    const card = to.getBoundingClientRect();
    const ax = c.right - g.left;
    const ay = row.top - g.top + 30;
    const bx = card.left - g.left;
    const by = card.top - g.top + 26;
    // Hide the line when its target has scrolled out of the evidence column.
    if (card.bottom < 80 || card.top > window.innerHeight) {
      setPath(null);
      return;
    }
    const mid = ax + (bx - ax) / 2;
    const d = `M ${ax} ${ay} H ${mid} V ${by} H ${bx}`;
    setPath({ d, length: Math.abs(bx - ax) + Math.abs(by - ay), a: [ax, ay], b: [bx, by] });
  }, [gridRef, claimsRef]);

  useLayoutEffect(() => {
    measure();
  }, [measure, version]);

  useEffect(() => {
    let frame = 0;
    const schedule = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(measure);
    };
    window.addEventListener("resize", schedule);
    window.addEventListener("scroll", schedule, { passive: true });
    const observer = new ResizeObserver(schedule);
    if (gridRef.current) observer.observe(gridRef.current);
    document.fonts?.ready.then(schedule);
    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener("resize", schedule);
      window.removeEventListener("scroll", schedule);
      observer.disconnect();
    };
  }, [measure, gridRef]);

  if (!path) return null;
  return (
    <svg className="pointer-events-none absolute inset-0 hidden lg:block overflow-visible" aria-hidden="true">
      <path
        key={version}
        d={path.d}
        className="leader-path"
        fill="none"
        stroke="var(--color-act)"
        strokeWidth="1.5"
        style={{ ["--leader-length" as string]: path.length }}
      />
      <circle cx={path.a[0]} cy={path.a[1]} r="4" fill="var(--color-act)" />
      <circle cx={path.b[0]} cy={path.b[1]} r="4" fill="var(--color-sheet)" stroke="var(--color-act)" strokeWidth="1.5" />
    </svg>
  );
}
