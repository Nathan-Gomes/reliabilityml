import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { api, type GateResult, type Report } from "../api";
import { Failed, Loading, PageHeader, Verdict, useReport } from "../components/ui";

interface Form {
  version: string;
  latency_multiplier: number;
  extra_error_pp: number;
  model_macro_f1: number | null;
  budget_override: number | null;
  urgent: boolean;
}

const PRESETS: Record<string, { label: string; form: Form }> = {
  clean_release: { label: "Clean release", form: { version: "orders-service 4.18.0", latency_multiplier: 1, extra_error_pp: 0, model_macro_f1: null, budget_override: null, urgent: false } },
  latency_regression: { label: "Slower build (+18% p95)", form: { version: "orders-service 4.18.1", latency_multiplier: 1.18, extra_error_pp: 0, model_macro_f1: null, budget_override: null, urgent: false } },
  error_regression: { label: "Buggy build (+0.6 pp errors)", form: { version: "orders-service 4.18.2", latency_multiplier: 1, extra_error_pp: 0.6, model_macro_f1: null, budget_override: null, urgent: false } },
  weaker_model: { label: "Weaker retrained model", form: { version: "incident-classifier retrain", latency_multiplier: 1, extra_error_pp: 0, model_macro_f1: 0.88, budget_override: null, urgent: false } },
  budget_freeze: { label: "Feature during budget freeze", form: { version: "web-frontend 9.4.0 (feature)", latency_multiplier: 1, extra_error_pp: 0, model_macro_f1: null, budget_override: 0.18, urgent: false } },
  urgent_fix_during_freeze: { label: "Hotfix during freeze", form: { version: "web-frontend 9.4.1 (hotfix)", latency_multiplier: 1, extra_error_pp: 0, model_macro_f1: null, budget_override: 0.18, urgent: true } },
};

export function Gate() {
  const report = useReport("gate");
  const [preset, setPreset] = useState("latency_regression");
  const [form, setForm] = useState<Form>(PRESETS.latency_regression.form);
  const run = useMutation({ mutationFn: (f: Form) => api.post<GateResult>("/api/validate-deployment", f) });
  if (report.isLoading) return <Loading />;
  if (!report.data) return <Failed error={report.error} />;
  const r = report.data as Report;
  const result = run.data ?? (r.examples[preset] as GateResult);

  function choose(name: string) {
    setPreset(name);
    setForm(PRESETS[name].form);
    run.reset();
  }

  return (
    <div className="page">
      <PageHeader
        title="Deployment validation gate"
        lede="The same four checks run from this page, from POST /validate-deployment, and as a GitHub Actions job whose failure blocks the deploy: tests, a canary replay of recorded traffic, the candidate model against production, and the error-budget policy."
      />
      <div className="split">
        <section className="panel">
          <h2>Candidate release</h2>
          <div className="presets" role="radiogroup" aria-label="Example releases">
            {Object.entries(PRESETS).map(([name, p]) => (
              <button key={name} role="radio" aria-checked={preset === name} className={`chip ${preset === name ? "on" : ""}`} onClick={() => choose(name)}>
                {p.label}
              </button>
            ))}
          </div>
          <form
            className="gate-form"
            onSubmit={(e) => {
              e.preventDefault();
              run.mutate(form);
            }}
          >
            <label className="field">
              Release
              <input value={form.version} onChange={(e) => setForm({ ...form, version: e.target.value })} />
            </label>
            <label className="field">
              p95 latency vs. current: {Math.round((form.latency_multiplier - 1) * 100)}%
              <input type="range" min={0.8} max={1.5} step={0.01} value={form.latency_multiplier} onChange={(e) => setForm({ ...form, latency_multiplier: Number(e.target.value) })} />
            </label>
            <label className="field">
              Extra failed requests: {form.extra_error_pp.toFixed(2)} pp
              <input type="range" min={0} max={1} step={0.05} value={form.extra_error_pp} onChange={(e) => setForm({ ...form, extra_error_pp: Number(e.target.value) })} />
            </label>
            <label className="field">
              Error budget remaining (worst service): {form.budget_override === null ? "current" : `${Math.round(form.budget_override * 100)}%`}
              <input type="range" min={0} max={1} step={0.01} value={form.budget_override ?? 0.6} onChange={(e) => setForm({ ...form, budget_override: Number(e.target.value) })} />
            </label>
            <label className="check">
              <input type="checkbox" checked={form.urgent} onChange={(e) => setForm({ ...form, urgent: e.target.checked })} /> Urgent reliability or security fix
            </label>
            <button className="button primary" type="submit" disabled={run.isPending}>
              {run.isPending ? "Running checks…" : "Run the gate"}
            </button>
          </form>
        </section>
        <section className={`panel gate-result ${result.passed ? "pass" : "fail"}`} aria-live="polite">
          <h2>{result.passed ? "Release allowed" : "Deploy blocked"}</h2>
          <p className="muted">{String(result.candidate.version)}</p>
          <ul className="checks">
            {result.checks.map((c) => (
              <li key={c.name}>
                <Verdict ok={c.passed}>{c.name}</Verdict>
                <span>{c.measured}</span>
                <span className="muted small">
                  threshold {c.threshold}. {c.detail}
                </span>
              </li>
            ))}
          </ul>
          {run.isError && <p className="error">{(run.error as Error).message}</p>}
        </section>
      </div>
    </div>
  );
}
