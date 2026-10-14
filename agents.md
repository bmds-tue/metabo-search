# metabo-search — maintenance notes

Search two metabolomics repositories from Python: **MetaboLights** and
**Metabolomics Workbench**. Two independent, strictly typed clients, plus a
local DuckDB working set built from search results. The repositories are
**never unified** — not in clients, models, or tables.

## Branches

- `main` — the current line: both clients, the DuckDB workspace, per-source
  ingestion, and the fetch/ingest hardening work. The `feature/duckdb-ingestion`
  and `perf/*` / `refactor/*` / `test/*` / `fix/*` branches are merged here.

## Layout

```
src/metabo_search/
├── transport.py    HttpClient: pooling, retries, Retry-After, rate limit
├── results.py      Results[T] (a list subclass) with .ids()/.records()/.fmt()
├── errors.py       MetaboSearchError hierarchy (incl. ParseError)
├── ingest.py       source-agnostic ingest mechanics (DDL, audit, skipped)
├── workspace.py    Workspace: DuckDB handle, IngestBatch/TableRows/IngestReport
├── metabolights/   client.py · models.py · parse.py · files.py · ingest.py
└── workbench/      client.py · models.py · parse.py · corpus.py · ingest.py
```

## Commands

```bash
./scripts/install.sh                                        # venv + package + dev extras + skill link
uv pip install --python .venv-local/bin/python -e ".[dev]"  # manual
.venv-local/bin/python -m pytest -q                         # offline tests
.venv-local/bin/python -m pytest tests/live -m "live and not live_slow" -q  # live (network)
.venv-local/bin/python -m pytest tests/live -m live_slow -q # + corpus cache (~95 s)
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
  only DB-touching part.
- **Shared mechanics live in `metabo_search/ingest.py`** (DDL rendering, row
  wrapping, the audit run, error collection) and import no source. Each source
  keeps only its spec and mapping.
- **Completeness is enforced** by `tests/test_ingest_completeness.py`: a
  sentinel builds each model with a marker per field and asserts every marker
  lands in a row, so a new upstream field fails the test instead of being
  silently dropped.
- **Promises live in `tests/contract.py`.** The offline suite (fixtures /
  `MockTransport`) and the live suite (`tests/live/`, real APIs) call the same
  assertion functions, so each documented promise has one definition. Live
  tests are marked `live` (and `live_slow` for the corpus) and are deselected
  by default; see `docs/ci.md`.
- **`ws.sql()` returns DuckDB's relation unwrapped** — no `Table` wrapper.
- **Ingestion bulk-loads via Arrow.** `Workspace.write` converts each table to
  a `pyarrow` table (schema from `DESCRIBE`), registers it, and merges with one
  set-based `ON CONFLICT`. Never use per-row `INSERT OR REPLACE`/`executemany`:
  it is O(n²) and OOMs at corpus scale. `duckdb` and `pyarrow` are the optional
  `ingest` extra, imported lazily: the clients and `import metabo_search` must
  not require them (`workspace.py` raises a message naming the extra).
  `pyarrow-stubs` is dev-only for strict typing.
- **Duplicate natural keys are deduped in Python (last wins) and reported** in
  `IngestReport.duplicates` — DuckDB's set-based `ON CONFLICT` picks an
  unspecified winner, so we never rely on it. Dedupe runs per Arrow chunk, so a
  key repeated across chunks is resolved by the upsert but not counted.
- **Workbench corpus** (`/ST/summary`) takes ~90 s and is filtered locally (the
  API has no free-text search). It is cached on disk (`~/.cache/metabo-search`,
  7-day TTL); `refresh_corpus()` forces a fetch and `cache_dir=None` disables it.
- **Docs are deliberately lean** — prefer docstrings over new Markdown.

## Docs

- `README.md` — what it is + usage examples.
- `SKILL.md` — the agent-facing skill (pi/Claude/opencode).
- `docs/api-design.md` — target public API.
- `docs/rebuild-plan.md` — architecture and the client branch plan.
- `docs/duckdb-ingestion-plan.md` — the ingestion schema and steps.
- `docs/ingestion-hardening-plan.md` — the fetch/ingest hardening plan.
- `docs/ingestion-hardening-plan.md` — **resume here**: fetch/ingest hardening
  plan (corpus cache, inspect parts, write-path fixes, branch/commit layout).
- `docs/ci.md` — CI tiers, the pinned live sample, and the promise matrix.
