"""Extractors that turn inbox files into Source objects."""

from __future__ import annotations

import asyncio
import json
import logging
import statistics
import tempfile
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import frontmatter

from second_brain import drive, state
from second_brain.config import (
    async_concurrency,
    feed_days_back,
    feed_max_entries_per_feed,
    feeds_config_path,
    gdrive_inbox_pdf_folder_id,
    gdrive_processed_pdf_folder_id,
    google_maps_api_key,
)
from second_brain.llm import _retry_async
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


def _html_to_markdown(html: str) -> str:
    """Convert ``html`` (already content-extracted by readability) to Markdown.

    ``markdownify`` preserves headings, lists, links, and emphasis — much
    more useful in Obsidian than ``text_content()`` plain text. Falls
    back to ``lxml.text_content`` if markdownify isn't installed.
    """
    try:
        from markdownify import markdownify  # type: ignore

        return markdownify(html, heading_style="ATX", strip=["script", "style"]).strip()
    except ImportError:
        try:
            from lxml import html as lxml_html  # type: ignore

            return lxml_html.fromstring(html).text_content().strip()
        except Exception:
            return html


def _extract_article_html(path: Path) -> Source | None:
    try:
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
        summary_html = doc.summary()
        text = _html_to_markdown(summary_html)
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

PDF_HEADING_SIZE_RATIO = 1.30  # word > median * ratio counts as heading
PDF_MIN_ARTICLE_CHARS = 200  # discard tiny fragments (captions, ads, ToCs)
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
                page_words = page.extract_words(extra_attrs=["size"], use_text_flow=True)
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


# ---------- RSS / Atom feeds ----------

FEED_MIN_BODY_CHARS = 400  # below → assume headlines-only, fetch link
FEED_FETCH_TIMEOUT_S = 15
FEED_USER_AGENT = "second-brain/0.1 (+https://github.com/) feedparser"


def _load_feeds_config() -> list[dict]:
    """Return ``[{name, url}, ...]`` from the JSON config file."""
    path = feeds_config_path()
    if not path.exists():
        logger.debug("feeds config not found at %s — skipping feed source", path)
        return []
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        logger.warning("feeds config %s unreadable: %s", path, exc)
        return []
    if not raw:
        logger.debug("feeds config %s is empty — skipping feed source", path)
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning("feeds config %s is malformed JSON: %s", path, exc)
        return []
    if not isinstance(data, list):
        logger.error("feeds config %s must be a JSON array", path)
        return []
    valid = [
        d
        for d in data
        if isinstance(d, dict) and isinstance(d.get("url"), str) and d["url"].strip()
    ]
    if len(valid) != len(data):
        logger.warning("feeds config: %d invalid entries skipped", len(data) - len(valid))
    return valid


def _strip_html(html: str) -> str:
    """Best-effort HTML → plain text for feed bodies.

    Tries readability first (great for full-article HTML); falls back to
    lxml ``text_content()`` for short summaries where readability tends
    to return nothing.
    """
    try:
        from lxml import html as lxml_html  # type: ignore
        from readability import Document  # type: ignore
    except ImportError:
        return html
    try:
        doc = Document(html)
        text = lxml_html.fromstring(doc.summary()).text_content().strip()
        if text:
            return text
    except Exception:
        pass
    try:
        from lxml import html as lxml_html2  # type: ignore

        return lxml_html2.fromstring(html).text_content().strip()
    except Exception:
        return html


async def _fetch_url_body_async(client, url: str, sem: asyncio.Semaphore) -> str:
    """Async-fetch ``url`` and return readability-stripped plain text.

    ``client`` is an ``httpx.AsyncClient`` shared across all fetches in
    the same gather call (connection pooling). ``sem`` bounds in-flight
    requests so we don't hammer origins or exhaust local sockets.
    Transient errors (timeouts, 5xx) are retried with exponential
    backoff; 4xx and other terminal failures are logged and skipped.
    """
    async with sem:
        try:

            async def call():
                resp = await client.get(url, follow_redirects=True)
                resp.raise_for_status()
                return resp

            resp = await _retry_async(call)
        except Exception as exc:
            logger.warning("feed entry fetch failed for %s: %s", url, exc)
            return ""
        text = resp.text
    return await asyncio.to_thread(_strip_html, text)


def _entry_inline_body(entry: dict) -> str:
    """Return the best inline body for a feedparser entry, or ``""``.

    Atom ``content`` wins over RSS ``summary`` when both are present and
    populated, since ``content`` is the standard place for full text.
    """
    content_list = entry.get("content") or []
    for c in content_list:
        value = (c.get("value") or "").strip()
        if value:
            return _strip_html(value)
    summary = (entry.get("summary") or "").strip()
    if summary:
        return _strip_html(summary)
    return ""


def _entry_state_id(entry: dict) -> str:
    """Stable, immutable ID for a feed entry."""
    return (
        entry.get("id") or entry.get("guid") or entry.get("link") or entry.get("title", "")
    ).strip()


def _entry_date(entry: dict) -> date | None:
    """Best-effort publication date from a feedparser entry.

    feedparser exposes parsed timestamps as ``time.struct_time`` under
    ``published_parsed`` (RSS) or ``updated_parsed`` (Atom). Either is
    acceptable as the entry's "when". Returns None when neither is
    present or parseable.
    """
    for key in ("published_parsed", "updated_parsed"):
        tp = entry.get(key)
        if not tp:
            continue
        try:
            return date(tp.tm_year, tp.tm_mon, tp.tm_mday)
        except (AttributeError, ValueError):
            continue
    return None


def _entry_in_window(entry: dict, target: date | None, days_back: int) -> bool:
    """True if the entry's pub date is within the allowed window.

    - ``target=None`` or ``days_back<0``: filter disabled, keep everything.
    - Otherwise: keep iff ``target - days_back <= entry_date <= target``.
    - Entries with no parseable date are always kept (don't drop content
      because of upstream metadata gaps).
    """
    if target is None or days_back < 0:
        return True
    d = _entry_date(entry)
    if d is None:
        return True
    earliest = target - timedelta(days=days_back)
    return earliest <= d <= target


def _parse_feed(url: str) -> tuple[list[dict], object]:
    """Sync wrapper around feedparser.parse — offloaded via ``to_thread``."""
    import feedparser  # type: ignore

    parsed = feedparser.parse(url, agent=FEED_USER_AGENT)
    return list(parsed.entries), parsed.bozo_exception if parsed.bozo else None


async def gather_feed_sources(target_date: date | None = None) -> list[Source]:
    """Poll every configured feed concurrently, return one Source per unseen entry.

    Freshness strategy (two complementary knobs):

    - **Per-feed recency cap** (``feed_max_entries_per_feed``, default 3):
      after parsing, each feed is sorted by publication date descending and
      only the top N entries are kept. State dedup still applies, so a feed
      that posts daily will normally yield 1 new Source/run; after a
      weekend, up to N entries can backfill. Set 0/negative to disable.
    - **Optional date window** (``feed_days_back``, default disabled): when
      enabled, also requires the entry's date to fall in
      ``[target_date - days_back, target_date]``. Used for explicit
      backfill runs against ``--date``.

    Parallelism:

    - Feed parsing (``feedparser.parse``) runs in a thread pool — sync
      library, but each call is independent so they overlap freely.
    - Per-entry URL fetches (used when the feed only has headlines) use
      a shared ``httpx.AsyncClient`` with a ``Semaphore`` cap from
      ``async_concurrency()``.

    Body resolution: inline ``content``/``summary`` if substantive, else
    fetch ``entry.link`` and strip via readability — required for
    headline-only feeds like TLDR.
    """
    feeds = _load_feeds_config()
    if not feeds:
        return []
    try:
        import feedparser  # type: ignore  # noqa: F401  — fail fast if missing
    except ImportError:
        logger.error("feedparser not installed — cannot gather feeds")
        return []
    try:
        import httpx  # type: ignore
    except ImportError:
        logger.error("httpx not installed — cannot fetch feed entry URLs")
        return []

    parse_tasks = [asyncio.to_thread(_parse_feed, fc["url"]) for fc in feeds]
    parsed_results = await asyncio.gather(*parse_tasks, return_exceptions=True)

    max_per_feed = feed_max_entries_per_feed()
    all_entries: list[tuple[dict, str]] = []
    for fc, result in zip(feeds, parsed_results, strict=True):
        url = fc["url"]
        name = fc.get("name") or url
        if isinstance(result, BaseException):
            logger.warning("feedparser failed for %s: %s", url, result)
            continue
        entries, bozo = result
        if bozo and not entries:
            logger.warning("feed %s returned no entries (bozo=%s)", url, bozo)
            continue
        # Sort by date desc so the top-N cap keeps the freshest. Entries
        # without a parsable date sink to the bottom but stay eligible
        # if room remains under the cap.
        sorted_entries = sorted(
            entries,
            key=lambda e: _entry_date(e) or date.min,
            reverse=True,
        )
        if max_per_feed > 0:
            sorted_entries = sorted_entries[:max_per_feed]
        for entry in sorted_entries:
            all_entries.append((entry, name))
    if not all_entries:
        return []

    days_back = feed_days_back()
    if target_date is not None and days_back >= 0:
        before_date_filter = len(all_entries)
        all_entries = [
            (e, n) for e, n in all_entries if _entry_in_window(e, target_date, days_back)
        ]
        logger.info(
            "feeds: date filter kept %d/%d entries (target=%s, days_back=%d)",
            len(all_entries),
            before_date_filter,
            target_date.isoformat(),
            days_back,
        )
        if not all_entries:
            return []

    all_ids = [_entry_state_id(e) for e, _ in all_entries]
    unseen = set(state.filter_unseen("feeds", all_ids))
    logger.info("feeds: %d new entries across %d feed(s)", len(unseen), len(feeds))

    new_entries: list[tuple[dict, str, str, str, str]] = []  # (entry, feed_name, sid, title, link)
    for entry, feed_name in all_entries:
        sid = _entry_state_id(entry)
        if not sid or sid not in unseen:
            continue
        unseen.discard(sid)
        title = (entry.get("title") or "(untitled)").strip()
        link = (entry.get("link") or "").strip()
        new_entries.append((entry, feed_name, sid, title, link))

    if not new_entries:
        return []

    sem = asyncio.Semaphore(async_concurrency())
    timeout = httpx.Timeout(FEED_FETCH_TIMEOUT_S)
    headers = {"User-Agent": FEED_USER_AGENT}

    async with httpx.AsyncClient(timeout=timeout, headers=headers) as client:

        async def resolve_body(entry: dict, link: str) -> str:
            inline = await asyncio.to_thread(_entry_inline_body, entry)
            if len(inline) >= FEED_MIN_BODY_CHARS or not link:
                return inline
            fetched = await _fetch_url_body_async(client, link, sem)
            return fetched if len(fetched) > len(inline) else inline

        body_tasks = [resolve_body(entry, link) for entry, _, _, _, link in new_entries]
        bodies = await asyncio.gather(*body_tasks, return_exceptions=True)

    sources: list[Source] = []
    for (entry, feed_name, sid, title, link), body in zip(new_entries, bodies, strict=True):
        if isinstance(body, BaseException):
            logger.warning("body resolution failed for %r: %s", title, body)
            continue
        if not body:
            logger.warning("feed entry %r has no body, skipping", title)
            continue
        sources.append(
            Source(
                type="feed",
                title=title,
                url=link or sid,
                content=body,
                state_id=sid,
                state_source="feeds",
                source_path=None,
                extra={
                    "feed_name": feed_name,
                    "published": entry.get("published") or entry.get("updated") or "",
                },
            )
        )
    return sources
