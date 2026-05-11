# Second Brain — Project Context

## Progetto
Workflow Python giornaliero che legge articoli web, transcript YouTube e
luoghi Google Maps salvati, li correla con le note Obsidian esistenti tramite
embeddings, e genera una nota di recap in `Daily/YYYY-MM-DD.md` con tag e
wikilink.

## Stack
- **Python 3.12** (`pyproject.toml`, hatchling build backend)
- **LLM locale (test)**: Ollama — `llama3` + `nomic-embed-text`
- **LLM cloud (produzione)**: OpenAI `gpt-4o-mini` via Chat Completions sincrone
  (no Batch API: latenza fino a 24h incompatibile con cron giornaliero)
- **Vector DB**: ChromaDB (locale) → Pinecone Serverless (cloud, futuro)
- **Vault**: Obsidian `.md` con frontmatter YAML
- **Scheduler**: cron locale (test) → GitHub Actions (produzione)
- **Embedding cloud**: `text-embedding-3-small`
- **Maps**: Google Maps Places API (opzionale, per arricchire metadati place)
- **Tooling**: `ruff` (lint+format), `mypy`, `pytest`

## Struttura repo
```
second-brain/
├── second_brain/         # package Python
│   ├── __init__.py
│   ├── __main__.py       # python -m second_brain
│   ├── cli.py            # click group: run + index
│   ├── config.py         # tutti gli os.environ.get() vivono qui
│   ├── models.py         # Source dataclass + SourceType/StateSource
│   ├── archive.py        # post-processing: move Inbox→Notes, Daily→YYYY/MM
│   ├── state.py          # delta tracking (.state/*.json)
│   ├── sources.py        # extractors: extract_article/youtube/place
│   ├── llm.py            # embed_text + call_llm (ollama/openai dispatch)
│   ├── vector.py         # ChromaDB open/query/upsert
│   ├── rendering.py      # frontmatter + markdown daily note
│   └── pipeline.py       # orchestrator daily + indexer Notes/
├── prompts/              # template editabili (recap/place/tags), scritti in
│                         #   inglese; il recap esce in IT o EN secondo la
│                         #   lingua dell'input
├── tests/                # pytest unit (state, rendering)
│   └── conftest.py       # fixture `vault` con monkeypatch VAULT_PATH
├── pyproject.toml        # PEP 621 + ruff + mypy + pytest config
├── .env.example
├── .gitignore
├── README.md
└── CLAUDE.md
```

## Struttura vault Obsidian
```
vault/
├── Inbox/                      ← input transitorio (svuotato dopo processing)
│   ├── articles/               ← .html (readability-lxml) OR .md (Web Clipper)
│   ├── youtube/                ← .txt (URL nudo) OR .md (URL + frontmatter)
│   └── places/                 ← .json con dati place Google Maps
├── Daily/                      ← OUTPUT: recap generati
│   ├── YYYY-MM-DD.md           ← Daily corrente (root)
│   └── YYYY/MM/YYYY-MM-DD.md   ← Daily passati archiviati (auto)
├── Notes/                      ← note personali da correlare
│   ├── *.md                    ← le tue note (indicizzate in Chroma)
│   ├── articles/YYYY-MM-DD/    ← articoli processati (auto, NON indicizzati)
│   ├── youtube/YYYY-MM-DD/     ← transcript YouTube processati (auto)
│   └── places/YYYY-MM-DD/      ← place JSON processati (auto)
└── .state/                     ← stato interno workflow (non sincronizzare)
    ├── processed_articles.json
    ├── processed_youtube.json
    ├── processed_places.json
    └── last_index.txt
```

**Archiviazione automatica (post-processing)**
Dopo ogni run con scrittura riuscita:
1. I file Daily passati in `Daily/*.md` vengono spostati in
   `Daily/{YYYY}/{MM}/{YYYY-MM-DD}.md` (solo file con stem data valida).
2. I file processati in `Inbox/{kind}/` vengono spostati in
   `Notes/{kind}/{run-date}/` per archiviazione cronologica.
3. Su collisione di nome, viene aggiunto suffisso `_1`, `_2`, ...
4. Le sottocartelle `Notes/{articles,youtube,places}/` sono ESCLUSE
   dall'indexing Chroma (vedi `archive.EXCLUDED_NOTES_SUBDIRS`). Le tue note
   personali "vere" devono stare direttamente sotto `Notes/` o in
   sottocartelle a tema con nomi diversi (es. `Notes/Kubernetes/`).

## Comandi principali
```bash
pip install -e ".[dev]"             # setup
second-brain index                  # indicizza Notes/ in ChromaDB
second-brain index --incremental    # solo file mtime > last_index
second-brain run                    # workflow giornaliero (ieri)
second-brain run --date 2026-05-09  # riesegui su data specifica
second-brain run --dry-run          # stampa su stdout, no scrittura/state
second-brain run --mode cloud       # forza OpenAI (override LLM_MODE)
python -m second_brain run          # equivalente senza entrypoint installato
pytest                              # test
ruff check . && ruff format .       # lint + format
```

## Variabili d'ambiente
```
VAULT_PATH=./vault
LLM_MODE=local|cloud          # default: local
OLLAMA_MODEL=llama3
OLLAMA_EMBED_MODEL=nomic-embed-text
OPENAI_API_KEY=...
OPENAI_MODEL=gpt-4o-mini
OPENAI_EMBED_MODEL=text-embedding-3-small
PINECONE_API_KEY=...
PINECONE_INDEX=second-brain
CHROMA_PATH=./vault/.chroma
GOOGLE_MAPS_API_KEY=...       # opzionale, solo se si usa Places API
LOG_LEVEL=DEBUG|INFO|WARNING  # default: INFO
```

Tutti gli accessi a env vivono in `second_brain/config.py`. Non leggere
`os.environ` altrove.

## Mappa moduli (orientamento rapido)
| Cosa modificare | File |
|-----------------|------|
| Aggiungere/rinominare env var | `second_brain/config.py` |
| Nuova sorgente (es. RSS) | `second_brain/sources.py` + entry in `EXTRACTORS` |
| Cambiare formato output Daily | `second_brain/rendering.py` |
| Logica orchestratore (gather→embed→enrich→commit) | `second_brain/pipeline.py` |
| Backend LLM (nuovo provider) | `second_brain/llm.py` |
| Vector store (es. Pinecone) | `second_brain/vector.py` |
| Logica dedup/state | `second_brain/state.py` |
| Archiviazione post-processing | `second_brain/archive.py` |
| Comandi CLI | `second_brain/cli.py` |
| Prompt sistema | `prompts/*.txt` (no rebuild) |

## Formato nota di output (Daily/YYYY-MM-DD.md)
```markdown
---
date: YYYY-MM-DD
tags: [tag1, tag2, tag3]
sources:
  - type: article|youtube|place
    title: "..."
    url: "..."
correlations:
  - "[[Notes/Nota Correlata]]"
---

## Recap del YYYY-MM-DD

### 📄 Titolo articolo
> Sintesi in 3-5 frasi (italiano o inglese, vedi regola lingua).
**Tag**: #tag1 #tag2
**Connesso a**: [[Notes/Nota]]

### 🎬 Titolo video YouTube
> Sintesi in 3-5 frasi.

### 📍 Nome Luogo — Città
> Sintesi 2-3 frasi: perché è interessante, categoria, note personali.
**Tag**: #città #categoria #da-visitare
**Connesso a**: [[Notes/Nota Correlata]]
[Apri in Maps](https://maps.google.com/?cid=...)

## 🔗 Connessioni tra i contenuti
Paragrafo che descrive pattern comuni tra i contenuti di oggi.
```

## Formato file place in input (Inbox/places/*.json)
```json
{
  "source": "google_maps",
  "place_id": "ChIJ...",
  "name": "Osteria da Mario",
  "category": "Ristorante italiano",
  "address": "Via Colleoni 4, Bergamo",
  "rating": 4.6,
  "reviews_count": 312,
  "url": "https://maps.google.com/?cid=...",
  "notes_personali": "consigliato da Luca, buon risotto",
  "saved_at": "2026-05-10T19:30:00"
}
```

## Delta tracking — Strategia State File
Il workflow è **idempotente**: può essere rilanciato più volte sulla stessa
data senza duplicare output. Ogni sorgente ha il proprio state file in
`vault/.state/`:

```python
# Struttura state file
{
  "processed": ["id1", "id2", ...],
  "last_run": "2026-05-10T07:05:00"
}
```

- **Articles**: ID = path relativo del file (es. `Inbox/articles/foo.html`
  oppure `Inbox/articles/foo.md` per clipping Markdown)
- **YouTube**: ID = URL del video (es. `https://youtube.com/watch?v=abc123`)
- **Places**: ID = `place_id` dal JSON, fallback path relativo se assente

API pubblica (`second_brain/state.py`):
```python
get_new_items(source_type, inbox_path) -> list[Path]
mark_processed(source_type, ids)       -> None   # post-write
get_item_id(source_type, file_path)    -> str
reset_state(source_type)               -> None
```

### Fallback mtime (prima run)
Se lo state file non esiste, usa `mtime == ieri` come bootstrap iniziale,
poi crea lo state file con tutti gli ID trovati.

### `.state/` e `.gitignore`
Il folder `.state/` NON va committato nel repo né sincronizzato da Obsidian
Sync. Già in `.gitignore`. Aggiungerlo anche all'exclude list di Obsidian
Sync.

## Regola lingua (recap + tag)
Output in italiano o inglese, scelto dalla lingua del contenuto in input:
- Contenuto IT → recap + tag IT
- Contenuto EN → recap + tag EN
- Altra lingua → fallback EN

Recap e tag devono coincidere nella lingua. Logica codificata nei prompt
(`prompts/recap.txt`, `prompts/tags.txt`). Place recap resta in italiano
(input strutturato locale, prompt `prompts/place.txt`).

I prompt di sistema sono scritti in inglese perché i modelli (qwen, llama)
seguono meglio le istruzioni in inglese e tendono a "trascinare" l'output
nella lingua del system prompt. Mantenere il prompt in inglese + direttiva
esplicita sulla lingua dell'output massimizza la compliance bilingue.

## Regole critiche
- Tag SEMPRE in kebab-case lowercase (`platform-engineering` non
  `PlatformEngineering`); normalizzazione in `rendering.kebab()`
- Wikilink usano il path relativo esatto della nota in `Notes/`, senza
  estensione `.md`
- Output primario SOLO in `vault/Daily/`
- Se un file Daily esiste già, **appendere** il body senza rigenerare il
  frontmatter (vedi `rendering.write_daily`)
- Logging su `stderr` (configurato in `config.configure_logging`),
  output file su `stdout` (per `--dry-run`)
- Zero `print()` nel codice: solo `logger.*`
- Gestire `KeyboardInterrupt` e scrivere comunque file parziale
- Lo state file va aggiornato SOLO se `write_daily` non lancia eccezioni
- Archiviazione file sorgente: solo DOPO `commit_state`. Se il move fallisce
  (permission, target esistente), si logga warning e si prosegue — lo state
  resta corretto, l'utente può spostare a mano
- Archiviazione Daily passati: PRIMA di `write_daily`. Sicura: usa `rename`
  atomico, salta i file di destinazione già esistenti
- Le sottocartelle `Notes/{articles,youtube,places}/` NON vanno indicizzate
  come note personali (filtro in `pipeline._is_excluded`)
- Max 5 tag per fonte, max 5 correlations per fonte

## Gotchas
- `youtube-transcript-api` restituisce lista di dict `{text, start, duration}`
  → joinali con spazio
- `readability-lxml` fallisce su pagine con JS-rendering → fallback al
  testo grezzo
- Articoli Markdown (`.md`): bypass readability, body usato direttamente.
  Metadata frontmatter opzionali: `title`, `source`/`url` (URL canonico)
- YouTube `.md`: l'URL viene estratto via regex dal body (o frontmatter).
  Metadata letti: `title`, `channel`, `published`, `duration`, `thumbnail`,
  `description` — finiscono in `Source.extra` per uso futuro
- YouTube transcript API: usare `YouTubeTranscriptApi().fetch(video_id, ...)`
  (API v1.x). Il legacy `get_transcript` non esiste più
- ChromaDB usa `upsert` non `add` per evitare duplicati su re-indicizzazione
- Ollama deve girare prima del workflow: `ollama serve &`
- Frontmatter YAML: usare `python-frontmatter`, mai parsing manuale
- Path Obsidian nei wikilink NON includono `.md`
- Places senza `place_id`: path file come ID nello state
- Google Maps Places API richiede fatturazione attiva anche nel free tier
- LLM JSON-mode: forzato via `format="json"` (Ollama) e
  `response_format={"type":"json_object"}` (OpenAI); fallback parser estrae
  oggetto tra prima `{` e ultima `}`

## Anti-pattern da evitare
- Non usare `os.system()` — usare `subprocess.run()`
- Non hardcodare path — tutto via `VAULT_PATH` env var (`config.vault_path()`)
- Non saltare il deduplication delle note già processate
- Non generare più di 5 tag per fonte
- Non inventare wikilink verso note inesistenti
- Non aggiornare lo state file se la scrittura della nota ha fallito
- Non leggere `os.environ` fuori da `config.py`
- Non aggiungere print() — solo `logging`
