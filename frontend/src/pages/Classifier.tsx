import { useQuery } from "@tanstack/react-query";
import { api, type Report } from "../api";
import { Bar, Heatmap } from "../components/charts";
import { Failed, Loading, PageHeader, Tag, useReport } from "../components/ui";
import { CATEGORY_LABEL, num, pct } from "../format";

export function Classifier() {
  const q = useReport("classifier");
  const reg = useQuery({ queryKey: ["registry"], queryFn: () => api.get<Report>("/api/registry") });
  if (q.isLoading) return <Loading />;
  if (!q.data) return <Failed error={q.error} />;
  const r = q.data as Report;
  const m = r.test_alerts.model;
  const b = r.test_alerts.rules;
  const classes = Object.keys(r.test_windows.model.per_class);
  const importance = Object.entries(r.importance as Record<string, number>).slice(0, 10);
  const maxImp = Math.max(...importance.map(([, v]) => v));

  return (
    <div className="page">
      <PageHeader
        title="Classifier: naming the likely cause"
        lede={`A ${r.model.kind.replace("_", " ")} trained on ${r.training.windows} labelled incident windows from four failure types, compared with hand-written rules taken straight from the runbooks. When the model is unsure, or the window looks unlike anything it trained on, it says "unknown".`}
      />
      <section className="results three">
        <article>
          <p className="k">Macro F1, test windows</p>
          <p className="v">{num(r.test_windows.model.macro_f1)}</p>
          <p className="n">rules baseline {num(r.test_windows.rules.macro_f1)}</p>
        </article>
        <article>
          <p className="k">Macro F1, real alerts</p>
          <p className="v">{num(m.macro_f1)}</p>
          <p className="n">
            rules baseline {num(b.macro_f1)} on the same {r.test_alerts.alerts} alerts
          </p>
        </article>
        <article>
          <p className="k">Held-out memory leaks</p>
          <p className="v">
            {r.held_out.model_unknown} of {r.held_out.alerts}
          </p>
          <p className="n">
            routed to unknown; the rules labelled {r.held_out.rules_memory_leak} correctly and sent {r.held_out.rules_unknown} to unknown
          </p>
        </article>
      </section>

      <div className="split">
        <section className="panel">
          <h2>Per class: model vs. rules (test windows)</h2>
          <table className="table">
            <thead>
              <tr>
                <th scope="col">Category</th>
                <th scope="col" className="num">Model F1</th>
                <th scope="col" className="num">Rules F1</th>
                <th scope="col" className="num">Model recall</th>
                <th scope="col" className="num">Support</th>
              </tr>
            </thead>
            <tbody>
              {classes.map((c) => (
                <tr key={c}>
                  <th scope="row">
                    <Tag category={c} />
                  </th>
                  <td className="num strong">{num(r.test_windows.model.per_class[c].f1)}</td>
                  <td className="num">{num(r.test_windows.rules.per_class[c]?.f1)}</td>
                  <td className="num">{pct(r.test_windows.model.per_class[c].recall)}</td>
                  <td className="num">{r.test_windows.model.per_class[c].support}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="muted small">
            Where the rules lose: traffic spikes and database saturation both raise latency and connections, so a single threshold
            cannot separate them; the model uses the combination of signals.
          </p>
        </section>
        <section className="panel">
          <h2>Confusion matrix, test-week alerts</h2>
          <Heatmap labels={m.confusion.labels.map((l: string) => CATEGORY_LABEL[l])} matrix={m.confusion.matrix} rowTitle="Actual" colTitle="Predicted" />
        </section>
      </div>

      <div className="split">
        <section className="panel">
          <h2>What drives the predictions</h2>
          <ul className="probs">
            {importance.map(([name, v]) => (
              <li key={name}>
                <span className="mono small">{name}</span>
                <Bar value={v} max={maxImp} className="s-0" />
                <span className="num">{num(v, 3)}</span>
              </li>
            ))}
          </ul>
        </section>
        <section className="panel">
          <h2>How "unknown" is decided</h2>
          <p>
            Two guards, both tuned on validation data only. The confidence cut-off ({pct(r.model.unknown_threshold)}) was chosen so
            that harmless noise alerts fall into unknown. The novelty guard measures distance to the nearest training windows and
            rejects anything beyond the 99th percentile ({num(r.model.novelty_threshold, 1)}).
          </p>
          <p className="muted small">
            Confidence alone was not enough: without the novelty guard, the forest labelled most memory-leak windows as traffic
            spikes with high confidence.
          </p>
          <h3>Model selection (validation, days 22–30)</h3>
          <table className="table compact">
            <thead>
              <tr>
                <th scope="col">Candidate</th>
                <th scope="col" className="num">Macro F1 incl. unknown</th>
              </tr>
            </thead>
            <tbody>
              {r.selection.map((s: Report, i: number) => (
                <tr key={i} className={s.kind === r.model.kind && JSON.stringify(s.params) === JSON.stringify(r.model.params) ? "chosen" : ""}>
                  <th scope="row">
                    {s.kind.replace("_", " ")} <span className="muted small">{Object.entries(s.params).map(([k, v]) => `${k}=${v}`).join(", ")}</span>
                  </th>
                  <td className="num">{num(s.val_macro_f1_with_unknown)}</td>
                </tr>
              ))}
              <tr>
                <th scope="row">rules baseline</th>
                <td className="num">{num(r.rules_validation.macro_f1)}</td>
              </tr>
            </tbody>
          </table>
        </section>
      </div>

      {reg.data && (
        <section className="panel">
          <h2>Model registry</h2>
          <table className="table">
            <thead>
              <tr>
                <th scope="col">Version</th>
                <th scope="col">Run</th>
                <th scope="col">Alias</th>
                <th scope="col">Registered</th>
                <th scope="col">Git commit</th>
              </tr>
            </thead>
            <tbody>
              {reg.data.versions.map((v: Report) => (
                <tr key={v.version}>
                  <th scope="row">v{v.version}</th>
                  <td>{v.name}</td>
                  <td>{Object.entries(reg.data.aliases).filter(([, ver]) => ver === v.version).map(([a]) => a).join(", ") || "–"}</td>
                  <td>{v.registered_at.slice(0, 16).replace("T", " ")}</td>
                  <td className="mono">{v.tags.git_sha}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="muted small">Runs are logged to MLflow with parameters, metrics, the confusion matrix, feature importance and the model file. The API serves the version holding the production alias.</p>
        </section>
      )}
    </div>
  );
}
