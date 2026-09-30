"""Load the runbook/postmortem corpus and split it into section chunks with stable IDs."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from ..paths import ROOT

CORPUS_DIR = ROOT / "corpus"


@dataclass
class Document:
    doc_id: str
    title: str
    doc_type: str
    category: str
    services: list[str]
    last_reviewed: str
    body: str
    path: str


@dataclass
class Chunk:
    chunk_id: str
    doc_id: str
    title: str
    section: str
    text: str  # what the model sees and cites
    doc_type: str
    category: str
    services: list[str] = field(default_factory=list)

    @property
    def embed_text(self) -> str:
        return f"{self.title}. {self.section}. {self.text}"


def load_documents(root: Path = CORPUS_DIR) -> list[Document]:
    docs = []
    for path in sorted(root.rglob("*.md")):
        raw = path.read_text()
        _, front, body = raw.split("---", 2)
        meta = yaml.safe_load(front)
        docs.append(
            Document(
                doc_id=path.stem,
                title=meta["title"],
                doc_type=meta["doc_type"],
                category=meta["category"],
                services=list(meta.get("services", [])),
                last_reviewed=str(meta["last_reviewed"]),
                body=body.strip(),
                path=str(path.relative_to(root)),
            )
        )
    return docs


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+|\n(?=\s*(?:-|\d+\.)\s)", text)
    return [p.strip() for p in parts if p.strip()]


def chunk_documents(docs: list[Document], overlap_sentences: int = 1) -> list[Chunk]:
    """One chunk per `##` section. Each chunk starts with the last sentence of the previous
    section (a small overlap) so context that straddles a heading is not lost."""
    chunks = []
    for doc in docs:
        sections = re.split(r"^## ", doc.body, flags=re.MULTILINE)
        previous_tail = ""
        for block in sections[1:]:
            heading, _, content = block.partition("\n")
            content = content.strip()
            text = content if not previous_tail else f"(Context: {previous_tail})\n{content}"
            chunks.append(
                Chunk(
                    chunk_id=f"{doc.doc_id}#{_slug(heading)}",
                    doc_id=doc.doc_id,
                    title=doc.title,
                    section=heading.strip(),
                    text=text,
                    doc_type=doc.doc_type,
                    category=doc.category,
                    services=doc.services,
                )
            )
            tail = _sentences(content)[-overlap_sentences:] if overlap_sentences else []
            previous_tail = " ".join(tail)[:240]
    return chunks


def sentences_of(chunk: Chunk) -> list[str]:
    body = re.sub(r"^\(Context: .*?\)\n", "", chunk.text, flags=re.DOTALL)
    return [re.sub(r"^\s*(?:-|\d+\.)\s*", "", s) for s in _sentences(body)]
