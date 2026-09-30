import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api, type AskResult, type IncidentRow, type Report } from "../api";
import { Failed, Loading, PageHeader, useReport } from "../components/ui";
import { CATEGORY_LABEL, pct } from "../format";

const SAMPLES = [
  "How do I tell a traffic spike apart from database saturation?",
  "How do I open the circuit breaker for auth-service on the gateway?",
  "What happens to releases when less than 25% of the error budget remains?",
  "Why did rolling back the gateway not fix the payment dependency failure?",
  "How do I restore postgres-db from last night's backup snapshot?",
];

export function AnswerView({ answer }: { answer: AskResult }) {
  if (answer.refused) {
    return (
      <div className="answer refused">
        <p className="answer-text">{answer.answer}</p>
        {answer.note && <p className="muted small">Why: {answer.note}.</p>}
      </div>
    );
  }
  const order = answer.citations.map((c) => c.chunk_id);
  return (
    <div className="answer">
      <p className="answer-text">
        {answer.sentences.map((s, i) => (
          <span key={i}>
            {s.text}{" "}
            {s.citations.map((c) => (
              <sup key={c} className="cite" title={c}>
                [{order.indexOf(c) + 1}]
              </sup>
            ))}{" "}
          </span>
        ))}
      </p>
      <ol className="sources">
        {answer.citations.map((c) => (
          <li key={c.chunk_id}>
            <strong>{c.title.replace(/^(Runbook|Policy|Postmortem) - /, "")}</strong>, {c.section} <span className="mono small muted">{c.chunk_id}</span>
          </li>
        ))}
      </ol>
      <p className="muted small">
        Engine: {answer.engine === "claude" ? `Claude, prompt ${answer.prompt_version}` : "extractive (no API key configured)"}
        {answer.note ? `. ${answer.note}` : ""}
      </p>
    </div>
  );
}

export function Assistant() {
  const [question, setQuestion] = useState(SAMPLES[0]);
  const [incident, setIncident] = useState("");
  const incidents = useQuery({ queryKey: ["incidents"], queryFn: () => api.get<IncidentRow[]>("/api/incidents") });
  const report = useReport("rag");
  const ask = useMutation({
    mutationFn: () => api.post<AskResult>("/api/ask", { question, incident_id: incident || null }),
  });
  if (report.isLoading) return <Loading />;
  if (!report.data) return <Failed error={report.error} />;
  const r = report.data as Report;
  const h = r.headline;
  const full = r.runs.extractive.full;
  const misses = (r.answers as Report[]).filter((x) => !x.answerable && !x.refused);

  return (
    <div className="page">
      <PageHeader
        title="Troubleshooting assistant"
        lede={`Answers come only from ${r.config.documents} approved runbooks, policies and postmortems (${r.config.chunks} sections). Every sentence cites the section it came from, citations are checked in code against what was retrieved, and when the documents don't cover a question it says so.`}
      />
      <div className="split">
        <section className="panel">
          <form
            className="ask"
            onSubmit={(e) => {
              e.preventDefault();
              if (question.trim().length >= 5) ask.mutate();
            }}
          >
            <label className="field">
              Question
              <textarea rows={3} value={question} onChange={(e) => setQuestion(e.target.value)} />
            </label>
            <label className="field">
              Incident context (optional)
              <select value={incident} onChange={(e) => setIncident(e.target.value)}>
                <option value="">None</option>
                {incidents.data?.map((i) => (
                  <option key={i.id} value={i.id}>
                    {i.id}: {CATEGORY_LABEL[i.predicted]} on {i.service}
                  </option>
                ))}
              </select>
            </label>
            <button className="button primary" type="submit" disabled={ask.isPending}>
              {ask.isPending ? "Searching…" : "Ask"}
            </button>
          </form>
          <div className="samples">
            <span className="muted small">Try:</span>
            {SAMPLES.map((s) => (
              <button key={s} className="chip" onClick={() => setQuestion(s)}>
                {s}
              </button>
            ))}
          </div>
          {ask.isError && <p className="error">{(ask.error as Error).message}</p>}
          {ask.data && (
            <>
              <AnswerView answer={ask.data} />
              <details className="retrieved">
                <summary>Retrieved sections ({ask.data.retrieved.length})</summary>
                <ul>
                  {ask.data.retrieved.map((c) => (
                    <li key={c.chunk_id}>
                      <span className="mono small">{c.chunk_id}</span> <span className="muted small">similarity {c.score.toFixed(2)}</span>
                    </li>
                  ))}
                </ul>
              </details>
            </>
          )}
        </section>
        <div className="stack">
          <section className="panel">
            <h2>Evaluation, {h.questions} questions</h2>
            <dl className="facts big">
              <div>
                <dt>Right document in top 3</dt>
                <dd>{pct(h.hit_at_3)}</dd>
              </div>
              <div>
                <dt>Right document ranked first</dt>
                <dd>{pct(h.hit_at_1)}</dd>
              </div>
              <div>
                <dt>Unanswerable questions refused</dt>
                <dd>{pct(h.correct_refusal_rate)}</dd>
              </div>
              <div>
                <dt>Answerable questions wrongly refused</dt>
                <dd>{pct(h.false_refusal_rate)}</dd>
              </div>
              <div>
                <dt>Answers citing only expected documents</dt>
                <dd>{pct(full.citation_accuracy)}</dd>
              </div>
            </dl>
            <p className="muted small">
              Refusal thresholds were tuned on the odd-numbered questions only. On the other half: {pct(r.runs.extractive.test_half.correct_refusal_rate)} correct
              refusals, {pct(r.runs.extractive.test_half.false_refusal_rate)} false refusals.
              {misses.length > 0 && <> Missed refusals: {misses.map((m: Report) => `"${m.question}"`).join("; ")}.</>}
            </p>
          </section>
          <section className="panel">
            <h2>Retrieval, by embedder</h2>
            <table className="table">
              <thead>
                <tr>
                  <th scope="col">Embedder</th>
                  <th scope="col" className="num">Hit@1</th>
                  <th scope="col" className="num">Hit@3</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(r.retrieval as Record<string, Report>).map(([name, v]) => (
                  <tr key={name} className={name === r.config.embedder ? "chosen" : ""}>
                    <th scope="row">{name}</th>
                    <td className="num">{pct(v["hit@1"])}</td>
                    <td className="num">{pct(v["hit@3"])}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="muted small">LSA (TF-IDF plus a 48-dimension latent space) runs in a few MB, which matters on a free-tier host.</p>
          </section>
        </div>
      </div>
    </div>
  );
}
