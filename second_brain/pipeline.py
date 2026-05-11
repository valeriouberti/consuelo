"""High-level orchestrators: daily recap pipeline + Notes/ indexer."""

from __future__ import annotations

import asyncio
import logging
import sys
import time
import traceback
from datetime import date
from pathlib import Path

import frontmatter
from tqdm import tqdm

from second_brain import state, vector
from second_brain.archive import EXCLUDED_NOTES_SUBDIRS
from second_brain.config import async_concurrency, vault_path
from second_brain.llm import (
    call_llm_async,
    embed_text_safe,
    embed_text_safe_async,
    load_prompt,
    parse_llm_json,
)
from second_brain.models import Source
from second_brain.rendering import kebab
from second_brain.sources import EXTRACTORS, gather_feed_sources, gather_pdf_sources

logger = logging.getLogger(__name__)

LAST_INDEX_FILE = ".state/last_index.txt"


# ---------- daily recap ----------


def _gather_file_sources() -> list[Source]:
    """Sync extractors (filesystem-based): articles/youtube/places."""
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


async def gather_sources(target_date: date | None = None) -> list[Source]:
    """Gather sources from all configured input channels concurrently.

    File-based extractors + Drive PDF download run in threads (sync libs);
    feed gathering is natively async. All three run in parallel.

    ``target_date`` is forwarded to ``gather_feed_sources`` so that the
    feed date filter can drop entries published outside the run's window
    (see ``FEED_DAYS_BACK`` env). File/PDF sources are not date-filtered
    here — they live in the Inbox and state already gates them.
    """
    file_task = asyncio.to_thread(_gather_file_sources)
    pdf_task = asyncio.to_thread(gather_pdf_sources)
    feed_task = gather_feed_sources(target_date=target_date)

    file_res, pdf_res, feed_res = await asyncio.gather(
        file_task, pdf_task, feed_task, return_exceptions=True
    )

    sources: list[Source] = []
    if isinstance(file_res, BaseException):
        logger.error("file-based gather failed: %s", file_res)
    else:
        sources.extend(file_res)

    if isinstance(pdf_res, BaseException):
        logger.error("gather_pdf_sources failed: %s", pdf_res)
    else:
        logger.info("pdfs: %d source(s) from Drive", len(pdf_res))
        sources.extend(pdf_res)

    if isinstance(feed_res, BaseException):
        logger.error("gather_feed_sources failed: %s", feed_res)
    else:
        logger.info("feeds: %d source(s) from RSS", len(feed_res))
        sources.extend(feed_res)

    return sources


async def embed_sources(sources: list[Source]) -> None:
    """Embed every source concurrently, bounded by ``async_concurrency()``.

    A failed embedding leaves ``s.embedding = None`` so downstream code
    can skip vector correlation for that source without aborting the
    whole run.
    """
    sem = asyncio.Semaphore(async_concurrency())

    async def one(s: Source) -> None:
        async with sem:
            try:
                s.embedding = await embed_text_safe_async(s.content)
            except Exception as exc:
                logger.warning("embedding failed for %s: %s", s.title, exc)
                s.embedding = None

    await asyncio.gather(*(one(s) for s in sources))


def _build_user_message(source: Source, related: list[dict]) -> str:
    """English framing — matches the system prompt language and avoids
    biasing the model toward Italian. The output language is controlled by
    the prompt's LANGUAGE RULE based on `source.content` only."""
    related_block = (
        "\n".join(f"- [[{r['path']}]] ({r['title']}): {r['preview']}" for r in related)
        or "(no related notes)"
    )
    return f"Content to analyze:\n{source.content}\n\nRelated Obsidian notes:\n{related_block}\n"


async def _enrich_async(source: Source, related: list[dict]) -> None:
    prompt_name = "place.txt" if source.type == "place" else "recap.txt"
    system_prompt = load_prompt(prompt_name)
    user_msg = _build_user_message(source, related)
    try:
        raw = await call_llm_async(system_prompt, user_msg)
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


async def enrich_sources(sources: list[Source]) -> None:
    """Vector-correlate + LLM-enrich every source concurrently.

    ``vector.query_correlations`` is sync (chromadb), so it runs in a
    thread before each LLM call. The shared ``Semaphore`` bounds both
    the vector queries and the LLM calls together — they cooperate
    naturally because each source's chain is fully sequential.
    """
    collection = vector.open_collection()
    sem = asyncio.Semaphore(async_concurrency())

    async def one(s: Source) -> None:
        async with sem:
            related = (
                await asyncio.to_thread(vector.query_correlations, collection, s.embedding)
                if s.embedding
                else []
            )
            await _enrich_async(s, related)

    await asyncio.gather(*(one(s) for s in sources))


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
