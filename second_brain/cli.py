"""Click CLI: ``second-brain run`` and ``second-brain index``."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from datetime import datetime, timedelta

import click
from dotenv import load_dotenv

from second_brain.archive import archive_previous_daily, archive_sources
from second_brain.config import configure_logging, llm_mode
from second_brain.llm import reset_usage, usage_summary
from second_brain.pipeline import (
    ask as ask_pipeline,
)
from second_brain.pipeline import (
    commit_state,
    embed_sources,
    enrich_sources,
    gather_sources,
    index_notes,
    log_status_report,
)
from second_brain.rendering import render_daily, write_daily
from second_brain.sources import archive_pdf_sources

logger = logging.getLogger(__name__)


@click.group()
def cli() -> None:
    """Second Brain — daily Obsidian recap workflow."""
    load_dotenv()
    configure_logging()


@cli.command()
@click.option("--date", "date_str", help="Target date YYYY-MM-DD (default: yesterday).")
@click.option("--dry-run", is_flag=True, help="Print to stdout; do not write file or update state.")
@click.option(
    "--mode",
    type=click.Choice(["local", "cloud"]),
    default=None,
    help="Override LLM_MODE for this run.",
)
def run(date_str: str | None, dry_run: bool, mode: str | None) -> None:
    """Process new inbox items and write the daily recap."""
    if mode:
        os.environ["LLM_MODE"] = mode

    if date_str:
        try:
            target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            logger.error("invalid --date format, expected YYYY-MM-DD")
            sys.exit(2)
    else:
        target_date = (datetime.now() - timedelta(days=1)).date()
    date_iso = target_date.isoformat()

    logger.info("target date: %s (mode=%s, dry_run=%s)", date_iso, llm_mode(), dry_run)
    reset_usage()

    async def _pipeline():
        sources = await gather_sources(target_date=target_date)
        if not sources:
            return sources
        await embed_sources(sources)
        await enrich_sources(sources)
        return sources

    sources = []
    try:
        sources = asyncio.run(_pipeline())
        if not sources:
            logger.info("no new sources to process — nothing to do")
            return
    except KeyboardInterrupt:
        logger.warning("interrupted — writing partial output")

    rendered = render_daily(date_iso, sources)

    if dry_run:
        sys.stdout.write(rendered)
        sys.stdout.flush()
        return

    archived_daily = archive_previous_daily(date_iso)
    if archived_daily:
        logger.info("archived %d previous daily note(s)", archived_daily)

    try:
        out_path = write_daily(date_iso, rendered)
        logger.info("wrote %s", out_path)
    except OSError as exc:
        logger.error("write failed — state NOT updated: %s", exc)
        sys.exit(1)

    commit_state(sources)

    moved = archive_sources(sources, date_iso)
    if moved:
        logger.info("archived %d processed source file(s)", moved)

    moved_pdfs = archive_pdf_sources(sources)
    if moved_pdfs:
        logger.info("archived %d processed PDF(s) on Drive", moved_pdfs)

    log_status_report(sources)

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
@click.option("-k", "top_k", default=8, show_default=True, help="How many vault entries to retrieve.")
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
