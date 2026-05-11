"""SQLite-backed cache for text embeddings.

Embeddings are deterministic for a given (model, text) pair, so re-indexing
the same note hits the API every time unless we cache. This module wraps
the cache as a content-addressed key/value store keyed by
``sha256(text + ":" + model)``.

The cache lives in ``$VAULT_PATH/.cache/embeddings.db`` and is safe to
delete at any time — next embed call will repopulate. Not synchronized
with vault state on purpose: we want it disposable.

Numpy is *not* a dependency; vectors are stored as raw little-endian
``float32`` bytes via ``struct``, decoded back to ``list[float]`` on
lookup.
"""

from __future__ import annotations

import hashlib
import logging
import sqlite3
import struct
import threading
from pathlib import Path

from second_brain.config import vault_path

logger = logging.getLogger(__name__)

_DB_FILENAME = "embeddings.db"
_SCHEMA = """
CREATE TABLE IF NOT EXISTS embeddings (
    key        TEXT PRIMARY KEY,
    model      TEXT NOT NULL,
    dim        INTEGER NOT NULL,
    vector     BLOB NOT NULL,
    created_at REAL NOT NULL DEFAULT (unixepoch())
);
"""

_conn: sqlite3.Connection | None = None
_lock = threading.Lock()


def _db_path() -> Path:
    return vault_path() / ".cache" / _DB_FILENAME


def _connection() -> sqlite3.Connection:
    """Lazily open the SQLite connection.

    ``check_same_thread=False`` is safe here because every read/write is
    serialized through ``_lock`` — needed since the async pipeline runs
    embeds from worker threads via ``asyncio.to_thread``.
    """
    global _conn
    if _conn is not None:
        return _conn
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.execute(_SCHEMA)
    conn.commit()
    _conn = conn
    return conn


def _key(text: str, model: str) -> str:
    h = hashlib.sha256()
    h.update(model.encode("utf-8"))
    h.update(b"\x00")
    h.update(text.encode("utf-8"))
    return h.hexdigest()


def _encode(vec: list[float]) -> bytes:
    return struct.pack(f"<{len(vec)}f", *vec)


def _decode(blob: bytes, dim: int) -> list[float]:
    return list(struct.unpack(f"<{dim}f", blob))


def get(text: str, model: str) -> list[float] | None:
    """Return the cached embedding for ``(text, model)`` or None."""
    if not text:
        return None
    try:
        with _lock:
            row = (
                _connection()
                .execute(
                    "SELECT dim, vector FROM embeddings WHERE key = ?",
                    (_key(text, model),),
                )
                .fetchone()
            )
    except sqlite3.Error as exc:
        logger.warning("embedding cache read failed: %s", exc)
        return None
    if row is None:
        return None
    dim, blob = row
    try:
        return _decode(blob, dim)
    except struct.error as exc:
        logger.warning("embedding cache decode failed (corrupted row): %s", exc)
        return None


def put(text: str, model: str, vector: list[float]) -> None:
    """Insert or replace the cached embedding for ``(text, model)``."""
    if not text or not vector:
        return
    try:
        with _lock:
            _connection().execute(
                "INSERT OR REPLACE INTO embeddings (key, model, dim, vector) VALUES (?, ?, ?, ?)",
                (_key(text, model), model, len(vector), _encode(vector)),
            )
            _connection().commit()
    except sqlite3.Error as exc:
        logger.warning("embedding cache write failed: %s", exc)


def stats() -> dict:
    """Counts + size for the end-of-run summary."""
    try:
        with _lock:
            row = (
                _connection()
                .execute("SELECT COUNT(*), COALESCE(SUM(LENGTH(vector)), 0) FROM embeddings")
                .fetchone()
            )
    except sqlite3.Error:
        return {"rows": 0, "bytes": 0}
    rows, byts = row if row else (0, 0)
    return {"rows": int(rows), "bytes": int(byts)}
