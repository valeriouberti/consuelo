"""Shared fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def vault(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolated vault rooted at ``tmp_path``."""
    monkeypatch.setenv("VAULT_PATH", str(tmp_path))
    (tmp_path / "Inbox" / "articles").mkdir(parents=True)
    (tmp_path / "Inbox" / "youtube").mkdir(parents=True)
    (tmp_path / "Inbox" / "places").mkdir(parents=True)
    (tmp_path / "Daily").mkdir()
    (tmp_path / "Notes").mkdir()
    return tmp_path
