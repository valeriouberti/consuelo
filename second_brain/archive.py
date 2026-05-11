"""Archive helpers — move processed sources and previous Daily notes.

Layout managed:
    vault/Notes/{articles,youtube,places}/{YYYY-MM-DD}/<file>   # processed sources
    vault/Daily/{YYYY}/{MM}/{YYYY-MM-DD}.md                     # archived daily

These cartelle vengono ESCLUSE dall'indexing di ``Notes/`` per evitare che
contenuti grezzi importati inquinino lo spazio delle correlazioni.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from second_brain.config import vault_path
from second_brain.models import Source

logger = logging.getLogger(__name__)

EXCLUDED_NOTES_SUBDIRS: frozenset[str] = frozenset({"articles", "youtube", "places"})


def _unique_target(target: Path) -> Path:
    """Avoid clobbering existing files: append numeric suffix if needed."""
    if not target.exists():
        return target
    stem, suffix = target.stem, target.suffix
    i = 1
    while True:
        candidate = target.with_name(f"{stem}_{i}{suffix}")
        if not candidate.exists():
            return candidate
        i += 1


def archive_sources(sources: list[Source], run_date_iso: str) -> int:
    """Move each source's inbox file to ``Notes/{state_source}/{run_date}/``.

    Returns the number of files moved. Failures are logged but never raised
    — archiving is best-effort and must not block the workflow (state has
    already been committed by the caller).
    """
    vault = vault_path()
    moved = 0
    for s in sources:
        if s.source_path is None:
            continue
        src = Path(s.source_path)
        if not src.exists():
            logger.debug("source file missing, skip archive: %s", src)
            continue
        target_dir = vault / "Notes" / s.state_source / run_date_iso
        target_dir.mkdir(parents=True, exist_ok=True)
        target = _unique_target(target_dir / src.name)
        try:
            src.rename(target)
            moved += 1
            logger.info("archived %s → %s", src.name, target.relative_to(vault))
        except OSError as exc:
            logger.warning("failed to archive %s: %s", src, exc)
    return moved


def archive_previous_daily(current_date_iso: str) -> int:
    """Move every ``Daily/*.md`` (except the current run's file) under
    ``Daily/{YYYY}/{MM}/{YYYY-MM-DD}.md``.

    Files whose stem is not a valid ``YYYY-MM-DD`` date are left alone.
    Returns the number of files moved.
    """
    daily_dir = vault_path() / "Daily"
    if not daily_dir.exists():
        return 0
    archived = 0
    for f in sorted(daily_dir.glob("*.md")):
        if f.stem == current_date_iso:
            continue
        try:
            d = datetime.strptime(f.stem, "%Y-%m-%d").date()
        except ValueError:
            continue
        target = daily_dir / f"{d.year:04d}" / f"{d.month:02d}" / f.name
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            logger.warning("archive target exists, skip: %s", target)
            continue
        try:
            f.rename(target)
            archived += 1
            logger.info("archived Daily %s → %s", f.name, target.relative_to(daily_dir.parent))
        except OSError as exc:
            logger.warning("failed to archive daily %s: %s", f, exc)
    return archived
