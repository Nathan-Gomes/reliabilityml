"""Troubleshooting assistant: retrieve runbook/postmortem chunks, answer with citations, or refuse.

Rules (enforced in the prompt *and* in code):
- every sentence cites at least one retrieved chunk ID,
- if retrieval is weak or the model cannot support an answer, return the refusal message,
- any citation that is not one of the retrieved chunk IDs rejects the whole answer.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from .corpus import Chunk, sentences_of
from .index import VectorStore

REFUSAL = "I couldn't find this in the approved runbooks or postmortems."
PROMPTS_DIR = Path(__file__).with_name("prompts")
STOP = set(
    [
        "a",
        "an",
        "the",
        "and",
        "or",
        "of",
        "to",
        "in",
        "on",
        "for",
        "with",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "what",
        "which",
        "who",
        "how",
        "when",
        "where",
        "why",
        "do",
        "does",
        "should",
        "i",
        "we",
        "our",
        "my",
        "it",
        "its",
        "this",
        "that",
        "these",
        "those",
        "if",
        "than",
        "then",
        "there",
        "from",
        "by",
        "at",
        "as",
        "can",
        "could",
        "would",
        "service",
        "services",
    ]
)
log = logging.getLogger(__name__)


@dataclass
class Citation:
    chunk_id: str
    title: str
    section: str


@dataclass
class Answer:
    question: str
    refused: bool
    sentences: list[dict] = field(default_factory=list)  # {text, citations: [chunk_id]}
    citations: list[Citation] = field(default_factory=list)
    retrieved: list[dict] = field(default_factory=list)  # {chunk_id, title, section, score}
    engine: str = "extractive"
    prompt_version: str | None = None
    note: str = ""

    @property
    def text(self) -> str:
        return REFUSAL if self.refused else " ".join(s["text"] for s in self.sentences)

    def to_dict(self) -> dict:
        return {
            "question": self.question,
            "refused": self.refused,
            "answer": self.text,
            "sentences": self.sentences,
            "citations": [c.__dict__ for c in self.citations],
            "retrieved": self.retrieved,
            "engine": self.engine,
            "prompt_version": self.prompt_version,
            "note": self.note,
        }


def content_words(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9][a-z0-9_-]+", text.lower())
    return {w.rstrip("s") if len(w) > 4 else w for w in words if w not in STOP}


def incident_context(incident: dict | None) -> str:
    if not incident:
        return ""
    parts = [f"Service {incident.get('service', '')}."]
    if incident.get("predicted") and incident["predicted"] != "unknown":
        parts.append(f"Predicted category {incident['predicted'].replace('_', ' ')}.")
    else:
        parts.append("Unclassified alert.")
    for ev in incident.get("evidence", []):
        if ev.get("notable"):
            parts.append(f"{ev['signal']} {ev['value']}.")
    return " ".join(parts)


def verify_citations(sentences: list[dict], retrieved_ids: set[str]) -> str | None:
    """Return an error message if any sentence is uncited or cites an ID that was not retrieved."""
    if not sentences:
        return "no sentences"
    for s in sentences:
        cites = s.get("citations") or []
        if not cites:
            return f"uncited sentence: {s.get('text', '')[:60]}"
        bad = [c for c in cites if c not in retrieved_ids]
        if bad:
            return f"citation not in retrieved set: {bad[0]}"
    return None


class Assistant:
    def __init__(
        self,
        store: VectorStore,
        k: int = 4,
        min_score: float = 0.12,
        min_overlap: int = 2,
        min_coverage: float = 0.5,
        prompt_version: str = "v2",
        api_key: str | None = None,
        model: str = "claude-opus-5",
    ):
        self.store = store
        self.k = k
        self.min_score = min_score  # below this top retrieval score, refuse without generating
        self.min_overlap = min_overlap  # extractive engine: content words a sentence must share
        self.min_coverage = min_coverage  # share of the question's key terms found in the retrieved text
        self.prompt_version = prompt_version
        self.api_key = api_key if api_key is not None else os.environ.get("RELIABILITYML_ANTHROPIC_API_KEY")
        self.model = model

    def retrieve(self, question: str, incident: dict | None = None) -> list[tuple[Chunk, float]]:
        return self.store.search(f"{question} {incident_context(incident)}".strip(), self.k)

    def ask(self, question: str, incident: dict | None = None, engine: str = "auto") -> Answer:
        hits = self.retrieve(question, incident)
        retrieved = [
            {"chunk_id": c.chunk_id, "title": c.title, "section": c.section, "score": round(s, 3)} for c, s in hits
        ]
        if not hits or hits[0][1] < self.min_score:
            return Answer(question, True, retrieved=retrieved, note="retrieval score below threshold")
        coverage = self.coverage(question, hits)
        if coverage < self.min_coverage:
            return Answer(
                question,
                True,
                retrieved=retrieved,
                note=f"only {coverage:.0%} of the question's key terms appear in the retrieved text",
            )
        use_llm = engine in ("auto", "claude") and self.api_key
        if use_llm:
            try:
                answer = self._claude(question, incident, hits)
                answer.retrieved = retrieved
                return answer
            except Exception as error:  # network, refusal, schema: fall back rather than fail
                log.warning("Claude answer failed, using extractive engine: %s", error)
        answer = self._extractive(question, hits)
        answer.retrieved = retrieved
        if use_llm:
            answer.note = "live model unavailable; extractive engine answered"
        return answer

    # ------------------------------------------------------------ engines

    @staticmethod
    def coverage(question: str, hits: list[tuple[Chunk, float]]) -> float:
        terms = content_words(question)
        if not terms:
            return 0.0
        seen = content_words(" ".join(f"{c.title} {c.section} {c.text}" for c, _ in hits))
        return len(terms & seen) / len(terms)

    def _extractive(self, question: str, hits: list[tuple[Chunk, float]]) -> Answer:
        """Rank retrieved sentences by similarity to the question; keep those close to the best one."""
        q = content_words(question)
        candidates = []
        for rank, (chunk, score) in enumerate(hits):
            heading = content_words(chunk.section)
            for position, sentence in enumerate(sentences_of(chunk)):
                # a sentence is read in the context of its section heading ("Root cause", "Mitigation")
                overlap = len(q & (content_words(sentence) | heading))
                if overlap >= self.min_overlap:
                    candidates.append((rank, position, sentence, chunk, score, overlap))
        if not candidates:
            return Answer(question, True, note="no retrieved sentence supports an answer")
        vectors = self.store.embedder.encode([question] + [c[2] for c in candidates])
        similarity = vectors[1:] @ vectors[0]
        scored = [
            (float(sim) + 0.05 * overlap + 0.25 * score, rank, position, sentence, chunk)
            for sim, (rank, position, sentence, chunk, score, overlap) in zip(similarity, candidates)
        ]
        best = max(t[0] for t in scored)
        top = sorted([t for t in scored if t[0] >= 0.75 * best], key=lambda t: -t[0])[:3]
        top.sort(key=lambda t: (t[1], t[2]))  # keep document order for readability
        sentences = [
            {"text": s if s.endswith((".", "`")) else s + ".", "citations": [c.chunk_id]} for _, _, _, s, c in top
        ]
        return self._finish(question, sentences, hits, "extractive", None)

    def _claude(self, question: str, incident: dict | None, hits: list[tuple[Chunk, float]]) -> Answer:
        import anthropic

        system = (PROMPTS_DIR / f"{self.prompt_version}.md").read_text()
        context = "\n\n".join(
            f'<chunk id="{c.chunk_id}" title="{c.title}" section="{c.section}">\n{c.text}\n</chunk>' for c, _ in hits
        )
        schema = {
            "type": "object",
            "properties": {
                "refused": {"type": "boolean"},
                "sentences": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "text": {"type": "string"},
                            "citations": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["text", "citations"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["refused", "sentences"],
            "additionalProperties": False,
        }
        client = anthropic.Anthropic(api_key=self.api_key, timeout=60.0, max_retries=1)
        response = client.messages.create(
            model=self.model,
            max_tokens=16000,
            system=system,
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"<incident>{incident_context(incident) or 'none'}</incident>\n"
                        f"<retrieved>\n{context}\n</retrieved>\n<question>{question}</question>"
                    ),
                }
            ],
            output_config={"format": {"type": "json_schema", "schema": schema}},
        )
        if response.stop_reason in ("refusal", "max_tokens"):
            raise RuntimeError(f"stop_reason={response.stop_reason}")
        data = json.loads(next(b.text for b in response.content if b.type == "text"))
        if data["refused"]:
            return Answer(
                question,
                True,
                engine="claude",
                prompt_version=self.prompt_version,
                note="model found no support in the retrieved chunks",
            )
        return self._finish(question, data["sentences"], hits, "claude", self.prompt_version)

    def _finish(self, question, sentences, hits, engine, prompt_version) -> Answer:
        ids = {c.chunk_id for c, _ in hits}
        problem = verify_citations(sentences, ids)
        if problem:
            return Answer(
                question,
                True,
                engine=engine,
                prompt_version=prompt_version,
                note=f"answer rejected by citation check ({problem})",
            )
        used: list[Citation] = []
        for s in sentences:
            for cid in s["citations"]:
                if cid not in [u.chunk_id for u in used]:
                    chunk = next(c for c, _ in hits if c.chunk_id == cid)
                    used.append(Citation(cid, chunk.title, chunk.section))
        return Answer(question, False, sentences, used, engine=engine, prompt_version=prompt_version)
