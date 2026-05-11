"""Extractors that turn inbox files into Source objects."""

from __future__ import annotations

import json
import logging
import statistics
import tempfile
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import frontmatter

from second_brain import drive, state
from second_brain.config import (
    gdrive_inbox_pdf_folder_id,
    gdrive_processed_pdf_folder_id,
    google_maps_api_key,
)
from second_brain.models import Source

logger = logging.getLogger(__name__)


def _extract_article_markdown(path: Path) -> Source | None:
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


def _extract_article_html(path: Path) -> Source | None:
    try:
        from lxml import html as lxml_html  # type: ignore
        from readability import Document  # type: ignore
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


def extract_article(path: Path) -> Source | None:
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


def extract_youtube(path: Path) -> Source | None:
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


def extract_place(path: Path) -> Source | None:
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


# ---------- PDF (Google Drive) ----------

PDF_HEADING_SIZE_RATIO = 1.30   # word > median * ratio counts as heading
PDF_MIN_ARTICLE_CHARS = 200     # discard tiny fragments (captions, ads, ToCs)
PDF_TITLE_MAX_LEN = 120


def _pdf_words(pdf_path: Path) -> list[dict]:
    """Return every word in the PDF as ``{text, size, page, top, x0}``.

    Pages are scanned in order; within a page, words are sorted by
    (top, x0) so reading order is preserved for single-column layouts
    and is approximately correct for two-column layouts after the
    upstream ``extract_words`` call applies its own grouping.
    """
    try:
        import pdfplumber  # type: ignore
    except ImportError as exc:
        logger.error("pdfplumber not installed: %s", exc)
        return []
    words: list[dict] = []
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page_idx, page in enumerate(pdf.pages):
                page_words = page.extract_words(
                    extra_attrs=["size"], use_text_flow=True
                )
                for w in page_words:
                    words.append(
                        {
                            "text": w["text"],
                            "size": float(w.get("size") or 0),
                            "page": page_idx,
                            "top": float(w.get("top") or 0),
                            "x0": float(w.get("x0") or 0),
                        }
                    )
    except Exception as exc:
        logger.warning("pdfplumber failed on %s: %s", pdf_path, exc)
        return []
    return words


def _group_lines(words: list[dict]) -> list[dict]:
    """Collapse ``words`` into lines: same page, same y-row (top within 2px)."""
    if not words:
        return []
    lines: list[dict] = []
    current: list[dict] = []
    for w in words:
        if not current:
            current = [w]
            continue
        last = current[-1]
        same_row = w["page"] == last["page"] and abs(w["top"] - last["top"]) < 2.0
        if same_row:
            current.append(w)
        else:
            lines.append(_line_from_words(current))
            current = [w]
    if current:
        lines.append(_line_from_words(current))
    return lines


def _line_from_words(ws: list[dict]) -> dict:
    text = " ".join(w["text"] for w in ws).strip()
    size = max(w["size"] for w in ws) if ws else 0.0
    return {"text": text, "size": size, "page": ws[0]["page"]}


def _split_articles(lines: list[dict], body_size: float) -> list[tuple[str, str]]:
    """Split ``lines`` into ``(title, body)`` tuples using a font-size heuristic.

    A line whose max word size exceeds ``body_size * PDF_HEADING_SIZE_RATIO``
    is treated as an article boundary; subsequent non-heading lines form
    the body until the next heading. Bodies under ``PDF_MIN_ARTICLE_CHARS``
    are dropped (likely ads, page headers, or stray captions).

    ``body_size`` should be the dominant font size of running text — pass
    in the median across raw words (not lines), so a few large headings
    don't pull the threshold up enough to hide themselves.
    """
    if not lines or body_size <= 0:
        return []
    threshold = body_size * PDF_HEADING_SIZE_RATIO

    articles: list[tuple[str, list[str]]] = []
    current_title: str | None = None
    current_body: list[str] = []
    for ln in lines:
        if ln["size"] >= threshold and ln["text"]:
            if current_title is not None:
                articles.append((current_title, current_body))
            current_title = ln["text"][:PDF_TITLE_MAX_LEN]
            current_body = []
        else:
            if ln["text"]:
                current_body.append(ln["text"])
    if current_title is not None:
        articles.append((current_title, current_body))

    out: list[tuple[str, str]] = []
    for title, body_lines in articles:
        body = "\n".join(body_lines).strip()
        if len(body) >= PDF_MIN_ARTICLE_CHARS:
            out.append((title, body))
    return out


def extract_pdf(
    local_path: Path,
    *,
    drive_file_id: str,
    drive_name: str,
    drive_view_url: str,
) -> list[Source]:
    """Return one Source per detected article in the PDF at ``local_path``.

    Headings are detected by relative font size. If no heading clears the
    threshold (e.g. uniform-font reports), the whole document is emitted
    as a single Source. All Sources from the same PDF share the same
    ``state_id`` (the Drive ``fileId``) so dedup is at PDF granularity.
    """
    words = _pdf_words(local_path)
    if not words:
        return []
    word_sizes = [w["size"] for w in words if w["size"] > 0]
    body_size = statistics.median(word_sizes) if word_sizes else 0.0
    lines = _group_lines(words)
    articles = _split_articles(lines, body_size)

    if not articles:
        full_text = "\n".join(ln["text"] for ln in lines if ln["text"]).strip()
        if len(full_text) < PDF_MIN_ARTICLE_CHARS:
            logger.warning("PDF %s produced no usable text", drive_name)
            return []
        articles = [(Path(drive_name).stem, full_text)]

    sources: list[Source] = []
    for idx, (title, body) in enumerate(articles):
        sources.append(
            Source(
                type="pdf",
                title=title,
                url=drive_view_url,
                content=body,
                state_id=drive_file_id,
                state_source="pdfs",
                source_path=None,
                extra={
                    "drive_file_id": drive_file_id,
                    "drive_name": drive_name,
                    "drive_view_url": drive_view_url,
                    "article_index": idx,
                },
            )
        )
    return sources


def gather_pdf_sources() -> list[Source]:
    """List unseen PDFs in the Drive inbox folder, download, extract.

    Returns an empty list if Drive is not configured — keeps the
    pipeline non-fatal when running locally without GCP credentials.
    """
    inbox_folder = gdrive_inbox_pdf_folder_id()
    if not inbox_folder:
        logger.debug("GDRIVE_INBOX_PDF_FOLDER_ID not set — skipping PDF source")
        return []

    files = drive.list_pdfs(inbox_folder)
    if not files:
        logger.info("pdfs: 0 files in Drive inbox")
        return []

    unseen_ids = set(state.filter_unseen("pdfs", [f.id for f in files]))
    new_files = [f for f in files if f.id in unseen_ids]
    logger.info("pdfs: %d new file(s) on Drive", len(new_files))
    if not new_files:
        return []

    sources: list[Source] = []
    for f in new_files:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=True) as tmp:
            tmp_path = Path(tmp.name)
            if drive.download_pdf(f.id, tmp_path) is None:
                continue
            try:
                file_sources = extract_pdf(
                    tmp_path,
                    drive_file_id=f.id,
                    drive_name=f.name,
                    drive_view_url=f.web_view_link,
                )
            except Exception:
                logger.exception("extract_pdf failed for %s", f.name)
                continue
            if not file_sources:
                logger.warning("no articles extracted from %s — skipping", f.name)
                continue
            sources.extend(file_sources)
    return sources


def archive_pdf_sources(sources: list[Source]) -> int:
    """Move each PDF's Drive file from inbox to processed. Returns count moved.

    Deduplicates on ``drive_file_id`` since multiple Sources can come
    from the same PDF.
    """
    inbox_folder = gdrive_inbox_pdf_folder_id()
    processed_folder = gdrive_processed_pdf_folder_id()
    if not inbox_folder or not processed_folder:
        return 0
    seen: set[str] = set()
    moved = 0
    for s in sources:
        if s.type != "pdf":
            continue
        fid = s.extra.get("drive_file_id")
        if not fid or fid in seen:
            continue
        seen.add(fid)
        if drive.move_to_processed(fid, inbox_folder, processed_folder):
            moved += 1
    return moved
