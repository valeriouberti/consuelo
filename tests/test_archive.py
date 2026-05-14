"""Unit tests for consuelo.archive."""

from __future__ import annotations

from pathlib import Path

from consuelo.archive import (
    archive_previous_daily,
    archive_sources,
)
from consuelo.models import Source


def _make_source(state_source: str, source_path: Path) -> Source:
    return Source(
        type="article" if state_source == "articles" else state_source[:-1],  # type: ignore[arg-type]
        title="t",
        url="u",
        content="c",
        state_id=f"Inbox/{state_source}/{source_path.name}",
        state_source=state_source,  # type: ignore[arg-type]
        source_path=source_path,
    )


def test_archive_sources_moves_articles_to_dated_subdir(vault: Path) -> None:
    f = vault / "Inbox" / "articles" / "foo.md"
    f.write_text("body")
    moved = archive_sources([_make_source("articles", f)], "2026-05-11")
    assert moved == 1
    assert not f.exists()
    assert (vault / "Notes" / "articles" / "2026-05-11" / "foo.md").read_text() == "body"


def test_archive_sources_handles_youtube_and_places(vault: Path) -> None:
    y = vault / "Inbox" / "youtube" / "vid.txt"
    p = vault / "Inbox" / "places" / "po.json"
    y.write_text("https://youtu.be/x")
    p.write_text('{"name":"x"}')
    moved = archive_sources(
        [_make_source("youtube", y), _make_source("places", p)],
        "2026-05-11",
    )
    assert moved == 2
    assert (vault / "Notes" / "youtube" / "2026-05-11" / "vid.txt").exists()
    assert (vault / "Notes" / "places" / "2026-05-11" / "po.json").exists()


def test_archive_sources_unique_suffix_on_collision(vault: Path) -> None:
    f1 = vault / "Inbox" / "articles" / "dup.md"
    f1.write_text("first")
    archive_sources([_make_source("articles", f1)], "2026-05-11")
    f2 = vault / "Inbox" / "articles" / "dup.md"
    f2.write_text("second")
    moved = archive_sources([_make_source("articles", f2)], "2026-05-11")
    assert moved == 1
    target_dir = vault / "Notes" / "articles" / "2026-05-11"
    assert (target_dir / "dup.md").read_text() == "first"
    assert (target_dir / "dup_1.md").read_text() == "second"


def test_archive_sources_skips_missing_source_path(vault: Path) -> None:
    f = vault / "Inbox" / "articles" / "ghost.md"  # never created
    moved = archive_sources([_make_source("articles", f)], "2026-05-11")
    assert moved == 0


def test_archive_previous_daily_moves_dated_files(vault: Path) -> None:
    (vault / "Daily" / "2026-05-09.md").write_text("d9")
    (vault / "Daily" / "2026-05-10.md").write_text("d10")
    (vault / "Daily" / "2026-05-11.md").write_text("d11")
    archived = archive_previous_daily("2026-05-11")
    assert archived == 2
    assert (vault / "Daily" / "2026-05-11.md").read_text() == "d11"
    assert (vault / "Daily" / "2026" / "05" / "2026-05-09.md").read_text() == "d9"
    assert (vault / "Daily" / "2026" / "05" / "2026-05-10.md").read_text() == "d10"


def test_archive_previous_daily_ignores_non_dated_files(vault: Path) -> None:
    (vault / "Daily" / "README.md").write_text("readme")
    (vault / "Daily" / "2026-05-09.md").write_text("d9")
    archived = archive_previous_daily("2026-05-11")
    assert archived == 1
    assert (vault / "Daily" / "README.md").exists()
