import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api, type IncidentRow, type Summary } from "../api";
import { Failed, IncidentStrip, Loading, PageHeader, Tag } from "../components/ui";
import { CATEGORIES, CATEGORY_LABEL, minutes, num, pct, serviceShort, when } from "../format";

export function Overview() {
  const summary = useQuery({ queryKey: ["summary"], queryFn: () => api.get<Summary>("/api/summary") });
  const incidents = useQuery({ queryKey: ["incidents"], queryFn: () => api.get<IncidentRow[]>("/api/incidents") });
  if (summary.isLoading || incidents.isLoading) return <Loading what="Loading results" />;
  if (!summary.data || !incidents.data) return <Failed error={summary.error ?? incidents.error} />;
  const s = summary.data;
  const c = s.classifier as Record<string, number>;
  const rows = incidents.data;
  const correct = rows.filter((r) => r.predicted === r.actual || (r.actual === "memory_leak" && r.predicted === "unknown")).length;

  return (
    <div className="page">
      <PageHeader
        title="Incident intelligence on a simulated five-service system"
        lede="ReliabilityML watches synthetic telemetry, decides when something is wrong, names the likely cause or says it doesn't know, tracks error budgets, and answers troubleshooting questions only from approved runbooks. Every number below is measured on a held-out test week."
      />

      <section className="results" aria-label="Headline results">
        <article>
          <p className="k">Median time to detect</p>
          <p className="v">{minutes(s.detection.median_ttd_min)}</p>
          <p className="n">
            {pct(s.detection.recall)} of incidents caught, {num(s.detection.false_alerts_per_day, 1)} false alerts per day
          </p>
        </article>
        <article>
          <p className="k">Classifier macro F1</p>
          <p className="v">{num(c.test_macro_f1_alerts)}</p>
          <p className="n">vs {num(c.rules_macro_f1_alerts)} for the hand-written rules on the same alerts</p>
        </article>
        <article>
          <p className="k">Unseen failure type</p>
          <p className="v">{pct(c.held_out_share_unknown)}</p>
          <p className="n">of memory-leak alerts routed to "unknown" instead of a confident wrong answer</p>
        </article>
        <article>
          <p className="k">Assistant refusals</p>
          <p className="v">{pct(s.rag.correct_refusal_rate)}</p>
          <p className="n">
            of unanswerable questions refused, {pct(s.rag.false_refusal_rate)} of answerable ones wrongly refused
          </p>
        </article>
      </section>

      <section className="panel">
        <div className="panel-head">
          <h2>The test week, alert by alert</h2>
          <p className="muted">
            {correct} of {rows.length} alerts labelled correctly. Click a mark to open the incident.
          </p>
        </div>
        <IncidentStrip incidents={rows} start="2026-07-31T00:00:00" end="2026-08-07T00:00:00" />
        <ul className="legend">
          {CATEGORIES.map((cat) => (
            <li key={cat}>
              <Tag category={cat} />
            </li>
          ))}
        </ul>
      </section>

      <div className="split">
        <section className="panel">
          <h2>Alert queue</h2>
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th scope="col">Alert</th>
                  <th scope="col">Fired</th>
                  <th scope="col">Where</th>
                  <th scope="col">Model</th>
                  <th scope="col" className="num">
                    Conf.
                  </th>
                  <th scope="col">Actual</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.id}>
                    <th scope="row">
                      <Link to={`/incident/${r.id}`}>{r.id}</Link>
                    </th>
                    <td>{when(r.fired_at)}</td>
                    <td>{serviceShort(r.service)}</td>
                    <td>
                      <Tag category={r.predicted} />
                    </td>
                    <td className="num">{pct(r.confidence)}</td>
                    <td>
                      <Tag category={r.actual} muted />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        <div className="stack">
          <section className="panel">
            <h2>Error budgets, last 30 days</h2>
            <ul className="budgets">
              {Object.entries(s.slo).map(([svc, v]) => (
                <li key={svc}>
                  <span>{svc}</span>
                  <span className="budget-bar" aria-hidden>
                    <span className={v.budget_remaining < 0.25 ? "low" : ""} style={{ width: `${Math.max(0, v.budget_remaining) * 100}%` }} />
                  </span>
                  <span className="num">{pct(v.budget_remaining)} left</span>
                </li>
              ))}
            </ul>
            <Link to="/slos">Burn rates and alerts</Link>
          </section>
          <section className="panel">
            <h2>Model lifecycle</h2>
            <dl className="facts">
              <div>
                <dt>In production</dt>
                <dd>
                  incident-classifier v{s.model.version} ({String(c.model).replace("_", " ")})
                </dd>
              </div>
              <div>
                <dt>Drift check</dt>
                <dd>
                  {s.drift.drift_detected ? `PSI ${num(s.drift.max_psi, 1)}, retrained` : "stable"};{" "}
                  {s.drift.promoted ? "candidate promoted" : "candidate rejected (not better)"}
                </dd>
              </div>
              <div>
                <dt>Deploy gate examples</dt>
                <dd>
                  {Object.values(s.gate).filter(Boolean).length} of {Object.keys(s.gate).length} releases allowed
                </dd>
              </div>
            </dl>
          </section>
          <section className="panel note">
            <h2>How to read this</h2>
            <p>
              The model knows four failure types. {CATEGORY_LABEL.memory_leak} was kept out of training entirely, so the right
              answer for those alerts is "unknown". Open any incident to see the evidence, the rules baseline's call, and a
              cited runbook answer.
            </p>
          </section>
        </div>
      </div>
    </div>
  );
}
