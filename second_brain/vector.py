"""ChromaDB persistence + similarity queries for the Notes/ corpus."""

from __future__ import annotations

import logging
from typing import Optional

from second_brain.config import chroma_path

logger = logging.getLogger(__name__)

COLLECTION_NAME = "notes"


def open_collection() -> Optional[object]:
    """Return the ``notes`` collection or ``None`` if Chroma is unavailable."""
    try:
        import chromadb  # type: ignore

        client = chromadb.PersistentClient(path=str(chroma_path()))
        return client.get_or_create_collection(name=COLLECTION_NAME)
    except Exception as exc:
        logger.warning("ChromaDB unavailable (%s) — skipping vector ops", exc)
        return None


def query_correlations(collection, embedding: list[float] | None, k: int = 5) -> list[dict]:
    if collection is None or embedding is None:
        return []
    try:
        result = collection.query(query_embeddings=[embedding], n_results=k)
    except Exception as exc:
        logger.warning("ChromaDB query failed: %s", exc)
        return []
    out: list[dict] = []
    metadatas = (result.get("metadatas") or [[]])[0]
    documents = (result.get("documents") or [[]])[0]
    for meta, doc in zip(metadatas, documents):
        out.append({
            "title": meta.get("title", ""),
            "path": meta.get("path", ""),
            "preview": (doc or "")[:200],
        })
    return out


def upsert_note(
    collection,
    note_id: str,
    embedding: list[float],
    title: str,
    path: str,
    tags: list[str],
    content: str,
) -> None:
    collection.upsert(
        ids=[note_id],
        embeddings=[embedding],
        metadatas=[{
            "title": title,
            "path": path,
            "tags": ",".join(tags),
        }],
        documents=[content[:2000]],
    )
