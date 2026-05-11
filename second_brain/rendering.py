"""Daily note rendering — frontmatter merge + markdown body + append."""

from __future__ import annotations

import logging
import re
from datetime import date
from pathlib import Path

import frontmatter

from second_brain.config import vault_path
from second_brain.models import Source

logger = logging.getLogger(__name__)

SOURCE_EMOJI = {"article": "📄", "youtube": "🎬", "place": "📍", "pdf": "📰", "feed": "📨"}


def kebab(tag: str) -> str:
    t = tag.strip().lstrip("#").lower()
    return re.sub(r"[^a-z0-9]+", "-", t).strip("-")


def _strip_md_ext(path: str) -> str:
    return path[:-3] if path.endswith(".md") else path


def _blockquote(text: str) -> str:
    """Render multi-paragraph text as a contiguous Markdown blockquote."""
    lines: list[str] = []
    for paragraph in text.split("\n\n"):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        if lines:
            lines.append(">")
        for ln in paragraph.splitlines():
            lines.append(f"> {ln}".rstrip())
    return "\n".join(lines)


def _render_section(source: Source) -> str:
    emoji = SOURCE_EMOJI.get(source.type, "•")
    parts = [f"### {emoji} {source.title}", _blockquote(source.recap)]
    if source.tags:
        parts.append("**Tag**: " + " ".join(f"#{t}" for t in source.tags))
    if source.correlations:
        wikilinks = " ".join(f"[[{_strip_md_ext(c)}]]" for c in source.correlations)
        parts.append(f"**Connesso a**: {wikilinks}")
    if source.type == "place" and source.url:
        parts.append(f"[Apri in Maps]({source.url})")
    if source.type == "pdf" and source.url:
        drive_name = source.extra.get("drive_name") or "PDF"
        parts.append(f"[Apri {drive_name} su Drive]({source.url})")
    if source.type == "feed" and source.url:
        feed_name = source.extra.get("feed_name") or "feed"
        parts.append(f"_Da {feed_name}_ — [Leggi originale]({source.url})")
    return "\n".join(parts)


def _build_frontmatter(date: str, sources: list[Source]) -> dict:
    seen_tags: set[str] = set()
    all_tags: list[str] = []
    for s in sources:
        for t in s.tags:
            if t not in seen_tags:
                seen_tags.add(t)
                all_tags.append(t)

    seen_corr: set[str] = set()
    correlations: list[str] = []
    for s in sources:
        for c in s.correlations:
            link = f"[[{_strip_md_ext(c)}]]"
            if link not in seen_corr:
                seen_corr.add(link)
                correlations.append(link)

    return {
        "date": date,
        "tags": all_tags,
        "sources": [{"type": s.type, "title": s.title, "url": s.url} for s in sources],
        "correlations": correlations,
    }


def _connections_paragraph(sources: list[Source]) -> str:
    if len(sources) < 2:
        return ""
    common: set[str] = set(sources[0].tags)
    for s in sources[1:]:
        common &= set(s.tags)
    if not common:
        return ""
    tags_str = ", ".join(f"#{t}" for t in sorted(common))
    return f"I contenuti di oggi condividono i temi: {tags_str}."


def _stats_footer(sources: list[Source]) -> str:
    """Compact footer line: counts that help spot anomalies at a glance."""
    n = len(sources)
    by_type: dict[str, int] = {}
    for s in sources:
        by_type[s.type] = by_type.get(s.type, 0) + 1
    types_str = ", ".join(f"{k}: {v}" for k, v in sorted(by_type.items()))
    unique_tags = {t for s in sources for t in s.tags}
    unique_corr = {c for s in sources for c in s.correlations}
    failed = sum(1 for s in sources if s.status != "ok")
    parts = [
        f"**Fonti**: {n} ({types_str})",
        f"**Tag unici**: {len(unique_tags)}",
        f"**Correlations**: {len(unique_corr)}",
    ]
    if failed:
        parts.append(f"⚠️ **Failed**: {failed}")
    return " · ".join(parts)


def render_daily(date: str, sources: list[Source]) -> str:
    """Return the full Daily/{date}.md content as a string."""
    meta = _build_frontmatter(date, sources)
    body_lines = [f"## Recap del {date}", ""]
    for s in sources:
        body_lines.append(_render_section(s))
        body_lines.append("")
    conn = _connections_paragraph(sources)
    if conn:
        body_lines.append("## 🔗 Connessioni tra i contenuti")
        body_lines.append(conn)
        body_lines.append("")
    if sources:
        body_lines.append(_stats_footer(sources))
        body_lines.append("")
    post = frontmatter.Post(content="\n".join(body_lines), **meta)
    return frontmatter.dumps(post) + "\n"


# ---------- per-item classified notes ----------

_SAFE_FILENAME_RE = re.compile(r"[^\w\s\-]", flags=re.UNICODE)
_DASHES_RE = re.compile(r"[\s_]+")


def safe_filename(title: str, max_len: int = 100) -> str:
    """Turn an arbitrary title into a filesystem-safe stem.

    Keeps unicode letters and digits, collapses whitespace/underscores
    to single dashes, strips leading/trailing dashes, and caps the
    length. ``"untitled"`` fallback so we never return an empty string.
    """
    s = _SAFE_FILENAME_RE.sub("", title)
    s = _DASHES_RE.sub("-", s).strip("-")
    s = s[:max_len].strip("-")
    return s or "untitled"


def _safe_category(category: str) -> str:
    """Sanitize an LLM-proposed category for use as a folder name."""
    c = re.sub(r"[\\/:*?\"<>|\r\n\t]", " ", category).strip()
    c = re.sub(r"\s+", " ", c)
    return c[:80] or "Uncategorized"


def render_classified_note(source: Source, today_iso: str) -> str:
    """Build the final Markdown for a classified article.

    Frontmatter carries every field useful for Obsidian indexing
    (title, source, date, category, tags, correlations). The body
    starts with a generated summary section and ends with the original
    content separated by a horizontal rule so the reader sees the
    LLM-curated context first, then the full source.
    """
    title = source.title or "Untitled"
    fm: dict = {
        "title": title,
        "source": source.url,
        "date_processed": today_iso,
        "category": _safe_category(source.category),
        "tags": list(source.tags),
    }
    if source.correlations:
        fm["correlations"] = [f"[[{_strip_md_ext(c)}]]" for c in source.correlations]

    body_lines = [f"## 📝 Summary _(generated {today_iso})_", _blockquote(source.recap), ""]
    if source.tags:
        body_lines.append("**Tag**: " + " ".join(f"#{t}" for t in source.tags))
    if source.correlations:
        wikilinks = " ".join(f"[[{_strip_md_ext(c)}]]" for c in source.correlations)
        body_lines.append(f"**Connesso a**: {wikilinks}")
    body_lines.append("")
    body_lines.append("---")
    body_lines.append("")
    body_lines.append(source.content.strip())
    body_lines.append("")

    post = frontmatter.Post(content="\n".join(body_lines), **fm)
    return frontmatter.dumps(post) + "\n"


def _unique_target(target: Path) -> Path:
    if not target.exists():
        return target
    stem, suffix = target.stem, target.suffix
    i = 1
    while True:
        cand = target.with_name(f"{stem}_{i}{suffix}")
        if not cand.exists():
            return cand
        i += 1


def write_classified_note(source: Source) -> Path:
    """Write the rendered note under ``Notes/<Category>/<safe-title>.md``.

    Returns the resulting path. Creates the category folder if needed,
    appends ``_1``/``_2`` on filename collision. Caller is responsible
    for deleting the original Inbox file (we don't touch it here so a
    write failure can't strand input data).
    """
    today_iso = date.today().isoformat()
    rendered = render_classified_note(source, today_iso)
    category = _safe_category(source.category)
    out_dir = vault_path() / "Notes" / category
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = safe_filename(source.title)
    target = _unique_target(out_dir / f"{stem}.md")
    target.write_text(rendered, encoding="utf-8")
    return target


def write_daily(date: str, rendered: str) -> Path:
    """Write or append to ``Daily/{date}.md``.

    If the file exists, append the body (without re-emitting frontmatter) so
    the existing metadata block is preserved.
    """
    daily_dir = vault_path() / "Daily"
    daily_dir.mkdir(parents=True, exist_ok=True)
    out_path = daily_dir / f"{date}.md"
    if out_path.exists():
        body = frontmatter.loads(rendered).content
        existing = out_path.read_text(encoding="utf-8")
        out_path.write_text(existing.rstrip() + "\n\n" + body + "\n", encoding="utf-8")
    else:
        out_path.write_text(rendered, encoding="utf-8")
    return out_path
