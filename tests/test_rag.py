import pytest

from reliabilityml.rag.assistant import REFUSAL, Assistant, verify_citations
from reliabilityml.rag.corpus import chunk_documents, load_documents
from reliabilityml.rag.index import VectorStore, make_embedder


@pytest.fixture(scope="module")
def assistant():
    store = VectorStore(make_embedder("lsa"), chunk_documents(load_documents()))
    return Assistant(store, min_score=0.0, min_overlap=2, min_coverage=0.4, api_key="")


def test_corpus_size_and_metadata():
    docs = load_documents()
    assert 15 <= len(docs) <= 25
    assert {d.doc_type for d in docs} == {"runbook", "postmortem", "policy"}
    assert all(d.last_reviewed and d.category for d in docs)


def test_answer_cites_only_retrieved_chunks(assistant):
    ans = assistant.ask("How do I open the circuit breaker for auth-service on the gateway?")
    assert not ans.refused
    retrieved = {r["chunk_id"] for r in ans.retrieved}
    assert ans.citations and all(c.chunk_id in retrieved for c in ans.citations)
    assert all(s["citations"] for s in ans.sentences)


def test_unanswerable_question_is_refused(assistant):
    ans = assistant.ask("How do I rotate the TLS certificates on the CDN?")
    assert ans.refused and ans.text == REFUSAL


def test_code_side_citation_check_rejects_foreign_ids():
    assert verify_citations([{"text": "x", "citations": ["made-up#chunk"]}], {"real#chunk"})
    assert verify_citations([{"text": "x", "citations": []}], {"real#chunk"})
    assert verify_citations([{"text": "x", "citations": ["real#chunk"]}], {"real#chunk"}) is None
