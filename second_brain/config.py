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


def vault_path() -> Path:
    return Path(os.environ.get("VAULT_PATH", "./vault")).expanduser().resolve()


def chroma_path() -> Path:
    raw = os.environ.get("CHROMA_PATH", str(vault_path() / ".chroma"))
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
