import type { CurvePoint, Report } from "../api";
import { CurvePlot } from "../components/charts";
import { Failed, Loading, PageHeader, Tag, useReport } from "../components/ui";
import { CATEGORY_LABEL, minutes, num, pct } from "../format";

export function Detection() {
  const q = useReport("detection");
  if (q.isLoading) return <Loading />;
  if (!q.data) return <Failed error={q.error} />;
  const r = q.data as Report;
  const op = r.operating_point;
  const curve = (points: CurvePoint[], n: number) => points.filter((p) => p.consecutive === n && p.median_ttd_min !== null);
  const groups = [1, 2, 3].map((n) => ({
    name: `EWMA, ${n} consecutive min`,
    className: `s-${n - 1}`,
    points: curve(r.curves.ewma_test, n).map((p) => ({ x: p.median_ttd_min as number, y: p.precision })),
    tips: curve(r.curves.ewma_test, n).map((p) => `z > ${p.threshold}: precision ${pct(p.precision)}, recall ${pct(p.recall)}, TTD ${minutes(p.median_ttd_min)}, ${num(p.false_alerts_per_day, 1)} false/day`),
  }));
  const chosen = r.curves.ewma_test.find((p: CurvePoint) => p.threshold === op.threshold && p.consecutive === op.consecutive);
  const t = r.test;
  const iso = r.isolation_forest.test;

  return (
    <div className="page">
      <PageHeader
        title="Detection: deciding when something is wrong"
        lede="Every signal gets a robust EWMA baseline built only from earlier minutes. An alert fires when a z-score stays above the threshold for N consecutive minutes. Lower thresholds alert sooner and cry wolf more often; the curve shows that trade-off on the held-out test week."
      />
      <div className="split">
        <section className="panel">
          <h2>Precision vs. time to detect</h2>
          <CurvePlot
            groups={groups}
            xLabel="Median minutes from fault start to first alert"
            yLabel="Alert precision"
            marker={chosen ? { x: chosen.median_ttd_min, y: chosen.precision, label: `chosen: z > ${op.threshold}, ${op.consecutive} min` } : undefined}
          />
          <ul className="legend">
            {groups.map((g) => (
              <li key={g.name}>
                <i className={`key ${g.className}`} /> {g.name}
              </li>
            ))}
          </ul>
          <p className="muted small">The operating point was chosen on the 30 training days: fewest false alerts with recall of at least 93%.</p>
        </section>
        <div className="stack">
          <section className="panel">
            <h2>Test week at the chosen setting</h2>
            <dl className="facts big">
              <div>
                <dt>Incidents detected</dt>
                <dd>
                  {t.detected} of {t.incidents} ({pct(t.recall)})
                </dd>
              </div>
              <div>
                <dt>Median time to detect</dt>
                <dd>{minutes(t.median_ttd_min)}</dd>
              </div>
              <div>
                <dt>False alerts per day</dt>
                <dd>{num(t.false_alerts_per_day, 2)}</dd>
              </div>
              <div>
                <dt>Alert precision</dt>
                <dd>{pct(t.precision)}</dd>
              </div>
            </dl>
          </section>
          <section className="panel">
            <h2>EWMA vs. Isolation Forest</h2>
            <table className="table">
              <thead>
                <tr>
                  <th scope="col">Detector</th>
                  <th scope="col" className="num">Recall</th>
                  <th scope="col" className="num">TTD</th>
                  <th scope="col" className="num">False/day</th>
                  <th scope="col" className="num">Precision</th>
                </tr>
              </thead>
              <tbody>
                <tr>
                  <th scope="row">EWMA z-score</th>
                  <td className="num">{pct(t.recall)}</td>
                  <td className="num">{minutes(t.median_ttd_min)}</td>
                  <td className="num">{num(t.false_alerts_per_day, 2)}</td>
                  <td className="num">{pct(t.precision)}</td>
                </tr>
                <tr>
                  <th scope="row">Isolation Forest</th>
                  <td className="num">{pct(iso.recall)}</td>
                  <td className="num">{minutes(iso.median_ttd_min)}</td>
                  <td className="num">{num(iso.false_alerts_per_day, 2)}</td>
                  <td className="num">{pct(iso.precision)}</td>
                </tr>
              </tbody>
            </table>
            <p className="muted small">
              Each detector at its own operating point, chosen the same way on the training days. The forest reacts to combinations of
              moderate deviations: in the test week it caught {pct(iso.recall)} of incidents in {minutes(iso.median_ttd_min)}, but raised{" "}
              {num(iso.false_alerts_per_day, 1)} false alerts a day against {num(t.false_alerts_per_day, 1)} for EWMA. Fewer false pages
              matter more here, so EWMA runs in production.
            </p>
          </section>
        </div>
      </div>
      <section className="panel">
        <h2>By failure type</h2>
        <table className="table">
          <thead>
            <tr>
              <th scope="col">Category</th>
              <th scope="col" className="num">Incidents</th>
              <th scope="col" className="num">Detected</th>
              <th scope="col" className="num">Median TTD</th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(t.per_category as Record<string, { incidents: number; detected: number; median_ttd_min: number | null }>).map(([cat, v]) => (
              <tr key={cat}>
                <th scope="row">
                  <Tag category={cat} />
                </th>
                <td className="num">{v.incidents}</td>
                <td className="num">{v.detected}</td>
                <td className="num">{minutes(v.median_ttd_min)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="muted small">
          {CATEGORY_LABEL.memory_leak} is the honest weak spot: a slow climb looks normal to a baseline that adapts, so it is only
          caught when containers start restarting, hours in. A trend detector on memory would be the next improvement.
        </p>
      </section>
    </div>
  );
}
