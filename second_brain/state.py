"""Delta tracking via per-source JSON state files.

State file layout (one per source under ``$VAULT_PATH/.state/``)::

    {
      "processed": ["id1", "id2", ...],
      "last_run": "2026-05-10T07:05:00"
    }
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from collections.abc import Iterable
from datetime import datetime, timedelta
from pathlib import Path

from second_brain.config import state_path, vault_path

logger = logging.getLogger(__name__)

SOURCE_TYPES = ("articles", "youtube", "places", "pdfs", "feeds")
SOURCE_EXTENSIONS = {
    "articles": (".html", ".htm", ".md"),
    "youtube": (".txt", ".md"),
    "places": (".json",),
}

YOUTUBE_URL_RE = re.compile(
    r"https?://(?:www\.|m\.)?(?:youtube\.com/watch\?[\w=&%-]*v=[\w-]+|youtu\.be/[\w-]+)"
)


def _state_dir() -> Path:
    return state_path()


def _state_file(source_type: str) -> Path:
    if source_type not in SOURCE_TYPES:
        raise ValueError(f"unknown source_type: {source_type}")
    return _state_dir() / f"processed_{source_type}.json"


def _load_state(path: Path) -> dict:
    if not path.exists():
        return {"processed": [], "last_run": None}
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("state file %s unreadable (%s) — starting fresh", path, exc)
        return {"processed": [], "last_run": None}
    data.setdefault("processed", [])
    data.setdefault("last_run", None)
    return data


def _atomic_write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".state-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _scan_inbox(source_type: str, inbox_path: Path) -> list[Path]:
    if not inbox_path.exists():
        return []
    exts = SOURCE_EXTENSIONS[source_type]
    return sorted(p for p in inbox_path.iterdir() if p.is_file() and p.suffix.lower() in exts)


def _extract_youtube_url(file_path: Path) -> str | None:
    try:
        text = file_path.read_text(encoding="utf-8", errors="ignore")
    except OSError as exc:
        logger.warning("cannot read youtube file %s: %s", file_path, exc)
        return None
    match = YOUTUBE_URL_RE.search(text)
    return match.group(0) if match else None


def _extract_place_id(file_path: Path) -> str | None:
    try:
        with file_path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("cannot parse place file %s: %s", file_path, exc)
        return None
    pid = data.get("place_id")
    return pid if isinstance(pid, str) and pid else None


def _relative_id(file_path: Path) -> str:
    vault = vault_path()
    try:
        return file_path.resolve().relative_to(vault).as_posix()
    except ValueError:
        return file_path.as_posix()


def get_item_id(source_type: str, file_path: Path) -> str:
    """Canonical ID used to dedupe a source item.

    Articles → vault-relative path. YouTube → URL inside the ``.txt``
    (path fallback). Places → ``place_id`` (path fallback). PDFs live on
    Google Drive and do not have a local file — the ID is the Drive
    ``fileId``, set externally by ``gather_pdf_sources``; this branch
    therefore raises if reached.
    """
    if source_type == "articles":
        return _relative_id(file_path)
    if source_type == "youtube":
        return _extract_youtube_url(file_path) or _relative_id(file_path)
    if source_type == "places":
        return _extract_place_id(file_path) or _relative_id(file_path)
    if source_type == "pdfs":
        raise ValueError("PDF state IDs are Drive fileIds — set them in gather_pdf_sources")
    if source_type == "feeds":
        raise ValueError("feed state IDs are entry guid/link — set them in gather_feed_sources")
    raise ValueError(f"unknown source_type: {source_type}")


def _bootstrap_yesterday(source_type: str, files: list[Path]) -> list[Path]:
    yesterday = (datetime.now() - timedelta(days=1)).date()
    selected = [p for p in files if datetime.fromtimestamp(p.stat().st_mtime).date() == yesterday]
    ids = [get_item_id(source_type, p) for p in selected]
    _atomic_write(
        _state_file(source_type),
        {"processed": ids, "last_run": datetime.now().isoformat(timespec="seconds")},
    )
    return selected


def get_new_items(source_type: str, inbox_path: Path) -> list[Path]:
    """Files in ``inbox_path`` whose IDs are not in the state file.

    First-run fallback: if the state file does not exist, use ``mtime ==
    yesterday`` and seed the state file with whatever was found.
    """
    state_path = _state_file(source_type)
    all_files = _scan_inbox(source_type, inbox_path)

    if not state_path.exists():
        logger.info("no state file for %s — bootstrapping by mtime=yesterday", source_type)
        return _bootstrap_yesterday(source_type, all_files)

    state = _load_state(state_path)
    seen = set(state["processed"])
    return [p for p in all_files if get_item_id(source_type, p) not in seen]


def filter_unseen(source_type: str, ids: Iterable[str]) -> list[str]:
    """Return only IDs not yet recorded in the state file.

    Useful for sources whose items don't live on the local filesystem
    (e.g. PDFs on Google Drive) — for those, ``get_new_items`` cannot
    scan an inbox folder, so the caller fetches IDs externally and
    delegates dedup here.

    No bootstrap-by-mtime: if the state file is missing, every ID is
    new and gets returned (and will be persisted on first
    ``mark_processed``).
    """
    state_path = _state_file(source_type)
    if not state_path.exists():
        return [i for i in ids if i]
    seen = set(_load_state(state_path)["processed"])
    return [i for i in ids if i and i not in seen]


def mark_processed(source_type: str, ids: Iterable[str]) -> None:
    """Append IDs to the processed list and refresh ``last_run``."""
    new_ids = [i for i in ids if i]
    if not new_ids:
        return
    state_path = _state_file(source_type)
    state = _load_state(state_path)
    seen = set(state["processed"])
    state["processed"].extend(i for i in new_ids if i not in seen)
    state["last_run"] = datetime.now().isoformat(timespec="seconds")
    _atomic_write(state_path, state)
    logger.info("marked %d new IDs as processed for %s", len(new_ids), source_type)


def reset_state(source_type: str) -> None:
    """Empty the state file to force a full re-process on next run."""
    _atomic_write(
        _state_file(source_type),
        {"processed": [], "last_run": None},
    )
    logger.info("reset state for %s", source_type)
