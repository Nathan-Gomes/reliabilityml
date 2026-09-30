import type { Category } from "./api";

export const CATEGORY_LABEL: Record<string, string> = {
  database_saturation: "Database saturation",
  deployment_regression: "Deployment regression",
  traffic_spike: "Traffic spike",
  memory_leak: "Memory leak",
  dependency_failure: "Dependency failure",
  unknown: "Unknown",
  "false alert": "False alert",
};

export const CATEGORIES: Category[] = [
  "database_saturation",
  "deployment_regression",
  "traffic_spike",
  "memory_leak",
  "dependency_failure",
  "unknown",
];

export const pct = (v: number | null | undefined, digits = 0) =>
  v === null || v === undefined || Number.isNaN(v) ? "–" : `${(v * 100).toFixed(digits)}%`;

export const num = (v: number | null | undefined, digits = 2) =>
  v === null || v === undefined || Number.isNaN(v) ? "–" : v.toFixed(digits);

export function minutes(v: number | null | undefined): string {
  if (v === null || v === undefined) return "–";
  if (v >= 90) return `${(v / 60).toFixed(1)} h`;
  return `${v % 1 === 0 ? v.toFixed(0) : v.toFixed(1)} min`;
}

// Timestamps are simulated wall-clock times without a zone; show them as written.
export function when(iso: string, withDate = true): string {
  const d = new Date(iso.replace(" ", "T"));
  const time = d.toLocaleTimeString("en-CA", { hour: "2-digit", minute: "2-digit", hour12: false });
  if (!withDate) return time;
  return `${d.toLocaleDateString("en-CA", { month: "short", day: "numeric" })}, ${time}`;
}

export const serviceShort = (s: string) => s.replace("-service", "").replace("web-", "");
