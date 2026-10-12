# metabo-search — maintenance notes

Search two metabolomics repositories from Python: **MetaboLights** and
**Metabolomics Workbench**. Two independent, strictly typed clients, plus a
local DuckDB working set built from search results. The repositories are
**never unified** — not in clients, models, or tables.

## Branches

- `feature/duckdb-ingestion` — DuckDB workspace + per-source ingestion. Done;
  plan in `docs/duckdb-ingestion-plan.md`.
- `main` — old pipeline; being replaced (merge the clients + ingestion work).

## Layout

```
src/metabo_search/
├── transport.py    HttpClient: pooling, retries, Retry-After, rate limit
├── results.py      Results[T] (a list subclass) with .ids()/.records()/.fmt()
├── errors.py       MetaboSearchError hierarchy (incl. ParseError)
├── workspace.py    Workspace: DuckDB handle, IngestBatch/TableRows/IngestReport
├── metabolights/   client.py · models.py · parse.py · files.py · ingest.py
└── workbench/      client.py · models.py · parse.py · ingest.py
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
- **Ingestion schema is generated from one spec.** Each `ingest.py` declares
  `_TABLES` (column → type, natural key); `DDL` is built from it, so tables and
  primary keys cannot drift. `batch()` is pure (no DB/network); `write()` is the
  only DB-touching part. `INSERT OR REPLACE` on the natural key makes re-ingest
  a no-op.
- **Completeness is enforced** by `tests/test_ingest_completeness.py`: every
  model field must be a column or declared in `_EXPANDED`, so a new upstream
  field fails the test instead of being silently dropped.
- **`ws.sql()` returns DuckDB's relation unwrapped** — no `Table` wrapper.
- **Ingestion bulk-loads via Arrow.** `Workspace.write` converts each table to
  a `pyarrow` table (schema from `DESCRIBE`), registers it, and merges with one
  set-based `ON CONFLICT`. Never use per-row `INSERT OR REPLACE`/`executemany`:
  it is O(n²) and OOMs at corpus scale. `pyarrow` is a core dep;
  `pyarrow-stubs` is dev-only for strict typing.
- **Duplicate natural keys are deduped in Python (last wins) and reported** in
  `IngestReport.duplicates` — DuckDB's set-based `ON CONFLICT` picks an
  unspecified winner, so we never rely on it.
- **Workbench corpus** (`/ST/summary`) takes ~90 s; fetched once per client
  instance and filtered locally (the API has no free-text search).
- **Docs are deliberately lean** — prefer docstrings over new Markdown.

## Docs

- `README.md` — what it is + usage examples.
- `SKILL.md` — the agent-facing skill (pi/Claude/opencode).
- `docs/api-design.md` — target public API.
- `docs/rebuild-plan.md` — architecture and the client branch plan.
- `docs/duckdb-ingestion-plan.md` — the ingestion branch schema and steps.
