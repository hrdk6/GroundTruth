/**
 * Shared readout primitives.
 *
 * The verification mark encodes state by SHAPE (filled / half / hollow) rather
 * than by three different hues. A long answer can carry twenty of these, and
 * twenty coloured badges would read as decoration; twenty small marks in a
 * margin read as a measurement. Red is reserved for the one state that is
 * actually a problem.
 */

import type { Verdict } from "@/lib/api";

export function VerificationMark({ verdict, size = 10 }: { verdict: Verdict; size?: number }) {
  const label = {
    supported: "Supported by its citation",
    partially: "Partly supported by its citation",
    unsupported: "Not supported by its citation",
  }[verdict];

  const stroke = verdict === "unsupported" ? "var(--color-alarm)" : "var(--color-ok)";

  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 10 10"
      role="img"
      aria-label={label}
      className="shrink-0"
    >
      <title>{label}</title>
      <rect x="0.75" y="0.75" width="8.5" height="8.5" fill="none" stroke={stroke} strokeWidth="1.5" />
      {verdict === "supported" && <rect x="2.5" y="2.5" width="5" height="5" fill={stroke} />}
      {verdict === "partially" && <rect x="2.5" y="2.5" width="2.5" height="5" fill={stroke} />}
    </svg>
  );
}

/** A small inline bar. Values are 0..1 unless `max` says otherwise. */
export function Meter({
  value,
  max = 1,
  width = 64,
  tone = "brass",
}: {
  value: number | null | undefined;
  max?: number;
  width?: number;
  tone?: "brass" | "ok" | "alarm" | "mute";
}) {
  if (value == null || Number.isNaN(value)) {
    return <span className="text-dim mono text-xs">—</span>;
  }
  const fraction = Math.max(0, Math.min(1, value / max));
  const color = {
    brass: "var(--color-brass)",
    ok: "var(--color-ok)",
    alarm: "var(--color-alarm)",
    mute: "var(--color-mute)",
  }[tone];

  return (
    <span className="inline-flex items-center gap-2">
      <span
        className="relative inline-block h-[6px] bg-line rounded-[1px] overflow-hidden"
        style={{ width }}
      >
        <span
          className="absolute inset-y-0 left-0 rounded-[1px]"
          style={{ width: `${fraction * 100}%`, backgroundColor: color }}
        />
      </span>
      <span className="mono text-xs tnum text-text">{value.toFixed(3)}</span>
    </span>
  );
}

/** Signed delta between two runs. Colour here IS the signal, so it earns it. */
export function Delta({ value, digits = 3 }: { value: number | null; digits?: number }) {
  if (value == null || Number.isNaN(value)) {
    return <span className="text-dim mono text-xs">—</span>;
  }
  if (Math.abs(value) < 1e-9) {
    return <span className="mono text-xs tnum text-dim">±0</span>;
  }
  const positive = value > 0;
  return (
    <span
      className="mono text-xs tnum"
      style={{ color: positive ? "var(--color-ok)" : "var(--color-alarm)" }}
    >
      {positive ? "+" : "−"}
      {Math.abs(value).toFixed(digits)}
    </span>
  );
}

export function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-[11px] text-dim">{label}</span>
      <span className="mono text-xs text-text tnum">{children}</span>
    </div>
  );
}

export function EmptyState({
  title,
  children,
}: {
  title: string;
  children?: React.ReactNode;
}) {
  return (
    <div className="border border-line rounded-sm bg-panel/40 px-6 py-10 text-center">
      <p className="text-text font-medium">{title}</p>
      {children && <div className="mt-2 text-sm text-mute max-w-prose mx-auto">{children}</div>}
    </div>
  );
}

export function ErrorNote({ message }: { message: string }) {
  return (
    <div
      role="alert"
      className="border-l-2 px-4 py-3 text-sm bg-panel"
      style={{ borderColor: "var(--color-alarm)" }}
    >
      {message}
    </div>
  );
}

export function Spinner({ label }: { label: string }) {
  return (
    <div className="flex items-center gap-3 text-sm text-mute" role="status">
      <svg width="14" height="14" viewBox="0 0 14 14" className="animate-spin" aria-hidden="true">
        <circle cx="7" cy="7" r="6" fill="none" stroke="var(--color-line-bright)" strokeWidth="2" />
        <path d="M13 7a6 6 0 0 0-6-6" fill="none" stroke="var(--color-brass)" strokeWidth="2" />
      </svg>
      {label}
    </div>
  );
}

export function formatMs(ms: number): string {
  if (ms >= 1000) return `${(ms / 1000).toFixed(2)}s`;
  return `${Math.round(ms)}ms`;
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
