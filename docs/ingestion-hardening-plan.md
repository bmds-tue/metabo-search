# Ingestion hardening — plan

Status: proposed. Fixes the scale failures found on a real 16 GiB machine. The
mapping layer (`batch()`) is sound; the problems are concentrated in
`Workspace.write`, `MetaboLights.inspect`, and two small `Workspace` helpers.
This is a focused rework of `Workspace.write`, not of the ingestion model.

## 0. Validation (done)

Measured on duckdb 1.5.6, 512 MiB memory cap, composite primary key:

| Load path | 200k rows | 500k rows |
|---|---|---|
| `executemany("INSERT OR REPLACE …")` | > 180 s (timeout) | OOM |
| staging + `executemany` + `ON CONFLICT` | ~47 s | — |
| **Arrow + `ON CONFLICT`** | **0.2 s** | **0.8 s** |

Findings: the culprit is per-row conflict resolution, **not**
`preserve_insertion_order` (47 s vs 50 s). Arrow is 300x faster than staging
and removes `executemany` from the hot path. `ON CONFLICT … DO UPDATE` works
with composite keys; all-key tables need `DO NOTHING`. DuckDB's set-based
`ON CONFLICT` does not define which of two same-key *input* rows survives, so
we dedupe in Python (last wins) — see §3.

Conclusion: the fix is **Arrow-based bulk load**, not staging.

## 1. `Workspace.write`: Arrow bulk load (P0, critical) — done

**Problem.** `executemany("INSERT OR REPLACE …")` row-by-row with composite
PKs blows the default 12.7 GiB limit on a ~121k-row shallow ingest. No tuning
hook.

**Fix (fundamental, one method).** Convert each table's rows to an Arrow table
using the target's own schema (from `DESCRIBE`), register it as a view, and do
one set-based `INSERT INTO <target> SELECT * FROM <incoming> ON CONFLICT
(<keys>) DO UPDATE SET …` (or `DO NOTHING` when the table is all keys). Chunked
at 250k rows. No staging table, no `executemany`.

Adds **`pyarrow` as a core dependency** (it is also what DuckDB's `.pl()`
needs) and `pyarrow-stubs` for strict typing. Also:
- `Workspace(path, *, memory_limit=None, threads=None, preserve_insertion_order=None)`
  — a real tuning hook; no reaching into `ws._con`.
- Whole batch stays in one transaction.

## 2. `MetaboLights.inspect`: samples-only mode (P0, critical)

**Problem.** `inspect()` always lists, downloads, and parses `m_*.tsv`. Five
human studies produced 1.2M abundance rows; the whole corpus is a memory bomb.
No way to ask for samples only.

**Fix (surgical).**
- `isa_files(http, study_id, *, include_metabolites=True)` — skip `m_` files
  before download when false.
- `MetaboLights.inspect(..., include_metabolites=True)` — default preserves
  today's behaviour; `include_metabolites=False` never fetches or parses MAFs,
  so `StudyDetail.metabolites` is empty and ingest writes no abundance rows.
  This is the supported "samples only" path (no `model_copy` hacks).

Streaming `inspect` (iterator instead of a materialised list) is **out of
scope**; chunking the call remains the user's lever.

## 3. Duplicate natural keys must be visible (P1)

**Problem.** `INSERT OR REPLACE` silently collapses repeated keys
(`ml_sample (study_id, sample_name)`, `wb_sample (study_id, local_sample_id,
mb_sample_id)`), undercounting with no warning.

**Fix (surgical).** `Workspace.write` already has `TableRows.keys`. Compute,
per table, `len(rows) - len(distinct keys)` for the incoming batch and return it
as `IngestReport.duplicates: dict[str, int]`. Default is report-only (some
tables legitimately dedupe); add an optional strict flag later if needed.
Cross-batch re-ingest stays idempotent and is not counted.

## 4. `tables()` should list base tables only (P1, trivial)

**Problem.** `CREATE OR REPLACE TEMP VIEW ml_human …` makes `ws.tables()`
list `ml_human`. `tables()` is not "tables".

**Fix.** Query `duckdb_tables() WHERE schema_name = 'main'` (same catalog
`schema()` already uses). Excludes temp views/tables.

## 5. API changes at a glance

```python
Workspace(path, *, memory_limit=None, threads=None, preserve_insertion_order=False)
Workspace.write(batch) -> IngestReport          # set-based, chunked, one txn
IngestReport(tables, total, skipped, duplicates)  # + duplicates

MetaboLights.inspect(..., include_metabolites=True)
isa_files(http, study_id, *, include_metabolites=True)
```

## 6. Tests

- **Memory/scale**: load a composite-PK table at a low `memory_limit` via
  `Workspace.write`; assert it completes and upserts correctly. This is the
  regression guard for #1.
- **Upsert semantics**: existing idempotency tests must still pass; add a
  duplicate-keys case asserting `IngestReport.duplicates`.
- **Samples-only inspect**: mock transport asserts no `m_*.tsv` request is
  made and `StudyDetail.metabolites == []`.
- **`tables()`**: create a temp view, assert it is not listed.

## 7. Non-goals

- No streaming/iterator `inspect` (chunk at the call site for now).
- No deletion/pruning of children removed upstream (upsert-only stays).
- No change to the pure `batch()` mapping or the per-source schema.
- No dataframe/Arrow layer.

## 8. Order

1. Validation gate (§0).
2. #1 `Workspace.write` rework + tuning + scale test (unblocks the corpus).
3. #2 `inspect` samples-only.
4. #4 `tables()` (trivial, do with #1).
5. #3 duplicate reporting (needs #1's shape).
