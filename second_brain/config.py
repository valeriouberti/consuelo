"""Centralized environment access. All `os.environ` reads live here."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path


def configure_logging() -> None:
    level = os.environ.get("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    # Silence noisy third-party INFO/DEBUG chatter that drowns our own logs:
    # - readability emits "ruthless removal did not work" on every fallback
    # - httpx / httpcore log each request line
    # - urllib3 connection pool debug
    for noisy in ("readability.readability", "httpx", "httpcore", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def vault_path() -> Path:
    return Path(os.environ.get("VAULT_PATH", "./vault")).expanduser().resolve()


def chroma_path() -> Path:
    raw = os.environ.get("CHROMA_PATH", str(vault_path() / ".chroma"))
    return Path(raw).expanduser().resolve()


def cache_path() -> Path:
    raw = os.environ.get("CACHE_PATH", str(vault_path() / ".cache"))
    return Path(raw).expanduser().resolve()


def state_path() -> Path:
    raw = os.environ.get("STATE_PATH", str(vault_path() / ".state"))
    return Path(raw).expanduser().resolve()


def llm_mode() -> str:
    return os.environ.get("LLM_MODE", "local").lower()


def ollama_model() -> str:
    return os.environ.get("OLLAMA_MODEL", "qwen2.5:14b")


def ollama_embed_model() -> str:
    return os.environ.get("OLLAMA_EMBED_MODEL", "nomic-embed-text")


def openai_model() -> str:
    return os.environ.get("OPENAI_MODEL", "gpt-4o-mini")


def openai_embed_model() -> str:
    return os.environ.get("OPENAI_EMBED_MODEL", "text-embedding-3-small")


def openai_api_key() -> str | None:
    return os.environ.get("OPENAI_API_KEY") or None


def google_maps_api_key() -> str | None:
    return os.environ.get("GOOGLE_MAPS_API_KEY") or None


def gdrive_credentials_json() -> str | None:
    """Path to a Google service account JSON key file.

    Used by ``second_brain.drive`` to authenticate against Drive v3.
    """
    return os.environ.get("GDRIVE_CREDENTIALS_JSON") or None


def gdrive_inbox_pdf_folder_id() -> str | None:
    """Drive folder ID where new PDFs are dropped by the user."""
    return os.environ.get("GDRIVE_INBOX_PDF_FOLDER_ID") or None


def gdrive_processed_pdf_folder_id() -> str | None:
    """Drive folder ID where processed PDFs are moved post-commit."""
    return os.environ.get("GDRIVE_PROCESSED_PDF_FOLDER_ID") or None


def async_concurrency() -> int:
    """Max concurrent async ops (LLM, embed, HTTP fetch).

    Default depends on ``LLM_MODE``:

    - ``cloud``: 8 — comfortably under OpenAI Tier 1 RPM, polite to
      HTTP origins like TLDR/Substack.
    - ``local``: 1 — Ollama serializes requests per model, so
      concurrency adds latency without throughput.

    Override with ``ASYNC_CONCURRENCY`` to tune (e.g. higher OpenAI tier).
    """
    raw = os.environ.get("ASYNC_CONCURRENCY")
    if raw:
        try:
            return max(1, int(raw))
        except ValueError:
            pass
    return 8 if llm_mode() == "cloud" else 1


def feed_max_entries_per_feed() -> int:
    """Cap on how many entries per feed are considered (most-recent-first).

    Default ``3`` — enough to catch up after weekends/holidays for a
    typical weekday newsletter, but small enough to avoid replaying old
    content. State dedup still filters anything already processed, so
    this is just an upper bound on "what's reasonably recent".

    Set to ``0`` or a negative number to disable the cap (consider every
    entry the feed exposes — useful for full backfill).
    """
    try:
        return int(os.environ.get("FEED_MAX_ENTRIES_PER_FEED", "3"))
    except ValueError:
        return 3


def feed_days_back() -> int:
    """Optional date-window filter, anchored on the run's target date.

    Default ``-1`` — disabled. The recency-based ``feed_max_entries_per_feed``
    handles freshness for the common case. Use this knob only when you
    explicitly want a date-anchored window (e.g. ``FEED_DAYS_BACK=7`` for
    a "everything from the last week" backfill against ``--date``).

    Entries without a parsable publication date are kept regardless.
    """
    try:
        return int(os.environ.get("FEED_DAYS_BACK", "-1"))
    except ValueError:
        return -1


def feeds_config_path() -> Path:
    """Path to the RSS/Atom feeds list (JSON array of ``{name, url}``).

    Default: ``$VAULT_PATH/.config/feeds.json``. Missing file is treated
    as "no feeds configured" — RSS gathering becomes a no-op.
    """
    raw = os.environ.get("FEEDS_CONFIG_PATH", str(vault_path() / ".config" / "feeds.json"))
    return Path(raw).expanduser().resolve()


def embed_char_limit() -> int:
    """Max chars sent to embedding model per call.

    Default ~20000 char ≈ ~6000 token, sicuro per ``nomic-embed-text-8k``
    (ctx 8192). Per il default ``nomic-embed-text`` (ctx 2048) abbassa a
    ``EMBED_CHAR_LIMIT=5000``. Cloud accetta molto di più.
    """
    try:
        return int(os.environ.get("EMBED_CHAR_LIMIT", "20000"))
    except ValueError:
        return 20000


def ollama_embed_num_ctx() -> int:
    """Context window passato a Ollama per embedding. Default 8192 (nomic
    nativo). Ignorato dai modelli cloud."""
    try:
        return int(os.environ.get("OLLAMA_EMBED_NUM_CTX", "8192"))
    except ValueError:
        return 8192


def ollama_chat_num_ctx() -> int:
    """Context window per chat completion. Default 8192 (llama3 nativo).
    Articoli lunghi richiedono ≥4096; llama3.1+ supporta fino a 128k."""
    try:
        return int(os.environ.get("OLLAMA_CHAT_NUM_CTX", "8192"))
    except ValueError:
        return 8192


def ollama_chat_num_predict() -> int:
    """Max token in output per chat completion. Default 2048 — sufficiente
    per recap multi-paragrafo + tag + correlations in JSON."""
    try:
        return int(os.environ.get("OLLAMA_CHAT_NUM_PREDICT", "2048"))
    except ValueError:
        return 2048


PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"
