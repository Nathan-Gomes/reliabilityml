import { useMutation, useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { api, type AskResult, type IncidentDetail, type Span } from "../api";
import { Bar, LineChart } from "../components/charts";
import { Failed, Loading, Tag } from "../components/ui";
import { CATEGORY_LABEL, pct, serviceShort, when } from "../format";
import { AnswerView } from "./Assistant";

const SIGNALS: { key: string; title: string; format: (v: number) => string }[] = [
  { key: "error_rate", title: "Error rate", format: (v) => `${(v * 100).toFixed(v < 0.01 ? 2 : 1)}%` },
  { key: "latency_p99", title: "p99 latency (ms)", format: (v) => v.toFixed(0) },
  { key: "request_rate", title: "Requests per second", format: (v) => v.toFixed(0) },
  { key: "memory_utilization", title: "Memory utilization", format: (v) => `${(v * 100).toFixed(0)}%` },
];

export function Incident() {
  const { id } = useParams();
  const q = useQuery({ queryKey: ["incident", id], queryFn: () => api.get<IncidentDetail>(`/api/incidents/${id}`) });
  const ask = useMutation({
    mutationFn: () =>
      api.post<AskResult>("/api/ask", {
        question: q.data?.predicted === "unknown" ? "The classifier returned unknown for an alert. What should I check first?" : `How do I mitigate ${CATEGORY_LABEL[q.data?.predicted ?? "unknown"].toLowerCase()}?`,
        incident_id: id,
      }),
  });
  if (q.isLoading) return <Loading what="Loading incident" />;
  if (!q.data) return <Failed error={q.error} />;
  const inc = q.data;
  const truth = inc.ground_truth;
  const band = truth ? [{ from: truth.start, to: truth.end, label: "injected fault" }] : [];
  const probs = Object.entries(inc.explanation.probabilities);

  return (
    <div className="page">
      <nav className="crumbs" aria-label="Breadcrumb">
        <Link to="/">Overview</Link> / {inc.id}
      </nav>
      <header className="incident-head">
        <div>
          <p className="muted">
            {when(inc.fired_at)} on {inc.service}, first signal {inc.signal.replace("_", " ")} (z {inc.peak_z})
          </p>
          <h1>
            <Tag category={inc.predicted} /> {inc.predicted === "unknown" ? "No confident category" : CATEGORY_LABEL[inc.predicted]}
          </h1>
          <p className="lede">
            {inc.explanation.unknown_reason
              ? `Routed to unknown: ${inc.explanation.unknown_reason}. Confidence ${pct(inc.confidence)}, novelty ${inc.explanation.novelty.toFixed(1)} against a limit of ${inc.explanation.novelty_threshold.toFixed(1)}.`
              : `Confidence ${pct(inc.confidence)} (cut-off ${pct(inc.explanation.confidence_threshold)}), and the window looks like the training data (novelty ${inc.explanation.novelty.toFixed(1)} of ${inc.explanation.novelty_threshold.toFixed(1)}).`}
          </p>
        </div>
        <aside className={`truth ${truth && (truth.category === inc.predicted || (truth.category === "memory_leak" && inc.predicted === "unknown")) ? "hit" : "miss"}`}>
          <p className="k">Ground truth</p>
          {truth ? (
            <>
              <p className="v">{CATEGORY_LABEL[truth.category]}</p>
              <p className="n">
                {truth.service}. {truth.detail}
              </p>
            </>
          ) : (
            <p className="v">False alert</p>
          )}
        </aside>
      </header>

      <div className="split">
        <div className="stack">
          <section className="panel">
            <h2>Signals around the alert</h2>
            <p className="muted small">One panel per signal; each line is a service. The shaded span is the injected fault.</p>
            <div className="multiples">
              {SIGNALS.filter((s) => inc.series[s.key]).map((s) => {
                const series = inc.series[s.key];
                const services = Object.keys(series).filter((k) => k !== "ts");
                return (
                  <div key={s.key}>
                    <h3>{s.title}</h3>
                    <LineChart
                      ts={series.ts as string[]}
                      series={services.map((svc) => ({ name: serviceShort(svc), values: series[svc] as number[], className: svc === inc.service || svc === truth?.service ? "s-focus" : "s-quiet" }))}
                      bands={band}
                      yFormat={s.format}
                      height={170}
                      width={380}
                      label={`${s.title} per service around the alert`}
                    />
                  </div>
                );
              })}
            </div>
          </section>
          <section className="panel">
            <h2>Trace at the alert</h2>
            <p className="muted small">
              Sample trace built from per-service span times. The service that added the most time vs its baseline:{" "}
              <strong>{inc.trace.slowest_added}</strong>.
            </p>
            <Waterfall root={inc.trace.root} />
          </section>
        </div>

        <div className="stack">
          <section className="panel">
            <h2>Why the model said this</h2>
            <ul className="probs">
              {probs.map(([label, p]) => (
                <li key={label}>
                  <span>{CATEGORY_LABEL[label]}</span>
                  <Bar value={p} className={`c-${label}`} />
                  <span className="num">{pct(p)}</span>
                </li>
              ))}
            </ul>
            <h3>Evidence</h3>
            <ul className="evidence">
              {inc.evidence.map((e) => (
                <li key={e.signal} className={e.notable ? "notable" : ""}>
                  <span>{e.signal}</span>
                  <strong>{e.value}</strong>
                </li>
              ))}
            </ul>
            <p className="baseline">
              Rules baseline: <Tag category={inc.rules_prediction} /> <span className="muted">({inc.rules_reason})</span>
            </p>
          </section>
          <section className="panel">
            <div className="panel-head">
              <h2>Runbook guidance</h2>
              <button className="button primary" onClick={() => ask.mutate()} disabled={ask.isPending}>
                {ask.isPending ? "Searching runbooks…" : "Ask the assistant"}
              </button>
            </div>
            {ask.data ? <AnswerView answer={ask.data} /> : <p className="muted">Answers cite runbook sections and refuse when the runbooks don't cover it.</p>}
          </section>
          <section className="panel">
            <h2>Logs and deploys</h2>
            {inc.deploys.length > 0 && (
              <ul className="log">
                {inc.deploys.map((d) => (
                  <li key={d.ts + d.service}>
                    <span className="mono">{when(d.ts, false)}</span> <strong>{d.kind}</strong> {d.service} {d.version}
                  </li>
                ))}
              </ul>
            )}
            {inc.logs.length ? (
              <ul className="log">
                {inc.logs.map((l, i) => (
                  <li key={i} className={l.level.toLowerCase()}>
                    <span className="mono">{when(l.ts, false)}</span> <strong>{l.level}</strong> {l.service}: {l.message}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="muted">No sampled log events in the 30 minutes around this alert.</p>
            )}
          </section>
        </div>
      </div>
    </div>
  );
}

function Waterfall({ root }: { root: Span }) {
  const rows: { span: Span; depth: number }[] = [];
  const walk = (s: Span, depth: number) => {
    rows.push({ span: s, depth });
    s.children.forEach((c) => walk(c, depth + 1));
  };
  walk(root, 0);
  const total = root.end_ms;
  return (
    <ul className="waterfall">
      {rows.map(({ span, depth }) => (
        <li key={span.span_id}>
          <span className="wf-name" style={{ paddingLeft: depth * 14 }}>
            {span.service}
          </span>
          <span className="wf-track">
            <span className={`wf-bar ${span.self_delta_ms > 15 ? "hot" : ""}`} style={{ left: `${(span.start_ms / total) * 100}%`, width: `${Math.max(1, ((span.end_ms - span.start_ms) / total) * 100)}%` }} />
          </span>
          <span className="num small">
            {span.self_ms.toFixed(0)} ms self{span.self_delta_ms > 1 ? `, +${span.self_delta_ms.toFixed(0)}` : ""}
          </span>
        </li>
      ))}
    </ul>
  );
}
