"""LLM client dispatcher (Ollama / OpenAI) plus prompt loading."""

from __future__ import annotations

import asyncio
import json
import logging
from functools import lru_cache
from pathlib import Path

from consuelo.config import (
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


# ---------- cost tracking (cloud only) ----------

# Per-1M-token USD pricing for the cloud models we support. Updated when
# OpenAI changes their tariff — keep in sync with
# https://openai.com/api/pricing/. Source-of-truth for the cost summary
# printed at the end of each run; off by an order of magnitude only
# matters until you notice and update the dict.
_PRICING_PER_1M_USD: dict[str, dict[str, float]] = {
    "gpt-4o-mini": {"input": 0.150, "output": 0.600},
    "gpt-4o": {"input": 2.500, "output": 10.000},
    "text-embedding-3-small": {"input": 0.020, "output": 0.0},
    "text-embedding-3-large": {"input": 0.130, "output": 0.0},
}

_usage: dict[str, int] = {"prompt": 0, "completion": 0, "embed": 0, "cache_hits": 0}


def reset_usage() -> None:
    """Zero the per-run token counters. Call at the start of a run."""
    _usage["prompt"] = 0
    _usage["completion"] = 0
    _usage["embed"] = 0
    _usage["cache_hits"] = 0


def _record_chat_usage(response) -> None:
    usage = getattr(response, "usage", None)
    if usage is None:
        return
    _usage["prompt"] += getattr(usage, "prompt_tokens", 0) or 0
    _usage["completion"] += getattr(usage, "completion_tokens", 0) or 0


def _record_embed_usage(response) -> None:
    usage = getattr(response, "usage", None)
    if usage is None:
        return
    _usage["embed"] += getattr(usage, "total_tokens", 0) or 0


def usage_summary() -> dict:
    """Snapshot of token counters + estimated USD cost for the current run."""
    chat_model = openai_model()
    embed_model = openai_embed_model()
    chat_price = _PRICING_PER_1M_USD.get(chat_model, {"input": 0.0, "output": 0.0})
    embed_price = _PRICING_PER_1M_USD.get(embed_model, {"input": 0.0, "output": 0.0})
    cost = (
        _usage["prompt"] / 1_000_000 * chat_price["input"]
        + _usage["completion"] / 1_000_000 * chat_price["output"]
        + _usage["embed"] / 1_000_000 * embed_price["input"]
    )
    return {
        "prompt_tokens": _usage["prompt"],
        "completion_tokens": _usage["completion"],
        "embed_tokens": _usage["embed"],
        "cache_hits": _usage["cache_hits"],
        "chat_model": chat_model,
        "embed_model": embed_model,
        "estimated_usd": round(cost, 4),
    }


def _cache_model_key() -> str:
    """Identifier for the active embedding model — namespace for cache keys."""
    return (
        f"cloud:{openai_embed_model()}"
        if llm_mode() == "cloud"
        else f"local:{ollama_embed_model()}"
    )


# ---------- retry ----------

RETRY_ATTEMPTS = 3
RETRY_BASE_DELAY_S = 1.0


def _is_transient(exc: BaseException) -> bool:
    """True if ``exc`` is worth retrying.

    Covers OpenAI rate limits and transient API errors plus generic
    network timeouts. Errors that won't fix themselves (auth, bad
    request, context overflow) are NOT transient — caller should fail
    fast or handle explicitly.
    """
    name = type(exc).__name__
    # OpenAI SDK exception class names (avoid importing openai at module load):
    if name in ("RateLimitError", "APIConnectionError", "APITimeoutError", "InternalServerError"):
        return True
    if name == "APIStatusError":
        status = getattr(exc, "status_code", None)
        return isinstance(status, int) and status >= 500
    # httpx / std network:
    if name in ("TimeoutException", "ConnectTimeout", "ReadTimeout", "ConnectError"):
        return True
    return False


async def _retry_async(coro_factory, *, attempts: int = RETRY_ATTEMPTS):
    """Run ``coro_factory()`` with exponential backoff on transient errors.

    ``coro_factory`` is a 0-arg callable returning a fresh coroutine — we
    can't reuse a coroutine across retries, it has to be recreated.
    """
    last_exc: BaseException | None = None
    for i in range(attempts):
        try:
            return await coro_factory()
        except BaseException as exc:  # noqa: BLE001 — we re-raise if non-transient
            if not _is_transient(exc) or i == attempts - 1:
                raise
            last_exc = exc
            delay = RETRY_BASE_DELAY_S * (2**i)
            logger.warning(
                "transient %s, retry %d/%d after %.1fs",
                type(exc).__name__,
                i + 1,
                attempts,
                delay,
            )
            await asyncio.sleep(delay)
    assert last_exc is not None
    raise last_exc


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
    _record_embed_usage(response)
    return list(response.data[0].embedding)


def embed_text(text: str) -> list[float]:
    from consuelo import embedding_cache  # local import: avoid cycles

    model_key = _cache_model_key()
    cached = embedding_cache.get(text, model_key)
    if cached is not None:
        _usage["cache_hits"] += 1
        return cached
    vector = _embed_cloud(text) if llm_mode() == "cloud" else _embed_local(text)
    embedding_cache.put(text, model_key, vector)
    return vector


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

    async def call():
        return await client.embeddings.create(model=openai_embed_model(), input=text)

    response = await _retry_async(call)
    _record_embed_usage(response)
    return list(response.data[0].embedding)


async def embed_text_async(text: str) -> list[float]:
    from consuelo import embedding_cache  # local import: avoid cycles

    model_key = _cache_model_key()
    cached = await asyncio.to_thread(embedding_cache.get, text, model_key)
    if cached is not None:
        _usage["cache_hits"] += 1
        return cached
    vector = await (_embed_cloud_async(text) if llm_mode() == "cloud" else _embed_local_async(text))
    await asyncio.to_thread(embedding_cache.put, text, model_key, vector)
    return vector


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
    _record_chat_usage(response)
    return response.choices[0].message.content or ""


def call_llm(system_prompt: str, user_msg: str) -> str:
    if llm_mode() == "cloud":
        return _chat_cloud(system_prompt, user_msg)
    return _chat_local(system_prompt, user_msg)


def _chat_local_text(system_prompt: str, user_msg: str) -> str:
    """Like ``_chat_local`` but without JSON format constraint — for prose output."""
    import ollama  # type: ignore

    response = ollama.chat(
        model=ollama_model(),
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_msg},
        ],
        options={
            "num_ctx": ollama_chat_num_ctx(),
            "num_predict": ollama_chat_num_predict(),
        },
    )
    return response["message"]["content"]


def _chat_cloud_text(system_prompt: str, user_msg: str) -> str:
    """Like ``_chat_cloud`` but without ``response_format=json_object``."""
    from openai import OpenAI  # type: ignore

    client = OpenAI(api_key=openai_api_key())
    response = client.chat.completions.create(
        model=openai_model(),
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_msg},
        ],
    )
    _record_chat_usage(response)
    return response.choices[0].message.content or ""


def call_llm_text(system_prompt: str, user_msg: str) -> str:
    """Plain-text variant of ``call_llm`` — use for prose answers (RAG, etc).

    The standard ``call_llm`` forces ``response_format=json_object`` because
    every recap prompt expects strict JSON. RAG-style answers want
    Markdown prose, so this variant drops the JSON constraint.
    """
    if llm_mode() == "cloud":
        return _chat_cloud_text(system_prompt, user_msg)
    return _chat_local_text(system_prompt, user_msg)


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

    async def call():
        return await client.chat.completions.create(
            model=openai_model(),
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_msg},
            ],
            response_format={"type": "json_object"},
        )

    response = await _retry_async(call)
    _record_chat_usage(response)
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
