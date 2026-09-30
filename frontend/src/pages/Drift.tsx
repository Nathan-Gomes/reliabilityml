import type { Report } from "../api";
import { Bar } from "../components/charts";
import { Failed, Loading, PageHeader, Tag, Verdict, useReport } from "../components/ui";
import { num, pct } from "../format";

export function Drift() {
  const q = useReport("drift");
  if (q.isLoading) return <Loading />;
  if (!q.data) return <Failed error={q.error} />;
  const r = q.data as Report;
  const d = r.decision;
  const prod = r.comparison.production;
  const cand = r.comparison.candidate;
  const top = r.signals.slice(0, 12);
  const maxPsi = Math.max(...top.map((s: Report) => s.psi));

  return (
    <div className="page">
      <PageHeader
        title="Drift and gated retraining"
        lede="A simulated platform upgrade permanently shifts the baselines: latency up 20% and a different request mix. The pipeline measures drift with the Population Stability Index, retrains on fresh labelled data, and promotes the new model only if it beats production on macro F1 without raising false alarms."
      />

      <ol className="cycle" aria-label="One full retraining cycle">
        <li className="done">
          <span className="step-k">Drift check</span>
          <strong>PSI {num(r.max_psi, 1)}</strong>
          <span>{r.significant_signals} signals above the 0.25 "significant" line</span>
        </li>
        <li className={d.retrained ? "done" : ""}>
          <span className="step-k">Retrain</span>
          <strong>{d.retrained ? `candidate v${d.candidate_version}` : "not needed"}</strong>
          <span>{r.evaluation.retrain_windows} new windows from the first days after the upgrade</span>
        </li>
        <li className={d.retrained ? "done" : ""}>
          <span className="step-k">Compare</span>
          <strong>
            F1 {num(cand?.macro_f1)} vs {num(prod?.macro_f1)}
          </strong>
          <span>same {r.evaluation.holdout_windows} held-out windows from later days</span>
        </li>
        <li className={d.promoted ? "done" : "stopped"}>
          <span className="step-k">Decision</span>
          <strong>{d.promoted ? "Promoted to production" : "Rejected, stays in staging"}</strong>
          <span>production remains v{d.production_version}</span>
        </li>
      </ol>

      <div className="split">
        <section className="panel">
          <h2>Largest shifts (PSI vs. training)</h2>
          <ul className="probs">
            {top.map((s: Report) => (
              <li key={s.signal + s.service}>
                <span>
                  {s.service} <span className="muted small">{s.signal.replace("_", " ")}</span>
                </span>
                <Bar value={Math.log1p(s.psi)} max={Math.log1p(maxPsi)} className={s.level === "significant" ? "s-1" : "s-0"} />
                <span className="num">{num(s.psi, 2)}</span>
              </li>
            ))}
          </ul>
          <p className="muted small">Rule of thumb: below 0.1 stable, 0.1–0.25 moderate, above 0.25 significant. Bars use a log scale.</p>
        </section>
        <div className="stack">
          <section className="panel">
            <h2>Promotion rule</h2>
            <ul className="checks">
              <li>
                <Verdict ok={!!d.better_macro_f1}>Better macro F1</Verdict>
                <span className="muted small">
                  {num(cand?.macro_f1, 3)} vs {num(prod?.macro_f1, 3)}
                </span>
              </li>
              <li>
                <Verdict ok={!!d.false_alarm_not_worse}>False-alarm rate not worse</Verdict>
                <span className="muted small">
                  {pct(cand?.false_alarm_rate)} vs {pct(prod?.false_alarm_rate)}
                </span>
              </li>
            </ul>
            <p>
              {d.promoted
                ? "Both conditions held, so the candidate took the production alias."
                : `The candidate tied production rather than beating it, so it was not promoted. The raw signals drifted a lot (${r.significant_signals} above 0.25), but the classifier's features are measured against each window's own recent baseline: their median PSI was ${num(r.feature_psi_median, 2)}, with ${r.feature_psi_significant} of ${r.feature_psi.length} above 0.25. The model's inputs barely moved, so retraining had little to fix.`}
            </p>
          </section>
          <section className="panel">
            <h2>Model health across the upgrade</h2>
            <p className="muted small">Same window type on both sides: known-category incident windows.</p>
            <dl className="facts">
              <div>
                <dt>Mean confidence, before the upgrade</dt>
                <dd>{pct(r.model_health.mean_confidence_before)}</dd>
              </div>
              <div>
                <dt>Mean confidence, after upgrade</dt>
                <dd>{pct(r.model_health.mean_confidence_after_upgrade)}</dd>
              </div>
              <div>
                <dt>Share routed to unknown, before → after</dt>
                <dd>
                  {pct(r.model_health.unknown_share_before)} → {pct(r.model_health.unknown_share_after_upgrade)}
                </dd>
              </div>
            </dl>
          </section>
          {prod && (
            <section className="panel">
              <h2>Per class on held-out windows</h2>
              <table className="table compact">
                <thead>
                  <tr>
                    <th scope="col">Category</th>
                    <th scope="col" className="num">Production F1</th>
                    <th scope="col" className="num">Candidate F1</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.keys(prod.per_class).map((c) => (
                    <tr key={c}>
                      <th scope="row">
                        <Tag category={c} />
                      </th>
                      <td className="num">{num(prod.per_class[c].f1)}</td>
                      <td className="num">{num(cand.per_class[c]?.f1)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </section>
          )}
        </div>
      </div>
    </div>
  );
}
