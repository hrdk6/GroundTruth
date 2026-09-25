/**
 * Shared report primitives.
 *
 * A verdict is drawn by FILL first -- solid, half, hollow -- and by hue
 * second. A long answer can carry twenty of these; twenty coloured badges read
 * as decoration, twenty marks in a gutter read as a measurement, and the fill
 * survives grayscale printing and colour-blind readers.
 */

import type { Verdict } from "@/lib/api";

export const VERDICT_LABEL: Record<Verdict, string> = {
  supported: "Supported",
  partially: "Partly supported",
  unsupported: "Unsupported",
};

const VERDICT_TITLE: Record<Verdict, string> = {
  supported: "Supported by its citation",
  partially: "Partly supported by its citation",
  unsupported: "Not supported by its citation",
};

const VERDICT_COLOR: Record<Verdict, string> = {
  supported: "var(--color-pass)",
  partially: "var(--color-partial)",
  unsupported: "var(--color-fail)",
};

/** Solid, half, or hollow square. `null` is a sentence nobody checked. */
export function VerificationMark({
  verdict,
  size = 12,
}: {
  verdict: Verdict | null;
  size?: number;
}) {
  if (!verdict) {
    return (
      <svg width={size} height={size} viewBox="0 0 12 12" role="img" aria-label="Not checked" className="shrink-0">
        <title>Not checked: asserts nothing to verify</title>
        <path d="M2.5 6h7" stroke="var(--color-rule-strong)" strokeWidth="1.5" strokeLinecap="round" />
      </svg>
    );
  }
  const color = VERDICT_COLOR[verdict];
  return (
    <svg width={size} height={size} viewBox="0 0 12 12" role="img" aria-label={VERDICT_TITLE[verdict]} className="shrink-0">
      <title>{VERDICT_TITLE[verdict]}</title>
      <rect x="0.75" y="0.75" width="10.5" height="10.5" rx="1.5" fill="none" stroke={color} strokeWidth="1.5" />
      {verdict === "supported" && <rect x="3" y="3" width="6" height="6" rx="0.5" fill={color} />}
      {verdict === "partially" && (
        <rect x="3" y="3" width="3" height="6" rx="0.5" fill="var(--color-partial-fill)" />
      )}
    </svg>
  );
}

/** The highlighter wash a focused claim is marked with, per verdict. */
export const VERDICT_WASH: Record<Verdict, string> = {
  supported: "var(--color-pass-wash)",
  partially: "var(--color-partial-wash)",
  unsupported: "var(--color-fail-wash)",
};

/** The verdict in words, beside the claim it judges. */
export function VerdictWord({ verdict }: { verdict: Verdict | null }) {
  if (!verdict) return <span className="machine text-ink-3">Not checked</span>;
  return (
    <span className="machine font-semibold" style={{ color: VERDICT_COLOR[verdict] }}>
      {VERDICT_LABEL[verdict]}
    </span>
  );
}

/**
 * The product's own mark: a sounding line. A lead dropped from the surface
 * until it touches the bottom -- the ground truth the name is about.
 */
export function Wordmark() {
  return (
    <span className="flex items-center gap-2.5">
      <svg width="26" height="28" viewBox="0 0 26 28" aria-hidden="true">
        <path
          d="M1 5c2.2 0 2.2-2 4.4-2s2.2 2 4.4 2 2.2-2 4.4-2 2.2 2 4.4 2 2.2-2 4.4-2"
          fill="none"
          stroke="var(--color-signal)"
          strokeWidth="1.6"
          strokeLinecap="round"
        />
        <path d="M13 5v13" stroke="var(--color-harbor-ink)" strokeWidth="1.2" strokeDasharray="1.6 2" />
        <path d="M13 17.5c-2 2.4-2.6 3.6-2.6 4.8a2.6 2.6 0 0 0 5.2 0c0-1.2-.6-2.4-2.6-4.8z" fill="var(--color-harbor-ink)" />
        <path d="M2 26.5h22" stroke="var(--color-pass-wash)" strokeWidth="1.6" strokeLinecap="round" />
      </svg>
      <span className="hidden min-[440px]:inline serif font-semibold tracking-[-0.01em] text-[21px] text-harbor-ink">
        GroundTruth
      </span>
    </span>
  );
}

/** A small inline bar. Values are 0..1 unless `max` says otherwise. */
export function Meter({
  value,
  max = 1,
  width = 72,
}: {
  value: number | null | undefined;
  max?: number;
  width?: number;
}) {
  if (value == null || Number.isNaN(value)) {
    return <span className="text-ink-3 mono text-xs">—</span>;
  }
  const fraction = Math.max(0, Math.min(1, value / max));
  return (
    <span className="inline-flex items-center gap-2">
      <span className="relative inline-block h-[6px] bg-well rounded-full overflow-hidden" style={{ width }}>
        <span className="absolute inset-y-0 left-0 rounded-full bg-ink-2" style={{ width: `${fraction * 100}%` }} />
      </span>
      <span className="mono text-[13px] tnum text-ink">{value.toFixed(3)}</span>
    </span>
  );
}

/**
 * A signed difference between two runs. It earns colour only when the
 * difference is distinguishable from noise: a green +0.071 on an interval that
 * spans zero is a claim the data does not make.
 */
export function Delta({
  value,
  digits = 3,
  significant = false,
}: {
  value: number | null;
  digits?: number;
  significant?: boolean;
}) {
  if (value == null || Number.isNaN(value)) {
    return <span className="text-ink-3 mono text-[13px]">—</span>;
  }
  if (Math.abs(value) < 1e-9) {
    return <span className="mono text-[13px] tnum text-ink-3">±0</span>;
  }
  const positive = value > 0;
  const color = significant ? (positive ? "var(--color-pass)" : "var(--color-fail)") : "var(--color-ink-2)";
  return (
    <span className="mono text-[13px] tnum" style={{ color, fontWeight: significant ? 700 : 400 }}>
      {positive ? "+" : "−"}
      {Math.abs(value).toFixed(digits)}
    </span>
  );
}

/**
 * One label/value pair in a run's details line. Mono is for measurements and
 * identifiers; a sentence (`prose`) stays in the text face.
 */
export function Field({
  label,
  children,
  prose = false,
}: {
  label: string;
  children: React.ReactNode;
  prose?: boolean;
}) {
  return (
    <div className="flex flex-col gap-0.5 min-w-0">
      <dt className="text-[13px] text-ink-3">{label}</dt>
      <dd className={prose ? "text-[15px] leading-snug text-ink" : "mono text-[14px] text-ink tnum"}>{children}</dd>
    </div>
  );
}

export function EmptyState({ title, children }: { title: string; children?: React.ReactNode }) {
  return (
    <div className="sheet px-6 py-12 text-center">
      <p className="serif text-ink font-medium text-[24px]">{title}</p>
      {children && <div className="mt-2 text-ink-2 max-w-prose mx-auto">{children}</div>}
    </div>
  );
}

export function ErrorNote({ message }: { message: string }) {
  return (
    <div
      role="alert"
      className="flex items-start gap-3 rounded-md border px-4 py-3 text-ink"
      style={{ borderColor: "var(--color-fail)", backgroundColor: "var(--color-fail-wash)" }}
    >
      <span className="pt-1.5">
        <VerificationMark verdict="unsupported" />
      </span>
      <span>{message}</span>
    </div>
  );
}

export function Spinner({ label }: { label: string }) {
  return (
    <div className="flex items-center gap-3 text-ink-2" role="status">
      <svg width="16" height="16" viewBox="0 0 16 16" className="animate-spin" aria-hidden="true">
        <circle cx="8" cy="8" r="6.5" fill="none" stroke="var(--color-rule)" strokeWidth="2" />
        <path d="M14.5 8A6.5 6.5 0 0 0 8 1.5" fill="none" stroke="var(--color-ink)" strokeWidth="2" strokeLinecap="round" />
      </svg>
      {label}
    </div>
  );
}

// A no-break space: "2.72" and "s" on separate lines read as two values.
const NBSP = String.fromCharCode(160);

export function formatMs(ms: number): string {
  if (ms >= 1000) return `${(ms / 1000).toFixed(2)}${NBSP}s`;
  return `${Math.round(ms)}${NBSP}ms`;
}

export function formatCost(usd: number | null | undefined): string {
  if (usd == null) return "—";
  if (usd === 0) return "$0";
  if (usd < 0.01) return `$${usd.toFixed(4)}`;
  return `$${usd.toFixed(2)}`;
}

export function formatTime(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/**
 * "23 Sep 2026", in UTC. Not `toLocaleDateString`: the server and the browser
 * disagree about locale and timezone, and a date rendered on both is a
 * hydration mismatch.
 */
export function formatDate(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return `${date.getUTCDate()} ${MONTHS[date.getUTCMonth()]} ${date.getUTCFullYear()}`;
}

/**
 * A heading path as a reader should see it. Hugo headings carry explicit
 * anchors -- `Termination of Pods {#pod-termination}` -- which the parser keeps
 * in the stored path; they are markup, not part of the title. So are the
 * backticks around a field name in a heading.
 */
export function cleanHeading(path: string | null | undefined): string {
  return (path ?? "").replace(/\s*\{#[^}]*\}/g, "").replace(/`/g, "");
}
