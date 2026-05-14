"""Unit tests for consuelo.state."""

from __future__ import annotations

import json
from pathlib import Path

from consuelo import state


def test_get_item_id_article(vault: Path) -> None:
    f = vault / "Inbox" / "articles" / "foo.html"
    f.write_text("<html></html>")
    assert state.get_item_id("articles", f) == "Inbox/articles/foo.html"


def test_get_item_id_youtube_extracts_url(vault: Path) -> None:
    f = vault / "Inbox" / "youtube" / "v.txt"
    f.write_text("watch this: https://youtu.be/abc123XYZ_-\n")
    assert state.get_item_id("youtube", f) == "https://youtu.be/abc123XYZ_-"


def test_get_item_id_place_uses_place_id(vault: Path) -> None:
    f = vault / "Inbox" / "places" / "p.json"
    f.write_text(json.dumps({"place_id": "ChIJxxx", "name": "X"}))
    assert state.get_item_id("places", f) == "ChIJxxx"


def test_get_item_id_place_fallback_to_path(vault: Path) -> None:
    f = vault / "Inbox" / "places" / "p.json"
    f.write_text(json.dumps({"name": "X"}))  # no place_id
    assert state.get_item_id("places", f) == "Inbox/places/p.json"


def test_mark_processed_then_get_new_items_excludes(vault: Path) -> None:
    inbox = vault / "Inbox" / "articles"
    a = inbox / "a.html"
    b = inbox / "b.html"
    a.write_text("x")
    b.write_text("x")
    # Force state file to exist so we skip the mtime bootstrap path.
    state.mark_processed("articles", ["Inbox/articles/a.html"])
    new_items = state.get_new_items("articles", inbox)
    assert new_items == [b]


def test_reset_state_clears_processed(vault: Path) -> None:
    state.mark_processed("articles", ["one", "two"])
    state.reset_state("articles")
    state_file = vault / ".state" / "processed_articles.json"
    assert json.loads(state_file.read_text())["processed"] == []
