# Fetch & ingestion hardening — record

Status: **implemented** — all five branches are merged into `main`. Kept as the
record of the fetch/ingest hardening work. Every item has a symptom, evidence,
fix, and acceptance criteria.

## 0. Status

All five branches are merged into `main`; nothing here is pending. This section
is kept only as the verification recipe and context for the landed work.

- **Already landed** (do not redo): DuckDB workspace (`workspace.py`), per-source
  `ingest.py` mappings, `ml.ingest`/`wb.ingest`, completeness test, isolation
  test, Arrow bulk load, `inspect(include_metabolites=False)`, docs.
- **Verify before/after every change**:
  ```bash
  .venv-local/bin/pyright            # strict, 0 errors
  .venv-local/bin/ruff check src tests
  .venv-local/bin/python -m pytest tests/ -q
  ```
- **Every commit must be green.** After a branch is written, verify each commit
  by checking it out and running pytest (as done previously).

## 1. Context

`metabo-search` searches MetaboLights and Metabolomics Workbench, ingests the
hits you care about into a local DuckDB file, and analyses with SQL. Sources
never unify. Ingestion flow:

```
search()/inspect() → typed models → ml.ingest(obj, ws) → source ingest.write(ws, obj)
                                          └─ batch(obj)  [PURE] → Workspace.write
```

`Workspace.write` bulk-loads each table via Arrow (schema from `DESCRIBE`),
dedupes natural keys, merges with one set-based `ON CONFLICT`, in one
transaction. Reports are `IngestReport(tables, total, skipped, duplicates)`.

## 2. Locked decisions (do not re-litigate)

| Decision | Choice |
|---|---|
| Natural-key collisions | keep as is; report + drop duplicates (never throw), except `ml_sample` (see Issue I) |
| Prune on re-ingest | no; upsert-only, documented as working-set semantics |
| Import cost (`duckdb`+`pyarrow` at import) | accept |
| Duplicates | report in `IngestReport.duplicates`, last-wins, drop the rest |
| Shared ingest mechanics | extract into a neutral, source-agnostic module |
| WB corpus cache location | `~/.cache/metabo-search` default; `cache_dir=` argument |
| WB corpus TTL | 7 days; expose opt-out and refresh |
| WB cache scope | corpus-specific (not a generic HTTP cache) |
| Stale-on-error | serve stale cache if the refresh fetch fails |
| Report shape | keep one `IngestReport`; `Workspace.write` returns `skipped=[]` |

## 3. Open micro-decisions (defaults chosen; change if desired)

1. **Inspect API shape**: default **booleans** (`include_samples=…`, matching
   `MetaboLights.inspect(include_metabolites=…)`). Alternative: a
   `parts: frozenset[str]` set.
2. **Partial details**: default **document** that partial `StudyDetail`s are for
   selective ingestion, not an authoritative record. Alternative: add
   `StudyDetail.included: frozenset[str]`.

---

## 4. Issues and fixes

### A. Workbench whole-index corpus is not cached — P0

**Symptom.** `Workbench.all_studies()` fetches `/ST/summary` (~90 s) once per
client *instance*, in memory only. Not persisted, not shared across instances
or processes. Every new `Workbench()` re-pays it. `transport.py` has **no HTTP
cache** (no `cache`/`ttl`/`etag`). `docs/api-design.md` §11 claims an HTTP cache
— that is stale.

**Evidence.** `workbench/client.py`: `self._summaries` + `all_studies()`.

**Fix.** Corpus-specific disk cache.
- New `src/metabo_search/workbench/corpus.py` with a small cache helper.
- `Workbench(cache_dir: str | Path | None = Path.home()/".cache"/"metabo-search",
  corpus_ttl: timedelta = timedelta(days=7))`.
- `cache_dir=None` → no disk cache (today's behaviour).
- `wb.refresh_corpus() -> dict[str, StudySummary]` → force fetch, rewrite cache,
  update `_summaries`.
- `all_studies()` read order: memory → fresh cache → fetch (save). On fetch
  failure with a stale cache present → use stale + emit a visible notice.
- File: `<cache_dir>/wb-corpus-<sha1(base_url)[:12]>.json` containing
  `{"fetched_at": ISO8601, "base_url": ..., "payload": <raw /ST/summary>}`.
  Atomic write (temp file + `os.replace`).
- JSON (raw response), not parquet: no coupling to `StudySummary`.

**Acceptance criteria.**
- Second `Workbench()` with the same `cache_dir` does **not** hit `/ST/summary`.
- `cache_dir=None` always fetches.
- Expired cache triggers a fetch; if the fetch fails, the stale cache is used.
- `refresh_corpus()` always fetches and rewrites.
- Tests use `tmp_path` + `httpx.MockTransport`.

### B. `Workbench.inspect()` makes 4 calls/study, re-fetches summaries — P1

**Symptom.** `inspect()` reduces `str | StudySummary` inputs to `study_id` and
unconditionally calls `/summary`, `/factors`, `/analysis`, `/metabolites`. A
passed `StudySummary` is discarded and re-downloaded. No `include_*` option.

**Evidence.** Request log for one study: 4 requests, including `/summary`.
For 1,870 human studies: ~7,480 calls where 1,870 suffice (~20 min → ~5–6 min).

**Fix.**
- Reuse a passed `StudySummary`; only call `self.study(id)` for `str` inputs
  (and only when `include_summary`).
- Add `include_summary=True, include_samples=True, include_analyses=True,
  include_metabolites=True` (defaults preserve current behaviour).
- `StudyDetail.summary` is already `StudySummary | None`.

**Acceptance criteria.**
- `wb.inspect(summaries, include_analyses=False, include_metabolites=False)`
  issues exactly **1** request/study.
- Passing a `StudySummary` never issues a `/summary` request.
- `wb.inspect(ids)` with defaults still returns the full `StudyDetail`
  (existing tests unchanged).
- `wb.inspect(ids, include_summary=False, include_analyses=False,
  include_metabolites=False)` issues exactly 1 request/study.

### C. MetaboLights downloads the unparsed `i_Investigation.txt` — P1

**Symptom.** `isa_files` matches `[isam]_` and downloads `i_Investigation.txt`
for every study, but `parse_study_detail` only consumes `s_`, `a_`, `m_`. The
parse module's docstring explicitly says the investigation file is deliberately
not parsed. ~1,025 wasted downloads on the human corpus.

**Evidence.** `metabolights/files.py` `_ISA_NAME = r"^[isam]_…"` vs
`metabolights/parse.py` `parse_study_detail` (`s_`/`a_`/`m_` only).

**Fix.** Drop `i` from `_ISA_NAME` (keep `[sam]_`). Aligns code with the
documented intent. (Parsing it is the alternative, but explicitly out of scope.)

**Acceptance criteria.** An `inspect` never requests `i_Investigation.txt`;
existing sample/assay/MAF parsing unchanged.

### D. Whole-table dedupe is a scale hazard — P0

**Symptom.** `_dedupe` builds a dict keyed by full natural-key tuples for the
**entire** table before chunking, even with zero duplicates.

**Evidence.** Measured: **1,000,000 rows → 162 MB peak**, no duplicates.
`ml_metabolite_abundance` already reaches millions of rows.

**Fix.** Dedupe **per chunk** inside `_merge`. Global last-wins is preserved:
chunks are merged in order and `ON CONFLICT DO UPDATE` overwrites, so a later
chunk's row wins. Accumulate the collapsed count across chunks.

**Acceptance criteria.** Existing duplicate/upsert tests pass; peak dedupe
memory is bounded by `_CHUNK`, not table size; add a test that a duplicate split
across chunk boundaries still resolves last-wins.

### E. Audit is written in a second transaction — P0

**Symptom.** `ingest.write` = `ws.write(data)` then `_record_run` =
`ws.write(audit)`. Two transactions, two full DDL executions; data can commit
while the audit fails (no trail).

**Evidence.** `metabolights/ingest.py` and `workbench/ingest.py` `write()`.

**Fix.** Build the audit rows from the batch **before** writing (counts are
known from `batch.tables`), append them to the same `IngestBatch`, and let
`Workspace.write` commit once. Exclude the audit table from
`IngestReport.tables`/`total`.

**Acceptance criteria.** One transaction per ingest; an ingest writes data rows
and `*_ingest_run` rows atomically; `IngestReport` excludes the audit table.

### F. Duplicated ingest mechanics — P2

**Symptom.** `_build_ddl`, `_record_run`, `_to_table_rows`, `_add`, `_skipped`,
and the `batch`/`write` scaffolding are duplicated verbatim across the two
`ingest.py` files (~90 lines).

**Fix.** Move source-agnostic mechanics into a neutral module (in or beside
`workspace.py`; it must import no source). Proposed helpers:
`render_ddl(tables)`, `to_table_rows(tables, rows_by_name)`,
`record_run(ws, source, audit_table, tables_spec, report)`,
`skipped_ids(obj, detail_type)`. Each source keeps only its `_TABLES` spec and
mapping functions.

**Acceptance criteria.** Both `ingest.py` files shrink to spec + mapping; all
existing tests pass; isolation test still holds (`workspace.py` imports no
source).

### G. Completeness test is coarse; two paths untested — P2

**Symptom.** `tests/test_ingest_completeness.py` checks `model_fields ⊆
union(all columns) ∪ _EXPANDED`. The union includes the audit table, so a new
field sharing a name (`source`, `rows`) passes silently. `ml_funding` and
`ml_protocol_component` are never exercised (fixtures have empty lists).

**Fix.**
- Replace with a **sentinel test**: construct one instance per model with every
  field set to a unique marker, run `batch()`, assert every marker appears in
  the produced rows. Exclude intentionally-dropped fields (`StudyDetail.error`).
- Add explicit coverage for `ml_funding` and `ml_protocol_component`.

**Acceptance criteria.** Deleting any mapping line makes a sentinel test fail;
funding/component rows are asserted.

### H. Docs drift — P3

**Symptom.** `docs/api-design.md` still documents `Table`, `export()`, `.df()`,
and "HTTP cache on" — all removed or never built.

**Fix.** Refresh §8/§11; document corpus caching, partial-detail semantics, and
upsert-only behaviour.

---

### I. `ml_sample` natural key collapses distinct samples — P0

**Symptom.** `ml_sample`'s natural key is `(study_id, sample_name)`, but
`Sample Name` is a reusable label in real submissions, not a unique id, so
`(study_id, sample_name)` silently drops distinct samples. The Arrow dedupe now
reports the collapse in `IngestReport.duplicates`, but the rows are still lost.

**Evidence.** MTBLS66 has 8,167 rows but only 3,571 distinct `Sample Name`;
`"Control 1"` appears 206x, distinguished by `Source Name` (batch) and
`Factor Value[Replicate]`. The API's own `sampleCount` is 8,167. A random
600-study scan found 9 affected studies / ~4,921 collapsed rows (MTBLS66,
MTBLS436, MTBLS209, MTBLS372, MTBLS15555, MTBLS574, MTBLS450, ...). Well-curated
submissions are unaffected, so human-only spot checks can miss it.

**Fix.** Add `Sample.sample_row` (0-based row index within its sample sheet),
set in `parse_sample_file`, and include it in the natural keys of `ml_sample`,
`ml_sample_characteristic`, `ml_sample_factor`, and `ml_sample_file`. Reused
names are disambiguated by row; variant copies of the same sheet still collapse
to the API count (MTBLS13483: 76 rows -> 38; MTBLS744: 4 -> 2).

**Acceptance criteria.** MTBLS66 ingests 8,167 `ml_sample` rows; the other
affected studies match `sampleCount`; re-ingest stays idempotent; the
completeness test covers `sample_row`.

---

## 5. Branch & commit plan

Bases: client-only work branches off `main` (which already has the clients);
ingestion work branches off `feature/duckdb-ingestion`.

### Branch 1 — `perf/workbench-corpus-cache` (base `main`)
Purpose: skip the ~90 s WB index fetch on warm runs.
1. `feat(workbench): disk cache for the whole-index corpus`
2. `test(workbench): corpus cache hit / miss / ttl / refresh / stale`
3. `docs: corpus caching (README + SKILL)`

### Branch 2 — `perf/inspect-fetch` (base `main`)
Purpose: stop fetching what inspection doesn't use.
1. `feat(workbench): inspect only requested parts; reuse passed summaries`
2. `fix(metabolights): don't download the unparsed investigation file`
3. `test: inspection request counts`
4. `docs: partial-detail semantics`

### Branch 3 — `refactor/ingest-write-path` (base `feature/duckdb-ingestion`)
Purpose: memory-bounded, atomic write path; no duplication.
1. `perf(workspace): dedupe per chunk, not per table` (+ test)
2. `fix(ingest): record the audit in the same transaction` (+ test)
3. `refactor(ingest): extract shared mechanics` (+ tests)
4. `docs: upsert-only semantics + refresh api-design.md`

### Branch 4 — `test/ingest-completeness` (base `refactor/ingest-write-path`)
Purpose: prove no silent field loss (needs Branch 3's final structure).
1. `test(ingest): sentinel-based total-mapping test`
2. `test(metabolights): funding + protocol-component coverage`

### Branch 5 — `fix/metabolights-sample-key` (base `feature/duckdb-ingestion`)
Purpose: stop `ml_sample` dropping distinct samples (Issue I).
1. `fix(metabolights): key samples on (study_id, sample_name, sample_row)`
2. `test(metabolights): duplicate sample names stay distinct`
3. `docs: sample-row identity`

### Merge order
`1 → main`, `2 → main`; rebase `feature/duckdb-ingestion` on the updated
`main`; `3 → feature`; `4 → feature`; `5 → feature`; then
`feature/duckdb-ingestion → main`.

### Future (separate branch, not now)
- `feat/deep-result-cache` — per-study deep results keyed by
  `(study_id, revision)` (the role the removed `core/cache` played). Makes warm
  deep runs seconds instead of minutes. Needs a decision on the MetaboLights
  revision key.

## 6. Non-goals

- No pruning of children removed upstream (upsert-only stays).
- No natural-key discriminators (accepted collisions, reported) **except
  `ml_sample`**, which keys on `(study_id, sample_name, sample_row)` (Issue I).
- No generic HTTP cache.
- No streaming `inspect` iterator.
- No dataframe/`Table` wrapper; `ws.sql()` stays the raw DuckDB relation.

## 7. Definition of done

- Branch 1: warm `Workbench()` never re-fetches the corpus; TTL/refresh/opt-out
  work; tests offline.
- Branch 2: 1 request/study for samples-only; no `i_` download; defaults
  unchanged.
- Branch 3: dedupe memory bounded; one transaction per ingest; shared mechanics
  extracted; both `ingest.py` slim.
- Branch 4: sentinel completeness passes; funding/component covered.
- All branches: pyright strict 0, ruff clean, pytest green, every commit green.
