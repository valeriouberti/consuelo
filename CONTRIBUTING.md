# Contributing to Consuelo

Thanks for your interest. Consuelo is a personal-knowledge automation tool — contributions are welcome, especially around new source types, vector-store backends, and prompt tuning.

## Before you open a PR

For anything beyond a typo fix or a one-line bug fix, **open an issue first** so we can agree on the direction. A short discussion up front saves rework.

Good issue candidates:

- a new source type (Reddit, Pocket, Readwise, Notion, …)
- a new LLM or embedding backend
- a new vector-store backend (Pinecone, Qdrant, pgvector)
- a non-trivial change to the classify prompt or the renderer
- anything touching `pipeline.py` orchestration

## Dev setup

```bash
git clone https://github.com/valeriouberti/consuelo.git
cd consuelo
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

For the local LLM path, you'll also need [Ollama](https://ollama.com/) running with the models:

```bash
ollama serve &
ollama pull qwen2.5:14b
ollama create nomic-embed-text-8k -f scripts/nomic-8k.Modelfile
```

## Workflow

```bash
ruff check .          # lint
ruff format .         # auto-format
mypy consuelo         # types (best-effort, not strict)
pytest                # unit tests
```

All four must pass before a PR is merged. CI runs the same commands.

## Code style

- **Async first.** New I/O paths should be async. Sync wrappers exist for code that can't be (Chroma, feedparser) — wrap them in `asyncio.to_thread`.
- **No `os.environ` outside `config.py`.** Every env var has a typed getter in `consuelo/config.py`. Add a new one there.
- **No `print`.** Use `logging`. Status to `stderr`, user-visible output (CLI answers, dry-run) to `stdout`.
- **State writes only after success.** Never call `state.mark_processed()` before the artefact is on disk.
- **Tags are kebab-case.** Categories are domain-level Title Case (see `prompts/classify.txt`).
- **One module = one boundary.** If a source needs to call an external API, that call lives in `sources.py` (or a dedicated module like `drive.py`), not in the pipeline.

## Tests

- Unit tests live under `tests/`. Each module has a matching `test_*.py`.
- External calls are stubbed — tests must not hit the network, the OpenAI API, Ollama, Drive, or Chroma.
- For a new source type, the minimum is a test that:
  1. Builds a fixture file in a `tmp_path` inbox.
  2. Calls the extractor.
  3. Asserts on the resulting `Source` shape (`type`, `title`, `content`, `state_id`, `state_source`).

## Adding a new source type

The full walkthrough is in [`docs/sources.md`](docs/sources.md#adding-a-new-source-type). TL;DR:

1. Write the extractor in `sources.py` returning `list[Source]`.
2. Wire it into `pipeline.py::gather_sources` as a parallel branch.
3. Pick a stable state key; reuse or extend `state.py::_STATE_FILES`.
4. Add the type to `cli.py::SUPPORTED_TYPES` if you want it written.
5. Add a unit test.

## Commit style

[Conventional Commits](https://www.conventionalcommits.org/) preferred but not enforced:

```
feat(sources): add Pocket extractor
fix(state): handle corrupt JSON gracefully
docs(architecture): clarify retry policy
refactor(pipeline): extract classify_one helper
```

Keep commits scoped and the message in the imperative mood.

## Out of scope

- Multi-user / shared-vault features. Consuelo is a single-user tool by design.
- Rewriting the prompts to be more elaborate. Short, declarative prompts beat verbose ones for JSON-mode tasks.
- Heavyweight UI. The CLI is the interface; Obsidian renders the output.

## License

By contributing you agree your contribution is licensed under the MIT License (see [LICENSE](LICENSE)).
