import { useQuery } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";
import { Link, NavLink, Outlet, useLocation } from "react-router-dom";
import { api, type IncidentRow } from "../api";
import { CATEGORY_LABEL, when } from "../format";

const NAV = [
  { to: "/", label: "Overview", end: true },
  { to: "/detection", label: "Detection" },
  { to: "/classifier", label: "Classifier" },
  { to: "/slos", label: "SLOs & budgets" },
  { to: "/assistant", label: "Assistant" },
  { to: "/drift", label: "Drift & retraining" },
  { to: "/gate", label: "Deploy gate" },
];

export function Layout() {
  const [open, setOpen] = useState(false);
  const location = useLocation();
  const health = useQuery({ queryKey: ["health"], queryFn: () => api.get<{ model_version: number; commit: string; assistant_engine: string }>("/health") });
  return (
    <div className="shell">
      <a className="skip" href="#main">
        Skip to content
      </a>
      <header className="topbar">
        <Link to="/" className="brand" aria-label="ReliabilityML home">
          <svg width="26" height="26" viewBox="0 0 32 32" aria-hidden>
            <rect width="32" height="32" rx="7" fill="currentColor" />
            <path d="M5 18h5l3-8 4 13 3-7h7" fill="none" stroke="#fff" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
          ReliabilityML
        </Link>
        <button className="menu" aria-expanded={open} aria-controls="nav" onClick={() => setOpen(!open)}>
          {open ? "Close" : "Menu"}
        </button>
        <nav id="nav" className={open ? "open" : ""} aria-label="Main">
          {NAV.map((n) => (
            <NavLink key={n.to} to={n.to} end={n.end} onClick={() => setOpen(false)}>
              {n.label}
            </NavLink>
          ))}
        </nav>
        <p className="build">
          {health.data ? (
            <>
              Model v{health.data.model_version} in production, build {health.data.commit}
            </>
          ) : (
            "Connecting…"
          )}
        </p>
      </header>
      <main id="main" key={location.pathname}>
        <Outlet />
      </main>
      <footer className="foot">
        All services, telemetry, incidents, runbooks and postmortems are synthetic. <a href="/docs">API docs</a>
        <a href="https://github.com/Nathan-Gomes/reliabilityml">Source on GitHub</a>
      </footer>
    </div>
  );
}

export function PageHeader({ title, lede, children }: { title: string; lede?: ReactNode; children?: ReactNode }) {
  return (
    <header className="page-head">
      <div>
        <h1>{title}</h1>
        {lede && <p className="lede">{lede}</p>}
      </div>
      {children}
    </header>
  );
}

export function Tag({ category, muted = false }: { category: string; muted?: boolean }) {
  return (
    <span className={`tag ${muted ? "muted" : ""}`}>
      <i className={`sw c-${category.replace(" ", "_")}`} aria-hidden />
      {CATEGORY_LABEL[category] ?? category}
    </span>
  );
}

export function Verdict({ ok, children }: { ok: boolean; children: ReactNode }) {
  return <span className={`verdict ${ok ? "ok" : "bad"}`}>{ok ? "✓" : "✕"} {children}</span>;
}

export function Loading({ what = "Loading" }: { what?: string }) {
  return (
    <p className="state" role="status">
      <span className="spinner" aria-hidden />
      {what}…
    </p>
  );
}

export function Failed({ error }: { error: unknown }) {
  return (
    <p className="state error" role="alert">
      Could not load this view: {error instanceof Error ? error.message : "unknown error"}. The free server may still be
      waking up; reload in a few seconds.
    </p>
  );
}

export function useReport(name: string) {
  return useQuery({ queryKey: ["report", name], queryFn: () => api.get<Record<string, unknown>>(`/api/report/${name}`), staleTime: Infinity });
}

/* The incident strip: the test week as a timeline, the model's call on top, ground truth underneath. */
export function IncidentStrip({ incidents, start, end }: { incidents: IncidentRow[]; start: string; end: string }) {
  const t0 = new Date(start).getTime();
  const t1 = new Date(end).getTime();
  const pos = (iso: string) => ((new Date(iso).getTime() - t0) / (t1 - t0)) * 100;
  const days = Array.from({ length: Math.round((t1 - t0) / 86400000) + 1 }, (_, i) => t0 + i * 86400000);
  return (
    <div className="strip" role="group" aria-label="Test-week incidents: prediction above ground truth">
      <div className="strip-row">
        <span className="strip-label">Model says</span>
        <div className="strip-track">
          {incidents.map((i) => (
            <Link
              key={i.id}
              to={`/incident/${i.id}`}
              className={`strip-mark c-${i.predicted}`}
              style={{ left: `${pos(i.fired_at)}%` }}
              title={`${i.id}: predicted ${CATEGORY_LABEL[i.predicted]} (${Math.round(i.confidence * 100)}%), ${when(i.fired_at)}`}
              aria-label={`${i.id}, predicted ${CATEGORY_LABEL[i.predicted]}`}
            />
          ))}
        </div>
      </div>
      <div className="strip-row">
        <span className="strip-label">Actually was</span>
        <div className="strip-track">
          {incidents.map((i) => (
            <span
              key={i.id}
              className={`strip-mark actual c-${i.actual.replace(" ", "_")} ${i.actual === i.predicted || (i.actual === "memory_leak" && i.predicted === "unknown") ? "" : "miss"}`}
              style={{ left: `${pos(i.fired_at)}%` }}
              title={`${i.id}: ground truth ${CATEGORY_LABEL[i.actual]}`}
            />
          ))}
        </div>
      </div>
      <div className="strip-axis" aria-hidden>
        {days.map((d) => (
          <span key={d} style={{ left: `${((d - t0) / (t1 - t0)) * 100}%` }}>
            {new Date(d).toLocaleDateString("en-CA", { month: "short", day: "numeric" })}
          </span>
        ))}
      </div>
    </div>
  );
}
