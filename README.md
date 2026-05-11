# Second Brain

Workflow Python giornaliero che legge articoli web, transcript YouTube e
luoghi Google Maps salvati nella vault Obsidian, li correla con le note
esistenti tramite embeddings e produce una nota di recap in `Daily/`.

## Struttura repo

```
second-brain/
├── second_brain/        # package Python
│   ├── cli.py           # entrypoint `second-brain`
│   ├── config.py        # env vars
│   ├── models.py        # Source dataclass
│   ├── state.py         # delta tracking (.state/)
│   ├── sources.py       # extractors article/youtube/place
│   ├── llm.py           # ollama/openai dispatch + prompts
│   ├── vector.py        # ChromaDB helpers
│   ├── rendering.py     # daily note markdown
│   └── pipeline.py      # orchestrator + indexer
├── prompts/             # template editabili (recap, place, tags)
├── tests/               # pytest
├── pyproject.toml
├── .env.example
└── README.md
```

## Setup rapido (5 comandi)

```bash
git clone <repo> second-brain && cd second-brain
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env       # poi modifica i valori
second-brain index         # indicizza Notes/ una tantum
```

Per modalità locale: avvia Ollama prima dell'indicizzazione.

```bash
ollama serve &
ollama pull llama3
ollama pull nomic-embed-text
```

## Struttura vault

```
vault/
├── Inbox/                      # input transitorio
│   ├── articles/               # .html o .md (Web Clipper)
│   ├── youtube/                # .txt con URL YouTube
│   └── places/                 # .json place Google Maps
├── Daily/                      # OUTPUT
│   ├── YYYY-MM-DD.md           # Daily corrente
│   └── YYYY/MM/YYYY-MM-DD.md   # Daily archiviati (auto)
├── Notes/                      # note personali Obsidian
│   ├── *.md                    # tue note (indicizzate per correlazioni)
│   ├── articles/YYYY-MM-DD/    # articoli processati (auto, NON indicizzati)
│   ├── youtube/YYYY-MM-DD/     # transcript processati (auto)
│   └── places/YYYY-MM-DD/      # place processati (auto)
└── .state/                     # interno workflow (gitignored)
```

**Archiviazione automatica**

Dopo ogni `second-brain run` con successo:
- I file `Inbox/{kind}/*` vengono spostati in `Notes/{kind}/{run-date}/`.
- I Daily passati (qualunque `Daily/YYYY-MM-DD.md` con data != run corrente)
  vengono spostati in `Daily/YYYY/MM/YYYY-MM-DD.md`.
- `--dry-run` non sposta nulla.

Le sottocartelle `Notes/articles/`, `Notes/youtube/`, `Notes/places/` sono
escluse dall'indexing Chroma per evitare che gli articoli grezzi compaiano
come "note correlate". Le tue note personali devono stare direttamente
sotto `Notes/` o in sottocartelle a tema diverse (es. `Notes/Kubernetes/`).

Crea manualmente le cartelle prima della prima run:

```bash
mkdir -p vault/{Inbox/{articles,youtube,places},Daily,Notes}
```

## Configurazione `.env`

### Senza Google Maps API
Lascia `GOOGLE_MAPS_API_KEY=` vuoto. I place vengono comunque processati
usando solo i metadati nel JSON; non verranno aggiunte recensioni recenti.

### Con Google Maps API
1. Abilita Places API su Google Cloud Console
2. Crea API key con restrizione su Places API
3. Inserisci la chiave in `GOOGLE_MAPS_API_KEY=`

### Modalità LLM
- `LLM_MODE=local` → Ollama (gratis, offline, più lento)
- `LLM_MODE=cloud` → OpenAI (richiede `OPENAI_API_KEY`, più veloce)

## Come aggiungere contenuti all'Inbox

### Articoli
Estensioni accettate: `.html`, `.htm`, `.md`.

**Manuale (HTML):** salva la pagina con `Cmd+S` come `.html` in
`vault/Inbox/articles/`. Viene processato da readability-lxml.

**curl (HTML):**
```bash
curl -sL "https://example.com/post" -o vault/Inbox/articles/2026-05-11_slug.html
```

**Obsidian Web Clipper (Markdown):** configura per salvare in
`vault/Inbox/articles/` come `.md`. Lo script riconosce il frontmatter:

```yaml
---
title: "Titolo Articolo"      # usato come titolo nella nota Daily
source: "https://..."         # URL canonico (o `url:`)
clipped_at: 2026-05-11
---

Corpo dell'articolo in markdown.
```

Senza frontmatter, `title = nome file (senza ext)` e `url = file://...`.
Per articoli `.md` readability viene bypassato: il body markdown è già
pulito.

### YouTube
Estensioni accettate: `.txt` (URL nudo) o `.md` (frontmatter + URL nel body).

**`.txt` manuale (minimal):**
```bash
echo "https://youtu.be/dQw4w9WgXcQ" > vault/Inbox/youtube/2026-05-11_video.txt
```

**`.md` con frontmatter (consigliato, simile al flusso articoli):**

```yaml
---
title: "Titolo del video"
url: "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
channel: "Nome canale"
published: 2026-05-10
duration: "00:42:10"
thumbnail: "https://img.youtube.com/vi/dQw4w9WgXcQ/maxresdefault.jpg"
description: "Una riga descrittiva opzionale"
clipped_at: 2026-05-11
---

https://www.youtube.com/watch?v=dQw4w9WgXcQ
```

Lo script:
- estrae l'URL via regex dal contenuto (body o frontmatter)
- usa `title` dal frontmatter (fallback: `YouTube video {video_id}`)
- mette `channel`, `published`, `duration`, `thumbnail`, `description` in
  `Source.extra` (disponibili per usi futuri, es. include nel Daily output)
- scarica il transcript via `youtube-transcript-api` indipendentemente dai
  metadati del file

**Properties suggerite (per Web Clipper):**

| Property | Tipo | Web Clipper variable | Note |
|----------|------|----------------------|------|
| `title` | string | `{{title}}` | Titolo del video |
| `url` | string | `{{url}}` | URL canonico |
| `channel` | string | `{{author}}` o `{{meta:itemprop=author}}` | Nome canale |
| `published` | date | `{{published}}` o `{{meta:itemprop=datePublished}}` | Data pubblicazione |
| `duration` | string | `{{meta:itemprop=duration}}` (ISO 8601 → mm:ss se serve) | Durata |
| `thumbnail` | string | `{{image}}` o `https://img.youtube.com/vi/{{video_id}}/maxresdefault.jpg` | Anteprima |
| `clipped_at` | date | `{{date}}` | Quando l'hai salvato |
| `tags` | list | manuale | Hint pre-tagging (opzionale) |

**Web Clipper template YouTube:**

- **Triggers**: URL contains `youtube.com/watch`, URL contains `youtu.be/`
- **Output type**: Markdown
- **Path**: `Inbox/youtube`
- **File name**: `{{date|YYYY-MM-DD}}_{{title|slug}}.md`
- **Frontmatter**: usa la tabella sopra
- **Body**: `{{url}}`

**Limite:** un file = un video. Più URL nello stesso file vengono ignorati
(solo il primo match viene processato). Crea un file per video.

### Place Google Maps
**Manuale:** crea un `.json` in `vault/Inbox/places/`:

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
  "notes_personali": "consigliato da Luca",
  "saved_at": "2026-05-10T19:30:00"
}
```

**iOS Shortcut hint:** crea uno Shortcut "Save Place" che riceve un link
Google Maps via Share Sheet, costruisce il JSON sopra e lo salva via iCloud
Drive in `vault/Inbox/places/`. Usa l'azione *Get Details of URL* per
estrarre titolo e indirizzo dalla pagina.

## Uso

```bash
second-brain run                  # ieri, modalità da LLM_MODE
second-brain run --date 2026-05-09
second-brain run --dry-run        # stampa su stdout, non scrive
second-brain run --mode cloud     # override su OpenAI
second-brain index                # reindicizza Notes/ (full)
second-brain index --incremental  # solo file modificati
```

Equivalente senza entrypoint installato:

```bash
python -m second_brain run --dry-run
```

## Delta tracking — `.state/`

Ogni sorgente ha un file `vault/.state/processed_{source}.json` con la lista
degli ID già elaborati. Il workflow è **idempotente**: rilanciandolo non
duplica output. Lo state file viene aggiornato SOLO se la scrittura della
nota Daily è andata a buon fine.

`.state/` è in `.gitignore` ed escluso da Obsidian Sync: è uno stato locale
della macchina che esegue il workflow.

**Forzare re-elaborazione completa:**

```bash
python -c "from second_brain import state; state.reset_state('articles')"
```

## Cron (macOS)

Esegui ogni mattina alle 07:00:

```cron
0 7 * * * cd /Users/<you>/path/to/second-brain && /Users/<you>/path/to/second-brain/.venv/bin/second-brain run >> /tmp/second-brain.log 2>&1
```

Aggiungi con `crontab -e`. Verifica che il binario Python sia quello del
virtualenv (non quello di sistema).

## Sviluppo

```bash
pip install -e ".[dev]"
pytest                  # unit test
ruff check .            # lint
ruff format .           # format
```

## Troubleshooting

**Ollama non risponde**
```bash
ollama serve &
curl http://localhost:11434/api/tags   # deve rispondere 200
```
Se i modelli non ci sono: `ollama pull llama3 && ollama pull nomic-embed-text`.

**Transcript YouTube non disponibile**
Capita quando il video non ha sottotitoli (né manuali né auto-generati), o è
geo-bloccato. Lo script logga un warning e salta il video. Soluzioni:
- usare un video con sottotitoli
- impostare proxy/VPN se geo-bloccato
- per video critici, generare transcript manualmente e salvare il testo
  direttamente in `vault/Inbox/articles/` come `.html` con `<body>...</body>`

**`place_id` mancante nel JSON**
Lo script usa il path del file come ID nello state. Funziona, ma non puoi
chiamare la Places API per le recensioni recenti. Aggiungi `place_id`
estraendolo dall'URL del place (parametro `cid` o uso esplicito dell'API
Place Search).

**ChromaDB non raggiungibile / `Notes/` vuota**
Il workflow procede comunque, senza correlazioni (nessun wikilink in output).
Esegui `python index_vault.py` per popolare il vector store.

**OpenAI Batch API**
Non implementata nel main script perché ha latenza fino a 24h. Per cron
giornaliero usa le Chat Completions sincrone (default in `LLM_MODE=cloud`).
