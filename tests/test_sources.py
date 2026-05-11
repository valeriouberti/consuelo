"""Unit tests for second_brain.sources."""

from __future__ import annotations

from pathlib import Path

from unittest.mock import patch

from second_brain.sources import _youtube_metadata_from_md, extract_article


def test_extract_article_markdown_uses_frontmatter(vault: Path) -> None:
    f = vault / "Inbox" / "articles" / "clip.md"
    f.write_text(
        "---\n"
        'title: "Titolo Originale"\n'
        'source: "https://example.com/post"\n'
        "---\n\n"
        "Corpo dell'articolo in markdown.\n"
    )
    src = extract_article(f)
    assert src is not None
    assert src.type == "article"
    assert src.title == "Titolo Originale"
    assert src.url == "https://example.com/post"
    assert "Corpo dell'articolo" in src.content
    assert src.state_id == "Inbox/articles/clip.md"
    assert src.state_source == "articles"


def test_extract_article_markdown_without_frontmatter_falls_back(vault: Path) -> None:
    f = vault / "Inbox" / "articles" / "plain.md"
    f.write_text("# Heading\n\nSolo testo, niente frontmatter.\n")
    src = extract_article(f)
    assert src is not None
    assert src.title == "plain"
    assert src.url.startswith("file://")
    assert "Solo testo" in src.content


def test_extract_article_markdown_empty_returns_none(vault: Path) -> None:
    f = vault / "Inbox" / "articles" / "empty.md"
    f.write_text("---\ntitle: foo\n---\n\n   \n")
    assert extract_article(f) is None


def test_youtube_metadata_from_md_reads_known_keys(vault: Path) -> None:
    f = vault / "Inbox" / "youtube" / "vid.md"
    f.write_text(
        "---\n"
        'title: "Rust Async Talk"\n'
        'channel: "Rust Lang"\n'
        'published: 2026-04-15\n'
        'duration: "00:42:10"\n'
        'thumbnail: "https://img.youtube.com/vi/abc/maxresdefault.jpg"\n'
        'ignored: "drop me"\n'
        "---\n\n"
        "https://www.youtube.com/watch?v=abc123XYZ_-\n"
    )
    title, extra = _youtube_metadata_from_md(f)
    assert title == "Rust Async Talk"
    assert extra["channel"] == "Rust Lang"
    assert "ignored" not in extra
    assert extra["duration"] == "00:42:10"


class _FakeSnippet:
    def __init__(self, text: str) -> None:
        self.text = text


def test_extract_youtube_md_uses_frontmatter_title(vault: Path) -> None:
    from second_brain.sources import extract_youtube

    f = vault / "Inbox" / "youtube" / "talk.md"
    f.write_text(
        "---\n"
        'title: "Custom Title From FM"\n'
        'channel: "ACME"\n'
        "---\n\n"
        "https://youtu.be/abc123XYZ_-\n"
    )
    snippets = [_FakeSnippet("hello"), _FakeSnippet("world")]
    with patch("youtube_transcript_api.YouTubeTranscriptApi.fetch", return_value=snippets):
        src = extract_youtube(f)
    assert src is not None
    assert src.title == "Custom Title From FM"
    assert src.content == "hello world"
    assert src.url == "https://youtu.be/abc123XYZ_-"
    assert src.state_id == "https://youtu.be/abc123XYZ_-"
    assert src.extra["channel"] == "ACME"
    assert src.extra["video_id"] == "abc123XYZ_-"
