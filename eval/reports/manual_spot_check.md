# RAG answers: manual spot-check (extractive engine)

Reviewed by hand on 2026-09-30 against `artifacts/reports/rag.json`. Two questions per answered item:

1. **Supported:** does every cited chunk contain the claim it is attached to?
2. **Answers the question:** would an on-call engineer get what they asked for?

| Result | Count |
| --- | --- |
| Answered questions reviewed | 25 |
| Citations support their sentence | 25 / 25 (guaranteed: the extractive engine only quotes retrieved text) |
| Answer actually answers the question | 21 / 25 |
| Unanswerable questions answered anyway | 1 / 5 (q26, Kafka consumer lag, answered from a generic triage section) |

Weak answers (relevant sentences, but not the answer):

- **q09** "What does a memory leak look like in the memory graph?" Returned escalation and mitigation sentences; the Symptoms section was not in the top 4 chunks.
- **q13** "How do I open the circuit breaker for auth-service?" Returned the postmortem action item, not the runbook command (`CIRCUIT_BREAKER_AUTH=open`).
- **q15** "The classifier returned unknown. What should I check first?" Explained what `unknown` means rather than the first check.
- **q19** "When does a canary replay fail the gate?" Described the gate but not the 0.2 pp / 10% thresholds.

The automatic "citation accuracy" metric (80%) is stricter: it counts an answer as wrong if it cites any document outside
the question's expected list, even when that document is relevant (q01 citing the pool-exhaustion postmortem, for example).

What would fix the weak answers: an LLM engine that composes an answer from the retrieved chunks (implemented; enabled
with `RELIABILITYML_ANTHROPIC_API_KEY`, prompt versions in `src/reliabilityml/rag/prompts/`), and evaluating it with the
same set. Those runs are logged per prompt version when a key is present; the numbers above are for the key-less engine
that the public demo runs.
