"""Evaluate retrieval and answering on eval/rag_questions.yaml.

Refusal thresholds are tuned on the odd-numbered questions (dev) and reported on the even-numbered
ones (test) as well as on the full set, so the headline is not tuned on itself.
"""

from __future__ import annotations

import itertools
import logging
import os
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from .assistant import REFUSAL, Assistant
from .corpus import chunk_documents, load_documents
from .index import VectorStore, make_embedder

ROOT = Path(__file__).resolve().parents[3]
QUESTIONS = ROOT / "eval" / "rag_questions.yaml"
log = logging.getLogger(__name__)


def load_questions() -> list[dict]:
    return yaml.safe_load(QUESTIONS.read_text())


def available_embedders() -> list[str]:
    names = ["tfidf", "lsa"]
    try:
        import sentence_transformers  # noqa: F401

        names.append("sentence-transformers")
    except ImportError:
        pass
    return names


def retrieval_metrics(store: VectorStore, questions: list[dict], ks=(1, 3, 5)) -> dict:
    hits: dict[int, list[bool]] = {k: [] for k in ks}
    top_answerable: list[float] = []
    top_unanswerable: list[float] = []
    for q in questions:
        results = store.search(q["question"], max(ks))
        (top_answerable if q["answerable"] else top_unanswerable).append(results[0][1])
        if not q["answerable"]:
            continue
        docs = [c.doc_id for c, _ in results]
        for k in ks:
            hits[k].append(any(d in q["expected_docs"] for d in docs[:k]))
    return {
        **{f"hit@{k}": float(np.mean(v)) for k, v in hits.items()},
        "top_score_answerable_median": float(np.median(top_answerable)),
        "top_score_unanswerable_median": float(np.median(top_unanswerable)),
    }


def answer_metrics(assistant: Assistant, questions: list[dict], engine: str = "extractive") -> tuple[dict, list[dict]]:
    rows = []
    for q in questions:
        ans = assistant.ask(q["question"], engine=engine)
        cited_docs = {c.chunk_id.split("#")[0] for c in ans.citations}
        rows.append(
            {
                "id": q["id"],
                "question": q["question"],
                "answerable": q["answerable"],
                "refused": ans.refused,
                "answer": ans.text,
                "citations": [c.chunk_id for c in ans.citations],
                "citations_supported": (not ans.refused) and bool(cited_docs) and cited_docs <= set(q["expected_docs"]),
                "cites_expected": (not ans.refused) and bool(cited_docs & set(q["expected_docs"])),
                "note": ans.note,
            }
        )
    answerable = [r for r in rows if r["answerable"]]
    unanswerable = [r for r in rows if not r["answerable"]]
    answered = [r for r in answerable if not r["refused"]]
    metrics = {
        "correct_refusal_rate": float(np.mean([r["refused"] for r in unanswerable])) if unanswerable else None,
        "false_refusal_rate": float(np.mean([r["refused"] for r in answerable])) if answerable else None,
        "citation_accuracy": float(np.mean([r["citations_supported"] for r in answered])) if answered else None,
        "cites_expected_doc": float(np.mean([r["cites_expected"] for r in answered])) if answered else None,
        "answered": len(answered),
    }
    return metrics, rows


def _balanced(metrics: dict) -> float:
    return (metrics["correct_refusal_rate"] or 0) + (1 - (metrics["false_refusal_rate"] or 0))


def stage_rag(out: Path, registry=None) -> dict:
    from ..pipelines.build import write_json  # local import avoids a cycle

    chunks = chunk_documents(load_documents())
    questions = load_questions()
    dev = [q for i, q in enumerate(questions) if i % 2 == 0]
    test = [q for i, q in enumerate(questions) if i % 2 == 1]

    retrieval = {}
    stores = {}
    for name in available_embedders():
        store = VectorStore(make_embedder(name), chunks)
        stores[name] = store
        retrieval[name] = retrieval_metrics(store, questions)
    best = max(retrieval, key=lambda n: (retrieval[n]["hit@3"], retrieval[n]["hit@1"], n == "lsa"))
    store = stores[best]

    tuning = []
    dev_scores = sorted(store.search(q["question"], 1)[0][1] for q in dev)
    score_grid = sorted({0.0, *[round(float(v) - 1e-4, 4) for v in dev_scores]})
    for min_score, min_overlap, min_coverage in itertools.product(score_grid, [1, 2], [0.4, 0.5, 0.6, 0.7]):
        assistant = Assistant(
            store, min_score=min_score, min_overlap=min_overlap, min_coverage=min_coverage, api_key=""
        )
        m, _ = answer_metrics(assistant, dev)
        tuning.append(
            {
                "min_score": min_score,
                "min_overlap": min_overlap,
                "min_coverage": min_coverage,
                **m,
                "balanced": _balanced(m),
            }
        )
    # Best balance of correct and false refusals; ties go to the least aggressive settings.
    chosen = max(tuning, key=lambda r: (r["balanced"], -r["min_score"], -r["min_coverage"], -r["min_overlap"]))

    assistant = Assistant(
        store,
        min_score=chosen["min_score"],
        min_overlap=chosen["min_overlap"],
        min_coverage=chosen["min_coverage"],
        api_key="",
    )
    held_out, _ = answer_metrics(assistant, test)
    full, rows = answer_metrics(assistant, questions)
    runs: dict[str, dict[str, Any]] = {
        "extractive": {"engine": "extractive", "prompt_version": None, "full": full, "test_half": held_out}
    }

    key = os.environ.get("RELIABILITYML_ANTHROPIC_API_KEY")
    if key:  # evaluate each prompt version with the live model
        for version in ("v1", "v2"):
            live = Assistant(
                store,
                min_score=chosen["min_score"],
                min_overlap=chosen["min_overlap"],
                min_coverage=chosen["min_coverage"],
                prompt_version=version,
                api_key=key,
            )
            m, live_rows = answer_metrics(live, questions, engine="claude")
            runs[f"claude-{version}"] = {"engine": "claude", "prompt_version": version, "full": m}
            write_json(out / "reports" / f"rag_answers_claude_{version}.json", live_rows)

    if registry is not None:
        for name, run in runs.items():
            metrics = {
                **{f"retrieval_{k}": v for k, v in retrieval[best].items() if k.startswith("hit")},
                **{k: v for k, v in run["full"].items() if v is not None},
            }
            registry.log_run(
                f"rag-{name}",
                {
                    "embedder": best,
                    "k": assistant.k,
                    "min_score": chosen["min_score"],
                    "min_overlap": chosen["min_overlap"],
                    "min_coverage": chosen["min_coverage"],
                    "prompt_version": run["prompt_version"] or "n/a",
                    "corpus_chunks": len(chunks),
                },
                metrics,
                {"component": "rag"},
                {},
            )

    cited = next(r for r in rows if r["answerable"] and not r["refused"] and r["citations_supported"])
    refused = next(r for r in rows if not r["answerable"] and r["refused"])
    result = {
        "config": {
            "embedder": best,
            "k": assistant.k,
            "min_score": chosen["min_score"],
            "min_overlap": chosen["min_overlap"],
            "min_coverage": chosen["min_coverage"],
            "chunks": len(chunks),
            "documents": len({c.doc_id for c in chunks}),
            "refusal_message": REFUSAL,
        },
        "retrieval": retrieval,
        "tuning": tuning,
        "runs": runs,
        "answers": rows,
        "examples": {"cited": cited, "refused": refused},
        "headline": {
            "embedder": best,
            "hit_at_3": retrieval[best]["hit@3"],
            "hit_at_1": retrieval[best]["hit@1"],
            "correct_refusal_rate": full["correct_refusal_rate"],
            "false_refusal_rate": full["false_refusal_rate"],
            "citation_accuracy": full["citation_accuracy"],
            "questions": len(questions),
        },
    }
    write_json(out / "reports" / "rag.json", result)
    write_json(
        ROOT / "eval" / "reports" / "rag_eval.json", {k: result[k] for k in ("config", "retrieval", "runs", "headline")}
    )
    return result
