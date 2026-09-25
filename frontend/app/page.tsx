"use client";

/**
 * Ask.
 *
 * A reviewer arrives alone and a cold answer takes 20-60 s, so the page never
 * opens empty: it shows a recorded run -- a real response, captured verbatim
 * from the API -- that can be checked claim by claim before anyone waits. Their
 * own question replaces it with a live run in the same report.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, api, type QueryResponse } from "@/lib/api";
import recordedRun from "@/lib/recorded-run.json";
import { RecordedNotice, RunReport, type RecordedMeta } from "@/components/run-report";
import { StageLine } from "@/components/stage-line";
import { ErrorNote } from "@/components/primitives";

/**
 * Questions worth trying, each already asked of the live system. A note is
 * only attached where that behaviour was actually observed.
 */
const SUGGESTIONS: Array<{ question: string; note?: string }> = [
  { question: "How do I set a probe-level terminationGracePeriodSeconds?", note: "changed between releases" },
  { question: "How do I create a Pod with a seccomp profile?" },
  { question: "Which kubectl command patches a running Deployment?" },
  { question: "How much data can a ConfigMap hold?" },
  {
    question: "How much does a managed Kubernetes control plane cost per month on AWS?",
    note: "not in the docs, so it refuses",
  },
];

const RECORDED = recordedRun as unknown as {
  question: string;
  recorded: { first_run_trace_id: string; first_run_latency_ms: number; first_run_at: string };
  response: QueryResponse;
};

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
  // The recorded run's trace only exists on the database it was recorded in.
  const [recordedTrace, setRecordedTrace] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  // Answers land below the hero; asking brings the reader down to them.
  const resultsRef = useRef<HTMLElement>(null);

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
        /* The recorded run still renders; asking will say the API is down. */
      });
    api
      .trace(RECORDED.recorded.first_run_trace_id)
      .then(() => setRecordedTrace(RECORDED.recorded.first_run_trace_id))
      .catch(() => setRecordedTrace(null));
  }, []);

  const submit = useCallback(
    async (text: string) => {
      const trimmed = text.trim();
      if (loading) return;
      if (!trimmed) {
        inputRef.current?.focus();
        return;
      }

      setQuestion(trimmed);
      setLoading(true);
      setError(null);
      setResult(null);
      setVoted(null);
      setAsked(trimmed);
      requestAnimationFrame(() => resultsRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }));

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

  const vote = async (helpful: boolean) => {
    if (!result?.trace_id) return;
    setVoted(helpful);
    try {
      await api.feedback({ trace_id: result.trace_id, helpful });
    } catch {
      setVoted(null);
    }
  };

  const showingRecorded = !result && !loading && !error;
  const recordedMeta: RecordedMeta = {
    firstRunAt: RECORDED.recorded.first_run_at,
    firstRunLatencyMs: RECORDED.recorded.first_run_latency_ms,
    traceId: recordedTrace,
    onAskLive: () => void submit(RECORDED.question),
  };

  return (
    <div>
      {/* The harbour: the question is asked on the surface, over the contours
          of what it will be sounded against. */}
      {/* `clip`, not `hidden`: a hidden overflow is still a scroll container, and
          focusing a chip would scroll the hero's contents out from under it. */}
      <section className="on-harbor relative overflow-clip bg-harbor text-harbor-ink">
        <Contours />
        <div className="relative mx-auto max-w-[1320px] px-4 sm:px-8 pt-10 sm:pt-16 pb-12 sm:pb-16">
          <h1 className="serif font-medium text-[40px] sm:text-[60px] leading-[1.02] tracking-[-0.025em] max-w-[16ch]">
            Ask the Kubernetes docs.
          </h1>
          <p className="mt-4 text-[17px] sm:text-[19px] leading-[1.55] text-harbor-dim max-w-[56ch]">
            Every sentence in the answer comes back with the excerpt it quotes, and a second pass
            checks that the excerpt really says it
            {versions.length ? `. Releases ${versions.join(" and ")} are indexed.` : "."}
          </p>

          <form
            onSubmit={(event) => {
              event.preventDefault();
              void submit(question);
            }}
            className="mt-8 max-w-[860px]"
          >
            <div className="flex flex-col sm:flex-row gap-2.5 rounded-2xl bg-harbor-2/80 p-2 ring-1 ring-harbor-line">
              <input
                ref={inputRef}
                value={question}
                onChange={(event) => setQuestion(event.target.value)}
                placeholder="How do I roll back a Deployment?"
                aria-label="Your question"
                className="w-full sm:w-auto sm:flex-1 min-w-0 h-14 rounded-xl bg-sheet px-5 serif text-[19px] text-ink focus:outline-none focus:ring-4 focus:ring-signal/60 transition-shadow"
              />
              <button
                type="submit"
                disabled={loading}
                className="h-14 rounded-xl bg-signal px-9 text-[17px] font-bold text-harbor hover:bg-signal-hi disabled:opacity-60 disabled:cursor-wait transition-colors"
              >
                {loading ? "Asking…" : "Ask"}
              </button>
            </div>

            <div className="mt-4 flex flex-wrap items-center gap-x-6 gap-y-2 text-[14px] text-harbor-dim">
              <label className="flex items-center gap-2">
                Release
                <select
                  value={version}
                  onChange={(event) => setVersion(event.target.value)}
                  className="rounded-lg border border-harbor-line bg-harbor-2 px-2.5 py-1.5 text-[14px] text-harbor-ink"
                >
                  <option value="auto">Detect from the question</option>
                  {versions.map((v) => (
                    <option key={v} value={v}>
                      Kubernetes {v}
                    </option>
                  ))}
                </select>
              </label>
              <label className="flex items-center gap-2">
                Pipeline
                <select
                  value={configName}
                  onChange={(event) => setConfigName(event.target.value)}
                  className="rounded-lg border border-harbor-line bg-harbor-2 px-2.5 py-1.5 mono text-[13px] text-harbor-ink"
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

          <div className="mt-9">
            <h2 className="text-[15px] font-semibold text-harbor-ink">Or try a question that was asked before</h2>
            <ul className="mt-3 flex flex-wrap gap-2.5 max-w-[1000px]">
              {SUGGESTIONS.map((suggestion) => (
                <li key={suggestion.question}>
                  <button
                    onClick={() => void submit(suggestion.question)}
                    disabled={loading}
                    className="text-left rounded-full border border-harbor-line bg-harbor/40 px-4 py-2 text-[14.5px] leading-snug text-harbor-ink hover:bg-harbor-2 hover:border-harbor-dim disabled:opacity-50 transition-colors"
                  >
                    {suggestion.question}
                    {suggestion.note && (
                      <span className="ml-2 serif italic text-[15px] text-signal">{suggestion.note}</span>
                    )}
                  </button>
                </li>
              ))}
            </ul>
          </div>
        </div>
      </section>

      <div className="mx-auto max-w-[1320px] px-4 sm:px-8">
        {showingRecorded && (
          <div className="mt-8">
            <RecordedNotice recorded={recordedMeta} />
          </div>
        )}

        <section ref={resultsRef} className="pt-8 scroll-mt-16" aria-live="polite">
          {loading && <StageLine question={asked} />}

          {error && !loading && (
            <div className="max-w-[70ch]">
              <ErrorNote message={error} />
            </div>
          )}

          {result && !loading && (
            <RunReport question={asked} result={result} onFeedback={(h) => void vote(h)} voted={voted} />
          )}

          {showingRecorded && (
            <RunReport question={RECORDED.question} result={RECORDED.response} recorded={recordedMeta} />
          )}
        </section>
      </div>
    </div>
  );
}

/**
 * Depth contours behind the hero, the way a chart draws the seabed. Drawn from
 * a fixed formula, so the server and the browser render the same lines.
 */
function Contours() {
  const rings = Array.from({ length: 9 }, (_, i) => {
    const base = 70 + i * 46;
    const points = Array.from({ length: 73 }, (_, step) => {
      const t = (step / 72) * Math.PI * 2;
      const r =
        base * (1 + 0.09 * Math.sin(3 * t + i * 0.55) + 0.05 * Math.sin(5 * t - i * 0.8) + 0.03 * Math.cos(2 * t + i));
      return `${(Math.cos(t) * r * 1.35).toFixed(1)},${(Math.sin(t) * r).toFixed(1)}`;
    });
    return `M${points.join("L")}Z`;
  });
  return (
    <svg
      aria-hidden="true"
      className="pointer-events-none absolute -right-40 top-1/2 -translate-y-1/2 h-[140%] w-auto opacity-70"
      viewBox="-640 -480 1280 960"
    >
      {rings.map((d, i) => (
        <path
          key={i}
          d={d}
          fill="none"
          stroke="var(--color-harbor-line)"
          strokeWidth={i % 3 === 0 ? 1.4 : 0.9}
          strokeDasharray={i % 3 === 2 ? "3 6" : undefined}
        />
      ))}
    </svg>
  );
}
