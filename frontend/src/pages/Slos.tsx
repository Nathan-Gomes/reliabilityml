import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api, type Report, type SloStatus } from "../api";
import { LineChart } from "../components/charts";
import { Failed, Loading, PageHeader, useReport } from "../components/ui";
import { CATEGORY_LABEL, num, pct, when } from "../format";

const SERVICES = ["web-frontend", "api-gateway", "orders-service", "auth-service"];

export function Slos() {
  const report = useReport("slo");
  const [service, setService] = useState("web-frontend");
  const detail = useQuery({ queryKey: ["slo", service], queryFn: () => api.get<Report>(`/api/slo/${service}`) });
  if (report.isLoading) return <Loading />;
  if (!report.data) return <Failed error={report.error} />;
  const r = report.data as Report;

  return (
    <div className="page">
      <PageHeader
        title="SLOs and error budgets"
        lede="Availability target 99.9% and latency target 99% of requests under 300 ms, each over a rolling 30 days. Alerts use multi-window burn rates from the Google SRE Workbook: a page fires only when both the long and the short window burn faster than the threshold, so short blips don't wake anyone."
      />
      <p className="note-line">
        Computed on a 30-day production replay with a realistic incident rate ({r.incidents.length} incidents). The training data is
        deliberately incident-dense so the classifier has examples; no real service with that many incidents would meet 99.9%.
      </p>

      <section className="slo-grid" aria-label="Service SLOs">
        {SERVICES.map((svc) => {
          const a: SloStatus = r.services[svc].availability;
          const l: SloStatus = r.services[svc].latency;
          return (
            <button key={svc} className={`slo-card ${svc === service ? "active" : ""}`} onClick={() => setService(svc)} aria-pressed={svc === service}>
              <span className="slo-name">{svc}</span>
              <span className="slo-row">
                <span>Availability</span>
                <strong>{pct(a.sli, 3)}</strong>
              </span>
              <span className="budget-bar" aria-hidden>
                <span className={a.budget_remaining < 0.25 ? "low" : ""} style={{ width: `${Math.max(0, a.budget_remaining) * 100}%` }} />
              </span>
              <span className="slo-row small">
                <span>{pct(a.budget_remaining)} budget left</span>
                <span>{num(a.budget_minutes_left, 1)} of {num(a.budget_minutes_total, 1)} min</span>
              </span>
              <span className="slo-row small">
                <span>Latency SLI</span>
                <span>
                  {pct(l.sli, 2)}, {pct(l.budget_remaining)} left
                </span>
              </span>
            </button>
          );
        })}
      </section>

      {detail.data && (
        <div className="split">
          <section className="panel">
            <h2>{service}: availability burn rate</h2>
            <LineChart
              ts={detail.data.series.ts}
              series={[
                { name: "1-hour window", values: detail.data.series.burn_1h, className: "s-0" },
                { name: "6-hour window", values: detail.data.series.burn_6h, className: "s-1" },
              ]}
              thresholds={[
                { value: 14.4, label: "page: 14.4×" },
                { value: 6, label: "page: 6×" },
                { value: 1, label: "budget pace: 1×" },
              ]}
              logY
              yMax={200}
              yFormat={(v) => `${v >= 10 ? v.toFixed(0) : v.toFixed(1)}×`}
              height={220}
              label={`Burn rate for ${service} over 30 days`}
            />
            <ul className="legend">
              <li>
                <i className="key s-0" /> 1-hour window
              </li>
              <li>
                <i className="key s-1" /> 6-hour window
              </li>
            </ul>
            <h3>Budget remaining</h3>
            <LineChart
              ts={detail.data.series.ts}
              series={[{ name: "Budget remaining", values: detail.data.series.budget_remaining, className: "s-2" }]}
              thresholds={[{ value: 0.25, label: "release freeze below 25%" }]}
              yMax={1}
              yFormat={(v) => `${Math.round(v * 100)}%`}
              height={150}
              label={`Error budget remaining for ${service}`}
            />
          </section>
          <div className="stack">
            <section className="panel">
              <h2>Burn rates now</h2>
              <table className="table">
                <thead>
                  <tr>
                    <th scope="col">Window</th>
                    <th scope="col" className="num">Availability</th>
                    <th scope="col" className="num">Latency</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.keys(detail.data.availability.burn_rates).map((w) => (
                    <tr key={w}>
                      <th scope="row">{w}</th>
                      <td className="num">{num(detail.data.availability.burn_rates[w], 2)}×</td>
                      <td className="num">{num(detail.data.latency.burn_rates[w], 2)}×</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </section>
            <section className="panel">
              <h2>Alerts that fired</h2>
              {detail.data.alerts.length === 0 ? (
                <p className="muted">No burn-rate alerts for this service in the window.</p>
              ) : (
                <ul className="alerts">
                  {detail.data.alerts.map((a: Report, i: number) => (
                    <li key={i}>
                      <span className={`sev ${a.severity}`}>{a.severity}</span>
                      <span>
                        {a.sli}, {a.rule}
                      </span>
                      <span className="muted small">
                        {when(a.start)}, peak {num(a.peak_long, 1)}×
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </section>
          </div>
        </div>
      )}

      <div className="split">
        <section className="panel">
          <h2>Alert rules</h2>
          <table className="table">
            <thead>
              <tr>
                <th scope="col">Severity</th>
                <th scope="col">Long / short window</th>
                <th scope="col" className="num">Burn rate</th>
                <th scope="col" className="num">Budget spent if sustained</th>
              </tr>
            </thead>
            <tbody>
              {r.rules.map((rule: Report) => (
                <tr key={rule.long}>
                  <th scope="row">{rule.severity}</th>
                  <td>
                    {rule.long} / {rule.short}
                  </td>
                  <td className="num">{rule.threshold}×</td>
                  <td className="num">
                    {pct(rule.budget_share)} in {rule.long}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="muted small">Burn rate = observed error rate ÷ allowed error rate (0.1% for 99.9%). At 1× the budget lasts exactly 30 days.</p>
        </section>
        <section className="panel">
          <h2>Incidents in the replay</h2>
          <ul className="alerts">
            {r.incidents.map((i: Report) => (
              <li key={i.fault_id}>
                <span>{CATEGORY_LABEL[i.category]}</span>
                <span>{i.service}</span>
                <span className="muted small">{when(i.start)}</span>
              </li>
            ))}
          </ul>
        </section>
      </div>
    </div>
  );
}
