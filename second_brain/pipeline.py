"""High-level orchestrators: daily recap pipeline + Notes/ indexer."""

from __future__ import annotations

import logging
import sys
import time
import traceback
from pathlib import Path

import frontmatter
from tqdm import tqdm

from second_brain import state, vector
from second_brain.archive import EXCLUDED_NOTES_SUBDIRS
from second_brain.config import vault_path
from second_brain.llm import call_llm, embed_text_safe, load_prompt, parse_llm_json
from second_brain.models import Source
from second_brain.rendering import kebab
from second_brain.sources import EXTRACTORS

logger = logging.getLogger(__name__)

LAST_INDEX_FILE = ".state/last_index.txt"


# ---------- daily recap ----------

def gather_sources() -> list[Source]:
    inbox = vault_path() / "Inbox"
    sources: list[Source] = []
    for key, extractor in EXTRACTORS.items():
        folder = inbox / key
        try:
            new_files = state.get_new_items(key, folder)
        except Exception as exc:
            logger.error("state.get_new_items(%s) failed: %s", key, exc)
            continue
        logger.info("%s: %d new items", key, len(new_files))
        for fpath in new_files:
            try:
                src = extractor(fpath)
            except Exception:
                logger.error("extractor failed for %s:\n%s", fpath, traceback.format_exc())
                continue
            if src is not None:
                sources.append(src)
    return sources


def embed_sources(sources: list[Source]) -> None:
    for s in sources:
        try:
            s.embedding = embed_text_safe(s.content)
        except Exception as exc:
            logger.warning("embedding failed for %s: %s", s.title, exc)
            s.embedding = None


def _build_user_message(source: Source, related: list[dict]) -> str:
    """English framing — matches the system prompt language and avoids
    biasing the model toward Italian. The output language is controlled by
    the prompt's LANGUAGE RULE based on `source.content` only."""
    related_block = "\n".join(
        f"- [[{r['path']}]] ({r['title']}): {r['preview']}" for r in related
    ) or "(no related notes)"
    return (
        f"Content to analyze:\n{source.content}\n\n"
        f"Related Obsidian notes:\n{related_block}\n"
    )


def _enrich(source: Source, related: list[dict]) -> None:
    prompt_name = "place.txt" if source.type == "place" else "recap.txt"
    system_prompt = load_prompt(prompt_name)
    user_msg = _build_user_message(source, related)
    try:
        raw = call_llm(system_prompt, user_msg)
        payload = parse_llm_json(raw)
    except Exception:
        logger.error("LLM call failed:\n%s", traceback.format_exc())
        source.recap = "_[Recap non disponibile — errore LLM]_"
        return
    source.recap = (payload.get("recap") or "").strip() or "_[Recap vuoto]_"
    tags = [kebab(t) for t in (payload.get("tags") or []) if t]
    source.tags = [t for t in tags if t][:5]
    correlations = payload.get("correlations") or []
    source.correlations = [str(c).strip() for c in correlations if c][:5]


def enrich_sources(sources: list[Source]) -> None:
    collection = vector.open_collection()
    for s in sources:
        related = vector.query_correlations(collection, s.embedding) if s.embedding else []
        _enrich(s, related)


def commit_state(sources: list[Source]) -> None:
    by_source: dict[str, list[str]] = {}
    for s in sources:
        by_source.setdefault(s.state_source, []).append(s.state_id)
    for key, ids in by_source.items():
        try:
            state.mark_processed(key, ids)
        except Exception as exc:
            logger.error("mark_processed(%s) failed: %s", key, exc)


# ---------- vault indexing ----------

def _normalize_tags(raw) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        return [t.strip().lstrip("#") for t in raw.split(",") if t.strip()]
    if isinstance(raw, list):
        return [str(t).strip().lstrip("#") for t in raw if str(t).strip()]
    return []


def _read_last_index() -> float:
    p = vault_path() / LAST_INDEX_FILE
    if not p.exists():
        return 0.0
    try:
        return float(p.read_text().strip())
    except (OSError, ValueError):
        return 0.0


def _write_last_index(ts: float) -> None:
    p = vault_path() / LAST_INDEX_FILE
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"{ts}\n")


def _is_excluded(path: Path, notes_dir: Path) -> bool:
    """True se ``path`` sta sotto ``Notes/{articles,youtube,places}/``."""
    try:
        rel_parts = path.resolve().relative_to(notes_dir.resolve()).parts
    except ValueError:
        return False
    return bool(rel_parts) and rel_parts[0] in EXCLUDED_NOTES_SUBDIRS


def _gather_notes(incremental: bool) -> list[Path]:
    notes_dir = vault_path() / "Notes"
    if not notes_dir.exists():
        logger.error("Notes/ directory not found at %s", notes_dir)
        return []
    all_md = sorted(p for p in notes_dir.rglob("*.md") if not _is_excluded(p, notes_dir))
    if not incremental:
        return all_md
    cutoff = _read_last_index()
    return [p for p in all_md if p.stat().st_mtime > cutoff]


def index_notes(incremental: bool) -> int:
    """Index Notes/ into Chroma. Returns count of indexed notes."""
    files = _gather_notes(incremental)
    if not files:
        logger.info("no notes to index")
        return 0
    logger.info("indexing %d notes (incremental=%s)", len(files), incremental)
    collection = vector.open_collection()
    if collection is None:
        logger.error("ChromaDB not available — aborting index")
        return 0
    vault = vault_path()
    indexed = 0
    for fpath in tqdm(files, desc="indexing", unit="note", file=sys.stderr):
        try:
            post = frontmatter.load(fpath)
        except Exception as exc:
            logger.warning("frontmatter parse failed for %s: %s", fpath, exc)
            continue
        content = (post.content or "").strip()
        if not content:
            continue
        rel = fpath.resolve().relative_to(vault).as_posix()
        title = str(post.metadata.get("title") or fpath.stem)
        tags = _normalize_tags(post.metadata.get("tags"))
        try:
            embedding = embed_text_safe(content)
        except Exception as exc:
            logger.error("embedding failed for %s: %s", rel, exc)
            continue
        vector.upsert_note(collection, rel, embedding, title, rel, tags, content)
        indexed += 1
    _write_last_index(time.time())
    return indexed
