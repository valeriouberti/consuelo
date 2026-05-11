# Second Brain

Workflow Python che processa contenuti salvati nella tua vault Obsidian
(articoli web, transcript YouTube, place Google Maps, PDF su Google Drive,
RSS feed) e li trasforma in note arricchite: ogni item viene classificato
da un LLM, ottiene categoria + tag + summary + correlations con le tue
note esistenti, e finisce in `Notes/<Categoria>/<slug>.md`.

Include un comando RAG `ask` per interrogare l'intera vault in linguaggio
naturale.

## Quick start (5 comandi)

```bash
git clone <repo> second-brain && cd second-brain
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env                # modifica i valori
mkdir -p vault/{Inbox/{articles,youtube,places},Notes}
```

### Modalità locale (Ollama, gratis, offline)

```bash
ollama serve &
ollama pull qwen2.5:14b
ollama pull nomic-embed-text-8k
```

### Modalità cloud (OpenAI, veloce, paghi)

Imposta in `.env`:
```
LLM_MODE=cloud
OPENAI_API_KEY=sk-...
```

## Cosa fa il workflow

```
Inbox/articles/<file>.html|.md
    ↓
[extract]  readability → markdown via markdownify
    ↓
[embed]    cache hit O(1) o API call
    ↓
[classify] LLM → JSON {category, summary, tags, correlations}
    ↓
[write]    Notes/<Categoria>/<slug>.md (frontmatter + summary + body)
    ↓
[cleanup]  delete Inbox file, mark state processed
```

### Output `.md` finale

```markdown
---
category: Tech
correlations: ['[[Notes/Kubernetes/Basics]]']
date_processed: '2026-05-11'
source: https://www.ft.com/content/...
tags: [kubernetes, operators, platform-engineering]
title: Kubernetes Operators Pattern
---

## 📝 Summary _(generated 2026-05-11)_
> Sintesi 3-5 frasi LLM-curate.

**Tag**: #kubernetes #operators #platform-engineering
**Connesso a**: [[Notes/Kubernetes/Basics]]

---

# Kubernetes Operators Pattern

<body originale markdown preservato>
```

## Struttura vault

```
vault/
├── Inbox/                          # input transitorio
│   ├── articles/                   # .html (Web Clipper, curl) o .md
│   ├── youtube/                    # .txt (URL) o .md (TODO per-item)
│   └── places/                     # .json (TODO per-item)
├── Notes/                          # OUTPUT + tue note personali
│   ├── Tech/                       # auto-create dal classifier
│   ├── Finance/
│   ├── Travel/
│   └── <Le tue note>/              # indicizzate per correlations
├── .config/
│   └── feeds.json                  # lista RSS (opzionale)
├── .state/                         # delta tracking (gitignored)
├── .cache/                         # embedding cache (gitignored)
└── .chroma/                        # vector store (gitignored)
```

**Categoria libera con hint**: il classifier riceve la lista delle
cartelle già esistenti sotto `Notes/` e preferisce riusarle invece di
inventare duplicati ("AI" vs "Artificial Intelligence" → sceglie quella
esistente).

## Comandi

```bash
# Processa articoli in Inbox
second-brain run
second-brain run --dry-run                  # stampa output, no scrittura
second-brain run --mode cloud               # override OpenAI

# RAG sulle tue note + Daily
second-brain ask "cosa ho letto sui Kubernetes operators?"
second-brain ask "ricorda i posti che voglio visitare a Berlino" -k 12

# Indicizza vault (Notes/ + Daily/) per le correlations
second-brain index
second-brain index --incremental            # solo file modificati
second-brain index --no-daily               # solo Notes/

# Equivalente senza entrypoint installato
python -m second_brain run
```

## Come aggiungere contenuti

### Articoli (`Inbox/articles/`)

Estensioni: `.html`, `.htm`, `.md`.

**HTML manuale** — salva la pagina con `Cmd+S` come `.html`:
```bash
curl -sL "https://example.com/post" -o vault/Inbox/articles/2026-05-11_slug.html
```

**Obsidian Web Clipper (Markdown)** — configura output `vault/Inbox/articles/`,
frontmatter così:
```yaml
---
title: "Titolo Articolo"      # usato come slug filename
source: "https://..."         # URL canonico (o `url:`)
clipped_at: 2026-05-11
---

Body dell'articolo in markdown.
```

Senza frontmatter: `title = nome file` e `url = file://...`.

### YouTube (TODO)

Estensioni: `.txt` (URL nudo) o `.md` (frontmatter + URL).
Attualmente **gathered ma non scritti** nel per-item flow. Verranno
implementati nella prossima iterazione.

### Place Google Maps (TODO)

`.json` in `Inbox/places/`. Stessa nota: gathered ma non scritti.

```json
{
  "source": "google_maps",
  "place_id": "ChIJ...",
  "name": "Osteria da Mario",
  "category": "Ristorante italiano",
  "address": "Via Colleoni 4, Bergamo",
  "rating": 4.6,
  "url": "https://maps.google.com/?cid=...",
  "notes_personali": "consigliato da Luca"
}
```

### PDF (Google Drive)

PDF non vivono in git/vault — su Drive, source of truth. Pipeline legge,
estrae articoli con heading-detection font-size, scrive note arricchite.

**Setup una tantum**:

1. Crea Service Account su Google Cloud → scarica JSON key
2. Crea 2 cartelle Drive: `Inbox/PDFs/` e `Processed/PDFs/`
3. Condividile come **Editor** con email service account
4. Copia folder ID dall'URL Drive e metti in `.env`:
   ```
   GDRIVE_CREDENTIALS_JSON=/path/sa.json
   GDRIVE_INBOX_PDF_FOLDER_ID=1AbC...
   GDRIVE_PROCESSED_PDF_FOLDER_ID=1XyZ...
   ```

Quotidiano: salvi PDF su `Inbox/PDFs/` su Drive. Pipeline scarica,
processa, sposta a `Processed/PDFs/`.

### RSS feed (Newsletter)

Crea `vault/.config/feeds.json`:
```json
[
  {"name": "TLDR Tech", "url": "https://tldr.tech/api/rss/tech"},
  {"name": "TLDR AI",   "url": "https://tldr.tech/api/rss/ai"}
]
```

Feed top-N entries più recenti per feed (default 3). Per newsletter
email-only (FT, WSJ), opzioni:
1. Cerca RSS premium subscriber (FT `myFT > Feeds`)
2. **Kill the Newsletter** — `kill-the-newsletter.com` genera email
   anonimo che converte newsletter in feed RSS

`FEED_MAX_ENTRIES_PER_FEED=N` per cambiare cap (recovery dopo weekend).

## Comando `ask` (RAG)

Interroga la vault in linguaggio naturale:

```bash
second-brain ask "quali pattern distribuiti ho studiato?"
```

Embedda la query → cerca top-K entries in Chroma (Notes/ + Daily/) →
LLM risponde con citation wiki-link. Richiede `second-brain index` una
volta per popolare il vector store.

Output stdout, formato:
```markdown
Negli ultimi mesi hai studiato consensus distribuito tramite Raft
[[Notes/Distributed/Raft]] e Paxos [[Notes/Distributed/Paxos]]...

## Fonti
- [[Notes/Distributed/Raft]] — Raft consensus
- [[Notes/Distributed/Paxos]] — Multi-Paxos
```

## Configurazione `.env`

Vedi `.env.example` per la lista completa. Variabili principali:

| Env | Default | Uso |
|-----|---------|-----|
| `VAULT_PATH` | `./vault` | Root vault Obsidian |
| `LLM_MODE` | `local` | `local` (Ollama) o `cloud` (OpenAI) |
| `ASYNC_CONCURRENCY` | 8 cloud, 1 local | Concurrent LLM/embed/HTTP |
| `EMBED_CHAR_LIMIT` | 20000 | Auto-shrink su context overflow |
| `FEED_MAX_ENTRIES_PER_FEED` | 3 | Top-N per feed |
| `LOG_LEVEL` | INFO | DEBUG per verbose |

### Modelli

- **Local Ollama** (default): `qwen2.5:14b` (chat) + `nomic-embed-text-8k` (embed)
  - Tradeoff: gratis, offline, ~12s per articolo
- **Cloud OpenAI**: `gpt-4o-mini` + `text-embedding-3-small`
  - Tradeoff: ~$0.01 per 100 articoli, ~2s per articolo

## Async + cache

Pipeline parallelizza embed + LLM + URL fetch via `asyncio` + `httpx`.
Cloud: ~5x speedup vs sync. Local: serializzato da Ollama, ma codice
compatible.

**Embedding cache** SQLite in `vault/.cache/embeddings.db`. Stesso
articolo re-processed = 0 chiamate API embed. Cache namespace per modello
(cambio modello = cache separata).

## Cost tracking

Cloud mode logga a fine run:
```
INFO embedding cache: 17 hits
INFO cost: $0.0042 (chat: 1240 prompt + 380 completion @ gpt-4o-mini
              | embed: 2100 @ text-embedding-3-small)
```

Pricing hardcoded in `llm.py::_PRICING_PER_1M_USD`, sync manuale con
[OpenAI pricing](https://openai.com/api/pricing/).

## Cron / automation

**Crontab macOS** (ogni mattina alle 07:00):
```cron
0 7 * * * cd /Users/<you>/path/to/second-brain && /Users/<you>/path/to/second-brain/.venv/bin/second-brain run >> /tmp/second-brain.log 2>&1
```

Per GitHub Actions in produzione: setup OAuth/SA + vault sync (rsync,
git LFS, S3). TODO esempio workflow.

## Sviluppo

```bash
pip install -e ".[dev]"
pytest                  # unit test (22 attivi)
ruff check .            # lint
ruff format .           # format
```

## Troubleshooting

**Ollama non risponde**
```bash
ollama serve &
curl http://localhost:11434/api/tags   # deve rispondere 200
```

**Articoli `.html` con poca struttura preservata**
Readability+markdownify produce risultato pulito ma non perfetto per
pagine JS-rendered o con layout complesso. Salva direttamente in `.md`
via Web Clipper se possibile.

**Categoria nuova creata invece di riusare esistente**
LLM riceve `existing_categories()` come hint. Se sbaglia, è il prompt
da raffinare (`prompts/classify.txt`) o lascia che impari su volume.
Folder simili "AI" vs "Artificial Intelligence" si possono mergiare a
mano una volta accumulate.

**Drive: service account 403 / file non trovato**
Service account NON ha quota propria. Cartelle DEVONO essere condivise
come Editor con `client_email` dal JSON SA. Verifica condivisione.

**RSS feed bozo / parsing error**
Feed malformato lato producer. Log warning, skip feed. Verifica URL feed
con `curl -sL <url>`.

**`feeds.json` malformed JSON**
File vuoto/corrupt → log warning, feed skipped. Cancella file o ripristina
formato valido.

**Daily/ non più aggiornato**
Workflow è ora per-item. Daily aggregato disabilitato. Le note enriched
vivono in `Notes/<Categoria>/`. Per re-attivare Daily, modifica `cli.py::run`.

**Cost tracking sballato**
Pricing in `llm.py::_PRICING_PER_1M_USD` può essere obsoleto. Update sync
con OpenAI pricing page.

**Embedding cache occupa molto spazio**
Cache safe-to-delete: `rm vault/.cache/embeddings.db`. Rebuild on demand.

**ChromaDB warning / vault empty**
Workflow procede senza correlations (`source.correlations = []`).
Esegui `second-brain index` per popolare.
