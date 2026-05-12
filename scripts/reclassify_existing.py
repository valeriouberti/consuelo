"""One-shot: classify existing vault notes + move under Notes/<Category>/.

Walks the vault for .md files outside Notes/Inbox/Daily/.obsidian, builds
Source records, runs embed + classify (with existing folder names as
category hints), writes the rendered note under Notes/<Category>/, then
deletes the original.

Usage::
    python -m scripts.reclassify_existing            # full run
    python -m scripts.reclassify_existing --dry-run  # log what would happen
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

import frontmatter
from dotenv import load_dotenv

load_dotenv()

from second_brain.config import configure_logging, vault_path  # noqa: E402
from second_brain.models import Source  # noqa: E402
from second_brain.pipeline import _classify_one, embed_sources  # noqa: E402
from second_brain.rendering import write_classified_note  # noqa: E402

logger = logging.getLogger("reclassify")

EXCLUDED_TOP = {
    "Notes",
    "Inbox",
    "Daily",
    ".obsidian",
    ".config",
    ".cache",
    ".state",
    ".chroma",
    ".trash",
}


def gather_existing_notes() -> list[Source]:
    vault = vault_path()
    sources: list[Source] = []
    for p in vault.rglob("*.md"):
        rel = p.relative_to(vault)
        if not rel.parts or rel.parts[0] in EXCLUDED_TOP:
            continue
        try:
            post = frontmatter.load(str(p))
        except Exception as exc:
            logger.error("frontmatter parse failed for %s: %s", rel, exc)
            continue
        title = str(post.metadata.get("title") or p.stem).strip() or p.stem
        body = post.content.strip()
        if not body:
            logger.warning("skipping empty note: %s", rel)
            continue
        sources.append(
            Source(
                type="article",
                title=title,
                url="",
                content=body,
                state_id=str(rel),
                state_source="articles",
                source_path=p,
            )
        )
    return sources


def collect_category_hints() -> list[str]:
    """All current subdirectory names across the vault — gives the LLM the
    full universe of categories the user already organizes by, not just
    top-level Notes/."""
    vault = vault_path()
    hints: set[str] = set()
    for p in vault.rglob("*"):
        if not p.is_dir():
            continue
        rel = p.relative_to(vault)
        if not rel.parts or rel.parts[0] in EXCLUDED_TOP:
            continue
        name = p.name
        if name.startswith("."):
            continue
        cleaned = name.lstrip("0123456789_- ").strip()
        if cleaned:
            hints.add(cleaned)
    return sorted(hints)


async def classify_all(sources: list[Source], categories: list[str]) -> None:
    from second_brain.config import async_concurrency
    from second_brain import vector

    collection = vector.open_collection()
    sem = asyncio.Semaphore(async_concurrency())

    async def one(s: Source) -> None:
        async with sem:
            related = (
                await asyncio.to_thread(vector.query_correlations, collection, s.embedding)
                if s.embedding and collection is not None
                else []
            )
            await _classify_one(s, related, categories)
            logger.info("classified: %s -> %s [%s]", s.title, s.category, s.status)

    await asyncio.gather(*(one(s) for s in sources))


async def main_async(dry_run: bool) -> int:
    sources = gather_existing_notes()
    if not sources:
        logger.info("no notes to reclassify")
        return 0

    categories = collect_category_hints()
    logger.info("found %d notes to reclassify; category hints: %s", len(sources), categories)

    logger.info("embedding %d notes...", len(sources))
    await embed_sources(sources)

    logger.info("classifying...")
    await classify_all(sources, categories)

    vault = vault_path()
    moved = 0
    failed = 0
    for s in sources:
        if s.status != "ok":
            failed += 1
            logger.warning("skip move (status=%s): %s", s.status, s.source_path)
            continue
        if dry_run:
            logger.info("[dry-run] would move %s -> Notes/%s/", s.source_path.relative_to(vault), s.category)
            continue
        try:
            target = write_classified_note(s)
            s.source_path.unlink()
            moved += 1
            logger.info("moved: %s -> %s", s.source_path.name, target.relative_to(vault))
        except Exception as exc:
            failed += 1
            logger.error("write/delete failed for %s: %s", s.source_path, exc)

    logger.info("done: %d moved, %d failed, %d total", moved, failed, len(sources))
    return 0 if failed == 0 else 1


def main() -> None:
    configure_logging()
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    sys.exit(asyncio.run(main_async(args.dry_run)))


if __name__ == "__main__":
    main()
