# Configuration reference

All settings are read inside `consuelo/config.py`. No other module touches `os.environ` directly — every consumer calls `config.something()`.

## Loading

`config.py` reads from process environment. A `.env` file at the project root is loaded automatically by `python-dotenv` (via the `cli.py` import). For container/cron deployments you can skip the `.env` and inject env vars directly.

## Core

| Variable | Default | Purpose |
|---|---|---|
| `VAULT_PATH` | `./vault` | Root of the Obsidian vault. All other vault-relative paths derive from this. |
| `LLM_MODE` | `local` | `local` (Ollama) or `cloud` (OpenAI). One-off override available via `--mode` on `consuelo run`. |
| `LOG_LEVEL` | `INFO` | `DEBUG` for verbose tracing, `WARNING` for quiet cron runs. |
| `OUTPUT_LANGUAGE` | _(auto)_ | When set, forces summary/tags language. Without it, language follows the source. |

## Ollama (local mode)

| Variable | Default | Notes |
|---|---|---|
| `OLLAMA_MODEL` | `qwen2.5:14b` | Chat model. `qwen2.5:7b` is faster but less reliable for JSON-mode output. |
| `OLLAMA_EMBED_MODEL` | `nomic-embed-text-8k` | Built from the 8k Modelfile under `scripts/`. Falls back to `nomic-embed-text` (2 k ctx) if absent. |
| `OLLAMA_CHAT_NUM_CTX` | `8192` | Chat context window. The default Ollama value (`2048`) truncates long articles. |
| `OLLAMA_CHAT_NUM_PREDICT` | `2048` | Max output tokens. |
| `OLLAMA_EMBED_NUM_CTX` | `8192` | Embed context. Must match the loaded model's context. |

Build the 8 k embed model once:

```bash
ollama create nomic-embed-text-8k -f scripts/nomic-8k.Modelfile
```

## OpenAI (cloud mode)

| Variable | Default | Notes |
|---|---|---|
| `OPENAI_API_KEY` | — | Required when `LLM_MODE=cloud`. |
| `OPENAI_MODEL` | `gpt-4o-mini` | Chat model. The classify prompt is short enough that mini is sufficient. |
| `OPENAI_EMBED_MODEL` | `text-embedding-3-small` | 1536-dim embeddings, cheap and fast. |

## Vector store + embeddings

| Variable | Default | Notes |
|---|---|---|
| `CHROMA_PATH` | `$VAULT_PATH/.chroma` | Where the persistent Chroma collection lives. **Keep this off iCloud / Dropbox** — SQLite + cloud sync = corruption risk. |
| `EMBED_CHAR_LIMIT` | `20000` | Inputs longer than this are truncated before embedding (auto-shrink protects against context overflow). Drop to `5000` if you're on a 2 k-context embed model. |
| `PINECONE_API_KEY` | — | Legacy / future. The Pinecone backend is not the default. |
| `PINECONE_INDEX` | `consuelo` | Legacy. |

## State + cache

By default, state and cache live inside the vault (`vault/.state/`, `vault/.cache/`). For multi-machine setups it's often cleaner to keep them outside the synced vault, since they're machine-local and not interesting to share.

| Variable | Default | Notes |
|---|---|---|
| `STATE_PATH` | `$VAULT_PATH/.state` | Per-source idempotency state files. |
| `CACHE_PATH` | `$VAULT_PATH/.cache` | Embedding cache directory (SQLite file lives inside). |

A common pattern: keep state + cache + Chroma all under `~/.local/share/consuelo/` so iCloud only sees plain markdown.

## Concurrency

| Variable | Default | Notes |
|---|---|---|
| `ASYNC_CONCURRENCY` | `8` cloud, `1` local | Semaphore cap for embed + classify. Local Ollama is single-threaded against the loaded model, so >1 just adds overhead. |

## RSS feeds

| Variable | Default | Notes |
|---|---|---|
| `FEEDS_CONFIG_PATH` | `$VAULT_PATH/.config/feeds.json` | JSON array of `{name, url}`. See [`sources.md`](sources.md#rss--atom-feeds). |
| `FEED_MAX_ENTRIES_PER_FEED` | `3` | Per-feed top-N most-recent cap. `0`/negative disables. |
| `FEED_DAYS_BACK` | `0` | `0` = today only; `N>0` = N-day backfill window; `-1` = no date filter. Entries without a parseable publication date are always kept. |

## Google Drive (PDFs)

| Variable | Default | Notes |
|---|---|---|
| `GDRIVE_CREDENTIALS_JSON` | — | Path to the service account JSON key. |
| `GDRIVE_INBOX_PDF_FOLDER_ID` | — | Drive folder ID containing PDFs to process. Must be shared as Editor with the SA email. |
| `GDRIVE_PROCESSED_PDF_FOLDER_ID` | — | Destination folder for processed PDFs. Same sharing requirements. |

Drive ingestion is optional — leave the variables empty to skip the PDF source type.

## Google Maps (places)

| Variable | Default | Notes |
|---|---|---|
| `GOOGLE_MAPS_API_KEY` | — | Optional. When set, the place extractor enriches `Source.content` with recent reviews from the Places API. |

## Sample `.env`

A realistic configuration for local-first usage:

```ini
# --- Vault ---
VAULT_PATH=/Users/me/Library/Mobile Documents/iCloud~md~obsidian/Documents/Personal

# --- LLM mode ---
LLM_MODE=local

# --- Local (Ollama) ---
OLLAMA_MODEL=qwen2.5:7b
OLLAMA_CHAT_NUM_CTX=8192
OLLAMA_CHAT_NUM_PREDICT=2048
OLLAMA_EMBED_MODEL=nomic-embed-text-8k
OLLAMA_EMBED_NUM_CTX=8192

# --- Cloud (OpenAI) — leave blank if not using ---
OPENAI_API_KEY=
OPENAI_MODEL=gpt-4o-mini
OPENAI_EMBED_MODEL=text-embedding-3-small

# --- Vector store + cache (kept off iCloud) ---
CHROMA_PATH=/Users/me/.local/share/consuelo/chroma
STATE_PATH=/Users/me/.local/share/consuelo/state
CACHE_PATH=/Users/me/.local/share/consuelo/cache

# --- RSS feeds ---
FEEDS_CONFIG_PATH=/Users/me/.../Personal/Inbox/feeds/feeds.json
FEED_MAX_ENTRIES_PER_FEED=3
FEED_DAYS_BACK=0

# --- Drive PDFs (optional) ---
GDRIVE_CREDENTIALS_JSON=credentials/gdrive.json
GDRIVE_INBOX_PDF_FOLDER_ID=1AbC...
GDRIVE_PROCESSED_PDF_FOLDER_ID=1XyZ...

# --- Embedding ---
EMBED_CHAR_LIMIT=20000

# --- Output ---
OUTPUT_LANGUAGE=italian
LOG_LEVEL=INFO
```
