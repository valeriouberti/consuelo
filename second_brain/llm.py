"""LLM client dispatcher (Ollama / OpenAI) plus prompt loading."""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path

from second_brain.config import (
    PROMPTS_DIR,
    embed_char_limit,
    llm_mode,
    ollama_chat_num_ctx,
    ollama_chat_num_predict,
    ollama_embed_model,
    ollama_embed_num_ctx,
    ollama_model,
    openai_api_key,
    openai_embed_model,
    openai_model,
)

logger = logging.getLogger(__name__)


# ---------- embeddings ----------


def _embed_local(text: str) -> list[float]:
    import ollama  # type: ignore

    # Use the new `embed` endpoint: legacy `embeddings` ignores options.num_ctx.
    result = ollama.embed(
        model=ollama_embed_model(),
        input=text,
        options={"num_ctx": ollama_embed_num_ctx()},
    )
    return list(result["embeddings"][0])


def _embed_cloud(text: str) -> list[float]:
    from openai import OpenAI  # type: ignore

    client = OpenAI(api_key=openai_api_key())
    response = client.embeddings.create(model=openai_embed_model(), input=text)
    return list(response.data[0].embedding)


def embed_text(text: str) -> list[float]:
    if llm_mode() == "cloud":
        return _embed_cloud(text)
    return _embed_local(text)


def _is_context_overflow(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return "context length" in msg or "context window" in msg or "maximum context" in msg


def embed_text_safe(text: str, max_chars: int | None = None) -> list[float]:
    """Embed con auto-shrink: tronca a ``max_chars`` (default da env) e, se
    il backend lamenta overflow di contesto, dimezza fino a successo o
    soglia minima di sicurezza."""
    limit = max_chars if max_chars is not None else embed_char_limit()
    payload = text[:limit]
    while True:
        try:
            return embed_text(payload)
        except Exception as exc:
            if not _is_context_overflow(exc) or len(payload) <= 500:
                raise
            new_len = len(payload) // 2
            logger.warning(
                "embed context overflow at %d chars — retrying at %d", len(payload), new_len
            )
            payload = payload[:new_len]


# ---------- async embeddings ----------


async def _embed_local_async(text: str) -> list[float]:
    import ollama  # type: ignore

    client = ollama.AsyncClient()
    result = await client.embed(
        model=ollama_embed_model(),
        input=text,
        options={"num_ctx": ollama_embed_num_ctx()},
    )
    return list(result["embeddings"][0])


async def _embed_cloud_async(text: str) -> list[float]:
    from openai import AsyncOpenAI  # type: ignore

    client = AsyncOpenAI(api_key=openai_api_key())
    response = await client.embeddings.create(model=openai_embed_model(), input=text)
    return list(response.data[0].embedding)


async def embed_text_async(text: str) -> list[float]:
    if llm_mode() == "cloud":
        return await _embed_cloud_async(text)
    return await _embed_local_async(text)


async def embed_text_safe_async(text: str, max_chars: int | None = None) -> list[float]:
    """Async twin of ``embed_text_safe`` with the same shrink-on-overflow logic."""
    limit = max_chars if max_chars is not None else embed_char_limit()
    payload = text[:limit]
    while True:
        try:
            return await embed_text_async(payload)
        except Exception as exc:
            if not _is_context_overflow(exc) or len(payload) <= 500:
                raise
            new_len = len(payload) // 2
            logger.warning(
                "embed context overflow at %d chars — retrying at %d", len(payload), new_len
            )
            payload = payload[:new_len]


# ---------- chat completion ----------


def _chat_local(system_prompt: str, user_msg: str) -> str:
    import ollama  # type: ignore

    response = ollama.chat(
        model=ollama_model(),
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_msg},
        ],
        format="json",
        options={
            "num_ctx": ollama_chat_num_ctx(),
            "num_predict": ollama_chat_num_predict(),
        },
    )
    return response["message"]["content"]


def _chat_cloud(system_prompt: str, user_msg: str) -> str:
    from openai import OpenAI  # type: ignore

    client = OpenAI(api_key=openai_api_key())
    response = client.chat.completions.create(
        model=openai_model(),
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_msg},
        ],
        response_format={"type": "json_object"},
    )
    return response.choices[0].message.content or ""


def call_llm(system_prompt: str, user_msg: str) -> str:
    if llm_mode() == "cloud":
        return _chat_cloud(system_prompt, user_msg)
    return _chat_local(system_prompt, user_msg)


# ---------- async chat completion ----------


async def _chat_local_async(system_prompt: str, user_msg: str) -> str:
    import ollama  # type: ignore

    client = ollama.AsyncClient()
    response = await client.chat(
        model=ollama_model(),
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_msg},
        ],
        format="json",
        options={
            "num_ctx": ollama_chat_num_ctx(),
            "num_predict": ollama_chat_num_predict(),
        },
    )
    return response["message"]["content"]


async def _chat_cloud_async(system_prompt: str, user_msg: str) -> str:
    from openai import AsyncOpenAI  # type: ignore

    client = AsyncOpenAI(api_key=openai_api_key())
    response = await client.chat.completions.create(
        model=openai_model(),
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_msg},
        ],
        response_format={"type": "json_object"},
    )
    return response.choices[0].message.content or ""


async def call_llm_async(system_prompt: str, user_msg: str) -> str:
    if llm_mode() == "cloud":
        return await _chat_cloud_async(system_prompt, user_msg)
    return await _chat_local_async(system_prompt, user_msg)


# ---------- prompts ----------


@lru_cache(maxsize=8)
def load_prompt(name: str) -> str:
    path = Path(PROMPTS_DIR) / name
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.error("cannot read prompt %s: %s", path, exc)
        return ""


def parse_llm_json(raw: str) -> dict:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        start, end = raw.find("{"), raw.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(raw[start : end + 1])
            except json.JSONDecodeError:
                pass
    logger.warning("LLM output not valid JSON, returning empty result")
    return {}
