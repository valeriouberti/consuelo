"""Domain models shared across modules."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Optional

SourceType = Literal["article", "youtube", "place"]
StateSource = Literal["articles", "youtube", "places"]


@dataclass
class Source:
    type: SourceType
    title: str
    url: str
    content: str
    state_id: str
    state_source: StateSource
    source_path: Optional[Path] = None  # original inbox file, used for archiving
    embedding: Optional[list[float]] = None
    recap: str = ""
    tags: list[str] = field(default_factory=list)
    correlations: list[str] = field(default_factory=list)
    extra: dict = field(default_factory=dict)
