"""Click CLI: ``second-brain run`` and ``second-brain index``."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from datetime import datetime

import click
from dotenv import load_dotenv

from second_brain.config import configure_logging, llm_mode
from second_brain.llm import reset_usage, usage_summary
from second_brain.pipeline import (
    ask as ask_pipeline,
)
from second_brain.pipeline import (
    classify_sources,
    commit_state,
    embed_sources,
    gather_sources,
    index_notes,
    log_status_report,
)
from second_brain.rendering import render_classified_note, write_classified_note

logger = logging.getLogger(__name__)


@click.group()
def cli() -> None:
    """Second Brain — daily Obsidian recap workflow."""
    load_dotenv()
    configure_logging()


@cli.command()
@click.option(
    "--dry-run", is_flag=True, help="Print rendered notes to stdout; do not write or update state."
)
@click.option(
    "--mode",
    type=click.Choice(["local", "cloud"]),
    default=None,
    help="Override LLM_MODE for this run.",
)
def run(dry_run: bool, mode: str | None) -> None:
    """Per-article processing: classify, summarize, route into Notes/<Category>/.

    Picks up every new file in Inbox/articles/, runs LLM classification +
    summary, writes the enriched note in Notes/<Category>/<slug>.md, and
    consumes the original. Other source types (YouTube, PDF, feed, place)
    are gathered but currently no-ops in the per-item flow — they will
    follow in a subsequent iteration.
    """
    if mode:
        os.environ["LLM_MODE"] = mode

    logger.info("mode=%s, dry_run=%s", llm_mode(), dry_run)
    reset_usage()

    async def _pipeline():
        sources = await gather_sources(target_date=None)
        if not sources:
            return sources
        await embed_sources(sources)
        await classify_sources(sources)
        return sources

    sources: list = []
    try:
        sources = asyncio.run(_pipeline()) or []
    except KeyboardInterrupt:
        logger.warning("interrupted — partial state, nothing committed")
        return

    article_sources = [s for s in sources if s.type == "article"]
    other_sources = [s for s in sources if s.type != "article"]
    if other_sources:
        logger.warning(
            "skipping %d non-article source(s) — per-item flow not implemented yet",
            len(other_sources),
        )

    if not article_sources:
        logger.info("no new articles to process — nothing to do")
        return

    today_iso = datetime.now().date().isoformat()

    if dry_run:
        for s in article_sources:
            sys.stdout.write(render_classified_note(s, today_iso))
            sys.stdout.write("\n\n")
        sys.stdout.flush()
        return

    written: list[tuple] = []
    for s in article_sources:
        try:
            out_path = write_classified_note(s)
        except OSError as exc:
            logger.error("write failed for %s: %s", s.title, exc)
            s.status = "llm_fail"
            continue
        logger.info("wrote %s [%s]", out_path, s.category)
        written.append((s, out_path))

    if written:
        commit_state([s for s, _ in written])
        for s, _ in written:
            src = s.source_path
            if src is not None and src.exists():
                try:
                    src.unlink()
                    logger.info("consumed inbox file %s", src.name)
                except OSError as exc:
                    logger.warning("could not delete inbox %s: %s", src, exc)

    log_status_report(article_sources)

    u = usage_summary()
    if u["cache_hits"]:
        logger.info("embedding cache: %d hits", u["cache_hits"])
    if llm_mode() == "cloud":
        logger.info(
            "cost: $%.4f (chat: %d prompt + %d completion @ %s | embed: %d @ %s)",
            u["estimated_usd"],
            u["prompt_tokens"],
            u["completion_tokens"],
            u["chat_model"],
            u["embed_tokens"],
            u["embed_model"],
        )


@cli.command()
@click.argument("query")
@click.option(
    "-k", "top_k", default=8, show_default=True, help="How many vault entries to retrieve."
)
@click.option(
    "--mode",
    type=click.Choice(["local", "cloud"]),
    default=None,
    help="Override LLM_MODE for this query.",
)
def ask(query: str, top_k: int, mode: str | None) -> None:
    """Ask a question over the indexed vault (Notes/ + Daily/).

    Example::

        second-brain ask "cosa ho letto sui Kubernetes operators?"
    """
    if mode:
        os.environ["LLM_MODE"] = mode
    reset_usage()
    answer, hits = ask_pipeline(query, k=top_k)
    if not hits:
        logger.warning("no vault matches — answer based on empty context")
    sys.stdout.write(answer.rstrip() + "\n")
    sys.stdout.flush()
    u = usage_summary()
    if u["cache_hits"]:
        logger.info("embedding cache: %d hits", u["cache_hits"])
    if llm_mode() == "cloud":
        logger.info(
            "cost: $%.4f (%d prompt + %d completion tok)",
            u["estimated_usd"],
            u["prompt_tokens"],
            u["completion_tokens"],
        )


@cli.command()
@click.option("--incremental", is_flag=True, help="Only index notes modified since last index.")
@click.option(
    "--no-daily",
    is_flag=True,
    help="Skip Daily/ recaps (default: include past Dailies for cross-temporal recall).",
)
def index(incremental: bool, no_daily: bool) -> None:
    """Index Notes/ (and Daily/ by default) into ChromaDB."""
    start = time.time()
    count = index_notes(incremental, include_daily=not no_daily)
    elapsed = time.time() - start
    logger.info("done — %d notes indexed in %.1fs", count, elapsed)


if __name__ == "__main__":
    cli()
