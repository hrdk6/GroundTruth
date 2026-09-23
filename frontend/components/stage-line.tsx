"use client";

import { useEffect, useState } from "react";

/** The route a `full` answer takes, in order. */
const ROUTE = ["Resolve version", "Retrieve", "Rank", "Generate", "Verify each claim"];

/**
 * The wait for a cold answer, which is 20-60 s on the free-tier model.
 *
 * The request reports nothing until it finishes, so the cursor loops along the
 * whole route and never pretends to know which stage is running. What is real
 * is the elapsed time, and the route itself.
 */
export function StageLine({ question }: { question: string }) {
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    const started = performance.now();
    const timer = window.setInterval(() => setElapsed((performance.now() - started) / 1000), 250);
    return () => window.clearInterval(timer);
  }, []);

  return (
    <div className="sheet px-5 sm:px-8 py-7" role="status" aria-live="polite">
      <p className="text-xl sm:text-2xl font-bold text-ink text-balance wrap-break-word">{question}</p>

      <div className="mt-7">
        <ol className="hidden sm:grid grid-cols-5 gap-2 mb-2.5">
          {ROUTE.map((stage) => (
            <li key={stage} className="machine text-ink-3">
              {stage}
            </li>
          ))}
        </ol>
        <div className="relative h-[6px] rounded-full bg-well overflow-hidden">
          <span className="stage-cursor absolute inset-y-0 rounded-full bg-ink" />
        </div>
      </div>

      <p className="mt-4 text-[14px] text-ink-2">
        Running for <span className="mono tnum text-ink">{elapsed.toFixed(0)} s</span>. A cold answer
        takes 20–60 s here, most of it verifying each claim against its excerpt; a question asked
        before replays in under a second.
      </p>
    </div>
  );
}
