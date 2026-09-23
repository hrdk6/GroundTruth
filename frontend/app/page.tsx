"use client";

/**
 * Ask — the chat view.
 *
 * The hero interaction is not the answer text; it is the link from a claim to
 * the evidence for it. Clicking a [n] marker scrolls its excerpt into view and
 * flashes it, and every sentence carries a verification mark in the margin. A
 * reader should be able to check the system rather than trust it.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { ApiError, api, type Citation, type QueryResponse, type Segment } from "@/lib/api";
import { AnswerText } from "@/components/answer-text";
import {
  ErrorNote,
  Field,
  Spinner,
  VerificationMark,
  cleanHeading,
  formatCost,
  formatMs,
} from "@/components/primitives";

const EXAMPLES = [
  "What does the kubelet do when a liveness probe fails?",
  "How do I set a memory limit on a container?",
  "What changed about seccomp profiles between versions?",
  "Which kubectl command patches a running Deployment?",
];

/**
 * The answer's sentences, as the server split them.
 *
 * This used to re-split `answer` in the browser and look each sentence up in
 * the verifier's list by exact text. The two splitters disagreed at the edges
 * -- a `[3]` after the full stop, a full-width `【1】` -- and a sentence whose
 * split differed silently lost its verdict. The server now returns segments
 * produced by the verifier's own splitter, so there is one definition of a
 * sentence.
 */
/** At most `limit` characters, cut at a word, with the cut shown. */
function clip(text: string, limit: number): string {
  if (text.length <= limit) return text;
  const cut = text.slice(0, limit);
  const space = cut.lastIndexOf(" ");
  return `${(space > limit * 0.6 ? cut.slice(0, space) : cut).trimEnd()}…`;
}

function segmentsOf(result: QueryResponse): Segment[] {
  if (result.segments?.length) return result.segments;
  // An older API without segments: one block, no per-sentence marks.
  return [{ text: result.answer, citations: [], factual: false, verdict: null, reason: "" }];
}

export default function AskPage() {
  const [question, setQuestion] = useState("");
  const [version, setVersion] = useState<string>("auto");
  // Empty until /versions answers with the server's default pipeline.
  const [configName, setConfigName] = useState<string>("");
  const [versions, setVersions] = useState<string[]>([]);
  const [configs, setConfigs] = useState<string[]>([]);
  const [result, setResult] = useState<QueryResponse | null>(null);
  const [asked, setAsked] = useState<string>("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [voted, setVoted] = useState<boolean | null>(null);
  const [activeCitation, setActiveCitation] = useState<number | null>(null);

  const excerptRefs = useRef<Record<number, HTMLDivElement | null>>({});

  useEffect(() => {
    api
      .versions()
      .then((data) => {
        setVersions(data.versions);
        setConfigs(data.configs);
        // The server's shipping pipeline, not `baseline`: the baseline exists
        // to be beaten, and defaulting to it hid verification and conflict
        // notes -- the features the page is about -- behind a dropdown.
        setConfigName((current) => current || data.default_config);
      })
      .catch(() => {
        /* The banner on first query covers an unreachable backend. */
      });
  }, []);

  const submit = useCallback(
    async (text: string) => {
      const trimmed = text.trim();
      if (!trimmed || loading) return;

      setLoading(true);
      setError(null);
      setResult(null);
      setVoted(null);
      setActiveCitation(null);
      setAsked(trimmed);

      try {
        const response = await api.query({
          question: trimmed,
          version: version === "auto" ? null : version,
          config_name: configName || null,
        });
        setResult(response);
      } catch (exc) {
        setError(exc instanceof ApiError ? exc.message : "Something went wrong.");
      } finally {
        setLoading(false);
      }
    },
    [configName, loading, version],
  );

  const focusCitation = useCallback((marker: number) => {
    setActiveCitation(marker);
    const node = excerptRefs.current[marker];
    if (node) {
      node.scrollIntoView({ behavior: "smooth", block: "center" });
      node.classList.remove("evidence-lock");
      // Reflow so the animation restarts when the same marker is clicked twice.
      void node.offsetWidth;
      node.classList.add("evidence-lock");
    }
  }, []);

  const segments = useMemo(() => (result ? segmentsOf(result) : []), [result]);

  const vote = async (helpful: boolean) => {
    if (!result?.trace_id) return;
    setVoted(helpful);
    try {
      await api.feedback({ trace_id: result.trace_id, helpful });
    } catch {
      setVoted(null);
    }
  };

  return (
    <div className="mx-auto max-w-[1440px] px-4 sm:px-6 py-8">
      {/* Question bar */}
      <form
        onSubmit={(event) => {
          event.preventDefault();
          void submit(question);
        }}
        className="flex flex-col gap-3"
      >
        <div className="flex flex-col sm:flex-row gap-2">
          <input
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
            placeholder="Ask about the Kubernetes documentation"
            aria-label="Your question"
            className="flex-1 bg-panel border border-line rounded-sm px-4 py-3 text-text placeholder:text-dim focus:border-line-bright"
          />
          <button
            type="submit"
            disabled={loading || !question.trim()}
            className="px-5 py-3 rounded-sm font-medium bg-brass text-ink disabled:opacity-40 disabled:cursor-not-allowed hover:brightness-110 transition"
          >
            Ask
          </button>
        </div>

        <div className="flex flex-wrap items-center gap-x-5 gap-y-2 text-sm">
          <label className="flex items-center gap-2">
            <span className="text-mute">Version</span>
            <select
              value={version}
              onChange={(event) => setVersion(event.target.value)}
              className="bg-panel border border-line rounded-sm px-2 py-1 mono text-xs text-text"
            >
              <option value="auto">auto-detect</option>
              {versions.map((v) => (
                <option key={v} value={v}>
                  {v}
                </option>
              ))}
            </select>
          </label>

          <label className="flex items-center gap-2">
            <span className="text-mute">Pipeline</span>
            <select
              value={configName}
              onChange={(event) => setConfigName(event.target.value)}
              className="bg-panel border border-line rounded-sm px-2 py-1 mono text-xs text-text"
            >
              {!configName && <option value="">server default</option>}
              {configs.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </select>
          </label>
        </div>
      </form>

      {/* Examples and orientation, shown only before the first question */}
      {!result && !loading && !error && (
        <div className="mt-8">
          <p className="text-sm text-mute mb-3">Try one of these:</p>
          <div className="flex flex-wrap gap-2">
            {EXAMPLES.map((example) => (
              <button
                key={example}
                onClick={() => {
                  setQuestion(example);
                  void submit(example);
                }}
                className="text-left text-sm px-3 py-2 border border-line rounded-sm text-mute hover:text-bright hover:border-line-bright transition-colors"
              >
                {example}
              </button>
            ))}
          </div>

          {/* What the reader should expect back. Three claims the system will
              then have to live up to on the very next screen. */}
          <div className="mt-12 rule-ticked" />
          <div className="mt-6 grid grid-cols-1 md:grid-cols-3 gap-x-10 gap-y-6 max-w-5xl">
            <Commitment heading="Every claim is cited">
              Each sentence points at the excerpt behind it. Click a marker to
              read the source.
            </Commitment>
            <Commitment heading="Citations are checked">
              A second pass asks whether each cited excerpt really supports its
              sentence. When too little holds up, the answer is withheld rather
              than dressed up.
            </Commitment>
            <Commitment heading="Versions stay separate">
              Answers come from one release. Where the docs changed, the
              difference is shown beside the answer instead of blended into it.
            </Commitment>
          </div>

          <p className="mt-8 text-xs text-dim max-w-prose">
            {versions.length > 0
              ? `Indexed: Kubernetes ${versions.join(", ")}.`
              : "No corpus indexed yet — run `make ingest` to load the documentation."}
          </p>
        </div>
      )}

      {loading && (
        <div className="mt-8">
          <Spinner label={`Retrieving and answering: “${asked}”`} />
        </div>
      )}

      {error && (
        <div className="mt-8">
          <ErrorNote message={error} />
        </div>
      )}

      {result && (
        <div className="mt-8 grid grid-cols-1 lg:grid-cols-[minmax(0,1fr)_minmax(0,420px)] gap-8">
          {/* ---------------- Answer ---------------- */}
          <section>
            <div className="rule-ticked mb-4" />
            <h1 className="text-lg text-bright font-medium mb-5">{asked}</h1>

            {result.abstained ? (
              <div className="border-l-2 border-brass-dim pl-4 py-2">
                <p className="text-text">{result.answer}</p>
                <p className="mt-2 text-sm text-mute">
                  The system declined rather than answer from excerpts that don&apos;t
                  support a claim. That is the intended behaviour when the
                  documentation doesn&apos;t cover a question.
                </p>
              </div>
            ) : (
              <div className="space-y-2">
                {segments.map((segment, index) => (
                  // A div, not a p: a sentence can hold a code block.
                  <div
                    key={index}
                    className="flex gap-3 items-start leading-relaxed"
                    title={segment.reason || undefined}
                  >
                    <span className="pt-[7px] w-[10px] shrink-0">
                      {segment.verdict && <VerificationMark verdict={segment.verdict} />}
                    </span>
                    <div className="flex-1 min-w-0 wrap-anywhere">
                      <AnswerText
                        text={segment.display || segment.text}
                        citation={(marker, key) => (
                          <button
                            key={key}
                            onClick={() => focusCitation(marker)}
                            aria-label={`Show excerpt ${marker}`}
                            className={`mono text-[11px] align-super px-1 rounded-[2px] transition-colors ${
                              activeCitation === marker
                                ? "bg-brass text-ink"
                                : "text-brass hover:bg-raised"
                            }`}
                          >
                            {marker}
                          </button>
                        )}
                      />
                    </div>
                  </div>
                ))}
              </div>
            )}

            {/* Conflict notes: adjacent to the answer, never merged into it. */}
            {result.conflicts.length > 0 && (
              <div className="mt-6 border border-brass-dim/50 rounded-sm">
                <div className="px-4 py-2 border-b border-brass-dim/40 text-sm text-brass">
                  This differs in other versions
                </div>
                <div className="divide-y divide-line">
                  {result.conflicts.slice(0, 3).map((conflict, index) => (
                    <div key={index} className="px-4 py-3 text-sm">
                      <div className="mono text-xs text-mute">
                        {conflict.source_path}
                        {conflict.heading_path && ` · ${cleanHeading(conflict.heading_path)}`}
                      </div>
                      <div className="mt-2 grid sm:grid-cols-2 gap-3">
                        <div>
                          <div className="mono text-[11px] text-brass mb-1">
                            v{conflict.latest_version} — answered from this
                          </div>
                          <p className="text-text/90 text-[13px] leading-snug">
                            {clip(conflict.latest_text, 260)}
                          </p>
                        </div>
                        <div>
                          <div className="mono text-[11px] text-mute mb-1">
                            v{conflict.other_version}
                          </div>
                          <p className="text-mute text-[13px] leading-snug">
                            {clip(conflict.other_text, 260)}
                          </p>
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Readout */}
            <div className="mt-6 rule pt-4 flex flex-wrap gap-x-8 gap-y-3">
              <Field label="Answered from">
                {result.version_used ? `v${result.version_used}` : "—"}
              </Field>
              <Field label="Latency">{formatMs(result.latency_ms)}</Field>
              <Field label="Cost">{formatCost(result.cost_usd)}</Field>
              {result.verification?.support_fraction != null && (
                <Field label="Claims supported">
                  {(result.verification.support_fraction * 100).toFixed(0)}%
                </Field>
              )}
              <Field label="Excerpts used">{result.citations.length}</Field>
              <Field label="Pipeline">{result.config_name}</Field>
              {result.regenerated && <Field label="Regenerated">once, after verification</Field>}
            </div>
            <p className="mt-2 text-xs text-dim">{result.version_reason}</p>

            <div className="mt-5 flex items-center gap-3 text-sm">
              <span className="text-mute">Was this useful?</span>
              <button
                onClick={() => void vote(true)}
                disabled={!result.trace_id}
                className={`px-3 py-1 border rounded-sm transition-colors ${
                  voted === true
                    ? "border-ok text-ok"
                    : "border-line text-mute hover:text-bright hover:border-line-bright"
                } disabled:opacity-40`}
              >
                Yes
              </button>
              <button
                onClick={() => void vote(false)}
                disabled={!result.trace_id}
                className={`px-3 py-1 border rounded-sm transition-colors ${
                  voted === false
                    ? "border-alarm text-alarm"
                    : "border-line text-mute hover:text-bright hover:border-line-bright"
                } disabled:opacity-40`}
              >
                No
              </button>
              {result.trace_id && (
                <Link
                  href={`/traces/${result.trace_id}`}
                  className="ml-auto text-brass hover:underline"
                >
                  See how this answer was produced
                </Link>
              )}
            </div>
          </section>

          {/* ---------------- Evidence ---------------- */}
          <aside className="lg:sticky lg:top-20 lg:self-start lg:max-h-[calc(100vh-6rem)] lg:overflow-y-auto">
            <div className="rule-ticked mb-4" />
            <h2 className="text-sm text-mute mb-3">
              Evidence · {result.citations.length} excerpt
              {result.citations.length === 1 ? "" : "s"} cited
            </h2>

            {result.citations.length === 0 ? (
              <p className="text-sm text-dim">
                No excerpt was cited, so there is nothing to check here.
              </p>
            ) : (
              <div className="space-y-3">
                {result.citations.map((citation) => (
                  <Excerpt
                    key={citation.marker}
                    citation={citation}
                    active={activeCitation === citation.marker}
                    onSelect={() => setActiveCitation(citation.marker)}
                    ref={(node) => {
                      excerptRefs.current[citation.marker] = node;
                    }}
                  />
                ))}
              </div>
            )}
          </aside>
        </div>
      )}
    </div>
  );
}

function Commitment({ heading, children }: { heading: string; children: React.ReactNode }) {
  return (
    <div className="border-t border-line-bright pt-3">
      <h2 className="text-text font-medium text-[15px]">{heading}</h2>
      <p className="mt-1.5 text-sm text-mute leading-relaxed">{children}</p>
    </div>
  );
}

function Excerpt({
  citation,
  active,
  onSelect,
  ref,
}: {
  citation: Citation;
  active: boolean;
  onSelect: () => void;
  ref: (node: HTMLDivElement | null) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const long = citation.text.length > 420;

  return (
    <div
      ref={ref}
      onClick={onSelect}
      className={`border rounded-sm transition-colors ${
        active ? "border-brass bg-raised" : "border-line bg-panel/60 hover:border-line-bright"
      }`}
    >
      <div className="px-3 py-2 flex items-start gap-2 border-b border-line">
        <span
          className={`mono text-[11px] px-1.5 rounded-[2px] shrink-0 ${
            active ? "bg-brass text-ink" : "bg-raised text-brass"
          }`}
        >
          {citation.marker}
        </span>
        <div className="min-w-0 flex-1">
          <a
            href={citation.url}
            target="_blank"
            rel="noreferrer noopener"
            className="mono text-[11px] text-text hover:text-brass break-all"
            onClick={(event) => event.stopPropagation()}
          >
            {citation.source_path}
          </a>
          <div className="mono text-[11px] text-dim mt-0.5">
            v{citation.version}
            {citation.heading_path && ` · ${cleanHeading(citation.heading_path)}`}
          </div>
        </div>
      </div>

      <div className="px-3 py-2">
        <p className="text-[13px] leading-snug text-text/90 whitespace-pre-wrap wrap-anywhere">
          {expanded || !long ? citation.text : `${citation.text.slice(0, 420)}…`}
        </p>
        {long && (
          <button
            onClick={(event) => {
              event.stopPropagation();
              setExpanded((value) => !value);
            }}
            className="mt-2 text-xs text-brass hover:underline"
          >
            {expanded ? "Show less" : "Show full excerpt"}
          </button>
        )}
      </div>
    </div>
  );
}
