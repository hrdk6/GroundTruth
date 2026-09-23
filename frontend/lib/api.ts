/**
 * Typed client for the GroundTruth API.
 *
 * Requests go to /api/*, which next.config.mjs rewrites to the backend. That
 * keeps the browser on one origin, so there is no CORS preflight per request.
 */

export interface Citation {
  marker: number;
  chunk_id: number;
  source_path: string;
  version: string;
  heading_path: string;
  title: string;
  url: string;
  text: string;
  score: number;
}

export type Verdict = "supported" | "partially" | "unsupported";

export interface SentenceVerification {
  sentence: string;
  citations: number[];
  verdict: Verdict;
  reason: string;
}

export interface Verification {
  support_fraction?: number;
  citation_precision?: number | null;
  checked?: number;
  skipped_non_factual?: number;
  unsupported_count?: number;
  sentences?: SentenceVerification[];
}

/**
 * One sentence of the answer, split by the server with the same function the
 * verifier used. Rendering these -- instead of re-splitting `answer` in the
 * browser -- is what keeps each verdict attached to the sentence it judged.
 */
export interface Segment {
  text: string;
  citations: number[];
  factual: boolean;
  verdict: Verdict | null;
  reason: string;
}

export interface Conflict {
  source_path: string;
  heading_path: string;
  latest_version: string;
  other_version: string;
  latest_text: string;
  other_text: string;
  similarity: number;
}

export interface QueryResponse {
  answer: string;
  segments: Segment[];
  citations: Citation[];
  version_used: string | null;
  version_reason: string;
  conflicts: Conflict[];
  conflict_note: string;
  verification: Verification;
  abstained: boolean;
  regenerated: boolean;
  config_name: string;
  trace_id: string | null;
  latency_ms: number;
  cost_usd: number;
}

export interface TraceSummary {
  trace_id: string;
  question: string;
  config_name: string;
  version_used: string | null;
  abstained: boolean;
  status: string;
  latency_ms: number;
  cost_usd: number;
  started_at: string;
  span_count: number;
  feedback: boolean | null;
}

export interface Span {
  span_id: string;
  parent_span_id: string | null;
  name: string;
  sequence: number;
  started_at: string;
  ended_at: string | null;
  duration_ms: number;
  status: string;
  input: Record<string, unknown>;
  output: Record<string, unknown>;
  attributes: Record<string, unknown>;
}

export interface TraceDetail extends TraceSummary {
  answer: string;
  meta: Record<string, unknown>;
  spans: Span[];
}

/** A 95% percentile-bootstrap interval over per-item scores. */
export interface Interval {
  mean: number;
  low: number;
  high: number;
  n: number;
}

/** Whether every gold quote could match some indexed chunk at all. */
export interface Integrity {
  evidence_total: number;
  evidence_matchable: number;
  recall_ceiling: number;
}

export interface ExperimentSummary {
  id: string;
  config_name: string;
  split: string;
  mode: string;
  timestamp: string;
  git_sha: string | null;
  git_dirty: boolean | null;
  dataset_version: string | null;
  dataset_size: number | null;
  metrics: Record<string, number | null>;
  confidence: Record<string, Interval>;
  integrity: Partial<Integrity>;
  cost_usd: number | null;
  /** A pre-audit record from experiments/superseded/: history, not a result. */
  superseded: boolean;
}

export interface ExperimentDetail extends ExperimentSummary {
  dataset_size: number;
  config: { name: string; config_hash: string; chunker_name: string; values: unknown };
  metrics_by_category: Record<string, Record<string, number | null>>;
  attribution: Record<string, number>;
  latency: { p50_ms: number; p95_ms: number; total_seconds: number };
  cost: { cost_usd: number; calls: number; cached_calls: number };
  items: ExperimentItem[];
}

export interface ExperimentItem {
  item_id: string;
  category: string;
  question: string;
  answerable: boolean;
  best_rank: number | null;
  "recall@5": number;
  "recall@10": number;
  mrr: number;
  "ndcg@10": number;
  answer: string;
  abstained: boolean;
  judge_passed: boolean | null;
  judge_score: number | null;
  attribution: string;
  attribution_detail: string;
  latency_ms: number;
}

/** B minus A for one metric, paired by item. */
export interface PairedMetric {
  metric: string;
  n: number;
  mean_a: number;
  mean_b: number;
  delta: number;
  low: number;
  high: number;
  p_value: number;
  wins: number;
  losses: number;
  distinguishable: boolean;
}

export interface Comparison {
  a: string;
  b: string;
  method: string;
  metrics: PairedMetric[];
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`/api${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...init?.headers },
      cache: "no-store",
    });
  } catch {
    // A network failure here almost always means the backend is not running,
    // so say that rather than surfacing "Failed to fetch".
    throw new ApiError("Can't reach the API. Is the backend running?", 0);
  }

  if (!response.ok) {
    let detail: string | null = null;
    try {
      const body = await response.json();
      if (typeof body?.detail === "string") detail = body.detail;
    } catch {
      /* no JSON body — fall through to a status-based message */
    }

    if (!detail) {
      // The dev proxy answers 502/504 when nothing is listening on the backend
      // port, and Next turns some of those into a bare 500. "500 Internal
      // Server Error" tells the reader nothing they can act on.
      detail =
        response.status >= 500
          ? "The API didn't respond. Start the backend with `make up`, or `make dev` to run it without containers."
          : `${response.status} ${response.statusText}`;
    }
    throw new ApiError(detail, response.status);
  }

  return response.json() as Promise<T>;
}

export const api = {
  health: () => request<Record<string, unknown>>("/health"),

  versions: () =>
    request<{ versions: string[]; configs: string[]; default_config: string }>("/versions"),

  query: (body: { question: string; version?: string | null; config_name?: string | null }) =>
    request<QueryResponse>("/query", { method: "POST", body: JSON.stringify(body) }),

  feedback: (body: { trace_id: string; helpful: boolean; comment?: string }) =>
    request<{ status: string }>("/feedback", { method: "POST", body: JSON.stringify(body) }),

  traces: (limit = 50) => request<TraceSummary[]>(`/traces?limit=${limit}`),

  trace: (id: string) => request<TraceDetail>(`/traces/${id}`),

  experiments: (includeSuperseded = false) =>
    request<ExperimentSummary[]>(
      `/experiments${includeSuperseded ? "?include_superseded=true&limit=100" : ""}`,
    ),

  experiment: (id: string) => request<ExperimentDetail>(`/experiments/${id}`),

  compare: (a: string, b: string) =>
    request<Comparison>(
      `/experiments/compare?a=${encodeURIComponent(a)}&b=${encodeURIComponent(b)}`,
    ),
};
