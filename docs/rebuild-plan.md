# Rebuild plan — independent source clients + per-source DuckDB stores

Status: **complete** — both clients and the DuckDB ingestion work are merged
into `main`. Supersedes the `mtbls-wb` branch's "two repositories behind one
pipeline" design. The target public API is in `docs/api-design.md`.

## 1. Why the current approach failed

The `mtbls-wb` branch tried to unify MetaboLights and Metabolomics Workbench
**through a shared Python object model** (`StudyCandidate`) and a shared
operation interface (`StudyRepository`), then run both through one typed
pipeline (`core/`).

The two sources have different APIs and are not commensurable:

| Concern | MetaboLights | Metabolomics Workbench |
|---|---|---|
| Bulk index | paginated search API, 3 511 studies | one `/ST/summary` corpus, ~4 580 studies, ~90 s |
| Identity | `MTBLS…` | `ST…` / `AN…` |
| Organism vocab | `Homo sapiens` facet | Latin name + metstat common name (`Human`) |
| Tissue vocab | `blood plasma` / `blood serum` facets | SOURCE has only `Blood` |
| Disease | factor / free text | metstat `DISEASE` slot, canonical title-case |
| Metabolites | `m_*.tsv` MAF, true m×s matrix; 30 965-compound index | `/metabolites` list, `datatable`, `mwtab`; no MAF |
| Raw files | HTTP `FILES/` listing + download | no HTTPS raw endpoint (FTP only) |
| Sample evidence | ISA sample sheet + assay file map | `factors` rows |

Every difference became a conditional in shared code, a doc warning, or a
silent wrong answer (`has_maf` hard-failing every Workbench candidate; a
MetaboLights URL built for an `ST…` id; zero Workbench sample sentences).

**Conclusion: do not unify the sources.** Each source gets its own client, its
own models, its own DuckDB schema, and its own analysis helpers. The only
shared code is infrastructure (HTTP transport, a generic results container, the
exception base). Cross-source work is the user's own SQL over two databases,
never a library-provided join.

## 2. Target architecture

```
metabo_search/
├── transport.py                # shared: HttpClient, retries, rate limit, errors
├── results.py                  # shared: Results[T] (list subclass), Table
├── workspace.py                # shared: Workspace (dumb DuckDB handle)
├── metabolights/
│   ├── client.py               # MetaboLights live client (search + inspect)
│   ├── models.py               # pydantic models (MetaboLights only)
│   ├── files.py                # FILES walk, ISA/MAF download
│   ├── parse.py                # ISA-Tab text → typed records
│   └── ingest.py               # DDL + batch() + write() → ml_* tables
└── workbench/
    ├── client.py               # Workbench live client (search + inspect)
    ├── models.py               # pydantic models (Workbench only)
    ├── parse.py                # factors/analysis/metabolites normalization
    └── ingest.py               # DDL + batch() + write() → wb_* tables
```

Layering rules:

1. **One source per package.** No cross-package model imports. A test asserts
   that `metabolights/` never imports `workbench/` and vice versa.
2. **Sources depend on the workspace; the workspace never depends on sources.**
   `workspace.py` imports no source package. A test enforces this.
3. **Parsing is separate from transport.** `client.py` does HTTP + typed
   envelopes; `parse.py` turns payloads/ISA text into records.
4. **Ingestion is per-source and pure where it counts.** `ingest.batch(obj)`
   maps models → rows with no DB/network (unit-testable);
   `ingest.write(ws, obj)` ensures DDL and upserts. `ml.ingest(hits, ws)` is the
   ergonomic entry point.
5. **Search first, ingest the hits.** The web APIs are the search engine; the
   DuckDB `Workspace` is a working set built from search results and extended
   on demand. We never mirror a whole repository. No `core.*`, no merged view,
   no `repository` discriminator column.
6. **Analysis is SQL.** Discovery, faceting, cohort stats, metabolite search are
   per-source queries/views, not a shared pipeline.
7. **Deep data is on demand.** Shallow hits are ingested from search; sample/
   metabolite data is fetched per selected study via `inspect()` and added.

## 3. Phase 0 — Reset

```bash
git tag archive/mtbls-wb mtbls-wb     # escape hatch
git checkout main
git branch -D mtbls-wb
git checkout -b rebuild
```

- **Base: `main`.** It holds the proven MetaboLights code (ISA parsing, MAF
  heuristics, downloader). Port the Workbench endpoint client + vocab fixtures
  from `/tmp/metabo-search-mtbls-wb-reference/`; do **not** re-adopt
  `repositories/` or `core/`.
- Add `duckdb>=1.1`; keep `httpx`, `pydantic`, `rapidfuzz`.
- Delete from the critical path: `core/` (pipeline), `repositories/` (seam),
  `workflow.py`, `searcher.py`/`scorer.py`/`summarizer.py`, `sample_gen.py`.

## 4. Phase 1 — MetaboLights client (solid)

Spec: **`docs/api-design.md`** §6. Covers the core loop first (basic study
search, files/ISA/MAF, `inspect`); advanced search, the compound index, and
ontology/statistics are deferred. The API was verified against the live
OpenAPI spec (`ws3/openapi.json`); study-detail REST is gone, so study-level
data comes from the index and deep data from ISA/MAF.

Robustness checklist (both clients): pooled keep-alive client, `User-Agent`,
separate timeouts; retries on 5xx/timeouts/resets with backoff+jitter; honour
`Retry-After` on 429; token-bucket rate limit; typed error taxonomy; every
response validated into a typed record; raw payload handed to a sink;
deterministic pagination; offline record/replay fixtures.

## 5. Phase 2 — Workbench client (solid)

Spec: **`docs/api-design.md`** §6. Ports the proven endpoint client + parsers
from `/tmp/metabo-search-mtbls-wb-reference/workbench/` (endpoint builders,
retry, corpus maps) but keeps the models Workbench-only. Known traps encoded as
tests: `/metabolites` empty list ⇒ count unknown (never 0); packed factor
strings; row-index-keyed species map.

## 6. Phase 3 — Per-source DuckDB stores

Two separate files, two schemas, no merged views. DDL sketch:

One `Workspace` file can hold both sources side by side, in separate tables
(`ml_*` / `wb_*`). There is no merged view.

**MetaboLights tables** (written by `ml.ingest(StudyHit)` / `ml.ingest(StudyDetail)`):

```
ml_study         study_id PK, title, description, status, curation_request,
                 submission_date, public_release_date, modified_time,
                 sample_count, assay_count, raw_file_count, derived_file_count,
                 size_in_bytes, investigation_file_path, sample_file_path
ml_study_term    study_id, facet, term, source_ref, accession
ml_study_factor_pair / ml_study_lineage / ml_study_annotation
ml_assay / ml_assay_file / ml_assay_technique / ml_publication / ml_protocol
ml_protocol_component / ml_protocol_parameter
ml_contact / ml_contact_role / ml_submitter / ml_submitter_role / ml_funding
ml_file          study_id, path, name, directory, extension, size_bytes, category
ml_sample / ml_sample_characteristic / ml_sample_factor / ml_sample_file
ml_metabolite / ml_metabolite_abundance
ml_ingest_run
```

**Workbench tables** (written by `wb.ingest(StudySummary)` / `wb.ingest(StudyDetail)`):

```
wb_study         study_id PK, study_title, species, institute, analysis_type,
                 number_of_samples, submission_date, release_date, version,
                 revision_no, revision_datetime, revision_comment, license,
                 license_url, study_url
wb_sample / wb_sample_factor
wb_analysis
wb_metabolite
wb_ingest_run
```

Ingestion:

- `ml.ingest(hits, ws)` / `wb.ingest(summaries, ws)` delegate to the source's
  `ingest.write(ws, obj)`; `Workspace.write(batch)` performs the upsert.
- Writes are **idempotent** (upsert on natural keys) and **incremental**:
  re-adding the same hits changes nothing; adding `StudyDetail` enriches rows
  already ingested from `StudyHit`.
- There is **no** `update()` that ingests a repository. You add what you
  searched for.
- DuckDB is single-writer: `inspect()` parallelizes fetches; writes happen on
  one thread.
- Every ingest records an `*_ingest_run` row (source, table, row count, time).
- **Transparency:** a contract test asserts the mapping is total (every modeled
  field lands in a column or child table); `IngestReport`, `ws.tables()` and
  `ws.schema()` make the result visible. No `raw_*` tables for now.

## 7. Phase 4 — Analysis over the workspace

Analysis runs on whatever has been ingested. SQL is the primary tool
(`ws.sql()`); typed convenience helpers are added per source as needed.

- MetaboLights vocabulary: `organism`, `sample_type`, `technique`,
  `min_samples`, `metabolite`, …
- Workbench vocabulary: `species`, `source`, `disease`, `analysis_type`,
  `min_samples`, `metabolite`, …
- No cross-source join is provided; both sources in one file can be joined by
  the user in their own SQL (their vocabulary mapping, their call).

## 8. Phase 5 — Skill integration

Rewrite `SKILL.md` around the loop: search the source's web API, `ml.ingest()`
the hits, analyse with SQL, `inspect()` selected studies and ingest them,
repeat. Per-source vocabulary; no "one flow across both".

## 9. Salvage vs discard

| Keep (port/adapt) | Discard |
|---|---|
| `inspector.py` ISA parsing → `metabolights/parse.py` | `repositories/base.py` seam |
| `maf.py` heuristics → `metabolights/parse.py` | `repositories/metabolights.py` adapter |
| `downloader.py` FILES walk + retry → `metabolights/files.py` | `repositories/workbench/*` search/slots/inspect |
| `client.py` v2 search wrapper → `metabolights/client.py` | `core/` typed pipeline |
| workbench `client.py` + `corpora.py` + `vocab.py` + `matcher.py` + fixtures | shared `StudyCandidate` as universal model |
| real-world test fixtures (`tests/fixtures`) | `searcher`/`scorer`/`summarizer` orchestration |
| retry/keep-alive/process-parse measurements (`docs/perf.md`) | per-step JSON cache (`core/cache.py`) |
| | `sample_gen.py` per-sample sentences |

## 10. Testing strategy

- **Clients**: unit tests against recorded fixtures; retry/rate-limit tests with
  a monkeypatched transport; live smoke gated by an env flag.
- **Parsing**: reuse real-world fixtures (MTBLS719/1375/78/640/1333) and add
  Workbench factor/analysis/metabolite fixtures.
- **Workspace**: add fixtures to a temp DuckDB; assert schema, row counts,
  referential integrity; assert **re-adding is idempotent**; assert adding a
  `StudyDetail` enriches existing `StudyHit` rows without duplication.
- **Analysis**: golden SQL results per source.
- **Isolation test**: assert `metabolights/` and `workbench/` do not import each
  other.
- **Live end-to-end** (opt-in): counts against known totals (≈3 511 ML, ≈4 580 WB).

## 11. Decisions (resolved)

1. **Base branch** — `rebuild` from `main`; workbench reference saved in `/tmp`. ✅
2. **Typed records** — pydantic v2, validated as statically as possible. ✅
3. **Search first** — never mirror a repository. Search the web API, ingest the
   hits you care about, analyse, fetch more on demand. ✅
4. **Metabolite abundances** — full m×s matrix in DuckDB (long table). ✅
5. **CLI** — none; library functions only. ✅
6. **`sample_gen`** — dropped. ✅
7. **No unification** — independent clients, models, schemas, and queries. ✅
8. **Ingestion** — per-source `ingest.py`: pure `batch()` + generic
   `Workspace.write()`; `ml.ingest(hits, ws)` primary. Sources → workspace only. ✅
9. **Transparency** — total mapping enforced by tests; `IngestReport`,
   `ws.tables()`, `ws.schema()`, `*_ingest_run`; no `raw_*` tables now. ✅
10. **`Results[T]`** — a `list` subclass. ✅
11. **Workspace path** — `./metabo.duckdb` default, no env override, path may
    be passed. ✅

## 12. Execution — two branches, small commits

Branch 1 `refactor/clients` starts from `main`; branch 2
`feature/duckdb-ingestion` starts after branch 1 merges. Every commit leaves
the tree green (pytest, ruff, pyright). No mypy. `uv` is the package manager.

### Branch 1 — `refactor/clients` (no DuckDB)

| # | Commit | What |
|---|---|---|
| C0 | `docs(readme): explain the client rebuild` | root `README.md`: what it is + examples + why the rebuild |
| C1 | `chore: typing + lint gates` | `py.typed`, pyright strict config, ruff, dev deps |
| C2 | `feat(transport): shared HTTP client + errors` | pool, retries, `Retry-After`, rate limit, raw sink, error taxonomy |
| C3 | `feat(results): Results list subclass + Table` | `results.py` |
| C4 | `feat(metabolights): study-index models` | pydantic models for the search document |
| C5 | `feat(metabolights): search/study/studies` | `metabolights/client.py` |
| C6 | `feat(metabolights): ISA + MAF parsers` | port `inspector.py`/`maf.py` → `metabolights/parse.py` |
| C7 | `feat(metabolights): files() + download()` | port `downloader.py` → `metabolights/files.py` |
| C8 | `feat(metabolights): inspect() -> StudyDetail` | compose download + parse |
| C9 | `feat(workbench): models + parse` | factors, empty `/metabolites`, flat `/metstat` |
| C10 | `feat(workbench): search/study` | corpus-backed search |
| C11 | `feat(workbench): samples/analyses/metabolites/inspect` | deep methods |
| C12 | `test: contract + isolation` | OpenAPI contract; no cross-package imports |
| C13 | `docs: clients reference` | lean; rely on docstrings |
| C14 | `refactor!: remove legacy pipeline/seam` | delete `core/`, `repositories/`, old modules + tests; rewrite `__init__.py` |
| C15 | `chore: tidy deps` | drop unused; ship `py.typed` + DDL |

### Branch 2 — `feature/duckdb-ingestion`

| # | Commit | What |
|---|---|---|
| D0 | `chore(deps): add duckdb` | dependency only |
| D1 | `feat(workspace): Workspace handle` | `write(IngestBatch)`, `sql`, `tables`, `schema`, `export` |
| D2 | `feat(metabolights): ingest mapping` | DDL + pure `batch()` + `write()` |
| D3 | `feat(workbench): ingest mapping` | DDL + pure `batch()` + `write()` |
| D4 | `feat(metabolights): ml.ingest()` | idempotent round trip |
| D5 | `feat(workbench): wb.ingest()` | idempotent round trip |
| D6 | `test: ingestion completeness` | every modeled field lands in a column or child table |
| D7 | `feat(workspace): analysis helpers` | `cohort`, `facets`, readers, `export` |
| D8 | `docs: ingestion + analysis` | update docs + `SKILL.md` around the loop |
| D9 | `chore: ingestion run log` | `*_ingest_run` audit rows in reports |

### Testing (every commit)

- **Offline by default**; live conformance is a separate marked tier
  (`-m live`; see `docs/ci.md`).
- **Transport:** `httpx.MockTransport` for retries, `Retry-After`, rate limit,
  4xx no-retry, error mapping.
- **Models:** fixture validation + contract test vs the vendored OpenAPI spec
  (MetaboLights) or pinned fixtures (Workbench).
- **Parsers:** real-world ISA fixtures (MTBLS719/1375/78/640/1333) + MAF cases.
- **Ingestion:** pure `batch()` tests (no DB), DuckDB round trip, idempotency,
  completeness.
- **Isolation:** `workspace.py` imports no source package; `metabolights/` and
  `workbench/` never import each other.
- **Static:** `ruff` + `pyright` strict; `py.typed` shipped.
