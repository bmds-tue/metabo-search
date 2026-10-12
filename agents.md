# metabo-search — maintenance notes

Search two metabolomics repositories from Python: **MetaboLights** and
**Metabolomics Workbench**. Two independent, strictly typed clients, plus (next)
a local DuckDB working set built from search results. The repositories are
**never unified** — not in clients, models, or tables.

## Branches

- `refactor/clients` — the two clients. Done, under review.
- `feature/duckdb-ingestion` — DuckDB workspace + per-source ingestion.
  Plan: `docs/duckdb-ingestion-plan.md` (rebase onto `main` after the clients
  branch merges).
- `main` — old pipeline; being replaced.

## Layout

```
src/metabo_search/
├── transport.py    HttpClient: pooling, retries, Retry-After, rate limit
├── results.py      Results[T] (a list subclass) with .ids()/.records()/.fmt()
├── errors.py       MetaboSearchError hierarchy (incl. ParseError)
├── metabolights/   client.py · models.py · parse.py · files.py
└── workbench/      client.py · models.py · parse.py
```

## Commands

```bash
./scripts/install.sh                                        # venv + package + dev extras + skill link
uv pip install --python .venv-local/bin/python -e ".[dev]"  # manual
.venv-local/bin/python -m pytest tests/ -q                  # offline tests
.venv-local/bin/pyright                                     # strict (no mypy)
.venv-local/bin/ruff check src tests
```

## Conventions & gotchas

- **pyright + pydantic**: use keyword defaults `Field(default=..., alias=...)`,
  never positional `Field("", alias=...)`; and `Field(default=[])`, not
  `default_factory=list`, for `list[SomeModel]` fields (pyright infers
  `list[Unknown]` otherwise).
- **Dependency direction**: sources depend on the workspace, never the reverse.
  `metabolights/` and `workbench/` never import each other. Both are enforced by
  tests (`tests/test_isolation.py`).
- **`inspect()` is best-effort**: a study that fails after retries comes back as
  a `StudyDetail` with `.error` set and empty data; `on_error="raise"` opts into
  abort-on-first-failure.
- **No repository mirroring.** Search the web APIs, ingest only the hits you
  care about, analyse, fetch more on demand.
- **Workbench corpus** (`/ST/summary`) takes ~90 s; fetched once per client
  instance and filtered locally (the API has no free-text search).
- **Docs are deliberately lean** — prefer docstrings over new Markdown.

## Docs

- `README.md` — what it is + usage examples.
- `SKILL.md` — the agent-facing skill (pi/Claude/opencode).
- `docs/api-design.md` — target public API.
- `docs/rebuild-plan.md` — architecture and the client branch plan.
- `docs/duckdb-ingestion-plan.md` — the next branch.
