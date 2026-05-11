"""Extractors that turn inbox files into Source objects."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlparse

import frontmatter

from second_brain import state
from second_brain.config import google_maps_api_key
from second_brain.models import Source

logger = logging.getLogger(__name__)


def _extract_article_markdown(path: Path) -> Optional[Source]:
    """Markdown clipping (es. Obsidian Web Clipper)."""
    try:
        post = frontmatter.load(path)
    except Exception as exc:
        logger.warning("frontmatter parse failed for %s: %s", path, exc)
        return None
    text = (post.content or "").strip()
    if not text:
        logger.warning("empty content for markdown article %s", path)
        return None
    meta = post.metadata
    title = str(meta.get("title") or path.stem)
    url = str(meta.get("source") or meta.get("url") or path.as_uri())
    return Source(
        type="article",
        title=title,
        url=url,
        content=text,
        state_id=state.get_item_id("articles", path),
        state_source="articles",
        source_path=path,
    )


def _extract_article_html(path: Path) -> Optional[Source]:
    try:
        from readability import Document  # type: ignore
        from lxml import html as lxml_html  # type: ignore
    except ImportError as exc:
        logger.error("readability-lxml not installed: %s", exc)
        return None
    try:
        raw = path.read_text(encoding="utf-8", errors="ignore")
    except OSError as exc:
        logger.warning("cannot read article %s: %s", path, exc)
        return None
    try:
        doc = Document(raw)
        title = doc.short_title() or path.stem
        text = lxml_html.fromstring(doc.summary()).text_content().strip()
    except Exception as exc:
        logger.warning("readability failed for %s (%s) — using raw text", path, exc)
        title, text = path.stem, raw
    if not text.strip():
        logger.warning("empty content for article %s", path)
        return None
    return Source(
        type="article",
        title=title,
        url=path.as_uri(),
        content=text,
        state_id=state.get_item_id("articles", path),
        state_source="articles",
        source_path=path,
    )


def extract_article(path: Path) -> Optional[Source]:
    """Dispatch on extension: .md → frontmatter+body; .html/.htm → readability."""
    if path.suffix.lower() == ".md":
        return _extract_article_markdown(path)
    return _extract_article_html(path)


YOUTUBE_META_KEYS = ("channel", "published", "duration", "thumbnail", "description")


def _youtube_metadata_from_md(path: Path) -> tuple[str | None, dict]:
    """Return ``(title, extra)`` from a YouTube ``.md`` file's frontmatter.

    All keys are optional — missing fields fall back to defaults set by the
    caller.
    """
    try:
        post = frontmatter.load(path)
    except Exception as exc:
        logger.warning("frontmatter parse failed for youtube %s: %s", path, exc)
        return None, {}
    meta = post.metadata
    title = str(meta["title"]) if meta.get("title") else None
    extra = {k: meta[k] for k in YOUTUBE_META_KEYS if k in meta}
    return title, extra


def extract_youtube(path: Path) -> Optional[Source]:
    try:
        from youtube_transcript_api import YouTubeTranscriptApi  # type: ignore
    except ImportError as exc:
        logger.error("youtube-transcript-api not installed: %s", exc)
        return None
    url = state.get_item_id("youtube", path)
    if not url.startswith("http"):
        logger.warning("no YouTube URL found in %s", path)
        return None
    parsed = urlparse(url)
    if "youtu.be" in parsed.netloc:
        video_id = parsed.path.lstrip("/")
    else:
        video_id = parse_qs(parsed.query).get("v", [""])[0]
    if not video_id:
        logger.warning("cannot extract video_id from %s", url)
        return None
    try:
        fetched = YouTubeTranscriptApi().fetch(video_id, languages=["it", "en"])
        transcript = " ".join(getattr(s, "text", "") for s in fetched).strip()
    except Exception as exc:
        logger.warning("transcript unavailable for %s: %s", url, exc)
        return None
    if not transcript:
        logger.warning("empty transcript for %s", url)
        return None

    title = f"YouTube video {video_id}"
    extra: dict = {"video_id": video_id}
    if path.suffix.lower() == ".md":
        md_title, md_extra = _youtube_metadata_from_md(path)
        if md_title:
            title = md_title
        extra.update(md_extra)

    return Source(
        type="youtube",
        title=title,
        url=url,
        content=transcript,
        state_id=url,
        state_source="youtube",
        source_path=path,
        extra=extra,
    )


def _fetch_place_reviews(place_id: str) -> list[str]:
    api_key = google_maps_api_key()
    if not api_key or not place_id.startswith("ChIJ"):
        return []
    try:
        import requests  # type: ignore

        resp = requests.get(
            "https://maps.googleapis.com/maps/api/place/details/json",
            params={"place_id": place_id, "fields": "reviews", "key": api_key, "language": "it"},
            timeout=10,
        )
        resp.raise_for_status()
        reviews = resp.json().get("result", {}).get("reviews", [])
        return [r.get("text", "").strip() for r in reviews[:3] if r.get("text")]
    except Exception as exc:
        logger.warning("Places API fetch failed for %s: %s", place_id, exc)
        return []


def extract_place(path: Path) -> Optional[Source]:
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("cannot read place %s: %s", path, exc)
        return None

    name = data.get("name", path.stem)
    category = data.get("category", "?")
    address = data.get("address", "?")
    rating = data.get("rating", "?")
    reviews_count = data.get("reviews_count", "?")
    notes_personali = data.get("notes_personali", "")
    place_url = data.get("url", "")
    place_id = data.get("place_id", "")

    lines = [
        f"Place: {name} ({category}, {address})",
        f"Rating: {rating}/5 — {reviews_count} recensioni",
    ]
    if notes_personali:
        lines.append(f'Note personali: "{notes_personali}"')

    reviews = _fetch_place_reviews(place_id) if place_id else []
    if reviews:
        lines.append("\nRecensioni recenti:")
        lines.extend(f'- "{r}"' for r in reviews)

    return Source(
        type="place",
        title=name,
        url=place_url,
        content="\n".join(lines),
        state_id=state.get_item_id("places", path),
        state_source="places",
        source_path=path,
        extra={
            "category": category,
            "address": address,
            "rating": rating,
            "notes_personali": notes_personali,
            "place_id": place_id,
        },
    )


EXTRACTORS = {
    "articles": extract_article,
    "youtube": extract_youtube,
    "places": extract_place,
}
