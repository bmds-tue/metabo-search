# DuckDB ingestion plan — search → ingest → analyse

Status: ready to implement. Branch `feature/duckdb-ingestion`, rebased onto
`main` (the `refactor/clients` branch is merged).

This document is self-contained: a fresh session should be able to implement it
without re-deriving the clients. Read it fully, then follow §8.

---

## 1. Context

`metabo-search` searches two metabolomics repositories from Python:
**MetaboLights** and **Metabolomics Workbench**. They have different APIs,
vocabularies, and data models and are **never unified** — not in clients, not in
models, not in the database. The two clients are merged into `main`; this
branch adds a local DuckDB **working set**.

**The loop** (this is the whole product):

```
search (web API) ──► typed results ──► ml.ingest(hits, ws) ──► DuckDB
     ▲                                                             │
     │                                                             ▼
     └──── inspect deeper / search again ─── analyse (SQL) ◄────────┘
```

We do **not** mirror the repositories. You search, ingest only the hits you care
about, analyse, and fetch more on demand.

**Hard rules** (already enforced for the clients; keep them):
1. Sources depend on the workspace; the workspace never depends on sources.
   `workspace.py` imports no source package. A test enforces this.
2. `metabolights/` and `workbench/` never import each other. A test enforces this.
3. Ingestion is a per-source module with a **pure** mapping function
   (`batch(obj) -> IngestBatch`, no DB/network) plus a `write(ws, obj)`.
4. The mapping must be **total**: every field of every ingested model lands in a
   column or a child table. A test enforces this (no silent data loss).
5. Keep code simple; assert pre/post-conditions the type signature cannot express.

### What exists today

```
src/metabo_search/
├── transport.py     HttpClient: pooling, retries, Retry-After, rate limit, raw sink
├── results.py       Results[T] (a list subclass) with .ids()/.records()/.fmt()
├── errors.py        MetaboSearchError hierarchy
├── metabolights/    client.py · models.py · parse.py · files.py
└── workbench/       client.py · models.py · parse.py
```

Public surface today: `from metabo_search import MetaboLights, Workbench`.

Client behaviour worth knowing for ingestion:
- `inspect()` is **best-effort**: a study that fails (network, or no ISA
  metadata on the mirror, or a malformed file → `ParseError`) comes back as a
  `StudyDetail` with a non-empty `.error` and empty data. Ingest must **skip**
  errored details. `on_error="raise"` opts into abort-on-first-failure.
- `search()` pages deterministically (default sort `studyId asc`).
- `errors.py` has `ParseError(MetaboSearchError)` for malformed data files.

### Environment

- Package manager: **uv**. Venv: `.venv-local`.
  Reinstall: `uv pip install --python .venv-local/bin/python -e ".[dev]"`.
- Tests: `.venv-local/bin/python -m pytest tests/ -q` (currently 75, all offline).
- Types: `.venv-local/bin/pyright` (strict; config already points at `.venv-local`).
  **No mypy.** Lint: `.venv-local/bin/ruff check src tests`.
- Docs are deliberately lean. Use Google-style docstrings + inline comments for
  non-obvious steps; do not add large new docs beyond this plan and a README touch.

---

## 2. Goal / non-goals

**Goal.** Add a local DuckDB workspace with transparent, idempotent, per-source
ingestion and a small analysis surface.

**Non-goals.** No repository mirroring, no bulk bootstrap, no cross-source
tables/views, no CLI, no raw-JSON audit tables (add later only if needed), no
per-sample sentence generation.

---

## 3. Interfaces to build

### 3.1 `workspace.py` (new, source-agnostic)

```python
@dataclass(frozen=True)
class TableRows:
    name: str
    columns: tuple[str, ...]
    rows: list[dict[str, object]]
    keys: tuple[str, ...]          # natural key (also the DDL PRIMARY KEY)

@dataclass(frozen=True)
class IngestBatch:
    ddl: str                        # CREATE TABLE IF NOT EXISTS ... for this source
    tables: list[TableRows]

@dataclass(frozen=True)
class IngestReport:
    tables: dict[str, int]          # table -> rows written
    total: int

class Workspace:
    def __init__(self, path: str | Path = "metabo.duckdb") -> None: ...
    def write(self, batch: IngestBatch) -> IngestReport: ...
    def sql(self, query: str, params: Sequence[object] | None = None) -> Table: ...
    def tables(self) -> list[str]: ...
    def schema(self, table: str | None = None) -> str: ...
    def export(self, query: str, dest: str | Path, *, format: str = "parquet") -> Path: ...
    def close(self) -> None: ...    # + context manager
```

`Table` (SQL result) is an Arrow-backed wrapper: `.records() -> list[dict]`,
`.df()`, `.arrow()`, `.fmt()`, `__len__`. It can live in `workspace.py` (it needs
`duckdb`/`pyarrow`) or `results.py` — keep `results.py` dependency-free, so put
`Table` in `workspace.py`.

**`Workspace.write`** must be idempotent: execute `batch.ddl`, then
`INSERT OR REPLACE INTO <name> (<columns>) VALUES (?, …)` via `executemany` for
each `TableRows` (the DDL declares `PRIMARY KEY (keys)`). Verify DuckDB's
`INSERT OR REPLACE` honours the PK; if not, fall back to
`DELETE FROM <name> WHERE (keys) IN (…)` then `INSERT`. Return per-table counts.

### 3.2 Per-source ingestion (`metabolights/ingest.py`, `workbench/ingest.py`)

```python
DDL: str                                   # CREATE TABLE IF NOT EXISTS ...

def batch(obj: object) -> IngestBatch: ...  # PURE: models -> rows, no DB
def write(ws: Workspace, obj: object) -> IngestReport: ...  # ws.write(batch(obj))
```

`batch(obj)` dispatches on the pydantic model type and is unit-tested with **no
DB**. It must accept:
- MetaboLights: `StudyHit`, `StudyDetail`, `Results[StudyHit]`, `Results[StudyDetail]`.
- Workbench: `StudySummary`, `StudyDetail`, `Results[...]` of either.

For a sequence, merge into one batch (same DDL, tables concatenated).

Skip `StudyDetail` entries whose `.error` is non-empty (best-effort inspect);
record them in the `IngestReport` if convenient, but never write empty data.

### 3.3 Client convenience

```python
ml.ingest(obj, ws) -> IngestReport      # delegates to metabolights.ingest.write
wb.ingest(obj, ws) -> IngestReport      # delegates to workbench.ingest.write
```

`client.py` may import its own `ingest.py` (source → workspace direction is fine).
No circular import: `workspace.py` imports no source.

### 3.4 Analysis helpers (small; `sql()` is the main tool)

Add per-source helpers only where they pay off, e.g.
`metabolights/analysis.py`: `facets(ws, facet)`, `cohort(ws, **filters)`.
Return `Table` or typed rows. Do not build a query DSL.

---

## 4. Model inventory (fields the schema must cover)

Full definitions: `src/metabo_search/metabolights/models.py`,
`src/metabo_search/workbench/models.py`. Natural keys in parentheses.

**MetaboLights**
- `StudyHit`: study_id (PK), title, description, status, study_category,
  curation_request, submission_date, public_release_date, modified_time,
  sample_count, assay_count, raw_file_count, derived_file_count, size_in_bytes,
  size_in_text, investigation_file_path, sample_file_path.
  Child collections: organisms, organism_parts, sample_types, technology_types,
  design_descriptors, factors, variants (`OntologyTerm`); annotations,
  curator_annotations, parse_errors (str); organism_lineage (`OrganismLineage`);
  raw_file_extensions, derived_file_extensions (str); assays (`Assay`);
  assay_techniques (`AssayTechnique`); factor_pairs (`FactorPair`);
  publications (`Publication`); protocols (`Protocol`); contacts (`Contact`);
  submitters (`Submitter`); fundings (`Funding`).
- `OntologyTerm`: term, term_source_ref, term_accession_number.
- `Assay`: file_path, technology_platform, measurement_type (OntologyTerm),
  technology_type (OntologyTerm), technique (AssayTechnique), assignment_files.
- `AssayTechnique`: name, main, sub, technique.
- `FactorPair`: name, value.
- `Publication`: title, author_list, pubmed_id, doi, status (OntologyTerm).
- `Protocol`: name, description, uri, version, protocol_type (OntologyTerm),
  components (`ProtocolComponent`: name, type, term_source_ref,
  term_accession_number), parameters (`ProtocolParameter`: term,
  term_source_ref, term_accession_number).
- `Contact`: first_name, last_name, mid_initials, email, affiliation, address,
  orcid, roles (`OntologyTerm`).
- `Submitter`: fullname, email, affiliation, address, country, orcid, roles (str).
- `Funding`: fund_ref_id, funder, grant_identifier.
- `OrganismLineage`: domain, kingdom, phylum, klass, order, family, genus, species.
- `Sample`: sample_name, sample_row, source_name, organism, organism_part,
  sample_type, characteristics (dict), factor_values (dict), raw_files,
  derived_files. `sample_row` is the 0-based row index within the sample sheet;
  it disambiguates the reused `Sample Name` labels (see §5).
- `Metabolite`: study_id, file, row, database_identifier,
  metabolite_identification, chemical_formula, mass_to_charge, retention_time,
  abundances (dict sample→float).
- `StudyFile`: path, name, directory, extension, size_bytes, category.
- `StudyDetail`: study_id, samples, metabolites, files, error.

**Workbench**
- `StudySummary`: study_id (PK), study_title, species, institute, analysis_type,
  number_of_samples, submission_date, release_date, version, revision_no,
  revision_datetime, revision_comment, license, license_url, study_url.
- `SampleRow`: study_id, local_sample_id, mb_sample_id, sample_source, factors
  (packed str), raw_data, factor_values (dict).
- `AnalysisRecord`: study_id, analysis_id, analysis_summary, analysis_type,
  chromatography_system, column_name, chromatography_type, ms_instrument_name,
  ms_instrument_type, ms_type, ion_mode, nmr_instrument_type,
  nmr_experiment_type, spectrometer_frequency, nmr_solvent, units.
- `MetaboliteRow`: study_id, analysis_id, analysis_summary, metabolite_name,
  refmet_name.
- `StudyDetail`: study_id, summary, samples, analyses, metabolites, error.

---

## 5. Schema

Per source, table names prefixed `ml_` / `wb_`. No shared tables.

**MetaboLights**

| Table | Natural key | Notes |
|---|---|---|
| `ml_study` | study_id | scalars from `StudyHit` |
| `ml_study_term` | (study_id, facet, term) | facet ∈ organism/organism_part/sample_type/technology_type/design_descriptor/factor/variant |
| `ml_study_lineage` | (study_id, rank, term) | |
| `ml_study_factor_pair` | (study_id, name, value) | |
| `ml_study_annotation` | (study_id, kind, ordinal) | kind ∈ annotation/curator/parse_error; value text |
| `ml_assay` | (study_id, assay_index) | measurement/technology type flattened + technique |
| `ml_assay_file` | (study_id, assay_index, path) | from `assignment_files` |
| `ml_assay_technique` | (study_id, ordinal, name) | |
| `ml_publication` | (study_id, ordinal, doi) | status flattened |
| `ml_protocol` | (study_id, ordinal) | |
| `ml_protocol_component` | (study_id, protocol_ordinal, ordinal) | |
| `ml_protocol_parameter` | (study_id, protocol_ordinal, ordinal) | |
| `ml_contact` | (study_id, ordinal) | |
| `ml_contact_role` | (study_id, contact_ordinal, term) | |
| `ml_submitter` | (study_id, ordinal) | |
| `ml_submitter_role` | (study_id, submitter_ordinal, role) | |
| `ml_funding` | (study_id, ordinal) | |
| `ml_sample` | (study_id, sample_name, sample_row) | sample_row disambiguates reused names |
| `ml_sample_characteristic` | (study_id, sample_name, sample_row, key) | from `characteristics` |
| `ml_sample_factor` | (study_id, sample_name, sample_row, name) | from `factor_values` |
| `ml_sample_file` | (study_id, sample_name, sample_row, kind, path) | kind ∈ raw/derived |
| `ml_metabolite` | (study_id, file, row) | |
| `ml_metabolite_abundance` | (study_id, file, row, sample) | from `abundances` |
| `ml_file` | (study_id, path) | |
| `ml_ingest_run` | run_id | audit: source, table, rows, at |

**Workbench**

| Table | Natural key | Notes |
|---|---|---|
| `wb_study` | study_id | from `StudySummary` |
| `wb_sample` | (study_id, local_sample_id, mb_sample_id) | |
| `wb_sample_factor` | (study_id, local_sample_id, name) | from `factor_values` |
| `wb_analysis` | (study_id, analysis_id) | |
| `wb_metabolite` | (study_id, analysis_id, metabolite_name, refmet_name) | |
| `wb_ingest_run` | run_id | audit |

Every DDL declares `PRIMARY KEY (…)` matching the natural key, so
`INSERT OR REPLACE` is idempotent.

---

## 6. Idempotency & transparency

- Re-ingesting the same object changes nothing (PK upsert).
- Ingesting a `StudyDetail` after a `StudyHit` enriches existing rows.
- Every `ingest` returns an `IngestReport` (table → row count) and writes an
  `*_ingest_run` row (source, table, rows, timestamp).
- `ws.tables()` / `ws.schema()` show exactly what exists; the user can query it.
- Completeness is enforced by a test (§9), not by hope.

---

## 7. Public API additions

```python
from metabo_search import MetaboLights, Workbench, Workspace

ws = Workspace("project.duckdb")        # default ./metabo.duckdb
ml = MetaboLights()

hits = ml.search("human plasma lipidomics", organisms=["Homo sapiens"], limit=100)
ml.ingest(hits, ws)                      # study-level
ml.ingest(ml.inspect(hits[:10]), ws)     # deep: samples + metabolites

ws.sql("select facet, count(*) from ml_study_term group by 1 order by 2 desc")
ws.tables(); ws.schema()
ws.export("select * from ml_study", "studies.parquet")
```

Export `Workspace` from the top-level `metabo_search.__init__` (update `__all__`
to `["MetaboLights", "Workbench", "Workspace"]`; update the isolation test's
expected set).

---

## 8. Steps (small commits; each green)

| # | Commit | Content |
|---|---|---|
| D0 | `chore(deps): add duckdb` | add `duckdb>=1.1`; reinstall; import smoke |
| D1 | `feat(workspace): DuckDB workspace handle` | `workspace.py`: `TableRows`, `IngestBatch`, `IngestReport`, `Workspace`, `Table`; tests |
| D2 | `feat(metabolights): ingestion mapping` | `metabolights/ingest.py` DDL + pure `batch()` + `write()`; pure tests |
| D3 | `feat(workbench): ingestion mapping` | `workbench/ingest.py`; pure tests |
| D4 | `feat(metabolights): ml.ingest()` | client method; round-trip + idempotency tests |
| D5 | `feat(workbench): wb.ingest()` | client method; round-trip + idempotency tests |
| D6 | `test: ingestion completeness + isolation` | every model field mapped; `workspace.py` imports no source |
| D7 | `feat(analysis): small per-source helpers` | `facets`/`cohort` (or skip if `sql()` suffices); tests |
| D8 | `docs: ingestion + README` | short README section; update `SKILL.md` |
| D9 | `chore: ingest run audit` | `*_ingest_run` rows surfaced in reports |

After each commit: `pyright` (0 errors), `ruff` clean, `pytest` green.

---

## 9. Testing

- **Pure mapping**: `batch(obj)` tests with no DB, asserting table names, row
  counts, and a few field values (including dicts like `characteristics`,
  `factor_values`, `abundances`).
- **Round trip**: write a fixture object into a temp DuckDB, then `sql()` and
  assert rows.
- **Idempotency**: write twice → identical counts; `StudyHit` then `StudyDetail`
  → enrichment, no duplicates.
- **Completeness**: for each ingested model, assert every `model_fields` name
  appears in the union of the batch's columns (directly or in a named child
  table). This is the no-silent-loss guarantee.
- **Isolation**: AST-scan `workspace.py` (and `analysis.py`) for imports of
  `metabo_search.metabolights` / `metabo_search.workbench` — must be none.
- **Error handling**: a `StudyDetail` with `.error` set is skipped, not written.
- All tests offline; fixtures already exist under `tests/fixtures/`.

---

## 10. Gotchas & conventions

- **pyright + pydantic quirks** (already hit on the clients branch):
  - use keyword defaults `Field(default=..., alias=...)`, never positional
    `Field("", alias=...)`;
  - for `list[SomeModel]` fields use `Field(default=[])`, not
    `default_factory=list` (pyright infers `list[Unknown]`).
- **DuckDB is single-writer.** Fetch in parallel; write on one thread.
- **`Workspace()` defaults to `./metabo.duckdb`**; a path may be passed; no env
  override.
- **`INSERT OR REPLACE` needs a PK** in the DDL. Composite natural keys are fine.
- **`Metabolite.abundances` / `Sample.characteristics` / `SampleRow.factor_values`
  are dicts** → child tables (long format), not JSON columns.
- **`StudyDetail.error`**: skip errored studies during ingest. It is set for
  network failures, for studies with no ISA metadata on the mirror, and for
  `ParseError` (malformed data files) — all collected per study, never raised.
- **`ml.ingest`/`wb.ingest` are conveniences**; the canonical call is
  `<source>.ingest.write(ws, obj)`. Keep `batch()` pure and DB-free.
- **`DownloadReport`** is not ingested (files are already `StudyFile` rows via
  `ml.files` → ingest `Results[StudyFile]`).
- **No `raw_*` tables** for now.

---

## 11. Definition of done

- `from metabo_search import MetaboLights, Workbench, Workspace` works.
- `ml.ingest(hits, ws)` and `wb.ingest(summaries, ws)` round-trip into a queryable
  DuckDB; re-ingest is a no-op.
- `ws.tables()` / `ws.schema()` show the full schema; `IngestReport` reports
  counts; `*_ingest_run` rows exist.
- Completeness + isolation tests pass; `pyright` strict clean; `ruff` clean;
  all tests offline and green.
- `workspace.py` imports no source package.
