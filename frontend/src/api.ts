// Typed access to the ReliabilityML API. Report shapes mirror src/reliabilityml/pipelines/build.py.

export type Category =
  | "database_saturation"
  | "deployment_regression"
  | "traffic_spike"
  | "memory_leak"
  | "dependency_failure"
  | "unknown";

export interface IncidentRow {
  id: string;
  fired_at: string;
  ended_at: string;
  service: string;
  signal: string;
  peak_z: number;
  predicted: Category;
  confidence: number;
  rules_prediction: Category;
  status: string;
  actual: Category | "false alert";
  unknown_reason: string | null;
}

export interface Explanation {
  probabilities: Record<string, number>;
  confidence: number;
  novelty: number;
  novelty_threshold: number;
  confidence_threshold: number;
  unknown_reason: string | null;
}

export interface Span {
  span_id: string;
  name: string;
  service: string;
  start_ms: number;
  end_ms: number;
  self_ms: number;
  self_delta_ms: number;
  children: Span[];
}

export type Series = { ts: string[] } & Record<string, number[] | string[]>;

export interface IncidentDetail extends Omit<IncidentRow, "actual" | "unknown_reason"> {
  started_at: string;
  explanation: Explanation;
  rules_reason: string;
  evidence: { signal: string; value: string; notable: boolean }[];
  ground_truth: { fault_id: string; category: Category; service: string; start: string; end: string; detail: string } | null;
  series: Record<string, Series>;
  trace: { trace_id: string; root: Span; slowest_added: string };
  logs: { ts: string; service: string; level: string; message: string }[];
  deploys: { ts: string; service: string; version: string; kind: string }[];
}

export interface CurvePoint {
  threshold: number;
  consecutive: number;
  recall: number;
  median_ttd_min: number | null;
  false_alerts_per_day: number;
  precision: number;
  alerts: number;
}

export interface Summary {
  detection: { recall: number; median_ttd_min: number; false_alerts_per_day: number; precision: number;
    isolation_forest: { recall: number; median_ttd_min: number; false_alerts_per_day: number; precision: number } };
  classifier: Record<string, number | string | null>;
  slo: Record<string, { availability: number; budget_remaining: number }>;
  rag: { embedder: string; hit_at_3: number; hit_at_1: number; correct_refusal_rate: number;
    false_refusal_rate: number; citation_accuracy: number; questions: number };
  drift: { max_psi: number; drift_detected: boolean; promoted: boolean; candidate_macro_f1: number | null;
    production_macro_f1: number | null };
  gate: Record<string, boolean>;
  model: { version: number; name: string; registered_at: string; run_id: string };
}

export interface SloStatus {
  slo: string;
  target: number;
  sli: number;
  budget_consumed: number;
  budget_remaining: number;
  budget_minutes_total: number;
  budget_minutes_left: number;
  burn_rates: Record<string, number>;
}

export interface GateCheck { name: string; passed: boolean; measured: string; threshold: string; detail: string }
export interface GateResult { candidate: Record<string, unknown>; passed: boolean; checks: GateCheck[] }

export interface AskResult {
  question: string;
  refused: boolean;
  answer: string;
  sentences: { text: string; citations: string[] }[];
  citations: { chunk_id: string; title: string; section: string }[];
  retrieved: { chunk_id: string; title: string; section: string; score: number }[];
  engine: string;
  prompt_version: string | null;
  note: string;
}

export class ApiError extends Error {
  constructor(public status: number, public detail: unknown) {
    super(typeof detail === "string" ? detail : `Request failed (${status})`);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, { ...init, headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) } });
  if (!response.ok) {
    let detail: unknown = response.statusText;
    try {
      detail = (await response.json()).detail;
    } catch {
      /* not JSON */
    }
    throw new ApiError(response.status, detail);
  }
  return response.json() as Promise<T>;
}

export const api = {
  get: <T,>(path: string) => request<T>(path),
  post: <T,>(path: string, body: unknown) => request<T>(path, { method: "POST", body: JSON.stringify(body) }),
};

// Any report: the pages narrow the parts they use.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export type Report = any;
