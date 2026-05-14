"""Unit tests for consuelo.rendering."""

from __future__ import annotations

from pathlib import Path

import frontmatter

from consuelo.models import Source
from consuelo.rendering import kebab, render_daily, write_daily


def test_kebab_lowercases_and_dashes() -> None:
    assert kebab("Platform Engineering") == "platform-engineering"
    assert kebab("  #Già/tagged  ") == "gi-tagged"


def _src(**overrides) -> Source:
    base = dict(
        type="article",
        title="Foo",
        url="file:///foo.html",
        content="content",
        state_id="Inbox/articles/foo.html",
        state_source="articles",
        recap="Sintesi di prova.",
        tags=["python", "test"],
        correlations=["Notes/Foo.md"],
    )
    base.update(overrides)
    return Source(**base)  # type: ignore[arg-type]


def test_render_daily_emits_valid_frontmatter() -> None:
    rendered = render_daily("2026-05-10", [_src()])
    post = frontmatter.loads(rendered)
    assert post.metadata["date"] == "2026-05-10"
    assert "python" in post.metadata["tags"]
    assert post.metadata["correlations"] == ["[[Notes/Foo]]"]
    assert "### 📄 Foo" in post.content
    assert "[[Notes/Foo]]" in post.content


def test_render_daily_connections_paragraph_on_shared_tag() -> None:
    a = _src(title="A", tags=["python", "test"])
    b = _src(title="B", tags=["python", "infra"])
    rendered = render_daily("2026-05-10", [a, b])
    assert "🔗 Connessioni tra i contenuti" in rendered
    assert "#python" in rendered


def test_render_daily_no_connections_when_no_shared_tag() -> None:
    a = _src(title="A", tags=["python"])
    b = _src(title="B", tags=["rust"])
    rendered = render_daily("2026-05-10", [a, b])
    assert "Connessioni tra i contenuti" not in rendered


def test_write_daily_creates_then_appends(vault: Path) -> None:
    rendered_a = render_daily("2026-05-10", [_src(title="A")])
    rendered_b = render_daily("2026-05-10", [_src(title="B")])
    out_a = write_daily("2026-05-10", rendered_a)
    out_b = write_daily("2026-05-10", rendered_b)
    assert out_a == out_b
    text = out_a.read_text()
    assert "### 📄 A" in text
    assert "### 📄 B" in text
    # Frontmatter must appear exactly once.
    assert text.count("\n---\n") == 1 or text.startswith("---\n")
    assert text.count("---") == 2  # opening + closing only
