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
      window.scrollTo({ top: 0, behavior: "smooth" });

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
    <div className="mx-auto max-w-[1320px] px-4 sm:px-8">
      {/* The same two columns as the report below: the question over the
          claims, the suggestions over the evidence. */}
      <section className="pt-8 sm:pt-10 pb-7 border-b border-rule grid grid-cols-1 lg:grid-cols-[minmax(0,1fr)_minmax(0,400px)] gap-x-12 gap-y-7">
        <div>
          <p className="text-[17px] text-ink-2 max-w-[62ch]">
            Ask about the Kubernetes documentation
            {versions.length ? ` (releases ${versions.join(" and ")})` : ""}. Every claim in the answer
            cites an excerpt, and a second pass checks that the excerpt really supports it.
          </p>

          <form
            onSubmit={(event) => {
              event.preventDefault();
              void submit(question);
            }}
            className="mt-4"
          >
            <div className="flex flex-col sm:flex-row gap-2.5">
              <input
                ref={inputRef}
                value={question}
                onChange={(event) => setQuestion(event.target.value)}
                placeholder="How do I roll back a Deployment?"
                aria-label="Your question"
                className="w-full sm:w-auto sm:flex-1 min-w-0 h-14 rounded-md border border-rule-strong bg-sheet px-4 text-[18px] text-ink shadow-[0_1px_2px_rgb(21_23_28/0.05)] focus:border-act focus:outline-none focus:ring-4 focus:ring-act-wash transition-shadow"
              />
              <button
                type="submit"
                disabled={loading}
                className="h-14 rounded-md bg-act px-8 text-[17px] font-bold text-sheet hover:bg-act-deep disabled:opacity-60 disabled:cursor-wait transition-colors"
              >
                {loading ? "Asking…" : "Ask"}
              </button>
            </div>

            <div className="mt-3 flex flex-wrap items-center gap-x-6 gap-y-2 text-[14px] text-ink-2">
              <label className="flex items-center gap-2">
                Release
                <select
                  value={version}
                  onChange={(event) => setVersion(event.target.value)}
                  className="rounded-md border border-rule-strong bg-sheet px-2 py-1 mono text-[13px] text-ink"
                >
                  <option value="auto">detect from the question</option>
                  {versions.map((v) => (
                    <option key={v} value={v}>
                      {v}
                    </option>
                  ))}
                </select>
              </label>
              <label className="flex items-center gap-2">
                Pipeline
                <select
                  value={configName}
                  onChange={(event) => setConfigName(event.target.value)}
                  className="rounded-md border border-rule-strong bg-sheet px-2 py-1 mono text-[13px] text-ink"
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
          {showingRecorded && (
            <div className="mt-6">
              <RecordedNotice recorded={recordedMeta} />
            </div>
          )}
        </div>

        <div>
          <h2 className="text-[15px] font-bold text-ink mb-2">Or try one that was asked before</h2>
          <ul className="sheet divide-y divide-rule overflow-hidden">
            {SUGGESTIONS.map((suggestion) => (
              <li key={suggestion.question}>
                <button
                  onClick={() => void submit(suggestion.question)}
                  disabled={loading}
                  className="w-full text-left px-4 py-2 text-[14.5px] leading-snug text-ink hover:bg-paper disabled:opacity-50 transition-colors"
                >
                  {suggestion.question}
                  {suggestion.note && <span className="ml-1.5 text-[13px] text-ink-3">· {suggestion.note}</span>}
                </button>
              </li>
            ))}
          </ul>
        </div>
      </section>

      <section className="pt-8" aria-live="polite">
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
  );
}
