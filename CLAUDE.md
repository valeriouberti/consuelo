# Second Brain — Project Context

## Progetto
Workflow Python che processa contenuti salvati nella vault Obsidian
(articoli, transcript YouTube, place Google Maps, PDF su Google Drive,
RSS feed). Per ogni item: LLM classifica → assegna categoria + tag +
summary, scrive nota arricchita in `Notes/<Categoria>/<slug>.md` con
correlations alle note esistenti via embeddings.

**Workflow corrente**: per-item classifier (articoli). Daily recap
aggregato disabilitato. YouTube/PDF/feed/place gathered ma skip nel
per-item flow (TODO: prossima iterazione).

## Stack
- **Python 3.12** (`pyproject.toml`, hatchling)
- **LLM locale**: Ollama — `qwen2.5:14b` + `nomic-embed-text-8k`
- **LLM cloud**: OpenAI `gpt-4o-mini` (sync Chat Completions, no Batch API)
- **Async pipeline**: `asyncio` + `httpx` + `AsyncOpenAI`/`ollama.AsyncClient`
- **Vector DB**: ChromaDB locale (Pinecone future, `vector.py` astratto)
- **Embedding cache**: SQLite (`vault/.cache/embeddings.db`, sha256 key)
- **Vault**: Obsidian `.md` + frontmatter YAML
- **Sources extra**:
  - Google Drive (PDF) via service account `google-api-python-client`
  - RSS/Atom `feedparser` (top-N per feed, body fetch su entries headline-only)
  - HTML→Markdown `markdownify` per articoli `.html`
- **Scheduler**: cron locale → GitHub Actions (futuro)
- **Tooling**: `ruff` (lint+format), `mypy`, `pytest`

## Struttura repo
```
second-brain/
├── second_brain/
│   ├── __init__.py
│   ├── __main__.py            # python -m second_brain
│   ├── cli.py                 # click: run + ask + index
│   ├── config.py              # tutti gli os.environ.get() vivono qui
│   ├── models.py              # Source dataclass + status + category
│   ├── archive.py             # existing_categories() + archive helpers
│   ├── state.py               # delta tracking + filter_unseen
│   ├── sources.py             # extractors + gather_pdf/feed_sources
│   ├── drive.py               # Drive v3 client (list/download/move)
│   ├── llm.py                 # sync+async embed/chat, retry, cost, cache
│   ├── embedding_cache.py     # SQLite content-addressed cache
│   ├── vector.py              # ChromaDB + upsert(kind="note"|"daily")
│   ├── rendering.py           # render_classified_note + safe_filename
│   └── pipeline.py            # async orchestrator + ask + index
├── prompts/
│   ├── classify.txt           # NEW: per-item classifier (JSON out)
│   ├── ask.txt                # NEW: RAG over vault (markdown out)
│   ├── recap.txt              # legacy Daily (non più chiamato)
│   ├── place.txt              # legacy place recap
│   └── tags.txt               # legacy
├── tests/                     # pytest unit (state, rendering)
├── feeds.example.json         # template RSS config
├── pyproject.toml
├── .env.example
├── .gitignore
├── README.md
└── CLAUDE.md
```

## Struttura vault Obsidian
```
vault/
├── Inbox/                          ← input transitorio
│   ├── articles/                   ← .html (readability+markdownify) / .md (Web Clipper)
│   ├── youtube/                    ← .txt o .md (TODO per-item flow)
│   └── places/                     ← .json (TODO per-item flow)
├── Notes/                          ← OUTPUT enriched + tue note personali
│   ├── <Categoria>/                ← NEW: classificazione auto LLM (Tech, Finance, ...)
│   │   └── <slug>.md
│   ├── <Note personali>/           ← le tue note, indicizzate Chroma
│   ├── articles/YYYY-MM-DD/        ← (legacy) archivio articoli — ESCLUSO indexing
│   ├── youtube/YYYY-MM-DD/         ← (legacy) ESCLUSO indexing
│   └── places/YYYY-MM-DD/          ← (legacy) ESCLUSO indexing
├── Daily/                          ← (legacy, non più scritto da `run`)
├── .config/
│   └── feeds.json                  ← lista RSS feed (opzionale)
├── .cache/
│   └── embeddings.db               ← SQLite cache embeddings
├── .chroma/                        ← Chroma persistent client
└── .state/                         ← delta tracking
    ├── processed_articles.json
    ├── processed_youtube.json
    ├── processed_places.json
    ├── processed_pdfs.json         ← Drive fileId
    ├── processed_feeds.json        ← entry guid/link
    └── last_index.txt
```

**Categoria libera ma con hint**: LLM riceve `existing_categories()` (lista
top-level folder sotto `Notes/` escluse archive subdirs), riusa nomi
esistenti preferenzialmente, propone nuovi solo se serve. Sanitization
folder name in `rendering._safe_category` (max 80 char, no slash/special).

**Archiviazione**: per articoli per-item, file Inbox viene **eliminato**
(`unlink`) dopo write+state. No più archive cronologico per articoli.
Legacy `archive_sources`/`archive_previous_daily` non più chiamati.

## Comandi principali
```bash
pip install -e ".[dev]"                # setup
second-brain run                        # per-item: articles → Notes/<Categoria>/
second-brain run --dry-run              # render su stdout, no scrittura
second-brain run --mode cloud           # override OPENAI
second-brain ask "query"                # RAG su Notes/ + Daily/
second-brain ask "query" -k 15          # top-K vault entries
second-brain index                      # indicizza Notes/ + Daily/ in Chroma
second-brain index --incremental        # solo mtime > last_index
second-brain index --no-daily           # skip Daily/, solo Notes/
pytest                                  # test
ruff check . && ruff format .           # lint + format
```

## Variabili d'ambiente
```
# --- Core ---
VAULT_PATH=./vault
LLM_MODE=local|cloud                    # default: local
LOG_LEVEL=DEBUG|INFO|WARNING            # default: INFO

# --- Ollama (local mode) ---
OLLAMA_MODEL=qwen2.5:14b
OLLAMA_EMBED_MODEL=nomic-embed-text-8k
OLLAMA_CHAT_NUM_CTX=8192
OLLAMA_CHAT_NUM_PREDICT=2048
OLLAMA_EMBED_NUM_CTX=8192

# --- OpenAI (cloud mode) ---
OPENAI_API_KEY=...
OPENAI_MODEL=gpt-4o-mini
OPENAI_EMBED_MODEL=text-embedding-3-small

# --- Vector + embeddings ---
CHROMA_PATH=./vault/.chroma
EMBED_CHAR_LIMIT=20000                  # auto-shrink su context overflow

# --- Async ---
ASYNC_CONCURRENCY=                      # default 8 cloud, 1 local

# --- Google Maps (opzionale, place reviews) ---
GOOGLE_MAPS_API_KEY=

# --- Google Drive (PDF source) ---
GDRIVE_CREDENTIALS_JSON=/path/sa.json
GDRIVE_INBOX_PDF_FOLDER_ID=
GDRIVE_PROCESSED_PDF_FOLDER_ID=

# --- RSS feeds ---
FEEDS_CONFIG_PATH=                      # default $VAULT_PATH/.config/feeds.json
FEED_MAX_ENTRIES_PER_FEED=3             # top-N per feed
FEED_DAYS_BACK=-1                       # date window opzionale, -1 = disabled

# --- Legacy ---
PINECONE_API_KEY=
PINECONE_INDEX=second-brain
```

Tutti gli accessi env vivono in `second_brain/config.py`. Non leggere
`os.environ` altrove.

## Mappa moduli (orientamento rapido)
| Cosa modificare | File |
|-----------------|------|
| Aggiungere/rinominare env var | `second_brain/config.py` |
| Nuova sorgente (es. Reddit) | `second_brain/sources.py` + flow in `pipeline.gather_sources` |
| Output formato nota classificata | `second_brain/rendering.py::render_classified_note` |
| Orchestratore (gather→embed→classify→write) | `second_brain/pipeline.py` |
| Backend LLM (nuovo provider) | `second_brain/llm.py` (sync + async + retry) |
| Cache embedding | `second_brain/embedding_cache.py` |
| Vector store (Pinecone) | `second_brain/vector.py` |
| Logica dedup/state | `second_brain/state.py` |
| Drive operations | `second_brain/drive.py` |
| Comandi CLI | `second_brain/cli.py` |
| Prompt sistema | `prompts/*.txt` (no rebuild, `@lru_cache(8)` reload manuale) |

## Formato nota classificata (Notes/<Categoria>/<slug>.md)
```markdown
---
category: Tech
correlations:
  - '[[Notes/Kubernetes/Basics]]'
date_processed: '2026-05-11'
source: https://www.ft.com/content/...
tags:
  - kubernetes
  - operators
  - platform-engineering
title: Kubernetes Operators Pattern
---

## 📝 Summary _(generated 2026-05-11)_
> Sintesi 3-5 frasi LLM-curate.

**Tag**: #kubernetes #operators #platform-engineering
**Connesso a**: [[Notes/Kubernetes/Basics]]

---

# Kubernetes Operators Pattern

<body originale markdown (HTML→md via markdownify per .html, raw per .md)>
```

## Delta tracking — State File
Workflow **idempotente**. Ogni sorgente ha `vault/.state/processed_<source>.json`:

```json
{"processed": ["id1", "id2"], "last_run": "2026-05-11T15:33:42"}
```

- **Articles**: ID = vault-relative path (`Inbox/articles/foo.html`)
- **YouTube**: ID = URL video
- **Places**: ID = `place_id` (fallback: path)
- **PDFs**: ID = Drive `fileId` (immutabile)
- **Feeds**: ID = entry `guid`/`link`

API pubblica (`second_brain/state.py`):
```python
get_new_items(source_type, inbox_path) -> list[Path]   # filesystem-based
filter_unseen(source_type, ids)        -> list[str]    # generic (Drive/feed)
mark_processed(source_type, ids)       -> None
get_item_id(source_type, file_path)    -> str
reset_state(source_type)               -> None
```

State aggiornato SOLO dopo write riuscito.

### `.state/`, `.cache/`, `.chroma/` e `.gitignore`
Tutte gitignored ed escluse da Obsidian Sync. Stato locale macchina.

## Pipeline async
```
asyncio.run(_pipeline())
  ├─ gather_sources(target_date=None)         [parallel]
  │   ├─ to_thread(_gather_file_sources)     ── articles/youtube/places
  │   ├─ to_thread(gather_pdf_sources)        ── Drive PDFs
  │   └─ gather_feed_sources(target_date)     ── RSS (httpx + Semaphore)
  ├─ embed_sources(sources)                   [Semaphore-bounded async]
  │   └─ embed_text_safe_async() × N          (cache hit → 0 API call)
  └─ classify_sources(sources)                [Semaphore-bounded async]
      └─ per source:
          ├─ to_thread(vector.query_correlations)
          └─ call_llm_async() → JSON {category, summary, tags, correlations}
```

Concurrency cap: `ASYNC_CONCURRENCY` env (8 cloud, 1 local).
Retry: `_retry_async` con exp backoff su transient (timeout, 429, 5xx).

## Cost tracking (cloud only)
Module-level counter in `llm.py`. `reset_usage()` a inizio run, `usage_summary()`
ritorna `{prompt_tokens, completion_tokens, embed_tokens, cache_hits,
estimated_usd}`. CLI logga summary fine run.

Pricing in `_PRICING_PER_1M_USD` — sync manuale con
https://openai.com/api/pricing/.

## Embedding cache
`vault/.cache/embeddings.db` SQLite. Key = `sha256(model_namespace || text)`.
Namespace = `cloud:text-embedding-3-small` vs `local:nomic-embed-text-8k` —
cambio modello = cache separata, no stale.

Float32 raw bytes via `struct.pack`. Encoding/decoding lossless per le
nostre dim (1536). Safe-to-delete: rebuild su miss.

## RAG `ask`
- Embed query → Chroma top-K (default 8) → builds context block
  `[i] path/title/kind/tags/excerpt` → `call_llm_text` (prompt
  `ask.txt`, no JSON mode) → markdown answer con wiki-link citations.
- Vault sources: Notes/ + Daily/ (indicizzati con `kind` metadata).
- Output stdout, log cost/cache stats su stderr.

## Regola lingua (recap + tag)
Output in italiano o inglese, scelto dalla lingua dell'input:
- Contenuto IT → recap IT + tag IT
- Contenuto EN → recap EN + tag EN
- Altra lingua → fallback EN

Prompt scritti in inglese (LLM seguono meglio istruzioni EN), con direttiva
esplicita sulla lingua output.

## Regole critiche
- Tag SEMPRE kebab-case lowercase (`platform-engineering`), normalizzati in
  `rendering.kebab()`
- Wikilink: path relativo Notes/ senza estensione `.md`
- Output nota per-item: `Notes/<Categoria>/<slug>.md` (slug via
  `rendering.safe_filename`, max 100c, unicode-safe, collision → `_1`,`_2`)
- Categoria sanitized: no `/\:*?"<>|`, max 80c, fallback `Uncategorized`
- Logging su `stderr`; output file/answer su `stdout` (per `--dry-run` / `ask`)
- Zero `print()` nel codice: solo `logger.*`
- State aggiornato SOLO dopo write riuscito (no orphan state)
- File Inbox eliminato (`unlink`) DOPO state commit, solo per per-item flow
- Max 7 tag per fonte (era 5, alzato per classify), max 5 correlations
- Categorie esistenti: scan top-level Notes/ folder, escluse archive subdirs

## Gotchas
- `youtube-transcript-api` v1.x: usa `YouTubeTranscriptApi().fetch(video_id)`,
  no più `get_transcript` legacy
- `readability-lxml` su pagine JS-rendered → fallback raw text
- Articoli `.md`: bypass readability, body markdown usato direttamente
- Articoli `.html`: readability `summary()` → `markdownify` (preserva
  heading/list/link)
- `_html_to_markdown` fallback a `text_content()` se markdownify mancante
- YouTube `.md`: URL via regex dal body o frontmatter; metadati
  `channel/published/duration/thumbnail/description` → `Source.extra`
- ChromaDB usa `upsert` (no `add`) per evitare duplicati su re-index
- Ollama deve girare prima del workflow: `ollama serve &`
- Ollama async = serializza (1 model in memoria). `ASYNC_CONCURRENCY=1`
  local è ottimo (parallelizzare = overhead senza gain)
- Frontmatter YAML: SEMPRE `python-frontmatter`, mai parsing manuale
- Path Obsidian nei wikilink: NO estensione `.md`
- Places senza `place_id`: path come ID nello state
- Google Drive: service account NON ha quota personale, cartelle DEVONO
  essere condivise come Editor con `client_email` dal JSON SA
- Drive `fileId` immutabile, sopravvive rename → ottimo state ID
- LLM JSON-mode: forzato in `call_llm`/`call_llm_async`
  (`format="json"` Ollama, `response_format={"type":"json_object"}` OpenAI).
  Per markdown output usa `call_llm_text` (no JSON constraint).
- `parse_llm_json` fallback: estrae oggetto tra prima `{` e ultima `}`
- Feed: top-N per feed (default 3), poi filter_unseen via state — combinano
- Feed entry senza body inline (TLDR): fetch URL → `markdownify`-strip → body
- httpx in `_fetch_url_body_async`: `follow_redirects=True` essenziale
  (es. tldr.tech/webdev redirect 308)
- Embedding cache: namespace per model evita stale su cambio modello
- Cache thread-safe via `_lock` (per `to_thread` async paths)
- Retry: 3 attempts exp backoff 1s/2s/4s, SOLO su transient (timeout/429/5xx).
  Errori permanenti (auth, bad request) propagano subito.
- 3rd party logger silenziati in `configure_logging`: readability, httpx,
  httpcore, urllib3 → WARNING

## Anti-pattern da evitare
- Non usare `os.system()` — usare `subprocess.run()`
- Non hardcodare path — tutto via `VAULT_PATH` (`config.vault_path()`)
- Non saltare il deduplication (state filter)
- Non generare più di 7 tag per fonte
- Non inventare wikilink verso note inesistenti
- Non aggiornare state file se write ha fallito
- Non leggere `os.environ` fuori da `config.py`
- Non aggiungere `print()` — solo `logging`
- Non importare `openai`/`ollama` al top-level (lazy import per minimizzare
  startup)
- Non riusare coroutine attraverso retry — `_retry_async` riceve factory
- Non scrivere Daily aggregato — workflow è per-item ora
