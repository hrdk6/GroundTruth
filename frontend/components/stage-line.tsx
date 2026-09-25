"use client";

import { useEffect, useState } from "react";

/** The route a `full` answer takes, in order. */
const ROUTE = ["Resolve the release", "Retrieve excerpts", "Rank them", "Write the answer", "Check each claim"];

/**
 * The wait for a cold answer, which is 20-60 s on the free-tier model.
 *
 * A sounding line: the lead drops, touches bottom, is hauled back, drops
 * again. The request reports nothing until it finishes, so the line never
 * pretends to know which stage is running. What is real is the elapsed time,
 * and the route, listed in order.
 */
export function StageLine({ question }: { question: string }) {
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    const started = performance.now();
    const timer = window.setInterval(() => setElapsed((performance.now() - started) / 1000), 250);
    return () => window.clearInterval(timer);
  }, []);

  return (
    <div className="sheet px-5 sm:px-8 py-8 grid grid-cols-[48px_minmax(0,1fr)] gap-x-5 sm:gap-x-8" role="status" aria-live="polite">
      <div className="relative h-[150px]" aria-hidden="true">
        <svg className="absolute left-0 top-0" width="48" height="10" viewBox="0 0 48 10">
          <path
            d="M1 6c3.9 0 3.9-4 7.8-4s3.9 4 7.8 4 3.9-4 7.8-4 3.9 4 7.8 4 3.9-4 7.8-4 3.9 4 5 4"
            fill="none"
            stroke="var(--color-act)"
            strokeWidth="1.6"
            strokeLinecap="round"
          />
        </svg>
        <span className="sounding-line absolute left-[23.5px] top-[7px] h-[118px] w-px border-l border-dashed border-ink-3" />
        <span className="sounding-lead absolute left-[18px] top-[6px] block" style={{ ["--depth" as string]: "112px" }}>
          <svg width="12" height="16" viewBox="0 0 12 16">
            <path d="M6 0c-3 3.6-4.6 6-4.6 8.6a4.6 4.6 0 0 0 9.2 0C10.6 6 9 3.6 6 0z" fill="var(--color-harbor)" />
          </svg>
        </span>
        <span className="absolute left-0 bottom-0 h-[5px] w-12 rounded-full bg-pass-wash" />
      </div>

      <div className="min-w-0">
        <p className="serif font-medium text-[24px] sm:text-[30px] leading-[1.2] text-ink text-balance wrap-break-word">
          {question}
        </p>
        <ol className="mt-5 flex flex-wrap gap-x-2 gap-y-1.5 text-[14px] text-ink-2">
          {ROUTE.map((stage, index) => (
            <li key={stage} className="flex items-center gap-2">
              {stage}
              {index < ROUTE.length - 1 && <span className="text-rule-strong" aria-hidden="true">/</span>}
            </li>
          ))}
        </ol>
        <p className="mt-4 text-[15px] text-ink-2 max-w-[62ch]">
          Sounding for <span className="tnum font-semibold text-ink">{elapsed.toFixed(0)} s</span>. A cold
          answer takes 20–60 s here, most of it checking each claim against its excerpt; a question asked
          before replays in under a second.
        </p>
      </div>
    </div>
  );
}
